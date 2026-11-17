"""Upload one finished run to the results store (store/CONTRACT.md). Only the command line calls this, after the
run is saved; no tool the verifier service can ask for leads here.

  push_run(run_dir, repo, store_url, token, project=None, say=print) -> {"id", "url"}

What is sent: the run document (toolserver/rundoc.py) and the films and screenshots it names, under the paths the
document gives them. Nothing is written into the run folder. Every failure is a PushError with a plain message.
"""
import http.client
import json
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

MAX_FILE = 50 * 1024 * 1024
MAX_DOCUMENT = 5 * 1024 * 1024


class PushError(Exception):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: the token goes only to the store we were told about."""

    def redirect_request(self, *args, **kwargs):
        return None


_open = open_no_redirect = urllib.request.build_opener(_NoRedirect).open    # also used for the verifier service and feedback


def _call(store, method, path, token, body=None, length=None, ctype="application/json", timeout=120):
    data = json.dumps(body).encode() if ctype == "application/json" and body is not None else body
    headers = {"Authorization": f"Bearer {token}", "Content-Type": ctype, "User-Agent": "groundtruth-push/1"}
    if length is not None:
        headers["Content-Length"] = str(length)
    req = urllib.request.Request(store + path, data=data, method=method, headers=headers)
    try:
        with _open(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error") or e.reason
        except Exception:  # noqa: BLE001
            msg = e.reason
        if 300 <= e.code < 400:
            msg = "it tried to send the upload somewhere else, which is not followed"
        raise PushError(f"the store answered {e.code} to {method} {path.split('/files/')[0]}: {msg}") from None
    except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
        raise PushError(f"couldn't reach the results store at {store} ({getattr(e, 'reason', e)})") from None
    except ValueError:
        raise PushError(f"the store at {store} did not answer with JSON; is that address really the results store?") from None


def push_run(run_dir, repo, store_url, token, project=None, say=print) -> dict:
    from . import rundoc
    run_dir = Path(run_dir)
    store = store_url.rstrip("/")
    if not (run_dir / "run.json").is_file():
        raise PushError(f"run {run_dir.name} did not finish (it has no run.json), so there is nothing to upload")
    if not token:
        raise PushError("no token for the results store")
    try:
        doc, files = rundoc.build(run_dir, repo=Path(repo) if repo else None, project=project)
    except Exception as e:  # noqa: BLE001
        raise PushError(f"couldn't build the run document from {run_dir.name} ({type(e).__name__}: {e})") from None
    if len(json.dumps(doc).encode()) > MAX_DOCUMENT:
        raise PushError("the run document is larger than the store takes (5 MB)")
    local, listed = {}, []
    for f in files:
        size = f["local"].stat().st_size
        if size > MAX_FILE:
            raise PushError(f"{f['path']} is {size / 1e6:.0f} MB, more than the store takes for one file (50 MB)")
        local[f["path"]] = (f["local"], size)
        listed.append({"path": f["path"], "kind": f["kind"], "size": size})    # never the local path
    total = sum(s for _, s in local.values())
    say(f"Uploading run {doc['run']} of {doc['project']}/{doc['feature']} to {store}: "
        f"the document and {len(listed)} file(s), {total / 1e6:.1f} MB")
    made = _call(store, "POST", "/v1/runs", token, {"document": doc, "files": listed})
    rid, missing = made.get("id"), made.get("missing") or []
    if not isinstance(rid, str) or not rid:
        raise PushError("the store did not give the run an id")
    for i, path in enumerate(missing, 1):
        if path not in local:
            raise PushError(f"the store asked for {path}, which this upload did not declare")
        src, size = local[path]
        with open(src, "rb") as fh:
            _call(store, "PUT", f"/v1/runs/{quote(rid)}/files/{quote(path)}", token, fh, length=size,
                  ctype="application/octet-stream", timeout=600)
        if i == len(missing) or i % 10 == 0:
            say(f"  sent {i} of {len(missing)} file(s)")
    _call(store, "POST", f"/v1/runs/{quote(rid)}/complete", token, {})
    return {"id": rid, "url": f"{store}/run.html?run={rid}", "project": doc["project"], "feature": doc["feature"]}
