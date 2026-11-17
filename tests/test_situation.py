"""The situation check and planning reads (claims path only).

A variation case first records whether its starting situation is the one its variation describes (situation); if not,
it has not been run. The planner of a claims path may call read-only data tools to see what exists (read_tool). The
older path and the walkthrough are unchanged."""
import json
import shlex
import sys
import tempfile
import unittest
from pathlib import Path

from service import prompts
from service.harness import BROWSER
from toolserver import rundoc
from toolserver.catalog import Catalog

from tests.test_proof_view import ScriptedCase, cmp
from tests.test_starting_state_wiring import ScriptedRun, WithATempDir, case, plan
from tests.test_walkthrough import WALKIN, Systems, tour

FIRST = "refused: first record whether the starting situation is the one this variation describes (situation), then check"
NONE = "record whether the starting situation is the one this variation describes (situation) before you submit"
NOT_IT = ('the starting situation is not the one this variation describes, so this case has not been run: the outcome is '
          '"cant_run", and observed says what the starting situation lacked')
NO_REF = ("refused: give ref, the element that shows the thing compared (from the latest observe); when the "
          "point is that something is missing, the area where it would be")
CLAIM = {"id": "p1", "statement": "A meal lists its stations.",
         "variations": [{"id": "v1", "what": "A meal with only fixed stations."}]}


def sit(matches, expected="A meal whose stations are all fixed ones.", found="Lunch: Grill and Salad, both fixed."):
    return ("situation", {"expected": expected, "found": found, "matches": matches})


HOLDS = ("submit", {"outcome": "holds", "observed": "x"})


