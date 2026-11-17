"""Spoken lines in the walkthrough: before each stop the verifier says what that part should do (say), and with each
comparison what it would say out loud (compare's say). Only the walkthrough has either; variation cases and the
older path are unchanged."""
import asyncio
import importlib
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from service import prompts
from service.harness import BROWSER, WALK, case_tools
from toolserver import rundoc
from toolserver.catalog import Catalog

from tests.test_proof_view import OUTLINE, ScriptedCase, _Handler, cmp, receipts, ref_of
from tests.test_starting_state_wiring import WithATempDir
from tests.test_walkthrough import spoken

LONG = "say it in one or two short sentences, at most 110 characters; call say again for the next thought"
FIRST_SAY = ("refused: first say, in one or two short spoken sentences, what this part of the app should do "
             "(say, with this stop's claim), then check it")
NO_REF = ("refused: give ref, the element that shows the thing compared (from the latest observe); when the "
          "point is that something is missing, the area where it would be")
NOT_LISTED = "refused: claim is the id written in brackets at the start of the stop; it is one of: tabs, latest"


class TheTool(unittest.TestCase):
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
        self.cat = Catalog({"url": f"http://127.0.0.1:{self.httpd.server_address[1]}"}, self.dir / "unit", record=True)

    def tearDown(self):
        self.cat.close()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def test_too_long_or_empty_is_refused_in_the_lead_s_words(self):
        for text in ("", "   ", "x" * 111):
            res = self.cat.run("say", {"text": text, "claim": "tabs"})
            self.assertEqual(res["text"], "refused: " + LONG)
            self.assertNotIn("said", receipts(self.dir / "unit")[-1])
        res = self.cat.run("compare", {"what": "w", "expected": "e", "found": "f", "matches": True, "say": "x" * 111})
        self.assertEqual(res["text"], "refused: " + LONG)
        self.assertIsNone(self.cat._page)                                  # neither opens a page by itself
        ok = self.cat.run("say", {"text": "x" * 110, "claim": "tabs"})
        self.assertEqual(ok["text"], "said")
        self.assertIsNone(self.cat._page)

    def test_with_a_ref_the_element_is_outlined_in_blue_and_observe_never_lists_it(self):
        before = self.cat.run("observe", {})["text"]
        seen = {}
        hold = self.cat._hold

        def watch(ms):
            seen.setdefault("outline", self.cat.page().evaluate(OUTLINE))
            seen.setdefault("observed", self.cat.run("observe", {})["text"])
            seen.setdefault("ms", ms)
            hold(ms)
        began = time.time()
        with mock.patch.object(self.cat, "_hold", side_effect=watch):
            res = self.cat.run("say", {"text": "Each test should show its last result.", "claim": "tabs",
                                       "ref": ref_of(before, r"listitem .*: Login")})
        self.assertEqual(res["text"], "said")
        self.assertEqual(seen["outline"]["border"], "rgba(60, 110, 200, 0.9)")
        self.assertEqual(seen["ms"], 300)
        self.assertEqual(seen["observed"], before)
        d = res["data"]
        self.assertEqual({k: d[k] for k in ("text", "element", "claim")},
                         {"text": "Each test should show its last result.", "element": "Login works", "claim": "tabs"})
        self.assertTrue(began <= d["t"] <= time.time())
        last = receipts(self.dir / "unit")[-1]
        self.assertEqual((last["tool"], last["result"], last["said"]), ("say", "said", d))

    def test_with_no_ref_nothing_is_drawn_and_there_is_no_wait(self):
        self.cat.run("observe", {})
        with mock.patch.object(self.cat, "_hold") as hold, mock.patch.object(self.cat, "_outline") as outline:
            res = self.cat.run("say", {"text": "Next, the history.", "claim": "latest"})
        hold.assert_not_called()
        outline.assert_not_called()
        self.assertIsNone(res["data"]["element"])

    def test_compare_carries_what_was_said(self):
        res = self.cat.run("compare", {"what": "w", "expected": "e", "found": "f", "matches": True, "say": " It works. "})
        self.assertEqual(res["data"]["say"], "It works.")
        self.assertEqual(receipts(self.dir / "unit")[-1]["compared"]["say"], "It works.")
        plain = self.cat.run("compare", {"what": "w", "expected": "e", "found": "f", "matches": True})
        self.assertNotIn("say", plain["data"])                            # outside a walkthrough the data is as it was


