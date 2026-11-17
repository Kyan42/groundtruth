"""A stand-in for the results store, so the dashboard can be worked on without it.

Serves dashboard/ as static files and answers the read routes of store/CONTRACT.md
from dashboard/samples/*.json (the run id is the sample's file name) and
dashboard/samples/files/<name>/... , and the feedback routes from memory, starting from
dashboard/samples/feedback/<name>.json when there is one (nothing is written to disk).
Run summaries are built by store/db.py's own functions, so they match the store's exactly; run it from the repo.
Standard library only; listens on 127.0.0.1.

    python dashboard/dev_server.py [--port 8911] [--samples DIR]
"""
import argparse
import json
import mimetypes
import os
import re
import secrets
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from store.db import claims_of, commit_of  # noqa: E402 -- the same run summary as the store's

TYPES ={".webm": "video/webm", ".png": "image/png", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".html": "text/html; charset=utf-8", ".svg": "image/svg+xml",
         ".json": "application/json", ".jpg": "image/jpeg", ".mp4": "video/mp4"}
SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")


def content_type(path: Path) -> str:
    return TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def inside(root: Path, rel: str):
    """The file at rel under root, or None if rel is unsafe or leaves root."""
    if not rel or rel.startswith("/") or "\\" in rel or any(p in ("", ".", "..") for p in rel.split("/")):
        return None
    target = (root / rel).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return None
    return target if target.is_file() else None


class Samples:
    def __init__(self, folder: Path):
        self.folder = folder

    def ids(self):
        return sorted(p.stem for p in self.folder.glob("*.json"))

    def document(self, run_id: str):
        if not SAFE_ID.match(run_id):
            return None
        path = self.folder / f"{run_id}.json"
        return json.loads(path.read_text()) if path.is_file() else None

    def summary(self, run_id: str, doc: dict) -> dict:
        totals = doc.get("totals") or {}
        return {"id": run_id, "project": doc.get("project"), "feature": doc.get("feature"), "run": doc.get("run"),
                "ran_at": doc.get("ran_at"), "uploaded_at": doc.get("ran_at"), "complete": True,
                "properties": totals.get("properties") or {}, "cases": totals.get("cases") or {},
                "commit": commit_of(doc), "claims": claims_of(doc)}


FEEDBACK_KINDS = ("not_what_i_meant", "wrong_result", "right")


class Feedback:
    """Feedback per run, kept in memory, as the store keeps it (store/CONTRACT.md)."""

    def __init__(self, folder: Path):
        self.lock = threading.Lock()
        self.by_run = {}
        for path in sorted((folder / "feedback").glob("*.json")) if (folder / "feedback").is_dir() else []:
            self.by_run[path.stem] = list(json.loads(path.read_text()))

    def list(self, run_id):
        with self.lock:
            return [dict(x) for x in self.by_run.get(run_id, [])]

    def add(self, run_id, prop, case, kind, note):
        entry = {"id": secrets.token_hex(8), "property": prop, "case": case, "kind": kind, "note": note,
                 "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")}
        with self.lock:
            self.by_run.setdefault(run_id, []).append(entry)
        return dict(entry)

    def delete(self, run_id, fid):
        with self.lock:
            items = self.by_run.get(run_id, [])
            keep = [x for x in items if x["id"] != fid]
            self.by_run[run_id] = keep
            return len(keep) < len(items)


