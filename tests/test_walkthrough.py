"""The window size, the walkthrough (one film of the whole feature in use, one stop per claim), and the prompt split
for cases through the API. No model: conversations and the service are scripts; the browser is a real one on a
small local page."""
import asyncio
import importlib
import json
import os
import struct
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import groundtruth
from service import prompts
from service.harness import BROWSER, TOUR, WALK, case_problems, clean_walkthrough, tour_problems, validate_input, walk_problems
from toolserver import rundoc
from toolserver.catalog import Catalog
from toolserver.runner import Runner, ServiceError, run_verify, viewport_of, walkthrough_input

from tests.test_agent_commands import FEATURE, App, FakeService, call, make_repo, serve
from tests.test_proof_view import _Handler, ScriptedCase, cmp
from tests.test_starting_state_wiring import ScriptedRun, WithATempDir, case, plan

CLAIMS = [{"id": "tabs", "title": "Right tests in each tab", "statement": "New has one card per new test.",
           "checked_against": "The tests written for the run."},
          {"id": "latest", "title": "", "statement": "Each test shows its latest result.", "checked_against": ""}]
WALKIN = {"goal": "A reviewer can trust a run's page.", "claims": CLAIMS}


def tour(what="[tabs] Open New and compare the cards. [latest] Read each result.", **extra):
    return case("tour", **{"setting": "Two runs of the shop project, with 5 tests.", "what": what, **extra})


# -------------------------------------------------------------------- 1. the prompt split
class ThePromptSplit(unittest.TestCase):
    def test_together_they_are_the_old_text_and_the_second_starts_where_it_did(self):
        both = prompts.CASE_FROM_CLAIM + prompts.CASE_FROM_CLAIM_BROWSER
        self.assertIn('the situation the variation describes.\n\nTwo tools for what text cannot show. hover points', both)
        self.assertNotIn("hover", prompts.CASE_FROM_CLAIM)
        self.assertIn('say in "found" what the picture shows.\n\nThrough the pages, every comparison needs ref:', prompts.CASE_FROM_CLAIM_BROWSER)
        self.assertTrue(prompts.CASE_FROM_CLAIM_BROWSER.endswith("watching sees outlined while your comparison is on screen.\n"))
        self.assertNotIn("every comparison needs ref", prompts.CASE_FROM_CLAIM)          # an API case is never asked for one

    def test_an_api_case_is_not_told_about_hover_or_look(self):
        api = plan([case("v1", setting="s")])
        api["entry_points"] = [{"id": "e1", "door": "api", "where": "GET /api/runs"}]
        run = Systems(tempfile.mkdtemp(), [api], prop={"id": "p1", "statement": "s", "variations": [{"id": "v1", "what": "w"}]})
        run.work()
        self.assertEqual(run.systems["p1/v1"], prompts.CASE + prompts.CASE_API + prompts.CASE_FROM_CLAIM + prompts.CASE_SITUATION
                         + "\n" + prompts.SHARED)
        self.assertEqual(run.given["p1/v1"], {"http_request", "compare", "situation"})
        browser = Systems(tempfile.mkdtemp(), [plan([case("v1", setting="s")])],
                          prop={"id": "p1", "statement": "s", "variations": [{"id": "v1", "what": "w"}]})
        browser.work()
        self.assertEqual(browser.systems["p1/v1"], prompts.CASE + prompts.CASE_BROWSER + prompts.CASE_FROM_CLAIM
                         + prompts.CASE_SITUATION + prompts.CASE_FROM_CLAIM_BROWSER + "\n" + prompts.SHARED)


class Systems(ScriptedRun):
    """A scripted run that keeps each conversation's system prompt and settings, and can give each planning unit its own plans."""

    def __init__(self, *a, plans_for=None, **kw):
        super().__init__(*a, **kw)
        self.plans_for, self.systems, self.kw = plans_for or {}, {}, {}

    def converse(self, unit, role, tools, system, message, **kw):
        self.kw[unit] = kw
        return super().converse(unit, role, tools, system, message, **kw)

    def _claude(self, prompt, system, extra, name, timeout=900):
        unit = self.conv["unit"]
        self.systems[unit] = system
        self.turns = getattr(self, "turns", {})
        self.turns[unit] = extra[extra.index("--max-turns") + 1]
        keep = self.plans
        if unit in self.plans_for:
            self.plans = self.plans_for[unit]
        try:
            return super()._claude(prompt, system, extra, name, timeout)
        finally:
            self.plans = keep


