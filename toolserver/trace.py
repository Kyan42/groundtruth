"""Plain-code tracing over a repo (stdlib only): searches and chain-following.

Groundtruth traces a property from the lines that touch some data up to the
pages a person can open. This module does the mechanical half: complete,
repeatable text searches and "who uses this" chain-following. It never judges
relevance. Everything is text patterns, not a parser; every approximation is
meant to err toward reporting MORE, and where a result is approximate the
output says so (extra keys: ``enclosing()["approx"]``, ``climb()["dead_ends"]``).

Known blind spots (places this can still report FEWER than the truth):
- search is line-based: a regex meant to span lines never matches.
- referencers: imports through bundler/tsconfig aliases other than ``@/`` (and
  bare specifiers that are not repo-root/``src``-relative), specifiers built at
  run time (``import(`./${x}`)``, ``require(name)``, ``importlib.import_module``,
  ``__import__``), files loaded by framework convention or config rather than an
  import (Next.js route handlers, middleware, server actions referenced by
  ``action={fn}`` through props, plugin registries), HTML pages that load a
  script through JS (``document.createElement("script")``) or a bundler entry.
- climb: a page that reaches a file only through a request address (fetch to an
  API route) is not a "use"; callers must join that hop with search().
  Inheritance of Next.js layouts is by folder prefix only.
- enclosing (JS/TS): braces inside regex literals, object-literal methods
  (``{ foo() {} }``, ``foo: () => {}``) and functions passed as arguments
  (``useCallback(() => {})``) are not definitions; the answer is then the
  next definition outward. Python: decorators and dedented multi-line strings
  can confuse indentation.
- looks_like_write: writes through an alias (``const c = state.cart; c.push()``),
  through a callee that mutates its argument by a different name, and via
  setters/proxies are only seen when the name itself is on the line.
- computed_addresses: wrapped clients (axios instances, ``session.get``,
  ``ky``, helper functions like ``api(path)``), and addresses built in one place
  and passed to a literal-looking call elsewhere.
- address_literals: absolute URLs (``http://host/api``) and addresses without a
  leading slash (``"api/cart"``) are not reported.
"""
from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath

SKIP_DIRS = {"node_modules", "__pycache__", ".next", "dist", "build"}
MAX_BYTES = 1_000_000
JS_EXTS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".vue", ".svelte", ".astro"}
PY_EXTS = {".py", ".pyw"}
HTML_EXTS = {".html", ".htm"}
RESOLVE_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".vue", ".svelte",
                ".astro", ".json", ".css", ".scss", ".sass", ".less")
PAGE_EXTS = {".tsx", ".jsx", ".ts", ".js", ".mdx"}
LAYOUT_STEMS = {"layout", "template", "loading", "error", "not-found", "default"}
STATIC_ROOTS = {"static", "public", "templates", "www"}
MAX_CHAINS = 50
MAX_EXPANSIONS = 5000
_IDENT = r"[A-Za-z_$][\w$]*"


# ---------------------------------------------------------------- file access

class _Snapshot:
    """Every visible text file of a repo, read once."""

    def __init__(self, repo: Path):
        self.root = Path(repo).resolve()
        self.files: dict[str, str] = {}
        self._lines: dict[str, list[str]] = {}
        for dirpath, dirnames, filenames in os.walk(self.root, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if not _hidden_name(d)
                                 and not os.path.islink(os.path.join(dirpath, d)))
            for fn in sorted(filenames):
                if _hidden_name(fn):
                    continue
                full = Path(dirpath) / fn
                text = _read_text(self.root, full)
                if text is not None:
                    self.files[full.relative_to(self.root).as_posix()] = text

    def lines(self, rel: str) -> list[str]:
        if rel not in self._lines:
            self._lines[rel] = self.files[rel].splitlines()
        return self._lines[rel]

    def check(self, path: str) -> str:
        rel = _rel_path(self.root, path)
        if rel not in self.files:
            raise ValueError(f"not a visible text file in the repo: {path}")
        return rel


def _hidden_name(name: str) -> bool:
    return name.startswith(".") or name in SKIP_DIRS


def _read_text(root: Path, full: Path) -> str | None:
    try:
        real = full.resolve()
        real.relative_to(root)  # a symlink must not lead out of the repo
        if not real.is_file() or real.stat().st_size > MAX_BYTES:
            return None
        data = real.read_bytes()
    except (OSError, ValueError):
        return None
    if b"\x00" in data[:8192]:
        return None
    return data.decode("utf-8", errors="replace")


def _rel_path(root: Path, path: str) -> str:
    p = Path(path)
    full = p if p.is_absolute() else root / p
    try:
        rel = full.resolve().relative_to(root)
    except ValueError:
        raise ValueError(f"path is outside the repo: {path}") from None
    if not rel.parts:
        raise ValueError(f"not a file: {path}")
    if any(_hidden_name(part) for part in rel.parts):
        raise ValueError(f"path is hidden or skipped: {path}")
    return rel.as_posix()


def _ext(rel: str) -> str:
    return PurePosixPath(rel).suffix.lower()


# ---------------------------------------------------------------- search / context

def search(repo: Path, pattern: str, *, max_hits: int = 500) -> dict:
    """Every line in the repo's visible files matching the regex."""
    try:
        rx = re.compile(pattern)
    except re.error as e:
        raise ValueError(f"invalid regex {pattern!r}: {e}") from None
    snap = _Snapshot(repo)
    hits, total = [], 0
    for rel in sorted(snap.files):
        for i, line in enumerate(snap.lines(rel), 1):
            if rx.search(line):
                total += 1
                if len(hits) < max_hits:
                    hits.append({"path": rel, "line": i, "text": line.strip()[:200]})
    return {"hits": hits, "total": total, "truncated": total > len(hits)}


