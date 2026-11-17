"""Hosted mode (GT_HOSTED=1): both processes on one data folder, one identity, sign-in for the pages, the verifier
forwarded under /verifier/, spending limits, and the `users` and `login` commands. Real servers on free ports with
their data in a temporary folder. No model: `claude` is a script that submits what the test says and reports a cost,
so the real harness, the internal routes and the work loop all run."""
import http.client
import json
import os
import sqlite3
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import groundtruth
from service import server as service_server
from store.db import Store, limit_message
from store.server import start_in_background
from tests.test_cli import ANSWER, CLEAN_ENV, run
from tests.test_store import CLIP, FILES, SHOT, document

PLAN = {"applies_to": "any listing", "action": "adding it to the cart", "compares": "the subtotal and the listed price",
        "how_found": "Looked at the pages.", "not_covered": "", "kind": "behaviour",
        "entry_points": [{"id": "e1", "door": "browser", "where": "Listing page: the add-to-cart button"}],
        "cases": [{"id": c, "entry_point": "e1", "what": f"Add a listing ({c})."} for c in ("c1", "c2", "c3")]}
RUN_INPUT = {"properties": [{"id": "p1", "statement": "Adding a listing raises the subtotal by its price."}],
             "env": {"url": "http://127.0.0.1:1", "has_reset": True}}
DRAFT_INPUT = {"said": "show the stations for each meal", "existing": []}
GOOD_DRAFT = {k: ANSWER[k] for k in ("feature", "goal", "claims", "questions", "not_checked")}

FAKE_CLAUDE = """#!{py}
# Stands in for `claude -p`: reads the conversation's tool-server settings, submits the scripted answer for its role
# through the service's internal route (as service/proxy.py would), and reports a cost.
import json, os, sys, urllib.request
args = sys.argv
gt = json.load(open(args[args.index("--mcp-config") + 1]))["mcpServers"]["gt"]["env"]
script = json.load(open(os.environ["FAKE_CLAUDE_SCRIPT"]))
answer = script.get(gt["GT_ROLE"])
if answer is not None:
    req = urllib.request.Request(gt["GT_SERVICE"] + "/internal/runs/" + gt["GT_RUN"] + "/submit",
                                 data=json.dumps({{"unit": gt["GT_UNIT"], "report": answer}}).encode(),
                                 headers={{"Authorization": "Bearer " + gt["GT_TOKEN"], "Content-Type": "application/json"}})
    urllib.request.urlopen(req, timeout=30).read()
print(json.dumps({{"total_cost_usd": script["cost"], "num_turns": 1}}))
"""


