"""List every route of a web app from trustworthy sources (stdlib only).

Sources: a Next.js build manifest, Next.js route files on disk, the app's own
OpenAPI document, or a source registered by the project (openapi / file /
command / next). An empty set never looks like a pass: with no source the
result has no routes and a note saying so.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
_METHOD_ALT = "|".join(HTTP_METHODS)
ROUTE_FILE_NAMES = {"route.ts", "route.js", "route.tsx", "route.jsx", "route.mjs"}
SCRIPT_EXTS = {".ts", ".js", ".tsx", ".jsx", ".mjs"}
SKIP_DIRS = {"node_modules", ".next"}
MANIFEST_REL = ".next/server/app-paths-manifest.json"
SOURCE_ORDER = ["next-build", "next-files", "openapi", "file", "command"]

_RE_EXPORT_FN = re.compile(rf"^\s*export\s+(?:async\s+)?function\s*\*?\s*({_METHOD_ALT})\b", re.M)
_RE_EXPORT_CONST = re.compile(rf"^\s*export\s+(?:const|let|var)\s+({_METHOD_ALT})\b", re.M)
# Next.js metadata files (favicon.ico, sitemap.ts, icon.png, ...) appear in the
# manifest as "/<name>/route" but are not route handlers.
_RE_METADATA = re.compile(
    r"^(favicon\.ico|sitemap(\.xml)?|robots(\.txt)?|manifest(\.json|\.webmanifest)?"
    r"|(icon|apple-icon|opengraph-image|twitter-image)([-.\w]*)?)$")
_RE_EXPORT_LIST = re.compile(r"^\s*export\s*\{([^}]*)\}", re.M)


# ---------------------------------------------------------------- helpers

def _segment(seg: str) -> str | None:
    """Map one app-router directory segment to a URL segment (None = dropped)."""
    if seg.startswith("(") and seg.endswith(")"):
        return None  # route group
    if seg.startswith("@"):
        return None  # parallel-route slot
    m = re.fullmatch(r"\[\[\.\.\.(\w+)\]\]|\[\.\.\.(\w+)\]", seg)
    if m:
        return ":" + (m.group(1) or m.group(2)) + "*"
    m = re.fullmatch(r"\[(\w+)\]", seg)
    if m:
        return ":" + m.group(1)
    return seg


def _url_from_segments(segs) -> str:
    out = [s for s in (_segment(x) for x in segs if x) if s is not None]
    return "/" + "/".join(out)


def _normalize_path(path: str) -> str:
    """Normalise an externally supplied path: {id} -> :id, [id] -> :id."""
    path = re.sub(r"\{(\w+)\}", r":\1", path.strip())
    if "[" in path:
        path = _url_from_segments(path.split("/"))
    if not path.startswith("/"):
        path = "/" + path
    return path


def has_params(path: str) -> bool:
    return ":" in path or "*" in path


def _shape(path: str) -> str:
    """Path with parameter names erased, for comparing sources."""
    return re.sub(r":\w+\*?", lambda m: ":*" if m.group(0).endswith("*") else ":", path)


def _methods_in_source(text: str) -> list[str]:
    found = set(_RE_EXPORT_FN.findall(text)) | set(_RE_EXPORT_CONST.findall(text))
    for group in _RE_EXPORT_LIST.findall(text):
        for part in group.split(","):
            part = part.strip()
            if not part:
                continue
            name = re.split(r"\s+as\s+", part)[-1].strip()
            if name in HTTP_METHODS:
                found.add(name)
    return [m for m in HTTP_METHODS if m in found]


def _walk(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            if not name.startswith("."):
                yield Path(dirpath) / name


def _app_dirs(repo: Path) -> list[Path]:
    return [d for d in (repo / "app", repo / "src" / "app") if d.is_dir()]


def _pages_api_dirs(repo: Path) -> list[Path]:
    return [d for d in (repo / "pages" / "api", repo / "src" / "pages" / "api") if d.is_dir()]


def _app_route_files(repo: Path) -> list[Path]:
    return [f for d in _app_dirs(repo) for f in _walk(d) if f.name in ROUTE_FILE_NAMES]


def _pages_api_files(repo: Path) -> list[Path]:
    out = []
    for d in _pages_api_dirs(repo):
        for f in _walk(d):
            if f.suffix in SCRIPT_EXTS and not f.name.endswith(".d.ts"):
                out.append(f)
    return out


def _no_redirect_opener():
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def app_relative_url(app_url: str, path: str) -> str:
    """Join app_url and an app-relative path; ValueError if it would leave the app."""
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//") \
            or "\\" in path or "://" in path:
        raise ValueError(f"path must be app-relative (start with a single '/'): {path!r}")
    base = urlsplit(app_url)
    if base.scheme not in ("http", "https") or not base.netloc:
        raise ValueError(f"app_url must be an http(s) URL: {app_url!r}")
    url = app_url.rstrip("/") + path
    if urlsplit(url).netloc != base.netloc:
        raise ValueError(f"path would leave the app's host: {path!r}")
    return url


# ---------------------------------------------------------------- readers

def read_next_build(repo: Path) -> tuple[list[tuple[None, str]], str]:
    """Route-handler paths from the Next.js build manifest (no methods)."""
    data = json.loads((repo / MANIFEST_REL).read_text(encoding="utf-8"))
    out = []
    for key in data:
        if key == "/route" or key.endswith("/route"):
            segs = [x for x in key[: -len("route")].split("/") if x]
            if "__metadata_id__" in key or (segs and _RE_METADATA.match(segs[-1])):
                continue
            out.append((None, _url_from_segments(segs)))
    return sorted(set(out), key=lambda r: r[1]), MANIFEST_REL


def read_next_files(repo: Path) -> tuple[list[tuple[str, str]], str]:
    """(method, path) from app-router route files and pages/api files."""
    out = []
    for f in _app_route_files(repo):
        app_root = next(d for d in _app_dirs(repo) if d in f.parents)
        path = _url_from_segments(f.parent.relative_to(app_root).parts)
        methods = _methods_in_source(f.read_text(encoding="utf-8", errors="replace")) or ["ANY"]
        out.extend((m, path) for m in methods)
    for f in _pages_api_files(repo):
        api_root = next(d for d in _pages_api_dirs(repo) if d in f.parents)
        parts = list(f.relative_to(api_root).with_suffix("").parts)
        if parts and parts[-1] == "index":
            parts.pop()
        out.append(("ANY", _url_from_segments(["api", *parts])))
    roots = [str(d.relative_to(repo)) + "/**/route.*" for d in _app_dirs(repo)]
    roots += [str(d.relative_to(repo)) + "/**" for d in _pages_api_dirs(repo)]
    return sorted(set(out), key=lambda r: (r[1], r[0])), ", ".join(roots)


def read_openapi(app_url: str, path: str = "/openapi.json", timeout: float = 10) -> tuple[list[tuple[str, str]], str]:
    """(method, path) from the app's own OpenAPI document. ValueError on bad path."""
    url = app_relative_url(app_url, path)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with _no_redirect_opener().open(req, timeout=timeout) as resp:
            doc = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        code = e.code
        e.close()
        raise LookupError(f"HTTP {code}") from None
    if not isinstance(doc, dict) or not isinstance(doc.get("paths"), dict) \
            or not ("openapi" in doc or "swagger" in doc):
        raise LookupError("not an OpenAPI document")
    out = []
    for p, item in doc["paths"].items():
        if not isinstance(item, dict):
            continue
        for k in item:
            if isinstance(k, str) and k.upper() in HTTP_METHODS:
                out.append((k.upper(), _normalize_path(p)))
    return sorted(set(out), key=lambda r: (r[1], r[0])), url


