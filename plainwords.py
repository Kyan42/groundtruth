"""One translation of a recorded tool call into plain words, for anything a person reads (the dashboard's steps, and
later the HTML report). Input is one receipt line: {"t", "tool", "args", "result"}."""
import re

_MEASURE = {"process_info": "measure the app's process", "gpu_check": "check whether the app's process has opened a GPU",
            "cpu_sample": "sample the app's CPU use", "net_connections": "list the app's network connections"}


def failed(receipt) -> bool:
    return bool(receipt.get("refused")) or str(receipt.get("result", "")).startswith(("error:", "refused:"))


def _short(text, n=70):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def words(receipt) -> str:
    """What was done, in a few plain words. Never a tool name, a reference or a selector."""
    tool, a, res = receipt.get("tool"), receipt.get("args") or {}, str(receipt.get("result", ""))
    a = a if isinstance(a, dict) else {}
    if tool == "navigate":
        return f"open {a.get('path', '')}".strip()
    if tool == "observe":
        return "look at the page"
    if tool == "click":
        if res.startswith("clicked "):
            what = res[len("clicked "):]
            what = re.sub(r'^a "', 'link "', what)            # the page's tag name for a link
            return "click " + _short(what)
        return "try to click " + (_short(f"{a.get('role', '')} \"{a.get('name', '')}\"") if a.get("role") else "a control")
    if tool == "hover":
        if res.startswith("pointed at "):
            return "point at " + _short(re.sub(r'^a "', 'link "', res[len("pointed at "):]))
        return "try to point at " + (_short(f"{a.get('role', '')} \"{a.get('name', '')}\"") if a.get("role") else "a control")
    if tool == "look":
        return ("look at an element as a picture" if a.get("ref") else "look at the page as a picture") + (
            f": {_short(a.get('label'), 50)}" if a.get("label") else "")
    if tool == "compare":
        return _short(a.get("what"), 200) or "compare"
    if tool == "say":
        return _short(a.get("text"), 110) or "say something"
    if tool == "fill":
        m = re.match(r"filled (.+) with (\d+) characters", res)
        value = _short(a.get("value", ""), 50)
        return (f"type \"{value}\" into {_short(m.group(1), 40)}" if m else f"try to type \"{value}\"")
    if tool == "click_and_watch":
        return f"click a control and watch {a.get('attribute', 'an attribute')} change"
    if tool == "read_attribute":
        return f"read {a.get('attribute', 'an attribute')} of an element"
    if tool == "wait":
        ms = a.get("ms") or 0
        return f"wait {ms / 1000:g} seconds"
    if tool == "screenshot":
        return "take a screenshot" + (f": {_short(a.get('label'), 50)}" if a.get("label") else "")
    if tool == "media":
        return "read what each player has loaded"
    if tool == "http_request":
        return f"{a.get('method', 'GET')} {a.get('path', '')}".strip()
    if tool in _MEASURE:
        return _MEASURE[tool]
    if tool == "list_files":
        return "list the files"
    if tool == "list_routes":
        return "list the app's routes"
    if tool == "grep":
        return f"search the code for {_short(a.get('pattern'), 50)}"
    if tool == "find":
        return f"find where {_short(a.get('pattern'), 50)} appears"
    if tool == "read_lines":
        return f"read {a.get('path', '')} lines {a.get('start', '?')} to {a.get('end', '?')}"
    if tool == "match_each":
        return f"check every file matching {a.get('glob', '')}"
    if tool == "reset_data":
        return "reset the app's data"
    return str(tool or "an action").replace("_", " ")


def kind(tool) -> str:
    """action: it did something to the app (pointing at something counts); look: it only read the page or looked at
    a picture of it; compare: a comparison the verifier recorded; request: a direct call; reading: a measurement or
    a player reading; code: it read source; other: bookkeeping."""
    if tool in ("navigate", "click", "fill", "click_and_watch", "wait", "hover"):
        return "action"
    if tool in ("observe", "read_attribute", "screenshot", "look"):
        return "look"
    if tool == "compare":
        return "compare"
    if tool == "say":
        return "say"
    if tool == "http_request":
        return "request"
    if tool in _MEASURE or tool == "media":
        return "reading"
    if tool in ("list_files", "grep", "find", "read_lines", "match_each", "list_routes"):
        return "code"
    return "other"