class TheOrderWithinAStop(WithATempDir):
    def talk(self, script, walk=True, door="browser"):
        run = ScriptedCase(self.tmp.name, script)
        tools = case_tools("behaviour", door, {}, True) + (["say"] if walk else [])
        rep, meta, cause = run.converse(f"{WALK}/tour" if walk else "p1/v1", "case", tools, "s", "m", kind="behaviour", door=door,
                                        from_claim=True, walk={"ids": ["tabs", "latest"], "what": "[tabs] a [latest] b"} if walk else None)
        return run, rep

    def test_the_refusals_in_order(self):
        run, _ = self.talk([("observe", {}),
                            cmp("Cards", True, ref=None, claim="nope", say=""),         # everything wrong: no ref first
                            cmp("Cards", True, claim="nope", say=""),                   # then a claim not in the list
                            cmp("Cards", True, claim="tabs", say=""),                   # then: nothing said yet for this stop
                            spoken("latest"), cmp("Cards", True, claim="tabs", say="Good."),   # said for another stop only
                            spoken("tabs"), cmp("Cards", True, claim="tabs", say=""),   # said, but nothing said with it
                            cmp("Cards", True, claim="tabs", say="It shows both new tests. That works."),
                            cmp("Cards", True, claim="tabs", say="Still fine."),        # a second check needs a new line first
                            ("say", {"text": "x", "claim": "nope"})])
        self.assertEqual([r["text"] if isinstance(r, dict) else r for r in run.replies[1:]],
                         [NO_REF, NOT_LISTED, FIRST_SAY, "ok", FIRST_SAY, "ok", "refused: " + LONG, "noted", FIRST_SAY, NOT_LISTED])
        self.assertEqual(run.n, 4)                     # only observe, the two lines said and the one comparison left the harness

    def test_a_line_said_is_neither_a_comparison_nor_using_the_way_in(self):
        run, rep = self.talk([spoken("tabs"), ("submit", {"outcome": "holds", "observed": "x"}),
                              ("observe", {}), ("submit", {"outcome": "holds", "observed": "x"})])
        self.assertIn("haven't used the way in", run.replies[1])
        self.assertIn("record each comparison this outcome rests on", run.replies[3])
        self.assertIsNone(rep)

    def test_a_variation_case_has_no_say_and_no_say_on_compare(self):
        self.assertNotIn("say", case_tools("behaviour", "browser", {}, True))
        self.assertNotIn("say", case_tools("behaviour", "browser", {}))
        seen = []
        run = ScriptedCase(self.tmp.name, [("observe", {}), spoken("tabs"), cmp("Cards", True, say="It works."),
                                           ("submit", {"outcome": "holds", "observed": "x"})])
        orig = run.call_local
        run.call_local = lambda tool, args, timeout=None: (seen.append((tool, args)), orig(tool, args))[1]
        run.talk()
        self.assertEqual(run.replies[1], {"text": "refused: say is not available in this conversation"})
        self.assertEqual(run.replies[2], {"text": "noted"})                 # no line needed first outside the walkthrough
        self.assertNotIn("say", seen[1][1])
        self.assertEqual(run.replies[-1], "saved; you're done")


