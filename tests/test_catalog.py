"""Tests for toolserver.catalog. No model, no network beyond a local http.server on port 0.

Run from the repo root:  .venv/bin/python -m unittest tests.test_catalog -v
"""
import json
import os
import sys
import tempfile
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from toolserver.catalog import Catalog  # noqa: E402

PAGE = b"""<!doctype html><html><head><title>t</title></head><body>
<label for="who">Name</label><input id="who" type="text">
<button onclick="document.getElementById('out').textContent = 'Hello ' + document.getElementById('who').value">Greet</button>
<p id="out" data-testid="out"></p>
</body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = PAGE if self.path in ("/", "/index.html") else b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "text/html" if body is PAGE else "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class _Server:
    def __enter__(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()


def _fake_modules():
    """Fakes of toolserver.routes / toolserver.foreach with the agreed signatures (deterministic for tests)."""
    routes = types.ModuleType("toolserver.routes")

    def list_routes(repo, app_url, source=None):
        return {"routes": [{"method": "GET", "path": "/api/a", "params": [], "sources": ["code"]}],
                "sources": ["code"], "agree": True, "disagreements": [], "note": ""}

    routes.list_routes = list_routes
    routes.format_routes = lambda result: "\n".join(f"{r['method']} {r['path']}" for r in result["routes"])

    foreach = types.ModuleType("toolserver.foreach")

    def probe_each(app_url, routes_, *, methods=None, path_prefix=None, headers=None, body=None,
                   expect_status, timeout=10):
        return {"kind": "probe", "checked": len(routes_), "passed": len(routes_), "violators": [],
                "skipped": [], "results": [], "rule": f"status == {expect_status}"}

    def match_each(repo, glob, *, must_contain=None, must_not_contain=None):
        return {"kind": "match", "checked": 1, "passed": 1, "violators": [], "skipped": [], "results": [],
                "rule": f"{glob} contains {must_contain}"}

    foreach.probe_each = probe_each
    foreach.match_each = match_each
    foreach.format_result = lambda r: f"{r['passed']}/{r['checked']} passed ({r['rule']})"
    return {"toolserver.routes": routes, "toolserver.foreach": foreach}


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name).resolve()
        self.repo = root / "repo"
        (self.repo / "src").mkdir(parents=True)
        (self.repo / "src" / "app.py").write_text("\n".join(f"line {i}" for i in range(1, 31)) + "\n")
        (self.repo / ".env").write_text("SECRET=1\n")
        (self.repo / "node_modules").mkdir()
        (self.repo / "node_modules" / "dep.js").write_text("line 1\n")
        (root / "outside.txt").write_text("outside 1\noutside 2\n")
        os.symlink(root / "outside.txt", self.repo / "src" / "escape.txt")
        self.claim = root / "claim"
        self.cat = Catalog({"url": "http://127.0.0.1:9", "repo": str(self.repo)}, self.claim)

    def tearDown(self):
        self.cat.close()
        self.tmp.cleanup()

    def receipts(self):
        return [json.loads(l) for l in (self.claim / "receipts.jsonl").read_text().splitlines()]

    def assertRefused(self, res):
        self.assertTrue(res["isError"], res)
        self.assertTrue(res["text"].startswith("refused: "), res)
        self.assertIsNone(res["data"])

    # -------------------------------------------------- dispatch and refusals
    def test_unknown_tool_refused(self):
        self.assertRefused(self.cat.run("shell", {"cmd": "ls"}))
        self.assertRefused(self.cat.run(["not", "a", "name"], None))
        self.assertNotIn("submit_report", self.cat.names())

    def test_wrong_argument_name_and_type_refused(self):
        self.assertRefused(self.cat.run("read_lines", {"file": "src/app.py", "start": 1, "end": 2}))
        self.assertRefused(self.cat.run("read_lines", {"path": "src/app.py"}))
        self.assertRefused(self.cat.run("read_lines", {"path": "src/app.py", "start": "1", "end": 2}))
        self.assertRefused(self.cat.run("read_lines", ["src/app.py", 1, 2]))

    def test_http_request_off_app_refused(self):
        self.assertRefused(self.cat.run("http_request", {"method": "GET", "path": "file:///etc/hosts"}))
        self.assertRefused(self.cat.run("http_request", {"method": "GET", "path": "http://example.com/"}))
        self.assertRefused(self.cat.run("http_request", {"method": "GET", "path": "http://127.0.0.1:9.evil.com/"}))
        self.assertRefused(self.cat.run("navigate", {"path": "file:///etc/hosts"}))

    def test_http_request_to_app(self):
        with _Server() as s:
            cat = Catalog({"url": s.url}, self.claim)
            res = cat.run("http_request", {"method": "get", "path": "/api/x"})
            self.assertFalse(res["isError"], res)
            self.assertEqual(res["text"], '200\n{"ok": true}')
            res = cat.run("http_request", {"method": "GET", "path": s.url + "/api/x"})
            self.assertFalse(res["isError"], res)

    def test_read_lines_limits(self):
        self.assertRefused(self.cat.run("read_lines", {"path": "src/app.py", "start": 1, "end": 16}))
        res = self.cat.run("read_lines", {"path": "src/app.py", "start": 1, "end": 15})
        self.assertFalse(res["isError"], res)
        self.assertEqual(len(res["text"].splitlines()), 15)
        self.assertEqual(res["text"].splitlines()[0], "   1 line 1")

    def test_read_lines_outside_or_hidden_refused(self):
        self.assertRefused(self.cat.run("read_lines", {"path": "../outside.txt", "start": 1, "end": 2}))
        self.assertRefused(self.cat.run("read_lines", {"path": "/etc/hosts", "start": 1, "end": 2}))
        self.assertRefused(self.cat.run("read_lines", {"path": "src/escape.txt", "start": 1, "end": 2}))
        self.assertRefused(self.cat.run("read_lines", {"path": ".env", "start": 1, "end": 1}))
        self.assertRefused(self.cat.run("read_lines", {"path": "node_modules/dep.js", "start": 1, "end": 1}))

    def test_list_files_and_grep_hide_files(self):
        self.assertEqual(self.cat.run("list_files", {})["text"], "src/app.py")
        res = self.cat.run("grep", {"pattern": "line 1$|SECRET|outside"})
        self.assertEqual(res["text"], "src/app.py:1: line 1")

    def test_code_tools_need_repo(self):
        cat = Catalog({"url": "http://127.0.0.1:9"}, self.claim)
        res = cat.run("list_files", None)
        self.assertRefused(res)
        self.assertIn("no repo is registered", res["text"])

    def test_process_tools(self):
        res = self.cat.run("process_info", {})
        self.assertRefused(res)
        self.assertIn("no process id is registered", res["text"])
        cat = Catalog({"url": "http://127.0.0.1:9", "pid": os.getpid()}, self.claim)
        res = cat.run("process_info", {})
        self.assertFalse(res["isError"], res)
        pids = [int(l.split()[0]) for l in res["text"].splitlines() if l.strip()]
        self.assertIn(os.getpid(), pids)

    def test_reset_data(self):
        self.assertIn("no reset command is registered", self.cat.run("reset_data", {})["text"])
        cat = Catalog({"url": "http://127.0.0.1:9", "repo": str(self.repo), "reset": "touch reset-ran"}, self.claim)
        self.assertEqual(cat.run("reset_data", {})["text"], "reset done")
        self.assertTrue((self.repo / "reset-ran").exists())
        cat = Catalog({"url": "http://127.0.0.1:9", "reset": "echo boom; exit 3"}, self.claim)
        res = cat.run("reset_data", {})
        self.assertTrue(res["isError"])
        self.assertTrue(res["text"].startswith("error: ") and "boom" in res["text"], res)

    def test_receipts(self):
        self.cat.phase = 2
        self.cat.run("list_files", {})
        self.cat.run("nope", {"x": object()})
        r = self.receipts()
        self.assertEqual(len(r), 2)
        self.assertEqual(r[0]["phase"], 2)
        self.assertEqual(r[0]["tool"], "list_files")
        self.assertEqual(r[0]["result"], "src/app.py")
        self.assertNotIn("refused", r[0])
        self.assertTrue(r[1]["refused"])
        self.assertTrue(r[1]["result"].startswith("refused: "))
        self.assertIsInstance(r[1]["args"]["x"], str)  # repr fallback
        self.assertIsInstance(r[0]["t"], float)

    # -------------------------------------------------- sweeps and finish
    def test_sweeps_and_finish(self):
        with mock.patch.dict(sys.modules, _fake_modules()):
            res = self.cat.run("list_routes", {})
            self.assertEqual(res["text"], "GET /api/a")
            self.assertTrue(res["data"]["agree"])
            res = self.cat.run("probe_each", {"expect_status": 401, "methods": ["GET"]})
            self.assertFalse(res["isError"], res)
            self.assertTrue(res["text"].startswith("check-1\n1/1 passed"), res)
            self.assertEqual(res["data"]["id"], "check-1")
            self.assertEqual(res["data"]["sources"], ["code"])
            saved = json.loads((self.claim / "check-1.json").read_text())
            self.assertEqual(saved["rule"], "status == 401")
            res = self.cat.run("match_each", {"glob": "**/*.py", "must_contain": "line"})
            self.assertTrue(res["text"].startswith("check-2\n"), res)
        (self.claim / "shot-1.png").write_bytes(b"\x89PNG")
        report = self.cat.finish({
            "verdict": "holds", "evidence": "e", "settled_by": "code", "extra": {"k": 1},
            "excerpts": [{"path": "src/app.py", "start": 3, "end": 5, "why": "w"},
                         {"path": "src/app.py", "start": 1, "end": 16, "why": "too long"},
                         {"path": "../outside.txt", "start": 1, "end": 1, "why": "escape"}],
            "screenshots": ["shot-1", "shot-9", "../receipts"],
            "checks": ["check-1", "check-7"],
        })
        self.assertEqual(report["extra"], {"k": 1})
        self.assertEqual(len(report["excerpts"]), 1)
        self.assertEqual(report["excerpts"][0]["lines"], ["line 3", "line 4", "line 5"])
        self.assertEqual(report["screenshots"], ["shot-1"])
        self.assertEqual([c["id"] for c in report["checks"]], ["check-1"])
        notes = " ".join(report["notes"])
        for word in ("longer than 15", "outside the repo", "shot-9", "check-7"):
            self.assertIn(word, notes)
        self.assertEqual(json.loads((self.claim / "report.json").read_text()), report)

    def test_match_each_glob_escape_refused(self):
        for g in ("../*.txt", "/etc/*", "src/../../*.txt", "~/x"):
            self.assertRefused(self.cat.run("match_each", {"glob": g, "must_contain": "outside"}))

    def test_sweeps_against_real_modules(self):
        try:
            import toolserver.foreach  # noqa: F401
            import toolserver.routes  # noqa: F401
        except Exception as e:  # noqa: BLE001
            self.skipTest(f"real toolserver.routes / foreach not importable yet: {e}")
        with _Server() as s:
            cat = Catalog({"url": s.url, "repo": str(self.repo)}, self.claim)
            res = cat.run("list_routes", {})
            self.assertNotIn("refused: bad arguments", res["text"], res)
            res = cat.run("probe_each", {"expect_status": [200]})
            self.assertNotIn("refused: bad arguments", res["text"], res)
            res = cat.run("match_each", {"glob": "**/*.py", "must_contain": "line 1"})
            self.assertFalse(res["isError"], res)
            self.assertEqual(res["data"]["checked"], 1)
            self.assertTrue((self.claim / f"{res['data']['id']}.json").is_file())
            res = cat.run("match_each", {"glob": "src/*.txt", "must_contain": "outside"})
            self.assertEqual(res["data"]["checked"], 0, res)  # symlink escape skipped

    # -------------------------------------------------- browser
    def test_browser(self):
        try:
            from playwright.sync_api import sync_playwright
            pw = sync_playwright().start()
            try:
                pw.chromium.launch().close()
            finally:
                pw.stop()
        except Exception as e:  # noqa: BLE001
            self.skipTest(f"Chromium cannot launch here: {e}")
        with _Server() as s:
            cat = Catalog({"url": s.url}, self.claim)
            try:
                res = cat.run("observe", {})
                self.assertFalse(res["isError"], res)
                self.assertIn('button "Greet"', res["text"])
                self.assertEqual(cat.run("fill", {"label": "Name", "value": "Ada"})["text"],
                                 'filled the field labelled "Name" with 3 characters (3 not counting spaces)')
                self.assertEqual(cat.run("click", {"role": "button", "name": "Greet"})["text"], 'clicked button "Greet"')
                self.assertIn("Hello Ada", cat.run("observe", {})["text"])
                self.assertEqual(cat.run("screenshot", {"label": "after"})["text"], "shot-1")
                self.assertTrue((self.claim / "shot-1.png").stat().st_size > 0)
                self.assertTrue(cat.run("click", {"role": "button", "name": "Missing"})["text"].startswith("error: "))
            finally:
                cat.close()
                cat.close()

    def test_close_twice_without_browser(self):
        self.cat.close()
        self.cat.close()


if __name__ == "__main__":
    unittest.main()
