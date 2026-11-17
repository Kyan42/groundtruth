import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from toolserver.foreach import format_result, match_each, probe_each, replay  # noqa: E402
from toolserver.routes import list_routes  # noqa: E402

ROUTES = [
    {"method": "GET", "path": "/api/health", "params": False},
    {"method": "POST", "path": "/api/cart", "params": False},
    {"method": "POST", "path": "/api/watchlist", "params": False},
    {"method": "POST", "path": "/api/moved", "params": False},
    {"method": "GET", "path": "/api/orders/:id", "params": True},
    {"method": "ANY", "path": "/api/legacy", "params": False},
]


class Handler(BaseHTTPRequestHandler):
    seen = []

    def _reply(self):
        Handler.seen.append((self.command, self.path, self.headers.get("X-Test")))
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        if self.path == "/api/cart":
            code = 401
        elif self.path == "/api/moved":
            code = 302
        elif self.path in ("/api/watchlist", "/api/health"):
            code = 200
        else:
            code = 404
        self.send_response(code)
        if code == 302:
            self.send_header("Location", "/api/health")
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_GET = do_POST = _reply

    def log_message(self, *a):
        pass


class ProbeTests(unittest.TestCase):
    def setUp(self):
        Handler.seen = []
        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def test_probe_post(self):
        r = probe_each(self.url, ROUTES, methods=["POST"], path_prefix="/api/",
                       headers={"X-Test": "1"}, body="{}", expect_status=[401, 403])
        self.assertEqual(r["checked"], 3)
        self.assertEqual(r["passed"], 1)
        statuses = {x["path"]: x["status"] for x in r["results"]}
        self.assertEqual(statuses, {"/api/cart": 401, "/api/watchlist": 200, "/api/moved": 302})
        self.assertEqual(sorted(v["path"] for v in r["violators"]), ["/api/moved", "/api/watchlist"])
        # redirect not followed: /api/health never requested
        self.assertNotIn(("GET", "/api/health", "1"), Handler.seen)
        self.assertTrue(all(h == "1" for _, _, h in Handler.seen))
        self.assertEqual(r["skipped"], [{"method": "ANY", "path": "/api/legacy",
                                         "why": "method unknown (ANY)"}])
        self.assertEqual(r["rule"], {"kind": "probe_each", "methods": ["POST"], "path_prefix": "/api/",
                                     "headers": {"X-Test": "1"}, "body": "{}",
                                     "expect_status": [401, 403]})
        text = format_result(r)
        self.assertEqual(text.splitlines()[0], "checked 3, passed 1, violators 2, skipped 1")
        self.assertIn("VIOLATOR POST /api/watchlist -> status 200", text)

    def test_probe_all_skips_params(self):
        r = probe_each(self.url, ROUTES, expect_status=[200])
        self.assertEqual(r["checked"], 4)
        self.assertIn({"method": "GET", "path": "/api/orders/:id", "why": "path has parameters"},
                      r["skipped"])
        self.assertIn("SKIPPED GET /api/orders/:id: path has parameters", format_result(r))

    def test_connection_error(self):
        r = probe_each("http://127.0.0.1:1", ROUTES[:1], expect_status=[200], timeout=2)
        self.assertEqual(r["results"][0]["status"], 0)
        self.assertTrue(r["results"][0]["error"])
        self.assertEqual(len(r["violators"]), 1)

    def test_expect_status_required(self):
        for bad in ([], None, ["401"], [True], 401):
            with self.assertRaises(ValueError):
                probe_each(self.url, ROUTES, expect_status=bad)

    def test_checked_zero(self):
        r = probe_each(self.url, ROUTES, path_prefix="/nothing", expect_status=[401])
        self.assertEqual(r["checked"], 0)
        self.assertEqual(format_result(r).splitlines()[0],
                         "checked 0: nothing matched, so this proves nothing")
        r2 = probe_each(self.url, [], expect_status=[401])
        self.assertEqual(format_result(r2).splitlines()[0],
                         "checked 0: nothing matched, so this proves nothing")

    def test_never_leaves_app_host(self):
        r = probe_each(self.url, [{"method": "GET", "path": "@evil.example/x", "params": False}],
                       expect_status=[200])
        self.assertEqual(r["checked"], 0)
        self.assertEqual(len(r["skipped"]), 1)

    def test_replay_probe(self):
        root = Path(self.tmp.name)
        (root / "routes.json").write_text(json.dumps(
            [{"method": r["method"], "path": r["path"]} for r in ROUTES]))
        source = {"type": "file", "path": "routes.json"}
        routes = list_routes(root, self.url, source)["routes"]
        first = probe_each(self.url, routes, methods=["POST"], expect_status=[401])
        again = replay(first["rule"], repo=root, app_url=self.url, source=source)
        for k in ("checked", "passed"):
            self.assertEqual(first[k], again[k])
        self.assertEqual(first["violators"], again["violators"])
        self.assertEqual(first["skipped"], again["skipped"])
        self.assertEqual(first["checked"], 3)


class MatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        files = {
            "app/api/cart/route.ts": "import { auth } from '@/lib/auth'\nexport function POST() { auth() }\n",
            "app/api/watchlist/route.ts": "export function POST() {}\n// TODO\n",
            "app/api/health/route.ts": "export function GET() { auth() }\nconsole.log('x')\n",
            "app/node_modules/pkg/route.ts": "nothing\n",
            "app/.cache/route.ts": "nothing\n",
        }
        for rel, text in files.items():
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)

    def tearDown(self):
        self.tmp.cleanup()

    def test_match(self):
        r = match_each(self.root, "app/**/route.ts", must_contain=r"auth\(\)",
                       must_not_contain=r"console\.log")
        self.assertEqual(r["kind"], "match_each")
        self.assertEqual(r["checked"], 3)
        self.assertEqual(r["passed"], 1)
        self.assertNotIn("skipped", r)
        self.assertIn({"path": "app/api/watchlist/route.ts", "why": "missing must_contain"},
                      r["violators"])
        self.assertIn({"path": "app/api/health/route.ts", "why": "has must_not_contain at line 2"},
                      r["violators"])
        self.assertEqual(r["rule"], {"kind": "match_each", "glob": "app/**/route.ts",
                                     "must_contain": r"auth\(\)", "must_not_contain": r"console\.log"})
        self.assertEqual(format_result(r).splitlines()[0], "checked 3, passed 1, violators 2")

    def test_bad_inputs(self):
        with self.assertRaises(ValueError):
            match_each(self.root, "../**/*.ts", must_contain="x")
        with self.assertRaises(ValueError):
            match_each(self.root, "app/../../*.ts", must_contain="x")
        with self.assertRaises(ValueError):
            match_each(self.root, "/etc/*", must_contain="x")
        with self.assertRaises(ValueError):
            match_each(self.root, "app/**/*.ts")
        with self.assertRaises(ValueError):
            match_each(self.root, "app/**/*.ts", must_contain="(")

    def test_nothing_matched(self):
        r = match_each(self.root, "nope/**/*.py", must_contain="x")
        self.assertEqual(r["checked"], 0)
        self.assertEqual(format_result(r), "checked 0: nothing matched, so this proves nothing")

    def test_replay_match(self):
        r = match_each(self.root, "app/**/route.ts", must_contain=r"auth\(\)")
        again = replay(r["rule"], repo=self.root, app_url="http://127.0.0.1:1")
        self.assertEqual((r["checked"], r["passed"], r["violators"]),
                         (again["checked"], again["passed"], again["violators"]))
        with self.assertRaises(ValueError):
            replay({"kind": "other"}, repo=self.root, app_url="http://127.0.0.1:1")


if __name__ == "__main__":
    unittest.main()
