"""Where the starting-state pieces join in the harness: a bad plan is turned back on both planning paths, an accepted
starting state reaches the case the work loop reads, and for every try the order is reset, set-up, then the conversation.

The model is replaced by a script; everything else (submit, the plan clean-up, the work loop) is the real code."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from service.harness import Run

TOOLS = [{"name": "make_listing", "does": "Creates one listing.",
          "takes": {"title": {"type": "string"}, "price_cents": {"type": "integer"}}}]
STEP = {"tool": "make_listing", "input": {"title": "Grand piano", "price_cents": 120000},
        "because": "No listing in the reset data costs $1,000 or more, and the pages cannot create one."}
WANT = {"state": "An order placed more than 30 days ago.", "tool": {"name": "make_order", "does": "Creates an order with a given date."}}
MADE = {"id": "u1", "page": "/listings/u1"}


def plan(cases):
    return {"applies_to": "any listing", "action": "adding it to the cart", "compares": "the subtotal and the listed price",
            "how_found": "Looked at the pages.", "not_covered": "", "kind": "behaviour",
            "entry_points": [{"id": "e1", "door": "browser", "where": "Listing page: the add-to-cart button"}], "cases": cases}


def case(cid, **extra):
    return {"id": cid, "entry_point": "e1", "what": f"Add a listing ({cid}).", **extra}


class ScriptedRun(Run):
    """A run whose model and tool server are scripts. `plans` are submitted in order by the planning conversation;
    `silent` lists (unit, try) pairs where a case conversation ends without a result; `fail_set_up` makes set_up fail."""

    def __init__(self, tmp, plans, silent=(), fail_set_up=False, prop=None, **env):
        super().__init__({"properties": [prop or {"id": "p1", "statement": "Adding a listing raises the subtotal by its price."}],
                          "env": {"url": "http://127.0.0.1:1", "has_reset": True, "data_tools": TOOLS, **env}},
                         "scripted", Path(tmp), "http://127.0.0.1:0")
        self.plans, self.silent, self.fail_set_up = list(plans), set(silent), fail_set_up
        self.log, self.replies, self.messages, self.given, self.tries = [], [], {}, {}, {}

    def call_local(self, tool, args, timeout=None):
        self.log.append((tool, args.get("claim") or args.get("tool"), args.get("reset")))
        if tool == "set_up":
            if self.fail_set_up:
                return {"text": "error: make_listing failed (exit 1)", "isError": True, "data": None}
            return {"text": "set up", "isError": False, "data": MADE}
        if tool == "end_claim":
            return {"text": "saved", "isError": False, "data": args["report"]}
        return {"text": "ready; reset done", "isError": False, "data": None}

    def converse(self, unit, role, tools, system, message, **kw):
        self.roles = getattr(self, "roles", []) + [role]
        if role == "anchor":        # the search's starting point is not what these tests are about
            return ({"applies_to": "any listing", "action": "adding it", "compares": "subtotal and price", "kind": "behaviour"},
                    {"seconds": 0, "cost_usd": 0, "turns": 1, "calls": 0, "rejected": [], "refused_tools": []}, None)
        return super().converse(unit, role, tools, system, message, **kw)

    def _claude(self, prompt, system, extra, name, timeout=900):
        conv = self.conv
        unit, role = conv["unit"], conv["role"]
        self.log.append(("converse", unit, role))
        self.messages.setdefault(unit, []).append(prompt)
        self.given[unit] = set(conv["tools"])
        if role in ("discovery", "planner"):
            self.system = system
            for rep in self.plans:
                self.replies.append(self.submit(unit, rep)["text"])
        else:
            n = self.tries[unit] = self.tries.get(unit, 0) + 1
            if (unit, n) not in self.silent:
                conv["report"] = {"outcome": "holds", "observed": "The subtotal rose by the listed price."}
        return {"total_cost_usd": 0.01, "num_turns": 1}

    def timeline(self, unit):
        """What happened for one case, in order: opened (with or without a reset), set-up, conversation, saved."""
        out, mine = [], False
        for e in self.log:
            if e[0] == "begin_claim":
                mine = e[1] == unit
                if mine:
                    out.append("reset" if e[2] else "open")
            elif e[0] == "end_claim":
                if e[1] == unit:
                    out.append("saved")
                mine = False
            elif mine:
                out.append("set_up" if e[0] == "set_up" else "converse" if e[0] == "converse" else e[0])
        return out

    def result(self, cid):
        prop = self.report["properties"][0]
        return next(c for c in prop["cases"] if c["id"] == cid)


class WithATempDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def run_it(self, plans, **kw):
        run = ScriptedRun(self.tmp.name, plans, **kw)
        run.work()
        self.assertNotIn("error", run.report, run.report.get("error"))
        return run


class OnePlanningConversation(WithATempDir):
    """No readable code: one conversation finds the ways in and plans the cases."""

    def test_a_bad_starting_state_turns_the_plan_back(self):
        bad = plan([case("c1", starts_from=[{**STEP, "tool": "make_order"}])])
        good = plan([case("c1", starts_from=[STEP])])
        run = self.run_it([bad, good])
        self.assertIn("not accepted: case c1, starting step 1: no data tool named make_order is declared", run.replies[0])
        self.assertEqual(run.replies[1], "saved; you're done")
        self.assertEqual(run.result("c1")["starts_from"], [STEP])                 # the accepted one reached the case

    def test_a_request_for_a_declared_tool_turns_the_plan_back(self):
        bad = plan([case("c1", needs={"state": "A dear listing.", "tool": {"name": "make_listing", "does": "Creates a listing."}})])
        run = self.run_it([bad])
        self.assertIn("already declared", run.replies[0])
        self.assertEqual(run.report["properties"][0]["cases"], [])               # no accepted plan, so nothing ran
        self.assertNotIn("set_up", [e[0] for e in run.log])

    def test_the_planner_is_told_the_tools_and_the_rules(self):
        run = self.run_it([plan([case("c1")])])
        self.assertIn("make_listing: Creates one listing.", run.messages["p1/discovery"][0])
        self.assertIn("starts_from", run.system)

    def test_each_case_gets_reset_then_set_up_then_its_conversation(self):
        run = self.run_it([plan([case("c1"), case("c2", starts_from=[STEP, STEP]), case("c3")])])
        self.assertEqual(run.timeline("p1/c1"), ["reset", "converse", "saved"])
        self.assertEqual(run.timeline("p1/c2"), ["reset", "set_up", "set_up", "converse", "saved"])
        self.assertEqual(run.timeline("p1/c3"), ["reset", "converse", "saved"])
        self.assertEqual(run.timeline("p1/discovery"), ["open", "converse", "saved"])   # finding cases never resets or sets up
        told = run.messages["p1/c2"][0]
        self.assertIn("this was set up", told)
        self.assertIn('"page": "/listings/u1"', told)
        self.assertNotIn("this was set up", run.messages["p1/c1"][0])
        done = run.result("c2")["result"]["starts_from"]
        self.assertEqual([(s["tool"], s["returned"], s["because"]) for s in done], [("make_listing", MADE, STEP["because"])] * 2)
        self.assertNotIn("starts_from", run.result("c1")["result"])
        self.assertEqual(run.report["properties"][0]["verdict"], "holds")

    def test_no_conversation_is_ever_given_a_data_tool(self):
        run = self.run_it([plan([case("c1", starts_from=[STEP])])])
        for unit, tools in run.given.items():
            self.assertFalse({"set_up", "data_tools"} & tools, unit)

    def test_a_case_that_needs_a_missing_tool_is_not_started_and_carries_the_request(self):
        run = self.run_it([plan([case("c1"), case("c2", needs=WANT)])])
        self.assertEqual(run.timeline("p1/c2"), ["reset", "saved"])
        res = run.result("c2")["result"]
        self.assertEqual((res["outcome"], res["data_request"]["tool"]["name"]), ("cant_run", "make_order"))
        self.assertIn("An order placed more than 30 days ago.", res["observed"])
        self.assertEqual(run.timeline("p1/c1"), ["reset", "converse", "saved"])
        self.assertEqual(run.report["properties"][0]["verdict"], "cant_tell")      # a case that did not run never counts as held

    def test_a_failed_step_stops_the_case_before_its_conversation(self):
        run = self.run_it([plan([case("c1", starts_from=[STEP, STEP])])], fail_set_up=True)
        self.assertEqual(run.timeline("p1/c1"), ["reset", "set_up", "saved"])     # the second step is not tried either
        res = run.result("c1")["result"]
        self.assertEqual(res["outcome"], "cant_run")
        self.assertIn("step 1 (make_listing) failed", res["observed"])
        self.assertEqual(res["starts_from"][0]["error"], "error: make_listing failed (exit 1)")
        self.assertEqual(run.report["properties"][0]["verdict"], "cant_tell")

    def test_a_second_try_resets_and_sets_up_again(self):
        run = self.run_it([plan([case("c1", starts_from=[STEP])])], silent={("p1/c1", 1)})
        self.assertEqual(run.timeline("p1/c1"), ["reset", "set_up", "converse", "reset", "set_up", "converse", "saved"])
        res = run.result("c1")["result"]
        self.assertEqual((res["outcome"], res["meta"]["attempts"], len(res["starts_from"])), ("holds", 2, 1))

    def test_without_a_reset_command_no_step_is_accepted(self):
        run = self.run_it([plan([case("c1", starts_from=[STEP])]), plan([case("c1")])], has_reset=False)
        self.assertIn("no reset command", run.replies[0])
        self.assertIn("no reset command is registered", run.messages["p1/discovery"][0])
        self.assertNotIn("set_up", [e[0] for e in run.log])

    def test_without_a_reset_command_a_request_says_that_is_the_reason(self):
        run = self.run_it([plan([case("c1"), case("c2", needs=WANT)])], has_reset=False)
        res = run.result("c2")["result"]
        self.assertEqual(res["outcome"], "cant_run")
        self.assertIn("no reset command is registered", res["observed"])
        self.assertNotIn("no declared data tool", res["observed"])


class SearchThenPlanner(WithATempDir):
    """Readable code: plain code finds the ways in, then a planner conversation writes the cases for them."""
    WAY = {"id": "e1", "door": "browser", "page": "/listings/:id", "file": "components/Add.tsx", "status": "found",
           "why": "calls POST /api/cart", "route": "POST /api/cart", "chain": []}
    TRACE = {"ways": [WAY], "roots": [{"path": "lib/cart.ts", "line": 4, "name": "addToCart"}],
             "routes": [{"method": "POST", "path": "/api/cart", "file": "app/api/cart/route.ts"}],
             "patterns": [r"\.cart\b"], "data": "the cart", "labels": 3, "label_cost_usd": 0.0, "excluded": []}

    def planned(self, cases):
        return {"wheres": [{"id": "e1", "where": "Listing page: the add-to-cart button"}], "cases": cases, "not_covered": ""}

    def run_it(self, plans, **kw):
        tracer = mock.Mock()
        tracer.return_value.run_trace.return_value = dict(self.TRACE)
        tracer.return_value.cost = 0.0
        with mock.patch("service.harness.Tracer", tracer):
            return super().run_it(plans, has_repo=True, **kw)

    def test_a_bad_starting_state_turns_the_cases_back(self):
        bad = self.planned([case("c1", starts_from=[{**STEP, "input": {"title": "Grand piano"}}])])
        good = self.planned([case("c1", starts_from=[STEP]), case("c2", needs=WANT), case("c3")])
        run = self.run_it([bad, good])
        self.assertIn("needs the input price_cents", run.replies[0])
        self.assertEqual(run.replies[1], "saved; you're done")
        self.assertEqual([e[2] for e in run.log if e[0] == "converse" and e[1] == "p1/discovery"], ["planner"])
        prop = run.report["properties"][0]
        self.assertTrue(prop["plan"]["traced"])
        by_id = {c["id"]: c for c in prop["plan"]["cases"]}
        self.assertEqual(by_id["c1"]["starts_from"], [STEP])                      # the accepted starting state is on the plan's case
        self.assertEqual(by_id["c2"]["needs"]["tool"]["name"], "make_order")
        self.assertNotIn("starts_from", by_id["c3"])
        self.assertEqual(run.timeline("p1/c1"), ["reset", "set_up", "converse", "saved"])
        self.assertEqual(run.timeline("p1/c2"), ["reset", "saved"])
        self.assertEqual(run.timeline("p1/c3"), ["reset", "converse", "saved"])
        self.assertEqual(run.result("c2")["result"]["data_request"]["state"], WANT["state"])

    def test_the_planner_is_told_the_tools_and_the_rules(self):
        run = self.run_it([self.planned([case("c1")])])
        self.assertIn("make_listing: Creates one listing.", run.messages["p1/discovery"][0])
        self.assertIn("starts_from", run.system)


if __name__ == "__main__":
    unittest.main()
