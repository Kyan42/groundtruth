"""The command line the owner agreed on: seven everyday commands run from inside the repo (login, connect, draft,
claims, approve, verify, feedback), two occasional ones (results, answer), every older name still working, and the
service's draft job. No model: the service, the store and `gh` are scripts or local fakes."""
import io
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import yaml

import groundtruth
from service import prompts
from service.harness import Draft, draft_problems
from toolserver import approval, draftfile

from tests.test_agent_commands import FEATURE, App, FakeService, make_repo, serve
from tests.test_real_use import FAKE_GH

CLEAN_ENV = {k: v for k, v in os.environ.items() if k not in ("GT_STORE", "GT_SERVICE", "GT_STORE_TOKEN", "GT_CONFIG")}


def run(argv, cwd=None, stdin=None):
    """groundtruth <argv>, from `cwd`; -> (printed text, exit code)."""
    out, code, old = io.StringIO(), 0, os.getcwd()
    try:
        if cwd:
            os.chdir(cwd)
        with redirect_stdout(out), mock.patch("sys.stdin", io.StringIO(stdin or "")):
            groundtruth.main(argv)
    except SystemExit as e:
        code = e.code
    finally:
        os.chdir(old)
    return out.getvalue(), code


class WithAConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        env = mock.patch.dict(os.environ, {**CLEAN_ENV, "GT_CONFIG": str(self.root / "home" / "config.json")}, clear=True)
        env.start()
        self.addCleanup(env.stop)


# -------------------------------------------------------------------- finding the app and the feature
class FindingTheAppAndTheFeature(WithAConfig):
    def test_from_inside_the_repo_with_one_feature(self):
        repo = make_repo(self.root)
        (repo / "src" / "deep").mkdir(parents=True)
        out, code = run(["claims"], cwd=repo / "src" / "deep")
        self.assertEqual(code, 0, out)
        self.assertTrue(out.startswith("tabs-trial: 2 claims, "), out)                  # the brief printout is the default
        self.assertEqual(run(["claims", "tabs"], cwd=repo)[1], 0)
        self.assertEqual(run(["claims", str(repo), "tabs"])[1], 0)          # an explicit folder still works
        self.assertEqual(run(["claims", str(repo)])[1], 0)

    def test_several_features_are_listed_and_none_found_is_plain(self):
        repo = make_repo(self.root)
        (repo / ".groundtruth" / "features" / "menu.yml").write_text(FEATURE.replace("tabs-trial", "menu"))
        out, code = run(["claims"], cwd=repo)
        self.assertEqual(code, "This app has more than one set of claims; say which: menu, tabs")
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        out, code = run(["claims"], cwd=elsewhere)
        self.assertIn("This folder is not part of an app Groundtruth knows", str(code))
        empty = self.root / "empty"
        (empty / ".groundtruth").mkdir(parents=True)
        self.assertIn("This app has no claims yet", str(run(["claims"], cwd=empty)[1]))

    def test_connect_and_draft_may_start_at_the_git_root(self):
        repo = self.root / "fresh"
        (repo / "src").mkdir(parents=True)
        os.system(f"git -C {repo} init -q")
        old = os.getcwd()
        os.chdir(repo / "src")
        try:
            self.assertEqual(groundtruth.find_app(create=True), repo.resolve())
            with self.assertRaises(SystemExit):
                groundtruth.find_app()
        finally:
            os.chdir(old)


