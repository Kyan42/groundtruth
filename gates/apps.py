"""Start/stop the demo app, build the control copy, and drive the groundtruth CLI. Stdlib only."""
from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = str(REPO / ".venv" / "bin" / "python")
CLI = str(REPO / "groundtruth.py")
DEMO = REPO / "demo"
WORK = REPO / "gates" / "work"

VERSIONS = {"agent": "1", "fixed": "0"}

# The planted second letter-to-mouth mapping (same as /tmp/gt-dup/render.py).
PLANTED_RENDER = '''"""Turns text into a sequence of mouth frames for the avatar."""
from accel import rasterizer_device
from visemes import viseme_for  # noqa: F401

# Faster lookup table for the hot path
MOUTH = {"a": "open", "e": "wide", "i": "wide", "o": "round", "u": "round", "m": "closed", "b": "closed",
         "p": "closed", "f": "teeth", "v": "teeth"}

FRAME_MS = 80


def render(text: str) -> dict:
    device = rasterizer_device()
    frames = [MOUTH.get(ch.lower(), "rest") for ch in text if not ch.isspace()]
    # Pretend rasterization: a little CPU work per frame so the render is measurable.
    checksum = 0
    for i, f in enumerate(frames):
        for j in range(20_000):
            checksum = (checksum * 31 + j + len(f) + i) % 1_000_003
    return {"frames": frames, "frame_ms": FRAME_MS, "count": len(frames), "checksum": checksum, "_device": device}
'''


def _listening_pids(port: int) -> list[int]:
    out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                         capture_output=True, text=True).stdout.split()
    return sorted({int(p) for p in out if p.strip().isdigit()})


def free_port(port: int) -> None:
    """Terminate whatever listens on `port` (SIGTERM, then SIGKILL after 3 s) and wait until it is free."""
    pids = _listening_pids(port)
    if not pids:
        return
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.time() + 3
    while time.time() < deadline:
        if not _listening_pids(port):
            return
        time.sleep(0.1)
    for pid in _listening_pids(port):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    deadline = time.time() + 5
    while time.time() < deadline:
        if not _listening_pids(port):
            return
        time.sleep(0.1)
    raise RuntimeError(f"port {port} is still in use by {_listening_pids(port)}")