def call(url, method, path, body=None, token=None, headers=None):
    """-> (status, headers, JSON or bytes)"""
    from urllib.parse import urlsplit
    u = urlsplit(url)
    path = u.path.rstrip("/") + path          # a base with a prefix, such as http://host:port/verifier
    c = http.client.HTTPConnection(u.hostname, u.port, timeout=30)
    h = dict(headers or {})
    if token:
        h["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        h.setdefault("Content-Type", "application/json")
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    raw = r.read()
    out_headers = {k.lower(): v for k, v in r.getheaders()}
    out_headers["set-cookie-all"] = r.headers.get_all("Set-Cookie") or []
    c.close()
    try:
        return r.status, out_headers, json.loads(raw) if raw else None
    except ValueError:
        return r.status, out_headers, raw


class Hosted(unittest.TestCase):
    """A hosted service and a hosted store on one data folder, with two users."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.data = self.root / "data"
        self.dash = self.root / "dash"
        (self.dash / "js").mkdir(parents=True)
        for name in ("index.html", "run.html", "signin.html", "style.css"):
            (self.dash / name).write_text(f"<!-- {name} -->")
        (self.dash / "js" / "signin.js").write_text("// sign in")
        (self.dash / "js" / "runs.js").write_text("// runs")
        self.svc = service_server.make_server(0, "scripted", hosted=True, data_dir=self.data, workdir=self.root / "work")
        threading.Thread(target=self.svc.serve_forever, daemon=True).start()
        self.svc_url = f"http://127.0.0.1:{self.svc.server_address[1]}"
        self.srv, self.url = start_in_background(self.data, 0, self.dash, hosted=True, service_url=self.svc_url)
        for s in (self.srv, self.svc):
            self.addCleanup(s.server_close)
            self.addCleanup(s.shutdown)
        self.store = Store(self.data, hosted=True)
        self.alice = self.store.add_user("alice")
        self.bob = self.store.add_user("bob", limit=1)
        self.host = self.url.split("://")[1]
        claude = self.root / "claude"
        claude.write_text(FAKE_CLAUDE.format(py=sys.executable))
        claude.chmod(0o755)
        for p in (mock.patch("service.harness.CLAUDE", str(claude)),
                  mock.patch.dict(os.environ, {"FAKE_CLAUDE_SCRIPT": str(self.root / "script.json")})):
            p.start()
            self.addCleanup(p.stop)
        self.script(cost=0.4)

    def script(self, **roles):
        (self.root / "script.json").write_text(json.dumps(roles))

    def upload(self, token):
        s, _, made = call(self.url, "POST", "/v1/runs", {"document": document(), "files": FILES}, token=token)
        self.assertEqual(s, 201, made)
        for f in FILES:
            s, _, _ = call(self.url, "PUT", f"/v1/runs/{made['id']}/files/{f['path']}", CLIP if f["path"].endswith(".webm") else SHOT,
                           token=token, headers={"Content-Type": "application/octet-stream"})
            self.assertEqual(s, 204)
        self.assertEqual(call(self.url, "POST", f"/v1/runs/{made['id']}/complete", {}, token=token)[0], 200)
        return made["id"]

    def sign_in(self, token):
        s, h, body = call(self.url, "POST", "/v1/session", {"token": token}, headers={"Origin": f"http://{self.host}"})
        self.assertEqual(s, 200, body)
        cookies = h["set-cookie-all"]
        session = next(c for c in cookies if c.startswith("gt_session="))
        for part in ("HttpOnly", "Secure", "SameSite=Lax", "Max-Age=2592000", "Path=/"):
            self.assertIn(part, session)
        self.assertTrue(any(c.startswith("gt_signed_in=1;") and "HttpOnly" not in c for c in cookies))
        return session.split(";")[0]

    def drive(self, base, token, inp=RUN_INPUT):
        """Start a run and play the user's tool server until the report: every action succeeds."""
        s, _, made = call(base, "POST", "/v1/runs", {"input": inp}, token=token)
        if s != 200:
            return s, made
        results = []
        for _ in range(200):
            s, _, out = call(base, "POST", f"/v1/runs/{made['run']}/results", {"results": results}, token=token)
            self.assertEqual(s, 200, out)
            if "report" in out:
                return made["run"], out["report"]
            results = [{"id": a["id"], "text": "saved" if a["tool"] == "end_claim" else "ok", "isError": False,
                        "data": a["args"].get("report") if a["tool"] == "end_claim" else None} for a in out["actions"]]
        self.fail("the run never finished")

    def spend(self, name):
        with sqlite3.connect(self.data / "store.db") as c:
            return c.execute("SELECT s.kind, s.cost_usd, s.job FROM spend s JOIN users u ON u.id=s.user_id WHERE u.name=? ORDER BY s.id",
                             (name,)).fetchall()


class StoreHosted(Hosted):
    def test_no_local_user_and_no_token_file(self):
        self.assertFalse((self.data / "token").exists())
        self.assertEqual([u["name"] for u in self.store.list_users()], ["alice", "bob"])

    def test_nothing_without_a_token_or_a_sign_in_even_named_local(self):
        rid = self.upload(self.alice)
        local = {"Host": "127.0.0.1"}
        for path in ("/v1/runs", "/v1/projects", f"/v1/runs/{rid}", f"/v1/runs/{rid}/files/p1/c1/shot-1.png", f"/v1/runs/{rid}/feedback"):
            s, _, body = call(self.url, "GET", path, headers=local)
            self.assertEqual((s, body.get("sign_in")), (401, "/signin.html"), path)
        s, _, _ = call(self.url, "POST", "/v1/runs", {"document": document(), "files": []}, headers=local)
        self.assertEqual(s, 401)
        s, _, _ = call(self.url, "POST", f"/v1/runs/{rid}/feedback", {"property": "p", "kind": "right"},
                       headers={**local, "Origin": "http://127.0.0.1"})
        self.assertEqual(s, 401)
        self.assertEqual(call(self.url, "GET", "/v1/runs", token="wrong")[0], 401)
        self.assertEqual(call(self.url, "GET", "/health")[0], 200)

    def test_pages_send_you_to_sign_in_and_only_the_sign_in_page_is_open(self):
        for path in ("/", "/index.html", "/run.html?run=abc"):
            s, h, _ = call(self.url, "GET", path)
            self.assertEqual(s, 302, path)
            self.assertTrue(h["location"].startswith("/signin.html?next=%2F"), h["location"])
        self.assertEqual(call(self.url, "GET", "/js/runs.js")[0], 401)
        for path in ("/signin.html", "/style.css", "/js/signin.js"):
            self.assertEqual(call(self.url, "GET", path)[0], 200, path)

    def test_one_user_never_sees_another_users_runs(self):
        rid = self.upload(self.alice)
        self.assertEqual(call(self.url, "GET", f"/v1/runs/{rid}", token=self.bob)[0], 404)
        self.assertEqual(call(self.url, "GET", f"/v1/runs/{rid}/files/p1/c1/clip-1.webm", token=self.bob)[0], 404)
        self.assertEqual(call(self.url, "GET", "/v1/runs", token=self.bob)[2], {"runs": []})
        self.assertEqual(call(self.url, "GET", "/v1/projects", token=self.bob)[2], {"projects": []})
        self.assertEqual(call(self.url, "GET", f"/v1/runs/{rid}/feedback", token=self.bob)[0], 404)
        cookie = self.sign_in(self.bob)
        self.assertEqual(call(self.url, "GET", f"/v1/runs/{rid}", headers={"Cookie": cookie})[0], 404)
        self.assertEqual(len(call(self.url, "GET", "/v1/runs", token=self.alice)[2]["runs"]), 1)

    def test_the_cookie_flow_a_film_with_a_range_then_sign_out(self):
        rid = self.upload(self.alice)
        s, _, body = call(self.url, "POST", "/v1/session", {"token": "wrong"})
        self.assertEqual((s, body["error"]), (401, "That token is not valid here. Check that you copied all of it."))
        cookie = self.sign_in(self.alice)
        jar = {"Cookie": cookie}
        self.assertEqual(call(self.url, "GET", "/v1/session", headers=jar)[2], {"user": "alice", "via": "cookie"})
        self.assertEqual(call(self.url, "GET", "/index.html", headers=jar)[0], 200)
        s, _, run_ = call(self.url, "GET", f"/v1/runs/{rid}", headers=jar)
        self.assertEqual((s, run_["id"]), (200, rid))
        s, h, part = call(self.url, "GET", f"/v1/runs/{rid}/files/p1/c1/clip-1.webm", headers={**jar, "Range": "bytes=100-199"})
        self.assertEqual((s, h["content-range"], part), (206, f"bytes 100-199/{len(CLIP)}", CLIP[100:200]))
        # uploads never take the cookie
        self.assertEqual(call(self.url, "POST", "/v1/runs", {"document": document(), "files": []}, headers=jar)[0], 401)
        # signing out needs this site's Origin too, then the cookie stops working
        self.assertEqual(call(self.url, "DELETE", "/v1/session", headers=jar)[0], 403)
        s, h, _ = call(self.url, "DELETE", "/v1/session", headers={**jar, "Origin": f"http://{self.host}"})
        self.assertEqual(s, 204)
        self.assertTrue(all("Max-Age=0" in c for c in h["set-cookie-all"]))
        self.assertEqual(call(self.url, "GET", f"/v1/runs/{rid}", headers=jar)[0], 401)
        self.assertEqual(call(self.url, "GET", f"/v1/runs/{rid}/files/p1/c1/clip-1.webm", headers=jar)[0], 401)
        self.assertEqual(call(self.url, "GET", "/index.html", headers=jar)[0], 302)

    def test_a_new_token_ends_every_sign_in(self):
        cookie = self.sign_in(self.alice)
        self.store.new_token("alice")
        self.assertEqual(call(self.url, "GET", "/v1/runs", headers={"Cookie": cookie})[0], 401)
        self.assertEqual(call(self.url, "GET", "/v1/runs", token=self.alice)[0], 401)

    def test_a_cookie_write_from_another_site_is_refused(self):
        rid = self.upload(self.alice)
        jar = {"Cookie": self.sign_in(self.alice)}
        fb = {"property": "p1", "kind": "right"}
        doc = document()
        doc["properties"] = [{"id": "p1", "cases": []}]
        call(self.url, "POST", "/v1/runs", {"document": doc, "files": []}, token=self.alice)
        for origin in ({"Origin": "https://evil.example"}, {}, {"Origin": "null"},
                       {"Origin": f"http://{self.host}", "Sec-Fetch-Site": "cross-site"}):
            s, _, body = call(self.url, "POST", f"/v1/runs/{rid}/feedback", fb, headers={**jar, **origin})
            self.assertEqual(s, 403, (origin, body))
        s, _, body = call(self.url, "POST", f"/v1/runs/{rid}/feedback", fb, headers={**jar, "Origin": f"http://{self.host}"})
        self.assertEqual(s, 201, body)
        s, _, _ = call(self.url, "DELETE", f"/v1/runs/{rid}/feedback/{body['id']}", headers={**jar, "Origin": "https://evil.example"})
        self.assertEqual(s, 403)
        # with the token (the command line) no Origin is needed
        self.assertEqual(call(self.url, "POST", f"/v1/runs/{rid}/feedback", fb, token=self.alice)[0], 201)

    def test_a_configured_public_address_is_the_only_origin(self):
        self.srv.RequestHandlerClass.public_url = "https://gt.example.com"
        rid = self.upload(self.alice)
        jar = {"Cookie": self.sign_in_anywhere(self.alice)}
        fb = {"property": "p1", "kind": "right"}
        self.assertEqual(call(self.url, "POST", f"/v1/runs/{rid}/feedback", fb, headers={**jar, "Origin": f"http://{self.host}"})[0], 403)
        self.assertNotEqual(call(self.url, "POST", f"/v1/runs/{rid}/feedback", fb, headers={**jar, "Origin": "https://gt.example.com"})[0], 403)

    def sign_in_anywhere(self, token):
        s, h, _ = call(self.url, "POST", "/v1/session", {"token": token})       # no Origin: the command line, curl
        self.assertEqual(s, 200)
        return next(c for c in h["set-cookie-all"] if c.startswith("gt_session=")).split(";")[0]


class ServiceHosted(Hosted):
    def test_every_public_route_needs_a_token_and_health_says_little(self):
        for method, path, body in (("POST", "/v1/runs", {"input": RUN_INPUT}), ("POST", "/v1/runs/abc/results", {}),
                                   ("DELETE", "/v1/runs/abc", None), ("POST", "/v1/drafts", {"input": DRAFT_INPUT}),
                                   ("GET", "/v1/drafts/abc", None)):
            s, _, out = call(self.svc_url, method, path, body)
            self.assertEqual((s, out["error"]), (401, "unauthorized"), path)
            self.assertEqual(call(self.svc_url, method, path, body, token="wrong")[0], 401, path)
        self.assertEqual(call(self.svc_url, "GET", "/health")[2], {"ok": True, "version": "3"})
        self.assertEqual(call(self.svc_url, "GET", "/health", token=self.alice)[2], {"ok": True, "version": "3", "user": "alice"})
        self.assertEqual(call(self.svc_url, "GET", "/health", token="wrong")[0], 401)

    def test_a_run_is_continued_and_deleted_only_by_whoever_started_it(self):
        self.script(cost=0.01)          # discovery submits nothing: the run is quick
        s, _, made = call(self.svc_url, "POST", "/v1/runs", {"input": RUN_INPUT}, token=self.alice)
        self.assertEqual(s, 200)
        rid = made["run"]
        self.assertEqual(call(self.svc_url, "POST", f"/v1/runs/{rid}/results", {"results": []}, token=self.bob)[0], 404)
        self.assertEqual(call(self.svc_url, "DELETE", f"/v1/runs/{rid}", token=self.bob)[0], 404)
        self.assertFalse(service_server.RUNS[rid].aborted)
        self.assertEqual(call(self.svc_url, "DELETE", f"/v1/runs/{rid}", token=self.alice)[0], 200)
        self.assertTrue(service_server.RUNS[rid].aborted)

    def test_internal_routes_only_from_this_machine(self):
        self.script(cost=0.01)
        rid = call(self.svc_url, "POST", "/v1/runs", {"input": RUN_INPUT}, token=self.alice)[2]["run"]
        job = service_server.RUNS[rid]
        body = {"unit": "nothing", "report": {}}
        self.assertEqual(call(self.svc_url, "POST", f"/internal/runs/{rid}/submit", body, token=job.token)[0], 200)
        self.assertEqual(call(self.svc_url, "POST", f"/internal/runs/{rid}/submit", body, token="wrong")[0], 403)
        with mock.patch.object(service_server.Handler, "_peer_is_loopback", lambda self: False):
            s, _, out = call(self.svc_url, "POST", f"/internal/runs/{rid}/submit", body, token=job.token)
            self.assertEqual(s, 403, out)
            self.assertEqual(call(self.svc_url, "POST", f"/internal/runs/{rid}/call", body, token=job.token)[0], 403)
        call(self.svc_url, "DELETE", f"/v1/runs/{rid}", token=self.alice)

    def test_the_limit_stops_further_cases_cleanly_then_refuses_a_run_and_a_draft(self):
        # bob's limit is $1 and every model session costs $0.40: discovery ($0.40) and the first case's two tries
        # ($0.80) are spent; the second and third cases are not started.
        self.script(cost=0.4, discovery=PLAN)
        rid, report = self.drive(self.svc_url, self.bob)
        self.assertNotIn("error", report)
        stopped = report["stopped_by_limit"]
        self.assertEqual(stopped["message"], "You have reached this month's limit of $1 on this verifier. Ask whoever runs it to raise it.")
        self.assertEqual((stopped["cases_not_run"], stopped["properties_not_planned"]), (["p1/c2", "p1/c3"], []))
        prop = report["properties"][0]
        self.assertEqual(prop["verdict"], "cant_tell")
        self.assertEqual([c["result"].get("spending_limit") for c in prop["cases"]], [None, True, True])
        self.assertEqual([(k, round(c, 2)) for k, c, _ in self.spend("bob")], [("run", 0.4)] * 3)
        self.assertEqual({j for *_, j in self.spend("bob")}, {rid})
        s, _, out = call(self.svc_url, "POST", "/v1/runs", {"input": RUN_INPUT}, token=self.bob)
        self.assertEqual((s, out["error"], out["message"]), (402, "spending_limit", limit_message(1)))
        s, _, out = call(self.svc_url, "POST", "/v1/drafts", {"input": DRAFT_INPUT}, token=self.bob)
        self.assertEqual((s, out["message"]), (402, limit_message(1)))
        self.store.set_limit("bob", None)
        self.assertEqual(call(self.svc_url, "POST", "/v1/drafts", {"input": DRAFT_INPUT}, token=self.bob)[0], 202)

    def test_a_run_whose_limit_is_reached_before_discovery_plans_nothing(self):
        self.store.set_limit("bob", 0.5)
        self.script(cost=0.6, discovery=PLAN)
        _, report = self.drive(self.svc_url, self.bob, {**RUN_INPUT, "properties": RUN_INPUT["properties"] + [
            {"id": "p2", "statement": "Removing a listing lowers the subtotal by its price."}]})
        self.assertEqual(report["stopped_by_limit"]["properties_not_planned"], ["p2"])
        self.assertEqual(report["stopped_by_limit"]["cases_not_run"], ["p1/c1", "p1/c2", "p1/c3"])
        self.assertIn("limit of $0.50", report["properties"][1]["no_plan_because"])

    def test_a_draft_is_a_job_that_its_owner_collects_and_its_cost_is_kept(self):
        self.script(cost=0.25, draft=GOOD_DRAFT)
        s, _, made = call(self.svc_url, "POST", "/v1/drafts", {"input": DRAFT_INPUT}, token=self.alice)
        self.assertEqual(s, 202, made)
        did = made["draft"]
        self.assertEqual(call(self.svc_url, "GET", f"/v1/drafts/{did}", token=self.bob)[0], 404)
        for _ in range(100):
            s, _, got = call(self.svc_url, "GET", f"/v1/drafts/{did}", token=self.alice)
            if got["state"] != "working":
                break
            time.sleep(0.1)
        self.assertEqual((got["state"], got["draft"]["claims"][0]["id"]), ("done", "stations-listed"))
        self.assertEqual(self.spend("alice"), [("draft", 0.25, did)])            # recorded by the background thread
        self.assertEqual(call(self.svc_url, "GET", f"/v1/drafts/{did}", token=self.alice)[2]["state"], "done")   # kept
        with mock.patch.object(service_server, "DRAFT_KEEP", -1):
            self.assertEqual(call(self.svc_url, "GET", f"/v1/drafts/{did}", token=self.alice)[0], 404)          # then dropped

    def test_a_draft_with_no_accepted_answer_fails_plainly(self):
        self.script(cost=0.1)
        did = call(self.svc_url, "POST", "/v1/drafts", {"input": DRAFT_INPUT}, token=self.alice)[2]["draft"]
        for _ in range(100):
            got = call(self.svc_url, "GET", f"/v1/drafts/{did}", token=self.alice)[2]
            if got["state"] != "working":
                break
            time.sleep(0.1)
        self.assertEqual(got["state"], "failed")
        self.assertIn("gave no accepted draft", got["message"])


class OneAddress(Hosted):
    def test_the_store_forwards_exactly_the_verifiers_public_routes(self):
        s, _, out = call(self.url, "GET", "/verifier/health")
        self.assertEqual((s, out), (200, {"ok": True, "version": "3"}))
        self.assertEqual(call(self.url, "GET", "/verifier/health", token="wrong")[0], 401)
        self.assertEqual(call(self.url, "POST", "/verifier/v1/runs", {"input": RUN_INPUT})[0], 401)
        # a page's cookie is never passed on, so it can't start anything
        jar = {"Cookie": self.sign_in(self.alice)}
        self.assertEqual(call(self.url, "POST", "/verifier/v1/drafts", {"input": DRAFT_INPUT}, headers=jar)[0], 401)
        for method, path in (("POST", "/verifier/internal/runs/abc/submit"), ("POST", "/verifier/internal/runs/abc/call"),
                             ("GET", "/verifier/v1/runs"), ("POST", "/verifier/v1/runs/a%2Fb/results"), ("PUT", "/verifier/v1/runs"),
                             ("GET", "/verifier/"), ("POST", "/verifier/v1/runs/../../internal/runs/x/submit")):
            self.assertEqual(call(self.url, method, path, {} if method != "GET" else None, token=self.alice)[0], 404, path)

    def test_a_whole_run_and_a_draft_through_the_one_address(self):
        self.script(cost=0.4, discovery=PLAN)
        rid, report = self.drive(self.url + "/verifier", self.bob)
        self.assertEqual(report["stopped_by_limit"]["cases_not_run"], ["p1/c2", "p1/c3"])
        self.script(cost=0.1, draft=GOOD_DRAFT)
        s, _, made = call(self.url, "POST", "/verifier/v1/drafts", {"input": DRAFT_INPUT}, token=self.alice)
        self.assertEqual(s, 202)
        for _ in range(100):
            got = call(self.url, "GET", f"/verifier/v1/drafts/{made['draft']}", token=self.alice)[2]
            if got["state"] != "working":
                break
            time.sleep(0.1)
        self.assertEqual(got["state"], "done")

    def test_the_service_being_down_is_a_plain_502(self):
        self.srv.RequestHandlerClass.service_url = "http://127.0.0.1:1"
        s, _, out = call(self.url, "GET", "/verifier/health")
        self.assertEqual(s, 502)
        self.assertIn("could not be reached", out["message"])


class Commands(Hosted):
    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, {**CLEAN_ENV, "GT_CONFIG": str(self.root / "home" / "cli.json"), "GT_DATA": str(self.data),
                                           "FAKE_CLAUDE_SCRIPT": str(self.root / "script.json")}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def test_users_add_list_limit_token_remove(self):
        out, code = run(["users", "add", "carol", "--limit", "50"])
        self.assertEqual(code, 0, out)
        token = out.split("only its hash is kept): ")[1].split()[0]
        self.assertIn("Added carol, with a limit of $50 a month.", out)
        self.assertNotIn(token, (self.data / "store.db").read_bytes().decode("latin-1"))
        self.assertEqual(call(self.svc_url, "GET", "/health", token=token)[2]["user"], "carol")
        self.assertIn("already a user called carol", str(run(["users", "add", "carol"])[1]))
        self.store.record_spend(self.store.user_by_token(token), "r1", "run", 12.5)
        out, _ = run(["users", "list"])
        self.assertIn("carol: limit $50, spent $12.50 this month on 1 run, 0 stored runs", out)
        self.assertIn("alice: limit none, spent $0 this month on 0 runs, 0 stored runs", out)
        self.assertIn("now has a limit of $12.50", run(["users", "limit", "carol", "12.5"])[0])
        s, _, out_ = call(self.svc_url, "POST", "/v1/runs", {"input": RUN_INPUT}, token=token)
        self.assertEqual((s, out_["message"]), (402, limit_message(12.5)))
        self.assertIn("no spending limit", run(["users", "limit", "carol", "none"])[0])
        out, code = run(["users", "token", "carol"])
        new = out.strip().splitlines()[-1]
        self.assertEqual(call(self.svc_url, "GET", "/health", token=token)[0], 401)
        self.assertEqual(call(self.svc_url, "GET", "/health", token=new)[0], 200)
        self.upload(new)
        self.assertIn("1 stored run, which would be left with no owner", str(run(["users", "remove", "carol"])[1]))
        self.assertIn("Removed bob", run(["users", "remove", "bob"])[0])
        self.assertEqual(call(self.svc_url, "GET", "/health", token=self.bob)[0], 401)
        self.assertIn("no user called nobody", str(run(["users", "limit", "nobody", "5"])[1]))
        self.assertIn("is not an amount", str(run(["users", "limit", "carol", "lots"])[1]))

    def test_login_with_one_address_saves_both_and_every_command_sends_the_token(self):
        out, code = run(["login", self.url, "--token", self.alice])
        self.assertEqual(code, 0, out)
        f = Path(os.environ["GT_CONFIG"])
        self.assertEqual(json.loads(f.read_text()), {"service": f"{self.url}/verifier", "store": self.url, "token": self.alice})
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
        self.assertEqual(groundtruth.service_token(), self.alice)
        _, code = run(["login", self.url, "--token", "wrong"])
        self.assertIn("did not accept that token (401)", str(code))

    def test_verify_push_feedback_and_draft_each_send_the_saved_token(self):
        from tests.test_agent_commands import App, FakeService, make_repo, serve
        from tests.test_cli import DraftService

        class Recording(FakeService):
            seen = []

            def do_POST(self):  # noqa: N802
                Recording.seen.append((self.path, self.headers.get("Authorization")))
                super().do_POST()

        repo = make_repo(self.root)
        app, app_url = serve(App)
        fake, fake_url = serve(Recording)
        drafts, drafts_url = serve(DraftService)
        for s in (app, fake, drafts):
            self.addCleanup(s.server_close)
            self.addCleanup(s.shutdown)
        (repo / ".groundtruth" / "env.json").write_text(json.dumps({"url": app_url}))
        os.environ["PATH"] = str(self.root / "no-gh")
        cfg = Path(os.environ["GT_CONFIG"])
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({"service": fake_url, "store": self.url, "token": self.alice}))
        out, code = run(["verify", "tabs"], cwd=repo)
        self.assertEqual(code, 0, out)
        self.assertEqual({a for _, a in Recording.seen}, {f"Bearer {self.alice}"})
        self.assertEqual([p for p, _ in Recording.seen][:2], ["/v1/runs", "/v1/runs/r1/results"])
        self.assertEqual(len(call(self.url, "GET", "/v1/runs", token=self.alice)[2]["runs"]), 1)      # pushed with the token
        out, code = run(["feedback", "tabs", "--kind", "right"], cwd=repo)
        self.assertEqual(code, 0, out)
        rid = call(self.url, "GET", "/v1/runs", token=self.alice)[2]["runs"][0]["id"]
        self.assertEqual(call(self.url, "GET", f"/v1/runs/{rid}/feedback", token=self.alice)[2]["feedback"][0]["kind"], "right")
        DraftService.answer, DraftService.working, DraftService.auth[:] = ANSWER, 0, []
        out, code = run(["draft", "--service", drafts_url, "--said", "-"], cwd=repo, stdin="words")
        self.assertEqual(code, 0, out)
        self.assertEqual(set(DraftService.auth), {f"Bearer {self.alice}"})
        # and a store elsewhere never gets this machine's local token file
        cfg.write_text(json.dumps({"service": fake_url, "store": "https://gt.example.com", "token": ""}))
        from toolserver.push import PushError
        with self.assertRaises(PushError):
            groundtruth.store_token("https://gt.example.com")

    def test_the_limit_in_the_command_lines_own_words(self):
        from tests.test_agent_commands import App, FakeService, make_repo, serve
        from tests.test_cli import DraftService

        class Limited(FakeService):
            refuse = True

            def do_POST(self):  # noqa: N802
                if Limited.refuse:
                    self.rfile.read(int(self.headers.get("Content-Length") or 0))
                    return self.reply(402, {"error": "spending_limit", "message": limit_message(50)})
                if self.path != "/v1/runs":                       # a report that the limit stopped part way
                    self.rfile.read(int(self.headers.get("Content-Length") or 0))
                    props = [{"id": "tabs", "statement": "s", "verdict": "cant_tell", "cases": [], "not_run": ["v2"]}]
                    return self.reply(200, {"report": {"run": "r1", "order": ["tabs"], "properties": props, "stopped_by_limit": {
                        "message": limit_message(50), "cases_not_run": ["tabs/v2"], "properties_not_planned": ["result"]}}})
                super().do_POST()

        class DraftLimited(DraftService):
            def do_POST(self):  # noqa: N802
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                self.reply(402, {"error": "spending_limit", "message": limit_message(50)})

        repo = make_repo(self.root)
        app, app_url = serve(App)
        svc, svc_url = serve(Limited)
        drafts, drafts_url = serve(DraftLimited)
        for s in (app, svc, drafts):
            self.addCleanup(s.server_close)
            self.addCleanup(s.shutdown)
        (repo / ".groundtruth" / "env.json").write_text(json.dumps({"url": app_url}))
        os.environ["PATH"] = str(self.root / "no-gh")
        _, code = run(["verify", "tabs", "--service", svc_url], cwd=repo)
        self.assertEqual(code, limit_message(50))
        import shutil
        shutil.rmtree(repo / ".groundtruth" / "runs")         # the refused run's folder; the next one may start in the same second
        Limited.refuse = False
        out, code = run(["verify", "tabs", "--service", svc_url, "--no-push"], cwd=repo)
        self.assertIn(f"The run stopped early. {limit_message(50)}\n  Not run for that reason: tabs/v2\n"
                      "  Not planned for that reason: result\n", out)
        run_json = json.loads(sorted((repo / ".groundtruth" / "runs").iterdir())[-1].joinpath("run.json").read_text())
        self.assertEqual(run_json["stopped_by_limit"]["cases_not_run"], ["tabs/v2"])
        _, code = run(["draft", "--service", drafts_url, "--said", "-"], cwd=repo, stdin="words")
        self.assertEqual(code, limit_message(50))

    def test_a_removed_user_spends_nothing_more(self):
        uid = self.store.user_by_token(self.alice)
        self.assertIsNone(self.store.over_limit(uid))
        self.store.remove_user("alice")
        self.assertIsNotNone(self.store.over_limit(uid))

    def test_login_checks_a_store_elsewhere_with_the_token(self):
        # a store not on this machine is asked who the token belongs to; here "elsewhere" is simulated by is_local
        with mock.patch.object(groundtruth, "is_local", lambda host: False):
            _, code = run(["login", "--service", self.svc_url, "--store", self.url, "--token", self.alice])
            self.assertEqual(code, 0)
            _, code = run(["login", "--service", self.svc_url, "--store", "http://127.0.0.1:1", "--token", self.alice])
            self.assertIn("Couldn't reach the results store", str(code))

    def test_the_settings_file_is_cli_json_and_the_old_one_is_never_read(self):
        with mock.patch.dict(os.environ, {"HOME": str(self.root / "home2")}):
            os.environ.pop("GT_CONFIG")
            old = self.root / "home2" / ".groundtruth" / "config.json"
            old.parent.mkdir(parents=True)
            old.write_text(json.dumps({"service": "http://old", "store": "http://old", "token": "old"}))
            self.assertEqual(groundtruth.config_path(), self.root / "home2" / ".groundtruth" / "cli.json")
            self.assertEqual(groundtruth.load_config(), {})
            self.assertEqual(groundtruth.service_address(), "http://127.0.0.1:8790")


