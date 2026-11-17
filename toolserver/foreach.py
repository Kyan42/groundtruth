"""Apply one mechanical rule to every member of a set (stdlib only).

probe_each sends one request per listed route; match_each checks every file
matching a glob. Both return the count checked and the violators; checked == 0
is reported as proving nothing.
"""
from __future__ import annotations

import http.client
import re
import urllib.error
import urllib.request
from pathlib import Path, PurePath

from .routes import _no_redirect_opener, app_relative_url, list_routes

SKIP_PARTS = {"node_modules", "__pycache__"}


def _check_expect(expect_status) -> list[int]:
    if not isinstance(expect_status, (list, tuple)) or not expect_status or \
            not all(isinstance(s, int) and not isinstance(s, bool) for s in expect_status):
        raise ValueError("expect_status must be a non-empty list of ints")
    return list(expect_status)


def _request(opener, url: str, method: str, headers: dict, body: str | None, timeout: float):
    data = body.encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=dict(headers))
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, None
    except urllib.error.HTTPError as e:  # 3xx (redirects not followed), 4xx, 5xx
        code = e.code
        e.close()
        return code, None
    except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
        reason = getattr(e, "reason", None) or e
        return 0, f"{type(e).__name__}: {reason}"


def probe_each(app_url: str, routes: list[dict], *, methods: list[str] | None = None,
               path_prefix: str | None = None, headers: dict | None = None,
               body: str | None = None, expect_status: list[int], timeout: float = 10) -> dict:
    expect = _check_expect(expect_status)
    want = [m.upper() for m in methods] if methods is not None else None
    headers = dict(headers or {})
    rule = {"kind": "probe_each", "methods": want, "path_prefix": path_prefix,
            "headers": headers, "body": body, "expect_status": expect}
    opener = _no_redirect_opener()
    results, violators, skipped, seen = [], [], [], set()
    for r in routes:
        method = (r.get("method") or "ANY").upper()
        path = r.get("path", "")
        if (method, path) in seen:
            continue
        seen.add((method, path))
        if path_prefix is not None and not path.startswith(path_prefix):
            continue
        if method == "ANY":
            # Kept visible even when `methods` is set: the route may accept them.
            skipped.append({"method": method, "path": path, "why": "method unknown (ANY)"})
            continue
        if want is not None and method not in want:
            continue
        if r.get("params") or ":" in path or "*" in path:
            skipped.append({"method": method, "path": path, "why": "path has parameters"})
            continue
        try:
            url = app_relative_url(app_url, path)
        except ValueError as e:
            skipped.append({"method": method, "path": path, "why": f"not requested: {e}"})
            continue
        status, err = _request(opener, url, method, headers, body, timeout)
        ok = status in expect
        res = {"method": method, "path": path, "status": status, "ok": ok}
        if err:
            res["error"] = err
        results.append(res)
        if not ok:
            v = {"method": method, "path": path, "status": status}
            if err:
                v["error"] = err
            violators.append(v)
    return {"kind": "probe_each", "checked": len(results),
            "passed": sum(1 for x in results if x["ok"]),
            "violators": violators, "skipped": skipped, "results": results, "rule": rule}


def _check_glob(glob: str) -> None:
    if not isinstance(glob, str) or not glob.strip():
        raise ValueError("glob must be a non-empty string")
    pp = PurePath(glob)
    if pp.is_absolute() or glob.startswith(("/", "\\", "~")) or re.match(r"^[A-Za-z]:", glob):
        raise ValueError(f"glob must be repo-relative: {glob!r}")
    if ".." in pp.parts or ".." in re.split(r"[\\/]", glob):
        raise ValueError(f"glob must not contain '..': {glob!r}")


def match_each(repo: Path, glob: str, *, must_contain: str | None = None,
               must_not_contain: str | None = None) -> dict:
    if must_contain is None and must_not_contain is None:
        raise ValueError("give must_contain, must_not_contain, or both")
    _check_glob(glob)
    try:
        want = re.compile(must_contain, re.M) if must_contain is not None else None
        bad = re.compile(must_not_contain, re.M) if must_not_contain is not None else None
    except re.error as e:
        raise ValueError(f"invalid regex: {e}") from e
    repo = Path(repo)
    root = repo.resolve()
    rule = {"kind": "match_each", "glob": glob, "must_contain": must_contain,
            "must_not_contain": must_not_contain}
    results, violators = [], []
    for f in sorted(repo.glob(glob)):
        rel = f.relative_to(repo)
        if any(p in SKIP_PARTS or p.startswith(".") for p in rel.parts):
            continue
        if not f.is_file():
            continue
        try:
            f.resolve().relative_to(root)
        except ValueError:
            continue  # symlink leading outside the repo
        text = f.read_text(encoding="utf-8", errors="replace")
        whys = []
        if want is not None and not want.search(text):
            whys.append("missing must_contain")
        if bad is not None:
            m = bad.search(text)
            if m:
                whys.append(f"has must_not_contain at line {text.count(chr(10), 0, m.start()) + 1}")
        rel_s = rel.as_posix()
        results.append({"path": rel_s, "ok": not whys})
        if whys:
            violators.append({"path": rel_s, "why": "; ".join(whys)})
    return {"kind": "match_each", "checked": len(results),
            "passed": sum(1 for x in results if x["ok"]),
            "violators": violators, "results": results, "rule": rule}


def format_result(result: dict) -> str:
    checked = result.get("checked", 0)
    violators = result.get("violators", [])
    skipped = result.get("skipped")
    if checked == 0:
        head = "checked 0: nothing matched, so this proves nothing"
    else:
        head = f"checked {checked}, passed {result.get('passed', 0)}, violators {len(violators)}"
        if skipped is not None:
            head += f", skipped {len(skipped)}"
    lines = [head]
    for v in violators:
        if "method" in v:
            line = f"VIOLATOR {v['method']} {v['path']} -> status {v['status']}"
            if v.get("error"):
                line += f" ({v['error']})"
        else:
            line = f"VIOLATOR {v['path']}: {v['why']}"
        lines.append(line)
    for s in skipped or []:
        lines.append(f"SKIPPED {s['method']} {s['path']}: {s['why']}")
    return "\n".join(lines)


def replay(rule: dict, *, repo: Path | None, app_url: str, source: dict | None = None) -> dict:
    kind = rule.get("kind") if isinstance(rule, dict) else None
    if kind == "probe_each":
        listing = list_routes(repo, app_url, source)
        return probe_each(app_url, listing["routes"], methods=rule.get("methods"),
                          path_prefix=rule.get("path_prefix"), headers=rule.get("headers"),
                          body=rule.get("body"), expect_status=rule.get("expect_status"))
    if kind == "match_each":
        if repo is None:
            raise ValueError("match_each replay needs a repo")
        return match_each(repo, rule.get("glob"), must_contain=rule.get("must_contain"),
                          must_not_contain=rule.get("must_not_contain"))
    raise ValueError(f"unknown rule kind: {kind!r}")
