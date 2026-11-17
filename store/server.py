"""The results store's HTTP service (the routes in CONTRACT.md), plus the dashboard's files on the same address.

    serve(port=8900, data_dir=".store")     runs until stopped
    make_server(data_dir, port=0, dashboard_dir=None, quiet=False, hosted=False, ...) -> server, for tests

Locally it listens on 127.0.0.1. Uploads need the bearer token; reads from this machine's browser act as the one
local user. Each request opens its own database connection, so the threaded server is safe.

Hosted (GT_HOSTED=1, see store/hosting.py) nothing is allowed for looking local: every read needs a bearer token or a
page's sign-in cookie (POST /v1/session), every page but the sign-in page needs signing in, a page's change made with
the cookie must come from this site's own origin, and the verifier service's public routes are forwarded from
/verifier/... to the service on this machine, so people have one address.
"""
import hashlib
import http.client
import json
import mimetypes
import os
import re
import tempfile
import threading
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from .db import FEEDBACK_KINDS, SESSION_DAYS, Store

ROOT = Path(__file__).resolve().parent.parent
MAX_FILE = 50 * 1024 * 1024          # one film or screenshot
MAX_DOCUMENT = 5 * 1024 * 1024       # the run document, as JSON
MAX_BODY = MAX_DOCUMENT + 1024 * 1024  # the upload's body: the document plus its list of files
MAX_FILES = 10000
CHUNK = 256 * 1024
TYPES = {".webm": "video/webm", ".png": "image/png", ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".json": "application/json", ".svg": "image/svg+xml"}
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
RUN_ID = r"[A-Za-z0-9_-]{1,64}"
SESSION_COOKIE, SIGNED_IN_COOKIE = "gt_session", "gt_signed_in"   # the second only tells the pages to show "Sign out"
SIGN_IN_PAGE = "/signin.html"
# Hosted: what anyone may fetch without signing in (the sign-in page and what it needs; none of it shows data).
OPEN_ASSETS = {"signin.html", "style.css", "gt-mark.svg", "js/signin.js"}
# Hosted: the verifier service's public routes, forwarded from /verifier<path> to the service on this machine. Exactly
# these: never /internal/*, which the service would otherwise see arriving from this machine.
JOB_ID = r"[A-Za-z0-9_]{1,64}"
FORWARDED = [("GET", r"/health"), ("POST", r"/v1/runs"), ("POST", rf"/v1/runs/{JOB_ID}/results"), ("DELETE", rf"/v1/runs/{JOB_ID}"),
             ("POST", r"/v1/drafts"), ("GET", rf"/v1/drafts/{JOB_ID}")]
FORWARD_PREFIX = "/verifier"
FORWARD_TIMEOUT = 60          # every forwarded route answers well within this (results long-polls ~20 s; drafts are polled)
MAX_FORWARD_BODY = 4 * 1024 * 1024 + 1024


