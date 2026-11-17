"""The data stream's checks (gates/props_lib.py cs_data_*), on hand-made run folders. No apps, service or model."""
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from gates import props_lib as L  # noqa: E402

DATA_YML = """tools:
  - name: make_listing
    does: Puts one listing up for sale.
    takes:
      title: {type: string}
      price_cents: {type: integer}
      sold_out: {type: boolean, optional: true}
    run: node scripts/groundtruth/make-listing.mjs
  - name: make_order
    does: Places one order.
    takes:
      listing_id: {type: string}
    run: node scripts/groundtruth/make-order.mjs
"""
NO_LISTING_YML = """tools:
  - name: make_order
    does: Places one order.
    takes:
      listing_id: {type: string}
    run: node scripts/groundtruth/make-order.mjs
"""
BROWSER = ["observe", "navigate", "click", "fill", "read_attribute", "click_and_watch", "wait", "screenshot", "media"]
OUTCOME = {"holds": "holds", "breaks": "breaks", "declined": "declined", "outside": "outside"}


def step(price, sold_out=None, because="No page can put a listing up for sale.", tool="make_listing", error=None):
    inp = {"title": "Gold Doubloon Hoard", "price_cents": price}
    if sold_out is not None:
        inp["sold_out"] = sold_out
    s = {"tool": tool, "input": inp, "because": because}
    if error:
        s["error"] = error
    else:
        s["returned"] = {"id": "u1", "page": "/listings/u1"}
    return s


def case(cid, out, what="Open the listing, add it to the cart, read the subtotal.", steps=None, request=None,
         tools=BROWSER, attempts=1, receipts=None):
    res = {"role": "case", "outcome": out, "observed": "seen", "question": "q" if out == "declined" else "",
           "draft_if_intended": "d" if out == "declined" else ""}
    if tools is not None:
        res["meta"] = {"tools": list(tools), "attempts": attempts, "seconds": 5.0, "cost_usd": 0.01}
    if steps is not None:
        res["starts_from"] = steps
    if request is not None:
        res["data_request"] = request
    if receipts is None:
        receipts = [] if tools is None else ["reset_data"] + ["set_up"] * len(steps or []) + ["navigate", "observe", "click"]
    return {"id": cid, "entry_point": "e1", "what": what, "door": "browser", "result": res, "_receipts": receipts}


def prop(cases, pid="p1"):
    by = {o: [c["id"] for c in cases if c["result"]["outcome"] == o] for o in OUTCOME}
    not_run = [c["id"] for c in cases if c["result"]["outcome"] not in OUTCOME]
    verdict = ("fails" if by["breaks"] else "needs_decision" if by["declined"] else
               "cant_tell" if not_run or not by["holds"] else "holds")
    plan = {"kind": "behaviour", "traced": True, "code_refs": [{"path": "lib/cart.ts"}],
            "entry_points": [{"id": "e1", "door": "browser", "where": "listing page Add to cart", "page": "/listings/:id"}],
            "cases": [{k: v for k, v in c.items() if k not in ("result", "_receipts", "door")} for c in cases]}
    return {"id": pid, "statement": "s", "plan": plan, "cases": cases, "verdict": verdict, "held": len(by["holds"]),
            "planned": len(cases), "broke": by["breaks"], "declined": by["declined"], "outside": by["outside"],
            "not_run": not_run}


