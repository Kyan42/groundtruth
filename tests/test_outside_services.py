"""Outside services: a project lists the services its app calls and what may be done with each; a data tool may read
a service marked readable and may say it only reads; nothing changes a real outside service, and a case that would
need that is "could not run" with a plain reason. A project without the services file, and tools without the two
new fields, behave exactly as before."""
import json
import tempfile
import unittest
from pathlib import Path

from service import prompts
from service.harness import setup_problems, validate_input
from toolserver import datatools
from toolserver.catalog import Catalog
from toolserver.datatools import DataToolsError, load, load_services
from toolserver.runner import describe, registration_problem

from tests.test_starting_state_wiring import ScriptedRun, WithATempDir, case, plan

SERVICES = """services:
  - name: menu service
    what: Where menus, stations and events come from. Calling it only reads.
    allowed: read
  - name: team feedback channel
    what: The feedback form posts to the team's chat. Calling it sends a real message.
    allowed: stand-in only
  - name: payments
    what: Takes card payments.
    allowed: off
"""
READER = """  - name: read_menu
    does: Reads today's menu from the menu service.
    works_on: " Menu  Service "
    changes: Nothing
    run: "echo '{\\"dishes\\": 3}'"
"""
CHANGER = """  - name: make_order
    does: Places one order.
    run: "echo '{\\"id\\": 1}'"
"""
OLD_BLOCKED = '\nThis project declares data tools, but they cannot be used: no reset command is registered. Use "needs" for any case that needs data the app does not already have.\n'
NO_RESET = ("refused: no reset command is registered. A case that sets up data must start from clean data, "
            "or one case's data would leak into the next")


class WithARepo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name).resolve() / "repo"
        (self.repo / ".groundtruth").mkdir(parents=True)

    def write(self, data=None, services=None):
        for name, text in (("data.yml", data), ("services.yml", services)):
            f = self.repo / ".groundtruth" / name
            if text is None:
                f.unlink(missing_ok=True)
            else:
                f.write_text(text)

    def problem(self, data=None, services=None):
        self.write(data, services)
        with self.assertRaises(DataToolsError) as e:
            load(self.repo)
        return str(e.exception)


class TheServicesFile(WithARepo):
    def test_read_in_file_order_and_absent_means_none(self):
        self.assertIsNone(load_services(self.repo))
        self.write(services=SERVICES)
        self.assertEqual([(s["name"], s["allowed"]) for s in load_services(self.repo)],
                         [("menu service", "read"), ("team feedback channel", "stand-in only"), ("payments", "off")])

    def test_its_errors_are_plain_words(self):
        one = "services:\n  - name: menu\n    what: Menus.\n    allowed: read\n"
        bad = [("services:\n  - name: menu\n    what: Menus.\n    allowed: write\n", "must be exactly read, stand-in only or off"),
               ("services:\n  - name: ''\n    what: Menus.\n    allowed: read\n", "A name is 1 to 40 characters"),
               ("services:\n  - name: " + "x" * 41 + "\n    what: Menus.\n    allowed: read\n", "A name is 1 to 40 characters"),
               (one + "  - name: ' MENU '\n    what: Again.\n    allowed: off\n", "is listed twice"),
               ("services:\n  - name: menu\n    what: " + "x" * 201 + "\n    allowed: read\n", "at most 200 characters"),
               ("services:\n  - name: menu\n    allowed: read\n", "needs `what`"),
               ("services:\n  - name: menu\n    what: Menus.\n    allowed: read\n    url: x\n", "unknown key 'url'"),
               ("services:\n" + "".join(f"  - name: s{i}\n    what: W.\n    allowed: off\n" for i in range(13)), "At most 12"),
               ("services:\n  - name: menu\n    what: Menus.\n    allowed: true\n", "must be exactly read, stand-in only or off"),
               ("services: menu\n", "must be a list"), ("tools: []\n", "must start with `services:`"),
               ("services: [\n", "not valid YAML")]
        for text, words in bad:
            with self.subTest(words=words):
                self.write(services=text)
                with self.assertRaises(DataToolsError) as e:
                    load_services(self.repo)
                self.assertIn(words, str(e.exception))
                self.assertIn(".groundtruth/services.yml", str(e.exception))
        self.assertIn("services.yml is not valid YAML", self.problem(data="tools:\n" + CHANGER, services="services: [\n"))


