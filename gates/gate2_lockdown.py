"""Gate 2: lockdown. Play a misbehaving verifier service and prove the local tool server refuses.

    .venv/bin/python gates/gate2_lockdown.py

Data tools: a service that asks for an undeclared data tool, gives a declared one bad inputs or shell-looking values,
or calls set_up where the rules forbid it (finding cases, after another call, a seventh time, with no reset command) is
refused each time, with a receipt; and the commands of declared tools are never shown to it.

Also: a service asking for an upload (push, upload, store, ...) or for the results store's address is refused, and
the store receives nothing during the run: only the command line uploads, after the run is saved.

Stdlib only (plus the local half under test). Spends no model usage. Every server binds to 127.0.0.1 port 0.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
import tempfile
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from toolserver.runner import run_verify  # noqa: E402

RESULTS = ROOT / "gates" / "results" / "gate2_lockdown.json"
CHECKS: list[dict] = []


def check(name, ok, detail="") -> bool:
    CHECKS.append({"name": name, "ok": bool(ok), "detail": str(detail)})
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {str(detail)[:200]}", flush=True)
    return bool(ok)


# ------------------------------------------------------------------ servers
class _Quiet(BaseHTTPRequestHandler):
    def log_message(self, *a):  # noqa: D401
        pass


def serve(handler_cls):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


PAGE = b"<!doctype html><html><head><title>lock</title></head><body><h1>Lock app</h1></body></html>"


class AppHandler(_Quiet):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)


OTHER_HITS: list[str] = []


class OtherHandler(_Quiet):
    def _any(self):
        OTHER_HITS.append(f"{self.command} {self.path}")
        body = b"secret"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = _any


STORE_HITS: list[str] = []


class StoreHandler(_Quiet):
    """Stands in for the results store during the run: nothing may reach it, since only the command line uploads."""

    def _any(self):
        STORE_HITS.append(f"{self.command} {self.path}")
        body = b'{"id": "x", "missing": []}'
        self.send_response(201)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = _any


class FakeService:
    """Follows a fixed script; records every result the runner posts, keyed by action id."""

    def __init__(self, script):
        self.script = script
        self.calls = 0
        self.results: dict = {}
        self.ordered: list = []
        self.started: dict | None = None
        self.deleted = False
        self.lock = threading.Lock()

    def handler(self):
        svc = self

        class H(_Quiet):
            def _reply(self, obj, code=200):
                b = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                with svc.lock:
                    if self.path == "/v1/runs":
                        svc.started = body
                        return self._reply({"run": "lock1", "actions": []})
                    if self.path == "/v1/runs/lock1/results":
                        for r in body.get("results", []):
                            svc.ordered.append(r)
                            key = r.get("id") if r.get("id") is not None else "__none__"
                            svc.results[key] = r
                        i = svc.calls
                        svc.calls += 1
                        if i < len(svc.script):
                            return self._reply(svc.script[i])
                        return self._reply({"report": {"run": "lock1", "order": ["p1"], "properties": []}})
                    return self._reply({"message": "not found"}, 404)

            def do_DELETE(self):
                svc.deleted = True
                self._reply({})

        return H


# ------------------------------------------------------------------ main
def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="gt-gate2-"))
    servers = []
    try:
        return _run(tmp, servers)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        check("gate ran to completion", False, f"{type(e).__name__}: {e}")
        return 1
    finally:
        for s in servers:
            try:
                s.shutdown()
                s.server_close()
            except Exception:  # noqa: BLE001
                pass
        shutil.rmtree(tmp, ignore_errors=True)
        passed = bool(CHECKS) and all(c["ok"] for c in CHECKS)
        RESULTS.parent.mkdir(parents=True, exist_ok=True)
        RESULTS.write_text(json.dumps({"passed": passed, "checks": CHECKS}, indent=2))
        print(f"\n{'PASSED' if passed else 'FAILED'}: {sum(c['ok'] for c in CHECKS)}/{len(CHECKS)} checks; wrote {RESULTS}")


UNIT = "p1/c1"


def _feature(repo: Path):
    (repo / ".groundtruth" / "features").mkdir(parents=True)
    (repo / ".groundtruth" / "features" / "lock.yml").write_text(
        "feature: lock\n"
        "goal: The lock page shows its heading.\n"
        "properties:\n"
        "  - id: p1\n"
        "    statement: The lock page shows a heading reading Lock app.\n")


def _run(tmp: Path, servers: list) -> int:
    # ---- the throwaway project
    repo = tmp / "repo"
    (repo / "src").mkdir(parents=True)
    small = ["def lock():", "    return 'locked'", "", "def unlock():", "    return 'open'"]
    (repo / "src" / "small.py").write_text("\n".join(small) + "\n")
    (repo / "src" / "big.py").write_text("".join(f"x_{i} = {i}\n" for i in range(1, 601)))
    _feature(repo)
    outside = tmp / "outside.txt"
    outside.write_text("OUTSIDE SECRET 1\nOUTSIDE SECRET 2\n")
    canary = tmp / "CANARY_MUST_NOT_EXIST"

    app_srv, app_url = serve(AppHandler)
    servers.append(app_srv)
    other_srv, other_url = serve(OtherHandler)
    servers.append(other_srv)
    other_port = other_srv.server_address[1]
    store_srv, store_url = serve(StoreHandler)
    servers.append(store_srv)

    (repo / ".groundtruth" / "env.json").write_text(json.dumps({"url": app_url, "pid": os.getpid(), "repo": str(repo)}))

    # ---- the misbehaving service's script
    must_refuse = {
        "cmd_shell": {"tool": "shell", "args": {"cmd": f"touch {canary}"}},
        "cmd_bash": {"tool": "bash", "args": {"cmd": f"touch {canary}"}},
        "cmd_exec": {"tool": "exec", "args": {"cmd": f"touch {canary}"}},
        "cmd_run_command": {"tool": "run_command", "args": {"cmd": f"touch {canary}"}},
        "cmd_write_file": {"tool": "write_file", "args": {"path": str(canary), "content": "x"}},
        "host_http": {"tool": "http_request", "args": {"method": "GET", "path": f"http://127.0.0.1:{other_port}/secret"}},
        "host_navigate": {"tool": "navigate", "args": {"path": f"http://127.0.0.1:{other_port}/"}},
        "host_file_scheme": {"tool": "http_request", "args": {"method": "GET", "path": "file:///etc/hosts"}},
        "out_dotdot": {"tool": "read_lines", "args": {"path": "../outside.txt", "start": 1, "end": 2}},
        "out_abs": {"tool": "read_lines", "args": {"path": "/etc/hosts", "start": 1, "end": 2}},
        "out_glob": {"tool": "match_each", "args": {"glob": "../*", "must_contain": "x"}},
        "too_long": {"tool": "read_lines", "args": {"path": "src/big.py", "start": 1, "end": 500}},
        "proto_bad_claim": {"tool": "begin_claim", "args": {"claim": "zz"}},
        "proto_phase3": {"tool": "begin_phase", "args": {"claim": UNIT, "phase": 3}},
        "proto_not_dict": "not a dict",
        "proto_wrong_arg": {"tool": "read_lines", "args": {"file": "src/small.py"}},
        "proto_reset": {"tool": "reset_data", "args": {}},
        # uploading is something only the command line does, after the run: no tool may start one
        "up_push": {"tool": "push", "args": {}},
        "up_upload": {"tool": "upload", "args": {"run": "latest"}},
        "up_store": {"tool": "store", "args": {"url": store_url}},
        "up_push_run": {"tool": "push_run", "args": {"store": store_url, "token": "x"}},
        "up_upload_file": {"tool": "upload_file", "args": {"path": "src/small.py", "url": store_url}},
        "up_publish": {"tool": "publish", "args": {}},
        "up_store_http": {"tool": "http_request", "args": {"method": "POST", "path": f"{store_url}/v1/runs", "body": "{}"}},
        "up_store_navigate": {"tool": "navigate", "args": {"path": f"{store_url}/"}},
        # a page reference is a fixed-form token from observe; it must never work as a selector
        "ref_selector_click": {"tool": "click", "args": {"ref": "e1 >> css=body"}},
        "ref_selector_fill": {"tool": "fill", "args": {"value": "x", "ref": "css=input"}},
        "ref_selector_read": {"tool": "read_attribute", "args": {"attribute": "id", "ref": "xpath=//body"}},
    }
    # catalog-level refusals that must leave a receipt with "refused": true
    receipt_ids = ["cmd_shell", "cmd_bash", "cmd_exec", "cmd_run_command", "cmd_write_file", "host_http",
                   "host_navigate", "host_file_scheme", "out_dotdot", "out_abs", "out_glob", "too_long",
                   "proto_wrong_arg", "proto_reset", "ref_selector_click", "ref_selector_fill", "ref_selector_read",
                   "up_push", "up_upload", "up_store", "up_push_run", "up_upload_file", "up_publish", "up_store_http",
                   "up_store_navigate"]
    must_work = {
        "ok_http": {"tool": "http_request", "args": {"method": "GET", "path": "/"}},
        "ok_read": {"tool": "read_lines", "args": {"path": "src/small.py", "start": 1, "end": 3}},
        "ok_list": {"tool": "list_files", "args": {}},
    }
    batch = []
    for aid, act in {**must_refuse, **must_work}.items():
        batch.append(act if not isinstance(act, dict) else {"id": aid, **act})

    end_report = {"verdict": "fails", "evidence": "The heading is missing (fake service claim).", "settled_by": "code",
                  "screenshots": ["shot-99"],
                  "excerpts": [{"path": "src/small.py", "start": 1, "end": 3, "why": "where lock is defined"},
                               {"path": "src/big.py", "start": 1, "end": 500, "why": "too long"},
                               {"path": "../outside.txt", "start": 1, "end": 1, "why": "outside"}]}
    # bad unit names: in their own step after the batch, so one hole cannot disturb the checks above.
    # (These are refused by the runner itself, so their trace is in <run>/runner.jsonl, not in the unit's receipts.)
    bad_units = {
        "unit_dotdot_case": "p1/../x",
        "unit_dotdot_lead": "../p1/c1",
        "unit_three_parts": "p1/c1/extra",
        "unit_unknown_property": "zz/c1",
        "unit_no_case_part": "p1",
        "unit_case_40_chars": "p1/" + "c" * 40,
    }
    unit_batch = [{"id": aid, "tool": "begin_claim", "args": {"claim": name, "reset": False}} for aid, name in bad_units.items()]
    script = [
        {"actions": [{"id": "bc", "tool": "begin_claim", "args": {"claim": UNIT, "reset": False}}]},
        {"actions": batch},
        {"actions": unit_batch},
        {"actions": [{"id": "ec", "tool": "end_claim", "args": {"claim": UNIT, "report": end_report}}]},
        {"actions": [{"id": "bc_again", "tool": "begin_claim", "args": {"claim": UNIT, "reset": False}}]},
        {"report": {"run": "lock1", "order": ["p1"], "properties": []}},
    ]
    svc = FakeService(script)
    svc_srv, svc_url = serve(svc.handler())
    servers.append(svc_srv)

    # ---- the run
    # the store's address and a token are in the environment, as they would be for `verify --push`
    saved_env = {k: os.environ.get(k) for k in ("GT_STORE", "GT_STORE_TOKEN")}
    os.environ.update({"GT_STORE": store_url, "GT_STORE_TOKEN": "gate2-token"})
    try:
        run_dir = run_verify(repo, "lock", svc_url, say=lambda *_: None)
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    run_dir = Path(run_dir)
    check("run finished", (run_dir / "run.json").is_file(), f"run dir {run_dir.name}; service saw {svc.calls} result posts")
    check(f"begin_claim {UNIT} accepted", (svc.results.get("bc") or {}).get("text", "").startswith("ready"),
          (svc.results.get("bc") or {}).get("text"))
    check(f"end_claim {UNIT} accepted", (svc.results.get("ec") or {}).get("text") == "saved",
          (svc.results.get("ec") or {}).get("text"))

    # ---- refusals
    def refused(aid, label):
        r = svc.results.get(aid)
        ok = bool(r) and r.get("isError") is True and str(r.get("text", "")).startswith("refused")
        check(label, ok, "no result came back" if not r else f"isError={r.get('isError')} text={r.get('text')!r}")

    for aid in must_refuse:
        refused(aid if aid != "proto_not_dict" else "__none__", f"refused {aid}")
    for aid, name in bad_units.items():
        refused(aid, f"refused {aid} (begin_claim {name!r})")
    refused("bc_again", f"refused begin_claim {UNIT} again after it was finished")

    # ---- things that must still work
    r = svc.results.get("ok_http") or {}
    check("works http_request GET /", r.get("isError") is False and str(r.get("text", "")).startswith("200"),
          repr(str(r.get("text"))[:80]))
    r = svc.results.get("ok_read") or {}
    txt = str(r.get("text", ""))
    got_lines = txt.splitlines()
    ok = r.get("isError") is False and len(got_lines) == 3 and all(
        got_lines[i].endswith(small[i]) and got_lines[i].strip().startswith(str(i + 1)) for i in range(3))
    check("works read_lines src/small.py 1-3", ok, repr(txt[:120]))
    r = svc.results.get("ok_list") or {}
    files = str(r.get("text", "")).splitlines()
    check("works list_files (has src/small.py, nothing under .groundtruth)",
          r.get("isError") is False and "src/small.py" in files and not any(".groundtruth" in f for f in files),
          f"files={files}")

    # ---- side effects
    check("canary does not exist", not canary.exists(), str(canary.name))
    check("other host received zero requests", len(OTHER_HITS) == 0, f"hits={OTHER_HITS}")
    check("the results store received zero requests during the run", len(STORE_HITS) == 0, f"hits={STORE_HITS}")
    check("the upload code was never loaded by the run", "toolserver.push" not in sys.modules, "toolserver.push")
    outside_read = [x for x in svc.ordered if "OUTSIDE SECRET" in json.dumps(x)]
    check("outside.txt content never sent to the service", not outside_read, f"{len(outside_read)} results contained it")

    # ---- receipts
    rec_file = run_dir / UNIT / "receipts.jsonl"
    receipts = [json.loads(l) for l in rec_file.read_text().splitlines() if l.strip()] if rec_file.is_file() else []
    by_action = {aid: must_refuse[aid] for aid in receipt_ids}
    missing = []
    for aid, act in by_action.items():
        hit = [x for x in receipts if x.get("refused") is True and x.get("tool") == act["tool"]
               and x.get("args") == act["args"]]
        if not hit:
            missing.append(aid)
    check("receipts.jsonl has a refused entry for every catalog-level refusal", rec_file.is_file() and not missing,
          f"{len(receipts)} receipts; missing={missing}")

    # ---- report.json
    rep_file = run_dir / UNIT / "report.json"
    rep = json.loads(rep_file.read_text()) if rep_file.is_file() else {}
    ex = rep.get("excerpts") or []
    check("report keeps exactly the one valid excerpt with its real lines",
          len(ex) == 1 and ex[0].get("path") == "src/small.py" and ex[0].get("lines") == small[:3]
          and ex[0].get("why") == "where lock is defined",
          f"excerpts={[(e.get('path'), e.get('start'), e.get('end'), e.get('lines')) for e in ex]}")
    check("report screenshots empty", rep.get("screenshots") == [], f"screenshots={rep.get('screenshots')}")
    notes = rep.get("notes") or []
    joined = "\n".join(map(str, notes))
    ok = ("src/big.py" in joined and "../outside.txt" in joined and "shot-99" in joined)
    check("report notes mention dropped excerpts and screenshot", ok, f"notes={notes}")

    # ---- what was sent
    sent_file = run_dir / "sent.json"
    sent_raw = sent_file.read_text() if sent_file.is_file() else ""
    paths = {str(repo), str(repo.resolve()), str(tmp), str(tmp.resolve())}

    def leaks(v):
        if isinstance(v, dict):
            return any(leaks(k) or leaks(x) for k, x in v.items())
        if isinstance(v, list):
            return any(leaks(x) for x in v)
        return isinstance(v, str) and any(p in v for p in paths)

    sent = json.loads(sent_raw) if sent_raw else None
    check("sent.json has no value containing the repo path", sent is not None and not leaks(sent),
          f"keys={list((sent or {}).keys())}; env={(sent or {}).get('env')}")
    check("what the service received matches sent.json and leaks no path",
          svc.started is not None and svc.started.get("input") == sent and not leaks(svc.started),
          f"client={(svc.started or {}).get('client')}")
    check("the pid value is not sent (only has_pid)", str(os.getpid()) not in json.dumps(svc.started or {}), f"pid {os.getpid()}")

    _run_failing_reset(tmp, servers, app_url)
    _run_data_tools(tmp, servers, app_url)
    return 0 if all(c["ok"] for c in CHECKS) else 1


def _run_failing_reset(tmp: Path, servers: list, app_url: str):
    """A second, separate project whose registered reset command fails: begin_claim with reset must say so as an
    error (the case would otherwise run on data that was meant to be clean and isn't); without reset it works."""
    repo = tmp / "repo-reset"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "small.py").write_text("def lock():\n    return 'locked'\n")
    _feature(repo)
    (repo / ".groundtruth" / "env.json").write_text(
        json.dumps({"url": app_url, "pid": os.getpid(), "repo": str(repo), "reset": "exit 3"}))
    script = [
        {"actions": [{"id": "r_true", "tool": "begin_claim", "args": {"claim": UNIT, "reset": True}}]},
        # observation only: is the unit left open after the failed reset? (reported, not a check)
        {"actions": [{"id": "after_fail", "tool": "http_request", "args": {"method": "GET", "path": "/"}}]},
        {"actions": [{"id": "r_false", "tool": "begin_claim", "args": {"claim": UNIT, "reset": False}}]},
        {"actions": [{"id": "r_end", "tool": "end_claim", "args": {"claim": UNIT, "report": {"outcome": "holds", "observed": "x"}}}]},
        {"report": {"run": "lock1", "order": ["p1"], "properties": []}},
    ]
    svc = FakeService(script)
    srv, url = serve(svc.handler())
    servers.append(srv)
    run_dir = Path(run_verify(repo, "lock", url, say=lambda *_: None))
    check("reset run finished", (run_dir / "run.json").is_file(), f"run dir {run_dir.name}; service saw {svc.calls} result posts")
    r = svc.results.get("r_true") or {}
    txt = str(r.get("text", ""))
    check("begin_claim with reset:true and a failing reset is an error naming the reset",
          r.get("isError") is True and "reset" in txt.lower() and not txt.startswith("ready"), f"isError={r.get('isError')} text={txt!r}")
    r = svc.results.get("r_false") or {}
    check("begin_claim with reset:false still works", r.get("isError") is False and str(r.get("text", "")).startswith("ready"),
          f"isError={r.get('isError')} text={r.get('text')!r}")
    r = svc.results.get("r_end") or {}
    check(f"end_claim {UNIT} accepted after reset:false", r.get("text") == "saved", repr(r.get("text")))
    r = svc.results.get("after_fail") or {}
    print(f"NOTE  after a failed reset the unit stays open: http_request GET / -> isError={r.get('isError')} "
          f"text={str(r.get('text'))[:60]!r}", flush=True)