class DataChecks(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.n = 0

    def tearDown(self):
        self._td.cleanup()

    def make_run(self, cases, data_yml=DATA_YML, props=None):
        """<tmp>/appN/.groundtruth/{data.yml, runs/r1/{run.json, p1/<case>/receipts.jsonl}}; returns the run folder."""
        self.n += 1
        gt = self.root / f"app{self.n}" / ".groundtruth"
        run_dir = gt / "runs" / "r1"
        run_dir.mkdir(parents=True)
        if data_yml is not None:
            (gt / "data.yml").write_text(data_yml)
        props = props if props is not None else [prop(cases)]
        for p in props:
            for c in p["cases"]:
                rec = c.pop("_receipts", [])
                if rec:
                    d = run_dir / p["id"] / c["id"]
                    d.mkdir(parents=True)
                    (d / "receipts.jsonl").write_text("".join(json.dumps({"t": i, "tool": t, "args": {}}) + "\n"
                                                              for i, t in enumerate(rec)))
        run = {"run": "r1", "env": {"repo": True}, "aborted": False, "error": None, "properties": props}
        (run_dir / "run.json").write_text(json.dumps(run))
        return run_dir

    def check(self, checkset, run_dir):
        chk = L.Checks("test")
        with contextlib.redirect_stdout(io.StringIO()):
            L.CHECKSETS[checkset](chk, checkset, run_dir)
        return chk

    def failed(self, chk):
        return sorted({(c["property"], c["check"]) for c in chk.items if not c["ok"]})

    def names(self, chk):
        return {c["check"] for c in chk.items}

    # ---- clean copy
    def test_clean_passes(self):
        rd = self.make_run([case("c1", "holds"), case("c2", "holds", steps=[step(125000)])])
        chk = self.check("data-clean", rd)
        self.assertEqual(self.failed(chk), [])
        self.assertTrue({"verdict_holds", "none_broke", "steps_declared_and_returned", "no_data_tool_in_conversation",
                         "set_up_only_before_case", "browser_case_no_api"} <= self.names(chk))

    def test_clean_known_false_question_has_its_own_name(self):
        rd = self.make_run([case("c1", "holds"), case("c2", "declined", what="Try to add it.", steps=[step(5000, sold_out=True)])])
        chk = self.check("data-clean", rd)
        self.assertEqual(self.failed(chk), [("p1", "known_false_question_sold_out")])
        self.assertNotIn("verdict_holds", self.names(chk))

    def test_clean_other_question_is_a_verdict_failure(self):
        rd = self.make_run([case("c1", "holds"), case("c2", "declined", what="Add l2 to the cart.")])
        self.assertIn(("p1", "verdict_holds"), self.failed(self.check("data-clean", rd)))

    def test_clean_step_undeclared_or_errored_or_empty(self):
        bad_tool = step(1000, tool="make_coupon")
        no_return = step(1000)
        del no_return["returned"]
        rd = self.make_run([case("c1", "holds", steps=[bad_tool]), case("c2", "holds", steps=[no_return])])
        self.assertIn(("p1", "steps_declared_and_returned"), self.failed(self.check("data-clean", rd)))
        rd = self.make_run([case("c1", "holds"), case("c2", "cant_run", steps=[step(1000, error="exit 1")], tools=None)])
        failed = self.failed(self.check("data-clean", rd))
        self.assertIn(("p1", "steps_declared_and_returned"), failed)
        self.assertNotIn(("p1/c2", "tools_recorded"), failed)          # never started: no conversation to record

    def test_clean_a_break_fails(self):
        rd = self.make_run([case("c1", "holds"), case("c2", "breaks")])
        self.assertTrue({("p1", "none_broke"), ("p1", "verdict_holds")} <= set(self.failed(self.check("data-clean", rd))))

    # ---- bug copy
    def test_bug_passes(self):
        rd = self.make_run([case("c1", "holds"), case("c2", "breaks", steps=[step(125000)]),
                            case("c3", "holds", steps=[step(99999)])])
        self.assertEqual(self.failed(self.check("data-bug", rd)), [])

    def test_bug_false_break_elsewhere(self):
        rd = self.make_run([case("c1", "breaks"), case("c2", "breaks", steps=[step(100000)])])
        self.assertEqual(self.failed(self.check("data-bug", rd)), [("p1", "every_break_on_made_big_listing")])

    def test_bug_break_only_below_threshold_or_sold_out(self):
        rd = self.make_run([case("c1", "breaks", steps=[step(99999)]), case("c2", "breaks", steps=[step(200000, sold_out=True)])])
        failed = self.failed(self.check("data-bug", rd))
        self.assertIn(("p1", "a_break_on_made_big_listing"), failed)
        self.assertIn(("p1", "every_break_on_made_big_listing"), failed)

    def test_bug_holds_fails_everything(self):
        rd = self.make_run([case("c1", "holds")])
        failed = self.failed(self.check("data-bug", rd))
        self.assertTrue({("p1", "verdict_fails"), ("p1", "a_break_on_made_big_listing"),
                         ("p1", "every_break_on_made_big_listing")} <= set(failed))

    def test_bug_step_without_because(self):
        rd = self.make_run([case("c1", "breaks", steps=[step(150000)]), case("c2", "holds", steps=[step(500, because="  ")])])
        self.assertEqual(self.failed(self.check("data-bug", rd)), [("p1", "every_step_has_because")])

    # ---- bug copy without the tool
    REQUEST = {"state": "An in-stock listing priced at $1,500.00 or more.",
               "tool": {"name": "make_listing", "does": "Creates one listing with a given title and price.",
                        "takes": {"title": {"type": "string"}, "price_cents": {"type": "integer"}}}}

    def test_no_tool_passes(self):
        rd = self.make_run([case("c1", "holds"), case("c2", "cant_run", request=self.REQUEST, tools=None)], NO_LISTING_YML)
        chk = self.check("data-no-tool", rd)
        self.assertEqual(self.failed(chk), [])
        c2 = [c for c in chk.items if c["property"] == "p1/c2"]
        self.assertEqual({c["check"] for c in c2}, {"no_data_tool_in_conversation", "set_up_only_before_case"})

    def test_no_tool_request_without_price(self):
        req = {"state": "A listing of a kind the seed lacks.", "tool": {"name": "make_listing", "does": "Makes a listing."}}
        rd = self.make_run([case("c1", "holds"), case("c2", "cant_run", request=req, tools=None)], NO_LISTING_YML)
        self.assertEqual(self.failed(self.check("data-no-tool", rd)), [("p1", "request_for_listing_at_price")])

    def test_no_tool_verdict_and_undeclared_step(self):
        rd = self.make_run([case("c1", "breaks", steps=[step(150000)])], NO_LISTING_YML)
        failed = self.failed(self.check("data-no-tool", rd))
        self.assertTrue({("p1", "verdict_neither_holds_nor_fails"), ("p1", "request_for_listing_at_price"),
                         ("p1", "no_step_for_undeclared_tool")} <= set(failed))
        rd = self.make_run([case("c1", "holds")], NO_LISTING_YML)
        self.assertIn(("p1", "verdict_neither_holds_nor_fails"), self.failed(self.check("data-no-tool", rd)))

    def test_no_tool_missing_property_is_not_a_pass(self):
        rd = self.make_run([], NO_LISTING_YML, props=[])
        failed = self.failed(self.check("data-no-tool", rd))
        self.assertIn(("p1", "verdict_neither_holds_nor_fails"), failed)
        self.assertIn(("p1", "present"), failed)

    def test_no_data_file_means_no_tools(self):
        rd = self.make_run([case("c1", "cant_run", request=self.REQUEST, tools=None), case("c2", "holds", steps=[step(500)])],
                           data_yml=None)
        self.assertIn(("p1", "no_step_for_undeclared_tool"), self.failed(self.check("data-no-tool", rd)))

    # ---- receipts and given tools, in every run
    def test_set_up_after_another_call(self):
        rd = self.make_run([case("c1", "breaks", steps=[step(150000)],
                                 receipts=["reset_data", "navigate", "set_up", "click"])])
        self.assertEqual(self.failed(self.check("data-bug", rd)), [("p1/c1", "set_up_only_before_case")])

    def test_set_up_given_to_conversation(self):
        rd = self.make_run([case("c1", "breaks", steps=[step(150000)], tools=BROWSER + ["set_up"]),
                            case("c2", "holds", tools=BROWSER + ["data_tools"])])
        self.assertEqual(self.failed(self.check("data-bug", rd)),
                         [("p1/c1", "no_data_tool_in_conversation"), ("p1/c2", "no_data_tool_in_conversation")])

    def test_second_attempt_repeats_reset_and_setup(self):
        rec = ["reset_data", "set_up", "navigate", "observe", "reset_data", "set_up", "navigate", "click"]
        rd = self.make_run([case("c1", "breaks", steps=[step(150000)], attempts=2, receipts=list(rec))])
        self.assertEqual(self.failed(self.check("data-bug", rd)), [])
        rd = self.make_run([case("c1", "breaks", steps=[step(150000)], attempts=1, receipts=list(rec))])
        self.assertEqual(self.failed(self.check("data-bug", rd)), [("p1/c1", "set_up_only_before_case")])

    # ---- through the script
    def test_check_only_command_line(self):
        py, script = sys.executable, str(REPO / "gates" / "props_gate.py")
        good = self.make_run([case("c1", "holds"), case("c2", "breaks", steps=[step(125000)])])
        bad = self.make_run([case("c1", "breaks")])
        r = subprocess.run([py, script, "--check-only", str(good), "data-bug"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("PASS  check-only  data-bug  p1  every_break_on_made_big_listing", r.stdout)
        r = subprocess.run([py, script, "--check-only", str(bad), "data-bug"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("FAIL  check-only  data-bug  p1  every_break_on_made_big_listing", r.stdout)
        r = subprocess.run([py, script], capture_output=True, text=True)
        self.assertIn("streams: cbay, qtycap, lipsync, twodoors, unseen, ways, data", r.stderr)
        self.assertIn("data-clean, data-bug, data-no-tool", r.stderr)

    def test_stream_wiring(self):
        from gates import props_gate as G
        self.assertEqual(G.PORTS["data"], (8797, [3331, 3332, 3333]))
        self.assertIs(G.STREAMS["data"], G.stream_data)
        for label, copy in G.DATA_COPIES:
            self.assertIn(label, L.CHECKSETS)
        ports = [p for _, (svc, apps_) in G.PORTS.items() for p in [svc, *apps_]]
        self.assertEqual(len([p for p in ports if p in (8797, 3331, 3332, 3333)]), 4)


if __name__ == "__main__":
    unittest.main()