# -------------------------------------------------------------------- login
class Health(BaseHTTPRequestHandler):
    seen = []
    answer = {"ok": True, "version": "3"}

    def do_GET(self):  # noqa: N802
        Health.seen.append(self.headers.get("Authorization"))
        body = json.dumps(Health.answer).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class Login(WithAConfig):
    def setUp(self):
        super().setUp()
        self.srv, self.url = serve(Health)
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def test_saves_the_three_and_they_become_the_defaults_below_flags_and_environment(self):
        out, code = run(["login", "--service", self.url, "--store", "http://127.0.0.1:9999", "--token", "secret"])
        self.assertEqual(code, 0, out)
        self.assertEqual(Health.seen[-1], "Bearer secret")
        f = Path(os.environ["GT_CONFIG"])
        self.assertEqual(json.loads(f.read_text()), {"service": self.url, "store": "http://127.0.0.1:9999", "token": "secret"})
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
        self.assertEqual(groundtruth.service_address(), self.url)
        self.assertEqual(groundtruth.store_address(), "http://127.0.0.1:9999")
        self.assertEqual(groundtruth.store_token(), "secret")
        self.assertTrue(groundtruth.store_configured())
        with mock.patch.dict(os.environ, {"GT_SERVICE": "http://127.0.0.1:1", "GT_STORE": "http://127.0.0.1:2", "GT_STORE_TOKEN": "env"}):
            self.assertEqual((groundtruth.service_address(), groundtruth.store_address(), groundtruth.store_token()),
                             ("http://127.0.0.1:1", "http://127.0.0.1:2", "env"))
            self.assertEqual(groundtruth.service_address("http://flag"), "http://flag")
            self.assertEqual(groundtruth.store_address("http://flag2"), "http://flag2")

    def test_a_token_from_standard_input_and_an_empty_one_for_a_local_service(self):
        self.assertEqual(run(["login", "--service", self.url], stdin="typed\n")[1], 0)
        self.assertEqual(json.loads(Path(os.environ["GT_CONFIG"]).read_text())["token"], "typed")
        self.assertEqual(run(["login", "--service", self.url, "--token", ""])[1], 0)

    def test_what_is_not_saved(self):
        _, code = run(["login", "--service", "https://verifier.example", "--token", ""])
        self.assertIn("needs a token", str(code))
        _, code = run(["login", "--service", "http://127.0.0.1:1", "--token", "t"])
        self.assertIn("Couldn't reach the verifier service", str(code))
        Health.answer = {"hello": 1}
        try:
            _, code = run(["login", "--service", self.url, "--token", "t"])
        finally:
            Health.answer = {"ok": True}
        self.assertIn("not as a Groundtruth verifier service", str(code))
        self.assertFalse(Path(os.environ["GT_CONFIG"]).exists())


# -------------------------------------------------------------------- connect
class Connect(WithAConfig):
    def setUp(self):
        super().setUp()
        self.app, self.url = serve(App)
        self.addCleanup(self.app.server_close)
        self.addCleanup(self.app.shutdown)
        self.repo = self.root / "app"
        (self.repo / ".groundtruth").mkdir(parents=True)

    def test_an_address_not_on_this_machine_is_refused(self):
        for address in ("https://www.example.com", "http://192.168.1.4:3000", "staging.example.org"):
            _, code = run(["connect", address], cwd=self.repo)
            self.assertIn("is not on this machine. The verifier clicks things", str(code), address)
        self.assertFalse((self.repo / ".groundtruth" / "env.json").exists())
        for host in ("localhost", "127.0.0.1", "::1", "app.localhost", "shop.test"):
            self.assertTrue(groundtruth.is_local(host), host)

    def test_it_saves_the_record_and_says_what_the_verifier_can_do_without_looking_for_a_process(self):
        (self.repo / ".groundtruth" / "data.yml").write_text(
            "tools:\n  - name: list_meals\n    does: Lists meals.\n    changes: nothing\n    run: echo '{}'\n"
            "  - name: make_meal\n    does: Makes a meal.\n    run: echo '{}'\n")
        with mock.patch.object(groundtruth, "_out", wraps=groundtruth._out) as out_calls:
            out, code = run(["connect", self.url, "--window", "1280x800", "--project", "shop"], cwd=self.repo)
        self.assertEqual(code, 0, out)
        self.assertFalse([c for c in out_calls.call_args_list if "lsof" in c.args[0] or "ps" in c.args[0]])
        env = json.loads((self.repo / ".groundtruth" / "env.json").read_text())
        self.assertNotIn("pid", env)
        self.assertEqual((env["url"], env["viewport"], env["project"], env["repo"]), (self.url, {"width": 1280, "height": 800}, "shop", str(self.repo)))
        self.assertIn("The verifier's browser window will be 1280 by 800.", out)
        self.assertIn("2 data tools declared, 1 of them only reads.", out)
        self.assertIn("No reset command: cases don't start from clean data, and only data tools that only read can be used.", out)
        out, _ = run(["connect", self.url, "--reset", "true"], cwd=self.repo)
        self.assertIn("every case starts from clean data, and data tools that change data can be used.", out)
        self.assertIn("this replaces the earlier connection", out)
        _, code = run(["connect", "http://127.0.0.1:1"], cwd=self.repo)
        self.assertIn("Nothing answers at http://127.0.0.1:1", str(code))
        _, code = run(["connect", self.url, "--reset", "false"], cwd=self.repo)
        self.assertIn("The reset command failed", str(code))

    def test_problems_in_the_tools_file_are_reported(self):
        (self.repo / ".groundtruth" / "data.yml").write_text("tools: oops\n")
        out, code = run(["connect", self.url], cwd=self.repo)
        self.assertEqual(code, 0)
        self.assertIn("Problem: .groundtruth/data.yml: `tools` must be a list", out)