class TheProxy(unittest.TestCase):
    def tools(self, unit, allowed):
        env = {"GT_SERVICE": "http://127.0.0.1:1", "GT_RUN": "r", "GT_TOKEN": "t", "GT_UNIT": unit, "GT_ROLE": "case",
               "GT_TOOLS": json.dumps(allowed)}
        sys.modules.pop("service.proxy", None)
        with mock.patch.dict(os.environ, env):
            proxy = importlib.import_module("service.proxy")
        self.addCleanup(sys.modules.pop, "service.proxy", None)
        return {t.name: t for t in asyncio.run(proxy.server.list_tools())}

    def test_say_is_described_word_for_word_and_only_in_the_walkthrough(self):
        walk = self.tools("walkthrough/tour", ["compare", "say"])
        self.assertEqual(walk["say"].description,
                         "Say one thing out loud to the person watching the film, before you check a stop: what this part of the app should "
                         "do, given the data that was put in. One or two short spoken sentences, at most 110 characters. claim: the id in "
                         "brackets at the start of the stop you are on. ref: something on screen to point at while you say it.")
        self.assertEqual(sorted(walk["say"].input_schema["required"]), ["claim", "text"])
        self.assertNotIn("say", self.tools("p1/v1", ["compare", "say"]))   # never defined outside the walkthrough


class TheDocument(unittest.TestCase):
    def test_say_steps_and_what_was_said_with_a_comparison(self):
        with tempfile.TemporaryDirectory() as t:
            run = Path(t) / "20261113-150000"
            unit = run / "walkthrough" / "tour"
            unit.mkdir(parents=True)
            (unit / "clip-1.webm").write_bytes(b"webm")
            t0 = 4000.0
            said = {"text": "Each test should show when it last ran.", "element": "Login works", "claim": "tabs", "t": t0 + 14.2}
            compared = {"what": "Last run", "expected": "e", "found": "f", "matches": True, "element": None, "claim": "tabs",
                        "say": "It says yesterday. That works.", "t": t0 + 20}
            rows = [{"t": t0 + 14.5, "tool": "say", "args": {"text": said["text"], "claim": "tabs", "ref": "e4"}, "result": "said", "said": said},
                    {"t": t0 + 15, "tool": "say", "args": {"text": "x" * 111, "claim": "tabs"}, "result": "refused: " + LONG, "refused": True},
                    {"t": t0 + 21.5, "tool": "compare", "args": {}, "result": "noted", "compared": compared}]
            (unit / "receipts.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
            (run / "run.json").write_text(json.dumps({"run": "20261113-150000", "feature": "f", "env": {"url": "u"}, "properties": [],
                                                      "walkthrough": {"plan": {}, "discovery_meta": {}, "no_plan_because": None,
                                                                      "case": {"id": "tour", "door": "browser", "result": {
                                                                          "outcome": "holds", "clips": [{"file": "clip-1.webm", "started": t0}]}}}}))
            steps = rundoc.build(run)[0]["walkthrough"]["steps"]
        self.assertEqual(steps[0], {"clip": 0, "at": 14.2, "kind": "say", "ok": True, "words": said["text"], "text": said["text"],
                                    "element": "Login works", "claim": "tabs"})
        self.assertEqual(len(steps), 2)                                     # a refused line is not a step
        self.assertEqual((steps[1]["kind"], steps[1]["say"], steps[1]["at"]), ("compare", "It says yesterday. That works.", 20.0))


class ThePrompt(unittest.TestCase):
    def test_the_walkthrough_case_text_is_the_lead_s(self):
        self.assertTrue(prompts.WALKTHROUGH_CASE.startswith("This case is a walkthrough: one film of the whole feature in use. Picture the owner"))
        self.assertIn("Each stop has three beats:", prompts.WALKTHROUGH_CASE)
        self.assertTrue(prompts.WALKTHROUGH_CASE.endswith('comparison matched and "breaks" when any did not.\n'))
        self.assertNotIn("three beats", prompts.CASE_FROM_CLAIM + prompts.CASE_FROM_CLAIM_BROWSER + prompts.CASE)   # only the walkthrough


if __name__ == "__main__":
    unittest.main()
