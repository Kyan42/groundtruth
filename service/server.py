"""Groundtruth's verifier service, as its own process. Start it from any directory:

    python -m service.server --port 8790          (or: groundtruth.py serve)
    GT_HOSTED=1 GT_DATA=/data python -m service.server --port 8790     hosted (see store/hosting.py)

Public (the user's tool server calls these; every connection is made from the user's side):
    POST   /v1/runs                    {input}    -> {run, actions: []}
    POST   /v1/runs/<id>/results       {results}  -> {actions: [...], progress} | {report}     (answers within ~20 s)
    DELETE /v1/runs/<id>
    POST   /v1/drafts                  {input}    -> 202 {draft: <id>}     (the conversation runs in the background)
    GET    /v1/drafts/<id>                        -> {state: working} | {state: done, draft, usage} | {state: failed, message}
    GET    /health                                -> {ok, version}   (and the model, when not hosted)
Internal (the model's tool list, service/proxy.py, calls these with the run's token):
    POST   /internal/runs/<id>/call    /internal/runs/<id>/submit

Hosted (GT_HOSTED=1): every public route but /health needs `Authorization: Bearer <token>`, checked against the users
table of the results store's database (GT_DATA/store.db), so one token works for both processes. A run or draft can be
read, continued or deleted only by the user who started it (anyone else gets 404). Before a run, a draft, each
discovery and each case, the user's spend this calendar month is checked against their limit (402 with a plain
message at the start; inside a run, the rest is reported as not run). Each model session's cost is written to the
spend table as the session ends. /internal/* is refused unless the connection comes from this machine (the socket's
peer address, never a header), on top of the run's token. Locally nothing needs a token, as before.

A finished draft is kept for 10 minutes for its owner to collect, then dropped.
The service has no path to the user's repo and never asks for one.
"""
import argparse
import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .harness import VERSION, Draft, Run, default_workdir

RUNS, DRAFTS = {}, {}        # id -> Run or Draft while it works; draft id -> Draft, until collected and expired
DRAFT_KEEP = 600             # seconds a finished draft is kept
DEFAULT_MODEL = "claude-sonnet-5-5"


