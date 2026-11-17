"""Filming a case: a case's browser is recorded, with a drawn pointer that glides to each click, and the clip is kept
in the case's folder once the browser closes. The model sees no difference: observe is the same with filming on or off."""
import json
import re
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from toolserver.catalog import Catalog
from toolserver.runner import Runner

PAGE = b"""<!doctype html><html><head><meta charset="utf-8"><title>controls</title></head><body>
<h1>Shop</h1>
<button onclick="document.getElementById('out').textContent = 'added'">Add to cart</button>
<label>Quantity <input id="q" value="1"></label>
<button onclick="this.remove(); document.getElementById('out').textContent = 'gone'">Vanish</button>
<a href="/second">Next page</a>
<p id="out">nothing yet</p>
</body></html>"""
SECOND = b"""<!doctype html><html><head><meta charset="utf-8"></head><body>
<button onclick="document.getElementById('o2').textContent = 'second clicked'">Again</button><p id="o2">-</p></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = {"/": PAGE, "/second": SECOND}.get(self.path)
        self.send_response(200 if body else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8" if body else "text/plain")
        self.end_headers()
        self.wfile.write(body or b"none")

    def log_message(self, *a):
        pass


CURSOR = """() => { const c = document.querySelector('groundtruth-cursor');
  return c && {parent: c.parentElement.tagName, inBody: document.body.contains(c),
               pe: getComputedStyle(c).pointerEvents, hidden: c.getAttribute('aria-hidden')}; }"""


class CaseVideo(unittest.TestCase):
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
        self.dir = Path(self.tmp.name)
        self.env = {"url": f"http://127.0.0.1:{self.httpd.server_address[1]}"}
        self.cats = []

    def tearDown(self):
        for c in self.cats:
            c.close()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def catalog(self, name, record):
        c = Catalog(self.env, self.dir / name, record=record)
        self.cats.append(c)
        return c

    def runner(self):
        return Runner(self.env, [{"id": "p1", "statement": "s"}], self.dir / "run", say=lambda *a: None)

    @staticmethod
    def act(r, tool, **args):
        res = r.execute({"tool": tool, "args": args})
        assert not res["isError"], res
        return res

    # ---------------------------------------------------------------- clips through the runner
    def test_a_finished_case_leaves_one_clip_listed_in_its_report(self):
        r = self.runner()
        self.act(r, "begin_claim", claim="p1/c1")
        before = __import__("time").time()
        self.act(r, "click", role="button", name="Add to cart")
        rep = self.act(r, "end_claim", claim="p1/c1", report={"outcome": "holds", "observed": "x"})["data"]
        unit = self.dir / "run" / "p1" / "c1"
        self.assertGreater((unit / "clip-1.webm").stat().st_size, 0)
        started = json.loads((unit / "clip-1.json").read_text())["started"]
        self.assertLessEqual(abs(started - before), 30)
        self.assertEqual(rep["clips"], [{"file": "clip-1.webm", "started": started}])
        self.assertEqual(json.loads((unit / "report.json").read_text())["clips"], rep["clips"])
        self.assertFalse((unit / ".clip-tmp").exists())

    def test_a_second_try_of_the_same_unit_keeps_both_clips(self):
        r = self.runner()
        self.act(r, "begin_claim", claim="p1/c1")
        self.act(r, "click", role="button", name="Add to cart")
        self.act(r, "begin_claim", claim="p1/c1")          # the harness's second attempt: same folder, new catalog
        self.act(r, "click", role="button", name="Add to cart")
        rep = self.act(r, "end_claim", claim="p1/c1", report={"outcome": "holds", "observed": "x"})["data"]
        unit = self.dir / "run" / "p1" / "c1"
        self.assertEqual([c["file"] for c in rep["clips"]], ["clip-1.webm", "clip-2.webm"])
        for n in (1, 2):
            self.assertGreater((unit / f"clip-{n}.webm").stat().st_size, 0)
            self.assertTrue((unit / f"clip-{n}.json").is_file())
        self.assertLessEqual(rep["clips"][0]["started"], rep["clips"][1]["started"])
        self.assertFalse((unit / ".clip-tmp").exists())

    def test_discovery_and_a_case_with_no_page_leave_no_clip(self):
        r = self.runner()
        self.act(r, "begin_claim", claim="p1/discovery")
        self.act(r, "observe")                                # it does open a page; it is just not filmed
        rep = self.act(r, "end_claim", claim="p1/discovery", report={"role": "discovery"})["data"]
        self.assertNotIn("clips", rep)
        self.assertEqual(list((self.dir / "run" / "p1" / "discovery").glob("clip-*")), [])
        self.assertFalse((self.dir / "run" / "p1" / "discovery" / ".clip-tmp").exists())

        self.act(r, "begin_claim", claim="p1/api1")
        self.act(r, "http_request", method="GET", path="/")
        rep = self.act(r, "end_claim", claim="p1/api1", report={"outcome": "holds", "observed": "x"})["data"]
        self.assertNotIn("clips", rep)
        self.assertEqual(sorted(f.name for f in (self.dir / "run" / "p1" / "api1").iterdir()), ["receipts.jsonl", "report.json"])

    def test_a_report_cannot_claim_a_clip_that_was_not_made(self):
        r = self.runner()
        self.act(r, "begin_claim", claim="p1/api2")
        rep = self.act(r, "end_claim", claim="p1/api2", report={"outcome": "holds", "clips": [{"file": "clip-9.webm"}]})["data"]
        self.assertNotIn("clips", rep)

    # ---------------------------------------------------------------- what the model sees
    def test_observe_is_the_same_with_and_without_filming_after_a_click(self):
        outs = []
        for name, record in (("plain", False), ("filmed", True)):
            c = self.catalog(name, record)
            first = c.run("observe", {})["text"]
            self.assertIn("Add to cart", first, first)
            ref = re.search(r'button "Add to cart" \[ref=(\w+)\]', first).group(1)
            self.assertEqual(c.run("click", {"ref": ref})["text"], 'clicked button "Add to cart"')
            c.run("fill", {"label": "Quantity", "value": "3"})
            outs.append((first, c.run("observe", {})["text"]))
            if record:
                self.assertIsNotNone(c.page().evaluate(CURSOR))   # the pointer is really there when observe runs
            c.close()   # one Playwright at a time: a second sync Playwright in the same thread fails ("Sync API inside the asyncio loop"); a run never has two
        self.assertEqual(outs[0], outs[1])
        self.assertIn("added", outs[1][1])

    def test_the_pointer_is_outside_body_ignores_the_pointer_and_is_hidden(self):
        c = self.catalog("filmed", True)
        c.run("click", {"role": "button", "name": "Add to cart"})
        self.assertEqual(c.page().evaluate(CURSOR), {"parent": "HTML", "inBody": False, "pe": "none", "hidden": "true"})
        self.assertEqual(c.page().evaluate("document.querySelectorAll('groundtruth-cursor').length"), 1)

    def test_after_a_navigation_the_pointer_is_drawn_again(self):
        c = self.catalog("filmed", True)
        c.run("click", {"role": "link", "name": "Next page"})
        c.page().wait_for_url("**/second")
        self.assertIsNone(c.page().evaluate(CURSOR))
        res = c.run("click", {"role": "button", "name": "Again"})
        self.assertFalse(res["isError"], res)
        self.assertIsNotNone(c.page().evaluate(CURSOR))
        self.assertIn("second clicked", c.run("observe", {})["text"])

    def test_a_glide_that_fails_never_stops_the_action(self):
        c = self.catalog("filmed", True)
        with mock.patch.object(c, "_glide_to", side_effect=RuntimeError("element detached mid-glide")):
            self.assertFalse(c.run("click", {"role": "button", "name": "Vanish"})["isError"])
            self.assertFalse(c.run("fill", {"label": "Quantity", "value": "7"})["isError"])
            res = c.run("click_and_watch", {"test_id": "none", "attribute": "x", "role": "button", "name": "Add to cart"})
        self.assertIn("gone", c.run("observe", {})["text"])
        self.assertNotIn("refused", res["text"])

    def test_every_action_on_an_element_glides_first(self):
        c = self.catalog("filmed", True)
        ref = re.search(r'button "Add to cart" \[ref=(\w+)\]', c.run("observe", {})["text"]).group(1)
        with mock.patch.object(c, "_glide_to", wraps=c._glide_to) as g:
            for tool, args in (("click", {"ref": ref}), ("click", {"role": "button", "name": "Add to cart"}),
                               ("fill", {"label": "Quantity", "value": "5"}),
                               ("click_and_watch", {"test_id": "none", "attribute": "x", "ms": 200, "role": "button", "name": "Add to cart"})):
                res = c.run(tool, args)
                self.assertNotIn("refused", res["text"], res)
        self.assertEqual(g.call_count, 4)
        self.assertEqual([call.args[1] for call in g.call_args_list], [True, True, False, True])   # no ring for typing

    def test_without_filming_no_pointer_is_ever_added(self):
        c = self.catalog("plain", False)
        c.run("click", {"role": "button", "name": "Add to cart"})
        c.run("fill", {"label": "Quantity", "value": "2"})
        c.run("click", {"role": "button", "name": "Vanish"})
        self.assertIsNone(c.page().evaluate(CURSOR))
        c.close()
        self.assertEqual([f.name for f in (self.dir / "plain").glob("clip-*")], [])
        self.assertFalse((self.dir / "plain" / ".clip-tmp").exists())

    def test_close_twice_keeps_one_clip(self):
        c = self.catalog("filmed", True)
        c.run("observe", {})
        c.close()
        c.close()
        self.assertEqual(sorted(f.name for f in (self.dir / "filmed").glob("clip-*")), ["clip-1.json", "clip-1.webm"])


if __name__ == "__main__":
    unittest.main()


class ReportClips(unittest.TestCase):
    """The HTML report links each clip by a path relative to report.html, never embedding it."""

    def test_clips_are_linked_under_the_case_and_a_case_without_one_shows_none(self):
        import report
        with tempfile.TemporaryDirectory() as t:
            run = Path(t)
            (run / "p1" / "c1").mkdir(parents=True)
            (run / "p1" / "c2").mkdir(parents=True)
            for n in (1, 2):
                (run / "p1" / "c1" / f"clip-{n}.webm").write_bytes(b"\x1aE\xdf\xa3 not really a film")
            clips = [{"file": "clip-1.webm", "started": 1794000000.0}, {"file": "clip-2.webm", "started": 1794000060.0}]
            cases = [{"id": "c1", "result": {"outcome": "holds", "observed": "x", "clips": clips}},
                     {"id": "c2", "result": {"outcome": "holds", "observed": "y",
                                             "clips": [{"file": "clip-1.webm"}, {"file": "../../x.webm"}]}}]
            (run / "run.json").write_text(json.dumps({"feature": "f", "properties": [
                {"id": "p1", "statement": "s", "verdict": "holds", "plan": {"kind": "behaviour"}, "cases": cases}]}))
            page = report.write_report(run).read_text()
        self.assertIn('<video controls preload="metadata" src="p1/c1/clip-1.webm">', page)
        self.assertIn('<video controls preload="metadata" src="p1/c1/clip-2.webm">', page)
        self.assertIn("second try", page)
        self.assertEqual(page.count("<video"), 2)          # c2's clip file does not exist, and a path is never followed
        self.assertNotIn("data:video", page)
