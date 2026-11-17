"""Data tool make_run: put one made-up run into the dashboard's test store and say exactly what is in it.

Run by the verifier's tool server from dashboard/ (see DATATOOLS.md): the inputs arrive as one JSON object on standard
input, GROUNDTRUTH_APP_URL names the store, and one JSON object is printed. Standard library only; Python 3.9.

Inputs: new_tests (0-6), rerun_tests (0-6), variations_each (1-10, default 3), verdicts (comma-separated words, one per
test, New tests first; holds, fails, needs_decision, cant_tell, not_run; tests beyond the list hold), films (default
true). What each verdict word makes:
  holds           every variation held
  fails           exactly one variation broke: the second, or the only one
  needs_decision  exactly one variation was declined with a question: the second, or the only one
  cant_tell       exactly one variation could not run: the second, or the only one
  not_run         no variations at all; the test is marked not run

It writes only to a store whose folder carries the MADE-UP-DATA-ONLY marker, using that folder's own token.
"""
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

from teststore import Refused, checked_folder

HERE = Path(__file__).resolve().parent
FILMS = HERE / "films"
LETTERS = "abcdefghij"
MAX_PRINT = 1900
VERDICTS = ("holds", "fails", "needs_decision", "cant_tell", "not_run")
ALL_VERDICTS = ("holds", "fails", "needs_decision", "cant_tell", "planned", "not_run")
OUTCOMES = ("holds", "breaks", "declined", "outside", "nothing_there", "cant_run", "planned")
SPECIAL = {"fails": "breaks", "needs_decision": "declined", "cant_tell": "cant_run"}
INPUTS = ("new_tests", "rerun_tests", "variations_each", "verdicts", "films")


class Problem(Exception):
    """A plain sentence for the caller."""


# ---------------------------------------------------------------- inputs

def _int(given, key, low, high, default=None):
    if key not in given:
        if default is None:
            raise Problem(f"make_run needs the input {key} (a whole number from {low} to {high}).")
        return default
    v = given[key]
    if not isinstance(v, int) or isinstance(v, bool) or not low <= v <= high:
        raise Problem(f"{key} must be a whole number from {low} to {high}; it was {json.dumps(v)[:40]}.")
    return v


def read_inputs(given):
    if not isinstance(given, dict):
        raise Problem("make_run takes one JSON object, like {\"new_tests\": 2, \"rerun_tests\": 1}.")
    for key in given:
        if key not in INPUTS:
            raise Problem(f"make_run has no input named {str(key)[:40]!r}; it takes {', '.join(INPUTS)}.")
    new = _int(given, "new_tests", 0, 6)
    rerun = _int(given, "rerun_tests", 0, 6)
    each = _int(given, "variations_each", 1, 10, default=3)
    if new + rerun < 1:
        raise Problem("new_tests and rerun_tests are both 0; ask for at least one test.")
    films = given.get("films", True)
    if not isinstance(films, bool):
        raise Problem("films must be true or false.")
    raw = given.get("verdicts", "")
    if not isinstance(raw, str):
        raise Problem("verdicts must be text: words separated by commas, like \"holds,fails\".")
    words = [w.strip() for w in raw.split(",")] if raw.strip() else []
    for w in words:
        if w not in VERDICTS:
            raise Problem(f"verdicts has the word {w[:30]!r}; each word must be one of {', '.join(VERDICTS)}.")
    if len(words) > new + rerun:
        raise Problem(f"verdicts names {len(words)} tests but only {new + rerun} were asked for.")
    words += ["holds"] * (new + rerun - len(words))
    return new, rerun, each, words, films


# ---------------------------------------------------------------- the made-up run

def pool():
    try:
        info = json.loads((FILMS / "films.json").read_text())
    except (OSError, ValueError):
        raise Problem("The film pool is missing (films/films.json next to make_run.py); "
                      "run make_films.py once.") from None
    out = {}
    for letter in LETTERS:
        f = info.get(f"film-{letter}") or {}
        path = FILMS / f"film-{letter}.webm"
        if not path.is_file() or not isinstance(f.get("seconds"), (int, float)):
            raise Problem(f"The film pool is incomplete: film-{letter} is missing; run make_films.py once.")
        out[letter] = {"name": f"film-{letter}", "seconds": float(f["seconds"]), "path": path, "size": path.stat().st_size}
    return out


def _secs(x):
    return f"{x:g}s"


