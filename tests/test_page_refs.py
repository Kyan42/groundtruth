"""Picking an element by the reference observe gave it: for controls with no name of their own, controls that share a
name, and fields that have only a placeholder. A reference is never a selector, and an old one never lands on another
element. Plus the media tool, which reads what the accessibility tree cannot show."""
import re
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from toolserver.catalog import Catalog

PAGE = b"""<!doctype html><html><head><meta charset="utf-8"><title>cards</title></head><body>
<button class="card" role="listitem" onclick="pick('one')"><span>Items go into the cart</span> <span>0:07</span> <span>C1</span></button>
<button class="card" role="listitem" onclick="pick('two')"><span>Items go into the cart</span> <span>0:08</span> <span>C2</span></button>
<button onclick="pick('first twin')">Open</button> <button onclick="pick('second twin')">Open</button>
<input placeholder="Search" oninput="document.getElementById('out').textContent = 'typed ' + this.value">
<button disabled>Sold out</button>
<button onclick="document.body.innerHTML = '<p>all new</p><button>Fresh</button>'">Rebuild</button>
<video id="v" src="/clip-one.webm" muted></video>
<p id="out">nothing yet</p>
<script>function pick(x) { document.getElementById('out').textContent = 'picked ' + x;
  if (x === 'two') document.getElementById('v').setAttribute('src', '/clip-two.webm'); }</script>
</body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        ok = self.path == "/"
        self.send_response(200 if ok else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8" if ok else "text/plain")
        self.end_headers()
        self.wfile.write(PAGE if ok else b"none")

    def log_message(self, *a):
        pass


class PageRefs(unittest.TestCase):
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
        self.cat = Catalog({"url": f"http://127.0.0.1:{self.httpd.server_address[1]}"}, Path(self.tmp.name) / "unit")
        self.cat.run("navigate", {"path": "/"})

    def tearDown(self):
        self.cat.close()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def tree(self):
        return self.cat.run("observe", {})["text"]

    def ref(self, pattern, nth=0):
        return re.findall(pattern + r"[^\n]*?\[ref=(\w+)\]", self.tree())[nth]

    def test_observe_keeps_what_it_showed_before_and_adds_a_reference_to_every_element(self):
        t = self.tree()
        self.assertIn('button "Sold out" [disabled]', t)
        self.assertIn("nothing yet", t)
        self.assertEqual(len(re.findall(r"- listitem \[ref=\w+\]", t)), 2)

    def test_a_control_with_no_name_of_its_own_can_be_clicked(self):
        res = self.cat.run("click", {"ref": self.ref(r"- listitem", 1)})
        self.assertEqual(res["text"], 'clicked listitem "Items go into the cart 0:08 C2"')   # the receipt says what it was
        self.assertIn("picked two", self.tree())

    def test_two_controls_with_the_same_name_can_be_told_apart(self):
        self.assertTrue(self.cat.run("click", {"role": "button", "name": "Open"})["isError"])   # by name: ambiguous
        self.cat.run("click", {"ref": self.ref(r'button "Open"', 1)})
        self.assertIn("picked second twin", self.tree())

    def test_a_field_with_only_a_placeholder_can_be_typed_into_and_the_count_is_reported(self):
        res = self.cat.run("fill", {"value": "brass  helmet", "ref": self.ref(r"- textbox")})
        self.assertFalse(res["isError"], res)
        self.assertIn("13 characters (11 not counting spaces)", res["text"])
        self.assertIn("typed brass helmet", self.tree())     # the tree shows runs of spaces as one

    def test_an_old_reference_fails_clearly_and_never_lands_on_another_element(self):
        old = self.ref(r"- listitem", 0)
        self.cat.run("click", {"role": "button", "name": "Rebuild"})
        res = self.cat.run("click", {"ref": old})
        self.assertTrue(res["isError"])
        self.assertIn("observe again", res["text"])
        self.assertIn("all new", self.tree())
        self.assertNotIn("picked", self.tree())

    def test_a_reference_is_never_a_selector(self):
        for bad in ("e1 >> css=button", "css=button", "//button", "e1]", "x" * 40, "E12"):
            res = self.cat.run("click", {"ref": bad})
            self.assertTrue(res["isError"] and res["text"].startswith("refused: "), (bad, res))
        for tool, args in (("fill", {"value": "x", "ref": "input"}), ("read_attribute", {"attribute": "id", "ref": "#out"})):
            self.assertTrue(self.cat.run(tool, args)["text"].startswith("refused: "))
        self.assertTrue(self.cat.run("click", {})["text"].startswith("refused: "))

    def test_read_attribute_by_reference(self):
        self.assertIn("placeholder = Search", self.cat.run("read_attribute", {"attribute": "placeholder", "ref": self.ref(r"- textbox")})["text"])

    def test_media_says_which_file_a_player_has_and_follows_a_change(self):
        before = self.cat.run("media", {})["text"]
        self.assertIn("video source /clip-one.webm", before)
        self.assertIn("not loaded", before)            # the file does not exist: the tool says so, it does not guess
        self.cat.run("click", {"ref": self.ref(r"- listitem", 1)})
        self.assertIn("video source /clip-two.webm", self.cat.run("media", {})["text"])
        self.cat.run("click", {"role": "button", "name": "Rebuild"})
        self.assertEqual(self.cat.run("media", {})["text"], "no video or audio element on this page")


if __name__ == "__main__":
    unittest.main()
