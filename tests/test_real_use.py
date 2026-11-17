"""Learning from real use: where each claim came from (source), what the PR and its issues said (context), what the
owner changed before approving (draft changes), how setting up went (the command log), and feedback kept by the
store. No model; `gh` is a fake script on PATH."""
import json
import os
import shlex
import sqlite3
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

import groundtruth
from store.db import token_hash
from toolserver import approval, rundoc
from toolserver.context import fetch
from toolserver.runner import ServiceError, claim_source

from tests.test_agent_commands import FEATURE, App, FakeService, call, make_repo, serve
from tests.test_store import StoreCase, document


class TheSource(unittest.TestCase):
    def test_parsed_in_plain_words(self):
        self.assertIsNone(claim_source({"id": "p1"}, "f.yml"))
        self.assertEqual(claim_source({"id": "p1", "source": {"kind": "issue", "ref": "acme/dining-app#786", "quote": " A meal shows its stations. "}}, "f.yml"),
                         {"kind": "issue", "ref": "acme/dining-app#786", "quote": "A meal shows its stations."})
        self.assertEqual(claim_source({"id": "p1", "source": {"kind": "interview"}}, "f.yml"), {"kind": "interview", "ref": "", "quote": ""})
        for bad, words in (("issue", "not a set of"), ({"kind": "email"}, "must be exactly issue, pull request, interview or other"),
                           ({"kind": "issue", "ref": "x" * 201}, "source `ref`"), ({"kind": "issue", "quote": "x" * 601}, "source `quote`"),
                           ({"kind": "issue", "url": "x"}, "not a set of")):
            with self.subTest(words=words), self.assertRaises(ServiceError) as e:
                claim_source({"id": "p1", "source": bad}, "f.yml")
            self.assertIn(words, str(e.exception))


SOURCED = FEATURE.replace('''    your_words: "all properties should appear in the right tab"
''', '''    your_words: "all properties should appear in the right tab"
    source:
      kind: issue
      ref: acme/dining-app#786
      quote: Every test written for a run should show up in New.
''')