# -------------------------------------------------------------------- --help and the older names
class HelpAndAliases(unittest.TestCase):
    def test_help_lists_the_seven_then_the_two_then_the_rest(self):
        out, code = run(["--help"])
        self.assertEqual(code, 0)
        head = out[out.index("Everyday commands"):]
        at = [head.index(f"\n  {n} ") for n in groundtruth.EVERYDAY + groundtruth.OCCASIONAL]
        self.assertEqual(at, sorted(at))
        self.assertLess(head.index("Occasional:"), head.index("\n  results "))
        rest = head.index("For running Groundtruth itself")
        self.assertLess(at[-1], rest)
        for n in groundtruth.ITSELF:
            self.assertGreater(head.index(f"\n  {n} "), rest, n)

    def test_every_name_still_parses(self):
        for name in groundtruth.EVERYDAY + groundtruth.OCCASIONAL + groundtruth.ITSELF:
            with self.subTest(name=name):
                self.assertEqual(run([name, "--help"])[1], 0)


# -------------------------------------------------------------------- verify: pushing by default, ending with the summary
class Verify(WithAConfig):
    def setUp(self):
        super().setUp()
        self.repo = make_repo(self.root)
        self.app, app_url = serve(App)
        self.svc, self.svc_url = serve(FakeService)
        from store.server import start_in_background
        self.store, self.store_url = start_in_background(self.root / "data", 0, self.root / "dash")
        for s in (self.app, self.svc, self.store):
            self.addCleanup(s.server_close)
            self.addCleanup(s.shutdown)
        (self.repo / ".groundtruth" / "env.json").write_text(json.dumps({"url": app_url}))
        os.environ["GT_STORE_TOKEN"] = (self.root / "data" / "token").read_text().strip()
        os.environ["PATH"] = str(self.root / "no-gh")
        import itertools
        import time as _time
        count, real = itertools.count(), _time.strftime
        # one run folder per verify, however fast; every other use of the clock is left alone
        p = mock.patch("toolserver.runner.time.strftime",
                       lambda fmt, *a: f"20261114-15{next(count):04d}" if fmt == "%Y%m%d-%H%M%S" else real(fmt, *a))
        p.start()
        self.addCleanup(p.stop)

    def latest(self):
        return sorted((self.repo / ".groundtruth" / "runs").iterdir())[-1]

    def test_no_store_configured_no_upload_and_the_summary_at_the_end(self):
        out, code = run(["verify", "tabs", "--service", self.svc_url], cwd=self.repo)
        self.assertEqual(code, 0, out)
        self.assertFalse((self.latest() / "pushed.json").exists())
        tail = out[out.index("\nRun "):]
        self.assertIn("Properties:", tail)
        self.assertIn("  Right tests in each tab (tabs): holds, 2 of 2 variations held.", tail)

    def test_a_configured_store_gets_the_run_unless_told_not_to(self):
        Path(os.environ["GT_CONFIG"]).parent.mkdir(parents=True)
        Path(os.environ["GT_CONFIG"]).write_text(json.dumps({"service": self.svc_url, "store": self.store_url, "token": ""}))
        out, code = run(["verify", "tabs"], cwd=self.repo)
        self.assertEqual(code, 0, out)
        self.assertTrue((self.latest() / "pushed.json").is_file())
        self.assertTrue(out.rstrip().splitlines()[-1].startswith(f"Feature page: {self.store_url}/feature.html?"), out[-400:])
        out, code = run(["verify", "tabs", "--no-push"], cwd=self.repo)
        self.assertFalse((self.latest() / "pushed.json").exists())

    def test_the_summary_says_what_is_not_checked_and_how_to_answer(self):
        s = {"run": "r", "feature": "f", "properties_by_verdict": {"needs_decision": 1}, "cases_held": 0, "cases": 1,
             "properties": [], "approval_flags": [], "not_checked": ["How fast the page loads."],
             "not_held": [{"property": "tabs", "case": "v2", "outcome_words": "needs your decision", "reason": "Is that intended?",
                           "is_question": True, "data_request": None}]}
        text = groundtruth.summary_text(s)
        self.assertIn("  tabs/v2: needs your decision. Question: Is that intended?\n    To answer: groundtruth answer tabs v2 intended   (or: bug)", text)
        self.assertIn("Not checked by these claims:\n  How fast the page loads.", text)