class TheToolChecks(WithARepo):
    def test_each_check(self):
        tool = "  - name: t\n    does: D.\n    run: x\n"
        cases = [(tool + "    works_on: menu service\n    changes: nothing\n", None, "there is no .groundtruth/services.yml"),
                 (tool + "    works_on: menu\n    changes: nothing\n", SERVICES, "which is not a service listed"),
                 (tool + "    works_on: menu service\n", SERVICES,
                  't works on the menu service, which may only be read here: say "changes: nothing", or remove the tool'),
                 (tool + "    works_on: menu service\n    changes: it adds a dish\n", SERVICES,
                  't works on the menu service, which may only be read here: say "changes: nothing", or remove the tool'),
                 (tool + "    works_on: team feedback channel\n    changes: nothing\n", SERVICES,
                  "t works on the team feedback channel, which no tool may call here"),
                 (tool + "    works_on: payments\n", SERVICES, "t works on the payments, which no tool may call here"),
                 (tool + "    changes: " + "x" * 121 + "\n", None, "at most 120 characters"),
                 (tool + "    changes: ''\n", None, "not a short sentence")]
        for text, services, words in cases:
            with self.subTest(words=words):
                self.assertIn(words, self.problem(data="tools:\n" + text, services=services))

    def test_a_reader_is_spelled_as_the_services_file_spells_it_and_only_reads(self):
        self.write(data="tools:\n" + READER + CHANGER, services=SERVICES)
        got = {t["name"]: t for t in load(self.repo)}
        self.assertEqual((got["read_menu"]["reads_only"], got["read_menu"]["works_on"]), (True, "menu service"))
        self.assertEqual((got["make_order"]["reads_only"], got["make_order"]["works_on"]), (False, None))
        told = describe({"url": "u", "repo": str(self.repo)})
        self.assertEqual([(t["name"], t["reads_only"], t["works_on"]) for t in told["data_tools"]],
                         [("read_menu", True, "menu service"), ("make_order", False, None)])
        self.assertEqual(told["outside_services"][0], {"name": "menu service", "allowed": "read",
                                                       "what": "Where menus, stations and events come from. Calling it only reads."})
        self.write(data="tools:\n  - name: t\n    does: D.\n    run: x\n    changes: Nothing at all\n")
        self.assertFalse(load(self.repo)[0]["reads_only"])                 # only the single word counts


class NothingChangesWithoutTheNewParts(WithARepo):
    """The hard requirement: no services file and no new fields behave exactly as before."""

    def test_what_is_sent_planned_and_refused_is_as_before(self):
        self.write(data="tools:\n" + CHANGER)
        told = describe({"url": "u", "repo": str(self.repo)})
        self.assertNotIn("outside_services", told)
        env = validate_input({"properties": [{"id": "p1", "statement": "s"}], "env": told})["env"]
        self.assertNotIn("outside_services", env)
        self.assertEqual((env["data_tools"], env["data_tools_blocked"]), ([], True))
        self.assertEqual(prompts.data_tools_note(env), OLD_BLOCKED)        # the blocked message, word for word
        self.assertNotIn("Outside services", prompts.discovery_message({"id": "p1", "statement": "s"}, env, []))
        with_reset = validate_input({"properties": [{"id": "p1", "statement": "s"}], "env": {**told, "has_reset": True}})["env"]
        self.assertEqual(prompts.data_tools_note(with_reset),
                         "\nData tools this project declares (plain code runs them before a case starts; you cannot call them):\n"
                         "- make_order: Places one order. Takes: nothing.\n")
        self.assertEqual(datatools.public(load(self.repo))[0].keys(), {"name", "does", "takes"})
        cat = Catalog({"url": "http://127.0.0.1:9", "repo": str(self.repo)}, self.repo / "unit")
        self.assertEqual(cat.run("set_up", {"tool": "make_order"})["text"], NO_RESET)
        self.assertEqual(cat.run("data_tools", {})["data"], [{"name": "make_order", "does": "Places one order.", "takes": {}}])

    def test_a_needs_without_the_new_fields_is_the_request_it_always_was(self):
        needs = {"state": "An order.", "tool": {"name": "make_order", "does": "Makes one."}}
        self.assertIsNone(setup_problems(case("c1", needs=needs), {"data_tools": []}))