def context(repo: Path, path: str, line: int, radius: int = 7) -> dict:
    """The lines around a hit, at most 15 lines in all."""
    root = Path(repo).resolve()
    rel = _rel_path(root, path)
    text = _read_text(root, root / rel)
    if text is None:
        raise ValueError(f"not a readable text file: {path}")
    lines = text.splitlines()
    radius = max(0, min(int(radius), 7))
    line = max(1, min(int(line), max(len(lines), 1)))
    start = max(1, line - radius)
    end = min(len(lines), line + radius)
    return {"path": rel, "start": start, "end": end, "lines": lines[start - 1:end]}


# ---------------------------------------------------------------- JS sanitizer

def _sanitize_js(text: str) -> list[str]:
    """Lines with strings, template literals and comments blanked to spaces.

    Quoted strings end at end of line (so an apostrophe in JSX text cannot eat
    the file). Template literals may span lines; their ``${}`` parts are parsed
    for nesting but blanked too. Regex literals are not recognised.
    """
    out_lines = []
    mode = ["code"]          # stack: code | tmpl | expr | block
    expr_depth = []          # brace depth inside each ${ } expression
    for raw in text.splitlines():
        out = []
        i, n = 0, len(raw)
        quote = None
        while i < n:
            c = raw[i]
            nxt = raw[i + 1] if i + 1 < n else ""
            top = mode[-1]
            blank = top != "code"
            if quote:
                out.append(" ")
                if c == "\\":
                    out.append(" " if nxt else "")
                    i += 2
                    continue
                if c == quote:
                    quote = None
                i += 1
                continue
            if top == "block":
                if c == "*" and nxt == "/":
                    mode.pop()
                    out.append("  ")
                    i += 2
                    continue
                out.append(" ")
                i += 1
                continue
            if top == "tmpl":
                if c == "\\":
                    out.append("  " if nxt else " ")
                    i += 2
                    continue
                if c == "`":
                    mode.pop()
                    out.append(" ")
                    i += 1
                    continue
                if c == "$" and nxt == "{":
                    mode.append("expr")
                    expr_depth.append(0)
                    out.append("  ")
                    i += 2
                    continue
                out.append(" ")
                i += 1
                continue
            # code or expr
            if c == "/" and nxt == "/":
                out.append(" " * (n - i))
                break
            if c == "/" and nxt == "*":
                mode.append("block")
                out.append("  ")
                i += 2
                continue
            if c in "'\"":
                quote = c
                out.append(" ")
                i += 1
                continue
            if c == "`":
                mode.append("tmpl")
                out.append(" ")
                i += 1
                continue
            if top == "expr":
                if c == "{":
                    expr_depth[-1] += 1
                elif c == "}":
                    if expr_depth[-1] == 0:
                        expr_depth.pop()
                        mode.pop()
                        out.append(" ")
                        i += 1
                        continue
                    expr_depth[-1] -= 1
            out.append(" " if blank else c)
            i += 1
        out_lines.append("".join(out))
    return out_lines


# ---------------------------------------------------------------- enclosing

_RX_FUNC = re.compile(rf"\b(?:export\s+)?(?:default\s+)?(?:async\s+)?function\b\s*\*?\s*({_IDENT})?\s*(?:<[^>(]*>)?\s*\(")
_RX_ARROW = re.compile(rf"\b(?:const|let|var)\s+({_IDENT})\s*(?::[^=]+?)?=\s*(?:async\s+)?"
                       rf"(function\b|\(|{_IDENT}\s*=>|<)")
_RX_DEFAULT_ARROW = re.compile(r"\bexport\s+default\s+(?:async\s+)?(?:\(|[A-Za-z_$][\w$]*\s*=>)")
_RX_CLASS = re.compile(rf"\bclass\s+({_IDENT})")
_RX_METHOD = re.compile(rf"^\s*(?:(?:public|private|protected|static|async|get|set|override|readonly|abstract)\s+)*"
                        rf"\*?\s*(#?{_IDENT})\s*(?:<[^>(]*>)?\s*\(")
_RX_FIELD_ARROW = re.compile(rf"^\s*(?:(?:public|private|protected|static|readonly|override)\s+)*"
                             rf"(#?{_IDENT})\s*(?::[^=]+?)?=\s*(?:async\s+)?(?:\(|{_IDENT}\s*=>|function\b)")
_NOT_METHODS = {"if", "for", "while", "switch", "catch", "return", "function", "with", "else", "do",
                "try", "new", "typeof", "await", "super", "throw", "yield", "delete", "void", "case"}