def _inside(repo: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(repo.resolve())
        return True
    except ValueError:
        return False


def _parse_method(m) -> str:
    if not isinstance(m, str) or m.strip().upper() not in (*HTTP_METHODS, "ANY"):
        raise ValueError(f"unknown HTTP method: {m!r}")
    return m.strip().upper()


def read_file_source(repo: Path, rel: str) -> tuple[list[tuple[str, str]], str]:
    target = repo / rel
    data = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise LookupError(f"{rel} does not hold a list")
    out = []
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise LookupError(f"{rel}: bad entry {item!r}")
        out.append((_parse_method(item.get("method", "ANY")), _normalize_path(item["path"])))
    return sorted(set(out), key=lambda r: (r[1], r[0])), rel


def read_command_source(repo: Path, cmd: str) -> tuple[list[tuple[str, str]], str]:
    proc = subprocess.run(cmd, shell=True, cwd=repo, timeout=30, capture_output=True, text=True)
    if proc.returncode != 0:
        raise LookupError(f"command exited {proc.returncode}: {proc.stderr.strip()[:300]}")
    out = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2:
            raise LookupError(f"command output line is not 'METHOD /path': {line!r}")
        out.append((_parse_method(parts[0]), _normalize_path(parts[1])))
    return sorted(set(out), key=lambda r: (r[1], r[0])), cmd


# ---------------------------------------------------------------- main

def _looks_like_next(repo: Path) -> bool:
    return (repo / MANIFEST_REL).is_file() or bool(_app_route_files(repo)) or bool(_pages_api_files(repo))


def list_routes(repo: Path | None, app_url: str, source: dict | None = None) -> dict:
    repo = Path(repo) if repo is not None else None
    notes: list[str] = []
    # name -> (entries, detail)
    got: dict[str, tuple[list, str]] = {}
    pages_paths: set[str] = set()

    def run_next(explicit: bool):
        if (repo / MANIFEST_REL).is_file():
            try:
                got["next-build"] = read_next_build(repo)
            except (OSError, ValueError) as e:
                notes.append(f"next-build manifest unreadable: {e}")
        elif explicit:
            notes.append(f"no {MANIFEST_REL} (app not built?)")
        if _app_dirs(repo) or _pages_api_dirs(repo):
            entries, detail = read_next_files(repo)
            got["next-files"] = (entries, detail)
            for f in _pages_api_files(repo):
                api_root = next(d for d in _pages_api_dirs(repo) if d in f.parents)
                parts = list(f.relative_to(api_root).with_suffix("").parts)
                if parts and parts[-1] == "index":
                    parts.pop()
                pages_paths.add(_url_from_segments(["api", *parts]))
        elif explicit:
            notes.append("no app/, src/app/ or pages/api directory")

    def run_openapi(path: str, quiet: bool):
        try:
            got["openapi"] = read_openapi(app_url, path)
        except (OSError, LookupError, json.JSONDecodeError, urllib.error.URLError,
                http.client.HTTPException) as e:
            if not quiet:
                notes.append(f"openapi source {path} unavailable: {e}")
        except ValueError:
            if quiet:
                return
            raise

    if source is None:
        if repo is not None and _looks_like_next(repo):
            run_next(explicit=False)
        run_openapi("/openapi.json", quiet=True)
    else:
        if not isinstance(source, dict):
            raise ValueError("source must be a dict")
        kind = source.get("type")
        if kind == "openapi":
            path = source.get("path", "/openapi.json")
            app_relative_url(app_url, path)  # validate before any request
            run_openapi(path, quiet=False)
        elif kind == "file":
            rel = source.get("path")
            if repo is None or not isinstance(rel, str) or not rel:
                raise ValueError("file source needs a repo and a 'path'")
            if Path(rel).is_absolute() or not _inside(repo, repo / rel):
                raise ValueError(f"file source path resolves outside the repo: {rel!r}")
            try:
                got["file"] = read_file_source(repo, rel)
            except (OSError, LookupError, json.JSONDecodeError, ValueError) as e:
                notes.append(f"file source {rel} unavailable: {e}")
        elif kind == "command":
            cmd = source.get("cmd")
            if repo is None or not isinstance(cmd, str) or not cmd.strip():
                raise ValueError("command source needs a repo and a 'cmd'")
            try:
                got["command"] = read_command_source(repo, cmd)
            except (OSError, LookupError, ValueError, subprocess.TimeoutExpired) as e:
                notes.append(f"command source failed: {e}")
        elif kind == "next":
            if repo is None:
                raise ValueError("next source needs a repo")
            run_next(explicit=True)
        else:
            raise ValueError(f"unknown route source type: {kind!r}")

    # ---- merge
    routes: dict[tuple[str, str], set[str]] = {}
    for name in ("next-files", "openapi", "file", "command"):
        for method, path in got.get(name, ([], ""))[0]:
            routes.setdefault((method, path), set()).add(name)

    disagreements: list[str] = []
    build_paths = {p for _, p in got["next-build"][0]} if "next-build" in got else None
    file_app_paths = {p for _, p in got.get("next-files", ([], ""))[0]} - pages_paths
    if build_paths is not None:
        for p in sorted(build_paths):
            if p in file_app_paths:
                for key in routes:
                    if key[1] == p and "next-files" in routes[key]:
                        routes[key].add("next-build")
            else:
                routes.setdefault(("ANY", p), set()).add("next-build")
                disagreements.append(f"{p}: in next-build, missing from next-files")
        for p in sorted(file_app_paths - build_paths):
            disagreements.append(f"{p}: in next-files, missing from next-build")

    # other pairs of sources: compare path sets (parameter names ignored)
    names = [n for n in SOURCE_ORDER if n in got]
    path_sets = {n: {p for _, p in got[n][0]} for n in names}
    for a in names:
        for b in names:
            if a == b or {a, b} == {"next-build", "next-files"}:
                continue
            b_shapes = {_shape(p) for p in path_sets[b]}
            for p in sorted(path_sets[a]):
                if _shape(p) not in b_shapes:
                    disagreements.append(f"{p}: in {a}, missing from {b}")

    out_routes = [
        {"method": m, "path": p, "params": has_params(p),
         "sources": [n for n in SOURCE_ORDER if n in srcs]}
        for (m, p), srcs in routes.items()
    ]
    out_routes.sort(key=lambda r: (r["path"], r["method"]))
    sources = [{"name": n, "detail": got[n][1], "paths": len(path_sets[n])} for n in names]
    if not sources:
        notes.insert(0, "no route source found")
    elif not out_routes:
        notes.append("route sources were found but listed no routes")
    return {
        "routes": out_routes,
        "sources": sources,
        "agree": not disagreements,
        "disagreements": disagreements,
        "note": "; ".join(notes),
    }


def format_routes(result: dict) -> str:
    routes = result.get("routes", [])
    sources = result.get("sources", [])
    n = len(routes)
    word = "route" if n == 1 else "routes"
    if not sources:
        head = f"0 routes: {result.get('note') or 'no route source found'}"
    elif not routes:
        names = ", ".join(s["name"] for s in sources)
        head = f"0 routes: {result.get('note') or 'sources listed no routes'} (sources: {names})"
    else:
        names = ", ".join(s["name"] for s in sources)
        if result.get("disagreements"):
            status = "(SOURCES DISAGREE: " + "; ".join(result["disagreements"]) + ")"
        elif len(sources) == 1:
            status = "(sources agree) - only one source, so nothing was cross-checked"
        else:
            status = "(sources agree)"
        head = f"{n} {word} from {names} {status}"
        if result.get("note"):
            head += f"\nnote: {result['note']}"
    lines = [head]
    for r in routes:
        lines.append(f"{r['method']} {r['path']}" + (" [has parameters]" if r.get("params") else ""))
    return "\n".join(lines)
