"""The film's clock: a step's `at` (seconds into the film, from the receipts' wall clock minus the film's `started`)
must point at the moment the film shows it, to within 0.2 s, and the film must run to the end of the case.

Measured by filming a small local page, then playing the saved film in a browser, seeking frame by frame, drawing
each frame on a canvas and reading one pixel."""
import base64
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from toolserver.catalog import Catalog

PAGE = b"""<!doctype html><html><head><meta charset="utf-8"></head>
<body style="margin:0;background:rgb(255,255,255)">
<p id="box" style="margin:0;position:absolute;left:200px;top:200px;width:300px;height:120px;background:rgb(240,240,240)">Cart total adds up</p><p>other</p>
</body></html>"""

SCAN = """async ({src, x, y, from, to, step, want}) => {
  const v = document.createElement('video'); v.muted = true; v.src = src; document.body.appendChild(v);
  await new Promise(r => v.readyState >= 1 ? r() : v.onloadedmetadata = r);
  if (!isFinite(v.duration)) { v.currentTime = 1e9; await new Promise(r => v.onseeked = r); }
  const c = document.createElement('canvas'); c.width = v.videoWidth; c.height = v.videoHeight;
  const ctx = c.getContext('2d');
  const px = async (t) => { v.currentTime = t; await new Promise(r => v.onseeked = r); ctx.drawImage(v, 0, 0);
                            return Array.from(ctx.getImageData(x, y, 1, 1).data.slice(0, 3)); };
  const match = ([r, g, b]) => want === 'green' ? (g > 120 && r < 110 && b < 130) : want === 'red' ? (r > 180 && g < 80 && b < 80) : want === 'mark' ? (r > 200 && g < 80 && b > 200) : false;
  let found = null;
  for (let t = Math.max(0, from); t <= Math.min(to, v.duration); t += step) {
    if (match(await px(t))) { found = t; break; }
  }
  return {duration: v.duration, found};
}"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *a):
        pass


class FilmClock(unittest.TestCase):
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
        self.cat = Catalog({"url": f"http://127.0.0.1:{self.httpd.server_address[1]}"}, self.dir, record=True)

    def tearDown(self):
        self.cat.close()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def started(self):
        """The film's start on the receipts' clock, as the run document reads it."""
        import json
        kept = json.loads((self.dir / "clip-1.json").read_text())
        self.assertTrue(kept["clock_from_mark"], kept)                 # set from the closing mark, not assumed
        return kept["started"]

    def test_a_film_that_starts_late_is_still_timed_right(self):
        """The recorder starts a film at its first frame. When that frame comes late (a busy machine), the closing mark
        still sets the film's clock: simulated here by a page made a second before the film really started."""
        self.cat.run("observe", {})
        self.cat.recording_started -= 1.0                              # as if the first frame came a second late
        time.sleep(1.0)
        t = time.time()
        self.cat.page().evaluate("document.body.style.background = 'rgb(230, 20, 20)'")
        time.sleep(0.5)
        self.cat.close()
        at = t - self.started()
        got = self.scan(50, 50, at, "red")
        self.assertIsNotNone(got["found"], got)
        self.assertLess(abs(got["found"] - at), 0.2, f"film {got['found']:.3f} s, at {at:.3f} s")

    def scan(self, x, y, around, want):
        """The first film time, within a second either side of `around`, at which pixel (x, y) shows `want`."""
        from playwright.sync_api import sync_playwright
        src = "data:video/webm;base64," + base64.b64encode((self.dir / "clip-1.webm").read_bytes()).decode()
        with sync_playwright() as pw:
            b = pw.chromium.launch()
            try:
                return b.new_page().evaluate(SCAN, {"src": src, "x": x, "y": y, "from": around - 1.0, "to": around + 1.0,
                                                    "step": 0.02, "want": want})
            finally:
                b.close()

    def test_a_change_at_a_known_moment_is_where_its_clock_says(self):
        self.cat.run("observe", {})
        time.sleep(1.5)
        t = time.time()
        self.cat.page().evaluate("document.body.style.background = 'rgb(230, 20, 20)'")
        time.sleep(1.0)
        self.cat.close()
        at = t - self.started()                                       # what the run document computes
        got = self.scan(50, 50, at, "red")
        self.assertIsNotNone(got["found"], got)
        self.assertLess(abs(got["found"] - at), 0.2, f"film {got['found']:.3f} s, clock {at:.3f} s")

    def test_a_comparison_outline_is_where_its_at_says_and_the_film_holds_to_the_end(self):
        ref = next(line for line in self.cat.run("observe", {})["text"].splitlines() if "Cart total" in line).split("[ref=")[1].split("]")[0]
        time.sleep(1.0)
        res = self.cat.run("compare", {"what": "Tests listed", "expected": "Cart total adds up, from the data", "found": "Cart total adds up",
                                       "matches": True, "ref": ref})
        held_until = time.time()
        self.cat.close()                                               # straight away: nothing changes after the hold
        at = res["data"]["t"] - self.started()                         # what the run document computes for the step
        held_until -= self.started()
        got = self.scan(197, 260, at, "green")                         # on the outline's left border (box at 200, 4 px out)
        self.assertIsNotNone(got["found"], got)
        self.assertLess(abs(got["found"] - at), 0.2, f"film {got['found']:.3f} s, at {at:.3f} s")
        self.assertGreaterEqual(got["duration"], held_until - 0.1, "the film ends before the comparison's hold did")
        end = self.scan(890, 690, got["duration"] - 0.05, "mark")     # the closing mark is gone before the film ends
        self.assertIsNotNone(self.scan(890, 690, got["duration"] - 1.0, "mark")["found"])
        self.assertIsNone(self.scan(890, 690, got["duration"] + 0.95, "mark")["found"], end)


if __name__ == "__main__":
    unittest.main()
