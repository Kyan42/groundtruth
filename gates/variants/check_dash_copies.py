"""Prove each planted bug by hand, with no model: every dashboard copy, a few made-up runs, what the page shows.

    .venv/bin/python gates/variants/check_dash_copies.py          (after build_dash_copies.py)

For each copy in gates/work (dash-clean, dash-regression-short, dash-single-holds, dash-other-test-film): a results
store is started in this process on a free port, serving that copy's page folder with that copy's test-store folder;
the copy's own make_run is run the way the tool server runs it (toolserver.datatools.run_tool: from the copy's folder,
JSON on standard input, GROUNDTRUTH_APP_URL set) for each SHAPE; the run page is opened in Chromium (Playwright) and
what is on screen is compared with what the tool returned:

  tabs   the New and Regression counts on the switch, and the cards (or the empty message) in each tab;
  cards  the result word on each test's card in the sidebar, and the "Properties: H of N hold" and "Cases: X of Y
         held" lines in the "This run" box below them;
  films  for every variation (opened by its address, #tN/cJ): the main player's src and the film's length.

The expected screen is the tool's record, except where the copy's bug is able to show for that shape (the same
"able to show" tests the gate uses, gates/props_lib.py able_*): there the expected screen is the specific wrong one.
One line per assertion; exit 1 on any failure. Every store is stopped and every copy's test store reset at the end.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
os.environ.pop("GT_TEST_STORE_DIR", None)        # each copy's tools must use the copy's own test store

from gates import props_lib as L  # noqa: E402
from store.server import start_in_background  # noqa: E402
from toolserver import datatools  # noqa: E402

WORK = REPO / "gates" / "work"
COPIES = {"dash-clean": None, "dash-regression-short": L.able_regression_short,
          "dash-single-holds": L.able_single_holds, "dash-other-test-film": L.able_other_test_film}
SHAPES = {
    "S1": {"new_tests": 2, "rerun_tests": 2, "variations_each": 1, "verdicts": "fails,holds,holds,fails"},
    "S2": {"new_tests": 1, "rerun_tests": 0, "variations_each": 3, "verdicts": "fails"},
    "S3": {"new_tests": 1, "rerun_tests": 1, "variations_each": 2, "verdicts": "holds,needs_decision", "films": False},
    "S4": {"new_tests": 2, "rerun_tests": 0, "variations_each": 1, "verdicts": "needs_decision,cant_tell"},
    "S5": {"new_tests": 0, "rerun_tests": 1, "variations_each": 1},
    "S6": {"new_tests": 1, "rerun_tests": 2, "variations_each": 2, "verdicts": "not_run"},
}
SHORT = {"holds": "Holds", "fails": "Fails", "needs_decision": "Your call", "cant_tell": "Can't tell", "not_run": "Not run in this run"}
EMPTY = {"New": "This run has no new properties.", "Regression": "No earlier properties were re-checked in this run."}
LINE = re.compile(r"^(c\d+) (\S+) (?:(film-[a-j]) ([\d.]+)s|no film)$")
TOLERANCE = 0.15            # seconds between the film's length in the page and in the tool's record

results = []


def say(ok: bool, copy: str, shape: str, what: str, detail: str):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {copy}  {shape}  {what}  {detail}", flush=True)


# ---------------------------------------------------------------- what the page should show
def parse(record: dict) -> list[dict]:
    """The tool's tests: id, tab, verdict, name, variations [{id, film, seconds}] (film None when it has none)."""
    tests = []
    for t in record["tests"]:
        vs = []
        for line in t["variations"]:
            m = LINE.match(line)
            if not m:
                raise RuntimeError(f"cannot read the variation line {line!r}")
            vs.append({"id": m.group(1), "film": m.group(3), "seconds": float(m.group(4)) if m.group(4) else None})
        tests.append({"id": t["id"], "tab": t["tab"], "verdict": t["verdict"], "name": t["sentence"], "variations": vs,
                      "outcomes": [LINE.match(line).group(2) for line in t["variations"]]})
    return tests