class ReadOnlyToolsNeedNoReset(WithARepo):
    def test_a_reader_runs_without_a_reset_and_a_changer_is_refused_in_the_same_project(self):
        self.write(data="tools:\n" + READER + CHANGER, services=SERVICES)
        cat = Catalog({"url": "http://127.0.0.1:9", "repo": str(self.repo)}, self.repo / "unit")
        self.addCleanup(cat.close)
        res = cat.run("set_up", {"tool": "read_menu"})
        self.assertFalse(res["isError"], res)
        self.assertEqual(res["data"], {"dishes": 3})
        self.assertEqual(cat.run("set_up", {"tool": "make_order"})["text"], NO_RESET)
        # still before anything else, and still counted toward the six
        cat2 = Catalog({"url": "http://127.0.0.1:9", "repo": str(self.repo)}, self.repo / "unit2")
        cat2.run("wait", {"ms": 0})
        self.assertIn("must come before anything else", cat2.run("set_up", {"tool": "read_menu"})["text"])
        cat2.close()
        cat3 = Catalog({"url": "http://127.0.0.1:9", "repo": str(self.repo)}, self.repo / "unit3")
        for _ in range(6):
            self.assertFalse(cat3.run("set_up", {"tool": "read_menu"})["isError"])
        self.assertIn("already made 6 set_up calls", cat3.run("set_up", {"tool": "read_menu"})["text"])
        cat3.close()

    def test_the_planner_sees_only_the_readers_and_is_told_the_rest_are_blocked(self):
        self.write(data="tools:\n" + READER + CHANGER, services=SERVICES)
        env = validate_input({"properties": [{"id": "p1", "statement": "s"}], "env": describe({"url": "u", "repo": str(self.repo)})})["env"]
        self.assertEqual([t["name"] for t in env["data_tools"]], ["read_menu"])
        self.assertTrue(env["data_tools_blocked"])
        self.assertEqual(prompts.data_tools_note(env),
                         "\nData tools this project declares (plain code runs them before a case starts; you cannot call them):\n"
                         "- read_menu: Reads today's menu from the menu service. Takes: nothing. Only reads.\n"
                         "Other data tools this project declares cannot be used here, because no reset command is registered.\n")
        with_reset = validate_input({"properties": [{"id": "p1", "statement": "s"}],
                                     "env": {**describe({"url": "u", "repo": str(self.repo)}), "has_reset": True}})["env"]
        self.assertEqual(([t["name"] for t in with_reset["data_tools"]], with_reset["data_tools_blocked"]), (["read_menu", "make_order"], False))
        self.assertNotIn("Other data tools", prompts.data_tools_note(with_reset))


ENV_SERVICES = [{"name": "menu service", "what": "Where menus come from.", "allowed": "read"},
                {"name": "team feedback channel", "what": "Posts to the team's chat.", "allowed": "stand-in only"},
                {"name": "payments", "what": "Takes card payments.", "allowed": "off"}]
LINES = ("- menu service: Where menus come from. It may be read, and nothing may change it.\n"
         "- team feedback channel: Posts to the team's chat. It is never called for real here.\n"
         "- payments: Takes card payments. It is never called for real here.\n")
PLAN_BLOCK = ("\nOutside services this app depends on, and what may be done with each here:\n" + LINES +
              'If a case needs one of these to be in a state it is not in today, no data tool can do that here. Plan the case with\n'
              '"needs" all the same, and in its tool give "works_on": the service\'s name exactly as written above, and "changes":\n'
              'true. Plain code then reports that the case could not be run. For a tool that would only read a service, give\n'
              '"changes": false. For a tool that would work on the app\'s own data, "works_on" is "the app\'s own data".\n')


def needing(cid, works_on=None, changes=None):
    tool = {"name": "make_menu", "does": "Puts a dish on today's menu."}
    if works_on is not None:
        tool["works_on"] = works_on
    if changes is not None:
        tool["changes"] = changes
    return case(cid, needs={"state": "A menu with a vegan dish.", "tool": tool})


class ThePlanCheck(unittest.TestCase):
    def test_works_on_is_one_of_the_names_given_or_the_app_s_own_data(self):
        env = {"data_tools": [], "outside_services": ENV_SERVICES}
        for ok in ("menu service", "payments", "the app's own data"):
            self.assertIsNone(setup_problems(needing("c1", ok, True), env), ok)
        why = setup_problems(needing("c1", "Menu Service", True), env)
        self.assertEqual(why, 'case c1: the tool it needs has "works_on": "Menu Service". It must be exactly one of: '
                              '"menu service", "team feedback channel", "payments", "the app\'s own data"')
        self.assertIn("must be exactly one of: \"the app's own data\"", setup_problems(needing("c1", "menu service"), {"data_tools": []}))
        self.assertIn('"changes" that is not true or false', setup_problems(needing("c1", "payments", "yes"), env))