def _js_definitions(text: str):
    """[(name, kind, start, end, top_level)] plus whether brace tracking broke."""
    lines = _sanitize_js(text)
    defs = []        # dicts: name kind start end top
    stack = []       # one entry per open "{": {"def": idx|None, "class": bool}
    paren = 0
    pending = None   # def waiting for its body brace
    broken = False

    def start_def(name, kind, line_no, need_arrow):
        nonlocal pending
        close_pending_expr(line_no, force=True)
        idx = len(defs)
        defs.append({"name": name, "kind": kind, "start": line_no, "end": None, "top": len(stack) == 0})
        pending = {"idx": idx, "stack": len(stack), "paren": paren, "need_arrow": need_arrow,
                   "seen_arrow": False, "expr": False}

    def close_pending_expr(line_no, force=False):
        nonlocal pending
        if pending is not None and (pending["expr"] or force):
            if defs[pending["idx"]]["end"] is None:
                defs[pending["idx"]]["end"] = line_no
            pending = None

    prev_sig = ""   # last significant characters seen (carried across lines)
    for ln, line in enumerate(lines, 1):
        events = []
        for m in _RX_FUNC.finditer(line):
            name = m.group(1)
            if name is None and re.search(r"\bexport\s+default\s+(?:async\s+)?function\s*\*?\s*\($",
                                          line[:m.end()]):
                name = "default"
            events.append((m.start(), name, "function", False))  # name None: anonymous, ignored
        for m in _RX_ARROW.finditer(line):
            is_fn = m.group(2).startswith("function")
            events.append((m.start(), m.group(1), "function", not is_fn))
        for m in _RX_DEFAULT_ARROW.finditer(line):
            events.append((m.start(), "default", "function", True))
        for m in _RX_CLASS.finditer(line):
            events.append((m.start(), m.group(1), "class", False))
        in_class = bool(stack) and stack[-1]["class"]
        if in_class:
            m = _RX_METHOD.match(line)
            if m and m.group(1) not in _NOT_METHODS:
                events.append((m.start(1), m.group(1), "function", False))
            else:
                m = _RX_FIELD_ARROW.match(line)
                if m:
                    events.append((m.start(1), m.group(1), "function", True))
        events.sort(key=lambda e: e[0])
        ev_i = 0
        for pos, c in enumerate(line):
            while ev_i < len(events) and events[ev_i][0] <= pos:
                _, name, kind, need_arrow = events[ev_i]
                ev_i += 1
                if name is None:
                    # anonymous function expression: not a named definition
                    continue
                start_def(name, kind, ln, need_arrow)
            if c.isspace():
                continue
            if pending is not None and pending["seen_arrow"] and not pending["expr"] and c != "{" \
                    and len(stack) == pending["stack"] and paren == pending["paren"]:
                # expression-bodied arrow: ends where depth returns to here at end of a line
                pending["expr"] = True
            if c == "(":
                paren += 1
            elif c == ")":
                paren = max(0, paren - 1)
            elif c == "{":
                entry = {"def": None, "class": False}
                if (pending is not None and not pending["expr"] and len(stack) == pending["stack"]
                        and paren == pending["paren"]
                        and (pending["seen_arrow"]
                             or (not pending["need_arrow"] and not re.search(r"[:|&,(=?\[{]$", prev_sig)))):
                    entry["def"] = pending["idx"]
                    entry["class"] = defs[pending["idx"]]["kind"] == "class"
                    pending = None
                stack.append(entry)
            elif c == "}":
                if not stack:
                    broken = True
                else:
                    entry = stack.pop()
                    if entry["def"] is not None:
                        defs[entry["def"]]["end"] = ln
                    if pending is not None and len(stack) < pending["stack"]:
                        close_pending_expr(ln, force=True)
            elif c == ";":
                if pending is not None and len(stack) == pending["stack"] and paren == pending["paren"]:
                    close_pending_expr(ln, force=True)
            elif c == "=" and pos + 1 < len(line) and line[pos + 1] == ">":
                if pending is not None and len(stack) == pending["stack"] and paren == pending["paren"]:
                    pending["seen_arrow"] = True
            prev_sig = (prev_sig + c)[-3:]
            if c == ">" and pos > 0 and line[pos - 1] == "=":
                prev_sig = "=>"
        if pending is not None and pending["expr"] and len(stack) <= pending["stack"] \
                and paren <= pending["paren"] and not re.search(r"(?:[-+*/%&|^=?:,.(\[{<>!]|=>)\s*$", line):
            close_pending_expr(ln, force=True)
    if stack or paren:
        broken = True
    for d in defs:
        if d["end"] is None:
            d["end"] = len(lines)  # never closed: assume it runs to the end (err wide)
    return defs, broken


def _py_enclosing(lines: list[str], line: int) -> dict:
    def indent(s):
        return len(s) - len(s.lstrip(" \t"))
    rx_def = re.compile(r"^\s*(?:async\s+)?(def|class)\s+([A-Za-z_]\w*)")
    target = lines[line - 1] if 0 < line <= len(lines) else ""
    if target.strip():
        m = rx_def.match(target)
        if m:
            return {"name": m.group(2), "kind": "function" if m.group(1) == "def" else "class", "line": line}
        level = indent(target)
    else:
        level = None
        for nxt in lines[line:]:
            if nxt.strip():
                level = indent(nxt)
                break
        if level is None:
            level = 0
    for i in range(line - 1, 0, -1):
        s = lines[i - 1]
        if not s.strip() or s.lstrip().startswith("#"):
            continue
        ind = indent(s)
        if ind < level:
            m = rx_def.match(s)
            if m:
                return {"name": m.group(2), "kind": "function" if m.group(1) == "def" else "class", "line": i}
            level = ind
            if level == 0:
                break
    return {"name": None, "kind": "module", "line": None}


def enclosing(repo: Path, path: str, line: int) -> dict:
    """The named definition a line sits inside.

    Extra key ``approx``: True when JS brace tracking broke and the answer is the
    outermost top-level definition starting before the line.
    """
    snap = _Snapshot(repo)
    rel = snap.check(path)
    ext = _ext(rel)
    if ext in HTML_EXTS:
        return {"name": None, "kind": "page", "line": None, "approx": False}
    if ext in PY_EXTS:
        out = _py_enclosing(snap.lines(rel), int(line))
        out["approx"] = False
        return out
    if ext in JS_EXTS:
        defs, broken = _js_definitions(snap.files[rel])
        if broken:
            tops = [d for d in defs if d["top"] and d["start"] <= line]
            if tops:
                d = tops[-1]
                return {"name": d["name"], "kind": d["kind"], "line": d["start"], "approx": True}
            return {"name": None, "kind": "module", "line": None, "approx": True}
        inside = [d for d in defs if d["start"] <= line <= d["end"]]
        if inside:
            d = max(inside, key=lambda d: (d["start"], -d["end"]))
            return {"name": d["name"], "kind": d["kind"], "line": d["start"], "approx": False}
        return {"name": None, "kind": "module", "line": None, "approx": False}
    return {"name": None, "kind": "module", "line": None, "approx": False}


# ---------------------------------------------------------------- exported names

_RX_EXP_DECL = re.compile(rf"^\s*export\s+(?:declare\s+)?(?:async\s+)?(?:abstract\s+)?"
                          rf"(?:function\s*\*?|const|let|var|class|interface|type|enum)\s+({_IDENT})", re.M)
