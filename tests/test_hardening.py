"""Fixes made after a review of the first split build: each was a real hole."""
import http.server
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from toolserver.catalog import Catalog  # noqa: E402
from toolserver.runner import registration_problem  # noqa: E402


def serve(handler):
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


class Hardening(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def dead_pid(self):
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        return p.pid

    def test_a_dead_process_is_an_error_not_a_clean_measurement(self):
        cat = Catalog({"url": "http://127.0.0.1:9", "pid": self.dead_pid()}, self.dir / "c")
        for tool in ("gpu_check", "process_info", "cpu_sample", "net_connections"):
            res = cat.run(tool, {"seconds": 1} if tool == "cpu_sample" else {})
            self.assertTrue(res["isError"], tool)
            self.assertIn("no longer running", res["text"], tool)
            self.assertNotIn("not loaded", res["text"], tool)

    def test_verify_refuses_a_stale_registration(self):
        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self):
                self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"ok")
        httpd, url = serve(H)
        try:
            self.assertIsNone(registration_problem({"url": url}))
            self.assertIn("no longer running", registration_problem({"url": url, "pid": self.dead_pid()}))
        finally:
            httpd.shutdown()
        self.assertIn("nothing answers", registration_problem({"url": url}))

    def test_grep_returns_at_most_15_lines_of_source(self):
        repo = self.dir / "repo"
        repo.mkdir()
        (repo / "a.py").write_text("\n".join(f"secret_{i} = {i}" for i in range(60)))
        res = Catalog({"url": "http://127.0.0.1:9", "repo": str(repo)}, self.dir / "c").run("grep", {"pattern": "secret_"})
        lines = res["text"].splitlines()
        self.assertEqual(sum(1 for line in lines if line.startswith("a.py:")), 15)
        self.assertIn("+45 more matching lines not shown", lines[-1])

    def test_the_browser_cannot_reach_another_host(self):
        hits = []

        class Other(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self):
                hits.append(self.path)
                self.send_response(200); self.send_header("Content-Length", "5"); self.end_headers(); self.wfile.write(b"other")
        other, other_url = serve(Other)

        class App(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self):
                if self.path == "/bounce":
                    self.send_response(302); self.send_header("Location", other_url + "/redirected"); self.send_header("Content-Length", "0"); self.end_headers()
                    return
                if self.path == "/hop":         # a redirect that stays inside the app, then leaves it
                    self.send_response(302); self.send_header("Location", "/bounce"); self.send_header("Content-Length", "0"); self.end_headers()
                    return
                if self.path == "/moved":       # an ordinary redirect inside the app must keep working
                    self.send_response(302); self.send_header("Location", "/landed"); self.send_header("Content-Length", "0"); self.end_headers()
                    return
                body = (f'<html><body><h1>App</h1><img src="{other_url}/pixel.png"><script src="{other_url}/x.js"></script>'
                        f'<a href="{other_url}/clicked">Leave</a><a href="/bounce">Bounce</a></body></html>').encode()
                self.send_response(200); self.send_header("Content-Type", "text/html"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        app, app_url = serve(App)
        cat = Catalog({"url": app_url}, self.dir / "c")
        try:
            try:
                first = cat.run("observe", {})
            except Exception as e:  # noqa: BLE001
                self.skipTest(f"Chromium could not launch: {e}")
            if first["isError"] and "Executable doesn't exist" in first["text"]:
                self.skipTest("Chromium is not installed")
            self.assertIn("App", first["text"])
            cat.run("click", {"role": "link", "name": "Leave"})
            cat.run("navigate", {"path": "/"})
            cat.run("click", {"role": "link", "name": "Bounce"})
            self.assertIn("blocked a redirect", cat.run("observe", {})["text"])
            cat.run("navigate", {"path": "/hop"})
            self.assertIn("blocked a redirect", cat.run("observe", {})["text"])
            self.assertIn("/landed", cat.run("navigate", {"path": "/moved"})["text"])
            self.assertEqual(hits, [], "the page reached another host")
            self.assertGreaterEqual(len(cat.blocked), 5)
        finally:
            cat.close(); app.shutdown(); other.shutdown()

    def test_probe_each_accepts_a_json_object_body(self):
        seen = []

        class App(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_POST(self):
                seen.append((self.headers.get("Content-Type"), self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()))
                self.send_response(409); self.send_header("Content-Length", "0"); self.end_headers()
        app, app_url = serve(App)
        repo = self.dir / "repo"
        repo.mkdir()
        (repo / "routes.json").write_text(json.dumps([{"method": "POST", "path": "/api/a"}, {"method": "POST", "path": "/api/b"}]))
        cat = Catalog({"url": app_url, "repo": str(repo), "routes": {"type": "file", "path": "routes.json"}}, self.dir / "c")
        try:
            res = cat.run("probe_each", {"expect_status": [409], "methods": ["POST"], "body": {"listingId": "l6"}})
            self.assertFalse(res["isError"], res["text"])
            self.assertEqual(res["data"]["checked"], 2)
            self.assertEqual(seen, [("application/json", '{"listingId": "l6"}')] * 2)
        finally:
            app.shutdown()


if __name__ == "__main__":
    unittest.main()