# -------------------------------------------------------------------- draft
ANSWER = {"feature": "menu-stations", "goal": "A diner can see which stations a meal has.",
          "claims": [{"id": "stations-listed", "title": "Stations listed per meal", "your_words": "show the stations for each meal",
                      "source": {"kind": "issue", "ref": "o/r#7", "quote": "show the stations for each meal"},
                      "statement": "Each meal on the menu page lists every station serving it.",
                      "checked_against": "The stations the menu service reports for that meal.",
                      "variations": [{"id": "one", "what": "A meal with one station."}, {"id": "many", "what": "A meal with four stations."},
                                     {"id": "none", "what": "A meal with no station: something: odd"}]}],
          "questions": [{"about": "stations-listed", "question": "Should closed stations be listed?"}],
          "not_checked": ["Matching the Figma design."]}


class DraftService(BaseHTTPRequestHandler):
    """The service's draft job as the command line sees it: POST answers 202 with an id at once, then GET says
    "working" `working` times before it is done."""
    seen, polls, auth = [], [], []
    answer = ANSWER
    working = 0

    def reply(self, status, obj):
        out = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        DraftService.seen.append(body)
        DraftService.auth.append(self.headers.get("Authorization"))
        DraftService.left = DraftService.working
        self.reply(202, {"draft": "d1"})

    def do_GET(self):  # noqa: N802
        DraftService.polls.append(self.path)
        DraftService.auth.append(self.headers.get("Authorization"))
        if DraftService.left > 0:
            DraftService.left -= 1
            return self.reply(200, {"state": "working"})
        self.reply(200, {"state": "done", "draft": DraftService.answer, "usage": {}})

    def log_message(self, *a):
        pass