def _get_root_ok(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


def start_lipsync(version: str, port: int = 8765, root: Path | None = None) -> subprocess.Popen:
    """Start a fresh demo process ("agent" opens the GPU on first render, "fixed" never does).
    Readiness only GETs `/`; nothing here ever POSTs to /api/render."""
    if version not in VERSIONS:
        raise ValueError(f"version must be 'agent' or 'fixed', got {version!r}")
    free_port(port)
    cwd = Path(root) if root else DEMO
    env = dict(os.environ, DEMO_AGENT_VERSION=VERSIONS[version])
    proc = subprocess.Popen([PY, "app.py", str(port)], cwd=cwd, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    deadline = time.time() + 15
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"demo app exited early with code {proc.returncode} (cwd {cwd}, port {port})")
        if _get_root_ok(port):
            return proc
        time.sleep(0.2)
    stop(proc)
    raise RuntimeError(f"demo app did not answer GET / with 200 within 15 s (cwd {cwd}, port {port})")


def stop(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _inside(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    return rel != Path(".")


def make_control_copy(dest: Path) -> Path:
    """Copy the demo with a second letter-to-mouth mapping planted in render.py.
    The claims (.groundtruth/features) are copied fresh every call; runs and env.json are never copied."""
    dest = Path(dest).resolve()
    roots = {Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve(), WORK.resolve()}
    if not any(_inside(dest, r) for r in roots):
        raise ValueError(f"refusing to build the control copy at {dest}: must be inside "
                         f"the system temp dir or {WORK}")
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    for name in ("app.py", "accel.py", "visemes.py"):
        shutil.copy2(DEMO / name, dest / name)
    shutil.copytree(DEMO / "static", dest / "static", ignore=ignore)
    (dest / "render.py").write_text(PLANTED_RENDER)
    (dest / ".groundtruth").mkdir()
    shutil.copytree(DEMO / ".groundtruth" / "features", dest / ".groundtruth" / "features", ignore=ignore)
    return dest


def _kill_group(proc: subprocess.Popen) -> None:
    """Kill the CLI and everything it started (claude -p, env_server, browsers). The group may outlive its leader."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return  # nothing left in the group
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    time.sleep(0.5)
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    if proc.poll() is None:
        proc.wait()


def _run_cli(args: list[str], timeout: int) -> str:
    """Run the CLI in its own process group, echoing output live; return the captured output.
    Raise on failure or timeout. The whole group is killed on timeout or interruption."""
    cmd = [PY, "-u", CLI, *args]
    print("$ " + " ".join(cmd), flush=True)
    proc = subprocess.Popen(cmd, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, text=True, bufsize=1, start_new_session=True)
    lines: list[str] = []

    def pump():
        for line in proc.stdout:
            lines.append(line)
            sys.stdout.write("    " + line)
            sys.stdout.flush()

    t = threading.Thread(target=pump, daemon=True)
    t.start()
    try:
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            t.join(5)
            raise RuntimeError(f"timed out after {timeout} s: {' '.join(args)}\n" + "".join(lines))
    finally:
        _kill_group(proc)  # also reaps leftovers after a normal exit or Ctrl-C
    t.join(5)
    out = "".join(lines)
    if proc.returncode != 0:
        raise RuntimeError(f"exit {proc.returncode}: {' '.join(args)}\n{out}")
    return out


def register(app_dir: Path, url: str) -> str:
    return _run_cli(["register", str(app_dir), "--url", url], timeout=60)


def verify(app_dir: Path, feature: str, only: str | None = None, timeout: int = 1500) -> Path:
    args = ["verify", str(app_dir), feature] + (["--only", only] if only else [])
    out = _run_cli(args, timeout=timeout)
    m = re.search(r"^Run (\S+?):", out, re.M)
    if not m:
        raise RuntimeError(f"no 'Run <id>:' line in verify output:\n{out}")
    run_dir = Path(app_dir).resolve() / ".groundtruth" / "runs" / m.group(1)
    if not run_dir.is_dir():
        raise RuntimeError(f"run directory {run_dir} does not exist\n{out}")
    return run_dir


# ---- added for the hosted/local split and for cbay (steps 2 and 3)
SERVICE_PORT = 8790
CBAY_APP = REPO.parent / "cbay" / "app"
NODE_BIN = "/opt/homebrew/opt/node@22/bin"


def _get_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


def _wait_ok(proc: subprocess.Popen, url: str, seconds: int, what: str) -> subprocess.Popen:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"{what} exited early with code {proc.returncode}")
        if _get_ok(url):
            return proc
        time.sleep(0.2)
    stop(proc)
    raise RuntimeError(f"{what} did not answer {url} within {seconds} s")


def start_service(port: int = SERVICE_PORT, model: str = "claude-sonnet-5-5") -> subprocess.Popen:
    """The verifier service as its own process, started from a temp directory: it has no path to any app's repo."""
    free_port(port)
    cwd = tempfile.mkdtemp(prefix="gt-service-cwd-")
    log = open(WORK.parent / "results" / "service.log", "a")
    proc = subprocess.Popen([PY, "-u", "-m", "service.server", "--port", str(port), "--model", model], cwd=cwd,
                            env=dict(os.environ, PYTHONPATH=str(REPO)), stdout=log, stderr=log, stdin=subprocess.DEVNULL)
    return _wait_ok(proc, f"http://127.0.0.1:{port}/health", 15, "verifier service")


def start_cbay(port: int = 3100) -> subprocess.Popen:
    """cbay (a real Next.js shop) from its existing production build."""
    free_port(port)
    env = dict(os.environ, PATH=NODE_BIN + os.pathsep + os.environ.get("PATH", ""))
    proc = subprocess.Popen(["node", "node_modules/next/dist/bin/next", "start", "--port", str(port)], cwd=CBAY_APP,
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    return _wait_ok(proc, f"http://127.0.0.1:{port}/api/health", 60, "cbay")
