"""The verifier's tool list, as the model sees it. Runs on the hosted side, next to the model.

`claude -p` talks to this over MCP. Nothing here touches the app, the process or the repo: every call is handed
to the harness (service/harness.py), which queues it for the user's tool server and waits for the result. The
tool names, descriptions and arguments are defined here, by the service, never by the client.
"""
import asyncio
import base64
import json
import os
import urllib.request

from mcp.server.mcpserver import MCPServer

SERVICE = os.environ["GT_SERVICE"]
RUN, TOKEN, UNIT = os.environ["GT_RUN"], os.environ["GT_TOKEN"], os.environ["GT_UNIT"]
ROLE = os.environ["GT_ROLE"]                         # discovery | case
ALLOWED = set(json.loads(os.environ["GT_TOOLS"]))    # the harness decides which tools this conversation gets

server = MCPServer("gt")


def _post(path, payload):
    req = urllib.request.Request(f"{SERVICE}/internal/runs/{RUN}/{path}", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read())


async def _call(tool, **args):
    args = {k: v for k, v in args.items() if v is not None}
    out = await asyncio.to_thread(_post, "call", {"unit": UNIT, "tool": tool, "args": args})
    return out["text"]


def tool(fn):
    if fn.__name__ in ALLOWED:
        server.tool()(fn)
    return fn


# ---- behaviour: a real browser on the app, and its API
@tool
async def observe() -> str:
    """The current page as an accessibility tree (roles, names, text, values, disabled state). Every element carries a
    reference such as [ref=e12], good until the page changes, that click, fill and read_attribute accept."""
    return await _call("observe")


@tool
async def navigate(path: str) -> str:
    """Go to an app-relative path such as "/" or "/cart"."""
    return await _call("navigate", path=path)


@tool
async def click(ref: str = "", role: str = "", name: str = "") -> str:
    """Click one element. Give ref, the reference observe listed for it (for example e12). Or give its ARIA role and
    accessible name exactly as observe listed them; that only works when exactly one element has them."""
    return await _call("click", **({"ref": ref} if ref else {"role": role, "name": name}))


@tool
async def fill(value: str, ref: str = "", label: str = "") -> str:
    """Replace the text in one field. Give ref, the reference observe listed for it, or its label. The result says how
    many characters were typed, with and without spaces: use those numbers, not your own count."""
    return await _call("fill", value=value, **({"ref": ref} if ref else {"label": label}))


@tool
async def read_attribute(attribute: str, ref: str = "", test_id: str = "") -> str:
    """Read one attribute (e.g. aria-current, disabled, data-shape) of one element: give ref, the reference observe
    listed for it, or its data-testid. Use for state the tree can't show."""
    return await _call("read_attribute", attribute=attribute, **({"ref": ref} if ref else {"test_id": test_id}))


@tool
async def click_and_watch(test_id: str, attribute: str, ms: int = 3000, ref: str = "", role: str = "", name: str = "") -> str:
    """Click an element (by ref from observe, or by role and name), then sample one attribute of another element (by data-testid) every ~40 ms for up to
    10 s. Returns the sequence of distinct values with timestamps, and saves a screenshot the first time the
    value differs from its starting value (cite it). Use for anything that animates or changes over time."""
    return await _call("click_and_watch", test_id=test_id, attribute=attribute, ms=ms, **({"ref": ref} if ref else {"role": role, "name": name}))


@tool
async def wait(ms: int) -> str:
    """Wait up to 10 s for the page to finish something in progress."""
    return await _call("wait", ms=ms)


@tool
async def media() -> str:
    """Every video and audio element on the page: the file it has loaded, its length and position, whether it is
    playing, whether it has loaded, and whether it is visible. The accessibility tree does not show these. It says
    what is loaded, not what the picture looks like."""
    return await _call("media")


@tool
async def screenshot(label: str) -> str:
    """Save a screenshot as evidence; returns its id to cite in the report."""
    return await _call("screenshot", label=label)