class TheTool(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_refusals_receipt_and_no_page(self):
        cat = Catalog({"url": "http://127.0.0.1:9"}, self.dir, record=True)
        self.addCleanup(cat.close)
        for bad, words in (({"expected": ""}, "expected is empty"), ({"found": " "}, "found is empty"),
                           ({"expected": "x" * 201}, "expected is longer than 200"), ({"found": "x" * 201}, "found is longer than 200"),
                           ({"matches": "no"}, "matches of situation must be bool")):
            res = cat.run("situation", {"expected": "e", "found": "f", "matches": True, **bad})
            self.assertTrue(res["text"].startswith("refused:"), res)
            self.assertIn(words, res["text"])
        res = cat.run("situation", {"expected": " e ", "found": "f", "matches": False})
        self.assertEqual(res["text"], "noted")
        self.assertEqual({k: res["data"][k] for k in ("expected", "found", "matches")}, {"expected": "e", "found": "f", "matches": False})
        self.assertIsNone(cat._page)                                        # needs no page and opens none
        rows = [json.loads(x) for x in (self.dir / "receipts.jsonl").read_text().splitlines()]
        self.assertEqual(rows[-1]["situation"], res["data"])
        self.assertTrue(all("situation" not in r for r in rows[:-1]))


class TheRules(WithATempDir):
    def talk(self, script, door="browser", check=True):
        run = ScriptedCase(self.tmp.name, script)
        tools = (BROWSER + ["hover", "look"] if door == "browser" else ["http_request"]) + ["compare", "situation"]
        rep, meta, _ = run.converse("p1/v1", "case", tools, "s", "m", kind="behaviour", door=door, from_claim=True, situation_check=check)
        return run, rep

    def texts(self, run):
        return [r["text"] if isinstance(r, dict) else r for r in run.replies]

    def test_compare_before_a_situation_is_refused_after_the_existing_refusals(self):
        run, _ = self.talk([("observe", {}), cmp("Stations", True, ref=None), cmp("Stations", True), sit(True), cmp("Stations", True)])
        self.assertEqual(self.texts(run)[1:], [NO_REF, FIRST, "ok", "noted"])
        self.assertEqual(run.n, 3)                                          # observe, the situation and the one comparison
        run, _ = self.talk([("http_request", {"method": "GET", "path": "/"}), cmp("Status", True, ref=None)], door="api")
        self.assertEqual(self.texts(run)[1], FIRST)                         # on the API door too

    def test_the_submit_rules(self):
        run, rep = self.talk([("observe", {}), HOLDS, sit(False), cmp("Stations", True), HOLDS,
                              ("submit", {"outcome": "breaks", "observed": "x"}),
                              ("submit", {"outcome": "cant_run", "observed": "The meal also had a rotating station."})])
        t = self.texts(run)
        self.assertEqual(t[1], "not accepted: " + NONE)
        self.assertEqual(t[4], "not accepted: " + NOT_IT)
        self.assertEqual(t[5], "not accepted: " + NOT_IT)
        self.assertEqual(t[6], "saved; you're done")
        self.assertEqual(rep["outcome"], "cant_run")

    def test_a_matching_situation_then_a_normal_holds_and_the_latest_counts(self):
        run, rep = self.talk([("observe", {}), sit(True), cmp("Stations", True), HOLDS])
        self.assertEqual(run.replies[-1], "saved; you're done")
        run, rep = self.talk([("observe", {}), sit(False), sit(True), cmp("Stations", True), HOLDS])
        self.assertEqual(run.replies[-1], "saved; you're done")
        run, rep = self.talk([("observe", {}), sit(True), cmp("Stations", True), sit(False), HOLDS])
        self.assertEqual(run.replies[-1], "not accepted: " + NOT_IT)

    def test_without_the_check_none_of_this_applies(self):
        run, rep = self.talk([("observe", {}), cmp("Stations", True), HOLDS], check=False)
        self.assertEqual(self.texts(run)[1:], ["noted", "saved; you're done"])

    def test_a_situation_is_neither_the_way_in_nor_a_comparison(self):
        run, rep = self.talk([sit(True), HOLDS, ("observe", {}), HOLDS])
        self.assertIn("haven't used the way in", self.texts(run)[1])
        self.assertIn("record each comparison this outcome rests on", self.texts(run)[3])


class InAWholeRun(WithATempDir):
    def test_variation_cases_get_it_and_the_older_path_and_walkthrough_do_not(self):
        run = Systems(self.tmp.name, [plan([case("v1", setting="s")])], prop=CLAIM,
                      plans_for={"walkthrough/discovery": [plan([tour(what="[tabs] a")])]})
        run.input["walkthrough"] = __import__("service.harness", fromlist=["clean_walkthrough"]).clean_walkthrough(WALKIN)
        run.work()
        self.assertIn("situation", run.given["p1/v1"])
        self.assertTrue(run.kw["p1/v1"]["situation_check"])
        self.assertIn(prompts.CASE_FROM_CLAIM + prompts.CASE_SITUATION + prompts.CASE_FROM_CLAIM_BROWSER, run.systems["p1/v1"])
        told = run.messages["p1/v1"][0]
        self.assertIn("Your case (v1), through this way in: Listing page: the add-to-cart button\n"
                      "The variation the owner approved for this case: A meal with only fixed stations.\nAdd a listing (v1).\n", told)
        self.assertNotIn("situation", run.given["walkthrough/tour"])
        self.assertFalse(run.kw["walkthrough/tour"]["situation_check"])
        self.assertNotIn(prompts.CASE_SITUATION, run.systems["walkthrough/tour"])
        self.assertNotIn("The variation the owner approved", run.messages["walkthrough/tour"][0])
        older = Systems(tempfile.mkdtemp(), [plan([case("c1")])])
        older.work()
        self.assertNotIn("situation", older.given["p1/c1"])
        self.assertNotIn(prompts.CASE_SITUATION, older.systems["p1/c1"])
        self.assertNotIn("The variation the owner approved", older.messages["p1/c1"][0])
        self.assertFalse(older.kw["p1/c1"].get("situation_check"))

    def test_the_prompt_texts(self):
        self.assertTrue(prompts.CASE_SITUATION.startswith("\nFirst, before you check anything the property says:"))
        self.assertTrue(prompts.CASE_SITUATION.endswith("never report holds or breaks for one.\n"))
        self.assertTrue(prompts.FROM_CLAIM.endswith(
            '- You may call a data tool marked "Only reads" yourself while planning, with read_tool (at most six calls), to find\n'
            "  out what exists today. Use it to make sure each case's starting state really produces the situation its variation\n"
            "  describes. What you read is for planning only: plain code runs the case's own starting steps again later and gives\n"
            "  the person carrying it out what they return, so never write a fact you read, or what a result should be, into a\n"
            "  case's steps.\n"
            "- A case tests its variation's situation and no other. If what exists today cannot give that situation and no data\n"
            '  tool can make it, plan the case with "needs". Never tell a case to go ahead in a different situation.\n'))


class TheDocument(unittest.TestCase):
    def test_a_situation_step_is_shaped_like_a_comparison(self):
        with tempfile.TemporaryDirectory() as t:
            run = Path(t) / "20261113-170000"
            unit = run / "p1" / "v1"
            unit.mkdir(parents=True)
            (unit / "clip-1.webm").write_bytes(b"webm")
            t0 = 5000.0
            rows = [{"t": t0 + 3, "tool": "situation", "args": {}, "result": "noted",
                     "situation": {"expected": "Only fixed stations.", "found": "Grill and Salad, both fixed.", "matches": True, "t": t0 + 2.5}},
                    {"t": t0 + 4, "tool": "situation", "args": {"expected": ""}, "result": "refused: expected is empty", "refused": True}]
            (unit / "receipts.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
            (run / "run.json").write_text(json.dumps({"run": "20261113-170000", "feature": "f", "env": {"url": "u"}, "properties": [
                {"id": "p1", "statement": "s", "verdict": "holds", "plan": {"from_claim": True},
                 "cases": [{"id": "v1", "door": "browser", "result": {"outcome": "holds", "clips": [{"file": "clip-1.webm", "started": t0}]}}]}]}))
            steps = rundoc.build(run)[0]["properties"][0]["cases"][0]["steps"]
        self.assertEqual(steps, [{"clip": 0, "at": 2.5, "kind": "compare", "ok": True, "words": "The starting situation",
                                  "what": "The starting situation", "expected": "Only fixed stations.", "found": "Grill and Salad, both fixed.",
                                  "matches": True, "element": None, "claim": None, "say": "", "situation": True}])


PY = shlex.quote(sys.executable)
DATA = f"""tools:
  - name: list_meals
    does: Lists today's meals and their stations.
    changes: nothing
    run: "echo '{{\\"meals\\": 2}}'"
  - name: make_meal
    does: Makes one meal.
    run: "echo '{{\\"id\\": 1}}'"
"""


class ReadTool(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name).resolve() / "repo"
        (self.repo / ".groundtruth").mkdir(parents=True)
        (self.repo / ".groundtruth" / "data.yml").write_text(DATA)
        self.env = {"url": "http://127.0.0.1:9", "repo": str(self.repo)}

    def test_in_a_discovery_unit_a_read_only_tool_runs_and_is_not_a_set_up(self):
        cat = Catalog(self.env, self.repo / "disc", discovery=True)
        self.addCleanup(cat.close)
        res = cat.run("read_tool", {"tool": "list_meals", "input": {}})
        self.assertEqual((res["isError"], res["text"], res["data"]), (False, '{"meals": 2}', {"meals": 2}))
        self.assertEqual((cat.set_up_log, cat.set_ups, cat.other_call), ([], 0, None))
        self.assertIn("does not say it only reads", cat.run("read_tool", {"tool": "make_meal"})["text"])
        self.assertIn("no data tool named 'nope'", cat.run("read_tool", {"tool": "nope"})["text"])
        self.assertIn("has no input named 'x'", cat.run("read_tool", {"tool": "list_meals", "input": {"x": 1}})["text"])
        case_unit = Catalog(self.env, self.repo / "case")
        self.addCleanup(case_unit.close)
        self.assertIn("only for planning", case_unit.run("read_tool", {"tool": "list_meals"})["text"])


READER_TOOLS = [{"name": "list_meals", "does": "Lists meals.", "takes": {}, "reads_only": True},
                {"name": "make_meal", "does": "Makes a meal.", "takes": {}, "reads_only": False}]


class ReadToolInPlanning(WithATempDir):
    def test_given_on_the_claims_path_only_when_a_read_only_tool_exists(self):
        run = ScriptedRun(self.tmp.name, [plan([case("v1", setting="s")])], prop=CLAIM, data_tools=READER_TOOLS)
        run.work()
        self.assertIn("read_tool", run.given["p1/discovery"])
        none = ScriptedRun(tempfile.mkdtemp(), [plan([case("v1", setting="s")])], prop=CLAIM)
        none.work()
        self.assertNotIn("read_tool", none.given["p1/discovery"])
        older = ScriptedRun(tempfile.mkdtemp(), [plan([case("c1")])], data_tools=READER_TOOLS)
        older.work()
        self.assertNotIn("read_tool", older.given["p1/discovery"])

    def test_a_seventh_read_is_refused(self):
        run = ScriptedCase(self.tmp.name, [("read_tool", {"tool": "list_meals", "input": {}})] * 7)
        run.converse("p1/discovery", "discovery", ["read_tool"], "s", "m")
        self.assertEqual(run.replies[:6], [{"text": "ok"}] * 6)
        self.assertEqual(run.replies[6], {"text": "refused: you have used your six reads: plan with what you know"})
        self.assertEqual(run.n, 6)


if __name__ == "__main__":
    unittest.main()