def content_type(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return TYPES.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream"


def bad_path(path) -> str | None:
    """Why a file path can't be used, or None. Paths are names inside one run, never followed on disk."""
    if not isinstance(path, str) or not path:
        return "a file path is empty"
    if len(path) > 512:
        return "a file path is too long"
    if ".." in path or path.startswith("/") or "\\" in path or "\x00" in path:
        return "a file path may not contain '..', start with '/', or contain a backslash or a null byte"
    if any(ord(ch) < 32 for ch in path) or any(part in ("", ".") for part in path.split("/")):
        return "a file path has an empty part, a '.' part or a control character"
    return None


class Refusal(Exception):
    def __init__(self, status, text, **extra):
        super().__init__(text)
        self.status, self.message, self.extra = status, text, extra


class Handler(BaseHTTPRequestHandler):
    store: Store = None
    dashboard: Path = None
    quiet = False
    hosted = False
    public_url = None         # hosted: the origin people use (GT_PUBLIC_URL); else the request's Host is the origin
    service_url = None        # hosted: the verifier service on this machine, for /verifier/ forwarding
    server_version = "groundtruth-store/1"

    def log_message(self, fmt, *args):
        if not self.quiet:
            super().log_message(fmt, *args)

    # ------------------------------------------------------------------ answers
    def _json(self, status, obj):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _empty(self, status):
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _plain(self, status, text):
        body = (text + "\n").encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _drain(self):
        """Read and drop a small refused body, so the client sees our answer rather than a broken connection."""
        if self.body_read:
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if 0 < n <= 1024 * 1024:
            self.rfile.read(n)
        elif n:
            self.close_connection = True

    # ------------------------------------------------------------------ who is asking
    def _cookie(self, name):
        try:
            jar = SimpleCookie(self.headers.get("Cookie") or "")
        except CookieError:
            return None
        return jar[name].value if name in jar else None

    def _bearer(self, auth):
        kind, _, token = auth.partition(" ")
        uid = self.store.user_by_token(token.strip()) if kind.lower() == "bearer" and token.strip() else None
        if uid is None:
            raise Refusal(401, "That token is not valid for this store.")
        return uid

    def _signed_in(self):
        """Hosted: the user of a valid bearer token (an invalid one is refused, never passed over), else of the sign-in
        cookie, else None."""
        auth = self.headers.get("Authorization")
        if auth is not None:
            return self._bearer(auth)
        uid = self.store.session_user(self._cookie(SESSION_COOKIE))
        if uid is not None:
            self.via_cookie = True
        return uid

    def _same_origin(self, required: bool):
        """A change made with the sign-in cookie must come from this site's own pages: the browser's Origin header
        must be this site's origin (GT_PUBLIC_URL, else the Host the request was sent to)."""
        origin = self.headers.get("Origin")
        if not origin or origin == "null":
            if required:
                raise Refusal(403, "A change made from a signed-in page must come from this site's own pages.")
            return
        try:
            got = urlsplit(origin)
            netloc = got.netloc.lower()
        except ValueError:
            netloc = ""
        if self.public_url:
            want = urlsplit(self.public_url)
            ok = got.scheme == want.scheme and netloc == want.netloc.lower()
        else:
            ok = bool(netloc) and netloc == (self.headers.get("Host") or "").lower()
        if not ok or (self.headers.get("Sec-Fetch-Site") or "same-origin") not in ("same-origin", "none"):
            raise Refusal(403, "A change made from a signed-in page must come from this site's own pages.")

    def _browser_user(self):
        """For feedback: the token's user, or (with no token) the local user when a browser on this machine asks, as
        for a read. A write from a page on another site is refused: its Origin is not this machine.
        Hosted: the token's user, or the signed-in page's user, whose changes must carry this site's own Origin."""
        if self.hosted:
            uid = self._user(False)
            if self.via_cookie and self.command in ("POST", "DELETE"):
                self._same_origin(required=True)
            return uid
        if self.headers.get("Authorization") is None and self.command in ("POST", "DELETE"):
            origin = self.headers.get("Origin")
            if origin and origin != "null":
                try:
                    host = urlsplit(origin).hostname or ""
                except ValueError:
                    host = ""
                if host.lower() not in LOCAL_HOSTS:
                    raise Refusal(403, "Feedback can be sent without a token only from a page on this machine.")
        return self._user(False)

    def _user(self, write: bool):
        auth = self.headers.get("Authorization")
        if self.hosted:
            if write and auth is None:          # uploads only ever with the token, never with a page's cookie
                raise Refusal(401, "Uploading needs your token (Authorization: Bearer <token>).")
            uid = self._signed_in()
            if uid is None:
                raise Refusal(401, "Sign in to see this.", sign_in=SIGN_IN_PAGE)
            return uid
        if auth is not None:
            return self._bearer(auth)
        if write:
            raise Refusal(401, "Uploading needs the store's token (Authorization: Bearer <token>).")
        # Reads without a token act as the one local user, but only when the browser is talking to this machine by
        # name, so a web page on another site can't reach the store through a look-alike host name.
        try:
            host = urlsplit("//" + (self.headers.get("Host") or "")).hostname or ""
        except ValueError:
            host = ""
        if host.lower() not in LOCAL_HOSTS:
            raise Refusal(401, "Reading without a token works only at 127.0.0.1 or localhost.")
        return self.store.local_user()

    # ------------------------------------------------------------------ routing
    def _route(self):
        self.body_read = False
        self.via_cookie = False
        url = urlsplit(self.path)
        path = url.path
        try:
            if path == "/health" and self.command in ("GET", "HEAD"):
                return self._json(200, {"ok": True, "store": 1})
            if path == "/v1/session":
                return self._session()
            if self.hosted and (path == FORWARD_PREFIX or path.startswith(FORWARD_PREFIX + "/")):
                sub = path[len(FORWARD_PREFIX):]
                if not any(self.command == method and re.fullmatch(pattern, sub) for method, pattern in FORWARDED):
                    raise Refusal(404, "No such route.")
                return self._forward(sub)
            if path == "/v1" or path.startswith("/v1/"):
                return self._api(path, parse_qs(url.query))
            if self.command not in ("GET", "HEAD"):
                raise Refusal(405, "Only GET works outside /v1/.")
            return self._static(path)
        except Refusal as e:
            self._drain()
            return self._json(e.status, {"error": e.message, **e.extra})
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as e:  # noqa: BLE001 -- answer something plain rather than drop the connection
            self.log_error("store error: %r", e)
            try:
                self._json(500, {"error": f"The store hit an error: {type(e).__name__}."})
            except OSError:
                pass

    do_GET = do_HEAD = do_POST = do_PUT = do_DELETE = _route

    def _api(self, path, query):
        m = re.fullmatch(rf"/v1/runs/({RUN_ID})/files/(.+)", path)
        if m:
            rel = unquote(m.group(2))
            why = bad_path(rel)
            if why:
                raise Refusal(400, why[0].upper() + why[1:] + ".")
            if self.command == "PUT":
                return self._put_file(self._user(True), m.group(1), rel)
            if self.command in ("GET", "HEAD"):
                return self._get_file(self._user(False), m.group(1), rel)
            raise Refusal(405, "Files can be sent with PUT or read with GET.")
        m = re.fullmatch(rf"/v1/runs/({RUN_ID})/feedback(?:/({RUN_ID}))?", path)
        if m:
            return self._feedback(m.group(1), m.group(2))
        if path == "/v1/runs" and self.command == "POST":
            return self._post_run(self._user(True))
        m = re.fullmatch(rf"/v1/runs/({RUN_ID})/complete", path)
        if m and self.command == "POST":
            missing = self.store.complete(self._user(True), m.group(1))
            if missing is None:
                raise Refusal(404, "There is no such run.")
            if missing:
                raise Refusal(409, f"{len(missing)} file(s) have not arrived yet.", missing=missing)
            return self._json(200, {"missing": []})
        if self.command not in ("GET", "HEAD"):
            raise Refusal(405 if path in ("/v1/runs", "/v1/projects") else 404, "No such route.")
        if path == "/v1/projects":
            return self._json(200, {"projects": self.store.projects(self._user(False))})
        if path == "/v1/runs":
            one = lambda k: (query.get(k) or [None])[0]  # noqa: E731
            limit = one("limit") or "50"
            if not limit.isdigit() or not 1 <= int(limit) <= 500:
                raise Refusal(400, "limit must be a whole number from 1 to 500.")
            return self._json(200, {"runs": self.store.runs(self._user(False), one("project"), one("feature"), int(limit))})
        m = re.fullmatch(rf"/v1/runs/({RUN_ID})", path)
        if m:
            run = self.store.run(self._user(False), m.group(1))
            if run is None:
                raise Refusal(404, "There is no such run.")
            return self._json(200, run)
        raise Refusal(404, "No such route.")

    # ------------------------------------------------------------------ signing in (pages, hosted)
    def _cookies(self, sid, max_age):
        secure = "; Secure" if self.hosted else ""
        return [f"{SESSION_COOKIE}={sid}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Lax{secure}",
                f"{SIGNED_IN_COOKIE}={'1' if sid else ''}; Path=/; Max-Age={max_age}; SameSite=Lax{secure}"]

    def _session_answer(self, status, obj, cookies):
        body = b"" if obj is None else json.dumps(obj).encode()
        self.send_response(status)
        if obj is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for c in cookies:
            self.send_header("Set-Cookie", c)
        self.end_headers()
        self.wfile.write(body)

    def _session(self):
        """GET: who is asking. POST {"token"}: sign a page in with a session cookie (hosted). DELETE: sign out."""
        if self.command in ("GET", "HEAD"):
            uid = self._user(False)
            how = "token" if self.headers.get("Authorization") else "cookie" if self.via_cookie else "local"
            return self._json(200, {"user": self.store.user_name(uid), "via": how})
        if not self.hosted:
            raise Refusal(404, "Signing in is only needed on a hosted store.")
        if self.command == "POST":
            self._same_origin(required=False)
            n = self._body_length(4096)
            self.body_read = True
            try:
                body = json.loads(self.rfile.read(n))
            except ValueError:
                raise Refusal(400, "The body is not JSON.") from None
            token = body.get("token") if isinstance(body, dict) else None
            uid = self.store.user_by_token(token.strip()) if isinstance(token, str) and token.strip() else None
            if uid is None:
                raise Refusal(401, "That token is not valid here. Check that you copied all of it.")
            sid = self.store.new_session(uid)
            return self._session_answer(200, {"user": self.store.user_name(uid)}, self._cookies(sid, SESSION_DAYS * 86400))
        if self.command == "DELETE":
            sid = self._cookie(SESSION_COOKIE)
            if sid:
                self._same_origin(required=True)
                self.store.end_session(sid)
            return self._session_answer(204, None, self._cookies("", 0))
        raise Refusal(405, "Sign in with POST, sign out with DELETE.")

    # ------------------------------------------------------------------ the verifier, on the same address (hosted)
    def _forward(self, sub):
        """Pass one public verifier request to the service on this machine and its answer back, in chunks. Only the
        token, the body and its type go on; never a cookie, so a page's sign-in can't start or spend anything."""
        n = 0
        if self.command == "POST":
            if self.headers.get("Transfer-Encoding"):
                raise Refusal(411, "The request must say its length (Content-Length).")
            n = self._body_length(MAX_FORWARD_BODY)
        headers = {"Content-Length": str(n), "Host": urlsplit(self.service_url).netloc}
        for k in ("Authorization", "Content-Type", "User-Agent"):
            if self.headers.get(k) is not None:
                headers[k] = self.headers[k]
        u = urlsplit(self.service_url)
        conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=FORWARD_TIMEOUT)
        try:
            try:
                conn.putrequest(self.command, sub, skip_host=True, skip_accept_encoding=True)
                for k, v in headers.items():
                    conn.putheader(k, v)
                conn.endheaders()
                self.body_read = True
                left = n
                while left:
                    chunk = self.rfile.read(min(CHUNK, left))
                    if not chunk:
                        raise Refusal(400, "The request ended before its stated length.")
                    conn.send(chunk)
                    left -= len(chunk)
                r = conn.getresponse()
            except (OSError, http.client.HTTPException):
                msg = "The verifier service could not be reached or did not answer in time. Try again in a minute."
                raise Refusal(502, msg, message=msg) from None
            self.send_response(r.status)
            for k in ("Content-Type", "Content-Length"):
                if r.getheader(k):
                    self.send_header(k, r.getheader(k))
            if not r.getheader("Content-Length"):
                self.close_connection = True
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            while True:
                chunk = r.read(CHUNK)
                if not chunk:
                    break
                self.wfile.write(chunk)
        finally:
            conn.close()

    # ------------------------------------------------------------------ feedback
    def _feedback(self, run_id, fid):
        uid = self._browser_user()
        if fid is not None:
            if self.command != "DELETE":
                raise Refusal(405, "One piece of feedback can only be deleted.")
            if self.store.feedback(uid, run_id) is None or not self.store.delete_feedback(uid, run_id, fid):
                raise Refusal(404, "There is no such feedback.")
            return self._empty(204)
        if self.command in ("GET", "HEAD"):
            items = self.store.feedback(uid, run_id)
            if items is None:
                raise Refusal(404, "There is no such run.")
            return self._json(200, {"feedback": items})
        if self.command != "POST":
            raise Refusal(405, "Feedback is read with GET and sent with POST.")
        run = self.store.run(uid, run_id)
        if run is None:
            raise Refusal(404, "There is no such run.")
        n = self._body_length(64 * 1024)
        self.body_read = True
        try:
            body = json.loads(self.rfile.read(n))
        except ValueError:
            raise Refusal(400, "The body is not JSON.") from None
        if not isinstance(body, dict):
            raise Refusal(400, "The body must be {\"property\", \"case\", \"kind\", \"note\"}.")
        kind, prop, case, note = body.get("kind"), body.get("property"), body.get("case"), body.get("note")
        if kind not in FEEDBACK_KINDS:
            raise Refusal(400, "kind must be not_what_i_meant, wrong_result or right.")
        props = {p.get("id"): p for p in run["document"].get("properties") or [] if isinstance(p, dict)}
        if not isinstance(prop, str) or prop not in props:
            raise Refusal(400, "property must be the id of a property of this run.")
        if case is not None and (not isinstance(case, str) or case not in {c.get("id") for c in props[prop].get("cases") or [] if isinstance(c, dict)}):
            raise Refusal(400, f"case must be the id of a case of {prop} in this run.")
        if note is None:
            note = ""
        if not isinstance(note, str) or len(note) > 1000:
            raise Refusal(400, "note must be text of at most 1,000 characters.")
        return self._json(201, self.store.add_feedback(uid, run_id, prop, case, kind, note.strip()))

    # ------------------------------------------------------------------ uploads
    def _body_length(self, most):
        raw = self.headers.get("Content-Length")
        if raw is None or not raw.strip().isdigit():
            raise Refusal(411, "The request must say its length (Content-Length).")
        n = int(raw)
        if n > most:
            raise Refusal(413, f"The request is too large (at most {most // (1024 * 1024)} MB).")
        return n

    def _post_run(self, uid):
        n = self._body_length(MAX_BODY)
        self.body_read = True
        try:
            body = json.loads(self.rfile.read(n))
        except ValueError:
            raise Refusal(400, "The body is not JSON.") from None
        doc, files = (body.get("document"), body.get("files")) if isinstance(body, dict) else (None, None)
        if not isinstance(doc, dict):
            raise Refusal(400, "The body needs a run document under \"document\".")
        for key in ("project", "feature", "run"):
            if not isinstance(doc.get(key), str) or not doc[key] or len(doc[key]) > 200:
                raise Refusal(400, f"The run document needs a \"{key}\" (text, at most 200 characters).")
        if len(json.dumps(doc).encode()) > MAX_DOCUMENT:
            raise Refusal(413, "The run document is larger than 5 MB.")
        if not isinstance(files, list) or len(files) > MAX_FILES:
            raise Refusal(400, f"\"files\" must be a list of at most {MAX_FILES} files.")
        checked, seen = [], set()
        for f in files:
            if not isinstance(f, dict):
                raise Refusal(400, "Each file must be {\"path\", \"kind\", \"size\"}.")
            why = bad_path(f.get("path"))
            if why:
                raise Refusal(400, why[0].upper() + why[1:] + ".")
            if f["path"] in seen:
                raise Refusal(400, f"The file {f['path']} is listed twice.")
            if f.get("kind") not in ("clip", "shot"):
                raise Refusal(400, f"The file {f['path']} needs a kind of \"clip\" or \"shot\".")
            size = f.get("size")
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                raise Refusal(400, f"The file {f['path']} needs a size in bytes.")
            if size > MAX_FILE:
                raise Refusal(413, f"The file {f['path']} is larger than 50 MB.")
            seen.add(f["path"])
            checked.append({"path": f["path"], "kind": f["kind"], "size": size, "content_type": content_type(f["path"])})
        rid, missing = self.store.put_run(uid, doc, checked)
        return self._json(201, {"id": rid, "missing": missing})

    def _put_file(self, uid, run_id, rel):
        row = self.store.declared(uid, run_id, rel)
        if row is None:
            raise Refusal(404, "That run has no such file declared.")
        n = self._body_length(MAX_FILE)
        if n > row["size"]:
            raise Refusal(413, f"The file is {n} bytes, more than the {row['size']} the upload declared.")
        if n != row["size"]:
            raise Refusal(400, f"The file is {n} bytes but the upload declared {row['size']}.")
        self.body_read = True
        digest, left = hashlib.sha256(), n
        fd, tmp = tempfile.mkstemp(dir=self.store.files, prefix=".incoming-")
        try:
            with os.fdopen(fd, "wb") as out:
                while left:
                    chunk = self.rfile.read(min(CHUNK, left))
                    if not chunk:
                        raise Refusal(400, "The file ended before its stated length.")
                    out.write(chunk)
                    digest.update(chunk)
                    left -= len(chunk)
            sha = digest.hexdigest()
            os.replace(tmp, self.store.files / sha)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self.store.file_arrived(uid, run_id, rel, sha)
        return self._empty(204)

    # ------------------------------------------------------------------ reads
    def _get_file(self, uid, run_id, rel):
        row = self.store.declared(uid, run_id, rel)
        if row is None or not row["sha256"]:
            raise Refusal(404, "There is no such file." if row is None else "That file has not arrived yet.")
        self._send_file(self.store.files / row["sha256"], row["content_type"], api=True)

    def _send_file(self, path: Path, ctype: str, api=False):
        size = path.stat().st_size
        start, end = 0, size - 1
        status = 200
        rng = self.headers.get("Range")
        m = re.fullmatch(r"\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)\s*", rng or "")
        if m and (m.group(1) or m.group(2)):       # one range; a list of ranges or anything malformed gets the whole file
            a, b = m.group(1), m.group(2)
            if a:
                start, end = int(a), min(int(b), size - 1) if b else size - 1
                ok = start < size and (not b or int(b) >= start)
            else:
                n = int(b)
                start, end, ok = max(0, size - n), size - 1, n > 0 and size > 0
            if not ok:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Type", "application/json")
                body = json.dumps({"error": "That byte range is outside the file."}).encode()
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)
                return
            status = 206
        length = max(0, end - start + 1)
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if api:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(path, "rb") as f:
            f.seek(start)
            left = length
            while left:
                chunk = f.read(min(CHUNK, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def _static(self, path):
        """A file from the dashboard folder. Nothing outside it is ever served."""
        rel = unquote(path).lstrip("/") or "index.html"
        if rel.endswith("/"):
            rel += "index.html"
        if ".." in rel.split("/") or "\\" in rel or "\x00" in rel or any(part.startswith(".") for part in rel.split("/")):
            return self._plain(404, "Not found.")
        if self.hosted and rel not in OPEN_ASSETS and self._signed_in() is None:
            if rel.endswith(".html"):       # a page: to the sign-in page, which comes back here afterwards
                self.send_response(302)
                self.send_header("Location", f"{SIGN_IN_PAGE}?next={quote(self.path, safe='')}")
                self.send_header("Content-Length", "0")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            return self._plain(401, "Sign in to see this.")
        if rel == "index.html" and not (self.dashboard / "index.html").is_file():
            return self._plain(200, "The dashboard is not built yet.")
        target = (self.dashboard / rel).resolve()
        try:
            target.relative_to(self.dashboard.resolve())
        except ValueError:
            return self._plain(404, "Not found.")
        if not target.is_file():
            return self._plain(404, "Not found.")
        self._send_file(target, content_type(target.name))


def make_server(data_dir, port=0, dashboard_dir=None, quiet=False, hosted=False, host="127.0.0.1", public_url=None,
                service_url="http://127.0.0.1:8790"):
    store = Store(data_dir, hosted=hosted)
    handler = type("StoreHandler", (Handler,), {"store": store, "dashboard": Path(dashboard_dir or ROOT / "dashboard").resolve(),
                                                "quiet": quiet, "hosted": bool(hosted), "public_url": public_url,
                                                "service_url": service_url.rstrip("/")})
    srv = ThreadingHTTPServer((host, port), handler)
    srv.daemon_threads = True
    srv.store = store
    return srv


def start_in_background(data_dir, port=0, dashboard_dir=None, **kw):
    """For tests: a running server and its address."""
    srv = make_server(data_dir, port, dashboard_dir, quiet=True, **kw)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def serve(port=8900, data_dir=None, dashboard_dir=None, host=None, hosted=None):
    from .hosting import bind_host, data_dir as default_data, hosted_from_env
    hosted = hosted_from_env() if hosted is None else hosted
    host = bind_host(host, hosted)
    data_dir = Path(data_dir) if data_dir else default_data()
    public_url = os.environ.get("GT_PUBLIC_URL", "").strip().rstrip("/") or None
    service_url = os.environ.get("GT_SERVICE_URL", "").strip() or "http://127.0.0.1:8790"
    try:
        srv = make_server(data_dir, port, dashboard_dir, hosted=hosted, host=host, public_url=public_url, service_url=service_url)
    except OSError as e:
        raise SystemExit(f"The store could not start on port {port} with data in {data_dir}: {e.strerror or e}")
    store = srv.store
    if hosted:
        print(f"Results store (hosted) on http://{host}:{srv.server_address[1]}  (data in {store.data}; "
              f"{len(store.list_users())} users; verifier forwarded from /verifier/ to {service_url})", flush=True)
        if not public_url:
            print("GT_PUBLIC_URL is not set, so a signed-in page's changes are checked against the request's own Host.", flush=True)
    else:
        print(f"Results store at http://127.0.0.1:{srv.server_address[1]}  (data in {store.data})", flush=True)
        print(f"Upload token: {store.token_path}" + ("  (new token written)" if store.made_token else ""), flush=True)
    if not (srv.RequestHandlerClass.dashboard / "index.html").is_file():
        print("The dashboard is not built yet; the address shows a one-line page until it is.", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        srv.server_close()
