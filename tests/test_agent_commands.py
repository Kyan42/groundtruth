"""The commands a coding agent runs: claims, approve (and what verify and the run page do with an approval), register's
reset check, up and status, summary, install-skills. No model, no fixed ports."""
import argparse
import http.server
import io
import json
import os
import signal
import socket
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import groundtruth  # noqa: E402
from toolserver import approval, rundoc  # noqa: E402
from toolserver.runner import run_verify  # noqa: E402

FEATURE = """feature: tabs-trial
goal: >-
  A reviewer can trust a run's page.
properties:
  - id: tabs
    title: Right tests in each tab
    your_words: "all properties should appear in the right tab"
    statement: >-
      New has one card for each test written for that run.
      No test is missing or in both.
    checked_against: The record of which tests were written for the run.
    variations:
      - id: only-new
        what: A run with only new tests.
      - A run with both.
  - id: result
    statement: A test shows as passing only if none of its variations broke.
"""


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve(handler):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


class Quiet(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, obj):
        b = json.dumps(obj).encode() if not isinstance(obj, bytes) else obj
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


class App(Quiet):
    def do_GET(self):
        self.reply(200, b"<html>app</html>")


class FakeService(Quiet):
    """Starts a run and answers the first results post with a finished report: tabs holds on two cases."""
    started = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path == "/v1/runs":
            FakeService.started.append(body)
            return self.reply(200, {"run": "r1", "actions": []})
        cases = [{"id": "only-new", "result": {"outcome": "holds", "observed": "ok"}}, {"id": "v2", "result": {"outcome": "holds", "observed": "ok"}}]
        props = [{"id": p["id"], "statement": p["statement"], "verdict": "holds", "held": 2, "planned": 2, "cases": cases}
                 for p in FakeService.started[-1]["input"]["properties"]]
        self.reply(200, {"report": {"run": "r1", "order": [p["id"] for p in props], "properties": props}})

    def do_DELETE(self):
        self.reply(200, {})


def make_repo(root: Path, text=FEATURE) -> Path:
    repo = root / "app"
    (repo / ".groundtruth" / "features").mkdir(parents=True)
    (repo / ".groundtruth" / "features" / "tabs.yml").write_text(text)
    return repo


def claims_of(repo):
    return approval.read_claims(repo, "tabs")[1]


class WithATempDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = make_repo(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def edit(self, old, new):
        f = self.repo / ".groundtruth" / "features" / "tabs.yml"
        text = f.read_text()
        self.assertIn(old, text)
        f.write_text(text.replace(old, new))


class Comparison(WithATempDir):
    def test_no_record_says_nothing(self):
        self.assertEqual(approval.compare(claims_of(self.repo), None), {})
        self.assertIsNone(approval.load_record(self.repo, "tabs"))

    def test_just_approved_is_approved(self):
        rec = approval.write_record(self.repo, "tabs", claims_of(self.repo))
        self.assertEqual(rec["properties"]["tabs"]["variations"], [{"id": "only-new", "what": "A run with only new tests."},
                                                                   {"id": "v2", "what": "A run with both."}])
        self.assertEqual(set(rec["properties"]["result"]), {"statement"})            # only the parts it has
        self.assertEqual(approval.compare(claims_of(self.repo), approval.load_record(self.repo, "tabs")),
                         {"tabs": {"state": "approved", "changed": []}, "result": {"state": "approved", "changed": []}})

    def test_each_kind_of_change(self):
        edits = [("No test is missing", "No test is lost", ["statement"]),
                 ("Right tests in each tab", "Right tabs", ["title"]),
                 ("all properties should appear", "every property should appear", ["your_words"]),
                 ("The record of which tests", "The list of which tests", ["checked_against"]),
                 ("A run with both.", "A run with both kinds.", ["variations"]),
                 ("      - id: only-new\n        what: A run with only new tests.\n", "", ["variations"])]
        for old, new, parts in edits:
            with self.subTest(parts=parts):
                (self.repo / ".groundtruth" / "features" / "tabs.yml").write_text(FEATURE)
                approval.write_record(self.repo, "tabs", claims_of(self.repo))
                self.edit(old, new)
                got = approval.compare(claims_of(self.repo), approval.load_record(self.repo, "tabs"))
                self.assertEqual(got["tabs"], {"state": "changed", "changed": parts})
                self.assertEqual(got["result"]["state"], "approved")

    def test_a_part_added_or_removed_is_a_change(self):
        approval.write_record(self.repo, "tabs", claims_of(self.repo))
        self.edit("    statement: A test shows", "    title: Failures never pass\n    statement: A test shows")
        self.edit("    title: Right tests in each tab\n", "")
        got = approval.compare(claims_of(self.repo), approval.load_record(self.repo, "tabs"))
        self.assertEqual(got["result"], {"state": "changed", "changed": ["title"]})
        self.assertEqual(got["tabs"], {"state": "changed", "changed": ["title"]})

    def test_whitespace_only_edits_are_not_changes(self):
        approval.write_record(self.repo, "tabs", claims_of(self.repo))
        self.edit("New has one card for each test written for that run.\n      No test",
                  "New has  one card for each test\n      written for that run.   No test")
        self.edit("what: A run with only new tests.", "what: '  A run with only new tests.  '")
        self.edit('your_words: "all properties', 'your_words: "  all properties')
        got = approval.compare(claims_of(self.repo), approval.load_record(self.repo, "tabs"))
        self.assertEqual(got["tabs"], {"state": "approved", "changed": []})

    def test_a_property_added_after_approval_is_new(self):
        approval.write_record(self.repo, "tabs", claims_of(self.repo))
        (self.repo / ".groundtruth" / "features" / "tabs.yml").write_text(FEATURE + "  - id: film\n    statement: Each film is its own.\n")
        got = approval.compare(claims_of(self.repo), approval.load_record(self.repo, "tabs"))
        self.assertEqual(got["film"], {"state": "new", "changed": []})
        self.assertEqual(approval.flag_lines(got), ["not approved yet: film"])
        self.assertEqual(approval.flag_lines({"tabs": {"state": "changed", "changed": ["statement", "variations"]}}),
                         ["changed since it was approved: tabs (the sentence, the variations)"])


def call(fn, **kw):
    out = io.StringIO()
    code = 0
    with redirect_stdout(out):
        try:
            fn(argparse.Namespace(**kw))
        except SystemExit as e:
            code = e.code
    return out.getvalue(), code


class ClaimsAndApprove(WithATempDir):
    def test_claims_prints_the_file_as_it_is(self):
        out, code = call(groundtruth.cmd_claims, repo=str(self.repo), feature="tabs", full=True)
        self.assertEqual(code, 0)
        self.assertEqual(out, "\n".join([
            "Claims for tabs-trial (.groundtruth/features/tabs.yml)",
            "Goal: A reviewer can trust a run's page.",
            "",
            "1. tabs: Right tests in each tab",
            "   Approval: not approved yet",
            "   Your words: “all properties should appear in the right tab”",
            "   Sentence: New has one card for each test written for that run. No test is missing or in both.",
            "   Checked against: The record of which tests were written for the run.",
            "   Variations:",
            "     - only-new: A run with only new tests.",
            "     - v2: A run with both.",
            "",
            "2. result",
            "   Approval: not approved yet",
            "   Sentence: A test shows as passing only if none of its variations broke.",
        ]) + "\n")

    def test_approve_then_claims_shows_each_state(self):
        out, code = call(groundtruth.cmd_approve, repo=str(self.repo), feature="tabs")
        self.assertEqual((code, out), (0, "Approved tabs: Right tests in each tab (2 variations)\nApproved result\n"
                                          "No draft was kept for these claims, so there is nothing to compare with.\n"))
        rec = json.loads((self.repo / ".groundtruth" / "approved" / "tabs.json").read_text())
        self.assertEqual(set(rec), {"feature", "approved_at", "properties", "draft_changes"})
        self.edit("No test is missing", "No test is lost")
        out, _ = call(groundtruth.cmd_claims, repo=str(self.repo), feature="tabs", full=True)
        self.assertIn("Last approved: ", out)
        self.assertIn("   Approval: changed since it was approved (the sentence)", out)
        self.assertIn("   Approval: approved", out)
        call(groundtruth.cmd_approve, repo=str(self.repo), feature="tabs")           # approving again replaces the record
        out, _ = call(groundtruth.cmd_claims, repo=str(self.repo), feature="tabs", full=True)
        self.assertNotIn("changed", out)

    def test_a_problem_is_said_plainly_and_nothing_is_approved(self):
        self.edit("      - A run with both.\n", "      - ''\n")
        for fn in (groundtruth.cmd_claims, groundtruth.cmd_approve):
            out, code = call(fn, repo=str(self.repo), feature="tabs")
            self.assertEqual(out, "")
            self.assertIn("variation 2 is not a sentence", str(code))
        self.assertFalse((self.repo / ".groundtruth" / "approved").exists())
        _, code = call(groundtruth.cmd_claims, repo=str(self.repo), feature="nope")
        self.assertIn("There is no claims file for nope", str(code))


class VerifyWithAnApproval(WithATempDir):
    def setUp(self):
        super().setUp()
        self.app, app_url = serve(App)
        self.svc, self.svc_url = serve(FakeService)
        (self.repo / ".groundtruth" / "env.json").write_text(json.dumps({"url": app_url}))

    def tearDown(self):
        for s in (self.app, self.svc):
            s.shutdown()
            s.server_close()
        super().tearDown()

    def verify(self, **kw):
        said = []
        run_dir = run_verify(self.repo, "tabs", self.svc_url, say=said.append, **kw)
        return said, json.loads((run_dir / "run.json").read_text()), run_dir

    def test_without_a_record_nothing_changes(self):
        said, run, run_dir = self.verify()
        self.assertNotIn("approval", run)
        self.assertFalse([s for s in said if "approved" in s])
        doc, _ = rundoc.build(run_dir, repo=self.repo)
        self.assertEqual([(p["approval"], p["approval_changed"]) for p in doc["properties"]], [(None, []), (None, [])])

    def test_changed_and_new_are_said_saved_and_shown(self):
        approval.write_record(self.repo, "tabs", claims_of(self.repo))
        self.edit("No test is missing", "No test is lost")
        self.edit("A run with both.", "A run with both kinds.")
        (self.repo / ".groundtruth" / "features" / "tabs.yml").write_text(
            (self.repo / ".groundtruth" / "features" / "tabs.yml").read_text() + "  - id: film\n    statement: Each film is its own.\n")
        said, run, run_dir = self.verify(only="tabs,result")
        self.assertEqual(said[0], "changed since it was approved: tabs (the sentence, the variations)")   # before the run starts
        self.assertTrue(said[1].startswith("Run "))
        self.assertNotIn("film", " ".join(said))                       # not in this run
        self.assertEqual(run["approval"]["properties"], {"tabs": {"state": "changed", "changed": ["statement", "variations"]},
                                                         "result": {"state": "approved", "changed": []}})
        self.assertTrue(run["approval"]["approved_at"])
        self.assertNotIn("approv", (run_dir / "sent.json").read_text())   # nothing about approval leaves the machine
        # the document keeps the run's own comparison, whatever the file says later
        approval.write_record(self.repo, "tabs", claims_of(self.repo))
        doc, _ = rundoc.build(run_dir, repo=self.repo)
        got = {p["id"]: (p["approval"], p["approval_changed"]) for p in doc["properties"]}
        self.assertEqual(got, {"tabs": ("changed", ["statement", "variations"]), "result": ("approved", []), "film": (None, [])})

    def test_a_new_property_is_said(self):
        approval.write_record(self.repo, "tabs", claims_of(self.repo)[:1])
        said, run, _ = self.verify()
        self.assertEqual(said[0], "not approved yet: result")
        self.assertEqual(run["approval"]["properties"]["result"], {"state": "new", "changed": []})


class RegisterTriesTheReset(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "app"
        self.repo.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def register(self, reset, skip=False, **kw):
        a = dict(repo=str(self.repo), url="http://127.0.0.1:9", pid=None, reset=reset, routes=None, project=None,
                 no_repo=False, no_pid=True, skip_reset_check=skip)
        a.update(kw)
        return call(groundtruth.cmd_register, **a)

    def env(self):
        return self.repo / ".groundtruth" / "env.json"

    def test_a_working_reset_is_run_once_in_the_repo(self):
        out, code = self.register("echo x >> reset-ran")
        self.assertEqual(code, 0)
        self.assertEqual((self.repo / "reset-ran").read_text(), "x\n")
        self.assertIn("(tried once now; it worked): echo x >> reset-ran", out)
        self.assertEqual(json.loads(self.env().read_text())["reset"], "echo x >> reset-ran")

    def test_a_failing_reset_writes_nothing(self):
        out, code = self.register("echo " + "a" * 400 + "; echo the-end >&2; exit 3")
        self.assertEqual(out, "")
        self.assertIn("The reset command failed (exit 3).", code)
        self.assertIn("the-end", code)
        self.assertNotIn("a" * 300, code)                      # only the last 300 characters
        self.assertIn("--skip-reset-check", code)
        self.assertFalse(self.env().exists())

    def test_a_reset_that_hangs_writes_nothing(self):
        with mock.patch.object(groundtruth, "RESET_TIMEOUT", 0.5):
            _, code = self.register("echo started; sleep 5")
        self.assertIn("did not finish within 0.5 seconds", code)
        self.assertFalse(self.env().exists())

    def test_skipping_the_check_stores_it_untried(self):
        out, code = self.register("exit 3", skip=True)
        self.assertEqual(code, 0)
        self.assertIn("(not tried now): exit 3", out)
        self.assertTrue(self.env().exists())

    def test_no_reset_tries_nothing(self):
        out, code = self.register(None)
        self.assertEqual(code, 0)
        self.assertIn("no reset command", out)


class UpAndStatus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.run_dir = self.root / "run"
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.stop_started)

    def stop_started(self):
        for f in self.run_dir.glob("*.pid") if self.run_dir.is_dir() else []:
            try:
                os.killpg(int(f.read_text()), signal.SIGTERM)
            except (OSError, ValueError):
                pass

    def test_start_twice_then_status(self):
        sp, tp = free_port(), free_port()
        said = []
        self.assertTrue(groundtruth.bring_up(sp, tp, self.run_dir, store_data=self.root / "store", say=said.append))
        self.assertEqual(len(said), 2)
        self.assertRegex(said[0], rf"^Verifier service: started at http://127.0.0.1:{sp} \(pid \d+, log .*service-{sp}.log\)$")
        self.assertRegex(said[1], rf"^Results store: started at http://127.0.0.1:{tp} \(pid \d+, log .*store-{tp}.log\)$")
        self.assertTrue((self.root / "store" / "token").is_file())     # its data went where it was told
        said = []
        self.assertTrue(groundtruth.bring_up(sp, tp, self.run_dir, say=said.append))
        self.assertTrue(said[0].startswith(f"Verifier service: already running at http://127.0.0.1:{sp} (version "))
        self.assertEqual(said[1], f"Results store: already running at http://127.0.0.1:{tp}")
        lines, ok = groundtruth.status_lines(sp, tp)
        self.assertTrue(ok)
        self.assertRegex(lines[0], rf"^Verifier service: running at http://127.0.0.1:{sp}, version \S+")
        self.assertEqual(lines[1], f"Results store: running at http://127.0.0.1:{tp}")
        lines, ok = groundtruth.status_lines(tp, sp)                    # each on the other's port
        self.assertFalse(ok)
        self.assertEqual(lines, [f"Verifier service: not running; port {tp} has the results store on it",
                                 f"Results store: not running; port {sp} has the verifier service on it"])

    def test_what_up_starts_outlives_the_command(self):
        import subprocess
        sp, tp = free_port(), free_port()
        code = (f"import sys; sys.path.insert(0, {str(ROOT)!r}); import groundtruth; from pathlib import Path; "
                f"sys.exit(0 if groundtruth.bring_up({sp}, {tp}, Path({str(self.run_dir)!r}), store_data=Path({str(self.root / 'store')!r})) else 1)")
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=90)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)           # the command has exited
        self.assertIn("Verifier service: started", r.stdout)
        said = []
        self.assertTrue(groundtruth.bring_up(sp, tp, self.run_dir, say=said.append))
        self.assertTrue(said[0].startswith("Verifier service: already running"), said)
        self.assertTrue(said[1].startswith("Results store: already running"), said)

    def test_nothing_running(self):
        sp, tp = free_port(), free_port()
        lines, ok = groundtruth.status_lines(sp, tp)
        self.assertFalse(ok)
        self.assertEqual(lines, [f"Verifier service: not running (nothing answers on port {sp})",
                                 f"Results store: not running (nothing answers on port {tp})"])

    def test_a_port_taken_by_something_else_is_left_alone(self):
        class Other(Quiet):
            def do_GET(self):
                self.reply(404, {"nope": 1})
        srv, _ = serve(Other)
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        port = srv.server_address[1]
        said = []
        with mock.patch("subprocess.Popen", side_effect=AssertionError("nothing may be started")):
            self.assertFalse(groundtruth.bring_up(port, port, self.run_dir, say=said.append))
        self.assertEqual(said[0], f"Verifier service: port {port} is taken by something else: it answered /health with 404. "
                                  "Nothing was started. Stop it, or choose another port with --service-port.")
        self.assertFalse(self.run_dir.exists())

    def test_the_defaults_match_verify_and_push(self):
        self.assertEqual((groundtruth.SERVICE_PORT, groundtruth.STORE_PORT), (8790, 8900))
        self.assertEqual(groundtruth.DEFAULT_STORE, "http://127.0.0.1:8900")
        if not os.environ.get("GT_SERVICE"):
            self.assertEqual(groundtruth.DEFAULT_SERVICE, "http://127.0.0.1:8790")