_RX_EXP_DEFAULT = re.compile(rf"^\s*export\s+default\s+(?:async\s+)?(?:abstract\s+)?(?:(?:function\s*\*?|class)\s+({_IDENT}))?", re.M)
_RX_EXP_LIST = re.compile(r"\bexport\s+(?:type\s+)?\{([^}]*)\}", re.S)
_RX_EXP_DESTRUCT = re.compile(r"^\s*export\s+(?:const|let|var)\s+\{([^}]*)\}\s*=", re.M)
_RX_CJS = re.compile(rf"\b(?:module\.)?exports\.({_IDENT})\s*=")
_RX_CJS_OBJ = re.compile(r"\bmodule\.exports\s*=\s*\{([^}]*)\}", re.S)
_RX_CJS_DEFAULT = re.compile(r"\bmodule\.exports\s*=")


def _exported_names_text(rel: str, text: str) -> list[str]:
    ext = _ext(rel)
    names: list[str] = []
    if ext in PY_EXTS:
        for m in re.finditer(r"^(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)", text, re.M):
            names.append(m.group(1))
    elif ext in JS_EXTS:
        clean = "\n".join(_sanitize_js(text))
        names += _RX_EXP_DECL.findall(clean)
        for m in _RX_EXP_DEFAULT.finditer(clean):
            names.append("default")
            if m.group(1):
                names.append(m.group(1))
        for m in _RX_EXP_LIST.finditer(clean):
            for part in m.group(1).split(","):
                part = re.sub(r"^\s*type\s+", "", part).strip()
                if not part:
                    continue
                bits = re.split(r"\s+as\s+", part)
                names.append(bits[-1].strip())
        for m in _RX_EXP_DESTRUCT.finditer(clean):
            for part in m.group(1).split(","):
                part = part.split("=")[0].strip()
                if part:
                    names.append(part.split(":")[-1].strip())
        names += _RX_CJS.findall(clean)
        for m in _RX_CJS_OBJ.finditer(clean):
            for part in m.group(1).split(","):
                key = part.split(":")[0].strip()
                if re.fullmatch(_IDENT, key):
                    names.append(key)
        if _RX_CJS_DEFAULT.search(clean):
            names.append("default")
    seen, out = set(), []
    for n in names:
        if n and n not in seen and re.fullmatch(r"[A-Za-z_$][\w$]*", n):
            seen.add(n)
            out.append(n)
    return out


def exported_names(repo: Path, path: str) -> list[str]:
    """Names other files can use from this file."""
    snap = _Snapshot(repo)
    rel = snap.check(path)
    return _exported_names_text(rel, snap.files[rel])


# ---------------------------------------------------------------- pages / layouts

def _app_dirs(snap: _Snapshot) -> list[str]:
    return [d for d in ("app", "src/app") if any(p.startswith(d + "/") for p in snap.files)]


def _segment_url(parts: list[str]) -> str:
    out = []
    for s in parts:
        if s.startswith("(") and s.endswith(")"):
            continue
        if s.startswith("@"):
            continue
        m = re.fullmatch(r"\[\[?\.\.\.(.+?)\]\]?", s)
        if m:
            out.append(":" + m.group(1) + "*")
            continue
        m = re.fullmatch(r"\[(.+)\]", s)
        if m:
            out.append(":" + m.group(1))
            continue
        out.append(s)
    return "/" + "/".join(out)


def _pages(snap: _Snapshot) -> list[dict]:
    found: dict[str, str] = {}
    for d in _app_dirs(snap):
        for rel in snap.files:
            if not rel.startswith(d + "/"):
                continue
            p = PurePosixPath(rel)
            if p.stem == "page" and p.suffix in PAGE_EXTS:
                found[rel] = _segment_url(list(p.relative_to(d).parts[:-1]))
    for d in ("pages", "src/pages"):
        for rel in snap.files:
            if not rel.startswith(d + "/"):
                continue
            p = PurePosixPath(rel)
            sub = p.relative_to(d)
            if p.suffix not in PAGE_EXTS or sub.parts[0] == "api" or p.name.startswith("_"):
                continue
            parts = list(sub.parts[:-1]) + ([] if p.stem == "index" else [p.stem])
            found[rel] = _segment_url(parts)
    for rel in snap.files:
        p = PurePosixPath(rel)
        if p.suffix.lower() not in HTML_EXTS:
            continue
        parts = list(p.parts)
        base = 0
        for i in range(len(parts) - 1):
            if parts[i] in STATIC_ROOTS:
                base = i + 1
        sub = parts[base:]
        if p.stem == "index":
            url = "/" + "/".join(sub[:-1])
        else:
            url = "/" + "/".join(sub)
        found[rel] = url
    return sorted(({"file": f, "url": u} for f, u in found.items()), key=lambda x: (x["url"], x["file"]))


def pages(repo: Path) -> list[dict]:
    """Every page a person can open."""
    return _pages(_Snapshot(repo))


def _layout_scope(snap: _Snapshot, rel: str) -> str | None:
    """Folder whose pages a layout-like file is shown on, or None."""
    p = PurePosixPath(rel)
    for d in _app_dirs(snap):
        if rel.startswith(d + "/") and p.stem in LAYOUT_STEMS and p.suffix in PAGE_EXTS:
            return str(p.parent)
    for d in ("pages", "src/pages"):
        if str(p.parent) == d and p.stem in ("_app", "_document") and p.suffix in PAGE_EXTS:
            return d
    return None


# ---------------------------------------------------------------- referencers

_RX_IMPORT_FROM = re.compile(r"\bimport\s+(?:type\s+)?([\w$*{}\s,]+?)\s*\bfrom\s*([\"'])([^\"'\n]+)\2")
_RX_IMPORT_BARE = re.compile(r"\bimport\s*([\"'])([^\"'\n]+)\1")
_RX_EXPORT_FROM = re.compile(r"\bexport\s+(?:type\s+)?([\w$*{}\s,]+?)\s*\bfrom\s*([\"'])([^\"'\n]+)\2")
_RX_DYN_IMPORT = re.compile(r"\bimport\s*\(\s*([\"'`])([^\"'`\n]+)\1\s*\)")
_RX_REQUIRE = re.compile(r"(?:\b(?:const|let|var)\s+([\w$]+|\{[^}]*\})\s*=\s*)?\brequire\s*\(\s*([\"'`])([^\"'`\n]+)\2\s*\)")
_RX_SCRIPT_SRC = re.compile(r"<script\b[^>]*?\bsrc\s*=\s*[\"']?([^\"'\s>]+)", re.I)