class WhenTheCaseComesUp(WithATempDir):
    def run_cases(self, *cases, services=ENV_SERVICES):
        run = ScriptedRun(self.tmp.name, [plan(list(cases))], **({"outside_services": services} if services else {}))
        run.work()
        self.assertNotIn("error", run.report, run.report.get("error"))
        return run

    def test_the_three_outcomes(self):
        run = self.run_cases(needing("readit", "menu service", True), needing("chat", "team feedback channel", True),
                             needing("pay", "payments", False), needing("look", "menu service", False),
                             needing("own", "the app's own data", True), needing("plain"))
        r = {c: {k: v for k, v in run.result(c)["result"].items() if k not in ("role", "baseline", "meta")}
             for c in ("readit", "chat", "pay", "look", "own", "plain")}
        self.assertEqual(r["readit"], {"outcome": "cant_run", "decided_by_harness": True, "observed":
                         "This case was not run: it needs the menu service to be in a state it is not in today. The menu service can "
                         "only be read here, so that would take a stand-in for it, and this project has none."})
        for c, name in (("chat", "team feedback channel"), ("pay", "payments")):
            self.assertEqual(r[c], {"outcome": "cant_run", "decided_by_harness": True, "observed":
                             f"This case was not run: it depends on the {name}, which is never called for real here. That would take a "
                             "stand-in for it, and this project has none."})
        for c in ("readit", "chat", "pay"):
            self.assertNotIn("data_request", r[c])
            self.assertEqual(run.timeline(f"p1/{c}"), ["reset", "saved"])
        self.assertEqual(r["look"]["data_request"]["tool"], {"name": "make_menu", "does": "Puts a dish on today's menu.", "takes": {},
                                                             "works_on": "menu service", "changes": False})
        self.assertEqual(r["own"]["data_request"]["tool"]["works_on"], "the app's own data")
        self.assertEqual(r["plain"]["data_request"]["tool"], {"name": "make_menu", "does": "Puts a dish on today's menu.", "takes": {}})
        self.assertTrue(r["look"]["observed"].startswith("This case was not run: no declared data tool can make its starting state."))


class WhatThePlannerIsTold(unittest.TestCase):
    def env(self, **extra):
        return {"url": "http://x", "data_tools": [], "outside_services": ENV_SERVICES, **extra}

    def test_both_planning_paths_and_a_from_claim_case(self):
        msg = prompts.discovery_message({"id": "p1", "statement": "s"}, self.env(), [])
        self.assertTrue(msg.endswith(PLAN_BLOCK + "\nPlan the cases."), msg[-900:])
        planner = prompts.planner_message({"id": "p1", "statement": "s"}, self.env(),
                                          {"applies_to": "a", "action": "b", "compares": "c", "kind": "behaviour"}, [], [], [])
        self.assertTrue(planner.endswith(PLAN_BLOCK + "\nPlan the cases."))
        told = prompts.case_message({"id": "p1", "statement": "s"}, {"applies_to": "a", "action": "b", "compares": "c"}, case("v1"),
                                    {"where": "w"}, self.env(has_reset=True), services=True)
        self.assertTrue(told.endswith("\nOutside services this app depends on, and what may be done with each here:\n" + LINES
                                      + "\nCarry out this case."))
        older = prompts.case_message({"id": "p1", "statement": "s"}, {"applies_to": "a", "action": "b", "compares": "c"}, case("c1"),
                                     {"where": "w"}, self.env(has_reset=True))
        self.assertNotIn("Outside services", older)

    def test_nothing_new_without_outside_services(self):
        env = {"url": "http://x", "data_tools": []}
        for msg in (prompts.discovery_message({"id": "p1", "statement": "s"}, env, []),
                    prompts.case_message({"id": "p1", "statement": "s"}, {"applies_to": "a", "action": "b", "compares": "c"}, case("v1"),
                                         {"where": "w"}, env, services=True)):
            self.assertNotIn("Outside services", msg)
        self.assertNotIn("outside_services", validate_input({"properties": [{"id": "p1", "statement": "s"}], "env": env})["env"])

    def test_the_service_bounds_what_it_is_sent(self):
        base = {"properties": [{"id": "p1", "statement": "s"}]}
        got = validate_input({**base, "env": {"url": "u", "outside_services": ENV_SERVICES}})["env"]["outside_services"]
        self.assertEqual([s["allowed"] for s in got], ["read", "stand-in only", "off"])
        for bad in ("x", [{"name": "m", "what": "w", "allowed": "write"}], [{"name": "", "what": "w", "allowed": "off"}],
                    [{"name": "m", "what": "w", "allowed": "off"}] * 13):
            with self.subTest(bad=str(bad)[:40]), self.assertRaises(ValueError):
                validate_input({**base, "env": {"url": "u", "outside_services": bad}})

    def test_a_from_claim_case_gets_the_lines_in_a_whole_run(self):
        with tempfile.TemporaryDirectory() as t:
            run = ScriptedRun(t, [plan([case("v1", setting="s")])], prop={"id": "p1", "statement": "s", "variations": [{"id": "v1", "what": "w"}]},
                              outside_services=ENV_SERVICES)
            run.work()
            self.assertIn(LINES + "\nCarry out this case.", run.messages["p1/v1"][0])
            self.assertIn(PLAN_BLOCK, run.messages["p1/discovery"][0])
            plain = ScriptedRun(tempfile.mkdtemp(), [plan([case("c1")])], outside_services=ENV_SERVICES)
            plain.work()
            self.assertNotIn("Outside services", plain.messages["p1/c1"][0])


if __name__ == "__main__":
    unittest.main()