def hand_made_run(repo: Path, run_id="20261112-101500"):
    run_dir = repo / ".groundtruth" / "runs" / run_id
    run_dir.mkdir(parents=True)
    held = {"id": "only-new", "result": {"outcome": "holds", "observed": "two cards"}}
    broke = {"id": "v2", "result": {"outcome": "breaks", "observed": "The run's page showed  test t3 in both tabs. " + "x" * 400}}
    asked = {"id": "c1", "result": {"outcome": "declined", "observed": "It refused.", "question": "The page hides failed tests; is that intended?"}}
    stuck = {"id": "c2", "result": {"outcome": "cant_run", "observed": "not started",
                                    "data_request": {"state": "A run with two failing tests.",
                                                     "tool": {"name": "make_run", "does": "Makes a run in the store.",
                                                              "takes": {"failing": {"type": "integer"}, "films": {"type": "boolean", "optional": True}}}}}}
    run = {"run": run_id, "feature": "tabs-trial", "feature_file": "tabs",
           "env": {"url": "http://127.0.0.1:9"}, "service": {"version": "3"},
           "properties": [{"id": "tabs", "statement": "New has one card.", "verdict": "fails", "cases": [held, broke]},
                          {"id": "result", "statement": "A test shows as passing only if none of its variations broke.",
                           "verdict": "needs_decision", "cases": [asked, stuck]},
                          {"id": "film", "statement": "Each variation plays its own film.", "verdict": "holds",
                           "cases": [{"id": "first", "result": {"outcome": "holds", "observed": "its own film"}}]}],
           "approval": {"approved_at": "2026-11-12T09:00:00",
                        "properties": {"tabs": {"state": "changed", "changed": ["statement"]}, "result": {"state": "new", "changed": []},
                                       "film": {"state": "approved", "changed": []}}}}
    (run_dir / "run.json").write_text(json.dumps(run))
    (run_dir / "sent.json").write_text(json.dumps({"properties": [{"id": "tabs", "title": "Right tests in each tab",
                                                                    "variations": [{"id": "only-new", "what": "x"}, {"id": "v2", "what": "y"}]},
                                                                   {"id": "result"}, {"id": "film", "title": "Its own film"}]}))
    return run_dir