RUN_MARKER = "GT_RUN_COMMAND_MARKER"


def _data_project(repo: Path, reset: str | None, app_url: str):
    """A project that declares data tools. Its one command (a script named after RUN_MARKER, so the marker is not a
    shell comment that could hide anything added after it) writes what it received (standard input, its command line
    and its environment) to a file and prints an id. If a value ever reached the command line, a value like
    "; touch <canary>" would run there and the canary would exist."""
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "small.py").write_text("def lock():\n    return 'locked'\n")
    _feature(repo)
    (repo / "tools").mkdir()
    (repo / "tools" / f"{RUN_MARKER}.py").write_text(
        "import json, os, sys\n"
        "data = sys.stdin.read()\n"
        "with open('received.jsonl', 'a') as f:\n"
        "    f.write(json.dumps({'stdin': data, 'argv': sys.argv, 'env': dict(os.environ)}) + '\\n')\n"
        "print(json.dumps({'id': 41, 'page': '/listings/41'}))\n")
    run = f"{shlex.quote(sys.executable)} tools/{RUN_MARKER}.py"
    (repo / ".groundtruth" / "data.yml").write_text(
        "tools:\n"
        "  - name: make_listing\n"
        "    does: Creates one listing and returns its id and the address of its page.\n"
        "    takes:\n"
        "      title: {type: string}\n"
        "      price_cents: {type: integer, does: The price in cents.}\n"
        "      sold_out: {type: boolean, optional: true}\n"
        f"    run: {json.dumps(run)}\n")
    env = {"url": app_url, "pid": os.getpid(), "repo": str(repo)}
    if reset:
        env["reset"] = reset
    (repo / ".groundtruth" / "env.json").write_text(json.dumps(env))


