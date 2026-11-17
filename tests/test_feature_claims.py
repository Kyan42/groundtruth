"""On the claims path, each claim is planned and carried out knowing the feature's other claims (how the app is meant
to work), so a starting state is not built against a rule the owner already approved. The older path never sees them."""
import json
import tempfile
import unittest
from pathlib import Path

from service import prompts
from service.harness import clean_feature_claims, validate_input
from toolserver.runner import run_verify

from tests.test_agent_commands import App, FakeService, make_repo, serve
from tests.test_starting_state_wiring import ScriptedRun, WithATempDir, case, plan
from tests.test_walkthrough import WALKIN, Systems, tour

FEATURE_CLAIMS = [{"id": "p1", "title": "Squares per test", "statement": "Each test has one square."},
                  {"id": "latest", "title": "", "statement": "For each feature, the tests named in that feature's latest run are listed."},
                  {"id": "colour", "title": "Colour of a square", "statement": "A square is green when the test held."}]
CLAIM = {"id": "p1", "statement": "Each test has one square.", "variations": [{"id": "v1", "what": "A test that held 11 days ago."}]}


class WhatTheServiceAccepts(unittest.TestCase):
    def test_bounded_like_properties(self):
        env = {"url": "http://x"}
        self.assertEqual(validate_input({"properties": [CLAIM], "env": env})["feature_claims"], [])
        got = validate_input({"properties": [CLAIM], "env": env, "feature_claims": FEATURE_CLAIMS})["feature_claims"]
        self.assertEqual([c["id"] for c in got], ["p1", "latest", "colour"])
        self.assertEqual(clean_feature_claims([{"id": "a", "title": " T ", "statement": " s "}]), [{"id": "a", "title": "T", "statement": "s"}])
        for bad in ("x", [{"id": "a b", "statement": "s"}], [{"id": "a", "statement": ""}], [{"id": "a", "statement": "s", "title": 3}],
                    [{"id": f"c{i}", "statement": "s"} for i in range(41)]):
            with self.subTest(bad=str(bad)[:40]), self.assertRaises(ValueError):
                clean_feature_claims(bad)


class InTheMessages(WithATempDir):
    LIST = ("- latest: For each feature, the tests named in that feature's latest run are listed.\n"
            "- Colour of a square: A square is green when the test held.\n")

    def test_the_planner_and_the_case_are_told_the_other_claims(self):
        run = Systems(self.tmp.name, [plan([case("v1", setting="s")])], prop=CLAIM)
        run.input["feature_claims"] = clean_feature_claims(FEATURE_CLAIMS)
        run.work()
        planner = run.messages["p1/discovery"][0]
        self.assertIn("\n" + prompts.OTHER_CLAIMS_PLAN + self.LIST, planner)
        self.assertNotIn("Squares per test", planner)                  # not the claim being worked on
        told = run.messages["p1/v1"][0]
        self.assertTrue(told.endswith("\n" + prompts.OTHER_CLAIMS_CASE + self.LIST + "\nCarry out this case."), told[-400:])

    def test_the_older_path_and_the_walkthrough_are_not(self):
        run = Systems(self.tmp.name, [plan([case("c1")])])
        run.input["feature_claims"] = clean_feature_claims(FEATURE_CLAIMS)
        run.work()
        for unit in ("p1/discovery", "p1/c1"):
            self.assertNotIn("other claims", run.messages[unit][0], unit)
        walk = Systems(self.tmp.name, [plan([case("v1", setting="s")])], prop=CLAIM,
                       plans_for={"walkthrough/discovery": [plan([tour(what="[tabs] a")])]})
        walk.input["feature_claims"] = clean_feature_claims(FEATURE_CLAIMS)
        walk.input["walkthrough"] = validate_input({"properties": [], "env": {"url": "u"}, "walkthrough": WALKIN})["walkthrough"]
        walk.work()
        for unit in ("walkthrough/discovery", "walkthrough/tour"):
            self.assertNotIn("other claims", walk.messages[unit][0], unit)

    def test_the_cant_run_paragraph_closes_case_from_claim(self):
        self.assertTrue(prompts.CASE_FROM_CLAIM.endswith(
            'the latest one counts.\n\nIf what was set up does not actually produce the situation this variation describes, because of how the app is\n'
            'meant to work, the case has not been run: the outcome is "cant_run", and observed says what is missing from the\n'
            'starting state. "breaks" is only for the property being false in the situation the variation describes.\n'))


class WhatTheRunnerSends(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = make_repo(Path(self.tmp.name))
        self.app, app_url = serve(App)
        self.svc, self.svc_url = serve(FakeService)
        for s in (self.app, self.svc):
            self.addCleanup(s.server_close)
            self.addCleanup(s.shutdown)
        (self.repo / ".groundtruth" / "env.json").write_text(json.dumps({"url": app_url}))

    def test_every_claim_whatever_only_says_and_none_on_the_older_path(self):
        run_verify(self.repo, "tabs", self.svc_url, say=lambda *a: None, only="tabs")
        sent = FakeService.started[-1]["input"]
        self.assertEqual(sent["feature_claims"], [
            {"id": "tabs", "title": "Right tests in each tab",
             "statement": "New has one card for each test written for that run. No test is missing or in both."},
            {"id": "result", "title": "", "statement": "A test shows as passing only if none of its variations broke."}])
        import time
        time.sleep(1.1)                                     # a new run folder
        run_verify(self.repo, "tabs", self.svc_url, say=lambda *a: None, only="result")
        self.assertNotIn("feature_claims", FakeService.started[-1]["input"])


if __name__ == "__main__":
    unittest.main()