class Summary(WithATempDir):
    def test_text(self):
        run_dir = hand_made_run(self.repo)
        (run_dir / "pushed.json").write_text(json.dumps({"store": "http://s", "id": "abc", "url": "http://s/run.html?run=abc"}))
        out, code = call(groundtruth.cmd_summary, repo=str(self.repo), run=None, json=False)
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertEqual(lines[0], "Run 20261112-101500 of tabs-trial, 2026-11-12 10:15:00: 1 holds, 1 fails, 1 needs your decision; 2 of 5 cases held.")
        self.assertEqual(lines.pop(4), "  Its own film (film): holds, 1 of 1 case held.")
        self.assertEqual(lines[1:4], ["Properties:",
                                      "  Right tests in each tab (tabs): fails, 1 of 2 variations held.",
                                      "  A test shows as passing only if none of its variations broke (result): needs your decision, 0 of 2 cases held."])
        self.assertEqual(lines[4], "Did not hold:")
        self.assertTrue(lines[5].startswith("  tabs/v2: broke. The run's page showed test t3 in both tabs. xxx"))
        self.assertEqual(len(lines[5]), len("  tabs/v2: broke. ") + 300)
        self.assertEqual(lines[6], "  result/c1: needs your decision. Question: The page hides failed tests; is that intended?")
        self.assertEqual(lines.pop(7), "    To answer: groundtruth answer result c1 intended   (or: bug)")
        self.assertEqual(lines[7], "  result/c2: could not run. The starting state it needed: A run with two failing tests. It asks for a "
                                   "data tool, make_run: Makes a run in the store. Takes: failing (integer); films (boolean, optional).")
        self.assertEqual(lines[8:], ["Approval:", "  changed since it was approved: tabs (the sentence)", "  not approved yet: result",
                                     "Dashboard: http://s/run.html?run=abc",
                                     "Feature page: http://s/feature.html?project=app&feature=tabs-trial"])

    def test_json(self):
        hand_made_run(self.repo)
        out, code = call(groundtruth.cmd_summary, repo=str(self.repo), run="20261112-101500", json=True)
        s = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(s["properties_by_verdict"], {"holds": 1, "fails": 1, "needs_decision": 1})
        self.assertEqual((s["properties"][2]["verdict"], s["properties"][2]["held"], s["properties"][2]["approval"]), ("holds", 1, "approved"))
        self.assertEqual([(c["property"], c["case"], c["outcome"]) for c in s["not_held"]],
                         [("tabs", "v2", "breaks"), ("result", "c1", "declined"), ("result", "c2", "cant_run")])
        self.assertEqual(s["not_held"][2]["data_request"]["tool"]["name"], "make_run")
        self.assertEqual(s["properties"][0]["approval"], "changed")
        self.assertIsNone(s["dashboard"])

    def test_a_run_still_going_is_said_plainly(self):
        hand_made_run(self.repo)
        (self.repo / ".groundtruth" / "runs" / "20261112-110000").mkdir()
        out, code = call(groundtruth.cmd_summary, repo=str(self.repo), run=None, json=False)
        self.assertEqual(out, "")
        self.assertIn("Run 20261112-110000 has no results yet: it is still running, or it stopped before saving them", code)
        _, code = call(groundtruth.cmd_summary, repo=str(self.repo), run="nope", json=False)
        self.assertIn("There is no run nope", code)

    def test_push_saves_what_the_store_answered(self):
        from store.server import start_in_background
        run_dir = hand_made_run(self.repo)
        srv, url = start_in_background(self.root / "data", 0, self.root / "dashboard")
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        token = (self.root / "data" / "token").read_text().strip()
        with mock.patch.dict(os.environ, {"GT_STORE_TOKEN": token}):
            out, code = call(groundtruth.cmd_push, repo=str(self.repo), run=None, store=url, project="shop")
        self.assertEqual(code, 0)
        pushed = json.loads((run_dir / "pushed.json").read_text())
        self.assertEqual(pushed["store"], url)
        self.assertEqual(out.splitlines()[-1], f"Uploaded. Dashboard: {pushed['url']}")
        s, _ = call(groundtruth.cmd_summary, repo=str(self.repo), run=None, json=False)
        self.assertEqual(s.splitlines()[-2:], [f"Dashboard: {pushed['url']}", f"Feature page: {url}/feature.html?project=shop&feature=tabs-trial"])
        with mock.patch.dict(os.environ, {"GT_STORE_TOKEN": token}):
            said = []
            got = groundtruth.upload_step(run_dir, self.repo, url, project="shop", say=said.append)
        self.assertEqual(json.loads((run_dir / "pushed.json").read_text())["url"], got)
        self.assertEqual(said[-2:], [f"Dashboard: {got}", f"Feature page: {url}/feature.html?project=shop&feature=tabs-trial"])