def _run_data_tools(tmp: Path, servers: list, app_url: str):
    repo = tmp / "repo-data"
    _data_project(repo, "true", app_url)
    canary = tmp / "DATA_CANARY_MUST_NOT_EXIST"
    shellish = {"sh_semicolon": f"\"; touch {canary}", "sh_dollar": f"$(touch {canary})", "sh_backtick": f"`touch {canary}`"}

    def setup(aid, tool="make_listing", **given):
        inp = {"title": "Lamp", "price_cents": 1200}
        inp.update(given)
        return {"id": aid, "tool": "set_up", "args": {"tool": tool, "input": inp}}

    bad_calls = {   # id -> (action, words the refusal must contain)
        "d_undeclared": (setup("d_undeclared", tool="drop_all_tables"), "no data tool named"),
        "d_extra": (setup("d_extra", colour="red"), "no input named 'colour'"),
        "d_missing": ({"id": "d_missing", "tool": "set_up", "args": {"tool": "make_listing", "input": {"title": "Lamp"}}},
                      "needs the input 'price_cents'"),
        "d_wrong_type": (setup("d_wrong_type", price_cents="1200"), "must be an integer"),
        "d_bool_not_int": (setup("d_bool_not_int", price_cents=True), "must be an integer"),
        "d_long": (setup("d_long", title="x" * 501), "at most 500"),
        "d_run_arg": ({"id": "d_run_arg", "tool": "set_up", "args": {"tool": "make_listing", "input": {}, "run": f"touch {canary}"}},
                      "bad arguments"),
    }
    case1 = [a for a, _ in bad_calls.values()]
    case1 += [setup(aid, title=v) for aid, v in shellish.items()]                 # 3 set_up calls that run
    case1 += [setup(f"d_ok{i}", sold_out=bool(i % 2)) for i in range(3)]          # 3 more: 6 in all
    case1 += [setup("d_seventh")]
    script = [
        {"actions": [{"id": "dd_begin", "tool": "begin_claim", "args": {"claim": "p1/discovery", "reset": True}}]},
        {"actions": [{"id": "d_list", "tool": "data_tools", "args": {}}, setup("d_in_discovery")]},
        {"actions": [{"id": "dd_end", "tool": "end_claim", "args": {"claim": "p1/discovery",
                                                                   "report": {"role": "discovery", "no_plan_because": "gate"}}}]},
        {"actions": [{"id": "d1_begin", "tool": "begin_claim", "args": {"claim": "p1/c1", "reset": True}}]},
        {"actions": case1},
        {"actions": [{"id": "d1_end", "tool": "end_claim", "args": {"claim": "p1/c1", "report": {
            "outcome": "holds", "observed": "x",
            "starts_from": [{"tool": "make_listing", "input": {"title": "never made"}, "because": "the first reason", "returned": {"id": 999}}]}}}]},
        {"actions": [{"id": "d2_begin", "tool": "begin_claim", "args": {"claim": "p1/c2", "reset": True}}]},
        {"actions": [{"id": "d2_http", "tool": "http_request", "args": {"method": "GET", "path": "/"}}, setup("d_after_call")]},
        {"actions": [{"id": "d2_end", "tool": "end_claim", "args": {"claim": "p1/c2", "report": {
            "outcome": "holds", "observed": "x",
            "starts_from": [{"tool": "make_listing", "input": {"title": "never made"}, "because": "x", "returned": {"id": 999}}]}}}]},
        {"report": {"run": "lock1", "order": ["p1"], "properties": []}},
    ]
    svc = FakeService(script)
    srv, url = serve(svc.handler())
    servers.append(srv)
    run_dir = Path(run_verify(repo, "lock", url, say=lambda *_: None))
    check("data tools run finished", (run_dir / "run.json").is_file(), f"run dir {run_dir.name}; service saw {svc.calls} result posts")
    for aid in ("dd_begin", "d1_begin", "d2_begin"):
        r = svc.results.get(aid) or {}
        check(f"data tools: {aid} accepted with a reset", r.get("isError") is False and "reset done" in str(r.get("text")), repr(r.get("text")))

    def receipts(unit):
        f = run_dir / unit / "receipts.jsonl"
        return [json.loads(x) for x in f.read_text().splitlines() if x.strip()] if f.is_file() else []

    def refused_with_receipt(aid, act, words, unit, results=None):
        r = (results or svc.results).get(aid) or {}
        txt = str(r.get("text", ""))
        has_receipt = any(x.get("refused") is True and x.get("tool") == "set_up" and x.get("args") == act["args"]
                          for x in receipts(unit))
        check(f"refused set_up: {aid} ({words})", r.get("isError") is True and txt.startswith("refused") and words in txt
              and has_receipt, f"receipt={has_receipt} text={txt!r}")

    # ---- data_tools
    r = svc.results.get("d_list") or {}
    data = r.get("data") or []
    check("data_tools lists the declared tool with what it takes",
          r.get("isError") is False and [t.get("name") for t in data] == ["make_listing"]
          and set(data[0]) == {"name", "does", "takes"} and set(data[0]["takes"]) == {"title", "price_cents", "sold_out"},
          f"data={data}")
    check("no result sent to the service contains a tool's run command",
          RUN_MARKER not in json.dumps(svc.ordered)
          and RUN_MARKER not in (run_dir / "sent.json").read_text(),
          "searched every result and sent.json")

    # ---- refusals
    refused_with_receipt("d_in_discovery", setup("d_in_discovery"), "discovery", "p1/discovery")
    for aid, (act, words) in bad_calls.items():
        refused_with_receipt(aid, act, words, "p1/c1")
    refused_with_receipt("d_seventh", setup("d_seventh"), "6 set_up calls", "p1/c1")
    refused_with_receipt("d_after_call", setup("d_after_call"), "already ran http_request", "p1/c2")
    r = svc.results.get("d2_http") or {}
    check("data tools: the other call in p1/c2 worked", r.get("isError") is False, repr(r.get("text"))[:80])

    # ---- shell-looking values: they run (they are valid strings), but only ever as data on standard input
    lines = [json.loads(x) for x in (repo / "received.jsonl").read_text().splitlines()] if (repo / "received.jsonl").is_file() else []
    for aid, value in shellish.items():
        r = svc.results.get(aid) or {}
        hit = [x for x in lines if json.loads(x["stdin"]).get("title") == value]
        elsewhere = [x for x in lines if value in json.dumps(x["argv"]) or value in json.dumps(x["env"])]
        check(f"set_up {aid}: the value arrived only on standard input, intact",
              r.get("isError") is False and len(hit) == 1 and not elsewhere,
              f"on stdin {len(hit)} time(s); on a command line or in the environment {len(elsewhere)} time(s)")
    check("data canary does not exist", not canary.exists(), canary.name)
    check("every set_up command got GROUNDTRUTH_APP_URL", bool(lines) and all(x["env"].get("GROUNDTRUTH_APP_URL") == app_url for x in lines),
          f"{len(lines)} runs")
    check("exactly 6 set_up commands ran in p1/c1 and none elsewhere", len(lines) == 6, f"{len(lines)} ran")

    # ---- the receipt of a successful set_up
    ok = [x for x in receipts("p1/c1") if x.get("tool") == "set_up" and not x.get("refused")]
    first = ok[0] if ok else {}
    check("a successful set_up's receipt records the tool, the input and what came back",
          len(ok) == 6 and first.get("args", {}).get("tool") == "make_listing"
          and first.get("args", {}).get("input", {}).get("title") == shellish["sh_semicolon"]
          and first.get("returned") == {"id": 41, "page": "/listings/41"},
          f"{len(ok)} successful set_up receipts; first={json.dumps(first)[:160]}")

    # ---- the saved result lists the set-up asked for here and what came of it, not what the report claimed
    saved1 = json.loads((run_dir / "p1" / "c1" / "report.json").read_text())
    steps = saved1.get("starts_from") or []
    made = [s for s in steps if "returned" in s]
    failed = [s for s in steps if "error" in s]
    check("the saved result lists every set_up asked for in order: 7 refused, 6 that returned, 1 refused, and not the claimed one",
          len(steps) == 14 and ["returned" in s for s in steps] == [False] * 7 + [True] * 6 + [False]
          and all(s["returned"] == {"id": 41, "page": "/listings/41"} for s in made)
          and all(str(s["error"]).startswith("refused") for s in failed)
          and made[0].get("input", {}).get("title") == shellish["sh_semicolon"] and "never made" not in json.dumps(steps),
          f"{len(steps)} steps, {len(made)} returned, {len(failed)} failed; first made={json.dumps(made[:1])[:120]}")
    check("only a step's reason is taken from the report, by position",
          bool(steps) and steps[0].get("because") == "the first reason" and all(s.get("because") == "" for s in steps[1:]),
          repr([s.get("because") for s in steps]))
    saved2 = json.loads((run_dir / "p1" / "c2" / "report.json").read_text())
    steps2 = saved2.get("starts_from") or []
    check("a claimed set-up is replaced by the refused one that was really asked for",
          len(steps2) == 1 and "returned" not in steps2[0] and "before anything else" in str(steps2[0].get("error"))
          and "never made" not in json.dumps(steps2), json.dumps(steps2)[:200])
    saved_d = json.loads((run_dir / "p1" / "discovery" / "report.json").read_text())
    check("a discovery's saved result shows its refused set_up as an error and nothing made",
          [("returned" in s, "not allowed while finding cases" in str(s.get("error"))) for s in saved_d.get("starts_from") or []] == [(False, True)],
          json.dumps(saved_d.get("starts_from"))[:200])

    # ---- a second project with no reset command
    repo2 = tmp / "repo-data-noreset"
    _data_project(repo2, None, app_url)
    script2 = [
        {"actions": [{"id": "n_begin", "tool": "begin_claim", "args": {"claim": "p1/c1", "reset": True}}]},
        {"actions": [setup("n_set_up")]},
        {"actions": [{"id": "n_end", "tool": "end_claim", "args": {"claim": "p1/c1", "report": {"outcome": "holds", "observed": "x"}}}]},
        {"report": {"run": "lock1", "order": ["p1"], "properties": []}},
    ]
    svc2 = FakeService(script2)
    srv2, url2 = serve(svc2.handler())
    servers.append(srv2)
    run_dir = Path(run_verify(repo2, "lock", url2, say=lambda *_: None))
    refused_with_receipt("n_set_up", setup("n_set_up"), "no reset command", "p1/c1", results=svc2.results)
    check("no set_up command ran without a reset command", not (repo2 / "received.jsonl").exists(), "received.jsonl")

    # ---- a broken declarations file stops the run before it starts
    repo3 = tmp / "repo-data-broken"
    _data_project(repo3, "true", app_url)
    (repo3 / ".groundtruth" / "data.yml").write_text("tools:\n  - name: make_listing\n    run: echo\n")
    svc3 = FakeService([])
    srv3, url3 = serve(svc3.handler())
    servers.append(srv3)
    try:
        run_verify(repo3, "lock", url3, say=lambda *_: None)
        err = None
    except Exception as e:  # noqa: BLE001
        err = str(e)
    check("a broken data.yml stops verify at the start, in plain words, before the service is contacted",
          err is not None and "make_listing" in err and "does" in err and svc3.started is None, repr(err))


if __name__ == "__main__":
    sys.exit(main())