def _norm(parts) -> str | None:
    out = []
    for part in parts:
        if part in ("", "."):
            continue
        if part == "..":
            if not out:
                return None
            out.pop()
        else:
            out.append(part)
    return "/".join(out)


def _strip_ext(p: str) -> str:
    s = PurePosixPath(p)
    return str(s.with_suffix("")) if s.suffix else p


def _js_spec_matches(importer: str, spec: str, target: str) -> bool:
    spec = spec.split("?")[0].split("#")[0]
    bases = []
    if spec.startswith("./") or spec.startswith("../") or spec in (".", ".."):
        bases.append(_norm(PurePosixPath(importer).parent.parts + tuple(spec.split("/"))))
    elif spec.startswith("@/") or spec.startswith("~/"):
        rest = spec[2:].split("/")
        bases += [_norm(rest), _norm(["src"] + rest)]
    elif spec.startswith("/"):
        rest = spec[1:].split("/")
        bases += [_norm(rest), _norm(["src"] + rest)]
    else:
        # bare specifier: package, or baseUrl-relative ("components/x"); err wide
        rest = spec.split("/")
        bases += [_norm(rest), _norm(["src"] + rest)]
    t_noext = _strip_ext(target)
    for b in bases:
        if not b:
            continue
        if target == b or t_noext == b:
            return True
        if PurePosixPath(target).stem == "index" and str(PurePosixPath(target).parent) == b:
            return True
        if _ext(b) in RESOLVE_EXTS and t_noext == _strip_ext(b):
            return True  # "./x.js" written for x.ts
    return False


def _parse_clause(clause: str) -> set[str]:
    names = set()
    clause = clause.strip()
    m = re.search(r"\{([^}]*)\}", clause)
    if m:
        for part in m.group(1).split(","):
            part = re.sub(r"^\s*type\s+", "", part).strip()
            if part:
                names.add(re.split(r"\s+as\s+|\s*:\s*", part)[-1].strip())
        clause = clause[:m.start()] + clause[m.end():]
    m = re.search(r"\*\s*as\s+([\w$]+)", clause)
    if m:
        names.add(m.group(1))
        clause = clause[:m.start()] + clause[m.end():]
    for part in clause.split(","):
        part = part.strip()
        if re.fullmatch(r"[A-Za-z_$][\w$]*", part) and part != "type":
            names.add(part)
    return {n for n in names if re.fullmatch(r"[A-Za-z_$][\w$]*", n)}


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _js_imports_of(importer: str, text: str, target: str):
    """[(line, (start_line, end_line), local_names)] for statements importing target."""
    found = []
    for rx, spec_g, clause_g in ((_RX_IMPORT_FROM, 3, 1), (_RX_EXPORT_FROM, 3, None),
                                 (_RX_IMPORT_BARE, 2, None), (_RX_DYN_IMPORT, 2, None), (_RX_REQUIRE, 3, 1)):
        for m in rx.finditer(text):
            if not _js_spec_matches(importer, m.group(spec_g), target):
                continue
            locals_ = _parse_clause(m.group(clause_g)) if clause_g and m.group(clause_g) else set()
            found.append((_line_of(text, m.start(spec_g)), (_line_of(text, m.start()), _line_of(text, m.end())), locals_))
    return found