class Handler(BaseHTTPRequestHandler):
    samples: Samples = None
    feedback: Feedback = None
    server_version = "groundtruth-dev"

    def log_message(self, *args):
        if os.environ.get("GT_DEV_LOG"):
            super().log_message(*args)

    def send_json(self, status: int, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urlsplit(self.path)
        path = unquote(url.path)
        if path == "/health":
            return self.send_json(200, {"ok": True, "store": 1})
        if path == "/v1/runs":
            return self.list_runs(parse_qs(url.query))
        if path == "/v1/projects":
            return self.list_projects()
        m = re.match(r"^/v1/runs/([^/]+)$", path)
        if m:
            return self.one_run(m.group(1))
        m = re.match(r"^/v1/runs/([^/]+)/feedback$", path)
        if m:
            if self.samples.document(m.group(1)) is None:
                return self.send_json(404, {"error": "There is no such run."})
            return self.send_json(200, {"feedback": self.feedback.list(m.group(1))})
        m = re.match(r"^/v1/runs/([^/]+)/files/(.+)$", path)
        if m:
            run_id, rel = m.group(1), m.group(2)
            if not SAFE_ID.match(run_id) or self.samples.document(run_id) is None:
                return self.send_json(404, {"error": "There is no run with that id."})
            target = inside(self.samples.folder / "files" / run_id, rel)
            if target is None:
                return self.send_json(404, {"error": "This run has no file at that path."})
            return self.send_file(target, ranges=True)
        if path.startswith("/v1/"):
            return self.send_json(404, {"error": "There is nothing at this address."})
        rel = path.lstrip("/") or "index.html"
        if rel.endswith("/"):
            rel += "index.html"
        target = inside(HERE, rel)
        if target is None or target.suffix == ".py":
            return self.send_json(404, {"error": "There is no page at this address."})
        return self.send_file(target, ranges=False)

    # ---------- feedback ----------
    def do_POST(self):
        path = unquote(urlsplit(self.path).path)
        m = re.match(r"^/v1/runs/([^/]+)/feedback$", path)
        if not m:
            return self.send_json(404, {"error": "No such route."})
        doc = self.samples.document(m.group(1))
        if doc is None:
            return self.send_json(404, {"error": "There is no such run."})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"null")
        except ValueError:
            return self.send_json(400, {"error": "The body is not JSON."})
        if not isinstance(body, dict):
            return self.send_json(400, {"error": "The body must be {\"property\", \"case\", \"kind\", \"note\"}."})
        kind, prop, case, note = body.get("kind"), body.get("property"), body.get("case"), body.get("note")
        if kind not in FEEDBACK_KINDS:
            return self.send_json(400, {"error": "kind must be not_what_i_meant, wrong_result or right."})
        props = {p.get("id"): p for p in doc.get("properties") or [] if isinstance(p, dict)}
        if not isinstance(prop, str) or prop not in props:
            return self.send_json(400, {"error": "property must be the id of a property of this run."})
        if case is not None and (not isinstance(case, str) or case not in {c.get("id") for c in props[prop].get("cases") or [] if isinstance(c, dict)}):
            return self.send_json(400, {"error": f"case must be the id of a case of {prop} in this run."})
        note = "" if note is None else note
        if not isinstance(note, str) or len(note) > 1000:
            return self.send_json(400, {"error": "note must be text of at most 1,000 characters."})
        return self.send_json(201, self.feedback.add(m.group(1), prop, case, kind, note.strip()))

    def do_DELETE(self):
        path = unquote(urlsplit(self.path).path)
        m = re.match(r"^/v1/runs/([^/]+)/feedback/([^/]+)$", path)
        if not m:
            return self.send_json(404, {"error": "No such route."})
        if self.samples.document(m.group(1)) is None or not self.feedback.delete(m.group(1), m.group(2)):
            return self.send_json(404, {"error": "There is no such feedback."})
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def list_runs(self, query):
        project = (query.get("project") or [""])[0]
        feature = (query.get("feature") or [""])[0]
        try:
            limit = int((query.get("limit") or ["50"])[0])
        except ValueError:
            return self.send_json(400, {"error": "The limit must be a whole number."})
        rows = []
        for run_id in self.samples.ids():
            doc = self.samples.document(run_id)
            if doc is None or (project and doc.get("project") != project) or (feature and doc.get("feature") != feature):
                continue
            rows.append(self.samples.summary(run_id, doc))
        rows.sort(key=lambda r: r.get("ran_at") or "", reverse=True)
        return self.send_json(200, {"runs": rows[:max(0, limit)]})

    def list_projects(self):
        by = {}
        for run_id in self.samples.ids():
            doc = self.samples.document(run_id)
            if doc is None:
                continue
            by.setdefault(doc.get("project"), []).append(self.samples.summary(run_id, doc))
        out = []
        for name, rows in sorted(by.items(), key=lambda kv: str(kv[0])):
            rows.sort(key=lambda r: r.get("ran_at") or "", reverse=True)
            out.append({"name": name, "runs": len(rows), "latest": rows[0] if rows else None})
        return self.send_json(200, {"projects": out})

    def one_run(self, run_id):
        doc = self.samples.document(run_id)
        if doc is None:
            return self.send_json(404, {"error": "There is no run with that id."})
        return self.send_json(200, {"id": run_id, "uploaded_at": doc.get("ran_at"), "complete": True,
                                    "files_base": f"/v1/runs/{run_id}/files/", "document": doc})

    def send_file(self, target: Path, ranges: bool):
        size = target.stat().st_size
        start, end, status = 0, size - 1, 200
        header = self.headers.get("Range") if ranges else None
        if header:
            m = re.match(r"^bytes=(\d*)-(\d*)$", header.strip())
            if not m or (m.group(1) == "" and m.group(2) == ""):
                return self.unsatisfiable(size)
            if m.group(1) == "":
                start = max(0, size - int(m.group(2)))
            else:
                start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
            if start >= size or start > end:
                return self.unsatisfiable(size)
            status = 206
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", content_type(target))
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        if ranges:
            self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with target.open("rb") as f:
            f.seek(start)
            left = length
            try:
                while left > 0:
                    chunk = f.read(min(65536, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def unsatisfiable(self, size):
        self.send_response(416)
        self.send_header("Content-Range", f"bytes */{size}")
        self.send_header("Content-Length", "0")
        self.end_headers()


def make_server(port: int = 8911, samples: Path = HERE / "samples") -> ThreadingHTTPServer:
    handler = type("SampleHandler", (Handler,), {"samples": Samples(Path(samples)), "feedback": Feedback(Path(samples))})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    return server


def main(argv=None):
    ap = argparse.ArgumentParser(description="Serve the dashboard with sample runs in place of the store.")
    ap.add_argument("--port", type=int, default=8911)
    ap.add_argument("--samples", type=Path, default=HERE / "samples", help="folder of run documents (*.json) and files/")
    args = ap.parse_args(argv)
    server = make_server(args.port, args.samples)
    print(f"Dashboard at http://127.0.0.1:{server.server_address[1]}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    sys.exit(main())
