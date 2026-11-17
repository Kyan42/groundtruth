"""The claims streams' checks (gates/props_lib.py cs_claims_*, the able-to-show tests) and their wiring in
gates/props_gate.py, on hand-made run folders. No apps, service, model or fixed ports."""
import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from gates import apps, props_gate as G, props_lib as L  # noqa: E402

CLAIMS_YML = (REPO / "dashboard" / ".groundtruth" / "features" / "claims.yml").read_text()
VARIATIONS = {"tabs": ["only-new", "only-reruns", "both", "one-test", "many-tests", "each-outcome"],
              "result": ["all-held", "one-broke-of-ten", "only-variation-broke", "needs-decision", "could-not-run"],
              "film": ["first-shown", "another-variation", "another-test", "back-again", "no-film"]}
BROWSER = ["observe", "navigate", "click", "fill", "read_attribute", "click_and_watch", "wait", "screenshot", "media"]
COUNTED = ("holds", "breaks", "declined", "outside")


def mr(error=None, **inp):
    """One make_run step as the tool server records it in result.starts_from."""
    s = {"tool": "make_run", "input": inp, "because": "The pages cannot make a run."}
    if error:
        s["error"] = error
    else:
        s["returned"] = {"run_id": "0123456789abcdef", "page": "/run.html?run=0123456789abcdef", "tests": []}
    return s


def case(cid, out="holds", steps=None, text=None):
    steps = [mr(new_tests=1, rerun_tests=0)] if steps is None else steps
    started = not (out == "cant_run" and any(s.get("error") for s in steps))     # else a conversation ran
    res = {"role": "case", "outcome": out, "observed": text or f"seen on {cid}", "starts_from": steps,
           "question": (text or "Is this intended?") if out == "declined" else "",
           "draft_if_intended": "d" if out == "declined" else ""}
    if started:
        res["meta"] = {"tools": list(BROWSER), "attempts": 1, "seconds": 5.0, "cost_usd": 0.01}
    rec = (["reset_data"] + ["set_up"] * len(steps) + (["navigate", "observe"] if started else []))
    return {"id": cid, "entry_point": "e1", "what": f"Do {cid}.", "door": "browser", "result": res, "_receipts": rec}


def claim(pid, cases):
    by = {o: [c["id"] for c in cases if c["result"]["outcome"] == o] for o in COUNTED}
    not_run = [c["id"] for c in cases if c["result"]["outcome"] not in COUNTED]
    verdict = ("fails" if by["breaks"] else "needs_decision" if by["declined"] else
               "cant_tell" if not_run or not by["holds"] else "holds")
    plan = {"kind": "behaviour", "from_claim": True, "code_refs": [], "how_found": "From the claim.",
            "entry_points": [{"id": "e1", "door": "browser", "where": "the run page"}],
            "cases": [{k: v for k, v in c.items() if k not in ("result", "_receipts", "door")} for c in cases]}
    return {"id": pid, "statement": "s", "plan": plan, "cases": cases, "verdict": verdict, "held": len(by["holds"]),
            "planned": len(cases), "broke": by["breaks"], "declined": by["declined"], "outside": by["outside"],
            "not_run": not_run}


def all_held(pid, **steps_by_case):
    return claim(pid, [case(v, steps=steps_by_case.get(v.replace("-", "_"))) for v in VARIATIONS[pid]])


