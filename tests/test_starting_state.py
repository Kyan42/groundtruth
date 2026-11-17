"""A case's starting state (DATATOOLS.md), on the verifier-service side: what a plan may ask for, how plain code
carries it out before the case, and what the case and the results are told."""
import unittest

from service import prompts
from service.harness import clean_setup, clean_tools, plan_problems, run_setup, setup_problems, validate_input

TOOLS = [{"name": "make_listing", "does": "Creates one listing.",
          "takes": {"title": {"type": "string", "optional": False, "does": ""},
                    "price_cents": {"type": "integer", "optional": False, "does": "The price in cents."},
                    "sold_out": {"type": "boolean", "optional": True, "does": ""}}}]
ENV = {"url": "http://127.0.0.1:1", "has_reset": True, "data_tools": TOOLS, "data_tools_blocked": False}


def step(**over):
    s = {"tool": "make_listing", "input": {"title": "Grand piano", "price_cents": 120000},
         "because": "No listing in the reset data costs $1,000 or more, and the pages cannot create one."}
    s.update(over)
    return s


def case(**extra):
    return {"id": "c1", "entry_point": "e1", "what": "Add the listing made for this case.", **extra}


class WhatAPlanMayAskFor(unittest.TestCase):
    def test_a_case_with_no_starting_state_is_fine(self):
        self.assertIsNone(setup_problems(case(), ENV))
        self.assertIsNone(setup_problems(case(starts_from=[], needs=None), ENV))

    def test_a_good_starting_state_is_accepted(self):
        self.assertIsNone(setup_problems(case(starts_from=[step()]), ENV))
        self.assertIsNone(setup_problems(case(starts_from=[step(input={"title": "x", "price_cents": 5, "sold_out": True})]), ENV))

    def test_each_step_must_fit_a_declared_tool(self):
        bad = [
            (step(tool="make_order"), "no data tool named make_order"),
            (step(input={"title": "x"}), "needs the input price_cents"),
            (step(input={"title": "x", "price_cents": "1200"}), "must be a integer"),
            (step(input={"title": "x", "price_cents": True}), "must be a integer"),          # yes/no is not a number
            (step(input={"title": "x", "price_cents": 5, "colour": "red"}), "takes no input named colour"),
            (step(input={"title": "x" * 501, "price_cents": 5}), "must be a string"),
            (step(input=["x", 5]), "input is an object"),
        ]
        for s, word in bad:
            with self.subTest(word=word):
                self.assertIn(word, setup_problems(case(starts_from=[s]), ENV))

    def test_every_step_says_why_the_pages_could_not_do_it(self):
        self.assertIn("because", setup_problems(case(starts_from=[step(because="  ")]), ENV))

    def test_at_most_six_steps_and_never_both_kinds(self):
        self.assertIn("at most 6", setup_problems(case(starts_from=[step()] * 7), ENV))
        both = case(starts_from=[step()], needs={"state": "s", "tool": {"name": "t", "does": "d"}})
        self.assertIn("give one", setup_problems(both, ENV))

    def test_a_request_names_the_missing_tool_and_never_one_that_exists(self):
        ok = {"state": "An order placed more than 30 days ago.", "tool": {"name": "make_order", "does": "Creates an order with a given date.",
                                                                          "takes": {"placed_at": {"type": "string"}}}}
        self.assertIsNone(setup_problems(case(needs=ok), ENV))
        self.assertIn('"needs" is', setup_problems(case(needs={"state": "x"}), ENV))
        have = {"state": "A listing.", "tool": {"name": "make_listing", "does": "Creates a listing."}}
        self.assertIn("already declared", setup_problems(case(needs=have), ENV))

    def test_without_usable_tools_only_a_request_is_possible(self):
        none = {**ENV, "data_tools": []}
        self.assertIn('"needs" instead', setup_problems(case(starts_from=[step()]), none))
        blocked = {**none, "data_tools_blocked": True}
        self.assertIn("no reset command", setup_problems(case(starts_from=[step()]), blocked))

    def test_the_single_conversation_path_applies_the_same_check(self):
        # plan_problems alone does not look at starting states; Run.submit applies setup_problems on both paths.
        # This guards the pieces it relies on: a plan's cases keep their starting state through clean_setup.
        kept = clean_setup(case(starts_from=[step()]))
        self.assertEqual(kept["starts_from"][0]["tool"], "make_listing")
        self.assertEqual(set(kept["starts_from"][0]), {"tool", "input", "because"})
        asked = clean_setup(case(needs={"state": " An old order. ", "tool": {"name": "make_order", "does": "Creates one.", "takes": "junk"}}))
        self.assertEqual(asked["needs"], {"state": "An old order.", "tool": {"name": "make_order", "does": "Creates one.", "takes": {}}})
        self.assertEqual(clean_setup(case()), {})
        self.assertTrue(callable(plan_problems))


