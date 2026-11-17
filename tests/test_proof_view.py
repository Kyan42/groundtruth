"""The proof view: on the claims path the verifier records each comparison as it makes it (the film's captions), can
point at an element without clicking it (hover), and can look at a picture (look). The older path is unchanged.

The tools run against a small local page; the harness is driven by scripted conversations; the run document is built
from a run folder written here. No model is involved."""
import asyncio
import base64
import importlib
import json
import os
import re
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from service import prompts
from service.harness import (BROWSER, Run, case_problems, case_tools, clean_plan, compare_problems, setting_problems)
from toolserver import catalog as catalog_module
from toolserver import rundoc
from toolserver.catalog import Catalog
from toolserver.runner import ServiceError, not_checked

from tests.test_starting_state_wiring import ScriptedRun, WithATempDir, case, plan

ROOT = Path(__file__).resolve().parent.parent

PAGE = b"""<!doctype html><html><head><meta charset="utf-8"><title>proof</title>
<style>#tip { display: none; } #card:hover + #tip { display: block; } .red { background: rgb(200, 0, 0); }</style></head><body>
<h1>Tests</h1>
<ul><li id="row">Cart   total
adds up</li><li>Login works</li></ul>
<a id="card" href="#">Cart total adds up</a><p id="tip">Preview: the cart total test</p>
<div class="red" style="width:120px;height:40px">Danger</div>
<button onclick="document.getElementById('out').textContent = 'added'">Add</button>
<p id="out">nothing yet</p>
</body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *a):
        pass


OUTLINE = """() => { const o = document.querySelector('groundtruth-outline');
  return o && {parent: o.parentElement.tagName, inBody: document.body.contains(o), pe: getComputedStyle(o).pointerEvents,
               hidden: o.getAttribute('aria-hidden'), border: getComputedStyle(o).borderTopColor,
               width: getComputedStyle(o).borderTopWidth}; }"""


def ref_of(observed, pattern):
    """The reference observe gave the first line that matches pattern."""
    line = next(line for line in observed.splitlines() if re.search(pattern, line))
    return re.search(r"\[ref=(\w+)\]", line).group(1)


def receipts(folder):
    return [json.loads(line) for line in (folder / "receipts.jsonl").read_text().splitlines()]


class TheThreeTools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
            pw = sync_playwright().start()
            try:
                pw.chromium.launch().close()
            finally:
                pw.stop()
        except Exception as e:  # noqa: BLE001
            raise unittest.SkipTest(f"Chromium cannot launch here: {e}")

    def setUp(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.env = {"url": f"http://127.0.0.1:{self.httpd.server_address[1]}"}
        self.cats = []

    def tearDown(self):
        for c in self.cats:
            c.close()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def catalog(self, name="unit", record=True):
        c = Catalog(self.env, self.dir / name, record=record)
        self.cats.append(c)
        return c

    # ---------------------------------------------------------------- hover
    def test_hover_points_without_clicking_and_shows_what_appears(self):
        c = self.catalog()
        seen = c.run("observe", {})["text"]
        self.assertNotIn("Preview: the cart total test", seen)
        with mock.patch.object(c, "_glide_to", wraps=c._glide_to) as g:
            res = c.run("hover", {"ref": ref_of(seen, r'link "Cart total adds up"')})
        self.assertEqual(res["text"], 'pointed at a "Cart total adds up"')
        self.assertEqual([call.args[1] for call in g.call_args_list], [False])        # the pointer glides there, no click ring
        self.assertIn("Preview: the cart total test", c.run("observe", {})["text"])
        self.assertEqual(c.run("hover", {"role": "button", "name": "Add"})["text"], 'pointed at button "Add"')
        self.assertIn("nothing yet", c.run("observe", {})["text"])                      # pointing never clicks

    # ---------------------------------------------------------------- look
    def test_look_saves_the_picture_returns_it_in_data_and_never_in_the_receipts(self):
        c = self.catalog()
        whole = c.run("look", {"label": "the list"})
        self.assertEqual(whole["text"], "shot-1")
        png = (self.dir / "unit" / "shot-1.png").read_bytes()
        self.assertEqual(whole["data"]["image"]["mime"], "image/png")
        self.assertEqual(base64.b64decode(whole["data"]["image"]["data"]), png)
        seen = c.run("observe", {})["text"]
        one = c.run("look", {"label": "the red box", "ref": ref_of(seen, r'generic .*: Danger')})
        self.assertEqual(one["text"], "shot-2")
        self.assertLess(len((self.dir / "unit" / "shot-2.png").read_bytes()), len(png))   # that element only
        self.assertEqual(c.run("screenshot", {"label": "after"})["text"], "shot-3")       # the same numbering as screenshot
        text = (self.dir / "unit" / "receipts.jsonl").read_text()
        self.assertNotIn(whole["data"]["image"]["data"][:40], text)
        self.assertNotIn("iVBOR", text)
        self.assertEqual([r["result"] for r in receipts(self.dir / "unit") if r["tool"] == "look"], ["shot-1", "shot-2"])

    def test_a_picture_over_two_megabytes_is_not_sent(self):
        c = self.catalog()
        with mock.patch.object(catalog_module, "MAX_PICTURE", 10):
            res = c.run("look", {"label": "big"})
        self.assertFalse(res["isError"])
        self.assertIsNone(res["data"])
        self.assertTrue(res["text"].startswith("shot-1 "))
        self.assertIn("larger than 2 MB", res["text"])
        self.assertTrue((self.dir / "unit" / "shot-1.png").is_file())

    def test_look_with_a_stale_reference_is_an_error(self):
        c = self.catalog()
        c.run("observe", {})
        res = c.run("look", {"label": "x", "ref": "e999"})
        self.assertTrue(res["isError"])
        self.assertIn("observe again", res["text"])

    # ---------------------------------------------------------------- compare
    def test_a_filmed_comparison_outlines_the_element_and_observe_never_lists_the_outline(self):
        c = self.catalog()
        before = c.run("observe", {})["text"]
        ref = ref_of(before, r'listitem .*: Cart total')
        seen = {}
        hold = c._hold

        def watch(ms):
            seen.setdefault("outline", c.page().evaluate(OUTLINE))
            seen.setdefault("observed", c.run("observe", {})["text"])
            seen.setdefault("ms", ms)
            hold(ms)
        began = time.time()
        with mock.patch.object(c, "_hold", side_effect=watch), mock.patch.object(c, "_glide_to", wraps=c._glide_to) as g:
            res = c.run("compare", {"what": "Tests listed for the shop project", "expected": "Cart total adds up, from the data put in",
                                    "found": "Cart total adds up", "matches": True, "ref": ref})
        self.assertFalse(res["isError"], res)
        self.assertEqual(res["text"], "noted")
        self.assertEqual(seen["outline"], {"parent": "HTML", "inBody": False, "pe": "none", "hidden": "true",
                                           "border": "rgba(22, 160, 70, 0.95)", "width": "3px"})
        self.assertEqual(seen["ms"], 1500)
        self.assertEqual(seen["observed"], before)              # the outline is never in what observe lists
        self.assertEqual([call.args[1] for call in g.call_args_list], [False])
        d = res["data"]
        self.assertEqual({k: d[k] for k in ("what", "expected", "found", "matches", "element", "claim")},
                         {"what": "Tests listed for the shop project", "expected": "Cart total adds up, from the data put in",
                          "found": "Cart total adds up", "matches": True, "element": "Cart total adds up", "claim": None})
        self.assertTrue(began <= d["t"] <= time.time())
        last = receipts(self.dir / "unit")[-1]
        self.assertEqual((last["tool"], last["result"], last["compared"]), ("compare", "noted", d))

    def test_a_comparison_that_did_not_match_is_outlined_in_red(self):
        c = self.catalog()
        ref = ref_of(c.run("observe", {})["text"], r'listitem .*: Login')
        seen = {}
        with mock.patch.object(c, "_hold", side_effect=lambda ms: seen.setdefault("o", c.page().evaluate(OUTLINE))):
            c.run("compare", {"what": "w", "expected": "e", "found": "f", "matches": False, "ref": ref})
        self.assertEqual(seen["o"]["border"], "rgba(220, 40, 40, 0.95)")

    def test_a_stale_reference_is_an_error_and_records_nothing(self):
        c = self.catalog()
        c.run("observe", {})
        res = c.run("compare", {"what": "w", "expected": "e", "found": "f", "matches": True, "ref": "e999"})
        self.assertTrue(res["isError"])
        self.assertIn("observe again", res["text"])
        self.assertNotIn("compared", receipts(self.dir / "unit")[-1])

    def test_with_no_reference_no_page_is_opened(self):
        c = self.catalog()
        res = c.run("compare", {"what": "Total shown", "expected": "$3, from the data", "found": "$3", "matches": True})
        self.assertFalse(res["isError"], res)
        self.assertIsNone(c._page)
        self.assertIsNone(res["data"]["element"])
        self.assertEqual(receipts(self.dir / "unit")[-1]["compared"], res["data"])

    def test_with_no_reference_a_filmed_page_holds_a_moment(self):
        c = self.catalog()
        c.run("observe", {})
        with mock.patch.object(c, "_hold") as hold:
            c.run("compare", {"what": "w", "expected": "e", "found": "f", "matches": True})
        hold.assert_called_once_with(600)

    def test_a_drawing_that_fails_never_fails_the_comparison(self):
        c = self.catalog()
        ref = ref_of(c.run("observe", {})["text"], r'listitem .*: Login')
        with mock.patch.object(c, "_glide_to", side_effect=RuntimeError("gone")), \
                mock.patch.object(catalog_module, "_OUTLINE_JS", "() => { throw new Error('navigated'); }"):
            res = c.run("compare", {"what": "w", "expected": "e", "found": "f", "matches": True, "ref": ref})
        self.assertFalse(res["isError"], res)

    def test_unfilmed_with_a_reference_reads_the_element_and_draws_nothing(self):
        c = self.catalog("plain", record=False)
        ref = ref_of(c.run("observe", {})["text"], r'listitem .*: Login')
        with mock.patch.object(c, "_hold") as hold:
            res = c.run("compare", {"what": "w", "expected": "e", "found": "f", "matches": True, "ref": ref})
        self.assertEqual(res["data"]["element"], "Login works")
        hold.assert_not_called()
        self.assertIsNone(c.page().evaluate(OUTLINE))

    def test_what_a_comparison_refuses(self):
        c = self.catalog()
        good = {"what": "w", "expected": "e", "found": "f", "matches": True}
        for bad, words in (({"what": " "}, "what is empty"), ({"expected": ""}, "expected is empty"), ({"found": ""}, "found is empty"),
                           ({"what": "x" * 101}, "what is longer than 100"), ({"expected": "x" * 201}, "expected is longer than 200"),
                           ({"found": "x" * 201}, "found is longer than 200"), ({"matches": "yes"}, "matches of compare must be bool"),
                           ({"matches": 1}, "must be bool"), ({"claim": "p 1;"}, "claim is the id")):
            with self.subTest(words=words):
                res = c.run("compare", {**good, **bad})
                self.assertTrue(res["isError"])
                self.assertTrue(res["text"].startswith("refused:"), res["text"])
                self.assertIn(words, res["text"])
                self.assertNotIn("compared", receipts(self.dir / "unit")[-1])
        ok = c.run("compare", {**good, "what": "x" * 100, "claim": "p1"})
        self.assertEqual(ok["data"]["claim"], "p1")
        self.assertIsNone(c._page)


# -------------------------------------------------------------------- the harness
class ToolLists(unittest.TestCase):
    def test_only_a_case_from_a_claim_gets_the_new_tools(self):
        env = {"has_pid": True}
        self.assertEqual(case_tools("behaviour", "browser", env), BROWSER)
        self.assertEqual(case_tools("behaviour", "api", env), ["http_request"])
        self.assertEqual(case_tools("behaviour", "browser", env, True), BROWSER + ["hover", "look", "compare"])
        self.assertEqual(case_tools("behaviour", "api", env, True), ["http_request", "compare"])
        self.assertIn("process_info", case_tools("measurement", "browser", env, True))
        self.assertEqual(case_tools("measurement", "browser", env), BROWSER + ["process_info", "gpu_check", "cpu_sample", "net_connections"])
        self.assertNotIn("compare", case_tools("code", "code", env, True))
        for t in ("hover", "look", "compare"):
            self.assertNotIn(t, BROWSER)
        self.assertEqual(BROWSER, ["observe", "navigate", "click", "fill", "read_attribute", "click_and_watch", "wait", "screenshot", "media"])


def conv(from_claim=True, door="browser", tools=(), compares=()):
    return {"kind": "behaviour", "door": door, "calls": [{"tool": t, "args": {}, "isError": False} for t in tools],
            "shots": set(), "checks": {}, "way_status": None, "from_claim": from_claim, "compares": list(compares)}


class TheAcceptanceRule(unittest.TestCase):
    NONE = "record each comparison this outcome rests on with compare, at the moment you make it, then submit again"
    BREAKS = 'a "breaks" outcome needs the comparison that did not match: record it with compare (matches false), then submit'

    def why(self, outcome, compares, **kw):
        return case_problems(conv(compares=compares, **kw), {"outcome": outcome, "observed": "x"})

    def test_the_three_refusals_word_for_word(self):
        self.assertEqual(self.why("holds", [], tools=["observe"]), self.NONE)
        self.assertEqual(self.why("breaks", [], tools=["observe"]), self.NONE)
        self.assertEqual(self.why("holds", [{"what": "Tests listed", "matches": True}, {"what": "Total", "matches": False}], tools=["observe"]),
                         'you recorded a comparison that did not match ("Total"). If the property is not true here, the outcome is '
                         "breaks. If you misread, record that comparison again as it really is, then submit")
        self.assertEqual(self.why("breaks", [{"what": "Total", "matches": True}], tools=["observe"]), self.BREAKS)

    def test_the_latest_under_each_what_counts_ignoring_case_and_spaces(self):
        fixed = [{"what": "Total  shown", "matches": False}, {"what": "total shown ", "matches": True}]
        self.assertIsNone(self.why("holds", fixed, tools=["observe"]))
        self.assertEqual(self.why("breaks", fixed, tools=["observe"]), self.BREAKS)
        broke = [{"what": "Total", "matches": True}, {"what": "TOTAL", "matches": False}, {"what": "Rows", "matches": True}]
        self.assertIsNone(self.why("breaks", broke, tools=["observe"]))

    def test_it_comes_after_every_other_check(self):
        self.assertIn("haven't used the way in", self.why("holds", [], tools=["compare"]))      # compare never counts as using the way in
        self.assertIsNone(self.why("holds", [{"what": "w", "matches": True}], tools=["look"]))  # look does, on the claims path
        self.assertIsNone(self.why("holds", [{"what": "w", "matches": True}], tools=["hover"]))
        self.assertIn("observed is required", case_problems(conv(), {"outcome": "holds", "observed": ""}))

    def test_other_outcomes_and_the_older_path_are_never_held_to_it(self):
        for outcome in ("outside", "cant_run"):
            self.assertIsNone(self.why(outcome, []))
        self.assertIsNone(case_problems({**conv(compares=[]), "calls": [{"tool": "observe", "args": {}, "isError": False}]},
                                        {"outcome": "declined", "observed": "x", "question": "q?", "draft_if_intended": "d."}))
        self.assertIsNone(self.why("holds", [], from_claim=False, tools=["observe"]))
        self.assertIsNone(self.why("holds", [{"what": "w", "matches": False}], from_claim=False, tools=["observe"]))
        older = {"kind": "behaviour", "door": "browser", "calls": [{"tool": "observe", "args": {}, "isError": False}],
                 "shots": set(), "checks": {}, "way_status": None}                  # a conversation from before this work
        self.assertIsNone(case_problems(older, {"outcome": "holds", "observed": "x"}))
        self.assertIn("haven't used the way in", case_problems({**older, "calls": [{"tool": "look", "args": {}, "isError": False}]},
                                                               {"outcome": "holds", "observed": "x"}))
        self.assertIsNone(compare_problems([{"what": "w", "matches": True}], "holds"))


class ScriptedCase(Run):
    """One case conversation whose model is a script of tool calls and submissions, through the real tool_call and
    submit. The tool server is a script too: compare returns what it was given, look returns a picture."""

    PICTURE = {"mime": "image/png", "data": base64.b64encode(b"\x89PNG fake").decode()}

    def __init__(self, tmp, script, fail=()):
        super().__init__({"properties": [{"id": "p1", "statement": "s"}], "env": {"url": "http://127.0.0.1:1"}},
                         "scripted", Path(tmp), "http://127.0.0.1:0")
        self.script, self.fail, self.replies, self.n = script, set(fail), [], 0

    def call_local(self, tool, args, timeout=None):
        self.n += 1
        if self.n in self.fail:
            return {"text": "error: no element has the reference e9 now", "isError": True, "data": None}
        if tool == "compare":
            return {"text": "noted", "isError": False, "data": {"element": None, "claim": None, "t": 1.0, **args}}
        if tool == "look":
            return {"text": f"shot-{self.n}", "isError": False, "data": {"image": self.PICTURE}}
        if tool == "screenshot":
            return {"text": f"shot-{self.n}", "isError": False, "data": None}
        return {"text": "ok", "isError": False, "data": None}

    def _claude(self, prompt, system, extra, name, timeout=900):
        unit = self.conv["unit"]
        for step in self.script:
            if step[0] == "submit":
                self.replies.append(self.submit(unit, step[1])["text"])
            else:
                self.replies.append(self.tool_call(unit, step[0], step[1]))
        return {"total_cost_usd": 0, "num_turns": 1}

    def talk(self, from_claim=True, door="browser"):
        return self.converse("p1/v1", "case", case_tools("behaviour", door, {}, from_claim), "system", "message",
                             kind="behaviour", door=door, from_claim=from_claim)


def cmp(what, matches, **kw):
    """One compare call. Through the pages a comparison needs ref, so it has one unless ref=None is given."""
    args = {"what": what, "expected": "3 tests, from the data put in", "found": "3 tests", "matches": matches, "ref": "e1", **kw}
    return ("compare", {k: v for k, v in args.items() if v is not None})


HOLDS = ("submit", {"outcome": "holds", "observed": "3 tests listed."})
BREAKS = ("submit", {"outcome": "breaks", "observed": "2 tests listed."})


class ScriptedConversations(WithATempDir):
    def talk(self, script, **kw):
        run = ScriptedCase(self.tmp.name, script, fail=kw.pop("fail", ()))
        rep, meta, cause = run.talk(**kw)
        return run, rep, meta

    def test_holds_with_no_comparison_is_sent_back_then_accepted(self):
        run, rep, meta = self.talk([("observe", {}), HOLDS, cmp("Tests listed", True), HOLDS])
        self.assertEqual(run.replies[1], "not accepted: " + TheAcceptanceRule.NONE)
        self.assertEqual(run.replies[-1], "saved; you're done")
        self.assertEqual(rep["outcome"], "holds")
        self.assertEqual(meta["rejected"], [TheAcceptanceRule.NONE])

    def test_a_mismatch_then_a_correction_under_the_same_what(self):
        run, rep, _ = self.talk([("observe", {}), cmp("Tests listed", False), HOLDS, cmp("tests  LISTED", True), HOLDS])
        self.assertIn('did not match ("Tests listed")', run.replies[2])
        self.assertEqual(run.replies[-1], "saved; you're done")

    def test_breaks_needs_the_failed_comparison(self):
        run, rep, _ = self.talk([("observe", {}), cmp("Tests listed", True), BREAKS, cmp("Tests listed", False), BREAKS])
        self.assertEqual(run.replies[2], "not accepted: " + TheAcceptanceRule.BREAKS)
        self.assertEqual(run.replies[-1], "saved; you're done")
        self.assertEqual(rep["outcome"], "breaks")

    def test_a_comparison_that_failed_to_record_does_not_count(self):
        run, rep, _ = self.talk([("observe", {}), cmp("Tests listed", True), HOLDS], fail={2})
        self.assertEqual(run.replies[2], "not accepted: " + TheAcceptanceRule.NONE)
        self.assertIsNone(rep)

    def test_the_older_path_accepts_a_result_with_no_comparisons(self):
        run, rep, meta = self.talk([("observe", {}), HOLDS], from_claim=False)
        self.assertEqual(run.replies[-1], "saved; you're done")
        self.assertEqual(meta["tools"], BROWSER)
        run, rep, _ = self.talk([("observe", {}), cmp("x", True), HOLDS], from_claim=False)
        self.assertEqual(run.replies[1], {"text": "refused: compare is not available in this conversation"})

    def test_the_picture_reaches_the_caller_only_for_look_and_a_look_can_be_cited(self):
        run, rep, _ = self.talk([("look", {"label": "the list"}), ("screenshot", {"label": "s"}), ("observe", {}),
                                 cmp("Colour", True), ("submit", {"outcome": "holds", "observed": "red", "screenshots": ["shot-1"]})])
        self.assertEqual(run.replies[0], {"text": "shot-1", "image": ScriptedCase.PICTURE})
        self.assertEqual(run.replies[1], {"text": "shot-2"})
        self.assertEqual(run.replies[2], {"text": "ok"})
        self.assertEqual(run.replies[3], {"text": "noted"})
        self.assertEqual(run.replies[-1], "saved; you're done")
        self.assertEqual(rep["screenshots"], ["shot-1"])

    def test_through_the_pages_a_compare_with_no_ref_is_refused_and_not_recorded(self):
        refusal = ("refused: give ref, the element that shows the thing compared (from the latest observe); when the "
                   "point is that something is missing, the area where it would be")
        run, rep, meta = self.talk([("observe", {}), cmp("Tests listed", True, ref=None), cmp("Tests listed", True, ref="  "), HOLDS,
                                    cmp("Tests listed", True), HOLDS])
        self.assertEqual(run.replies[1], {"text": refusal})
        self.assertEqual(run.replies[2], {"text": refusal})
        self.assertEqual(run.n, 2)                                # observe and the one compare with a ref: the refused never left
        self.assertEqual(run.replies[3], "not accepted: " + TheAcceptanceRule.NONE)   # a refused compare is not a recorded comparison
        self.assertEqual(run.replies[-1], "saved; you're done")
        self.assertEqual(rep["outcome"], "holds")

    def test_an_api_case_from_a_claim_may_compare_with_no_ref(self):
        run, rep, _ = self.talk([("http_request", {"method": "GET", "path": "/"}), cmp("Status", True, ref=None), HOLDS], door="api")
        self.assertEqual(run.replies[1], {"text": "noted"})
        self.assertEqual(run.replies[-1], "saved; you're done")

    def test_an_api_case_from_a_claim_records_comparisons_too(self):
        run, rep, _ = self.talk([("http_request", {"method": "GET", "path": "/"}), ("look", {"label": "x"}), cmp("Status", True), HOLDS],
                                door="api")
        self.assertEqual(run.replies[1], {"text": "refused: look is not available in this conversation"})
        self.assertEqual(run.replies[-1], "saved; you're done")


class ProxyPictures(unittest.TestCase):
    """The model's tool list: the picture travels back for look only, and the descriptions are the lead's, word for word."""

    def proxy(self, answer):
        env = {"GT_SERVICE": "http://127.0.0.1:1", "GT_RUN": "r", "GT_TOKEN": "t", "GT_UNIT": "p1/v1", "GT_ROLE": "case",
               "GT_TOOLS": json.dumps(["hover", "look", "compare", "screenshot"])}
        sys.modules.pop("service.proxy", None)
        with mock.patch.dict(os.environ, env):
            proxy = importlib.import_module("service.proxy")
        self.addCleanup(sys.modules.pop, "service.proxy", None)
        self.sent = []
        proxy._post = lambda path, payload: (self.sent.append(payload), answer)[1]
        return proxy

    def test_look_returns_the_text_and_the_picture(self):
        png = b"\x89PNG\r\n\x1a\nnot really"
        proxy = self.proxy({"text": "shot-4", "image": {"mime": "image/png", "data": base64.b64encode(png).decode()}})
        res = asyncio.run(proxy.server.call_tool("look", {"label": "the badge", "ref": "e3"}))
        self.assertEqual([c.type for c in res.content], ["text", "image"])
        self.assertEqual(res.content[0].text, "shot-4")
        self.assertEqual(base64.b64decode(res.content[1].data), png)
        self.assertEqual(self.sent, [{"unit": "p1/v1", "tool": "look", "args": {"label": "the badge", "ref": "e3"}}])

    def test_without_a_picture_look_and_the_others_return_text_only(self):
        proxy = self.proxy({"text": "shot-1 (the picture is larger than 2 MB, so it is not shown)"})
        res = asyncio.run(proxy.server.call_tool("look", {"label": "x"}))
        self.assertEqual([c.type for c in res.content], ["text"])
        for tool, args in (("screenshot", {"label": "x"}), ("hover", {"ref": "e1"}),
                           ("compare", {"what": "w", "expected": "e", "found": "f", "matches": False})):
            proxy = self.proxy({"text": "done", "image": {"mime": "image/png", "data": "AAAA"}})   # a picture sent anyway is not passed on
            res = asyncio.run(proxy.server.call_tool(tool, args))
            self.assertEqual([c.type for c in res.content], ["text"], tool)
        self.assertEqual(self.sent[-1]["args"], {"what": "w", "expected": "e", "found": "f", "matches": False})

    def test_the_descriptions_are_word_for_word(self):
        proxy = self.proxy({"text": ""})
        tools = {t.name: t.description for t in asyncio.run(proxy.server.list_tools())}
        self.assertEqual(tools["hover"], "Point at one element without clicking it, as a person moving the mouse over it would. Give ref, the reference observe listed for it, or its ARIA role and accessible name. Use it for anything that appears on hover (a preview, a tooltip, a menu); observe afterwards to read what appeared.")
        self.assertEqual(tools["look"], "Look at the page as a picture. With no ref, the whole page as it is on screen; with ref (from the latest observe), that one element, larger. Use it for what the page's text cannot show: colour, shape, size, position, whether something is really visible. Returns the picture and its id, which you can cite in the report.")
        self.assertEqual(tools["compare"], "Record one comparison at the moment you make it. what: the thing compared, in a few plain words. expected: the right answer and where it comes from. found: what the app shows, as you just read it. matches: whether they agree. ref: the element that shows it (from the latest observe), which is outlined in the film. It checks nothing itself: it is how a person watching the film follows your check.")


# -------------------------------------------------------------------- planning: setting, tools, prompts
CLAIM = {"id": "p1", "statement": "The Tests tab lists every test of the project.", "checked_against": "The tests put in.",
         "variations": [{"id": "v1", "what": "A project with three tests."}, {"id": "v2", "what": "A project with none."}]}


class KeepingSystems(ScriptedRun):
    """Also keeps every conversation's system prompt (ScriptedRun keeps only the planner's)."""

    def _claude(self, prompt, system, extra, name, timeout=900):
        self.systems = getattr(self, "systems", {})
        self.systems[self.conv["unit"]] = system
        return super()._claude(prompt, system, extra, name, timeout)


class PlannedFromAClaim(WithATempDir):
    def run_it(self, plans, **kw):
        run = KeepingSystems(self.tmp.name, plans, **kw)
        run.work()
        self.assertNotIn("error", run.report, run.report.get("error"))
        return run

    def test_a_plan_without_a_setting_is_sent_back_on_the_claims_path(self):
        run = self.run_it([plan([case("v1"), case("v2", setting="One project, no tests.")]),
                           plan([case("v1", setting="x" * 161), case("v2", setting="One project, no tests.")]),
                           plan([case("v1", setting="  One project: shop, with 3 tests.  "), case("v2", setting="One project, no tests.")])],
                          prop=CLAIM)
        self.assertEqual(run.replies[0], 'not accepted: case v1 needs "setting": one plain sentence, at most 160 characters, saying '
                                         "what the case starts from, for a person about to watch its film")
        self.assertIn('case v1: "setting" is longer than 160 characters', run.replies[1])
        self.assertEqual(run.replies[2], "saved; you're done")
        self.assertEqual(run.result("v1")["setting"], "One project: shop, with 3 tests.")

    def test_the_older_path_never_asks_for_one(self):
        run = self.run_it([plan([case("c1")])])
        self.assertEqual(run.replies, ["saved; you're done"])
        self.assertEqual(run.result("c1")["setting"], "")
        self.assertIsNone(setting_problems(plan([case("c1", setting="s")])))
        self.assertEqual(clean_plan(plan([case("c1", setting=" s ")]))["cases"][0]["setting"], "s")

    def test_tools_and_prompts_on_the_claims_path(self):
        run = self.run_it([plan([case("v1", setting="s"), case("v2", setting="s")])], prop=CLAIM)
        self.assertEqual(run.given["p1/discovery"], {"list_routes", "observe", "navigate", "hover", "look"})
        self.assertEqual(run.given["p1/v1"], set(BROWSER) | {"hover", "look", "compare", "situation"})
        self.assertIn(prompts.CASE_FROM_CLAIM, run.systems["p1/v1"])
        self.assertTrue(run.systems["p1/v1"].endswith(prompts.CASE_FROM_CLAIM + prompts.CASE_SITUATION + prompts.CASE_FROM_CLAIM_BROWSER
                                                      + "\n" + prompts.SHARED))
        self.assertIn('Each case also gets "setting"', run.systems["p1/discovery"])
        self.assertIn("the page's text cannot show. Plan those steps where a variation needs them. You have both tools too.\n",
                      prompts.FROM_CLAIM)

    def test_the_older_path_is_unchanged(self):
        run = self.run_it([plan([case("c1")])])
        self.assertEqual(run.given["p1/discovery"], {"list_routes", "observe", "navigate"})
        self.assertEqual(run.given["p1/c1"], set(BROWSER))
        self.assertEqual(run.systems["p1/c1"], prompts.CASE + prompts.CASE_BROWSER + "\n" + prompts.SHARED)
        self.assertNotIn('"setting"', run.systems["p1/discovery"])
        self.assertNotIn("hover", run.systems["p1/discovery"])


# -------------------------------------------------------------------- the feature file's not_checked
class NotChecked(unittest.TestCase):
    def test_the_list_is_read_in_plain_words(self):
        self.assertEqual(not_checked({}, "f.yml"), [])
        self.assertEqual(not_checked({"not_checked": [" How fast the page loads. ", "Old runs."]}, "f.yml"),
                         ["How fast the page loads.", "Old runs."])
        for bad, words in (("one", "a list of at most 12"), (["x"] * 13, "a list of at most 12"), ([""], "item 1"),
                           (["ok", "x" * 201], "item 2"), ([{"a": 1}], "item 1")):
            with self.subTest(words=words), self.assertRaises(ServiceError) as e:
                not_checked({"not_checked": bad}, "f.yml")
            self.assertIn(words, str(e.exception))
            self.assertIn("f.yml", str(e.exception))

    def test_the_claims_printout_shows_it_and_approval_ignores_it(self):
        import groundtruth
        from toolserver import approval
        with tempfile.TemporaryDirectory() as t:
            repo = Path(t)
            (repo / ".groundtruth" / "features").mkdir(parents=True)
            f = repo / ".groundtruth" / "features" / "tests.yml"
            body = "feature: tests\nproperties:\n  - id: p1\n    statement: The Tests tab lists every test.\n"
            f.write_text(body)
            self.assertNotIn("What these claims do not check", groundtruth.claims_text(repo, "tests", full=True))
            approval.write_record(repo, "tests", approval.read_claims(repo, "tests")[1])
            f.write_text(body + "not_checked:\n  - How fast the page loads.\n  - Runs older than a week.\n")
            text = groundtruth.claims_text(repo, "tests", full=True)
            self.assertIn("What these claims do not check:\n   - How fast the page loads.\n   - Runs older than a week.", text)
            self.assertIn("Approval: approved", text)                 # not part of the approval comparison
            brief = groundtruth.claims_text(repo, "tests", full=False)   # what `claims` prints unless --full is given
            self.assertEqual(brief.splitlines()[0][:26], "tests: 1 claim, approved 2")
            self.assertIn("\n1. p1\n   The Tests tab lists every test.\n", brief)
            self.assertNotIn("How fast the page loads", brief)
            self.assertNotIn("Approval:", brief)
            self.assertTrue(brief.endswith("and 2 things these claims do not check. Add --full to see them."))
            f.write_text(body + "not_checked: [1]\n")
            with self.assertRaises(approval.ClaimsError) as e:
                groundtruth.claims_text(repo, "tests", full=True)
            self.assertIn("item 1 of `not_checked`", str(e.exception))


# -------------------------------------------------------------------- the run document
class TheRunDocument(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        run = Path(self.tmp.name) / "20261113-100000"
        unit = run / "p1" / "v1"
        unit.mkdir(parents=True)
        (unit / "clip-1.webm").write_bytes(b"webm")
        (unit / "shot-1.png").write_bytes(b"png")
        t0 = 2000.0
        compared = {"what": "Tests listed for the shop project", "expected": "3 tests, from the data put in", "found": "3 rows",
                    "matches": True, "element": "Cart total adds up Login works", "claim": None, "t": t0 + 6.4}
        rows = [
            {"t": t0 + 1, "tool": "navigate", "args": {"path": "/"}, "result": "Now at http://127.0.0.1:1/"},
            {"t": t0 + 2, "tool": "hover", "args": {"ref": "e4"}, "result": 'pointed at a "Cart total adds up"'},
            {"t": t0 + 3, "tool": "look", "args": {"label": "the badge", "ref": "e5"}, "result": "shot-1"},
            {"t": t0 + 8.2, "tool": "compare", "args": {k: compared[k] for k in ("what", "expected", "found", "matches")} | {"ref": "e6"},
             "result": "noted", "compared": compared},
            {"t": t0 + 9, "tool": "compare", "args": {"what": "w", "expected": "e", "found": "f", "matches": True, "ref": "e9"},
             "result": "error: no element has the reference e9 now"},
            {"t": t0 + 10, "tool": "compare", "args": {"what": "", "expected": "e", "found": "f", "matches": True},
             "result": "refused: what is empty", "refused": True},
        ]
        (unit / "receipts.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
        variations = [{"id": "v1", "what": "A project with three tests."}, {"id": "v2", "what": "A project with none."}]
        (run / "sent.json").write_text(json.dumps({"properties": [{"id": "p1", "statement": "s", "variations": variations}]}))
        (run / "run.json").write_text(json.dumps({
            "run": "20261113-100000", "feature": "tests", "env": {"url": "http://127.0.0.1:1"},
            "not_checked": ["How fast the page loads."],
            "properties": [{"id": "p1", "statement": "s", "verdict": "holds", "checked_against": "The tests put in.",
                            "plan": {"kind": "behaviour", "from_claim": True, "entry_points": [{"id": "e1", "door": "browser", "where": "w"}]},
                            "cases": [{"id": "v1", "entry_point": "e1", "door": "browser", "what": "Open the Tests tab.",
                                       "setting": "One project: shop, with 3 tests.",
                                       "result": {"outcome": "holds", "observed": "3 rows.", "clips": [{"file": "clip-1.webm", "started": t0}]}}]},
                           {"id": "p2", "statement": "older", "verdict": "holds", "plan": {"kind": "behaviour"},
                            "cases": [{"id": "c1", "entry_point": "e1", "door": "browser", "what": "x", "result": {"outcome": "holds"}}]}]}))
        self.doc, _ = rundoc.build(run)
        self.case = self.doc["properties"][0]["cases"][0]

    def test_top_level_and_property(self):
        self.assertEqual(self.doc["not_checked"], ["How fast the page loads."])
        self.assertIsNone(self.doc["walkthrough"])
        p1, p2 = self.doc["properties"]
        self.assertTrue(p1["from_claim"])
        self.assertEqual(p1["variations"], [{"id": "v1", "what": "A project with three tests."}, {"id": "v2", "what": "A project with none."}])
        self.assertEqual((p2["from_claim"], p2["variations"]), (False, []))
        self.assertEqual((p2["cases"][0]["variation"], p2["cases"][0]["setting"]), ("", ""))

    def test_case_variation_and_setting(self):
        self.assertEqual(self.case["variation"], "A project with three tests.")
        self.assertEqual(self.case["setting"], "One project: shop, with 3 tests.")

    def test_steps(self):
        steps = self.case["steps"]
        self.assertEqual([(s["kind"], s["words"]) for s in steps],
                         [("action", "open /"), ("action", 'point at link "Cart total adds up"'),
                          ("look", "look at an element as a picture: the badge"), ("compare", "Tests listed for the shop project")])
        c = steps[3]
        self.assertEqual(c, {"clip": 0, "at": 6.4, "kind": "compare", "ok": True, "words": "Tests listed for the shop project",
                             "what": "Tests listed for the shop project", "expected": "3 tests, from the data put in", "found": "3 rows",
                             "matches": True, "element": "Cart total adds up Login works", "claim": None, "say": ""})
        self.assertEqual(self.case["shots"], [{"id": "shot-1", "file": "p1/v1/shot-1.png", "label": "the badge", "at": 3.0, "cited": False}])

    def test_older_runs_have_empty_defaults(self):
        doc = json.loads(json.dumps(self.doc))
        self.assertIn("not_checked", doc)
        run = Path(self.tmp.name) / "20261113-100000"
        data = json.loads((run / "run.json").read_text())
        data.pop("not_checked")
        (run / "run.json").write_text(json.dumps(data))
        (run / "sent.json").unlink()
        old, _ = rundoc.build(run)
        self.assertEqual(old["not_checked"], [])
        self.assertEqual(old["properties"][0]["variations"], [])
        self.assertEqual(old["properties"][0]["cases"][0]["variation"], "")


if __name__ == "__main__":
    unittest.main()
