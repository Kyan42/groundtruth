"""What the pull request and its issues said when a run started, read with the user's own `gh` login.

    fetch(repo, pr=None, timeout=10) -> {"pull_request", "issues", "fetched_at"} or None

Never interactive, never longer than `timeout` seconds in total, never raises: no `gh`, not logged in, not a git
repo, no pull request for the branch, or anything else that goes wrong gives None. Nothing here is sent to the
verifier service; it is kept with the run so a person can read it beside the results.
"""
import json
import os
import re
import shutil
import subprocess
import time

MAX_BODY = 6000
MAX_ISSUES = 5
CLOSES = re.compile(r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+#(\d+)\b")


def _gh(args, cwd, deadline):
    """One `gh` call, within what is left of the time allowed; the JSON it printed, or None."""
    left = deadline - time.monotonic()
    if left <= 0:
        return None
    env = {**os.environ, "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1", "NO_COLOR": "1"}
    try:
        p = subprocess.run(["gh", *args], cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, timeout=left)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    try:
        return json.loads(p.stdout)
    except ValueError:
        return None


def _text(v, most=MAX_BODY):
    return str(v or "")[:most]


def fetch(repo, pr=None, timeout=10.0):
    try:
        return _fetch(repo, pr, timeout)
    except Exception:  # noqa: BLE001 -- never fails or delays a run
        return None


def _fetch(repo, pr, timeout):
    if shutil.which("gh") is None:
        return None
    deadline = time.monotonic() + timeout
    args = ["pr", "view"] + ([str(pr)] if pr else []) + ["--json", "number,title,body,url,state,closingIssuesReferences"]
    got = _gh(args, repo, deadline)
    if not isinstance(got, dict) or not got.get("number"):
        return None
    pull = {"number": got.get("number"), "title": _text(got.get("title"), 300), "body": _text(got.get("body")),
            "url": _text(got.get("url"), 500), "state": _text(got.get("state"), 20)}
    numbers = []
    for ref in got.get("closingIssuesReferences") or []:
        n = ref.get("number") if isinstance(ref, dict) else None
        if isinstance(n, int) and n not in numbers:
            numbers.append(n)
    for m in CLOSES.finditer(pull["body"]):
        n = int(m.group(1))
        if n not in numbers:
            numbers.append(n)
    issues = []
    for n in numbers[:MAX_ISSUES]:
        one = _gh(["issue", "view", str(n), "--json", "number,title,body,url"], repo, deadline)
        if isinstance(one, dict) and one.get("number"):
            issues.append({"number": one.get("number"), "title": _text(one.get("title"), 300), "body": _text(one.get("body")),
                           "url": _text(one.get("url"), 500)})
    return {"pull_request": pull, "issues": issues, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S")}


def fetch_issue(repo, issue, timeout=10.0):
    """One issue (by number or address), as {"number", "title", "body", "url"}, or None. Same rules as fetch."""
    try:
        if shutil.which("gh") is None:
            return None
        one = _gh(["issue", "view", str(issue), "--json", "number,title,body,url"], repo, time.monotonic() + timeout)
        if not isinstance(one, dict) or not one.get("number"):
            return None
        return {"number": one.get("number"), "title": _text(one.get("title"), 300), "body": _text(one.get("body")),
                "url": _text(one.get("url"), 500)}
    except Exception:  # noqa: BLE001
        return None