class WhatTheServiceIsTold(unittest.TestCase):
    def run_input(self, **env):
        return validate_input({"properties": [{"id": "p1", "statement": "s"}], "env": {"url": "http://x", **env}})["env"]

    def test_tools_are_usable_only_with_a_reset_command(self):
        with_reset = self.run_input(has_reset=True, data_tools=TOOLS + [{"name": "bad name!", "does": "x"}, "junk"])
        self.assertEqual([t["name"] for t in with_reset["data_tools"]], ["make_listing"])
        self.assertFalse(with_reset["data_tools_blocked"])
        without = self.run_input(has_reset=False, data_tools=TOOLS)
        self.assertEqual((without["data_tools"], without["data_tools_blocked"]), ([], True))
        self.assertEqual(self.run_input(has_reset=True)["data_tools"], [])

    def test_a_command_never_reaches_the_planner(self):
        cleaned = clean_tools([{**TOOLS[0], "run": "node secret.mjs"}])
        self.assertNotIn("run", cleaned[0])
        self.assertNotIn("secret", prompts.data_tools_note({"data_tools": cleaned}))

    def test_nothing_a_tool_says_is_cut_short(self):
        # Found on the first trial: an input's description of 300 characters reached the planner cut at 200, hiding two
        # of the words it could have used. The tool server holds the file to 400; the service keeps all 400.
        long = "x" * 399 + "."
        kept = clean_tools([{"name": "make_run", "does": long, "takes": {"verdicts": {"type": "string", "does": long}}}])[0]
        self.assertEqual((kept["does"], kept["takes"]["verdicts"]["does"]), (long, long))
        self.assertIn(long, prompts.data_tools_note({"data_tools": [kept]}))

    def test_the_planner_is_told_the_tools_or_that_there_are_none(self):
        self.assertIn("make_listing: Creates one listing. Takes: title (string), price_cents (integer): The price in cents., sold_out (boolean, optional).",
                      prompts.data_tools_note(ENV))
        self.assertIn("declares no data tools", prompts.data_tools_note({"data_tools": []}))
        self.assertIn("no reset command is registered", prompts.data_tools_note({"data_tools": [], "data_tools_blocked": True}))


class CarryingItOut(unittest.TestCase):
    def test_steps_run_in_order_and_keep_what_each_returned(self):
        calls = []

        def call(tool, args):
            calls.append((tool, args))
            return {"isError": False, "text": "ok", "data": {"id": len(calls)}}

        done, problem = run_setup(case(starts_from=[step(), step(input={"title": "b", "price_cents": 1})]), call)
        self.assertIsNone(problem)
        self.assertEqual([c[0] for c in calls], ["set_up", "set_up"])
        self.assertEqual(calls[0][1], {"tool": "make_listing", "input": {"title": "Grand piano", "price_cents": 120000}})
        self.assertEqual([d["returned"] for d in done], [{"id": 1}, {"id": 2}])
        self.assertTrue(all(d["because"] for d in done))

    def test_the_first_failure_stops_the_rest_and_names_the_step(self):
        calls = []

        def call(tool, args):
            calls.append(args)
            return {"isError": True, "text": "error: make_listing failed (exit 4)", "data": None}

        done, problem = run_setup(case(starts_from=[step(), step()]), call)
        self.assertEqual(len(calls), 1)
        self.assertIn("step 1 (make_listing) failed", problem)
        self.assertEqual(done[0]["error"], "error: make_listing failed (exit 4)")

    def test_no_steps_means_nothing_is_called(self):
        self.assertEqual(run_setup(case(), lambda *a: self.fail("nothing should be called")), ([], None))

    def test_the_case_is_told_what_was_set_up_as_facts(self):
        msg = prompts.case_message({"id": "p1", "statement": "s", "decisions": []},
                                   {"applies_to": "a", "action": "b", "compares": "c"},
                                   case(), {"where": "Listing page"}, {"url": "http://x", "has_reset": True}, None,
                                   [{"tool": "make_listing", "input": {"price_cents": 120000}, "returned": {"id": "l9", "page": "/listings/l9"}}])
        self.assertIn("Before you began, and after the reset, this was set up", msg)
        self.assertIn('make_listing with {"price_cents": 120000} returned {"id": "l9", "page": "/listings/l9"}', msg)
        self.assertNotIn("this was set up", prompts.case_message({"id": "p1", "statement": "s", "decisions": []},
                                                                 {"applies_to": "a", "action": "b", "compares": "c"},
                                                                 case(), {"where": "w"}, {"url": "http://x", "has_reset": True}))

    def test_a_case_conversation_never_gets_a_data_tool(self):
        from service.harness import case_tools, discovery_tools
        for kind in ("behaviour", "measurement", "code"):
            for door in ("browser", "api", "code"):
                tools = case_tools(kind, door, {"has_pid": True, "has_repo": True})
                self.assertFalse({"set_up", "data_tools"} & set(tools), (kind, door))
        self.assertFalse({"set_up", "data_tools"} & set(discovery_tools({"has_repo": True})))


if __name__ == "__main__":
    unittest.main()