def steps_for(outcome, length):
    """Four plain steps, timed inside the film (or untimed when there is none)."""
    def at(t):
        return None if length is None else round(t, 2)
    clip = None if length is None else 0
    end = 3.0 if length is None else length
    steps = [
        {"clip": clip, "at": at(0.2), "kind": "action", "words": "open /made-up", "ok": True},
        {"clip": clip, "at": at(0.5), "kind": "look", "words": "look at the page", "ok": True},
        {"clip": clip, "at": at(end * 0.5), "kind": "action", "words": "click button \"Made-up button\"", "ok": True},
        {"clip": clip, "at": at(end - 0.5), "kind": "look", "words": "look at the page", "ok": True},
    ]
    if outcome == "cant_run":
        steps[2]["ok"] = False
        steps[2]["problem"] = "made up: the button was not there"
    return steps


OBSERVED = {
    "holds": "Made up: this variation held.",
    "breaks": "Made up: this variation broke.",
    "declined": "Made up: the app refused this variation.",
    "cant_run": "Made up: this variation could not run.",
}


def build(new, rerun, each, words, films, films_pool):
    """The run document, the files to send, and the summary to print (without the store's id yet)."""
    now = time.time()
    run = time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + "-" + secrets.token_hex(3)
    ran_at = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))
    props, files, summary = [], [], []
    for ti in range(new + rerun):
        tid = f"t{ti + 1}"
        word = words[ti]
        group = "new" if ti < new else "regression"
        sentence = f"Made-up test {ti + 1}"
        cases, lines = [], []
        if word != "not_run":
            special = 1 if each > 1 else 0          # the second variation, or the only one
            for j in range(each):
                cid = f"c{j + 1}"
                outcome = SPECIAL[word] if word in SPECIAL and j == special else "holds"
                film = films_pool[LETTERS[(ti + j) % len(LETTERS)]] if films else None
                clips = []
                if film:
                    path = f"{tid}/{cid}/{film['name']}.webm"
                    clips = [{"file": path, "started": round(now, 2)}]
                    files.append({"path": path, "kind": "clip", "size": film["size"], "local": film["path"]})
                c = {
                    "id": cid, "way_in": None, "door": "browser",
                    "what": f"Made-up steps for variation {j + 1} of test {ti + 1}.",
                    "stands_for": f"Made-up variation {j + 1} of test {ti + 1}.",
                    "outcome": outcome, "observed": OBSERVED[outcome],
                    "question": None, "draft_if_intended": None,
                    "seconds": film["seconds"] if film else 3.0, "attempts": 1,
                    "clips": clips, "shots": [],
                    "steps": steps_for(outcome, film["seconds"] if film else None),
                    "requests": [], "readings": [], "media": [], "excerpts": [], "checks": [], "notes": [],
                    "starts_from": [], "data_request": None,
                }
                if outcome == "declined":
                    c["question"] = f"Made up: is it intended that variation {j + 1} of test {ti + 1} is refused?"
                    c["draft_if_intended"] = f"Made-up test {ti + 1} refuses variation {j + 1}."
                cases.append(c)
                lines.append(f"{cid} {outcome} " + (f"{film['name']} {_secs(film['seconds'])}" if film else "no film"))
        counts = {o: sum(c["outcome"] == o for c in cases) for o in OUTCOMES}
        props.append({
            "id": tid, "statement": sentence, "example": "", "group": group, "verdict": word,
            "kind": None if word == "not_run" else "behaviour", "searched": False,
            "read_as": {"applies_to": "", "action": "", "compares": ""},
            "how_found": "", "not_covered": "", "no_plan_because": None, "search_notes": [],
            "ways_in": [], "left_out": [], "counts": counts,
            "seconds": round(sum(c["seconds"] for c in cases), 2), "cost_usd": 0, "cases": cases,
        })
        summary.append({"id": tid, "tab": "New" if group == "new" else "Regression", "sentence": sentence,
                        "verdict": word, "variations": lines})
    doc = {
        "schema": 1, "project": "made-up", "feature": "made-up",
        "goal": "A made-up run for checking the dashboard; nothing in it was really tested.",
        "run": run, "ran_at": ran_at,
        "ran_against": {"url": "http://made-up.test", "commit": "made-up", "uncommitted_changes": False,
                        "platform": "made up", "process_started": None, "registered_at": ran_at,
                        "could_measure": False, "could_read_code": False, "had_reset": True},
        "verifier": {"version": "made-up", "model": "none"},
        "error": None, "aborted": False,
        "totals": {
            "properties": {v: sum(p["verdict"] == v for p in props) for v in ALL_VERDICTS},
            "cases": {o: sum(p["counts"][o] for p in props) for o in OUTCOMES},
            "seconds": round(sum(p["seconds"] for p in props), 2), "cost_usd": 0,
        },
        "properties": props,
    }
    return doc, files, summary