# -------------------------------------------------------------------- 2. the window size
class TheWindowSize(unittest.TestCase):
    def test_sizes_are_checked_in_plain_words(self):
        self.assertEqual(viewport_of("1280x800"), {"width": 1280, "height": 800})
        self.assertEqual(viewport_of(" 600 X 500 "), {"width": 600, "height": 500})
        self.assertEqual(viewport_of({"width": 1920, "height": 1200}), {"width": 1920, "height": 1200})
        for bad in ("599x700", "1921x700", "900x499", "900x1201", "big", "900", {"width": "900", "height": 700}, {"width": True, "height": 700}):
            with self.subTest(bad=bad), self.assertRaises(ServiceError) as e:
                viewport_of(bad)
            self.assertIn("WIDTHxHEIGHT, for example 1280x800: a width from 600 to 1920 and a height from 500 to 1200", str(e.exception))

    def test_register_stores_it_and_refuses_a_bad_one(self):
        with tempfile.TemporaryDirectory() as t:
            repo = Path(t)
            app, url = serve(App)
            self.addCleanup(app.server_close)
            self.addCleanup(app.shutdown)
            args = dict(repo=str(repo), url=url, pid=None, no_pid=True, no_repo=True, reset=None, routes=None, project=None,
                        skip_reset_check=False)
            out, code = call(groundtruth.cmd_register, viewport="1280x800", **args)
            self.assertEqual(code, 0, out)
            self.assertEqual(json.loads((repo / ".groundtruth" / "env.json").read_text())["viewport"], {"width": 1280, "height": 800})
            _, code = call(groundtruth.cmd_register, viewport="5000x800", **args)
            self.assertIn("--viewport: the window size 5000x800 can't be used", str(code))
            out, code = call(groundtruth.cmd_register, viewport=None, **args)
            self.assertNotIn("viewport", json.loads((repo / ".groundtruth" / "env.json").read_text()))

    def test_the_document_always_carries_it(self):
        with tempfile.TemporaryDirectory() as t:
            run = Path(t)
            (run / "run.json").write_text(json.dumps({"run": "x", "feature": "f", "env": {"url": "u"}, "properties": []}))
            self.assertEqual(rundoc.build(run)[0]["ran_against"]["viewport"], {"width": 900, "height": 700})
            (run / "run.json").write_text(json.dumps({"run": "x", "feature": "f", "env": {"url": "u"}, "properties": [],
                                                      "viewport": {"width": 1280, "height": 800}}))
            self.assertEqual(rundoc.build(run)[0]["ran_against"]["viewport"], {"width": 1280, "height": 800})


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    return struct.unpack(">II", head[16:24])


class TheWindowInTheBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
            pw = sync_playwright().start()
            try:
                pw.chromium.launch().close()
            finally:
                pw.stop()
        except Exception as e:  # noqa: BLE001
            raise unittest.SkipTest(f"Chromium cannot launch here: {e}")

    def setUp(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.tmp = tempfile.TemporaryDirectory()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def test_every_page_filmed_or_not_has_the_size_and_so_does_the_film(self):
        for name, record, env, want in (("plain", False, {"viewport": {"width": 1280, "height": 800}}, (1280, 800)),
                                        ("filmed", True, {"viewport": {"width": 1280, "height": 800}}, (1280, 800)),
                                        ("default", True, {}, (900, 700))):
            with self.subTest(name=name):
                c = Catalog({"url": self.url, **env}, Path(self.tmp.name) / name, record=record)
                try:
                    self.assertEqual(c.run("screenshot", {"label": "x"})["text"], "shot-1")
                    self.assertEqual(png_size(Path(self.tmp.name) / name / "shot-1.png"), want)
                    self.assertEqual(c.page().evaluate("[innerWidth, innerHeight]"), list(want))
                    if record:
                        self.assertEqual(tuple(c._context._impl_obj._options["recordVideo"]["size"].values()), want)
                finally:
                    c.close()


# -------------------------------------------------------------------- 3. the walkthrough: what is sent, and checked
class WhatIsSent(unittest.TestCase):
    def test_every_claim_with_variations_in_file_order(self):
        import yaml
        got = walkthrough_input(yaml.safe_load(FEATURE), "tabs.yml")
        self.assertEqual(got, {"goal": "A reviewer can trust a run's page.",
                               "claims": [{"id": "tabs", "title": "Right tests in each tab",
                                           "statement": "New has one card for each test written for that run. No test is missing or in both.",
                                           "checked_against": "The record of which tests were written for the run."}]})
        with self.assertRaises(ServiceError) as e:
            walkthrough_input({"properties": [{"id": "p1", "statement": "s"}]}, "f.yml")
        self.assertIn("no claim with variations", str(e.exception))

    def test_the_service_checks_it(self):
        env = {"url": "http://x"}
        got = validate_input({"properties": [], "walkthrough": WALKIN, "env": env})
        self.assertEqual(got["walkthrough"]["claims"][0]["id"], "tabs")
        self.assertIsNone(validate_input({"properties": [{"id": "p1", "statement": "s"}], "env": env})["walkthrough"])
        with self.assertRaises(ValueError):
            validate_input({"properties": [], "env": env})                       # no properties and no walkthrough
        bad = [{"goal": "x" * 601, "claims": CLAIMS}, {"goal": 5, "claims": CLAIMS}, {"goal": "g", "claims": []},
               {"goal": "g", "claims": [CLAIMS[0]] * 2}, {"goal": "g", "claims": [{**CLAIMS[0], "id": "a b"}]},
               {"goal": "g", "claims": [{**CLAIMS[0], "id": "walkthrough"}]}, {"goal": "g", "claims": [{**CLAIMS[0], "statement": ""}]},
               {"goal": "g", "claims": [{**CLAIMS[0], "id": f"c{i}"} for i in range(21)]}, "yes"]
        for w in bad:
            with self.subTest(w=str(w)[:60]), self.assertRaises(ValueError):
                clean_walkthrough(w)
        with self.assertRaises(ValueError) as e:
            validate_input({"properties": [{"id": "walkthrough", "statement": "s"}], "env": env})
        self.assertIn("kept for the feature's walkthrough", str(e.exception))


class ThePlanCheck(unittest.TestCase):
    def test_the_tour_goes_through_the_pages_and_names_a_claim(self):
        ids = ["tabs", "latest"]
        self.assertIsNone(tour_problems(plan([tour()]), ids))
        self.assertIn("names no claim", tour_problems(plan([tour(what="Open the run page and look around.")]), ids))
        self.assertIn("names no claim", tour_problems(plan([tour(what="[other] Open it.")]), ids))
        api = plan([tour()])
        api["entry_points"] = [{"id": "e1", "door": "api", "where": "GET /"}]
        self.assertIn("through the pages", tour_problems(api, ids))


def spoken(claim, text="This part should list every new test."):
    return ("say", {"text": text, "claim": claim})


class TheClaimOnAComparison(WithATempDir):
    def talk(self, script, walk):
        run = ScriptedCase(self.tmp.name, script)
        conv = run.converse(f"{WALK}/tour" if walk else "p1/v1", "case", BROWSER + ["hover", "look", "compare"] + (["say"] if walk else []), "s", "m",
                            kind="behaviour", door="browser", from_claim=True,
                            walk={"ids": ["tabs", "latest"], "what": "[tabs] a [latest] b"} if walk else None)
        return run, conv

    def test_a_claim_not_listed_is_refused_by_the_harness(self):
        run, (rep, meta, _) = self.talk([("observe", {}), cmp("Cards", True, claim="nope", say="s"), cmp("Cards", True, say="s"),
                                          spoken("tabs"), cmp("Cards", True, claim="tabs", say="It shows both. Good."),
                                          spoken("latest"), cmp("Results", False, claim="latest", say="That is wrong."),
                                          ("submit", {"outcome": "breaks", "observed": "x"})], walk=True)
        refusal = "refused: claim is the id written in brackets at the start of the stop; it is one of: tabs, latest"
        self.assertEqual(run.replies[1], {"text": refusal})
        self.assertEqual(run.replies[2], {"text": refusal})
        self.assertEqual(run.replies[4], {"text": "noted"})
        self.assertEqual(run.n, 5)                              # the refused ones never reached the tool server
        self.assertEqual(run.replies[-1], "saved; you're done")

    def test_in_the_walkthrough_no_ref_is_refused_before_a_claim_not_listed(self):
        no_ref = ("refused: give ref, the element that shows the thing compared (from the latest observe); when the "
                  "point is that something is missing, the area where it would be")
        run, _ = self.talk([("observe", {}), cmp("Cards", True, claim="nope", ref=None), cmp("Cards", True, claim="nope"),
                            cmp("Cards", True, ref=None, claim="tabs")], walk=True)
        self.assertEqual(run.replies[1], {"text": no_ref})                              # both wrong: the missing ref is said first
        self.assertTrue(run.replies[2]["text"].startswith("refused: claim is the id written in brackets"))
        self.assertEqual(run.replies[3], {"text": no_ref})
        self.assertEqual(run.n, 1)                                                      # only observe reached the tool server

    def test_elsewhere_a_claim_is_never_passed_on(self):
        seen = []
        run = ScriptedCase(self.tmp.name, [("observe", {}), cmp("Cards", True, claim="tabs", say="It works."),
                                           ("submit", {"outcome": "holds", "observed": "x"})])
        orig = run.call_local
        run.call_local = lambda tool, args, timeout=None: (seen.append(args), orig(tool, args))[1]
        run.talk()
        self.assertNotIn("claim", seen[1])
        self.assertNotIn("say", seen[1])
        self.assertEqual(run.replies[-1], "saved; you're done")

    def test_every_stop_needs_a_comparison(self):
        run, (rep, meta, _) = self.talk([("observe", {}), spoken("tabs"), cmp("Cards", True, claim="tabs", say="Good."),
                                          ("submit", {"outcome": "holds", "observed": "x"}),
                                          spoken("latest"), cmp("Results", False, claim="latest", say="Not right."),
                                          ("submit", {"outcome": "holds", "observed": "x"}),
                                          ("submit", {"outcome": "breaks", "observed": "x"})], walk=True)
        self.assertEqual(run.replies[3], 'not accepted: no comparison yet for: latest. Do those stops, or record a comparison for each '
                                         'that says in "found" why it could not be done (matches false), then submit')
        self.assertIn('did not match ("Results")', run.replies[6])           # the from-claim rule as it is
        self.assertEqual(run.replies[7], "saved; you're done")

    def test_the_rule_alone(self):
        conv = {"walk": {"ids": ["a", "b"], "what": "[a] x [b] y [zz] z [a] again"}, "compares": [{"claim": "a"}]}
        self.assertIn("no comparison yet for: b.", walk_problems(conv, "holds"))
        self.assertIsNone(walk_problems(conv, "cant_run"))
        self.assertIsNone(walk_problems({**conv, "compares": [{"claim": "a"}, {"claim": "b"}]}, "breaks"))
        self.assertIsNone(case_problems({"kind": "behaviour", "door": "browser", "calls": [{"tool": "observe", "isError": False}],
                                         "shots": set(), "checks": {}, "from_claim": True, "walk": conv["walk"],
                                         "compares": [{"what": "w", "matches": True, "claim": "b"}]}, {"outcome": "outside", "observed": "x"}))


class TheProxy(unittest.TestCase):
    def tools(self, unit):
        env = {"GT_SERVICE": "http://127.0.0.1:1", "GT_RUN": "r", "GT_TOKEN": "t", "GT_UNIT": unit, "GT_ROLE": "case",
               "GT_TOOLS": json.dumps(["compare"])}
        sys.modules.pop("service.proxy", None)
        with mock.patch.dict(os.environ, env):
            proxy = importlib.import_module("service.proxy")
        self.addCleanup(sys.modules.pop, "service.proxy", None)
        return {t.name: t for t in asyncio.run(proxy.server.list_tools())}["compare"]

    def test_claim_is_asked_for_in_the_walkthrough_only(self):
        walk, plain = self.tools("walkthrough/tour"), self.tools("p1/v1")
        self.assertEqual(walk.description, plain.description + " claim: the id written in brackets at the start of the stop this comparison belongs to."
                         + " say: what you would say out loud at that moment, at most 110 characters: what you saw and whether it works.")
        self.assertIn("say", walk.input_schema["required"])
        self.assertNotIn("say", plain.input_schema["properties"])
        self.assertIn("claim", walk.input_schema["required"])
        self.assertNotIn("claim", plain.input_schema["properties"])


# -------------------------------------------------------------------- the walkthrough in a whole run
PROP = {"id": "p1", "statement": "s", "variations": [{"id": "v1", "what": "w"}]}


class InAWholeRun(WithATempDir):
    def run_walk(self, tour_plans, plan_only=False, props=True):
        inp_prop = PROP if props else None
        run = Systems(self.tmp.name, [plan([case("v1", setting="s")])], prop=inp_prop,
                      plans_for={f"{WALK}/discovery": tour_plans})
        run.input["walkthrough"] = clean_walkthrough(WALKIN)
        run.input["plan_only"] = plan_only
        run.work()
        self.assertNotIn("error", run.report, run.report.get("error"))
        return run

    def test_planned_then_carried_out_and_no_verdict_changes(self):
        bad = plan([tour(what="Look around.")])
        run = self.run_walk([plan([tour(setting="")]), bad, plan([tour()])])
        self.assertIn('needs "setting"', run.replies[1])
        self.assertIn("names no claim", run.replies[2])
        self.assertEqual(run.replies[3], "saved; you're done")
        w = run.report["walkthrough"]
        self.assertEqual(w["case"]["id"], "tour")
        self.assertEqual(w["case"]["result"]["outcome"], "holds")
        self.assertTrue(w["plan"]["from_claim"])
        self.assertIsNone(w["no_plan_because"])
        self.assertEqual(run.report["order"], ["p1"])
        self.assertEqual([p["id"] for p in run.report["properties"]], ["p1"])
        self.assertEqual(run.report["properties"][0]["verdict"], "holds")
        self.assertEqual(run.timeline(f"{WALK}/tour"), ["reset", "converse", "saved"])
        system = run.systems[f"{WALK}/tour"]
        self.assertTrue(system.endswith(prompts.CASE_FROM_CLAIM + prompts.CASE_FROM_CLAIM_BROWSER + "\n" + prompts.WALKTHROUGH_CASE
                                        + "\n" + prompts.SHARED))
        self.assertTrue(run.systems[f"{WALK}/discovery"].endswith(prompts.FROM_CLAIM + prompts.WALKTHROUGH_PLAN))
        self.assertEqual(run.turns[f"{WALK}/tour"], "150")
        self.assertEqual(run.turns["p1/v1"], "30")
        listing = ("- tabs: Right tests in each tab. New has one card per new test. Checked against: The tests written for the run.\n"
                   "- latest: Each test shows its latest result.\n")
        self.assertTrue(run.messages[f"{WALK}/discovery"][0].endswith("Plan the cases.\n\nThe claims this feature must keep, one stop for each:\n" + listing))
        self.assertTrue(run.messages[f"{WALK}/tour"][0].endswith("Carry out this case.\n\nThe claims, by the id written at each stop:\n" + listing))
        self.assertIn("The data put in for this walkthrough.", run.messages[f"{WALK}/tour"][0])
        self.assertEqual(run.given[f"{WALK}/tour"], set(BROWSER) | {"hover", "look", "compare", "say"})
        self.assertNotIn("say", run.given["p1/v1"])
        self.assertEqual(run.kw[f"{WALK}/tour"]["walk"], {"ids": ["tabs", "latest"], "what": tour()["what"]})

    def test_with_no_accepted_plan_it_says_why(self):
        run = self.run_walk([plan([tour(what="nothing")])])
        w = run.report["walkthrough"]
        self.assertIsNone(w["case"])
        self.assertTrue(w["no_plan_because"])
        self.assertEqual(run.report["properties"][0]["verdict"], "holds")

    def test_plan_only_skips_it_and_none_asked_leaves_no_key(self):
        run = self.run_walk([plan([tour()])], plan_only=True)
        self.assertEqual(run.report["walkthrough"]["no_plan_because"], "the run was plan-only")
        self.assertNotIn(f"{WALK}/discovery", run.systems)
        plain = Systems(self.tmp.name, [plan([case("v1", setting="s")])], prop=PROP)
        plain.work()
        self.assertNotIn("walkthrough", plain.report)


# -------------------------------------------------------------------- the command line and the run folder
class WalkService(FakeService):
    """Like FakeService, and when a walkthrough is asked for, sends one back with its case."""

    def do_POST(self):
        if self.path == "/v1/runs":
            return super().do_POST()
        inp = FakeService.started[-1]["input"]
        props = [{"id": p["id"], "statement": p["statement"], "verdict": "holds", "cases": []} for p in inp["properties"]]
        rep = {"run": "r1", "order": [p["id"] for p in props], "properties": props}
        if inp.get("walkthrough"):
            rep["walkthrough"] = {"plan": {"from_claim": True, "cases": [tour()]}, "discovery_meta": {"seconds": 3, "cost_usd": 0.5},
                                  "no_plan_because": None, "case": {**tour(), "door": "browser", "result": {"outcome": "holds"}}}
        self.reply(200, {"report": rep})


class FromTheCommandLine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = make_repo(Path(self.tmp.name))
        self.app, app_url = serve(App)
        self.svc, self.svc_url = serve(WalkService)
        for s in (self.app, self.svc):
            self.addCleanup(s.server_close)
            self.addCleanup(s.shutdown)
        self.env = {"url": app_url, "viewport": {"width": 1280, "height": 800}}
        ids = iter(f"20261113-1200{n:02d}" for n in range(60))             # one run folder per verify, however fast
        patcher = mock.patch("toolserver.runner.time.strftime", lambda fmt, *a: next(ids))
        patcher.start()
        self.addCleanup(patcher.stop)
        (self.repo / ".groundtruth" / "env.json").write_text(json.dumps(self.env))

    def verify(self, **kw):
        said = []
        run_dir = run_verify(self.repo, "tabs", self.svc_url, say=said.append, **kw)
        return said, json.loads((run_dir / "run.json").read_text()), FakeService.started[-1]["input"], run_dir

    def test_the_registered_size_is_used_and_verify_overrides_it_for_one_run(self):
        said, run, _, _ = self.verify()
        self.assertEqual(run["viewport"], {"width": 1280, "height": 800})
        self.assertIn("window 1280x800", said[0])
        _, run, sent, _ = self.verify(viewport="1024x768")
        self.assertEqual(run["viewport"], {"width": 1024, "height": 768})
        self.assertEqual(json.loads((self.repo / ".groundtruth" / "env.json").read_text()), self.env)   # env.json is not changed
        self.assertNotIn("viewport", json.dumps(sent))           # the service is not told it
        with self.assertRaises(ServiceError):
            self.verify(viewport="100x100")
        (self.repo / ".groundtruth" / "env.json").write_text(json.dumps({"url": self.env["url"]}))
        _, run, _, _ = self.verify()
        self.assertEqual(run["viewport"], {"width": 900, "height": 700})

    def test_a_walkthrough_is_sent_saved_and_documented(self):
        said, run, sent, run_dir = self.verify(walkthrough=True, only="result")
        self.assertEqual([p["id"] for p in sent["properties"]], ["result"])
        self.assertEqual([c["id"] for c in sent["walkthrough"]["claims"]], ["tabs"])      # every claim, whatever --only says
        self.assertEqual(run["walkthrough"]["case"]["result"], {"outcome": "holds"})
        doc, files = rundoc.build(run_dir, repo=self.repo)
        w = doc["walkthrough"]
        self.assertEqual((w["id"], w["variation"], w["setting"], w["outcome"]), ("tour", "", "Two runs of the shop project, with 5 tests.", "holds"))
        made, _ = rundoc._case(run_dir, "p1", {"id": "c1", "result": {"outcome": "holds"}})
        self.assertEqual(set(w), set(made))                         # exactly the shape of any case
        self.assertEqual(doc["totals"]["cost_usd"], 0.5)
        self.assertEqual([(p["id"], p["verdict"]) for p in doc["properties"]], [("tabs", "not_run"), ("result", "holds")])

    def test_walkthrough_only_runs_no_property(self):
        said, run, sent, run_dir = self.verify(walkthrough_only=True)
        self.assertEqual(sent["properties"], [])
        self.assertEqual([(p["id"], p["verdict"]) for p in run["properties"]], [("tabs", "not_run"), ("result", "not_run")])
        doc, _ = rundoc.build(run_dir, repo=self.repo)
        self.assertEqual([p["verdict"] for p in doc["properties"]], ["not_run", "not_run"])
        self.assertEqual(doc["walkthrough"]["outcome"], "holds")
        s, _ = call(groundtruth.cmd_summary, repo=str(self.repo), run=None, json=False)
        self.assertIn("Walkthrough: held.", s.splitlines())

    def test_refused_without_claims_and_for_a_property_called_walkthrough(self):
        f = self.repo / ".groundtruth" / "features" / "tabs.yml"
        f.write_text("feature: t\nproperties:\n  - id: p1\n    statement: s\n")
        for kw in ({"walkthrough": True}, {"walkthrough_only": True}):
            with self.subTest(kw=kw), self.assertRaises(ServiceError) as e:
                self.verify(**kw)
            self.assertIn("no claim with variations", str(e.exception))
        f.write_text("feature: t\nproperties:\n  - id: walkthrough\n    statement: s\n")
        with self.assertRaises(ServiceError) as e:
            self.verify()
        self.assertIn("kept for the feature's walkthrough", str(e.exception))

    def test_a_walkthrough_the_service_never_sent_is_a_case_that_could_not_run(self):
        with mock.patch.object(WalkService, "do_POST", FakeService.do_POST):
            _, run, _, run_dir = self.verify(walkthrough=True)
        doc, _ = rundoc.build(run_dir, repo=self.repo)
        self.assertEqual(doc["walkthrough"]["outcome"], "cant_run")
        self.assertIn("the service sent no walkthrough", doc["walkthrough"]["observed"])
        s, _ = call(groundtruth.cmd_summary, repo=str(self.repo), run=None, json=False)
        self.assertTrue(any(line.startswith("Walkthrough: could not run. The walkthrough could not be run") for line in s.splitlines()), s)


class TheDocument(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run = Path(self.tmp.name) / "20261113-120000"
        unit = self.run / "walkthrough" / "tour"
        unit.mkdir(parents=True)
        (unit / "clip-1.webm").write_bytes(b"webm")
        t0 = 3000.0
        rows = [{"t": t0 + 1, "tool": "navigate", "args": {"path": "/"}, "result": "Now at /"},
                {"t": t0 + 5, "tool": "compare", "args": {}, "result": "noted",
                 "compared": {"what": "Cards", "expected": "e", "found": "f", "matches": False, "element": None, "claim": "tabs", "t": t0 + 4.5}}]
        (unit / "receipts.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
        self.walk = {"plan": {"from_claim": True}, "discovery_meta": {"seconds": 10, "cost_usd": 0.2}, "no_plan_because": None,
                     "case": {**tour(), "door": "browser",
                              "result": {"outcome": "breaks", "observed": "One card missing.", "clips": [{"file": "clip-1.webm", "started": t0}],
                                         "meta": {"seconds": 40, "cost_usd": 0.3}}}}

    def build(self, walk):
        (self.run / "run.json").write_text(json.dumps({"run": "20261113-120000", "feature": "f", "env": {"url": "u"},
                                                       "properties": [{"id": "p1", "statement": "s", "verdict": "holds", "cases": []}],
                                                       **({"walkthrough": walk} if walk is not None else {})}))
        return rundoc.build(self.run)

    def test_a_case_like_any_other_with_its_stops_claims(self):
        doc, files = self.build(self.walk)
        w = doc["walkthrough"]
        self.assertEqual(w["id"], "tour")
        self.assertEqual(w["outcome"], "breaks")
        self.assertEqual(w["clips"], [{"file": "walkthrough/tour/clip-1.webm", "started": 3000.0}])
        self.assertIn({"path": "walkthrough/tour/clip-1.webm", "kind": "clip", "local": self.run / "walkthrough/tour/clip-1.webm"}, files)
        stop = w["steps"][1]
        self.assertEqual((stop["kind"], stop["claim"], stop["at"], stop["matches"]), ("compare", "tabs", 4.5, False))
        self.assertEqual((doc["totals"]["seconds"], doc["totals"]["cost_usd"]), (50.0, 0.5))
        self.assertEqual(doc["properties"][0]["verdict"], "holds")
        self.assertEqual(doc["totals"]["properties"]["holds"], 1)

    def test_none_asked_is_null_and_one_not_planned_could_not_run(self):
        self.assertIsNone(self.build(None)[0]["walkthrough"])
        w = self.build({"plan": None, "discovery_meta": {}, "no_plan_because": "the planner gave up", "case": None})[0]["walkthrough"]
        self.assertEqual((w["id"], w["outcome"], w["variation"]), ("tour", "cant_run", ""))
        self.assertEqual(w["observed"], "The walkthrough could not be run: the planner gave up")


if __name__ == "__main__":
    unittest.main()