class DraftCommand(WithAConfig):
    def setUp(self):
        super().setUp()
        self.svc, self.url = serve(DraftService)
        self.addCleanup(self.svc.server_close)
        self.addCleanup(self.svc.shutdown)
        self.repo = self.root / "app"
        (self.repo / ".groundtruth").mkdir(parents=True)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        DraftService.answer, DraftService.working = ANSWER, 0
        p = mock.patch.object(groundtruth, "DRAFT_POLL", 0.01)
        p.start()
        self.addCleanup(p.stop)

    def gh(self, mode):
        f = self.bin / "gh"
        f.write_text(FAKE_GH.format(py=sys.executable, mode=mode))
        f.chmod(0o755)
        os.environ["PATH"] = f"{self.bin}{os.pathsep}/usr/bin:/bin"

    def no_gh(self):
        os.environ["PATH"] = str(self.root / "nothing")

    def draft(self, *args, stdin=None):
        return run(["draft", "--service", self.url, *args], cwd=self.repo, stdin=stdin)

    def test_a_pull_request_and_its_issues(self):
        self.gh("ok")
        out, code = self.draft()
        self.assertEqual(code, 0, out)
        sent = DraftService.seen[-1]["input"]
        self.assertEqual((sent["pull_request"]["number"], [i["number"] for i in sent["issues"]], sent["said"]), (12, [7, 9], ""))
        self.assertIn("Drafting claims from pull request #12, issue #7 and issue #9...", out)
        self.assertIn("Added 1 claim to .groundtruth/features/menu-stations.yml: stations-listed.", out)
        self.assertIn("\nmenu-stations: 1 claim, not approved yet\n", out)
        self.assertIn("Questions the draft could not answer:\n   - (stations-listed) Should closed stations be listed?", out)
        self.assertIn("and 1 thing these claims do not check. Add --full to see them.", out)

    def test_an_issue_alone_and_your_own_words_alone_and_nothing(self):
        self.gh("nopr")
        out, code = self.draft("--issue", "7", "--feature", "stations")
        self.assertEqual(code, 0, out)
        self.assertEqual((DraftService.seen[-1]["input"]["pull_request"], [i["number"] for i in DraftService.seen[-1]["input"]["issues"]]), (None, [7]))
        self.assertTrue((self.repo / ".groundtruth" / "features" / "stations.yml").is_file())
        self.no_gh()
        (self.root / "said.txt").write_text("The menu should show each meal's stations.")
        out, code = self.draft("--said", str(self.root / "said.txt"), "--feature", "said")
        self.assertEqual(code, 0, out)
        self.assertEqual(DraftService.seen[-1]["input"]["said"], "The menu should show each meal's stations.")
        self.assertIn("Drafting claims from your own words...", out)
        out, code = self.draft("--said", "-", "--feature", "piped", stdin="From standard input.")
        self.assertEqual((code, DraftService.seen[-1]["input"]["said"]), (0, "From standard input."))
        n = len(DraftService.seen)
        _, code = self.draft()
        self.assertIn("Nothing to draft from", str(code))
        _, code = self.draft("--said", "-", stdin="x" * 8001)
        self.assertIn("at most 8,000", str(code))
        self.assertEqual(len(DraftService.seen), n)                      # nothing was sent for either

    def test_the_file_reads_back_exactly_and_like_a_hand_written_one(self):
        self.no_gh()
        self.draft("--said", "-", stdin="words")
        f = self.repo / ".groundtruth" / "features" / "menu-stations.yml"
        text = f.read_text()
        self.assertTrue(text.startswith("# Drafted by Groundtruth on "))
        self.assertIn(" from your own words. Not approved yet: read it, edit it, then run approve.\nfeature: menu-stations\n", text)
        self.assertIn("\n  - id: stations-listed\n    title: Stations listed per meal\n    your_words: show the stations for each meal\n", text)
        self.assertIn('        what: "A meal with no station: something: odd"\n', text)
        spec = yaml.safe_load(text)
        p = spec["properties"][0]
        for key in ("id", "title", "your_words", "source", "statement", "checked_against", "variations"):
            self.assertEqual(p[key], ANSWER["claims"][0][key], key)
        self.assertEqual((spec["questions"], spec["not_checked"]), (ANSWER["questions"], ANSWER["not_checked"]))
        expected = draftfile.render("menu-stations", ANSWER, "your own words", "2026-11-14 10:00")
        self.assertEqual(text.split("\n", 1)[1], expected.split("\n", 1)[1])

    def test_an_approved_feature_is_left_alone_and_new_claims_are_appended(self):
        f = self.repo / ".groundtruth" / "features" / "tabs.yml"
        f.parent.mkdir(parents=True)
        f.write_text(FEATURE)
        approval.write_record(self.repo, "tabs", approval.read_claims(self.repo, "tabs")[1])
        DraftService.answer = {**ANSWER, "claims": [{**ANSWER["claims"][0], "id": "tabs", "statement": "Something else."}, ANSWER["claims"][0]]}
        self.no_gh()
        out, code = self.draft("--said", "-", "--feature", "tabs", stdin="words")
        self.assertEqual(code, 0, out)
        text = f.read_text()
        self.assertTrue(text.startswith(FEATURE))                        # nothing before the new claims changed
        self.assertIn("Added 1 claim to .groundtruth/features/tabs.yml: stations-listed.", out)
        self.assertIn("Left alone, because the file already has them: tabs.", out)
        claims = dict(approval.read_claims(self.repo, "tabs")[1])
        self.assertEqual(list(claims), ["tabs", "result", "stations-listed"])
        self.assertNotEqual(claims["tabs"]["statement"], "Something else.")
        self.assertEqual(approval.compare(list(claims.items()), approval.load_record(self.repo, "tabs"))["tabs"]["state"], "approved")
        self.assertIn("1. Right tests in each tab\n", out)
        self.assertIn("(not approved yet)", out)                       # only the appended claim is marked

    def test_questions_block_approve_until_they_are_deleted(self):
        self.no_gh()
        self.draft("--said", "-", stdin="words")
        out, code = run(["approve", "menu-stations"], cwd=self.repo)
        self.assertIn("still has 1 question the draft could not answer", str(code))
        self.assertIsNone(approval.load_record(self.repo, "menu-stations"))
        f = self.repo / ".groundtruth" / "features" / "menu-stations.yml"
        text = f.read_text()
        f.write_text(text[:text.index("questions:")] + text[text.index("not_checked:"):])
        out, code = run(["approve", "menu-stations"], cwd=self.repo)
        self.assertEqual(code, 0, out)
        self.assertIn("0 changes since the claims were first drafted.", out)

    def test_it_asks_until_the_draft_is_done_and_prints_the_same(self):
        self.no_gh()
        DraftService.working, DraftService.polls[:] = 2, []
        out, code = self.draft("--said", "-", stdin="words")
        self.assertEqual(code, 0, out)
        self.assertEqual(DraftService.polls, ["/v1/drafts/d1"] * 3)
        self.assertIn("Added 1 claim to .groundtruth/features/menu-stations.yml: stations-listed.", out)
        with mock.patch.object(groundtruth, "DRAFT_LIMIT", 0):
            DraftService.working = 1000
            _, code = self.draft("--said", "-", "--feature", "other", stdin="words")
        self.assertIn("The draft took longer than 0 minutes", str(code))

    def test_a_draft_with_no_claims_writes_nothing(self):
        DraftService.answer = {**ANSWER, "claims": [], "questions": [{"about": "", "question": "What is the change meant to do?"}]}
        self.no_gh()
        out, code = self.draft("--said", "-", stdin="tidy things up")
        self.assertEqual(code, 0)
        self.assertIn("Question: What is the change meant to do?", out)
        self.assertFalse((self.repo / ".groundtruth" / "features").exists())


