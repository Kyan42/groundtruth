"""The tool catalog: the only thing the verifier can touch, as a plain synchronous class.

The model runs elsewhere and sends tool calls as data; a local runner hands each one to Catalog.run().
Tools are grouped by the kind of evidence they produce:

  ui       a real browser on the app            (observe, navigate, click, fill, read_attribute,
                                                 click_and_watch, wait, screenshot, media, hover, look;
                                                 and compare, which only records a comparison the verifier made)
  api      one HTTP request to the app          (http_request)
  runtime  measurements of the running process  (process_info, gpu_check, cpu_sample, net_connections)
  code     read-only, repo-scoped source access (list_files, grep, read_lines) -- max 15 lines per read
  sweeps   checks over every route / file       (list_routes, probe_each, match_each)
  data     reset the app's data, and run the     (reset_data, data_tools, set_up)
           project's declared data tools

The caller is untrusted: any tool name and any arguments may arrive. Every call, refused or not, is logged to
<claim_dir>/receipts.jsonl so the report can show what was actually observed.
"""
import asyncio
import importlib
import inspect
import json
import logging
import os
import re
import shutil
import subprocess
import time
import types
import typing
import urllib.request
from pathlib import Path

MAX_LINES = 15
MAX_EXCERPTS = 3


class Refused(Exception):
    """The call is not allowed; becomes {"text": "refused: <reason>", "isError": True}."""


def _json_safe(v):
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, dict):
        return {(k if isinstance(k, str) else repr(k)): _json_safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_json_safe(x) for x in v]
    return repr(v)


def _type_ok(value, hint) -> bool:
    if hint is inspect.Parameter.empty or hint is typing.Any:
        return True
    origin = typing.get_origin(hint)
    if origin in (typing.Union, types.UnionType):
        return any(_type_ok(value, h) for h in typing.get_args(hint))
    if hint is type(None):
        return value is None
    if origin is not None:
        hint = origin
    if hint is int:
        return isinstance(value, int) and not isinstance(value, bool)
    if hint is float:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, hint)


def _type_name(hint) -> str:
    return str(hint).replace("typing.", "") if not isinstance(hint, type) else hint.__name__


def _next_id(folder: Path, prefix: str, ext: str) -> int:
    """Highest N already used for <prefix>-N.<ext> in folder, so a second catalog never overwrites evidence."""
    n = 0
    for f in folder.glob(f"{prefix}-*.{ext}"):
        m = re.fullmatch(rf"{prefix}-(\d+)\.{ext}", f.name)
        if m:
            n = max(n, int(m.group(1)))
    return n



class _QuietCancelledRequests(logging.Filter):
    """When a page moves on while one of its requests is still being checked in _only_the_app, Playwright cancels that
    request's task and asyncio prints a traceback for it. Nothing went wrong there, so that one message is dropped.
    Every other asyncio message still gets through."""

    def filter(self, record):
        exc = record.exc_info[1] if record.exc_info else None
        return not (isinstance(exc, asyncio.CancelledError) and "Connection.dispatch" in record.getMessage())


logging.getLogger("asyncio").addFilter(_QuietCancelledRequests())

# The pointer drawn in a filmed page, the same as the older Node tool's: one element outside <body>, ignoring the
# pointer and hidden from accessibility, moved by one eased requestAnimationFrame animation (never many small real
# mouse moves). Each click also leaves a ring that fades, drawn inside the same element, because the Python API
# has no click marks of its own. The promise always settles: if animation frames stall (a hidden or navigating
# page), a timer ends it shortly after the planned length.
_GLIDE_JS = """({from, to, ms, mark}) => new Promise((resolve) => {
  let el = document.querySelector("groundtruth-cursor");
  if (!el) {
    el = document.createElement("groundtruth-cursor");
    el.setAttribute("aria-hidden", "true");
    el.style.cssText = "position:fixed;left:0;top:0;z-index:2147483647;pointer-events:none;width:22px;height:22px;";
    el.innerHTML = '<svg width="22" height="22" viewBox="0 0 22 22"><path d="M3 2 L3 18 L7.5 14 L10.5 20.5 L13 19.4 L10 13 L16 13 Z" fill="#fff" stroke="#111" stroke-width="1.4" stroke-linejoin="round"/></svg>';
    document.documentElement.appendChild(el);
  }
  const cursorEl = el;
  const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);
  let done = false;
  const finish = () => {
    if (done) return;
    done = true;
    cursorEl.style.transform = `translate(${to.x - 3}px, ${to.y - 2}px)`;
    if (mark) {
      const ring = document.createElement("span");
      ring.style.cssText = "position:absolute;left:3px;top:2px;width:26px;height:26px;margin:-13px 0 0 -13px;"
        + "box-sizing:border-box;border:3px solid rgba(230,40,40,0.9);border-radius:50%;pointer-events:none;";
      cursorEl.insertBefore(ring, cursorEl.firstChild);
      const anim = ring.animate([{opacity: 1, transform: "scale(0.4)"}, {opacity: 0, transform: "scale(1.3)"}],
                                {duration: 450, easing: "ease-out", fill: "forwards"});
      anim.onfinish = () => ring.remove();
      setTimeout(() => ring.remove(), 1000);
    }
    resolve();
  };
  const start = performance.now();
  const tick = (now) => {
    if (done) return;
    const t = Math.min(1, (now - start) / ms);
    const e = ease(t);
    cursorEl.style.transform = `translate(${from.x + (to.x - from.x) * e - 3}px, ${from.y + (to.y - from.y) * e - 2}px)`;
    if (t < 1) requestAnimationFrame(tick);
    else finish();
  };
  requestAnimationFrame(tick);
  setTimeout(finish, ms + 300);
})"""

