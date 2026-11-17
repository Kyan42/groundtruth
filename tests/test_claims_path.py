"""A claim written before the code: the owner approved its variations, and the verifier plans exactly one case for
each without reading the code. The model is a script; submit, the plan checks and the work loop are the real code."""
import unittest
from unittest import mock

from service import prompts
from service.harness import SOURCE, claim_parts, validate_input, variation_problems
from toolserver.runner import ServiceError
from toolserver.runner import claim_parts as file_claim_parts

from tests.test_starting_state_wiring import STEP, ScriptedRun, WithATempDir, case, plan

CLAIM = {"id": "p1", "statement": "New has one card for each test written for the run.",
         "checked_against": "The record of which tests were written for the run and which were rerun.",
         "variations": [{"id": "only-new", "what": "A run with only new tests."},
                        {"id": "only-reruns", "what": "A run with only rerun tests."},
                        {"id": "both", "what": "A run with both."}]}


def cases(*ids, **extra):
    return [case(i, **{"setting": "A project with a few tests.", **extra}) for i in ids]


class FromAFeatureFile(unittest.TestCase):
    def test_variations_may_be_sentences_or_have_ids(self):
        got = file_claim_parts({"id": "p1", "checked_against": " The record. ",
                                "variations": ["A run with only new tests.", {"id": "both", "what": " A run with both. "}]}, "f.yml")
        self.assertEqual(got, {"checked_against": "The record.",
                               "variations": [{"id": "v1", "what": "A run with only new tests."}, {"id": "both", "what": "A run with both."}]})
        self.assertEqual(file_claim_parts({"id": "p1"}, "f.yml"), {})          # an ordinary property is unchanged
        self.assertEqual(file_claim_parts({"id": "p1", "title": " Right tests in each tab "}, "f.yml"), {"title": "Right tests in each tab"})
        self.assertEqual(file_claim_parts({"id": "p1", "your_words": " should show you the video "}, "f.yml"), {"your_words": "should show you the video"})

    def test_problems_are_said_in_plain_words_before_anything_is_sent(self):
        bad = [({"variations": []}, "1 to 12 variations"), ({"variations": "one"}, "1 to 12 variations"),
               ({"variations": ["ok", ""]}, "variation 2 is not a sentence"),
               ({"variations": [{"id": "a", "what": "x"}, {"id": "a", "what": "y"}]}, "has the id 'a'"),
               ({"variations": [{"id": "discovery", "what": "x"}]}, "has the id 'discovery'"),
               ({"checked_against": ["a list"]}, "not a sentence"),
               ({"title": "x" * 61}, "not a short phrase"), ({"title": ""}, "not a short phrase"), ({"title": 5}, "not a short phrase"),
               ({"your_words": "x" * 301}, "not one or two sentences"), ({"your_words": ["a"]}, "not one or two sentences")]
        for extra, words in bad:
            with self.subTest(words=words), self.assertRaises(ServiceError) as e:
                file_claim_parts({"id": "p1", **extra}, "f.yml")
            self.assertIn(words, str(e.exception))