@tool
async def hover(ref: str = "", role: str = "", name: str = "") -> str:
    """Point at one element without clicking it, as a person moving the mouse over it would. Give ref, the reference observe listed for it, or its ARIA role and accessible name. Use it for anything that appears on hover (a preview, a tooltip, a menu); observe afterwards to read what appeared."""
    return await _call("hover", **({"ref": ref} if ref else {"role": role, "name": name}))


@tool
async def look(label: str, ref: str = ""):
    """Look at the page as a picture. With no ref, the whole page as it is on screen; with ref (from the latest observe), that one element, larger. Use it for what the page's text cannot show: colour, shape, size, position, whether something is really visible. Returns the picture and its id, which you can cite in the report."""
    out = await asyncio.to_thread(_post, "call", {"unit": UNIT, "tool": "look", "args": {"label": label, **({"ref": ref} if ref else {})}})
    return _with_picture(out)


def _with_picture(out):
    """The text of a look, and the picture itself when the harness passed one on."""
    img = out.get("image")
    if isinstance(img, dict) and img.get("mime") == "image/png" and isinstance(img.get("data"), str):
        from mcp.server.mcpserver import Image
        return [out["text"], Image(data=base64.b64decode(img["data"]), format="png")]
    return out["text"]


COMPARE = ("Record one comparison at the moment you make it. what: the thing compared, in a few plain words. expected: the right "
           "answer and where it comes from. found: what the app shows, as you just read it. matches: whether they agree. ref: the "
           "element that shows it (from the latest observe), which is outlined in the film. It checks nothing itself: it is how a "
           "person watching the film follows your check.")
WALKTHROUGH_CLAIM = " claim: the id written in brackets at the start of the stop this comparison belongs to."
WALKTHROUGH_SAY = " say: what you would say out loud at that moment, at most 110 characters: what you saw and whether it works."
SAY = ("Say one thing out loud to the person watching the film, before you check a stop: what this part of the app should "
       "do, given the data that was put in. One or two short spoken sentences, at most 110 characters. claim: the id in "
       "brackets at the start of the stop you are on. ref: something on screen to point at while you say it.")

if UNIT.startswith("walkthrough/"):
    # In the walkthrough only, each comparison says which claim's stop it belongs to and what was said with it, and
    # each stop starts with a spoken line.
    async def compare(what: str, expected: str, found: str, matches: bool, claim: str, say: str, ref: str = "") -> str:
        return await _call("compare", what=what, expected=expected, found=found, matches=matches, claim=claim, say=say,
                           **({"ref": ref} if ref else {}))
    compare.__doc__ = COMPARE + WALKTHROUGH_CLAIM + WALKTHROUGH_SAY

    async def say(text: str, claim: str, ref: str = "") -> str:
        return await _call("say", text=text, claim=claim, **({"ref": ref} if ref else {}))
    say.__doc__ = SAY
    tool(say)
else:
    async def compare(what: str, expected: str, found: str, matches: bool, ref: str = "") -> str:
        return await _call("compare", what=what, expected=expected, found=found, matches=matches, **({"ref": ref} if ref else {}))
    compare.__doc__ = COMPARE
tool(compare)


@tool
async def situation(expected: str, found: str, matches: bool) -> str:
    """Record, before any comparison, whether the starting situation is the one this variation describes. expected: what the variation's sentence requires of the starting situation. found: the situation you actually have, from what was set up and what the app shows. matches: whether they are the same situation. Each at most 200 characters, in plain words."""
    return await _call("situation", expected=expected, found=found, matches=matches)


@tool
async def read_tool(tool: str, input: dict | None = None) -> str:  # noqa: A002
    """Call one of the project's data tools that is marked "Only reads", to find out what exists before you plan. tool: its name. input: its inputs. At most six calls. It changes nothing."""
    return await _call("read_tool", tool=tool, input=input or {})


@tool
async def http_request(method: str, path: str, body: str = "") -> str:
    """Send one HTTP request to the app (app-relative path); returns the status and the first 1500 characters."""
    return await _call("http_request", method=method, path=path, body=body)