def shorter(summary):
    """The same tests with each one's variations in one phrase, for a run too big to list every variation and film:
    how many there are and which one did not hold."""
    out = []
    for t in summary:
        lines = t["variations"]
        odd = [" ".join(line.split()[:2]) for line in lines if line.split()[1] != "holds"]
        said = "none" if not lines else f"{len(lines)}, " + ("all hold" if not odd else ", ".join(odd) + (", the rest hold" if len(lines) > len(odd) else ""))
        out.append({**t, "variations": said})
    return out


def answer(run_id, app_url, summary):
    """One JSON object within the limit: every variation with its film when that fits, the shorter form when not."""
    page = f"/run.html?run={run_id}"
    full = json.dumps({"run_id": run_id, "page": page, "url": app_url + page, "tests": summary}, separators=(",", ":"))
    if len(full) <= MAX_PRINT:
        return full
    return json.dumps({"run_id": run_id, "page": page, "url": app_url + page, "tests": shorter(summary),
                       "note": "Too big to list each variation's film; ask for fewer tests or variations to get them."},
                      separators=(",", ":"))


# ---------------------------------------------------------------- talking to the store

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_open = urllib.request.build_opener(_NoRedirect).open


def call(base, method, path, token, data=None, ctype="application/json"):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": ctype, "User-Agent": "groundtruth-made-up/1"}
    if data is not None:
        headers["Content-Length"] = str(len(data))
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with _open(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        try:
            said = json.loads(e.read()).get("error") or e.reason
        except Exception:  # noqa: BLE001
            said = e.reason
        raise Problem(f"The store at {base} answered {e.code} to {method} {path.split('/files/')[0]}: {said}") from None
    except (urllib.error.URLError, OSError) as e:
        raise Problem(f"Could not reach the store at {base} ({getattr(e, 'reason', e)}).") from None
    except ValueError:
        raise Problem(f"The address {base} did not answer like the results store.") from None


def upload(base, token, doc, files):
    listed = [{"path": f["path"], "kind": f["kind"], "size": f["size"]} for f in files]
    made = call(base, "POST", "/v1/runs", token, json.dumps({"document": doc, "files": listed}).encode())
    rid = made.get("id")
    if not isinstance(rid, str) or not rid:
        raise Problem(f"The store at {base} did not give the run an id.")
    by_path = {f["path"]: f for f in files}
    cache = {}
    for path in made.get("missing") or []:
        f = by_path.get(path)
        if f is None:
            raise Problem(f"The store asked for {path}, which this run did not declare.")
        if f["local"] not in cache:
            cache[f["local"]] = f["local"].read_bytes()
        call(base, "PUT", f"/v1/runs/{quote(rid)}/files/{quote(path)}", token, cache[f["local"]], "application/octet-stream")
    call(base, "POST", f"/v1/runs/{quote(rid)}/complete", token, b"{}")
    return rid


def main():
    try:
        app_url = (os.environ.get("GROUNDTRUTH_APP_URL") or "").strip().rstrip("/")
        if not app_url:
            raise Problem("GROUNDTRUTH_APP_URL is not set, so there is no store to put the run in.")
        if not app_url.startswith(("http://", "https://")):
            raise Problem("GROUNDTRUTH_APP_URL must be an http:// or https:// address.")
        try:
            d = checked_folder()
        except Refused as e:
            raise Problem(str(e)) from None
        try:
            token = (d / "token").read_text().strip()
        except OSError:
            token = ""
        if not token:
            raise Problem(f"The test store in {d} has no token yet; start the store on that folder once.")
        try:
            given = json.loads(sys.stdin.read() or "null")
        except ValueError:
            raise Problem("The input is not JSON; send one JSON object on standard input.") from None
        new, rerun, each, words, films = read_inputs(given)
        doc, files, summary = build(new, rerun, each, words, films, pool() if films else {})
        if len(answer("0" * 16, app_url, summary)) > MAX_PRINT:     # the store's ids are 16 characters
            raise Problem(f"That shape ({new + rerun} tests with {each} variations each) is too big to describe in "
                          f"{MAX_PRINT} characters; ask for fewer tests or fewer variations.")
        rid = upload(app_url, token, doc, files)
        out = answer(rid, app_url, summary)
        if len(out) > MAX_PRINT:
            raise Problem(f"The description came to {len(out)} characters, more than {MAX_PRINT}; ask for fewer tests or variations.")
    except Problem as e:
        print(str(e)[:290], file=sys.stderr)
        sys.exit(1)
    print(out)


if __name__ == "__main__":
    main()