class Refused(Exception):
    def __init__(self, status, error, message):
        super().__init__(message)
        self.status, self.error, self.message = status, error, message


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    cfg: dict = None          # model, workdir, internal_url, hosted, store (hosted only)

    def log_message(self, *a):
        pass

    def _json(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 4 * 1024 * 1024:
            raise ValueError("body too large")
        return json.loads(self.rfile.read(n) or b"{}")

    # ---- who is asking (hosted only; locally everyone is the one user, None)
    def _peer_is_loopback(self) -> bool:
        from store.hosting import is_loopback
        return is_loopback(self.client_address[0])

    def _token_user(self):
        """The user a bearer token names: None when there is no Authorization header; Refused when it is not valid."""
        auth = self.headers.get("Authorization")
        if auth is None:
            return None
        kind, _, token = auth.partition(" ")
        uid = self.cfg["store"].user_by_token(token.strip()) if kind.lower() == "bearer" and token.strip() else None
        if uid is None:
            raise Refused(401, "unauthorized", "This verifier did not accept that token. Run groundtruth.py login again with the token you were given.")
        return uid

    def _user(self):
        if not self.cfg["hosted"]:
            return None
        uid = self._token_user()
        if uid is None:
            raise Refused(401, "unauthorized", "This verifier needs your token. Run groundtruth.py login with the token you were given.")
        return uid

    def _mine(self, table, job_id, uid):
        job = table.get(job_id)
        if job is None or getattr(job, "owner", None) != uid:
            raise Refused(404, "no_such_run", "There is no such run.")
        return job

    def _limit(self, uid):
        if uid is not None:
            why = self.cfg["store"].over_limit(uid)
            if why:
                raise Refused(402, "spending_limit", why)

    def _metered(self, job, uid, kind):
        """Hosted: record each model session's cost as it ends, and let a run ask for the limit before each step."""
        job.owner = uid
        if uid is None:
            return
        store = self.cfg["store"]

        def on_cost(cost):
            try:
                store.record_spend(uid, job.id, kind, cost)
            except Exception as e:  # noqa: BLE001 -- a failed write never stops the run; it is said in the log
                print(f"could not record ${cost} for {kind} {job.id}: {type(e).__name__}: {e}", file=sys.stderr, flush=True)

        job.on_cost = on_cost
        if kind == "run":
            job.limit_reached = lambda: store.over_limit(uid)

    def _answer(self, fn):
        try:
            fn()
        except Refused as e:
            self.close_connection = True          # the body may be unread
            self._json(e.status, {"error": e.error, "message": e.message})

    # ---- routes
    def do_GET(self):
        self._answer(self._get)

    def do_DELETE(self):
        self._answer(self._delete)

    def do_POST(self):
        self._answer(self._post)

    def _get(self):
        if self.path == "/health":
            if not self.cfg["hosted"]:
                return self._json(200, {"ok": True, "version": VERSION, "model": self.cfg["model"]})
            uid = self._token_user()           # a token, when one is sent, must be valid: login checks it this way
            return self._json(200, {"ok": True, "version": VERSION} | ({"user": self.cfg["store"].user_name(uid)} if uid else {}))
        m = re.fullmatch(r"/v1/drafts/(\w+)", self.path)
        if m:
            uid = self._user()
            _prune_drafts()
            job = self._mine(DRAFTS, m.group(1), uid)
            return self._json(200, job.answer or {"state": "working"})
        self._json(404, {"error": "not_found"})

    def _delete(self):
        uid = self._user()
        m = re.fullmatch(r"/v1/runs/(\w+)", self.path)
        if not m:
            raise Refused(404, "no_such_run", "There is no such run.")
        self._mine(RUNS, m.group(1), uid).aborted = True
        self._json(200, {"ok": True})

    def _post(self):
        m = re.fullmatch(r"/(v1|internal)/runs/(\w+)/(results|call|submit)", self.path)
        if m and m.group(1) == "internal":
            if self.cfg["hosted"] and not self._peer_is_loopback():
                raise Refused(403, "forbidden", "Not from here.")
            uid = None
        elif self.path in ("/v1/drafts", "/v1/runs") or m:
            uid = self._user()                  # before the body is read
        else:
            raise Refused(404, "not_found", "No such route.")
        try:
            body = self._body()
        except (ValueError, json.JSONDecodeError) as e:
            return self._json(400, {"error": "bad_request", "message": str(e)})
        cfg = self.cfg
        if self.path == "/v1/drafts":
            self._limit(uid)
            try:
                job = Draft(body.get("input") or {}, cfg["model"], cfg["workdir"], cfg["internal_url"])
            except ValueError as e:
                return self._json(400, {"error": "bad_input", "message": str(e)})
            self._metered(job, uid, "draft")
            job.answer = None
            RUNS[job.id] = DRAFTS[job.id] = job
            _prune_drafts()
            threading.Thread(target=_draft_work, args=(job,), daemon=True).start()
            return self._json(202, {"draft": job.id})
        if self.path == "/v1/runs":
            self._limit(uid)
            try:
                run = Run(body.get("input") or {}, cfg["model"], cfg["workdir"], cfg["internal_url"])
            except ValueError as e:
                return self._json(400, {"error": "bad_input", "message": str(e)})
            self._metered(run, uid, "run")
            RUNS[run.id] = run
            threading.Thread(target=run.work, daemon=True).start()
            return self._json(200, {"run": run.id, "actions": []})
        kind, what = m.group(1), m.group(3)
        if kind == "v1":
            if what != "results":
                raise Refused(404, "not_found", "No such route.")
            run = self._mine(RUNS, m.group(2), uid)
            if not isinstance(run, Run):
                raise Refused(404, "no_such_run", "There is no such run.")
            run.give_results(body.get("results"))
            actions = run.take_actions()
            if actions:
                return self._json(200, {"actions": actions, "progress": run.progress})
            if run.done.is_set():
                return self._json(200, {"report": run.report})
            return self._json(200, {"actions": [], "progress": run.progress})
        run = RUNS.get(m.group(2))
        if not run:
            return self._json(404, {"error": "no_such_run"})
        if self.headers.get("Authorization") == f"Bearer {run.token}":
            if what == "call":
                return self._json(200, run.tool_call(body.get("unit"), body.get("tool"), body.get("args") or {}))
            if what == "submit":
                return self._json(200, run.submit(body.get("unit"), body.get("report") or {}))
        self._json(403, {"error": "forbidden"})


def _draft_work(job):
    """The draft conversation, in its own thread; its answer waits in DRAFTS for its owner."""
    try:
        draft, usage, cause = job.work()
        job.answer = {"state": "done", "draft": draft, "usage": usage} if draft is not None else \
            {"state": "failed", "message": f"the draft conversation gave no accepted draft ({cause})", "usage": usage}
    except Exception as e:  # noqa: BLE001
        job.answer = {"state": "failed", "message": f"the draft job hit an error ({type(e).__name__})"}
    finally:
        job.finished = time.time()
        RUNS.pop(job.id, None)


def _prune_drafts():
    cutoff = time.time() - DRAFT_KEEP
    for did, job in list(DRAFTS.items()):
        if job.answer is not None and getattr(job, "finished", time.time()) < cutoff:
            DRAFTS.pop(did, None)


def make_server(port=0, model=DEFAULT_MODEL, host="127.0.0.1", hosted=False, data_dir=None, workdir=None):
    """The service, not yet serving. Hosted, it opens the results store's database in data_dir for its users."""
    store = None
    if hosted:
        from store.db import Store
        from store.hosting import data_dir as default_data
        store = Store(data_dir or default_data(), hosted=True)
    cfg = {"model": model, "workdir": workdir or default_workdir(), "hosted": bool(hosted), "store": store}
    httpd = ThreadingHTTPServer((host, port), type("ServiceHandler", (Handler,), {"cfg": cfg}))
    httpd.daemon_threads = True
    # service/proxy.py, started by each conversation on this machine, reaches the internal routes here.
    cfg["internal_url"] = f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.cfg = cfg
    return httpd


def serve(port=8790, model=DEFAULT_MODEL, host=None, hosted=None, data_dir=None):
    from store.hosting import bind_host, hosted_from_env
    hosted = hosted_from_env() if hosted is None else hosted
    host = bind_host(host, hosted)
    httpd = make_server(port, model, host, hosted, data_dir)
    cfg = httpd.cfg
    where = f"http://{host}:{httpd.server_address[1]}"
    if hosted:
        users = len(cfg["store"].list_users())
        print(f"verifier service (hosted) on {where} (model {model}, users from {cfg['store'].db_path}: {users})", flush=True)
        if not users:
            print("No users yet: add one with groundtruth.py users add <name> --limit DOLLARS", flush=True)
    else:
        print(f"verifier service on {where} (model {model}, work dir {cfg['workdir']})", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--host")
    ap.add_argument("--data")
    a = ap.parse_args()
    serve(a.port, a.model, a.host, data_dir=a.data)
