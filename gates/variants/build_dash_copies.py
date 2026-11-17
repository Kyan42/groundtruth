"""Build the four copies of the dashboard page used by the claims streams of gates/props_gate.py.

    .venv/bin/python gates/variants/build_dash_copies.py

Each copy is gates/work/<name>: the dashboard/ folder as committed at PAGE_COMMIT (git archive, so all four come from
the same commit, and the page under test stays the same while the real dashboard page moves on), without .groundtruth/runs, .groundtruth/test-store, .groundtruth/env.json, design/ and samples/; then the
copy's patch from gates/variants/ (none for dash-clean), applied with `patch -p1`; then the copy's own
`python3 .groundtruth/tools/init_store.py`, so it has its own test-store folder and marker. Re-runnable: it removes
and rebuilds only these four folders. It stops with a plain message if a patch does not apply cleanly, or if a copy's
claims.yml is not byte-identical to dashboard/.groundtruth/features/claims.yml. See gates/variants/DASH-COPIES.md.
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
VARIANTS = REPO / "gates" / "variants"
WORK = REPO / "gates" / "work"
CLAIMS = Path(".groundtruth") / "features" / "claims.yml"
# The page these copies are made from. It is pinned, because the copies are the app under test, not the product: the
# patches were written against this page, and the verifier's own results page keeps changing.
PAGE_COMMIT = "1564323"
COPIES = {"dash-clean": None, "dash-regression-short": "dash-regression-short.patch",
          "dash-single-holds": "dash-single-holds.patch", "dash-other-test-film": "dash-other-test-film.patch"}
LEFT_OUT = (".groundtruth/runs", ".groundtruth/test-store", ".groundtruth/env.json", "design", "samples")


def dashboard_at_head() -> bytes:
    r = subprocess.run(["git", "archive", "--format=tar", PAGE_COMMIT, "dashboard"], cwd=REPO, capture_output=True)
    if r.returncode != 0:
        sys.exit(f"git archive {PAGE_COMMIT} dashboard failed: {r.stderr.decode(errors='replace').strip()}")
    return r.stdout


def left_out(rel: str) -> bool:
    return any(rel == x or rel.startswith(x + "/") for x in LEFT_OUT)


def extract(tar_bytes: bytes, dest: Path) -> int:
    n = 0
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tf:
        for m in tf.getmembers():
            parts = Path(m.name).parts
            if not parts or parts[0] != "dashboard" or len(parts) == 1:
                continue
            rel = "/".join(parts[1:])
            if left_out(rel) or not (m.isfile() or m.isdir()):
                continue
            target = dest / rel
            if m.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(tf.extractfile(m).read())
            target.chmod(m.mode & 0o777 or 0o644)
            n += 1
    return n


def build(name: str, patch: str | None, tar_bytes: bytes) -> str:
    dest = WORK / name
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    n = extract(tar_bytes, dest)
    said = f"{name}: {n} files from {PAGE_COMMIT}"
    if patch:
        p = VARIANTS / patch
        r = subprocess.run(["patch", "-p1", "--forward", "--batch", "--no-backup-if-mismatch", "-i", str(p)], cwd=dest,
                           capture_output=True, text=True)
        if r.returncode != 0 or list(dest.rglob("*.rej")) or list(dest.rglob("*.orig")):
            sys.exit(f"{patch} did not apply cleanly to {dest}:\n{r.stdout}{r.stderr}")
        said += f", {patch} applied"
    r = subprocess.run(["python3", ".groundtruth/tools/init_store.py"], cwd=dest, capture_output=True, text=True,
                       env=_env())
    if r.returncode != 0:
        sys.exit(f"init_store.py failed in {dest}:\n{r.stdout}{r.stderr}")
    if (dest / CLAIMS).read_bytes() != (REPO / "dashboard" / CLAIMS).read_bytes():
        sys.exit(f"{dest / CLAIMS} differs from dashboard/{CLAIMS} (is dashboard/{CLAIMS} committed?)")
    return said + ", test store made"


def _env() -> dict:
    return {k: v for k, v in os.environ.items() if k != "GT_TEST_STORE_DIR"}


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    tar_bytes = dashboard_at_head()
    head = subprocess.run(["git", "rev-parse", "--short", PAGE_COMMIT], cwd=REPO, capture_output=True, text=True).stdout.strip()
    for name, patch in COPIES.items():
        print(build(name, patch, tar_bytes) + f" (commit {head})")


if __name__ == "__main__":
    main()