class WhatTheServiceAccepts(unittest.TestCase):
    def test_the_parts_survive_and_are_checked_again(self):
        kept = validate_input({"properties": [CLAIM], "env": {"url": "http://x"}})["properties"][0]
        self.assertEqual((kept["checked_against"], [v["id"] for v in kept["variations"]]),
                         (CLAIM["checked_against"], ["only-new", "only-reruns", "both"]))
        plain = validate_input({"properties": [{"id": "p1", "statement": "s"}], "env": {"url": "http://x"}})["properties"][0]
        self.assertEqual((plain["checked_against"], plain["variations"]), ("", []))
        for bad in ([], [{"id": "a b", "what": "x"}], [{"id": "a", "what": ""}], [{"id": "a", "what": "x"}] * 2, ["a sentence"]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                claim_parts({"id": "p1", "variations": bad})

    def test_a_plan_has_exactly_one_case_per_variation(self):
        vs = CLAIM["variations"]
        self.assertIsNone(variation_problems(plan(cases("only-new", "only-reruns", "both")), vs))
        self.assertIn("no case for variation only-reruns", variation_problems(plan(cases("only-new", "both")), vs))
        self.assertIn("case extra is not one of the approved variations", variation_problems(plan(cases("only-new", "only-reruns", "both", "extra")), vs))
        self.assertIn("tested through the running app", variation_problems({**plan(cases("only-new", "only-reruns", "both")), "kind": "code"}, vs))

    def test_the_planner_and_the_case_are_told_the_claim(self):
        msg = prompts.discovery_message(CLAIM | {"decisions": []}, {"url": "http://x", "has_repo": False, "data_tools": []}, [])
        self.assertIn("Checked against (where the right answer comes from):\nThe record of which tests", msg)
        self.assertIn("- only-reruns: A run with only rerun tests.", msg)
        self.assertIn("the code cannot be read", msg)
        told = prompts.case_message(CLAIM | {"decisions": []}, {"applies_to": "a", "action": "b", "compares": "c"}, case("both"),
                                    {"where": "The run page"}, {"url": "http://x", "has_reset": True})
        self.assertIn("Checked against (where the right answer comes from): The record of which tests", told)
        plain = prompts.case_message({"id": "p1", "statement": "s", "decisions": []}, {"applies_to": "a", "action": "b", "compares": "c"},
                                     case("c1"), {"where": "w"}, {"url": "http://x", "has_reset": True})
        self.assertNotIn("Checked against", plain)


class PlannedWithoutTheCode(WithATempDir):
    """A repo is registered (data tools need it), yet nothing on this path reads the code."""

    def run_claim(self, plans, **kw):
        tracer = mock.Mock(side_effect=AssertionError("the code search must not run for a claim with variations"))
        with mock.patch("service.harness.Tracer", tracer):
            run = super().run_it(plans, prop=CLAIM, has_repo=True, **kw)
        self.assertFalse(tracer.called)
        return run

    def test_no_code_search_no_code_tool_and_no_code_locations_asked_for(self):
        run = self.run_claim([plan(cases("only-new", "only-reruns", "both"))])        # this plan has no code_refs
        self.assertEqual(run.replies, ["saved; you're done"])
        self.assertNotIn("anchor", run.roles)
        self.assertNotIn("planner", run.roles)
        self.assertFalse(set(SOURCE) & run.given["p1/discovery"], run.given["p1/discovery"])
        for unit, tools in run.given.items():
            self.assertFalse(set(SOURCE) & tools, unit)
        self.assertIn("written and approved before any code existed", run.system)
        self.assertIn("- both: A run with both.", run.messages["p1/discovery"][0])
        prop = run.report["properties"][0]
        self.assertTrue(prop["plan"]["from_claim"])
        self.assertIsNone(prop["trace"])
        self.assertEqual(prop["checked_against"], CLAIM["checked_against"])
        self.assertEqual([c["id"] for c in prop["cases"]], ["only-new", "only-reruns", "both"])       # one row per approved variation
        self.assertEqual(prop["verdict"], "holds")

    def test_a_plan_that_drops_or_adds_a_variation_is_turned_back(self):
        run = self.run_claim([plan(cases("only-new", "both")), plan(cases("only-new", "only-reruns", "both", "mine")),
                              plan(cases("only-new", "only-reruns", "both"))])
        self.assertIn("no case for variation only-reruns", run.replies[0])
        self.assertIn("case mine is not one of the approved variations", run.replies[1])
        self.assertEqual(run.replies[2], "saved; you're done")

    def test_each_variation_gets_its_starting_state_and_is_told_the_claim(self):
        run = self.run_claim([plan([case("only-new", starts_from=[STEP], setting="A run with new tests."),
                                    case("only-reruns", starts_from=[STEP], setting="A run with reruns."),
                                    case("both", setting="A run with both.", needs={"state": "A run with both kinds.", "tool": {"name": "make_run", "does": "Makes a run."}})])])
        self.assertEqual(run.timeline("p1/only-new"), ["reset", "set_up", "converse", "saved"])
        self.assertEqual(run.timeline("p1/both"), ["reset", "saved"])                 # asked for a missing tool: not started
        self.assertIn("Checked against (where the right answer comes from): The record", run.messages["p1/only-new"][0])
        self.assertIn("this was set up", run.messages["p1/only-new"][0])
        self.assertEqual(run.report["properties"][0]["verdict"], "cant_tell")

    def test_an_ordinary_property_still_takes_the_code_path(self):
        from service.trace import TraceError
        tracer = mock.Mock()
        tracer.return_value.run_trace.side_effect = TraceError("stopped by the test")
        tracer.return_value.cost = 0.0
        with mock.patch("service.harness.Tracer", tracer):
            run = ScriptedRun(self.tmp.name, [plan(cases("c1"))], has_repo=True)
            run.work()
        self.assertIn("anchor", run.roles)                    # no variations: the search's starting point is still asked for
        self.assertTrue(tracer.return_value.run_trace.called)


if __name__ == "__main__":
    unittest.main()