class ClaimsChecks(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.n = 0

    def tearDown(self):
        self._td.cleanup()

    def make_run(self, props, claims_yml=CLAIMS_YML, error=None):
        """<tmp>/appN/.groundtruth/{features/claims.yml, runs/r1/{run.json, <claim>/<case>/receipts.jsonl}}."""
        self.n += 1
        gt = self.root / f"app{self.n}" / ".groundtruth"
        run_dir = gt / "runs" / "r1"
        run_dir.mkdir(parents=True)
        if claims_yml is not None:
            (gt / "features").mkdir()
            (gt / "features" / "claims.yml").write_text(claims_yml)
        for p in props:
            for c in p["cases"]:
                rec = c.pop("_receipts", [])
                d = run_dir / p["id"] / c["id"]
                d.mkdir(parents=True, exist_ok=True)
                (d / "receipts.jsonl").write_text("".join(json.dumps({"t": i, "tool": t, "args": {}}) + "\n"
                                                          for i, t in enumerate(rec)))
        run = {"run": "r1", "env": {"repo": "/x", "url": "http://127.0.0.1:1"}, "aborted": False, "error": error,
               "properties": props}
        (run_dir / "run.json").write_text(json.dumps(run))
        return run_dir

    def check(self, checkset, run_dir):
        chk = L.Checks("test")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            L.CHECKSETS[checkset](chk, checkset, run_dir)
        return chk, out.getvalue()

    def failed(self, chk):
        return sorted({(c["property"], c["check"]) for c in chk.items if not c["ok"]})

    def names(self, chk):
        return {c["check"] for c in chk.items}

    # ---- the clean copy
    def test_clean_passes_and_notes_film_and_uncounted_cases(self):
        film = claim("film", [case(v, "declined" if v == "no-film" else "holds", text="Q" * 300) for v in VARIATIONS["film"]])
        rd = self.make_run([all_held("tabs"), all_held("result"), film])
        chk, out = self.check("claims-clean", rd)
        self.assertEqual(self.failed(chk), [])
        self.assertTrue({"run_ok", "one_case_per_variation", "none_broke", "verdict_holds", "no_data_tool_in_conversation",
                         "set_up_only_before_case", "browser_case_no_api"} <= self.names(chk))
        self.assertNotIn("code_refs_present", self.names(chk))       # a claim is planned without reading the code
        self.assertNotIn("was_traced", self.names(chk))
        self.assertIn("NOTE  test  claims-clean  film  verdict needs_decision (reported, not judged)", out)
        self.assertIn("NOTE  test  claims-clean  film/no-film  declined: " + "Q" * 200 + "\n", out)
        self.assertEqual(len(chk.notes), 2)
        self.assertNotIn("verdict", {c["property"] + c["check"] for c in chk.items if c["property"] == "film"})

    def test_clean_a_break_on_any_claim_fails(self):
        film = claim("film", [case(v, "breaks" if v == "another-test" else "holds") for v in VARIATIONS["film"]])
        rd = self.make_run([all_held("tabs"), all_held("result"), film])
        self.assertEqual(self.failed(self.check("claims-clean", rd)[0]), [("film", "none_broke")])
        tabs = claim("tabs", [case(v, "breaks" if v == "both" else "holds") for v in VARIATIONS["tabs"]])
        rd = self.make_run([tabs, all_held("result"), all_held("film")])
        self.assertEqual(self.failed(self.check("claims-clean", rd)[0]), [("tabs", "none_broke"), ("tabs", "verdict_holds")])

    def test_clean_question_on_result_is_not_holds(self):
        res = claim("result", [case(v, "declined" if v == "all-held" else "holds") for v in VARIATIONS["result"]])
        rd = self.make_run([all_held("tabs"), res, all_held("film")])
        self.assertEqual(self.failed(self.check("claims-clean", rd)[0]), [("result", "verdict_holds")])

    def test_clean_missing_or_extra_variation_or_claim(self):
        tabs = claim("tabs", [case(v) for v in VARIATIONS["tabs"][:-1]] + [case("only-new")])
        rd = self.make_run([tabs, all_held("result")])
        failed = self.failed(self.check("claims-clean", rd)[0])
        self.assertIn(("tabs", "one_case_per_variation"), failed)
        self.assertIn(("film", "present"), failed)
        self.assertIn(("film", "none_broke"), failed)

    def test_clean_run_error_and_unreadable_claims_file(self):
        rd = self.make_run([all_held("tabs"), all_held("result"), all_held("film")], error="the service stopped")
        self.assertEqual(self.failed(self.check("claims-clean", rd)[0]), [("-", "run_ok")])
        rd = self.make_run([all_held("tabs"), all_held("result"), all_held("film")], claims_yml=None)
        self.assertEqual(self.failed(self.check("claims-clean", rd)[0]),
                         [("film", "one_case_per_variation"), ("result", "one_case_per_variation"), ("tabs", "one_case_per_variation")])

    def test_ordinary_property_still_needs_code_refs(self):
        p = all_held("tabs")
        p["plan"]["from_claim"] = False
        rd = self.make_run([p, all_held("result"), all_held("film")])
        self.assertIn(("tabs", "code_refs_present"), self.failed(self.check("claims-clean", rd)[0]))

    # ---- the able-to-show tests
    def test_able_to_show(self):
        self.assertTrue(L.able_regression_short({"new_tests": 0, "rerun_tests": 1}))
        self.assertFalse(L.able_regression_short({"new_tests": 6, "rerun_tests": 0}))
        self.assertFalse(L.able_regression_short({"new_tests": 1, "rerun_tests": True}))
        self.assertTrue(L.able_single_holds({"new_tests": 1, "rerun_tests": 0, "variations_each": 1, "verdicts": "fails"}))
        self.assertTrue(L.able_single_holds({"new_tests": 1, "rerun_tests": 1, "variations_each": 1, "verdicts": "holds, fails"}))
        self.assertFalse(L.able_single_holds({"new_tests": 1, "rerun_tests": 0, "verdicts": "fails"}))           # three each
        self.assertFalse(L.able_single_holds({"new_tests": 1, "rerun_tests": 0, "variations_each": 10, "verdicts": "fails"}))
        self.assertFalse(L.able_single_holds({"new_tests": 1, "rerun_tests": 0, "variations_each": 1, "verdicts": "needs_decision"}))
        self.assertFalse(L.able_single_holds({"new_tests": 1, "rerun_tests": 0, "variations_each": 1, "verdicts": "holds,fails"}))
        self.assertTrue(L.able_other_test_film({"new_tests": 1, "rerun_tests": 1}))
        self.assertTrue(L.able_other_test_film({"new_tests": 2, "rerun_tests": 0, "films": True}))
        self.assertFalse(L.able_other_test_film({"new_tests": 1, "rerun_tests": 0}))
        self.assertFalse(L.able_other_test_film({"new_tests": 2, "rerun_tests": 1, "films": False}))
        self.assertFalse(L.able_other_test_film({"new_tests": 1, "rerun_tests": 1, "verdicts": "not_run"}))
        self.assertTrue(L.able_other_test_film({"new_tests": 1, "rerun_tests": 2, "verdicts": "not_run"}))

    def test_able_reads_only_steps_that_ran(self):
        planned_only = case("c1", steps=[])
        planned_only["starts_from"] = [mr(new_tests=0, rerun_tests=3)]                    # the plan's steps
        self.assertFalse(L.able_case(planned_only, L.able_regression_short))
        self.assertFalse(L.able_case(case("c1", steps=[mr(error="exit 1", new_tests=0, rerun_tests=3)]), L.able_regression_short))
        self.assertTrue(L.able_case(case("c1", steps=[mr(new_tests=1, rerun_tests=0), mr(new_tests=0, rerun_tests=1)]),
                                    L.able_regression_short))

    # ---- the broken copies
    BUGS = {
        "claims-regression-short": ("tabs", "only-reruns", {"new_tests": 0, "rerun_tests": 1}, {"new_tests": 2, "rerun_tests": 0}),
        "claims-single-holds": ("result", "only-variation-broke",
                                {"new_tests": 1, "rerun_tests": 0, "variations_each": 1, "verdicts": "fails"},
                                {"new_tests": 1, "rerun_tests": 0, "variations_each": 10, "verdicts": "fails"}),
        "claims-other-test-film": ("film", "another-test", {"new_tests": 2, "rerun_tests": 1},
                                   {"new_tests": 1, "rerun_tests": 0, "films": False}),
    }

    def bug_run(self, checkset, outcomes, able_cases=(), error_on=None, drop=None):
        """The target claim: every variation holds unless `outcomes` says otherwise; the cases in `able_cases` start
        from data that lets the bug show, the others from data that does not."""
        target, _v, able, unable = self.BUGS[checkset]
        cases = []
        for v in VARIATIONS[target]:
            if v == drop:
                continue
            steps = [mr(error="exit 1" if v == error_on else None, **(able if v in able_cases else unable))]
            cases.append(case(v, outcomes.get(v, "holds"), steps=steps))
        return self.make_run([claim(target, cases)])

    def test_bug_checksets(self):
        for cs, (target, v, _a, _u) in self.BUGS.items():
            with self.subTest(cs):
                other = next(x for x in VARIATIONS[target] if x != v)
                # passes: a break where it can show, other able cases holding
                chk, out = self.check(cs, self.bug_run(cs, {v: "breaks"}, able_cases=(v, other)))
                self.assertEqual(self.failed(chk), [])
                self.assertTrue({"bug_caught", "no_break_where_bug_cannot_show", "verdict_fails", "one_case_per_variation",
                                 "set_up_only_before_case"} <= self.names(chk))
                self.assertIn(f"NOTE  test  {cs}  {target}/{v}  breaks; able to show yes; make_run(", out)
                third = next(x for x in VARIATIONS[target] if x not in (v, other))
                self.assertIn(f"NOTE  test  {cs}  {target}/{third}  holds; able to show no; make_run(", out)
                # a break where the bug cannot show (besides a real catch)
                chk, _ = self.check(cs, self.bug_run(cs, {v: "breaks", other: "breaks"}, able_cases=(v,)))
                self.assertEqual(self.failed(chk), [(target, "no_break_where_bug_cannot_show")])
                # only a break where it cannot show
                chk, _ = self.check(cs, self.bug_run(cs, {other: "breaks"}, able_cases=(v,)))
                self.assertEqual(self.failed(chk), [(target, "bug_caught"), (target, "no_break_where_bug_cannot_show")])
                # caught only by a question (or outside, or could not run): never a catch
                for out_word in ("declined", "outside", "cant_run"):
                    chk, _ = self.check(cs, self.bug_run(cs, {v: out_word}, able_cases=(v,)))
                    self.assertEqual(self.failed(chk), [(target, "bug_caught"), (target, "verdict_fails")], out_word)
                # no break at all
                chk, _ = self.check(cs, self.bug_run(cs, {}, able_cases=(v,)))
                self.assertEqual(self.failed(chk), [(target, "bug_caught"), (target, "verdict_fails")])
                # a missing variation
                chk, _ = self.check(cs, self.bug_run(cs, {v: "breaks"}, able_cases=(v,), drop=other))
                self.assertEqual(self.failed(chk), [(target, "one_case_per_variation")])
                # the able step errored: the case did not start from that data, so a break there does not count
                chk, _ = self.check(cs, self.bug_run(cs, {v: "breaks"}, able_cases=(v,), error_on=v))
                self.assertEqual(self.failed(chk), [(target, "bug_caught"), (target, "no_break_where_bug_cannot_show")])

    def test_bug_checkset_needs_its_target(self):
        rd = self.make_run([all_held("tabs")])
        failed = self.failed(self.check("claims-single-holds", rd)[0])
        self.assertTrue({("result", "present"), ("result", "bug_caught"), ("result", "verdict_fails"),
                         ("result", "one_case_per_variation")} <= set(failed))

    # ---- through the script
    def test_check_only_command_line_and_usage(self):
        py, script = sys.executable, str(REPO / "gates" / "props_gate.py")
        good = self.bug_run("claims-other-test-film", {"another-test": "breaks"}, able_cases=("another-test",))
        bad = self.bug_run("claims-other-test-film", {"another-test": "declined"}, able_cases=("another-test",))
        r = subprocess.run([py, script, "--check-only", str(good), "claims-other-test-film"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("PASS  check-only  claims-other-test-film  film  bug_caught", r.stdout)
        self.assertIn("NOTE  check-only  claims-other-test-film  film/another-test  breaks; able to show yes", r.stdout)
        r = subprocess.run([py, script, "--check-only", str(bad), "claims-other-test-film"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("FAIL  check-only  claims-other-test-film  film  bug_caught", r.stdout)
        r = subprocess.run([py, script], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("streams: cbay, qtycap, lipsync, twodoors, unseen, ways, data, claimsclean, claimsbugs", r.stderr)
        self.assertIn("claims-clean, claims-regression-short, claims-single-holds, claims-other-test-film", r.stderr)
        self.assertIn("A question, \"outside the wording\" or \"could not run\" is never counted as a catch.", r.stderr)

    def test_stream_wiring(self):
        self.assertEqual(G.PORTS["claimsclean"], (8798, [8921]))
        self.assertEqual(G.PORTS["claimsbugs"], (8799, [8922, 8923, 8924]))
        self.assertIs(G.STREAMS["claimsclean"], G.stream_claims)
        self.assertIs(G.STREAMS["claimsbugs"], G.stream_claims)
        self.assertEqual(G.CLAIM_COPIES["claimsclean"], (("dash-clean", "claims-clean", None),))
        self.assertEqual(G.CLAIM_COPIES["claimsbugs"], (("dash-regression-short", "claims-regression-short", "tabs"),
                                                         ("dash-single-holds", "claims-single-holds", "result"),
                                                         ("dash-other-test-film", "claims-other-test-film", "film")))
        for copies in G.CLAIM_COPIES.values():
            for _copy, cs, _only in copies:
                self.assertIn(cs, L.CHECKSETS)
        mine = {8798, 8799, 8921, 8922, 8923, 8924}
        others = {p for name, (svc, apps_) in G.PORTS.items() if not name.startswith("claims") for p in [svc, *apps_]}
        self.assertFalse(mine & others)
        self.assertFalse({8790, 8791, 8900, 8901, 8902} & {8798, 8799, 8921, 8922, 8923, 8924})

    def test_refuses_when_a_copy_is_missing_or_its_claims_differ(self):
        repo = self.root / "repo"
        work = repo / "gates" / "work"
        ref = repo / "dashboard" / L.CLAIMS_FILE
        ref.parent.mkdir(parents=True)
        ref.write_text(CLAIMS_YML)
        for copy in ("dash-clean", "dash-regression-short", "dash-single-holds", "dash-other-test-film"):
            f = work / copy / L.CLAIMS_FILE
            f.parent.mkdir(parents=True)
            f.write_text(CLAIMS_YML)
        with mock.patch.object(apps, "REPO", repo), mock.patch.object(apps, "WORK", work), \
                mock.patch.object(G, "run_stream", side_effect=AssertionError("must not start")) as run:
            self.assertEqual(G.claim_copy_problems("claimsclean"), [])
            self.assertEqual(G.claim_copy_problems("claimsbugs"), [])
            (work / "dash-single-holds" / L.CLAIMS_FILE).write_text(CLAIMS_YML + " ")
            shutil.rmtree(work / "dash-clean")
            for stream, word in (("claimsbugs", "dash-single-holds/.groundtruth/features/claims.yml differs"),
                                 ("claimsclean", "dash-clean is missing")):
                with self.assertRaises(SystemExit) as e:
                    G.main([stream])
                self.assertIn(f"Not starting {stream}", str(e.exception.code))
                self.assertIn(word, str(e.exception.code))
            run.assert_not_called()


class InProcessStoreShape(unittest.TestCase):
    def test_refuses_a_folder_without_the_marker_and_stops_like_a_process(self):
        with tempfile.TemporaryDirectory() as td:
            app = Path(td)
            (app / ".groundtruth" / "test-store").mkdir(parents=True)
            with self.assertRaises(RuntimeError):
                L.InProcessStore(app, 0)
            (app / ".groundtruth" / "test-store" / L.TEST_STORE_MARKER).write_text("made up\n")
            s = L.InProcessStore(app, 0)
            self.assertIsNone(s.poll())
            self.assertTrue(s.url.startswith("http://127.0.0.1:"))
            apps.stop(s)
            self.assertEqual(s.poll(), 0)
            apps.stop(s)                    # stopping twice is harmless


if __name__ == "__main__":
    unittest.main()