# ---- measurement: the app's running process
@tool
async def process_info() -> str:
    """The app's process tree: pid, parent, CPU %, memory, command."""
    return await _call("process_info")


@tool
async def gpu_check() -> str:
    """Whether any of the app's processes has opened a GPU device. A process that ever opened the GPU keeps the
    driver mapped, so this also catches past use."""
    return await _call("gpu_check")


@tool
async def cpu_sample(seconds: int = 3) -> str:
    """Sample the app's CPU % once a second for a few seconds (max 10)."""
    return await _call("cpu_sample", seconds=seconds)


@tool
async def net_connections() -> str:
    """Open network connections of the app's processes (listening and outbound)."""
    return await _call("net_connections")


# ---- listing: what exists (names only)
@tool
async def list_files() -> str:
    """The repo's source files, as relative paths. Names only."""
    return await _call("list_files")


@tool
async def list_routes() -> str:
    """Every API route the app serves (method and path), with the source the list came from and whether
    independent sources agree."""
    return await _call("list_routes")


# ---- every-item checks
@tool
async def probe_each(expect_status: list[int], methods: list[str] | None = None, path_prefix: str | None = None,
                     headers: dict | None = None, body: str | dict | None = None) -> str:
    """Send the same request to EVERY listed route (optionally only some methods or a path prefix) and compare
    each status with expect_status. Returns a check id, how many were checked and passed, every violator, and every
    route skipped (routes with path parameters are skipped). body is sent as given: pass a JSON object to send
    JSON, or a string to send exactly that text. A route that wrongly accepts the request will carry it out, so
    shape the request to be harmless."""
    return await _call("probe_each", expect_status=expect_status, methods=methods, path_prefix=path_prefix,
                       headers=headers, body=body)


@tool
async def match_each(glob: str, must_contain: str | None = None, must_not_contain: str | None = None) -> str:
    """Check EVERY repo file matching a glob (e.g. "app/api/**/route.ts") against a regex it must contain and/or
    one it must not contain. Returns a check id, the count checked and passed, and every violator."""
    return await _call("match_each", glob=glob, must_contain=must_contain, must_not_contain=must_not_contain)


# ---- source (discovery, and cases of properties about the code itself)
@tool
async def find(pattern: str) -> str:
    """Where a regex matches in the repo: every location as path:line, with no source text. Use it to find every
    call site or every use of a name; it is not cut short the way grep is."""
    return await _call("find", pattern=pattern)


@tool
async def grep(pattern: str) -> str:
    """Search the repo with a regex; returns at most 15 matching lines as path:line: text, then only counts."""
    return await _call("grep", pattern=pattern)


@tool
async def read_lines(path: str, start: int, end: int) -> str:
    """Read lines start..end (inclusive, at most 15) of one repo file, numbered."""
    return await _call("read_lines", path=path, start=start, end=end)


# ---- finishing
if ROLE == "draft":
    @server.tool()
    async def submit_draft(feature: str, goal: str, claims: list[dict], questions: list[dict] | None = None,
                           not_checked: list[str] | None = None) -> str:
        """Finish with the drafted claims. feature: a short name, lowercase words joined by hyphens. goal: one or two
        sentences. claims: each {"id", "title", "your_words", "source": {"kind", "ref", "quote"}, "statement",
        "checked_against", "variations": [{"id", "what"}]}. questions: each {"about": "<claim id or empty>",
        "question"}. not_checked: plain sentences."""
        out = await asyncio.to_thread(_post, "submit", {"unit": UNIT, "report": {
            "feature": feature, "goal": goal, "claims": claims, "questions": questions or [], "not_checked": not_checked or []}})
        return out["text"]