def expected(copy: str, tests: list[dict], showing: bool) -> dict:
    """{"tabs": {tab: [card label]}, "words": {test id: word}, "films": {(test, variation): (path, seconds) | None},
    "box": {"Properties": the line in the sidebar's "This run" box}}."""
    shown = list(tests)
    if copy == "dash-regression-short" and showing:
        last = [t for t in tests if t["tab"] == "Regression"][-1]
        shown = [t for t in tests if t is not last]
    tabs = {tab: [f"{t['id'].upper()}: {t['name']}" for t in shown if t["tab"] == tab] for tab in ("New", "Regression")}
    words = {}
    for t in shown:
        w = SHORT[t["verdict"]]
        if copy == "dash-single-holds" and showing and t["verdict"] == "fails" and len(t["variations"]) == 1:
            w = SHORT["holds"]
        words[t["id"]] = w
    films = {}
    with_vars = [t for t in shown if t["variations"]]
    for t in with_vars:
        for v in t["variations"]:
            src = t
            if copy == "dash-other-test-film" and showing and t is not with_vars[0]:
                src = with_vars[0]
            sv = next(x for x in src["variations"] if x["id"] == v["id"])
            films[(t["id"], v["id"])] = (f"{src['id']}/{v['id']}/{sv['film']}.webm", sv["seconds"]) if sv["film"] else None
    held = sum(t["verdict"] == "holds" for t in shown)
    variations = [o for t in shown for o in t["outcomes"]]
    return {"tabs": tabs, "words": words, "films": films,
            "box": {"Properties": f"{held} of {len(shown)} hold",
                    "Cases": f"{variations.count('holds')} of {len(variations)} held"}}


# ---------------------------------------------------------------- what the page shows
MAIN_FILM = """async () => {
  const v = document.querySelector('main .stage video');
  if (!v) return null;
  if (v.readyState < 1) await new Promise((r) => { v.addEventListener('loadedmetadata', r, {once: true}); v.addEventListener('error', r, {once: true}); });
  let d = v.duration;
  const time = (document.querySelector('main .ctrl .time') || {}).textContent || '';
  if (!isFinite(d)) {
    await new Promise((r) => { v.addEventListener('durationchange', r, {once: true}); v.currentTime = 1e9; });
    d = v.duration;
  }
  return {src: new URL(v.currentSrc || v.src).pathname, seconds: d, error: v.error ? v.error.code : null, time};
}"""


def read_tab(page, tab: str) -> tuple[int, list[str], dict, str]:
    """(count on the switch, card labels, {test id: word}, empty message) after clicking the tab."""
    btn = page.locator(".switch button", has_text=tab)
    count = int(btn.locator("i").inner_text())
    btn.click()
    page.wait_for_function("t => document.querySelector('.switch button[aria-pressed=true]').textContent.startsWith(t)", arg=tab)
    cards = page.locator(".cards .pcard")
    labels, words = [], {}
    for i in range(cards.count()):
        c = cards.nth(i)
        labels.append(c.get_attribute("aria-label"))
        word = c.locator(".v, .notrun-line").first.inner_text().strip()
        words[c.get_attribute("data-pid")] = word
    note = page.locator(".cards .empty-note")
    return count, labels, words, (note.inner_text().strip() if note.count() else "")


def read_film(page, tid: str, cid: str):
    page.evaluate("h => { location.hash = h; }", f"#{tid}/{cid}")
    page.wait_for_function("t => document.title.startsWith(t)", arg=f"{tid.upper()} {cid.upper()} ")
    return page.evaluate(MAIN_FILM)