# -------------------------------------------------------------------- the service's draft job
GOOD = {k: ANSWER[k] for k in ("feature", "goal", "claims", "questions", "not_checked")}


class ScriptedDraft(Draft):
    def __init__(self, tmp, answers, inp=None):
        super().__init__(inp or {"said": "show the stations for each meal", "existing": [{"id": "tabs", "title": "Tabs"}]},
                         "scripted", Path(tmp), "http://127.0.0.1:0")
        self.answers, self.replies, self.prompts = answers, [], []

    def _claude(self, prompt, system, extra, name, timeout=900):
        self.prompts.append((system, prompt, extra))
        for rep in self.answers:
            self.replies.append(self.submit(self.conv["unit"], rep)["text"])
        return {"total_cost_usd": 0.01, "num_turns": 1}


class TheDraftJob(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_a_valid_answer_and_an_invalid_one_sent_back(self):
        bad = {**GOOD, "claims": [{**GOOD["claims"][0], "your_words": "x" * 301}]}
        job = ScriptedDraft(self.tmp.name, [bad, GOOD])
        draft, usage, cause = job.work()
        self.assertTrue(job.replies[0].startswith("not accepted: claim stations-listed has a `your_words`"), job.replies[0])
        self.assertEqual(job.replies[1], "saved; you're done")
        self.assertEqual(draft["claims"][0]["id"], "stations-listed")
        self.assertEqual(usage["rejected"], [job.replies[0][len("not accepted: "):]])
        system, prompt, extra = job.prompts[0]
        self.assertEqual(system, prompts.DRAFT)
        self.assertIn("mcp__gt__submit_draft", extra)
        self.assertEqual([e for e in extra[extra.index("--allowedTools") + 1:] if e.startswith("mcp__")], ["mcp__gt__submit_draft"])
        self.assertIn("The person's own words:\nshow the stations for each meal", prompt)
        self.assertIn("- tabs: Tabs", prompt)

    def test_the_limits(self):
        claim = GOOD["claims"][0]
        cases = [({**GOOD, "claims": [{**claim, "id": f"c{i}"} for i in range(9)]}, "claims is a list of 1 to 8"),
                 ({**GOOD, "claims": [], "questions": []}, "claims is a list of 1 to 8"),
                 ({**GOOD, "questions": [{"about": "", "question": "q?"}] * 7}, "questions is a list of at most 6"),
                 ({**GOOD, "claims": [{**claim, "source": {**claim["source"], "quote": "x" * 601}}]}, "source `quote`"),
                 ({**GOOD, "claims": [{**claim, "id": "tabs"}]}, "the id tabs is already used"),
                 ({**GOOD, "feature": "Menu Stations"}, "lowercase words joined by hyphens"),
                 ({**GOOD, "claims": [{**claim, "title": "x" * 61}]}, "not a short phrase"),
                 ({**GOOD, "claims": [{k: v for k, v in claim.items() if k != "checked_against"}]}, "needs checked_against"),
                 ({**GOOD, "questions": [{"about": "nope", "question": "q?"}]}, '"about" is the id of a claim'),
                 ({**GOOD, "not_checked": ["x" * 201]}, "not_checked"),
                 ({**GOOD, "claims": [{**claim, "source": {"kind": "email"}}]}, "must be exactly issue, pull request, interview or other")]
        for rep, words in cases:
            with self.subTest(words=words):
                why = draft_problems(rep, {"tabs"})
                self.assertIsNotNone(why)
                self.assertIn(words, why)
        self.assertIsNone(draft_problems(GOOD, {"tabs"}))
        self.assertIsNone(draft_problems({**GOOD, "claims": [], "questions": [{"about": "", "question": "What is it for?"}]}, set()))

    def test_it_needs_something_to_draft_from(self):
        with self.assertRaises(ValueError):
            ScriptedDraft(self.tmp.name, [], inp={"existing": []})

    def test_the_message_is_built_by_code(self):
        msg = prompts.draft_message({"pull_request": {"number": 12, "title": "Show stations", "body": "Fixes #7"}, "said": "",
                                     "issues": [{"number": 7, "title": "Stations", "body": "As a diner..."}], "existing": [],
                                     "data_tools": [{"name": "list_meals", "does": "Lists meals.", "reads_only": True}],
                                     "outside_services": [{"name": "menu service", "what": "Menus.", "allowed": "read"}]})
        self.assertEqual(msg, "The pull request (#12): Show stations\nFixes #7\n\nIssue #7: Stations\nAs a diner...\n\n"
                              "Claims the app already has (do not repeat them):\nnone\n\n"
                              "Data tools this project declares:\n- list_meals: Lists meals. Only reads.\n\n"
                              "Outside services this app depends on, and what may be done with each here:\n"
                              "- menu service: Menus. It may be read, and nothing may change it.\n\nDraft the claims.")

    def test_the_prompt_text(self):
        self.assertTrue(prompts.DRAFT.startswith("You draft claims for Groundtruth, a verifier. A person asked for a change to a web app: you are given what they\n"))
        self.assertIn("- your_words: the sentence of theirs it comes from, copied exactly, typos included. Never reworded.\n", prompts.DRAFT)
        self.assertTrue(prompts.DRAFT.endswith("the author's words where you can. Finish with submit_draft exactly once.\n"))


if __name__ == "__main__":
    unittest.main()