elif ROLE == "anchor":
    @server.tool()
    async def submit_anchor(applies_to: str, action: str, compares: str, kind: str, never: bool, data: str = "",
                            roots: list[dict] | None = None, patterns: list[str] | None = None) -> str:
        """Point at where the trace starts. applies_to, action, compares: the property restated in parts (compares names
        which side is the reference). kind: behaviour | measurement | code. never: true only if the property says
        something must never happen or cannot be done (cannot, never, must not). data: the stored data the action writes (or
        the operation it performs), in plain words. roots: each {"path", "line"}: the line that itself carries out the
        action. patterns: 1 to 3 regular expressions for the NAME of that data (or operation). For kind code, leave
        data, roots and patterns empty."""
        out = await asyncio.to_thread(_post, "submit", {"unit": UNIT, "report": {
            "applies_to": applies_to, "action": action, "compares": compares, "kind": kind, "never": bool(never), "data": data,
            "roots": roots or [], "patterns": patterns or []}})
        return out["text"]
elif ROLE == "planner":
    @server.tool()
    async def submit_cases(wheres: list[dict], cases: list[dict], not_covered: str) -> str:
        """Finish with the cases. wheres: one {"id": "<way-in id from your message>", "where": "<the page and the
        control, in plain words>"} for every way in through the pages. cases: each {"id": "c1", "entry_point":
        "<way-in id>", "what": "<plain steps: where to start, what state to set up, what to do, what to read; nothing
        from the code>", "represents": "<what this case stands for>"}. A case may also carry "starts_from" or "needs"
        (its starting state, as your instructions describe). not_covered: what you left out and why."""
        out = await asyncio.to_thread(_post, "submit", {"unit": UNIT, "report": {"wheres": wheres, "cases": cases, "not_covered": not_covered}})
        return out["text"]
elif ROLE == "discovery":
    @server.tool()
    async def submit_plan(applies_to: str, action: str, compares: str, kind: str, how_found: str,
                          entry_points: list[dict], cases: list[dict], not_covered: str,
                          code_refs: list[dict] | None = None) -> str:
        """Finish discovery with the plan of cases.
        applies_to, action, compares: the property restated in parts (compares names which side is the reference).
        kind: behaviour | measurement | code.
        how_found: what you searched and looked at, and where each entry point came from.
        entry_points: each {"id": "e1", "door": "browser" | "api" | "code", "where": "<the page and the control on
          it, or the route, or the files>", "found_by": "<what in the code or the app showed this is a way in>"}.
        cases: each {"id": "c1", "entry_point": "e1", "what": "<plain steps: where to start, what state to set up,
          what to do, what to read; nothing from the code>", "represents": "<the class of situations this case
          stands for>"}.
        not_covered: what you left out, and why that is safe or not.
        code_refs: each {"path", "line"}: the locations your trace rests on (required when the code can be read).        A case may also carry "starts_from" or "needs" (its starting state, as your instructions describe).
        """
        out = await asyncio.to_thread(_post, "submit", {"unit": UNIT, "report": {
            "applies_to": applies_to, "action": action, "compares": compares, "kind": kind, "how_found": how_found,
            "entry_points": entry_points, "cases": cases, "not_covered": not_covered, "code_refs": code_refs or []}})
        return out["text"]
else:
    @server.tool()
    async def submit_case(outcome: str, observed: str, screenshots: list[str] | None = None, question: str | None = None,
                          draft_if_intended: str | None = None, excerpts: list[dict] | None = None,
                          checks: list[str] | None = None) -> str:
        """Finish the case. outcome: holds | breaks | declined | outside | nothing_there | cant_run. observed: the values you read, in
        order, so a person can check them. screenshots: ids to show. For declined: question (one sentence asking the
        owner whether the refusal is intended) and draft_if_intended (one sentence stating what the app does in that
        situation, as a rule of its own). For a property about the code itself: excerpts, up to 3 items
        {"path", "start", "end", "why"} of at most 15 lines from files you read, and checks, the check ids from
        match_each that the outcome rests on."""
        out = await asyncio.to_thread(_post, "submit", {"unit": UNIT, "report": {
            "outcome": outcome, "observed": observed, "screenshots": screenshots or [], "question": question or "",
            "draft_if_intended": draft_if_intended or "", "excerpts": excerpts or [], "checks": checks or []}})
        return out["text"]


if __name__ == "__main__":
    server.run("stdio")