# The outline a recorded comparison draws around the element it is about, in a filmed page: like the drawn pointer,
# one element outside <body>, hidden from accessibility and never catching the pointer, so observe never lists it.
# It removes itself after about a second and a half, even if nothing else runs on the page.
_OUTLINE_JS = """({box, color, ms}) => {
  const el = document.createElement("groundtruth-outline");
  el.setAttribute("aria-hidden", "true");
  el.style.cssText = "position:fixed;z-index:2147483646;pointer-events:none;box-sizing:border-box;border-radius:6px;"
    + `left:${box.x - 4}px;top:${box.y - 4}px;width:${box.width + 8}px;height:${box.height + 8}px;border:3px solid ${color};`;
  document.documentElement.appendChild(el);
  setTimeout(() => el.remove(), ms);
}"""
# The film's last frames carry a mark drawn at a known moment, so the film's clock can be checked against the
# receipts' once the film is saved. The recorder starts a film at its first frame, which is usually about one frame
# after the page is made but can be later on a busy machine; finding this mark measures that for each film. It is a
# small square in the bottom-right corner, outside <body>, hidden from accessibility and never catching the pointer,
# drawn after the case's last action and taken away a third of a second later.
_MARK = (255, 0, 255)
_LAST_FRAME_JS = """() => new Promise((resolve) => {
  const el = document.createElement("groundtruth-tick");
  el.setAttribute("aria-hidden", "true");
  el.style.cssText = "position:fixed;right:0;bottom:0;width:24px;height:24px;z-index:2147483647;pointer-events:none;background:rgb(255,0,255);";
  document.documentElement.appendChild(el);
  requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
  setTimeout(resolve, 200);
})"""
# Plays a saved film in a page and returns the first film time, within its last seconds, at which the mark shows.
_FIND_MARK_JS = """async ({w, h}) => {
  // Every wait here has a limit, and so does the whole search: a film that will not load or seek must never hold
  // up the case that made it. Any failure answers null, and the caller falls back to the page's own clock.
  const deadline = Date.now() + 8000;
  const within = (p, ms) => Promise.race([p, new Promise((r) => setTimeout(() => r("late"), ms))]);
  try {
    const v = document.querySelector("video");
    if (!v) return null;
    const ready = new Promise((r, no) => { if (v.readyState >= 1) r("ok"); else { v.onloadedmetadata = () => r("ok"); v.onerror = () => no(new Error("film")); } });
    if (await within(ready, 4000) === "late") return null;
    const seek = async (t) => {
      if (Date.now() > deadline) throw new Error("late");
      if (Math.abs(v.currentTime - t) < 0.0005 && !v.seeking) return;      // already there: no seeked event would come
      const done = new Promise((r) => { v.onseeked = () => r("ok"); });
      v.currentTime = t;
      if (await within(done, 1500) === "late") throw new Error("late");
    };
    if (!isFinite(v.duration)) await seek(1e9);
    if (!isFinite(v.duration) || !v.videoWidth) return null;
    const c = document.createElement("canvas"); c.width = v.videoWidth; c.height = v.videoHeight;
    const ctx = c.getContext("2d");
    const marked = async (t) => { await seek(t); ctx.drawImage(v, 0, 0);
      const [r, g, b] = ctx.getImageData(c.width - 8, c.height - 8, 1, 1).data; return r > 200 && g < 80 && b > 200; };
    let hi = null;                                    // the mark shows for a third of a second near the end
    for (let t = v.duration - 0.001; t >= Math.max(0, v.duration - 6); t -= 0.1) { if (await marked(t)) { hi = t; break; } }
    if (hi === null) return null;
    let lo = Math.max(0, hi - 0.5);
    if (await marked(lo)) return null;
    while (hi - lo > 0.01) { const m = (lo + hi) / 2; if (await marked(m)) hi = m; else lo = m; }
    return hi;
  } catch (e) {
    return null;
  }
}"""
_MATCH, _MISMATCH = "rgba(22,160,70,0.95)", "rgba(220,40,40,0.95)"
_SAYING = "rgba(60,110,200,0.9)"
MAX_SAY = 110
SAY_REFUSED = "say it in one or two short sentences, at most 110 characters; call say again for the next thought"
MAX_PICTURE = 2 * 1024 * 1024
_PLAIN_ID = re.compile(r"[A-Za-z0-9_-]{1,32}")