def _py_module_name(rel: str) -> str:
    p = PurePosixPath(rel)
    parts = list(p.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _py_imports_of(importer: str, text: str, target: str):
    full = _py_module_name(target)
    if not full:
        return []
    tparts = full.split(".")
    suffixes = {".".join(tparts[i:]) for i in range(len(tparts))}
    imp_pkg = _py_module_name(importer).split(".")
    if not importer.endswith("__init__.py"):
        imp_pkg = imp_pkg[:-1]
    found = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        start = i
        stmt = line
        # join continued import statements: parentheses or backslashes
        if re.match(r"^\s*(from|import)\b", line):
            while (stmt.count("(") > stmt.count(")") or stmt.rstrip().endswith("\\")) and i + 1 < len(lines):
                i += 1
                stmt += " " + lines[i]
        i += 1
        stmt_clean = stmt.split("#")[0].replace("\\", " ")
        m = re.match(r"^\s*from\s+(\.*)([\w.]*)\s+import\s+\(?(.*?)\)?\s*$", stmt_clean)
        if m:
            dots, mod, names = m.group(1), m.group(2), m.group(3)
            imported = []
            for part in names.split(","):
                bits = part.split()
                if bits:
                    imported.append((bits[0], bits[-1]))
            if dots:
                base = imp_pkg[:len(imp_pkg) - (len(dots) - 1)] if len(dots) - 1 <= len(imp_pkg) else None
                if base is None:
                    continue
                absmod = ".".join([*base, *([mod] if mod else [])])
                cands = {absmod} | ({".".join(absmod.split(".")[k:]) for k in range(len(absmod.split(".")))} if absmod else set())
                mods = cands
            else:
                mods = {mod}
            locals_ = set()
            hit = False
            for mm in mods:
                if not mm and not dots:
                    continue
                if mm in suffixes or any(s.startswith(mm + ".") for s in suffixes if mm) is False and False:
                    pass
                if mm and (mm in suffixes or any(mm.startswith(s + ".") for s in suffixes)):
                    hit = True
                    locals_ |= {loc for _, loc in imported if loc != "*"}
                for orig, loc in imported:
                    sub = f"{mm}.{orig}" if mm else orig
                    if sub in suffixes:
                        hit = True
                        locals_.add(loc)
            if hit:
                found.append((start + 1, (start + 1, i), locals_))
            continue
        m = re.match(r"^\s*import\s+(.+?)\s*$", stmt_clean)
        if m and not re.match(r"^\s*import\s*\(", stmt_clean):
            locals_ = set()
            hit = False
            for part in m.group(1).split(","):
                bits = part.split()
                if not bits:
                    continue
                mod = bits[0]
                loc = bits[-1] if len(bits) == 3 and bits[1] == "as" else mod.split(".")[0]
                if mod in suffixes or any(mod.startswith(s + ".") for s in suffixes) \
                        or any(s.startswith(mod + ".") for s in suffixes):
                    hit = True
                    locals_.add(loc)
            if hit:
                found.append((start + 1, (start + 1, i), locals_))
    return found


def _script_src_matches(importer: str, src: str, target: str) -> bool:
    src = src.split("?")[0].split("#")[0]
    if re.match(r"^[a-z]+:", src, re.I) or src.startswith("//"):
        return False
    if src.startswith("/"):
        s = src.lstrip("/")
        return target == s or target.endswith("/" + s)
    rel = _norm(PurePosixPath(importer).parent.parts + tuple(src.split("/")))
    return rel == target or (rel is not None and target.endswith("/" + rel))


def _referencers(snap: _Snapshot, target: str) -> list[dict]:
    exported = [n for n in _exported_names_text(target, snap.files[target]) if n != "default"]
    out = []
    for rel in sorted(snap.files):
        if rel == target:
            continue
        ext = _ext(rel)
        text = snap.files[rel]
        if ext in HTML_EXTS or ext in JS_EXTS:
            for m in _RX_SCRIPT_SRC.finditer(text):
                if _script_src_matches(rel, m.group(1), target):
                    ln = _line_of(text, m.start())
                    out.append({"path": rel, "line": ln, "text": snap.lines(rel)[ln - 1].strip()[:200], "via": "script"})
        if ext in JS_EXTS or ext in HTML_EXTS:
            imports = _js_imports_of(rel, text, target)
        elif ext in PY_EXTS:
            imports = _py_imports_of(rel, text, target)
        else:
            continue
        if not imports:
            continue
        lines = snap.lines(rel)
        skip, names = set(), set()
        for ln, (a, b), locals_ in imports:
            out.append({"path": rel, "line": ln, "text": lines[ln - 1].strip()[:200], "via": "import"})
            skip.update(range(a, b + 1))
            names |= locals_
        names |= set(exported)
        if not names:
            continue
        rx = re.compile(r"(?<![\w$])(" + "|".join(sorted(map(re.escape, names), key=len, reverse=True)) + r")(?![\w$])")
        for i, line in enumerate(lines, 1):
            if i in skip:
                continue
            used = []
            for m in rx.finditer(line):
                if m.group(1) not in used:
                    used.append(m.group(1))
            for name in used:
                out.append({"path": rel, "line": i, "text": line.strip()[:200], "via": name})
    seen, uniq = set(), []
    for r in sorted(out, key=lambda r: (r["path"], r["line"], r["via"])):
        key = (r["path"], r["line"], r["via"])
        if key not in seen:
            seen.add(key)
            uniq.append(r)
    return uniq


def referencers(repo: Path, path: str) -> list[dict]:
    """Every place another file uses this file (import lines and use lines)."""
    snap = _Snapshot(repo)
    return _referencers(snap, snap.check(path))


# ---------------------------------------------------------------- climb

def climb(repo: Path, path: str, *, max_depth: int = 8) -> dict:
    """Follow "who uses this" upward from a file until pages.

    Extra key ``dead_ends``: files reached that nothing uses and that are not
    pages (e.g. an API route handler; join those with a search for its address).
    """
    snap = _Snapshot(repo)
    start = snap.check(path)
    page_list = _pages(snap)
    page_by_file = {p["file"]: p for p in page_list}

    def pages_under(folder: str) -> list[dict]:
        return [p for p in page_list if p["file"].startswith(folder + "/")]

    chains: list[list[dict]] = []
    reached: dict[str, dict] = {}
    dead_ends: list[str] = []
    truncated = False
    result = {"start": start, "chains": chains, "pages": [], "reaches_page": False,
              "truncated": False, "dead_ends": dead_ends}

    def finish():
        result["pages"] = sorted(reached.values(), key=lambda p: (p["url"], p["file"]))
        result["reaches_page"] = bool(reached)
        result["truncated"] = truncated
        return result

    if start in page_by_file:
        chains.append([])
        reached[start] = page_by_file[start]
        return finish()
    scope = _layout_scope(snap, start)
    if scope is not None:
        chains.append([])
        for p in pages_under(scope):
            reached[p["file"]] = p
        return finish()

    ref_cache: dict[str, dict[str, dict]] = {}

    def refs_by_file(f: str) -> dict[str, dict]:
        if f not in ref_cache:
            grouped: dict[str, list[dict]] = {}
            for r in _referencers(snap, f):
                grouped.setdefault(r["path"], []).append(r)
            ref_cache[f] = {p: next((r for r in rs if r["via"] != "import"), rs[0]) for p, rs in grouped.items()}
        return ref_cache[f]

    queue = [(start, [], {start})]
    expansions = 0
    while queue:
        f, links, visited = queue.pop(0)
        expansions += 1
        if expansions > MAX_EXPANSIONS:
            truncated = True
            break
        refs = refs_by_file(f)
        fresh = {p: r for p, r in refs.items() if p not in visited}
        if not refs and f not in dead_ends:
            dead_ends.append(f)
        for p in sorted(fresh):
            link = fresh[p]
            new_links = links + [link]
            if p in page_by_file or _layout_scope(snap, p) is not None:
                if len(chains) >= MAX_CHAINS:
                    truncated = True
                    continue
                chains.append(new_links)
                if p in page_by_file:
                    reached[p] = page_by_file[p]
                else:
                    for pg in pages_under(_layout_scope(snap, p)):
                        reached[pg["file"]] = pg
            elif len(new_links) >= max_depth:
                truncated = True
            else:
                queue.append((p, new_links, visited | {p}))
    return finish()


# ---------------------------------------------------------------- looks_like_write

_MUTATORS = ("push|pop|shift|unshift|splice|sort|reverse|fill|set|add|delete|clear|append|extend|insert|"
             "remove|update|setdefault|popitem|write|save|copyWithin|assign|discard|merge|put|"
             "increment|decrement|replace|truncate|writelines|setItem|removeItem|persist|commit|"
             "create|destroy|upsert|bulkCreate|insertOne|insertMany|updateOne|updateMany|deleteOne|deleteMany")
_READ_METHODS = {"find", "filter", "map", "reduce", "reduceRight", "some", "every", "includes", "indexOf",
                 "lastIndexOf", "slice", "concat", "join", "get", "has", "keys", "values", "entries", "items",
                 "forEach", "toString", "toFixed", "toLowerCase", "toUpperCase", "trim", "startsWith",
                 "endsWith", "findIndex", "findLast", "at", "flat", "flatMap", "count", "index", "copy",
                 "length", "split", "format", "json", "then", "valueOf", "toISOString", "localeCompare"}
_READ_CALLS = {"console.log", "console.error", "console.warn", "console.info", "console.debug", "print", "len",
               "JSON.stringify", "String", "Number", "Boolean", "Array.isArray", "str", "repr", "int", "float",
               "bool", "isinstance", "type", "list", "tuple", "sorted", "sum", "min", "max", "any", "all",
               "Object.keys", "Object.values", "Object.entries", "structuredClone", "Array.from",
               "parseInt", "parseFloat", "encodeURIComponent", "formatPrice", "enumerate", "iter", "hash", "id",
               "dict", "set", "frozenset", "abs", "round", "Math.max", "Math.min", "Math.round", "Math.floor"}
_KNOWN_MUTATING_CALLS = re.compile(r"\b(?:Object\.(?:assign|defineProperty|defineProperties|setPrototypeOf)|"
                                   r"Reflect\.(?:set|deleteProperty|defineProperty)|setattr|delattr|"
                                   r"heapq\.\w+|random\.shuffle|json\.dump|pickle\.dump)\s*\($")


def looks_like_write(text: str, name: str) -> bool:
    """True when this line plausibly CHANGES `name` rather than only reading it."""
    line = text
    parts = [re.escape(p.strip()) for p in name.split(".") if p.strip()]
    if not parts:
        return True
    ref = r"(?<![\w$])" + r"\s*\??\.\s*".join(parts) + r"(?![\w$])"
    if not re.search(ref, line):
        return False
    member = r"(?:\s*\??\.\s*[\w$#]+|\s*\[(?:[^\[\]]|\[[^\[\]]*\])*\])*"
    N = ref + member
    assign_op = r"\s*(?:\*\*|//|>>>|<<|>>|&&|\|\||\?\?|[-+*/%&|^@:])?=(?![=>])"
    if re.search(N + assign_op, line):
        return True
    if re.search(N + r"\s*(?:\+\+|--)", line) or re.search(r"(?:\+\+|--)\s*" + N, line):
        return True
    if re.search(r"\b(?:delete|del)\s+" + N, line):
        return True
    if re.search(N + r"\s*\??\.\s*(?:" + _MUTATORS + r")\s*\(", line):
        return True
    if re.search(ref + r"\s*:\s*[^=:,)]+=(?![=>])", line):   # annotated assignment `name: T = v`
        return True
    # destructuring / tuple targets: `[a, name] = ...`, `({ name } = ...)`, `name, x = ...`, `x, name = ...`
    m = re.search(r"(?<![=!<>])=(?![=>])", line)
    if m:
        lhs = line[:m.start()]
        if re.search(ref, lhs) and not re.match(r"^\s*(?:export\s+)?(?:const|let|var|type|interface)\b", lhs):
            if re.search(r"[\[{,(]", lhs):
                return True
    if re.search(r"\bfor\s*\(?\s*(?:(?:const|let|var)\s+)?[\[{]?[^;]*?" + ref + r"[^;]*?\s+(?:in|of)\b", line) \
            and not re.search(r"\b(?:in|of)\s+[^;]*" + ref, line):
        return True
    if re.search(r"\bas\s+" + ref + r"\s*[:,)]?", line) and re.search(r"\b(?:with|except|import)\b", line):
        return True
    if re.search(r"\bglobal\s+.*" + ref, line) or re.search(r"\bnonlocal\s+.*" + ref, line):
        return True
    # name passed as an argument: the callee may change it, unless it is a known reader
    for m in re.finditer(r"([\w$.]+)\s*\(([^()]*)\)", line):
        args = m.group(2)
        if re.search(r"(?:^|,)\s*(?:\.\.\.)?" + ref + member + r"\s*(?:,|$)", args):
            callee = m.group(1)
            if callee in _READ_CALLS:
                continue
            if callee.split(".")[-1] in _READ_METHODS:
                continue
            return True
    if _KNOWN_MUTATING_CALLS.search(line.split(name)[0] if name in line else ""):
        return True
    # a method call on it that is not a known reader
    for m in re.finditer(ref + r"((?:\s*\??\.\s*[\w$#]+)*)\s*\??\.\s*([\w$#]+)\s*\(", line):
        if m.group(2) not in _READ_METHODS:
            return True
    return False


# ---------------------------------------------------------------- addresses

_RX_JS_CALLS = re.compile(
    r"(?<![\w$.])(fetch|axios(?:\.(?:get|post|put|patch|delete|head|options|request))?|"
    r"(?:\$|jQuery)\.(?:ajax|get|post|getJSON|load)|new\s+URL|new\s+Request|new\s+EventSource|new\s+WebSocket|"
    r"navigator\.sendBeacon|[\w$.]*\.open|window\.open)\s*\(")
_RX_PY_CALLS = re.compile(
    r"(?<![\w.])((?:requests|httpx)\.(?:get|post|put|patch|delete|head|options|request|stream)|"
    r"(?:urllib\.request\.)?urlopen|(?:urllib\.request\.)?Request)\s*\(")
_RX_FORM_ACTION = re.compile(r"\b(?:action|formAction)\s*=\s*\{")
_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}