class ThePrintoutDraftsAndApproval(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = make_repo(Path(self.tmp.name), SOURCED)
        self.file = self.repo / ".groundtruth" / "features" / "tabs.yml"

    def test_the_source_line_sits_under_your_words_and_is_not_approved(self):
        out, code = call(groundtruth.cmd_claims, repo=str(self.repo), feature="tabs", full=True)
        lines = out.splitlines()
        i = lines.index("   Your words: “all properties should appear in the right tab”")
        self.assertEqual(lines[i + 1], '   Source: issue acme/dining-app#786: "Every test written for a run should show up in New."')
        self.assertEqual(sum(line.startswith("   Source:") for line in lines), 1)          # only where present
        call(groundtruth.cmd_approve, repo=str(self.repo), feature="tabs")
        self.file.write_text(self.file.read_text().replace("ref: acme/dining-app#786", "ref: acme/dining-app#787"))
        self.assertIn("Approval: approved", call(groundtruth.cmd_claims, repo=str(self.repo), feature="tabs", full=True)[0])
        self.assertNotIn("source", approval.load_record(self.repo, "tabs")["properties"]["tabs"])
        self.file.write_text(self.file.read_text().replace("kind: issue", "kind: email"))
        _, code = call(groundtruth.cmd_claims, repo=str(self.repo), feature="tabs")
        self.assertIn("must be exactly issue, pull request, interview or other", str(code))

    def test_a_draft_is_kept_once_and_approve_lists_every_kind_of_change(self):
        call(groundtruth.cmd_claims, repo=str(self.repo), feature="tabs")
        draft = approval.load_draft(self.repo, "tabs")
        self.assertEqual(draft["properties"]["tabs"]["source"]["ref"], "acme/dining-app#786")
        kept = (self.repo / ".groundtruth" / "drafts" / "tabs.json").read_text()
        text = self.file.read_text()
        text = text.replace("title: Right tests in each tab", "title: The right tests in each tab")
        text = text.replace("      - A run with both.\n", "      - A run with both kinds.\n      - A run with none.\n")
        text = text.replace("ref: acme/dining-app#786", "ref: acme/dining-app#790")
        text = text.replace("  - id: result\n    statement: A test shows as passing only if none of its variations broke.\n",
                            "  - id: film\n    statement: Each film is its own.\n")
        self.file.write_text(text)
        call(groundtruth.cmd_claims, repo=str(self.repo), feature="tabs")
        self.assertEqual((self.repo / ".groundtruth" / "drafts" / "tabs.json").read_text(), kept)   # kept once
        out, code = call(groundtruth.cmd_approve, repo=str(self.repo), feature="tabs")
        self.assertEqual(code, 0)
        self.assertIn("5 changes since the claims were first drafted.", out)
        changes = approval.load_record(self.repo, "tabs")["draft_changes"]
        self.assertEqual(changes, [
            {"id": "tabs", "change": "edited", "part": "title", "before": "Right tests in each tab", "after": "The right tests in each tab"},
            {"id": "tabs", "change": "edited", "part": "variations", "before": "A run with only new tests.\nA run with both.",
             "after": "A run with only new tests.\nA run with both kinds.\nA run with none."},
            {"id": "tabs", "change": "edited", "part": "source",
             "before": 'issue acme/dining-app#786: "Every test written for a run should show up in New."',
             "after": 'issue acme/dining-app#790: "Every test written for a run should show up in New."'},
            {"id": "result", "change": "removed", "part": None, "before": "A test shows as passing only if none of its variations broke.", "after": ""},
            {"id": "film", "change": "added", "part": None, "before": "", "after": "Each film is its own."}])

    def test_no_draft_after_an_approval_and_nothing_changed(self):
        call(groundtruth.cmd_claims, repo=str(self.repo), feature="tabs")
        out, _ = call(groundtruth.cmd_approve, repo=str(self.repo), feature="tabs")
        self.assertIn("0 changes since the claims were first drafted.", out)
        self.assertEqual(approval.load_record(self.repo, "tabs")["draft_changes"], [])
        other = make_repo(Path(self.tmp.name) / "b", SOURCED)
        out, _ = call(groundtruth.cmd_approve, repo=str(other), feature="tabs")          # approved before any printout
        self.assertIn("No draft was kept", out)
        self.assertIsNone(approval.load_record(other, "tabs")["draft_changes"])
        call(groundtruth.cmd_claims, repo=str(other), feature="tabs")
        self.assertFalse((other / ".groundtruth" / "drafts" / "tabs.json").exists())      # a record exists: no draft


FAKE_GH = """#!{py}
import json, sys, time
args = sys.argv[1:]
mode = {mode!r}
if mode == "slow":
    time.sleep(30)
if mode == "nopr" and args[:2] == ["pr", "view"]:
    sys.stderr.write("no pull requests found for branch"); sys.exit(1)
if args[:2] == ["pr", "view"]:
    print(json.dumps({{"number": 12, "title": "Show stations", "body": "Fixes #7 and also closes #9.\\n" + "x" * 7000,
                      "url": "https://github.com/o/r/pull/12", "state": "OPEN", "closingIssuesReferences": [{{"number": 7}}]}}))
elif args[:2] == ["issue", "view"]:
    n = int(args[2])
    print(json.dumps({{"number": n, "title": f"Issue {{n}}", "body": "As a diner I want to see stations.", "url": f"https://github.com/o/r/issues/{{n}}"}}))
else:
    sys.exit(2)
"""


class TheContext(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bin = Path(self.tmp.name) / "bin"
        self.bin.mkdir()

    def gh(self, mode):
        f = self.bin / "gh"
        f.write_text(FAKE_GH.format(py=sys.executable, mode=mode))
        f.chmod(0o755)
        return mock.patch.dict(os.environ, {"PATH": f"{self.bin}{os.pathsep}/usr/bin:/bin"})

    def test_a_pull_request_with_its_closing_issues(self):
        with self.gh("ok"):
            got = fetch(self.tmp.name)
        pr = got["pull_request"]
        self.assertEqual((pr["number"], pr["title"], pr["state"], pr["url"]), (12, "Show stations", "OPEN", "https://github.com/o/r/pull/12"))
        self.assertEqual(len(pr["body"]), 6000)
        self.assertEqual([(i["number"], i["title"]) for i in got["issues"]], [(7, "Issue 7"), (9, "Issue 9")])
        self.assertTrue(got["fetched_at"])

    def test_no_pull_request_no_gh_and_too_slow_give_none_quickly(self):
        with self.gh("nopr"):
            self.assertIsNone(fetch(self.tmp.name))
        with mock.patch.dict(os.environ, {"PATH": str(Path(self.tmp.name) / "empty")}):
            self.assertIsNone(fetch(self.tmp.name))
        with self.gh("slow"):
            t0 = time.monotonic()
            self.assertIsNone(fetch(self.tmp.name, timeout=1.0))
            self.assertLess(time.monotonic() - t0, 3)

    def test_verify_keeps_it_and_never_sends_it(self):
        repo = make_repo(Path(self.tmp.name), SOURCED)
        app, app_url = serve(App)
        svc, svc_url = serve(FakeService)
        for s in (app, svc):
            self.addCleanup(s.server_close)
            self.addCleanup(s.shutdown)
        (repo / ".groundtruth" / "env.json").write_text(json.dumps({"url": app_url}))
        from toolserver.runner import run_verify
        ids = iter(f"20261114-1000{n:02d}" for n in range(10))
        with self.gh("ok"), mock.patch("toolserver.runner.time.strftime", lambda fmt, *a: next(ids)):
            said = []
            run_dir = run_verify(repo, "tabs", svc_url, say=said.append)
            sent = json.dumps(FakeService.started[-1])
            quiet = []
            skipped = run_verify(repo, "tabs", svc_url, say=quiet.append, no_context=True)
        run = json.loads((run_dir / "run.json").read_text())
        self.assertEqual(run["context"]["pull_request"]["number"], 12)
        self.assertNotIn("Show stations", sent)
        self.assertNotIn("dining-app", sent)                              # the source is never sent either
        self.assertEqual(run["sources"]["tabs"]["ref"], "acme/dining-app#786")
        doc, _ = rundoc.build(run_dir, repo=repo)
        self.assertEqual(doc["context"]["issues"][0]["number"], 7)
        self.assertEqual(doc["properties"][0]["source"], {"kind": "issue", "ref": "acme/dining-app#786",
                                                         "quote": "Every test written for a run should show up in New."})
        self.assertIsNone(doc["properties"][1]["source"])
        self.assertIsNone(json.loads((skipped / "run.json").read_text())["context"])
        self.assertFalse([s for s in quiet if "pull request" in s])
        with mock.patch.dict(os.environ, {"PATH": str(Path(self.tmp.name) / "empty")}):
            said = []
            run_verify(repo, "tabs", svc_url, say=said.append)
        self.assertIn("No pull request was found to keep with this run.", said)


class TheLogAndSetup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = make_repo(Path(self.tmp.name))

    def run_cmd(self, fn, **kw):
        import argparse
        a = argparse.Namespace(fn=fn, **kw)
        try:
            groundtruth.run_logged(a)
        except SystemExit:
            pass

    def test_each_command_leaves_one_line_and_never_its_arguments(self):
        with mock.patch("sys.stdout"):
            self.run_cmd(groundtruth.cmd_claims, repo=str(self.repo), feature="tabs")
            self.run_cmd(groundtruth.cmd_claims, repo=str(self.repo), feature="nope-secret")
        lines = [json.loads(x) for x in (self.repo / ".groundtruth" / "log.jsonl").read_text().splitlines()]
        self.assertEqual([(x["command"], x["ok"]) for x in lines], [("claims", True), ("claims", False)])
        self.assertIsNone(lines[0]["error"])
        self.assertTrue(lines[1]["error"].startswith("There is no claims file for nope-secret"))
        self.assertLessEqual(len(lines[1]["error"]), 200)
        self.assertEqual(set(lines[0]), {"command", "at", "seconds", "ok", "error"})
        self.assertNotIn("tabs", json.dumps(lines[0]))

    def test_a_folder_without_groundtruth_and_an_unwritable_log_never_fail(self):
        nowhere = Path(self.tmp.name) / "plain"
        nowhere.mkdir()
        self.run_cmd(lambda a: None, repo=str(nowhere))
        self.assertFalse((nowhere / ".groundtruth").exists())
        (self.repo / ".groundtruth" / "log.jsonl").mkdir()                 # can't be appended to
        self.run_cmd(lambda a: None, repo=str(self.repo))

    def test_setup_in_the_document(self):
        runs = self.repo / ".groundtruth" / "runs" / "20261114-120000"
        runs.mkdir(parents=True)
        (runs / "run.json").write_text(json.dumps({"run": "20261114-120000", "feature": "tabs-trial", "env": {"url": "u"}, "properties": []}))
        self.assertIsNone(rundoc.build(runs)[0]["setup"])
        log = [{"command": "register", "at": "2026-11-14T09:00:00", "seconds": 1, "ok": False, "error": "Nothing is listening on port 3000."},
               {"command": "register", "at": "2026-11-14T09:01:00", "seconds": 1, "ok": True, "error": None},
               {"command": "claims", "at": "2026-11-14T09:02:00", "seconds": 0.2, "ok": True, "error": None},
               {"command": "verify", "at": "2026-11-14T09:05:00", "seconds": 300, "ok": True, "error": None},
               {"command": "push", "at": "2026-11-14T09:11:00", "seconds": 3, "ok": False, "error": "later"}]
        (self.repo / ".groundtruth" / "log.jsonl").write_text("\n".join(json.dumps(x) for x in log) + "\nnot json\n")
        self.assertIsNone(rundoc.build(runs)[0]["setup"])                   # a broken log is no log
        (self.repo / ".groundtruth" / "log.jsonl").write_text("\n".join(json.dumps(x) for x in log) + "\n")
        self.assertEqual(rundoc.build(runs)[0]["setup"], {"first_command_at": "2026-11-14T09:00:00", "commands_before_first_run": 3,
                                                          "failed_commands": [{"command": "register", "error": "Nothing is listening on port 3000."}]})

    def test_draft_changes_in_the_document(self):
        runs = self.repo / ".groundtruth" / "runs" / "20261114-130000"
        runs.mkdir(parents=True)
        base = {"run": "20261114-130000", "feature": "tabs-trial", "env": {"url": "u"}, "properties": []}
        for approval_state, want in ((None, None), ({"approved_at": "x", "properties": {}}, None),
                                     ({"approved_at": "x", "properties": {}, "draft_changes": []}, []),
                                     ({"approved_at": "x", "properties": {}, "draft_changes": [
                                         {"id": "a", "change": "added", "part": None, "before": "", "after": "s", "extra": 1}]},
                                      [{"id": "a", "change": "added", "part": None, "before": "", "after": "s"}])):
            (runs / "run.json").write_text(json.dumps({**base, **({"approval": approval_state} if approval_state else {})}))
            self.assertEqual(rundoc.build(runs)[0]["draft_changes"], want)


class TheFeedbackRoutes(StoreCase):
    def setUp(self):
        super().setUp()
        doc = document()
        doc["properties"] = [{"id": "tabs", "cases": [{"id": "only-new"}, {"id": "v2"}]}, {"id": "result", "cases": []}]
        status, made = self.js("POST", "/v1/runs", {"document": doc, "files": []}, token=self.token)
        self.rid = made["id"]

    def test_post_read_and_delete_from_this_machine(self):
        status, saved = self.js("POST", f"/v1/runs/{self.rid}/feedback", {"property": "tabs", "case": "v2", "kind": "wrong_result",
                                                                          "note": " The old test is missing. "})
        self.assertEqual(status, 201, saved)
        self.assertEqual({k: saved[k] for k in ("property", "case", "kind", "note")},
                         {"property": "tabs", "case": "v2", "kind": "wrong_result", "note": "The old test is missing."})
        status, more = self.js("POST", f"/v1/runs/{self.rid}/feedback", {"property": "result", "kind": "right"})
        self.assertEqual((status, more["case"], more["note"]), (201, None, ""))
        status, got = self.js("GET", f"/v1/runs/{self.rid}/feedback")
        self.assertEqual([f["id"] for f in got["feedback"]], [saved["id"], more["id"]])
        status, _, _ = self.req("DELETE", f"/v1/runs/{self.rid}/feedback/{saved['id']}")
        self.assertEqual(status, 204)
        self.assertEqual(self.req("DELETE", f"/v1/runs/{self.rid}/feedback/{saved['id']}")[0], 404)
        self.assertEqual([f["id"] for f in self.js("GET", f"/v1/runs/{self.rid}/feedback")[1]["feedback"]], [more["id"]])

    def test_validation(self):
        for body, words in (({"property": "tabs", "kind": "meh"}, "kind must be"), ({"property": "nope", "kind": "right"}, "property must be"),
                            ({"property": "tabs", "case": "c9", "kind": "right"}, "case must be the id of a case of tabs"),
                            ({"property": "result", "case": "v2", "kind": "right"}, "case must be the id of a case of result"),
                            ({"property": "tabs", "kind": "right", "note": "x" * 1001}, "at most 1,000"), ([1], "The body must be")):
            status, got = self.js("POST", f"/v1/runs/{self.rid}/feedback", body)
            self.assertEqual(status, 400, body)
            self.assertIn(words, got["error"])
        self.assertEqual(self.js("POST", "/v1/runs/nosuchrun/feedback", {"property": "tabs", "kind": "right"})[0], 404)

    def test_scoped_by_user_and_no_writes_from_another_site_or_host(self):
        other = "other-token"
        with sqlite3.connect(self.data / "store.db") as c:
            c.execute("INSERT INTO users (name, token_hash, created) VALUES ('someone', ?, 'now')", (token_hash(other),))
        self.assertEqual(self.js("POST", f"/v1/runs/{self.rid}/feedback", {"property": "tabs", "kind": "right"}, token=other)[0], 404)
        self.assertEqual(self.js("GET", f"/v1/runs/{self.rid}/feedback", token=other)[0], 404)
        status, saved = self.js("POST", f"/v1/runs/{self.rid}/feedback", {"property": "tabs", "kind": "right"}, token=self.token)
        self.assertEqual(status, 201)
        self.assertEqual(self.req("DELETE", f"/v1/runs/{self.rid}/feedback/{saved['id']}", token=other)[0], 404)
        status, got = self.js("POST", f"/v1/runs/{self.rid}/feedback", {"property": "tabs", "kind": "right"},
                              headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403, got)
        self.assertEqual(self.js("POST", f"/v1/runs/{self.rid}/feedback", {"property": "tabs", "kind": "right"},
                                 headers={"Origin": f"http://127.0.0.1:{self.port}"})[0], 201)
        self.assertEqual(self.js("POST", f"/v1/runs/{self.rid}/feedback", {"property": "tabs", "kind": "right"},
                                 headers={"Host": "store.example"})[0], 401)


class TheFeedbackCommand(StoreCase):
    def test_posts_for_the_pushed_run_and_refuses_one_never_pushed(self):
        repo = make_repo(Path(self.tmp.name))
        run_dir = repo / ".groundtruth" / "runs" / "20261114-140000"
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text("{}")
        _, code = call(groundtruth.cmd_feedback, repo=str(repo), claim="tabs", variation=None, kind="right", note="", run=None, store=None)
        self.assertIn("was never pushed", str(code))
        doc = document()
        doc["properties"] = [{"id": "tabs", "cases": [{"id": "v2"}]}]
        status, made = self.js("POST", "/v1/runs", {"document": doc, "files": []}, token=self.token)
        (run_dir / "pushed.json").write_text(json.dumps({"store": self.url, "id": made["id"]}))
        with mock.patch.dict(os.environ, {"GT_STORE_TOKEN": self.token}):
            out, code = call(groundtruth.cmd_feedback, repo=str(repo), claim="tabs", variation="v2", kind="not_what_i_meant",
                             note="I meant the tests of the latest run only.", run=None, store=None)
        self.assertEqual(code, 0, out)
        self.assertEqual(out.strip(), "Feedback kept for tabs/v2 in run 20261114-140000: not_what_i_meant.")
        got = self.js("GET", f"/v1/runs/{made['id']}/feedback")[1]["feedback"]
        self.assertEqual([(f["property"], f["case"], f["note"]) for f in got], [("tabs", "v2", "I meant the tests of the latest run only.")])
        with mock.patch.dict(os.environ, {"GT_STORE_TOKEN": self.token}):
            _, code = call(groundtruth.cmd_feedback, repo=str(repo), claim="nope", variation=None, kind="right", note="", run=None, store=None)
        self.assertIn("The store did not keep the feedback: property must be", str(code))


if __name__ == "__main__":
    unittest.main()
