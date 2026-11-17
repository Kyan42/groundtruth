"""The rules the service's code enforces, with no model involved."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from service.harness import case_problems, case_tools, decide_verdict, discovery_tools, plan_problems  # noqa: E402


def case(cid, outcome, **extra):
    return {"id": cid, "result": {"outcome": outcome, "observed": "x", **extra}}


def plan(**over):
    p = {"applies_to": "in-stock items", "action": "add", "compares": "subtotal change against listing price", "kind": "behaviour",
         "how_found": "read the code", "not_covered": "", "code_refs": [{"path": "lib/cart.ts", "line": 22}],
         "entry_points": [{"id": "e1", "door": "browser", "where": "listing page, Add to cart"}],
         "cases": [{"id": "c1", "entry_point": "e1", "what": "add an item", "represents": "first add"}]}
    p.update(over)
    return p


def conv(kind="behaviour", door="browser", calls=(), shots=(), checks=None, way_status=None):
    return {"kind": kind, "door": door, "calls": [{"tool": t, "args": a, "isError": False} for t, a in calls],
            "shots": set(shots), "checks": checks or {}, "way_status": way_status}


class Verdict(unittest.TestCase):
    def v(self, *cases):
        return decide_verdict({}, list(cases))["verdict"]

    def test_a_broken_case_always_fails_and_nothing_softens_it(self):
        self.assertEqual(self.v(case("c1", "holds"), case("c2", "breaks")), "fails")
        self.assertEqual(self.v(case("c1", "breaks"), case("c2", "declined"), case("c3", "outside"), case("c4", "cant_run")), "fails")

    def test_a_declined_action_is_a_question_never_a_pass(self):
        out = decide_verdict({}, [case("c1", "holds"), case("c2", "declined", question="Intended?", draft_if_intended="Sold-out items can't be added.")])
        self.assertEqual(out["verdict"], "needs_decision")
        self.assertEqual(out["questions"], [{"case": "c2", "question": "Intended?", "draft_if_intended": "Sold-out items can't be added.", "observed": "x"}])
        self.assertEqual(self.v(case("c1", "declined")), "needs_decision")

    def test_a_case_not_run_means_cant_tell(self):
        self.assertEqual(self.v(case("c1", "holds"), case("c2", "cant_run")), "cant_tell")
        self.assertEqual(self.v(case("c1", "holds"), case("c2", "planned")), "cant_tell")

    def test_a_pass_needs_at_least_one_case_that_really_held(self):
        self.assertEqual(self.v(case("c1", "holds"), case("c2", "outside")), "holds")
        self.assertEqual(self.v(case("c1", "outside"), case("c2", "outside")), "cant_tell")
        self.assertEqual(self.v(), "cant_tell")

    def test_counts_add_up(self):
        out = decide_verdict({}, [case("a", "holds"), case("b", "breaks"), case("c", "declined"), case("d", "outside"), case("e", "cant_run")])
        self.assertEqual(out["held"] + len(out["broke"]) + len(out["declined"]) + len(out["outside"]) + len(out["not_run"]), out["planned"])


class Plans(unittest.TestCase):
    def test_a_good_plan_is_accepted(self):
        self.assertIsNone(plan_problems(plan(), {"has_repo": True}))

    def test_code_locations_are_required_when_code_can_be_read(self):
        self.assertIn("code_refs", plan_problems(plan(code_refs=[]), {"has_repo": True}))
        self.assertIsNone(plan_problems(plan(code_refs=[]), {"has_repo": False}))

    def test_every_way_in_through_the_pages_needs_a_case(self):
        p = plan(entry_points=[{"id": "e1", "door": "browser", "where": "listing"}, {"id": "e2", "door": "browser", "where": "quick add"}])
        self.assertIn("e2", plan_problems(p, {}))

    def test_limits(self):
        many = [{"id": f"c{i}", "entry_point": "e1", "what": "x"} for i in range(13)]
        self.assertIn("1 to 12", plan_problems(plan(cases=many), {}))
        self.assertIn("1 to 12", plan_problems(plan(cases=[]), {}))
        self.assertIn("kind", plan_problems(plan(kind="visual"), {}))
        self.assertIn("discovery", str(plan_problems(plan(cases=[{"id": "discovery", "entry_point": "e1", "what": "x"}]), {})) + "discovery")


class NoPageReferencesInPlans(unittest.TestCase):
    """A reference such as [ref=e12] is only good for one look at a page, so a plan that carries one is sent back."""

    def test_a_plan_that_names_a_reference_is_rejected(self):
        for text in ("Click [ref=e25] on the run page", "click ref e25", "Use reference f1e3 to open the card"):
            self.assertIn("page reference", plan_problems(plan(cases=[{"id": "c1", "entry_point": "e1", "what": text}])), text)
        self.assertIn("page reference", plan_problems(plan(entry_points=[{"id": "e1", "door": "browser", "where": "the card [ref=e14]"}])))

    def test_ordinary_words_and_way_in_ids_are_fine(self):
        for text in ("Open way in e1 and click the second card", "Add the item for reference, then read the subtotal", "Check the preference page"):
            self.assertIsNone(plan_problems(plan(cases=[{"id": "c1", "entry_point": "e1", "what": text}])), text)


class Tools(unittest.TestCase):
    def test_the_way_in_is_fixed_by_the_plan(self):
        env = {"has_pid": True, "has_repo": True}
        self.assertNotIn("http_request", case_tools("behaviour", "browser", env))
        self.assertEqual(case_tools("behaviour", "api", env), ["http_request"])
        self.assertIn("gpu_check", case_tools("measurement", "browser", env))
        self.assertNotIn("gpu_check", case_tools("measurement", "browser", {"has_pid": False}))
        for kind, door in (("behaviour", "browser"), ("behaviour", "api"), ("measurement", "api")):
            self.assertFalse({"grep", "read_lines", "find", "list_files", "match_each"} & set(case_tools(kind, door, env)))
        self.assertIn("read_lines", case_tools("code", "code", env))

    def test_discovery_only_looks(self):
        tools = discovery_tools({"has_repo": True})
        self.assertFalse({"click", "fill", "http_request", "click_and_watch"} & set(tools))
        self.assertNotIn("read_lines", discovery_tools({"has_repo": False}))


class CaseResults(unittest.TestCase):
    def test_a_result_must_match_what_was_done(self):
        self.assertIn("way in", case_problems(conv(), {"outcome": "holds", "observed": "x"}))
        self.assertIsNone(case_problems(conv(calls=[("observe", {})]), {"outcome": "holds", "observed": "x"}))
        self.assertIn("way in", case_problems(conv(door="api", calls=[]), {"outcome": "breaks", "observed": "x"}))
        self.assertIn("unknown screenshot", case_problems(conv(calls=[("observe", {})]), {"outcome": "holds", "observed": "x", "screenshots": ["shot-9"]}))

    def test_declined_needs_a_question_and_a_draft(self):
        c = conv(calls=[("click", {})])
        self.assertIn("question", case_problems(c, {"outcome": "declined", "observed": "refused"}))
        self.assertIsNone(case_problems(c, {"outcome": "declined", "observed": "refused", "question": "Intended?", "draft_if_intended": "It refuses."}))
        self.assertIn("does not apply", case_problems(conv(kind="code", door="code"), {"outcome": "declined", "observed": "x", "question": "q", "draft_if_intended": "d"}))

    def test_finding_no_control_is_recorded_as_a_case_that_does_not_count(self):
        for status in ("to check", "flagged"):
            rep = {"outcome": "nothing_there", "observed": "the header has only a cart link"}
            self.assertIsNone(case_problems(conv(calls=[("observe", {})], way_status=status), rep))
            self.assertEqual((rep["outcome"], rep["nothing_there"]), ("outside", True))
            self.assertEqual(decide_verdict({}, [case("c1", "holds"), {"id": "c2", "result": rep}])["verdict"], "holds")

    def test_a_way_in_the_search_is_sure_of_can_never_be_waved_off_as_nothing_there(self):
        # Otherwise a case that simply missed a real control would stop counting, and the property could still pass.
        for status in ("found", None):
            rep = {"outcome": "nothing_there", "observed": "I saw no button"}
            why = case_problems(conv(calls=[("observe", {})], way_status=status), rep)
            self.assertIn("cant_run", why)
            self.assertIn("outside", why)       # e.g. a listing that does not exist: the sentence does not cover it
            self.assertEqual(rep["outcome"], "nothing_there")
        self.assertEqual(decide_verdict({}, [case("c1", "holds"), case("c2", "cant_run")])["verdict"], "cant_tell")

    def test_outside_and_cant_run_need_no_action(self):
        self.assertIsNone(case_problems(conv(), {"outcome": "outside", "observed": "the sentence says in-stock; this item is sold out"}))
        self.assertIsNone(case_problems(conv(), {"outcome": "cant_run", "observed": "the page never loaded"}))

    def test_a_measurement_result_needs_a_measurement_after_the_last_action_and_can_always_be_fixed(self):
        early = conv(kind="measurement", calls=[("navigate", {}), ("gpu_check", {}), ("click", {})])
        self.assertIn("after your last action", case_problems(early, {"outcome": "holds", "observed": "x"}))
        early["calls"].append({"tool": "gpu_check", "args": {}, "isError": False})      # the model measures now and submits again
        self.assertIsNone(case_problems(early, {"outcome": "holds", "observed": "x"}))
        nothing = conv(kind="measurement", calls=[("observe", {}), ("gpu_check", {})])
        self.assertIn("nothing has been triggered", case_problems(nothing, {"outcome": "holds", "observed": "x"}))

    def test_code_results_cite_lines_that_were_read(self):
        c = conv(kind="code", door="code", calls=[("read_lines", {"path": "render.py"})])
        good = {"outcome": "holds", "observed": "x", "excerpts": [{"path": "render.py", "start": 1, "end": 10, "why": "uses the shared table"}]}
        self.assertIsNone(case_problems(c, good))
        self.assertIn("haven't read", case_problems(c, {**good, "excerpts": [{"path": "other.py", "start": 1, "end": 3, "why": "w"}]}))
        self.assertIn("1 to 15", case_problems(c, {**good, "excerpts": [{"path": "render.py", "start": 1, "end": 40, "why": "w"}]}))
        zero = conv(kind="code", door="code", checks={"check-1": {"checked": 0, "violators": []}})
        self.assertIn("zero files", case_problems(zero, {"outcome": "holds", "observed": "x", "checks": ["check-1"]}))


if __name__ == "__main__":
    unittest.main()