class InstallSkills(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.skills, self.project = root / "skills", root / "project"
        self.project.mkdir()
        self.project = self.project.resolve()
        self.launcher = Path("/opt/gt/bin/groundtruth")

    def run_it(self, remove=False):
        said = []
        ok = groundtruth.install_skills(self.project, self.skills, self.launcher, remove=remove, say=said.append)
        return ok, said

    def test_no_skills_yet(self):
        ok, said = self.run_it()
        self.assertFalse(ok)
        self.assertIn("There are no skills to install", said[0])
        self.skills.mkdir()
        self.assertFalse(self.run_it()[0])
        self.assertFalse((self.project / ".claude").exists())

    def test_install_and_remove(self):
        for name in ("verify-claims", "write-claims"):
            (self.skills / name).mkdir(parents=True)
            (self.skills / name / "SKILL.md").write_text(f"# {name}\nRun `{{{{GROUNDTRUTH}}}} status` then {{{{GROUNDTRUTH}}}} up.\n")
        (self.skills / "notes").mkdir()                                  # a folder without a SKILL.md is not a skill
        ok, said = self.run_it()
        self.assertTrue(ok)
        dest = self.project / ".claude" / "skills"
        self.assertEqual(said[:2], [f"Wrote {dest / 'verify-claims' / 'SKILL.md'}", f"Wrote {dest / 'write-claims' / 'SKILL.md'}"])
        self.assertEqual((dest / "write-claims" / "SKILL.md").read_text(),
                         "# write-claims\nRun `/opt/gt/bin/groundtruth status` then /opt/gt/bin/groundtruth up.\n")
        self.assertEqual(said[-1], '  "Bash(/opt/gt/bin/groundtruth *)"')
        self.assertIn("settings.local.json", said[-2])
        self.assertFalse((self.project / ".claude" / "settings.local.json").exists())
        self.assertFalse((dest / "notes").exists())
        (dest / "write-claims" / "extra.md").write_text("mine")         # something the user added stays
        (dest / "other-skill").mkdir()
        ok, said = self.run_it(remove=True)
        self.assertTrue(ok)
        self.assertEqual(len(said), 2)
        self.assertFalse((dest / "verify-claims").exists())
        self.assertEqual(sorted(p.name for p in (dest / "write-claims").iterdir()), ["extra.md"])
        self.assertTrue((dest / "other-skill").is_dir())
        ok, said = self.run_it(remove=True)
        self.assertIn("Nothing to remove", said[0])

    def test_install_for_copilot(self):
        # GitHub Copilot reads the same SKILL.md format from .github/skills; no Claude Code settings are mentioned.
        (self.skills / "groundtruth").mkdir(parents=True)
        (self.skills / "groundtruth" / "SKILL.md").write_text("Run `{{GROUNDTRUTH}} claims`.\n")
        said = []
        self.assertTrue(groundtruth.install_skills(self.project, self.skills, self.launcher, say=said.append, agent="copilot"))
        dest = self.project / ".github" / "skills" / "groundtruth" / "SKILL.md"
        self.assertEqual(dest.read_text(), "Run `/opt/gt/bin/groundtruth claims`.\n")
        self.assertEqual(said, [f"Wrote {dest}"])
        self.assertFalse((self.project / ".claude").exists())
        self.assertTrue(groundtruth.install_skills(self.project, self.skills, self.launcher, remove=True, say=said.append, agent="copilot"))
        self.assertFalse(dest.exists())
        said = []
        self.assertFalse(groundtruth.install_skills(self.project, self.skills, self.launcher, say=said.append, agent="cursor"))
        self.assertIn("no skills folder known", said[0])


class Launcher(unittest.TestCase):
    def test_without_its_python_it_says_so_in_one_line(self):
        import shutil
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "bin").mkdir()
            shutil.copy2(ROOT / "bin" / "groundtruth", Path(d) / "bin" / "groundtruth")
            r = subprocess.run([str(Path(d) / "bin" / "groundtruth"), "status"], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")
        self.assertEqual(len(r.stderr.strip().splitlines()), 1)
        self.assertIn("there is no Python at", r.stderr)

    def test_it_runs_the_command_line_from_anywhere(self):
        import subprocess
        launcher = ROOT / "bin" / "groundtruth"
        self.assertTrue(os.access(launcher, os.X_OK))
        sp, tp = free_port(), free_port()
        r = subprocess.run([str(launcher), "status", "--service-port", str(sp), "--store-port", str(tp)],
                           cwd=tempfile.gettempdir(), capture_output=True, text=True, timeout=60)
        if not (ROOT / ".venv" / "bin" / "python").exists():
            self.assertIn("there is no Python at", r.stderr)
            return
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"Verifier service: not running (nothing answers on port {sp})", r.stdout)


if __name__ == "__main__":
    unittest.main()