def _args(text: str, pos: int, max_args: int = 2) -> list[str]:
    """Top-level arguments of a call whose "(" is just before pos (may span lines)."""
    args, cur, depth, quote, i = [], [], 0, None, pos
    while i < len(text) and len(args) < max_args:
        c = text[i]
        if quote:
            cur.append(c)
            if c == "\\" and i + 1 < len(text):
                cur.append(text[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "'\"`":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                args.append("".join(cur).strip())
                return args
            depth -= 1
        elif c == "," and depth == 0:
            args.append("".join(cur).strip())
            cur = []
            i += 1
            continue
        cur.append(c)
        i += 1
    if cur and len(args) < max_args:
        args.append("".join(cur).strip())
    return args


def _classify_js(arg: str) -> str | None:
    a = arg.strip()
    if not a:
        return None
    if re.fullmatch(r"\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'", a):
        return None
    if re.fullmatch(r"`[^`]*`", a, re.S) and "${" not in a:
        return None
    if a.startswith("`") and "${" in a and re.fullmatch(r"`[^`]*`", a, re.S):
        return "template string with ${}"
    if _top_level_plus(a):
        return "concatenation with +"
    if a.startswith("{"):
        return "object argument (address inside may be computed)"
    if re.fullmatch(r"[\w$.?\[\]\"']+", a):
        return "variable"
    if "(" in a:
        return "function call"
    return "expression"


def _top_level_plus(a: str) -> bool:
    depth, quote = 0, None
    i = 0
    while i < len(a):
        c = a[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "'\"`":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "+" and depth == 0:
            return True
        i += 1
    return False


def _classify_py(arg: str) -> str | None:
    a = arg.strip()
    if not a or "=" in a.split("(")[0] and not a.startswith(("'", '"')) and re.match(r"^\w+\s*=", a):
        # keyword argument as first argument (url=...): classify its value
        if re.match(r"^\w+\s*=", a or ""):
            a = a.split("=", 1)[1].strip()
        if not a:
            return None
    m = re.fullmatch(r"([rRbBuU]{0,2})(\"\"\"[\s\S]*\"\"\"|'''[\s\S]*'''|\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*')", a)
    if m:
        return None
    m = re.match(r"^[rRbB]?[fF][rRbB]?([\"'])", a)
    if m:
        return "f-string" if "{" in a else None
    if re.search(r"[\"']\s*%\s*", a):
        return "% formatting"
    if re.search(r"\.format\s*\(", a):
        return ".format() call"
    if _top_level_plus(a):
        return "concatenation with +"
    if re.fullmatch(r"[\w.\[\]\"']+", a):
        return "variable"
    if "(" in a:
        return "function call"
    return "expression"


def computed_addresses(repo: Path) -> list[dict]:
    """Places where a request address is built from parts."""
    snap = _Snapshot(repo)
    out = []
    for rel in sorted(snap.files):
        ext = _ext(rel)
        text = snap.files[rel]
        lines = snap.lines(rel)
        if ext in JS_EXTS or ext in HTML_EXTS:
            for m in _RX_JS_CALLS.finditer(text):
                callee = re.sub(r"\s+", " ", m.group(1))
                args = _args(text, m.end())
                if callee.endswith(".open") and callee != "window.open":
                    first = args[0].strip("'\"`").upper() if args else ""
                    if not args or first not in _HTTP_METHODS:
                        if not (args and re.fullmatch(r"[\w$.]+", args[0]) and len(args) > 1):
                            continue  # not XHR-shaped (e.g. dialog.open())
                    arg = args[1] if len(args) > 1 else ""
                    which = "second argument of .open("
                else:
                    arg = args[0] if args else ""
                    which = f"first argument of {callee}("
                why = _classify_js(arg)
                if why:
                    ln = _line_of(text, m.start())
                    out.append({"path": rel, "line": ln, "text": lines[ln - 1].strip()[:200], "why": f"{which} is a {why}"})
            for m in _RX_FORM_ACTION.finditer(text):
                arg = _args(text, m.end(), 1)
                inner = arg[0] if arg else ""
                if re.fullmatch(r"\"[^\"\n]*\"|'[^'\n]*'|`[^`$]*`", inner.strip()):
                    continue
                ln = _line_of(text, m.start())
                out.append({"path": rel, "line": ln, "text": lines[ln - 1].strip()[:200],
                            "why": "form action is an expression, not a plain string"})
        elif ext in PY_EXTS:
            for m in _RX_PY_CALLS.finditer(text):
                callee = m.group(1)
                args = _args(text, m.end())
                if callee.endswith(".request"):
                    arg, which = (args[1] if len(args) > 1 else ""), f"second argument of {callee}("
                else:
                    arg, which = (args[0] if args else ""), f"first argument of {callee}("
                why = _classify_py(arg)
                if why:
                    ln = _line_of(text, m.start())
                    out.append({"path": rel, "line": ln, "text": lines[ln - 1].strip()[:200], "why": f"{which} is a {why}"})
    return sorted(out, key=lambda r: (r["path"], r["line"], r["why"]))


_RX_ADDR = re.compile(r"([\"'`])(/(?!/)[^\"'`\s]*)\1")


def address_literals(repo: Path, path: str) -> list[dict]:
    """String literals in one file that look like an app-relative address."""
    snap = _Snapshot(repo)
    rel = snap.check(path)
    out = []
    for i, line in enumerate(snap.lines(rel), 1):
        for m in _RX_ADDR.finditer(line):
            addr = m.group(2)
            if addr != "/" and not re.match(r"/[\w:$\[{.~-]", addr):
                continue
            out.append({"line": i, "text": line.strip()[:200], "address": addr})
    return out