class Catalog:
    phase: int = 1  # set by the runner; recorded on every receipt

    def __init__(self, env: dict, claim_dir: Path, headed: bool = False, record: bool = False,
                 discovery: bool = False):
        self.env = dict(env)
        self.out = Path(claim_dir)
        self.out.mkdir(parents=True, exist_ok=True)
        self.app_url = str(self.env["url"]).rstrip("/")
        self.repo = Path(self.env["repo"]).resolve() if self.env.get("repo") else None
        self.pid = self.env.get("pid")
        self.headed = headed
        vp = self.env.get("viewport")
        ok = isinstance(vp, dict) and all(isinstance(vp.get(k), int) and not isinstance(vp.get(k), bool) and vp[k] > 0 for k in ("width", "height"))
        self.viewport = {"width": vp["width"], "height": vp["height"]} if ok else {"width": 900, "height": 700}   # the window, and every film
        self.record = record        # film the page (a case's browser), with a pointer that glides to each click
        self.discovery = discovery  # a discovery unit only looks, so it may never set up data
        # What this unit has done so far, for set_up's rules. Refused calls are not counted; a call that started and
        # then failed is (even one whose failure is reported as "bad arguments" from inside the tool). It lives on this object, so a second attempt of a case (a new catalog) starts afresh.
        self.set_ups = 0            # set_up calls that ran, whether they worked or not
        self.set_up_log = []        # every set_up asked for: the tool, the input, and what it returned or how it failed
        self.other_call = None      # the first tool other than reset_data or set_up that ran in this unit
        self.was_reset = False      # set_up needs clean data: only after this unit's own reset has run
        self._ran = False           # whether the current call got past every check and started
        self.recording_started = None   # wall-clock time (the receipts' clock) the filmed page was created
        self._context = self._video = None
        self._cursor = None         # where the drawn pointer last stopped, in page coordinates
        self.blocked = []           # requests the page tried to make to other hosts
        self._pw = self._browser = self._page = None
        self._shots = _next_id(self.out, "shot", "png")
        self._checks = _next_id(self.out, "check", "json")
        self._tools = {
            # ui
            "observe": self.observe,
            "navigate": self.navigate,
            "click": self.click,
            "fill": self.fill,
            "read_attribute": self.read_attribute,
            "click_and_watch": self.click_and_watch,
            "wait": self.wait,
            "screenshot": self.screenshot,
            "media": self.media,
            "hover": self.hover,
            "look": self.look,
            "compare": self.compare,
            "say": self.say,
            "situation": self.situation,
            "read_tool": self.read_tool,
            # api
            "http_request": self.http_request,
            # runtime
            "process_info": self.process_info,
            "gpu_check": self.gpu_check,
            "cpu_sample": self.cpu_sample,
            "net_connections": self.net_connections,
            # code
            "list_files": self.list_files,
            "find": self.find,
            "trace": self.trace,
            "grep": self.grep,
            "read_lines": self.read_lines,
            # sweeps
            "list_routes": self.list_routes,
            "probe_each": self.probe_each,
            "match_each": self.match_each,
            # data
            "reset_data": self.reset_data,
            "data_tools": self.data_tools,
            "set_up": self.set_up,
        }

    # ------------------------------------------------------------ dispatch
    def names(self) -> list[str]:
        return list(self._tools)

    def run(self, tool: str, args: dict | None) -> dict:
        """Run one tool call from the (untrusted) caller. Never raises."""
        self._ran = False
        res = self._dispatch(tool, args)
        if self._ran:               # it got past every check and started; set_up's rules count it
            if tool == "set_up":
                self.set_ups += 1
            elif tool not in ("reset_data", "read_tool") and self.other_call is None:
                self.other_call = tool
        if tool == "set_up" and isinstance(args, dict) and len(self.set_up_log) < 20:
            # Every step that was asked for, in order: what it returned, or how it failed (a refusal is a failure too).
            step = {"tool": _json_safe(args.get("tool")), "input": _json_safe(args.get("input") or {})}
            step["error" if res["isError"] else "returned"] = res["text"] if res["isError"] else _json_safe(res["data"])
            self.set_up_log.append(step)
        self._receipt(tool, args, res)
        return res

    def _dispatch(self, tool, args) -> dict:
        try:
            if not isinstance(tool, str) or tool not in self._tools:
                raise Refused(f"unknown tool {tool!r}")
            if args is None:
                args = {}
            if not isinstance(args, dict) or not all(isinstance(k, str) for k in args):
                raise Refused("arguments must be an object with string keys")
            fn = self._tools[tool]
            sig = inspect.signature(fn)
            try:
                bound = sig.bind(**args)
            except TypeError as e:
                raise Refused(f"bad arguments for {tool}: {e}") from None
            hints = typing.get_type_hints(fn)
            for name, value in bound.arguments.items():
                hint = hints.get(name, inspect.Parameter.empty)
                default = sig.parameters[name].default
                if value is None and default is None:
                    continue
                if not _type_ok(value, hint):
                    raise Refused(f"argument {name} of {tool} must be {_type_name(hint)}, "
                                  f"got {type(value).__name__}")
            self._ran = True
            out = fn(**args)
        except Refused as e:
            self._ran = False       # the tool itself refused before acting: not counted
            return {"text": f"refused: {e}", "isError": True, "data": None, "refused": True}
        except TypeError as e:
            return {"text": f"refused: bad arguments for {tool}: {e}", "isError": True, "data": None,
                    "refused": True}
        except Exception as e:  # noqa: BLE001 -- the caller gets the error as text, the runner keeps going
            return {"text": f"error: {e}", "isError": True, "data": None}
        text, data = out if isinstance(out, tuple) else (out, None)
        return {"text": text, "isError": False, "data": data}

    def _receipt(self, tool, args, res: dict) -> None:
        line = {"t": round(time.time(), 2), "phase": self.phase, "tool": _json_safe(tool),
                "args": _json_safe(args if args is not None else {}), "result": res["text"]}
        if res.pop("refused", False):
            line["refused"] = True
        elif tool == "set_up" and not res["isError"]:
            line["returned"] = _json_safe(res["data"])     # what the tool made, kept with the input that made it
        elif tool == "compare" and not res["isError"]:
            line["compared"] = _json_safe(res["data"])     # the comparison as recorded; a picture from look is never kept here
        elif tool == "say" and not res["isError"]:
            line["said"] = _json_safe(res["data"])         # a spoken line of a walkthrough
        elif tool == "situation" and not res["isError"]:
            line["situation"] = _json_safe(res["data"])    # whether the case started in its variation's situation
        try:
            with open(self.out / "receipts.jsonl", "a") as f:
                f.write(json.dumps(line) + "\n")
        except Exception:  # noqa: BLE001 -- run() never raises
            pass

    # ------------------------------------------------------------ guards
    def _app_path(self, path: str) -> str:
        """An app-relative path, or a full URL under the registered app URL. Any other scheme/host is refused."""
        base = self.app_url
        under_app = path == base or (path.startswith(base) and path[len(base)] in "/?#")
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", path) and not under_app:
            raise Refused("only the app's own address is allowed")
        return path if under_app else base + "/" + path.lstrip("/")

    def _need_repo(self) -> Path:
        if self.repo is None:
            raise Refused("no repo is registered")
        return self.repo

    def _need_pid(self) -> int:
        if self.pid is None:
            raise Refused("no process id is registered")
        pid = int(self.pid)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            raise RuntimeError(f"the registered process (pid {pid}) is no longer running, so nothing can be "
                               "measured; the app has to be registered again after a restart") from None
        except PermissionError:
            pass                      # it exists, owned by someone else
        return pid

    def _repo_file(self, path: str) -> Path:
        repo = self._need_repo()
        f = (repo / path).resolve()
        if repo not in f.parents and f != repo:
            raise Refused("path is outside the repo")
        return f

    def _visible(self, f: Path) -> bool:
        """A regular file inside the repo (after resolving symlinks), not hidden, not node_modules/__pycache__."""
        repo = self.repo
        try:
            real = f.resolve()
            if repo not in real.parents:
                return False
            parts = f.relative_to(repo).parts + real.relative_to(repo).parts
        except ValueError:
            return False
        return real.is_file() and not any(p.startswith(".") or p in ("node_modules", "__pycache__") for p in parts)

    def _readable(self, path: str) -> Path:
        f = self._repo_file(path)
        if not f.exists():
            raise ValueError(f"no such file: {path}")
        if not self._visible(f):
            raise Refused("that file is hidden or not a regular source file")
        return f

    # ------------------------------------------------------------ ui: a browser on the app
    def page(self):
        if self._page is None:
            if self._pw is None:
                from playwright.sync_api import sync_playwright
                self._pw = sync_playwright().start()
            if self._browser is None:
                self._browser = self._pw.chromium.launch(headless=not self.headed,
                                                         slow_mo=250 if self.headed else 0)
            if self.record:
                ctx = self._browser.new_context(viewport=dict(self.viewport),
                                                record_video_dir=str(self.out / ".clip-tmp"),
                                                record_video_size=dict(self.viewport))
                p = ctx.new_page()
                started = time.time()    # film time 0; measured: the film shows a change 0.04-0.06 s (about one frame) after this clock says (tests/test_film_clock.py)
            else:
                ctx, p = None, self._browser.new_page(viewport=dict(self.viewport))
            p.route("**/*", self._only_the_app)
            try:
                p.goto(self.app_url)
            except Exception:
                p.close()
                if ctx is not None:          # the next call opens a fresh one; this one's partial film is not kept
                    try:
                        ctx.close()
                    except Exception:  # noqa: BLE001
                        pass
                    shutil.rmtree(self.out / ".clip-tmp", ignore_errors=True)
                raise
            if ctx is not None:
                self._context, self._video, self.recording_started = ctx, p.video, started
                self._cursor = None
            self._page = p
        return self._page

    def _glide(self, target, mark: bool = True) -> None:
        """When filming: move the drawn pointer to the target and, for a click, mark it. Never fails the action."""
        if not self.record or self._page is None:
            return
        try:
            self._glide_to(target, mark)
        except Exception:  # noqa: BLE001 -- the page navigated or the element went away mid-glide: act anyway
            pass

    def _glide_to(self, target, mark: bool = True) -> None:
        box = target.bounding_box(timeout=2000)
        if not box:
            return                    # not on screen yet: the action waits for it as usual
        p = self._page
        to = {"x": box["x"] + box["width"] / 2, "y": box["y"] + box["height"] / 2}
        size = p.viewport_size or self.viewport
        frm = self._cursor or {"x": size["width"] / 2, "y": size["height"] * 0.75}
        ms = round(min(700, max(250, ((to["x"] - frm["x"]) ** 2 + (to["y"] - frm["y"]) ** 2) ** 0.5 * 0.9)))
        self._cursor = to
        p.evaluate(_GLIDE_JS, {"from": frm, "to": to, "ms": ms, "mark": mark})

    def _is_app(self, url: str) -> bool:
        return url == self.app_url or url.startswith((self.app_url + "/", self.app_url + "?", self.app_url + "#"))

    def _only_the_app(self, route) -> None:
        """Every request the page makes (navigation, script, image) is dropped unless it goes to the app.

        The browser follows redirects on its own without asking again, so a redirect is never handed to it.
        Requests to the app are fetched here without following redirects. A redirect that leaves the app is
        replaced by a "blocked" page. A redirect that stays inside the app is turned into a fresh request (a new
        navigation for pages, another checked fetch for everything else), so every hop passes through this check."""
        from urllib.parse import urljoin
        req, url = route.request, route.request.url
        if url.startswith(("data:", "blob:", "about:")):
            route.continue_()
            return
        if not self._is_app(url):
            self.blocked.append(url[:200])
            route.abort()
            return
        try:
            resp = route.fetch(max_redirects=0)
            for _ in range(10):
                target = urljoin(url, resp.headers.get("location") or "") if 300 <= resp.status < 400 else None
                if not target or target == url and resp.status not in (301, 302, 303, 307, 308):
                    break
                if not self._is_app(target):
                    self.blocked.append(target[:200])
                    route.fulfill(status=403, content_type="text/plain",
                                  body=f"Groundtruth blocked a redirect that leaves the app: {target[:200]}")
                    return
                if req.is_navigation_request():
                    route.fulfill(status=200, content_type="text/html",
                                  body=f"<script>location.replace({json.dumps(target)})</script>")
                    return
                url, resp = target, route.fetch(url=target, method="GET", max_redirects=0)
            route.fulfill(response=resp)
        except Exception:  # noqa: BLE001 -- the page went away mid-request, or the app dropped the connection
            try:
                route.abort()
            except Exception:  # noqa: BLE001
                pass

    def _shot(self, p) -> str:
        self._shots += 1
        sid = f"shot-{self._shots}"
        p.screenshot(path=str(self.out / f"{sid}.png"))
        return sid

    def observe(self) -> str:
        """The current page as an accessibility tree (roles, names, text, values). Every element carries a
        reference such as [ref=e12], good until the next observe, that click, fill and read_attribute accept."""
        p = self.page()
        snap = p.locator("body").aria_snapshot(mode="ai")
        return f"URL: {p.url}\n{snap}"

    def navigate(self, path: str) -> str:
        """Go to an app-relative path such as "/"."""
        url = self._app_path(path)
        p = self.page()
        p.goto(url)
        return f"Now at {p.url}"

    REF = re.compile(r"(?:f\d{1,3})?e\d{1,6}")

    def _by_ref(self, ref: str):
        """The element the latest observe gave this reference to, and what it is, read here from the page.

        A reference is a short fixed-form token, never a selector: anything else is refused. One that no longer
        points at an element (the page changed since that observe) is an error, never a guess at another element."""
        if not self.REF.fullmatch(ref):
            raise Refused("a reference looks like e12, copied from the latest observe")
        loc = self.page().locator(f"aria-ref={ref}")
        try:
            what = loc.evaluate(
                "e => (e.getAttribute('role') || e.tagName.toLowerCase()) + ' \"' + "
                "(e.getAttribute('aria-label') || e.innerText || e.value || e.getAttribute('placeholder') || '')"
                ".replace(/\\s+/g, ' ').trim().slice(0, 80) + '\"'", timeout=3000)
        except Exception:  # noqa: BLE001
            raise RuntimeError(f"no element has the reference {ref} now. The page has changed since that observe: "
                               "observe again and use the new reference") from None
        return loc, what

    def _target(self, ref: str, role: str, name: str):
        """One element to act on: by reference, or by ARIA role and exact accessible name."""
        if ref:
            return self._by_ref(ref)
        if not role:
            raise Refused("give ref (from the latest observe), or role and name")
        return self.page().get_by_role(role, name=name, exact=True), f'{role} "{name}"'

    def click(self, ref: str = "", role: str = "", name: str = "") -> str:
        """Click one element: by its reference from the latest observe, or by ARIA role and exact accessible name."""
        loc, what = self._target(ref, role, name)
        self._glide(loc)
        loc.click(timeout=5000)
        self.page().wait_for_timeout(300)
        return f"clicked {what}"

    def fill(self, value: str, ref: str = "", label: str = "") -> str:
        """Replace the text in one field: by its reference from the latest observe, or by its label."""
        if ref:
            loc, what = self._by_ref(ref)
        elif label:
            loc, what = self.page().get_by_label(label, exact=True), f'the field labelled "{label}"'
        else:
            raise Refused("give ref (from the latest observe), or label")
        self._glide(loc, mark=False)  # typing, not a click: the pointer moves there, no ring
        loc.fill(value, timeout=5000)
        return f"filled {what} with {len(value)} characters ({len(re.sub(r'\s', '', value))} not counting spaces)"

    def media(self) -> str:
        """Every video and audio element on the page: its source address, length, position, whether it is playing
        and whether it has loaded. The accessibility tree does not show these. This says what is loaded, not what
        the picture looks like."""
        items = self.page().evaluate(
            "() => [...document.querySelectorAll('video, audio')].map(m => ({kind: m.tagName.toLowerCase(), "
            "src: m.currentSrc || m.getAttribute('src') || '', seconds: Number.isFinite(m.duration) ? +m.duration.toFixed(2) : null, "
            "at: +m.currentTime.toFixed(2), playing: !m.paused && !m.ended, loaded: m.readyState >= 2, "
            "width: m.videoWidth || 0, height: m.videoHeight || 0, error: m.error ? m.error.message || 'error' : '', "
            "visible: !!(m.offsetWidth || m.offsetHeight)}))")
        if not items:
            return "no video or audio element on this page"
        base = self.app_url
        lines = []
        for i, m in enumerate(items, 1):
            src = m["src"][len(base):] if m["src"].startswith(base) else m["src"]
            size = f', {m["width"]}x{m["height"]}' if m["kind"] == "video" else ""
            lines.append(f'{i}. {m["kind"]} source {src or "(none)"}; length {m["seconds"]} s; at {m["at"]} s; '
                         f'{"playing" if m["playing"] else "not playing"}; {"loaded" if m["loaded"] else "not loaded"}{size}; '
                         f'{"visible" if m["visible"] else "hidden"}' + (f'; error: {m["error"]}' if m["error"] else ""))
        return "\n".join(lines)

    def read_attribute(self, attribute: str, ref: str = "", test_id: str = "") -> str:
        """Read one attribute of one element: by its reference from the latest observe, or by its data-testid."""
        if ref:
            loc, what = self._by_ref(ref)
            return f"{what}: {attribute} = {loc.get_attribute(attribute, timeout=5000)}"
        if not test_id:
            raise Refused("give ref (from the latest observe), or test_id")
        return str(self.page().get_by_test_id(test_id).get_attribute(attribute, timeout=5000))

    def click_and_watch(self, test_id: str, attribute: str, ms: int = 3000, ref: str = "", role: str = "", name: str = "") -> str:
        """Click, then sample one attribute of another element every ~40 ms for up to 10 s; screenshot the
        first change."""
        p = self.page()
        loc = p.get_by_test_id(test_id)
        target, _ = self._target(ref, role, name)
        self._glide(target)            # before the first reading, so the glide is not inside the measured window
        start_val = loc.get_attribute(attribute, timeout=5000)
        target.click(timeout=5000)
        t0, seq, shot = time.time(), [(0, start_val)], None
        while (time.time() - t0) * 1000 < max(200, min(ms, 10_000)):
            v = loc.get_attribute(attribute)
            if v != seq[-1][1]:
                seq.append((int((time.time() - t0) * 1000), v))
                if shot is None and v != start_val:
                    shot = self._shot(p)
            p.wait_for_timeout(40)
        return " -> ".join(f"{v}@{t}ms" for t, v in seq) + (
            f"\nscreenshot of first change: {shot}" if shot else "\n(no change seen)")

    def wait(self, ms: int) -> str:
        """Wait up to 10 s for the page to finish something in progress."""
        p = self.page()
        p.wait_for_timeout(max(0, min(ms, 10_000)))
        return "waited"

    def screenshot(self, label: str) -> str:
        """Save a screenshot as evidence; returns its id to cite in the report."""
        return self._shot(self.page())

    def hover(self, ref: str = "", role: str = "", name: str = "") -> str:
        """Point at one element without clicking it: by its reference from the latest observe, or by ARIA role and
        exact accessible name. When filming, the drawn pointer glides there with no click ring."""
        loc, what = self._target(ref, role, name)
        self._glide(loc, mark=False)
        loc.hover(timeout=5000)
        self.page().wait_for_timeout(300)
        return f"pointed at {what}"

    def look(self, label: str, ref: str = ""):
        """A picture of the page as it is on screen, or of one element (by its reference from the latest observe).
        Saved as the next screenshot; the picture itself travels back in the result's data, never into the receipts."""
        import base64
        if ref:
            loc, _ = self._by_ref(ref)
            self._shots += 1
            sid = f"shot-{self._shots}"
            loc.screenshot(path=str(self.out / f"{sid}.png"), timeout=5000)
        else:
            sid = self._shot(self.page())
        png = (self.out / f"{sid}.png").read_bytes()
        if len(png) > MAX_PICTURE:
            return (f"{sid} (the picture is larger than 2 MB, so it is not shown; look at one element with ref for a "
                    "smaller one)"), None
        return sid, {"image": {"mime": "image/png", "data": base64.b64encode(png).decode()}}

    def _element(self, ref: str):
        """The element a reference names, scrolled into view, and its own text read by plain code."""
        loc, _ = self._by_ref(ref)
        loc.scroll_into_view_if_needed(timeout=5000)
        return loc, loc.evaluate("e => (e.innerText || e.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 300)", timeout=3000)

    def say(self, text: str, claim: str, ref: str = ""):
        """A spoken line of a walkthrough, recorded as it is said. It checks nothing. With a ref, the element's own
        text is read by plain code and, when filming, the pointer goes there and the element is outlined in blue."""
        began = time.time()
        if not text.strip() or len(text) > MAX_SAY:
            raise Refused(SAY_REFUSED)
        if not _PLAIN_ID.fullmatch(claim):
            raise Refused("claim is the id of a property, such as p1")
        element, t = None, began
        if ref:
            loc, element = self._element(ref)
            if self.record:
                self._glide(loc, mark=False)
                t = time.time()
                self._outline(loc, color=_SAYING)
                self._hold(300)
        return "said", {"text": text.strip(), "element": element, "claim": claim, "t": round(t, 2)}

    def situation(self, expected: str, found: str, matches: bool):
        """Record whether the case's starting situation is the one its variation describes. It checks nothing itself,
        needs no page and opens none."""
        began = time.time()
        for key, value in (("expected", expected), ("found", found)):
            if not value.strip():
                raise Refused(f"{key} is empty: say it in a few plain words")
            if len(value) > 200:
                raise Refused(f"{key} is longer than 200 characters: say it more briefly")
        if self.record and self._page is not None:
            self._hold(600)
        return "noted", {"expected": expected.strip(), "found": found.strip(), "matches": matches, "t": round(began, 2)}

    def read_tool(self, tool: str, input: dict | None = None):  # noqa: A002
        """While planning: run one declared data tool that says `changes: nothing`, to see what exists. It is not a
        set-up: it is not in the set-up log, needs no reset and counts as nothing else."""
        datatools = importlib.import_module("toolserver.datatools")
        repo = self._need_repo()
        if not self.discovery:
            raise Refused("read_tool is only for planning (a discovery unit)")
        try:
            tools = {t["name"]: t for t in datatools.load(repo)}
        except datatools.DataToolsError as e:
            raise Refused(str(e)) from None
        if tool not in tools:
            raise Refused(f"no data tool named {tool!r} is declared")
        if not tools[tool].get("reads_only"):
            raise Refused(f"{tool} does not say it only reads (changes: nothing), so it can't be called while planning")
        given = {} if input is None else input
        try:
            datatools.check_input(tools[tool], given)
        except ValueError as e:
            raise Refused(str(e)) from None
        made = datatools.run_tool(tools[tool], given, repo, str(self.env["url"]))
        return json.dumps(made), made

    def compare(self, what: str, expected: str, found: str, matches: bool, ref: str = "", claim: str = "", say: str = ""):
        """Record one comparison the verifier made. It checks nothing itself. With a ref, the element's own text is
        read by plain code and, when filming, the element is outlined (green when it matches, red when not)."""
        began = time.time()
        for key, value, most in (("what", what, 100), ("expected", expected, 200), ("found", found, 200)):
            if not value.strip():
                raise Refused(f"{key} is empty: say it in a few plain words")
            if len(value) > most:
                raise Refused(f"{key} is longer than {most} characters: say it more briefly")
        if claim and not _PLAIN_ID.fullmatch(claim):
            raise Refused("claim is the id of a property, such as p1")
        if len(say) > MAX_SAY:
            raise Refused(SAY_REFUSED)
        element, t = None, began
        if ref:
            loc, element = self._element(ref)
            if self.record:
                self._glide(loc, mark=False)
                t = time.time()
                self._outline(loc, color=_MATCH if matches else _MISMATCH)
                self._hold(1500)
        elif self.record and self._page is not None:     # never opens a page just to record a comparison
            self._hold(600)
        data = {"what": what.strip(), "expected": expected.strip(), "found": found.strip(), "matches": matches,
                "element": element, "claim": claim or None, "t": round(t, 2)}
        if say.strip():
            data["say"] = say.strip()         # what the verifier said out loud with it (a walkthrough only)
        return "noted", data

    def _outline(self, loc, color: str) -> None:
        """When filming: draw an outline around the element for about 1.5 s. Never fails the call."""
        try:
            box = loc.bounding_box(timeout=2000)
            if box:
                self._page.evaluate(_OUTLINE_JS, {"box": box, "color": color, "ms": 1500})
        except Exception:  # noqa: BLE001 -- the page moved on: the comparison is still recorded
            pass

    def _hold(self, ms: int) -> None:
        """Keep the film on what is shown for a moment."""
        try:
            self._page.wait_for_timeout(ms)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ api
    def http_request(self, method: str, path: str, body: str = "") -> str:
        """Send one HTTP request to the app; returns status and the first 1500 chars.

        A 4xx or 5xx answer is an observation, not a failure of the tool, so it comes back as a normal result.
        Redirects are not followed (a redirect could lead to another host): the 3xx and its Location are returned."""
        import urllib.error
        from .routes import _no_redirect_opener
        url = self._app_path(path)
        req = urllib.request.Request(url, data=body.encode() if body else None, method=method.upper(),
                                     headers={"Content-Type": "application/json"})
        try:
            with _no_redirect_opener().open(req, timeout=30) as r:
                return f"{r.status}\n{r.read().decode(errors='replace')[:1500]}"
        except urllib.error.HTTPError as e:
            where = f"Location: {e.headers.get('Location')}\n" if e.headers.get("Location") else ""
            return f"{e.code}\n{where}{e.read().decode(errors='replace')[:1500]}"

    # ------------------------------------------------------------ runtime: measure the process
    @staticmethod
    def _run(cmd, timeout=20):
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout

    def _pids(self) -> list[int]:
        """The registered pid and all its descendants."""
        root = self._need_pid()
        kids, seen, todo = {}, [], [root]
        for line in self._run(["ps", "-axo", "pid=,ppid="]).splitlines():
            parts = line.split()
            if len(parts) == 2:
                kids.setdefault(int(parts[1]), []).append(int(parts[0]))
        while todo:
            p = todo.pop()
            if p in seen:
                continue
            seen.append(p)
            todo += kids.get(p, [])
        return seen

    def process_info(self) -> str:
        """The dev server's process tree: pid, parent, CPU %, memory, command."""
        out = self._run(["ps", "-o", "pid=,ppid=,%cpu=,rss=,command=", "-p", ",".join(map(str, self._pids()))])
        return out.strip()

    def gpu_check(self) -> str:
        """Whether any dev-server process has the Apple GPU driver mapped (also catches past use)."""
        lines = []
        for pid in self._pids():
            vm = self._run(["vmmap", str(pid)], timeout=30)
            if "REGION TYPE" not in vm and "Virtual Memory Map" not in vm:
                raise RuntimeError(f"could not read the memory map of pid {pid}, so the GPU check proves nothing")
            drivers = sorted(set(re.findall(r"(AGX\w+|IOGPU\w*)", vm)))
            lines.append(f"pid {pid}: GPU driver {'LOADED: ' + ', '.join(drivers[:4]) if drivers else 'not loaded'}")
        return "\n".join(lines)

    def cpu_sample(self, seconds: int = 3) -> str:
        """Sample the dev server's CPU % once a second for a few seconds (max 10)."""
        pids = ",".join(map(str, self._pids()))
        rows = []
        for _ in range(max(1, min(seconds, 10))):
            rows.append(self._run(["ps", "-o", "pid=,%cpu=", "-p", pids]).strip().replace("\n", " | "))
            time.sleep(1)
        return "\n".join(rows)

    def net_connections(self) -> str:
        """Open network connections of the dev server's processes (listening and outbound)."""
        out = self._run(["lsof", "-nP", "-a", "-i", "-p", ",".join(map(str, self._pids()))])
        return out.strip() or "(none)"

    # ------------------------------------------------------------ code: read-only source access
    def _walk(self):
        repo = self._need_repo()
        return [f for f in sorted(repo.rglob("*")) if self._visible(f)]

    def list_files(self) -> str:
        """Every source file in the repo (relative paths)."""
        return "\n".join(str(f.relative_to(self.repo)) for f in self._walk())

    def find(self, pattern: str) -> str:
        """Where a regex matches in the repo: path:line for every match, and no source text."""
        self._need_repo()
        rx, hits, per_file = re.compile(pattern), [], {}
        for f in self._walk():
            rel = str(f.relative_to(self.repo))
            for i, line in enumerate(f.read_text(errors="ignore").splitlines(), 1):
                if rx.search(line):
                    per_file[rel] = per_file.get(rel, 0) + 1
                    if len(hits) < 300:
                        hits.append(f"{rel}:{i}")
        total = sum(per_file.values())
        if not total:
            return "0 matches"
        head = f"{total} matches in {len(per_file)} files"
        if total > len(hits):
            head += f" (first {len(hits)} locations shown; per file: " + ", ".join(f"{k} {v}" for k, v in sorted(per_file.items())[:40]) + ")"
        return head + "\n" + "\n".join(hits)

    def trace(self, op: str, pattern: str | None = None, path: str | None = None, line: int | None = None,
              name: str | None = None, text: str | None = None):
        """The plain-code half of tracing ways in: complete searches and chain-following over the repo. Only the
        service's own code calls this (no conversation is given it); it judges nothing."""
        repo = self._need_repo()
        tr = importlib.import_module("toolserver.trace")
        if path is not None:
            self._readable(path)                       # same limits as any other read: inside the repo, not hidden
        if op == "search":
            data = tr.search(repo, pattern)
            out = f"{data['total']} hits" + (" (truncated)" if data["truncated"] else "")
        elif op == "context":
            data = tr.context(repo, path, line)
            out = "\n".join(f"{data['start'] + i:4} {s}" for i, s in enumerate(data["lines"]))
        elif op == "enclosing":
            data = tr.enclosing(repo, path, line)
            out = f"{data.get('kind')} {data.get('name')}"
        elif op == "pages":
            data = {"pages": tr.pages(repo)}
            out = f"{len(data['pages'])} pages"
        elif op == "climb":
            data = tr.climb(repo, path)
            out = f"{len(data['chains'])} chains to {len(data['pages'])} pages"
        elif op == "computed_addresses":
            data = {"hits": tr.computed_addresses(repo)}
            out = f"{len(data['hits'])} places build an address from parts"
        elif op == "looks_like_write":
            data = {"write": bool(tr.looks_like_write(text or "", name or ""))}
            out = str(data["write"])
        elif op == "route_of":
            data = {"routes": self._route_of(tr, repo, path)}
            out = ", ".join(f"{r['method']} {r['path']}" for r in data["routes"]) or "not a route file"
        else:
            raise Refused(f"unknown trace op {op!r}")
        return out, data

    @staticmethod
    def _route_of(tr, repo, path):
        """The API routes a file serves. Next.js: a `route.*` file under app/ serves the address its folder spells,
        for each HTTP method it exports. Any other file: the app-relative addresses it mentions (candidates)."""
        parts = Path(path).parts
        methods = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
        if Path(path).stem == "route" and ("app" in parts):
            segs = []
            for seg in parts[parts.index("app") + 1:-1]:
                if seg.startswith("(") or seg.startswith("@"):
                    continue
                segs.append(":" + seg.strip("[].") + ("*" if "..." in seg else "") if seg.startswith("[") else seg)
            url = "/" + "/".join(segs)
            found = [m for m in tr.exported_names(repo, path) if m in methods]
            return [{"method": m, "path": url, "file": path, "certain": True} for m in (found or ["ANY"])]
        return [{"method": "ANY", "path": a["address"], "file": path, "line": a["line"], "certain": False}
                for a in tr.address_literals(repo, path)]

    def grep(self, pattern: str) -> str:
        """Search the repo with a regex; returns at most 15 matching lines as path:line: text, then only counts."""
        self._need_repo()
        rx, hits, more, files = re.compile(pattern), [], 0, set()
        for f in self._walk():
            for i, line in enumerate(f.read_text(errors="ignore").splitlines(), 1):
                if rx.search(line):
                    if len(hits) < MAX_LINES:
                        hits.append(f"{f.relative_to(self.repo)}:{i}: {line.strip()[:160]}")
                    else:
                        more += 1
                        files.add(str(f.relative_to(self.repo)))
        if more:
            hits.append(f"(+{more} more matching lines not shown, in: {', '.join(sorted(files)[:10])}; narrow the pattern)")
        return "\n".join(hits) or "(no matches)"

    def read_lines(self, path: str, start: int, end: int) -> str:
        """Read lines start..end (inclusive, at most 15) of one repo file, numbered."""
        if end - start + 1 > MAX_LINES:
            raise Refused(f"at most {MAX_LINES} lines per read; narrow the range")
        lines = self._readable(path).read_text(errors="replace").splitlines()
        return "\n".join(f"{i:4} {lines[i - 1]}" for i in range(max(1, start), min(end, len(lines)) + 1))

    # ------------------------------------------------------------ sweeps
    def _list_routes(self) -> dict:
        routes = importlib.import_module("toolserver.routes")
        return routes.list_routes(self.repo, self.app_url, self.env.get("routes"))

    def _save_check(self, result: dict) -> str:
        self._checks += 1
        cid = f"check-{self._checks}"
        result["id"] = cid
        (self.out / f"{cid}.json").write_text(json.dumps(_json_safe(result), indent=2))
        return cid

    def list_routes(self):
        """Every route the app serves, from the registered sources."""
        routes = importlib.import_module("toolserver.routes")
        result = self._list_routes()
        return routes.format_routes(result), result

    def probe_each(self, expect_status, methods: list | None = None, path_prefix: str | None = None,
                   headers: dict | None = None, body=None):
        """Send one request to every listed route and check each status against expect_status."""
        foreach = importlib.import_module("toolserver.foreach")
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
            headers = {"Content-Type": "application/json", **(headers or {})}
        listing = self._list_routes()
        result = foreach.probe_each(self.app_url, listing.get("routes", []), methods=methods,
                                    path_prefix=path_prefix, headers=headers, body=body,
                                    expect_status=expect_status)
        result["sources"] = listing.get("sources")
        result["agree"] = listing.get("agree")
        cid = self._save_check(result)
        return f"{cid}\n" + foreach.format_result(result), result

    def match_each(self, glob: str, must_contain: str | None = None, must_not_contain: str | None = None):
        """Check every repo file matching glob for text it must / must not contain."""
        repo = self._need_repo()
        if glob.startswith(("/", "\\", "~")) or re.match(r"^[A-Za-z]:", glob) or ".." in re.split(r"[\\/]", glob):
            raise Refused("the glob must be repo-relative and must not contain '..'")
        foreach = importlib.import_module("toolserver.foreach")
        result = foreach.match_each(repo, glob, must_contain=must_contain, must_not_contain=must_not_contain)
        cid = self._save_check(result)
        return f"{cid}\n" + foreach.format_result(result), result

    # ------------------------------------------------------------ data
    def reset_data(self) -> str:
        """Run the registered reset command, then reload the open page."""
        cmd = self.env.get("reset")
        if not cmd:
            raise Refused("no reset command is registered")
        r = subprocess.run(cmd, shell=True, cwd=str(self.repo) if self.repo else None, timeout=120,
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"reset failed (exit {r.returncode}): {((r.stdout or '') + (r.stderr or ''))[-300:]}")
        if self._page is not None:
            self._page.reload()
        self.was_reset = True
        return "reset done"

    def data_tools(self):
        """The data tools this project declares: each one's name, what it does and the inputs it takes. Never the
        command it runs."""
        datatools = importlib.import_module("toolserver.datatools")
        if self.repo is None:
            return "no repo is registered, so there are no data tools", []
        try:
            tools = datatools.public(datatools.load(self.repo))
        except datatools.DataToolsError as e:
            raise RuntimeError(str(e)) from None
        if not tools:
            return "this project declares no data tools", []
        lines = []
        for t in tools:
            ins = ", ".join(f"{k} ({v['type']}{', optional' if v['optional'] else ''})" + (f": {v['does']}" if v["does"] else "")
                            for k, v in t["takes"].items())
            lines.append(f"{t['name']}: {t['does']} Takes: {ins or 'nothing'}.")
        return "\n".join(lines), tools

    @staticmethod
    def _reads_only(datatools, repo, tool) -> bool:
        """Whether the named data tool is declared and says `changes: nothing`. False when the file can't be read
        (set_up then refuses as it always has)."""
        try:
            return any(t["name"] == tool and t.get("reads_only") for t in datatools.load(repo))
        except datatools.DataToolsError:
            return False

    def set_up(self, tool: str, input: dict | None = None):  # noqa: A002 -- the name the design uses
        """Run one declared data tool before a case starts, with the given inputs, and return what it made."""
        datatools = importlib.import_module("toolserver.datatools")
        repo = self._need_repo()
        if self.discovery:
            raise Refused("set_up is not allowed while finding cases (a discovery unit only looks)")
        if self.other_call is not None:
            raise Refused(f"set_up must come before anything else in a case, and this case already ran {self.other_call}")
        if self.set_ups >= 6:
            raise Refused("this case has already made 6 set_up calls, the most allowed")
        if not (self.env.get("reset") and self.was_reset) and not self._reads_only(datatools, repo, tool):
            # A tool that may change data needs this unit's own reset first. One that only reads does not.
            if not self.env.get("reset"):
                raise Refused("no reset command is registered. A case that sets up data must start from clean data, "
                              "or one case's data would leak into the next")
            raise Refused("set_up needs clean data, and this case was opened without running the reset")
        try:
            tools = {t["name"]: t for t in datatools.load(repo)}
        except datatools.DataToolsError as e:
            raise Refused(str(e)) from None
        if tool not in tools:
            raise Refused(f"no data tool named {tool!r} is declared" + (f" (declared: {', '.join(tools)})" if tools else ""))
        given = {} if input is None else input
        try:
            datatools.check_input(tools[tool], given)
        except ValueError as e:
            raise Refused(str(e)) from None
        made = datatools.run_tool(tools[tool], given, repo, str(self.env["url"]))
        return f"set up with {tool}; it returned {json.dumps(made)}", made

    # ------------------------------------------------------------ finishing
    def finish(self, report: dict) -> dict:
        """Make the hosted side's final report trustworthy (real lines, real files) and write report.json."""
        notes: list[str] = []
        if not isinstance(report, dict):
            notes.append("The report was not an object, so it was replaced with an empty one.")
            report = {}
        out = dict(report)

        excerpts = report.get("excerpts") or []
        if not isinstance(excerpts, list):
            notes.append("The excerpts were not a list, so they were dropped.")
            excerpts = []
        if len(excerpts) > MAX_EXCERPTS:
            notes.append(f"Only the first {MAX_EXCERPTS} of {len(excerpts)} excerpts were kept.")
        kept = []
        for ex in excerpts[:MAX_EXCERPTS]:
            try:
                kept.append(self._excerpt(ex))
            except (Refused, Exception) as e:  # noqa: BLE001
                label = ex.get("path") if isinstance(ex, dict) else repr(ex)
                notes.append(f"An excerpt from {label} was dropped because {e}.")
        out["excerpts"] = kept

        shots = report.get("screenshots") or []
        if not isinstance(shots, list):
            notes.append("The screenshots were not a list, so they were dropped.")
            shots = []
        good_shots = []
        for sid in shots:
            if isinstance(sid, str) and re.fullmatch(r"shot-\d+", sid) and (self.out / f"{sid}.png").is_file():
                good_shots.append(sid)
            else:
                notes.append(f"Screenshot {sid!r} was dropped because no such screenshot was taken.")
        out["screenshots"] = good_shots

        checks = report.get("checks") or []
        if not isinstance(checks, list):
            notes.append("The checks were not a list, so they were dropped.")
            checks = []
        loaded = []
        for cid in checks:
            f = self.out / f"{cid}.json" if isinstance(cid, str) and re.fullmatch(r"check-\d+", cid) else None
            if f is not None and f.is_file():
                try:
                    loaded.append(json.loads(f.read_text()))
                    continue
                except Exception:  # noqa: BLE001
                    pass
            notes.append(f"Check {cid!r} was dropped because no such check was run.")
        out["checks"] = loaded

        # The starting state listed is what this machine was asked to set up and what came of it, never what the report
        # claimed. Only each step's reason is taken from the report, by position: it is the planner's, and nothing
        # here can check it.
        claimed = report.get("starts_from")
        claimed = claimed if isinstance(claimed, list) else []
        if self.set_up_log:
            def reason(i):
                r = claimed[i].get("because") if i < len(claimed) and isinstance(claimed[i], dict) else None
                return r[:300] if isinstance(r, str) else ""
            out["starts_from"] = [{**step, "because": reason(i)} for i, step in enumerate(self.set_up_log)]
            if len(claimed) != len(self.set_up_log):
                notes.append(f"The report listed {len(claimed)} set-up step(s) and {len(self.set_up_log)} were asked for here; those are the ones kept.")
        else:
            if claimed:
                notes.append("The report listed set-up steps, but none ran in this case, so they were dropped.")
            out.pop("starts_from", None)

        clips = self.clips()
        if clips:
            out["clips"] = clips
        else:
            out.pop("clips", None)     # only files this machine made are listed, never what the report claimed
        out["notes"] = notes
        out = _json_safe(out)
        (self.out / "report.json").write_text(json.dumps(out, indent=2))
        return out

    def _excerpt(self, ex) -> dict:
        if not isinstance(ex, dict) or not isinstance(ex.get("path"), str):
            raise ValueError("it has no path")
        if self.repo is None:
            raise ValueError("no repo is registered")
        try:
            s, e = int(ex["start"]), int(ex["end"])
        except Exception:  # noqa: BLE001
            raise ValueError("its start and end are not line numbers") from None
        if e - s + 1 > MAX_LINES:
            raise ValueError(f"it is longer than {MAX_LINES} lines")
        if s < 1 or e < s:
            raise ValueError("its line range is empty or invalid")
        try:
            f = self._readable(ex["path"])
        except Refused as r:
            raise ValueError(str(r)) from None
        lines = f.read_text(errors="replace").splitlines()
        if e > len(lines):
            raise ValueError(f"the file has only {len(lines)} lines")
        return {**ex, "start": s, "end": e, "lines": lines[s - 1:e]}

    def clips(self) -> list:
        """Every film kept in this unit's folder, in order (a second attempt's comes after the first)."""
        found = []
        for f in self.out.glob("clip-*.webm"):
            m = re.fullmatch(r"clip-(\d+)\.webm", f.name)
            if m and f.stat().st_size > 0:
                meta = {}
                try:
                    meta = json.loads((self.out / f"clip-{m.group(1)}.json").read_text())
                except Exception:  # noqa: BLE001
                    pass
                found.append((int(m.group(1)), {"file": f.name, "started": meta.get("started") if isinstance(meta, dict) else None}))
        return [c for _, c in sorted(found, key=lambda x: x[0])]

    # ------------------------------------------------------------ teardown
    def close(self) -> None:
        """Close the browser. A filmed page's clip is only complete once its context closes, so it is kept here, as
        clip-N.webm beside clip-N.json, numbered after any clip already in the folder. Safe to call twice."""
        self._last_frame()
        if self._page is not None:
            try:
                self._page.unroute_all(behavior="ignoreErrors")
            except Exception:  # noqa: BLE001
                pass
        for attr, method in (("_page", "close"), ("_context", "close")):
            obj = getattr(self, attr)
            setattr(self, attr, None)
            if obj is not None:
                try:
                    getattr(obj, method)()
                except Exception:  # noqa: BLE001
                    pass
        self._keep_clip()
        for attr, method in (("_browser", "close"), ("_pw", "stop")):
            obj = getattr(self, attr)
            setattr(self, attr, None)
            if obj is not None:
                try:
                    getattr(obj, method)()
                except Exception:  # noqa: BLE001
                    pass

    def _last_frame(self) -> None:
        """When filming: draw the closing mark just before the film is closed. It makes the page draw one more frame,
        so the film runs to the end of the case (through the last comparison's hold) even when nothing on the page
        changed at the end, and its moment on the receipts' clock is kept to set the film's clock (see _keep_clip)."""
        self._mark_at = None
        if not self.record or self._page is None or self._video is None:
            return
        try:
            before = time.time()
            self._page.evaluate(_LAST_FRAME_JS)
            self._mark_at = (before + time.time()) / 2
            self._page.wait_for_timeout(350)
            self._page.evaluate("() => document.querySelectorAll('groundtruth-tick').forEach((e) => e.remove())")
            self._page.wait_for_timeout(250)          # and one more frame without it, so the film does not end on the mark
        except Exception:  # noqa: BLE001 -- the page is gone: the film ends where it ends
            self._mark_at = None

    def _film_start(self, clip: Path, recorded):
        """The receipts'-clock moment of the film's first frame, found from the closing mark: when it was drawn minus
        where it is in the film. Falls back to the time the page was made when the mark can't be found."""
        mark = getattr(self, "_mark_at", None)
        if mark is None or self._browser is None:
            return recorded, False
        page = None
        try:
            page = self._browser.new_page()
            body = clip.read_bytes()

            def serve(route):           # the film with byte ranges, which the player needs in order to seek
                if not route.request.url.endswith(".webm"):
                    route.fulfill(status=200, content_type="text/html", body='<video src="/clip.webm" muted></video>')
                    return
                m = re.match(r"bytes=(\d+)-(\d*)", route.request.headers.get("range") or "")
                first = int(m.group(1)) if m else 0
                last = min(int(m.group(2)) if m and m.group(2) else len(body) - 1, len(body) - 1)
                route.fulfill(status=206 if m else 200, body=body[first:last + 1],
                              headers={"Content-Type": "video/webm", "Accept-Ranges": "bytes",
                                       **({"Content-Range": f"bytes {first}-{last}/{len(body)}"} if m else {})})
            page.route("http://groundtruth.film/**", serve)
            page.goto("http://groundtruth.film/")
            at = page.evaluate(_FIND_MARK_JS, {"w": self.viewport["width"], "h": self.viewport["height"]})
            if at is None:
                return recorded, False
            start = mark - at
            return (start, True) if abs(start - recorded) < 10 else (recorded, False)
        except Exception:  # noqa: BLE001 -- the film is kept either way, with the page's clock
            return recorded, False
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:  # noqa: BLE001
                    pass

    def _keep_clip(self) -> None:
        video, started = self._video, self.recording_started
        self._video = self.recording_started = None
        tmp = self.out / ".clip-tmp"
        try:
            if video is not None:
                src = Path(video.path())
                if src.is_file() and src.stat().st_size > 0:
                    n = _next_id(self.out, "clip", "webm") + 1
                    shutil.move(str(src), str(self.out / f"clip-{n}.webm"))
                    start, measured = self._film_start(self.out / f"clip-{n}.webm", started)
                    (self.out / f"clip-{n}.json").write_text(json.dumps(
                        {"started": round(start, 2), "page_made": round(started, 2), "clock_from_mark": measured}))
        except Exception:  # noqa: BLE001 -- close() never raises; a clip that could not be kept is simply missing
            pass
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