class LocalUnchanged(unittest.TestCase):
    def test_a_local_service_and_store_need_no_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = service_server.make_server(0, "scripted", workdir=Path(tmp) / "w")
            threading.Thread(target=svc.serve_forever, daemon=True).start()
            url = f"http://127.0.0.1:{svc.server_address[1]}"
            try:
                self.assertEqual(call(url, "GET", "/health")[2], {"ok": True, "version": "3", "model": "scripted"})
                self.assertEqual(call(url, "DELETE", "/v1/runs/nope")[0], 404)
                self.assertEqual(call(url, "GET", "/v1/drafts/nope")[0], 404)
                self.assertIsNone(svc.cfg["store"])          # a local service never opens the store's database
            finally:
                svc.shutdown()
                svc.server_close()
            srv, surl = start_in_background(Path(tmp) / "data", 0, Path(tmp))
            try:
                self.assertTrue((Path(tmp) / "data" / "token").is_file())
                self.assertEqual(call(surl, "GET", "/v1/runs")[0], 200)
                self.assertEqual(call(surl, "POST", "/v1/session", {"token": "x"})[0], 404)
                self.assertEqual(call(surl, "GET", "/verifier/health")[0], 404)
            finally:
                srv.shutdown()
                srv.server_close()

    def test_only_hosted_mode_may_listen_beyond_this_machine(self):
        from store.hosting import bind_host
        self.assertEqual(bind_host(None, False), "127.0.0.1")
        with self.assertRaises(SystemExit):
            bind_host("0.0.0.0", False)
        self.assertEqual(bind_host("0.0.0.0", True), "0.0.0.0")
        with self.assertRaises(SystemExit):
            bind_host("10.0.0.5", True)


if __name__ == "__main__":
    unittest.main()