# ---------------------------------------------------------------- one copy
def check_shape(page, copy: str, shape: str, inp: dict, rec: dict, able):
    tests = parse(rec)
    showing = bool(able and able(inp))
    print(f"----  {copy}  {shape}  make_run {inp}; run {rec['run_id']}; bug able to show: "
          f"{'n/a' if able is None else 'yes' if showing else 'no'}", flush=True)
    want = expected(copy, tests, showing)
    right = expected("dash-clean", tests, False)

    def tag(kind, key):         # marks an assertion whose expected screen is the planted wrong one
        return " [the bug, visible]" if want[kind].get(key) != right[kind].get(key) else ""
    page.goto(rec["url"])
    page.wait_for_selector(".switch button")
    seen_words = {}
    for tab in ("New", "Regression"):
        count, labels, words, note = read_tab(page, tab)
        seen_words.update(words)
        w = want["tabs"][tab]
        ok = count == len(w) and labels == w and (bool(w) or note == EMPTY[tab])
        say(ok, copy, shape, f"tab {tab}{tag('tabs', tab)}", f"page: count {count}, cards {labels or note!r}; "
            f"expected: count {len(w)}, cards {w or EMPTY[tab]!r}")
    for key, w in want["box"].items():
        got = page.locator("section.run .kv", has_text=key).locator("b").inner_text().strip()
        say(got == w, copy, shape, f"run box {key}{tag('box', key)}", f"page '{key}: {got}'; expected '{key}: {w}'")
    for tid, w in want["words"].items():
        t = next(x for x in tests if x["id"] == tid)
        got = seen_words.get(tid)
        say(got == w, copy, shape, f"card {tid.upper()}{tag('words', tid)}", f"page word {got!r}; expected {w!r} "
            f"(recorded verdict {t['verdict']}, {len(t['variations'])} variation(s))")
    for (tid, cid), w in want["films"].items():
        got = read_film(page, tid, cid)
        own = next(v for v in next(t for t in tests if t["id"] == tid)["variations"] if v["id"] == cid)
        rec_says = f"{own['film']} {own['seconds']}s" if own["film"] else "no film"
        if w is None:
            say(got is None, copy, shape, f"film {tid}/{cid}", f"page: {'no main player' if got is None else got}; "
                f"expected no main player (record: {rec_says})")
            continue
        path, secs = w
        ok = (got is not None and got["error"] is None and got["src"].endswith("/files/" + path)
              and abs(got["seconds"] - secs) <= TOLERANCE)
        say(ok, copy, shape, f"film {tid}/{cid}{tag('films', (tid, cid))}",
            (f"page: src {got['src']}, length {got['seconds']:.2f}s, time {got['time']!r}" if got else "page: no main player")
            + f"; expected src .../files/{path}, length {secs}s (record: {rec_says})")


def check_copy(pw_browser, copy: str, able):
    app = WORK / copy
    tool = datatools.load(app)[0]
    srv, url = start_in_background(app / ".groundtruth" / "test-store", 0, app)
    try:
        ctx = pw_browser.new_context(viewport={"width": 1400, "height": 900})
        page = ctx.new_page()
        try:
            for shape, inp in SHAPES.items():
                datatools.check_input(tool, inp)
                rec = datatools.run_tool(tool, inp, app, url)
                check_shape(page, copy, shape, inp, rec, able)
        finally:
            ctx.close()
    finally:
        srv.shutdown()
        srv.server_close()
        print(f"----  {copy}  {L.reset_test_store(app)}", flush=True)


def main():
    from playwright.sync_api import sync_playwright
    missing = [c for c in COPIES if not (WORK / c / ".groundtruth" / "test-store" / L.TEST_STORE_MARKER).is_file()]
    if missing:
        sys.exit(f"Missing copies {missing}; run .venv/bin/python gates/variants/build_dash_copies.py first.")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            for copy, able in COPIES.items():
                try:
                    check_copy(browser, copy, able)
                except Exception as e:  # noqa: BLE001
                    say(False, copy, "-", "error", f"{type(e).__name__}: {e}")
        finally:
            browser.close()
    print(f"{'PASSED' if all(results) and results else 'FAILED'}: {sum(results)}/{len(results)} assertions", flush=True)
    sys.exit(0 if results and all(results) else 1)


if __name__ == "__main__":
    main()
