"""The dashboard pages, driven in Chromium against dashboard/dev_server.py and the sample run documents."""
import json
import re
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "dashboard" / "samples"
FILMED = "dashboard-one-property-filmed"

VERDICT = {"holds": "Holds", "fails": "Fails", "needs_decision": "Needs your decision", "cant_tell": "Can't tell",
           "planned": "Planned, not run yet", "not_run": "Not run in this run"}
OUTCOME = {"holds": "Held", "breaks": "Broke", "declined": "Needs your decision", "outside": "Outside the property's wording",
           "nothing_there": "Nothing there to test", "cant_run": "Could not run", "planned": "Planned, not run yet"}
OUTCOME_GLYPH = {"holds": "✓", "breaks": "✗", "declined": "?", "outside": "–", "nothing_there": "–", "cant_run": "!", "planned": "·"}
INTERNAL = ["cant_run", "needs_decision", "nothing_there", "not_run", "cant_tell"]

# Every button and link on the page, by the name a screen reader (or the verifier) would find it by.
NAMES = """() => [...document.querySelectorAll('button, a[href]')]
  .filter(e => e.offsetParent !== null || e.getClientRects().length)
  .map(e => (e.getAttribute('aria-label') || e.textContent).replace(/\\s+/g, ' ').trim())"""


def sentence(p, c):
    """A variation's sentence, as the pages choose it."""
    if (c.get("variation") or "").strip():
        return c["variation"].strip()
    v = next((v for v in p.get("variations") or [] if v.get("id") == c["id"]), None)
    return ((v or {}).get("what") or "").strip() or (c.get("stands_for") or "").strip() or (c.get("what") or "").strip()


def claim_name(p):
    return (p.get("title") or "").strip() or p["statement"]


def numbered(names):
    seen, out = {}, []
    for n in names:
        if names.count(n) < 2:
            out.append(n)
        else:
            seen[n] = seen.get(n, 0) + 1
            out.append(f"{n} ({seen[n]})")
    return out


def samples():
    return sorted(p.stem for p in SAMPLES.glob("*.json"))


def doc(name):
    return json.loads((SAMPLES / f"{name}.json").read_text())


class Dashboard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
            cls.pw = sync_playwright().start()
            try:
                cls.browser = cls.pw.chromium.launch()
            except Exception:
                cls.pw.stop()
                raise
        except Exception as e:  # noqa: BLE001
            raise unittest.SkipTest(f"Chromium cannot launch here: {e}")
        import sys
        sys.path.insert(0, str(ROOT / "dashboard"))
        from dev_server import make_server
        cls.server = make_server(0)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}/"

    def setUp(self):
        # Every test starts from the stored feedback the samples carry.
        from dev_server import Feedback
        self.server.RequestHandlerClass.feedback = Feedback(SAMPLES)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.server.server_close()

    def open(self, path, width=1440, height=1000, wait=".pcard"):
        page = self.browser.new_page(viewport={"width": width, "height": height})
        self.addCleanup(page.close)
        page.problems = []
        # The fonts are the one outside request; answer it here so the tests never need the network.
        page.route(re.compile(r"https://fonts\.(googleapis|gstatic)\.com/.*"),
                   lambda r: r.fulfill(status=200, content_type="text/css", body=""))
        missing_file = re.compile(r"/v1/runs/[^/]+/files/")

        def on_console(m):
            if m.type != "error":
                return
            if "Failed to load resource" in m.text and missing_file.search((m.location or {}).get("url", "")):
                return
            page.problems.append(f"console: {m.text}")

        def on_response(r):
            if r.status >= 400 and not (r.status == 404 and missing_file.search(r.url)):
                page.problems.append(f"{r.status} {r.url}")

        def on_failed(r):
            if not missing_file.search(r.url):
                page.problems.append(f"failed {r.url} {r.failure}")

        page.on("console", on_console)
        page.on("pageerror", lambda e: page.problems.append(f"page error: {e}"))
        page.on("response", on_response)
        page.on("requestfailed", on_failed)
        page.goto(self.base + path)
        if wait:
            page.wait_for_selector(wait)
        return page

    @staticmethod
    def expect(locator):
        from playwright.sync_api import expect
        return expect(locator)

    def assert_unique_names(self, page):
        names = page.evaluate(NAMES)
        dupes = sorted({n for n in names if names.count(n) > 1})
        self.assertEqual(dupes, [], "two controls share a name")
        self.assertNotIn("", names, "a control has no name")

    def assert_plain_words(self, page):
        text = page.inner_text("body")
        for word in INTERNAL:
            self.assertNotIn(word, text)

    # ---------- every sample ----------

    def test_every_sample_every_card_and_tile(self):
        for name in samples():
            with self.subTest(sample=name):
                d = doc(name)
                page = self.open(f"run.html?run={name}")
                self.assertEqual(page.locator(".pcard").count(), len(d["properties"]))
                self.assertEqual(page.locator("button[role], a[role]").count(), 0, "a control carries a role")
                card_names = numbered([claim_name(p) for p in d["properties"]])
                for p, card_name in zip(d["properties"], card_names):
                    card = page.get_by_role("button", name=card_name, exact=True)
                    card.click()
                    self.expect(card).to_have_attribute("aria-current", "true")
                    self.expect(page.locator("main .head h2")).to_have_text(claim_name(p))
                    self.assertEqual(page.locator("main .head .verdict-word").inner_text(), VERDICT[p["verdict"]])
                    self.assertEqual(page.locator("[aria-current=true].pcard").count(), 1)
                    cases = p.get("cases") or []
                    group = page.locator(f".pgroup[data-pid='{p['id']}']")
                    # Choosing a claim drops its variations down under it, one line each, in the claim's order.
                    if cases:
                        self.expect(group.locator(".vlist")).to_be_visible()
                        self.assertEqual(group.locator(".ptw").get_attribute("aria-expanded"), "true")
                    self.assertEqual(group.locator(".vline").count(), len(cases))
                    for i, (c, line_name) in enumerate(zip(cases, numbered([sentence(p, c) for c in cases]))):
                        line = group.locator(f".vline[data-cid='{c['id']}']")
                        self.assertEqual(line.get_attribute("aria-label"), line_name)
                        self.assertNotIn(c["id"].upper(), line.inner_text())
                        self.assertEqual(line.locator(".vn").inner_text(), f"V{i + 1}")
                        line.click()
                        self.expect(line).to_have_attribute("aria-current", "true")
                        self.expect(page.locator(".stagecol")).to_have_attribute("aria-label", f"Proof for the variation “{sentence(p, c)}”")
                        self.assertEqual(page.locator(".vline[aria-current=true]").count(), 1)
                        # The line above the proof says which variation it is, of how many, its sentence and how it came out.
                        head = page.locator(".stagecol > .vhead")
                        self.expect(head.locator(".vchip")).to_have_text(f"V{i + 1}")
                        self.assertEqual(head.locator(".vof").inner_text(), f"of {len(cases)}")
                        self.assertEqual(head.locator("p .glyph").inner_text(), OUTCOME_GLYPH[c["outcome"]])
                        self.assertEqual(head.locator("p").evaluate("p => p.lastChild.textContent"), sentence(p, c))
                        self.assertEqual(head.locator(".vout").inner_text(), OUTCOME[c["outcome"]])
                        # No side panel for a case; any outcome but held is explained once, under the proof.
                        self.assertEqual(page.locator(".body > aside.panel").count(), 0)
                        explained = page.locator(".stagecol > .outcome-box, .stagecol > .box.warn, .stagecol .sheet")
                        if c["outcome"] == "holds":
                            self.assertEqual(page.locator(".outcome-box, .stagecol > .box.warn").count(), 0)
                        else:
                            text = page.locator(".stagecol").inner_text()
                            if c["outcome"] == "declined":
                                self.assertIn("Is this intended?", text)
                            elif c["outcome"] != "planned":
                                self.assertIn(c["observed"].split("\n")[0][:60], text)
                            self.assertGreater(explained.count(), 0)
                        self.assertIn(f"#{p['id']}/{c['id']}", page.url)
                        self.assert_unique_names(page)
                    self.assert_plain_words(page)
                self.assertEqual(page.problems, [])

    def test_narrow_screen(self):
        for name in samples():
            with self.subTest(sample=name):
                page = self.open(f"run.html?run={name}", width=390, height=844)
                page.wait_for_timeout(300)
                width = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
                self.assertLessEqual(width[0], width[1], "the page scrolls sideways")
                side = page.locator("aside.side").bounding_box()
                main = page.locator("main.main").bounding_box()
                self.assertLessEqual(side["y"] + side["height"], main["y"] + 1, "the sidebar is not above the main area")
                self.assertEqual(page.problems, [])
        for view in ["lipsync-agent-version#g3/c2", "shop-cart#p2/a1c1", "shop-copy-three-places#p1",
                     "lipsync-agent-version#g1/c1", "shop-cart#p0/c4"]:
            with self.subTest(view=view):
                page = self.open(f"run.html?run={view}", width=390, height=844)
                page.wait_for_timeout(300)
                width = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
                self.assertLessEqual(width[0], width[1], "the page scrolls sideways")
        page = self.open("index.html", width=390, height=844, wait=".frow")
        for b in page.locator(".runs-btn").all():
            b.click()
        self.assertLessEqual(*page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]"))

    # ---------- the list: one row per feature, its runs under it ----------
    # From the samples: review-page has three runs (the newest rechecks two of its three claims), lip-sync two (g1
    # broke in the older and holds in the newer), and the two carts one each.

    LIST_WORDS = re.compile(r"\bpropert|\bcases?\b|not run|were not|was not", re.I)

    def rows(self, page):
        return page.evaluate("""() => [...document.querySelectorAll('.frow')].map(r => ({
          name: r.querySelector('a.fname').firstChild.textContent, href: r.querySelector('a.fname').getAttribute('href'),
          mark: r.querySelector('.glyph').textContent, tally: r.querySelector('.tally').firstChild.textContent + r.querySelector('.tally').childNodes[1].textContent,
          checked: r.querySelector('.tally span').textContent,
          facts: [...r.querySelectorAll('.facts > span:not(.runs-n)')].map(s => s.textContent), runs: r.querySelector('.runs-n').textContent }))""")

    def history(self, page, name):
        li = page.locator("li.feature", has=page.locator("a.fname", has_text=name))
        # What a run covered is in the link's name (after when it ran), for a screen reader; the line itself shows a tally.
        return [tuple(x) for x in li.locator(".runline").evaluate_all(
            "ls => ls.map(a => [a.getAttribute('href'), a.getAttribute('aria-label').split(': ').slice(1).join(': '), a.querySelector('.when').textContent])")]

    def tallies(self, page, name):
        """Each run line as it is seen: its mark, how many claims hold, and what it left out ("" when nothing)."""
        li = page.locator("li.feature", has=page.locator("a.fname", has_text=name))
        return [tuple(x) for x in li.locator(".runline").evaluate_all("""ls => ls.map(a => {
          const t = a.querySelector('.rtally'), left = t.querySelector('.rleft');
          return [a.querySelector('.glyph').textContent, [...t.childNodes].filter(n => n !== left).map(n => n.textContent).join(''), left ? left.textContent : ''];
        })""")]

    def test_runs_list(self):
        page = self.open("index.html", wait=".frow")
        rows = self.rows(page)
        # One row per feature, newest checked first.
        self.assertEqual([r["name"] for r in rows], ["groundtruth-cli · review-page", "lip-sync-demo · lip-sync", "shop-copy · cart", "shop · cart"])
        review, lipsync, copy, shop = rows
        self.assertEqual(review["href"], "feature.html?project=groundtruth-cli&feature=review-page")
        # The current state is each claim's newest result: p1 and p2 from the newest run, p3 from the one before.
        self.assertEqual((review["mark"], review["tally"], review["facts"], review["runs"]),
                         ("✗", "2 of 3 claims hold", ["1 fails", "10 variations tried"], "3 runs"))
        self.assertTrue(review["checked"].startswith("last checked Nov 11, 2026"), review["checked"])
        # A claim that broke in an older run and holds in a newer one holds.
        self.assertEqual((lipsync["mark"], lipsync["tally"], lipsync["facts"], lipsync["runs"]), ("✓", "3 of 3 claims hold", ["16 variations tried"], "2 runs"))
        self.assertEqual((shop["mark"], shop["tally"], shop["facts"], shop["runs"]), ("?", "1 of 3 claims holds", ["2 need your answer", "17 variations tried"], "1 run"))
        self.assertEqual((copy["tally"], copy["facts"]), ("0 of 1 claim holds", ["1 fails", "7 variations tried"]))
        self.assertEqual(page.locator(".history:not([hidden])").count(), 0)
        # The runs, newest first, each opening its run and saying what it covered.
        for name in ("groundtruth-cli · review-page", "shop · cart"):
            page.get_by_role("button", name=re.compile(r"runs? of " + re.escape(name) + "$")).click()
        self.assertEqual([(h, c) for h, c, _ in self.history(page, "groundtruth-cli · review-page")], [
            ("run.html?run=review-page-claims", "checked 2 of 3 claims: 1 holds, 1 fails"),
            (f"run.html?run={FILMED}", "checked 1 of 3 claims: 1 holds"),
            ("run.html?run=dashboard-three-properties", "checked all 3 claims: 1 holds, 1 fails, 1 can't be told")])
        # Each run line is a small copy of the feature's row: one mark, when it ran, how many of the claims it checked hold.
        self.assertEqual(self.tallies(page, "groundtruth-cli · review-page"), [
            ("✗", "1 of 2 claims holds", "only 2 of 3 checked"), ("✓", "1 of 1 claim holds", "only 1 of 3 checked"), ("✗", "1 of 3 claims holds", "")])
        self.assertEqual(self.tallies(page, "shop · cart"), [("?", "1 of 3 claims holds", "")])
        when = self.history(page, "groundtruth-cli · review-page")[0][2]
        self.assertIn("Nov 11, 2026", when)
        self.assertIn("commit 721de9b", when)
        self.assertEqual([c for _, c, _ in self.history(page, "shop · cart")], ["checked all 3 claims: 1 holds, 2 need your answer"])
        self.assertIsNone(self.LIST_WORDS.search(page.inner_text("body")))
        self.assert_plain_words(page)
        self.assert_unique_names(page)
        self.assertEqual(page.problems, [])
        # Clicking the row away from its name opens the feature's page, which says the same.
        row = page.locator(".frow", has=page.locator("a.fname", has_text="groundtruth-cli · review-page")).bounding_box()
        page.mouse.click(row["x"] + row["width"] * 0.6, row["y"] + 10)
        # This feature was filmed in use, so its page is the film, with the result beside its name; the summary of
        # claims is one link further and gives the same numbers.
        page.wait_for_selector(".fbar .fchip")
        self.assertTrue(page.url.endswith(review["href"]), page.url)
        self.assertEqual(page.locator(".fbar .fchip").inner_text(), "2 hold, 1 fails")
        page.locator("a.fdetail").click()
        page.wait_for_selector(".claim")
        self.assertEqual(page.locator(".fverdict").inner_text(), "2 hold, 1 fails.")
        self.assertIn("10 variations tried", page.locator(".fmeta").inner_text())

    def test_list_runs_button(self):
        # The arrow at the row's left opens and closes the history in place, by mouse or keyboard, and never follows the row's link.
        page = self.open("index.html", wait=".frow")
        start = page.url
        link = page.locator("a.fname", has_text="lip-sync-demo · lip-sync")
        btn = page.get_by_role("button", name="2 runs of lip-sync-demo · lip-sync", exact=True)
        hist = page.locator("#" + btn.get_attribute("aria-controls"))
        # It is at the row's left edge, before the name, and is the stop before the row's link.
        self.assertLess(btn.bounding_box()["x"], link.bounding_box()["x"])
        self.assertEqual(btn.inner_text().strip(), "")
        btn.focus()
        page.keyboard.press("Tab")
        self.assertTrue(link.evaluate("a => a === document.activeElement"), "the row's link is the next stop after the arrow")
        btn.focus()
        page.keyboard.press("Enter")
        self.expect(btn).to_have_attribute("aria-expanded", "true")
        self.assertTrue(hist.is_visible())
        self.assertEqual([c for _, c, _ in self.history(page, "lip-sync-demo · lip-sync")],
                         ["checked all 3 claims: 3 hold", "checked all 3 claims: 2 hold, 1 fails"])
        page.keyboard.press("Space")
        self.expect(btn).to_have_attribute("aria-expanded", "false")
        self.assertFalse(hist.is_visible())
        btn.click()
        self.expect(btn).to_have_attribute("aria-expanded", "true")
        page.wait_for_timeout(200)
        self.assertEqual(page.url, start)
        # One run still shows "1 run" and opens.
        one = page.get_by_role("button", name="1 run of shop-copy · cart", exact=True)
        one.click()
        self.assertEqual([h for h, _, _ in self.history(page, "shop-copy · cart")], ["run.html?run=shop-copy-three-places"])
        page.locator("li.feature", has=page.locator("a.fname", has_text="shop-copy · cart")).locator(".runline").click()
        page.wait_for_selector(".pcard")
        self.assertTrue(page.url.endswith("run.html?run=shop-copy-three-places"))

    def test_list_one_project(self):
        page = self.open("index.html?project=shop", wait=".frow")
        self.assertEqual([r["name"] for r in self.rows(page)], ["shop · cart"])

    def open_list(self, runs, width=1440):
        """The list page with the store's run summaries answered by these made-up ones."""
        body = json.dumps({"runs": runs})
        page = self.browser.new_page(viewport={"width": width, "height": 1000})
        self.addCleanup(page.close)
        page.problems = []
        page.on("pageerror", lambda e: page.problems.append(f"page error: {e}"))
        page.on("console", lambda m: page.problems.append(f"console: {m.text}") if m.type == "error" else None)
        page.route(re.compile(r"https://fonts\.(googleapis|gstatic)\.com/.*"), lambda r: r.fulfill(status=200, content_type="text/css", body=""))
        page.route(re.compile(r".*/v1/runs\?.*"), lambda r: r.fulfill(status=200, content_type="application/json", body=body))
        page.goto(self.base + "index.html")
        page.wait_for_selector(".frow")
        return page

    @staticmethod
    def summary(rid, feature, ran_at, claims, project="app"):
        """A run summary as GET /v1/runs gives it; claims: [(id, verdict, variations tried)]."""
        return {"id": rid, "project": project, "feature": feature, "run": rid, "ran_at": ran_at, "uploaded_at": ran_at,
                "complete": True, "properties": {}, "cases": {}, "commit": "abc1234",
                "claims": [{"id": c, "title": f"Claim {c}", "verdict": v, "variations": n} for c, v, n in claims]}

    # Shaped like the real sign-in feature: six claims, three hold and three can't be told; then c2, c3 and c5 are
    # rechecked and c2 and c5 now hold.
    SIGN_IN_FIRST = [("c1", "holds", 6), ("c2", "cant_tell", 5), ("c3", "cant_tell", 5), ("c5", "cant_tell", 3), ("c6", "holds", 2), ("c7", "holds", 3)]
    SIGN_IN_RECHECK = [("c1", "not_run", 0), ("c2", "holds", 5), ("c3", "cant_tell", 5), ("c5", "holds", 3), ("c6", "not_run", 0), ("c7", "not_run", 0)]

    def test_list_folds_a_recheck_into_one_row(self):
        S = self.summary
        runs = [   # newest first, as the store answers
            S("never", "never-checked", "2026-11-15T13:00:00", [("n1", "planned", 0), ("n2", "planned", 0)], project="other"),
            S("tour", "tour-only", "2026-11-15T12:00:00", [("t1", "not_run", 0)]),       # a walkthrough only: no claim checked
            S("recheck", "sign-in", "2026-11-15T11:08:49", self.SIGN_IN_RECHECK),
            S("first", "sign-in", "2026-11-15T10:59:26", self.SIGN_IN_FIRST),
            S("tested", "tour-only", "2026-11-14T09:00:00", [("t1", "holds", 4)]),
            S("fixed", "fixed", "2026-11-13T09:00:00", [("f1", "holds", 2)]),
            S("broke", "fixed", "2026-11-13T08:00:00", [("f1", "fails", 3)]),
        ]
        for width in (1440, 390):
            page = self.open_list(runs, width)
            rows = self.rows(page)
            # Ordered by when a claim was last checked, not by the newest run: tour-only's newest run checked nothing.
            self.assertEqual([r["name"] for r in rows], ["app · sign-in", "app · tour-only", "app · fixed", "other · never-checked"])
            sign_in, tour, fixed, never = rows
            # The two sign-in runs are one row with the true state; the recheck does not make c1, c6 or c7 "never run".
            self.assertEqual((sign_in["mark"], sign_in["tally"], sign_in["facts"], sign_in["runs"], sign_in["checked"]),
                             ("!", "5 of 6 claims hold", ["1 can't be told", "24 variations tried"], "2 runs", "last checked Nov 15, 2026, 11:08 AM"))
            self.assertEqual(sign_in["href"], "feature.html?project=app&feature=sign-in")
            self.assertEqual((tour["tally"], tour["facts"], tour["checked"]), ("1 of 1 claim holds", ["4 variations tried"], "last checked Nov 14, 2026, 9:00 AM"))
            self.assertEqual((fixed["mark"], fixed["tally"], fixed["facts"]), ("✓", "1 of 1 claim holds", ["2 variations tried"]))
            self.assertEqual((never["mark"], never["tally"], never["facts"], never["checked"], never["runs"]),
                             ("–", "0 of 2 claims hold", ["2 never run"], "not checked yet", "1 run"))
            for b in page.locator(".runs-btn").all():
                b.click()
            self.assertEqual(self.history(page, "app · sign-in"), [
                ("run.html?run=recheck", "checked 3 of 6 claims: 2 hold, 1 can't be told", "Nov 15, 2026, 11:08 AMcommit abc1234"),
                ("run.html?run=first", "checked all 6 claims: 3 hold, 3 can't be told", "Nov 15, 2026, 10:59 AMcommit abc1234")])
            self.assertEqual([c for _, c, _ in self.history(page, "app · tour-only")], ["checked no claims", "checked its one claim: it holds"])
            self.assertEqual([c for _, c, _ in self.history(page, "app · fixed")], ["checked its one claim: it holds", "checked its one claim: it fails"])
            self.assertEqual(self.tallies(page, "app · sign-in"), [("!", "2 of 3 claims hold", "only 3 of 6 checked"), ("!", "3 of 6 claims hold", "")])
            self.assertEqual(self.tallies(page, "app · tour-only"), [("–", "no claims checked", ""), ("✓", "1 of 1 claim holds", "")])
            self.assertEqual(self.tallies(page, "app · fixed"), [("✓", "1 of 1 claim holds", ""), ("✗", "0 of 1 claim holds", "")])
            self.assertIsNone(self.LIST_WORDS.search(page.inner_text("body")))
            self.assert_unique_names(page)
            self.assertLessEqual(*page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]"))
            self.assertEqual(page.problems, [])
            # A screen reader hears the whole row in the link's name.
            self.expect(page.get_by_role("link", name="app · sign-in: 5 of 6 claims hold, 1 can't be told, 24 variations tried, "
                                                       "last checked Nov 15, 2026, 11:08 AM", exact=True)).to_have_count(1)

    def test_list_rule_is_the_feature_pages(self):
        # The list folds run summaries with the feature page's own mergeResults; these are the edges of that rule.
        page = self.open("index.html", wait=".frow")
        got = page.evaluate("""async () => {
          const s = await import('/js/summary.js');
          const S = (id, at, claims, feature = 'f') => ({ id, project: 'p', feature, ran_at: at,
            claims: claims.map(([cid, verdict, variations]) => ({ id: cid, title: '', verdict, variations })) });
          const fold = (runs) => s.foldFeatures(runs)[0];
          const said = (f) => [s.holdLine(f.results), ...s.openWords(f.results), s.variationCount(f.results)];
          // A claim the newest run no longer has is dropped, as on the feature page; one it added and never ran is "never run".
          const removed = fold([S('b', '2026-11-02T00:00', [['a', 'holds', 1], ['new', 'not_run', 0]]),
                                S('a', '2026-11-01T00:00', [['a', 'fails', 2], ['gone', 'fails', 2]])]);
          // Only the newest RUNS_READ runs are read, as on the feature page: a result older than that is not used.
          const many = [];
          for (let i = 0; i < s.RUNS_READ; i++) many.push(S(`r${i}`, `2026-11-${String(20 - i).padStart(2, '0')}T00:00`, [['x', 'holds', 1], ['old', 'not_run', 0]]));
          many.push(S('oldest', '2026-11-01T00:00', [['x', 'holds', 1], ['old', 'holds', 7]]));
          const deep = fold(many);
          // A verdict this page has no words for still counts, as "with no result".
          const odd = fold([S('o', '2026-11-01T00:00', [['a', 'exploded', 1], ['b', 'holds', 1]])]);
          return { removed: said(removed), from: removed.results.map(r => r.run && r.run.id),
                   deep: said(deep), runs: deep.runs.length, odd: said(odd), oddRun: s.runCovered(S('o', '', [['a', 'exploded', 1], ['b', 'holds', 1]])),
                   empty: [s.holdLine([]), s.runCovered({ claims: [] }), s.runCovered({})] };
        }""")
        self.assertEqual(got["removed"], ["1 of 2 claims holds", "1 never run", 1])
        self.assertEqual(got["from"], ["b", None])
        self.assertEqual(got["deep"], ["1 of 2 claims holds", "1 never run", 1])
        self.assertEqual(got["runs"], 11)
        self.assertEqual(got["odd"], ["1 of 2 claims holds", "1 with no result", 2])
        self.assertEqual(got["oddRun"], "checked all 2 claims: 1 holds, 1 with no result")
        self.assertEqual(got["empty"], ["No claims", "checked no claims", "checked no claims"])

    def test_empty_store(self):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.route(re.compile(r".*/v1/runs\?.*"), lambda r: r.fulfill(status=200, content_type="application/json", body='{"runs": []}'))
        page.goto(self.base + "index.html")
        page.wait_for_selector("text=No runs have been uploaded yet.")

    def test_run_that_cannot_be_opened(self):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.goto(self.base + "run.html?run=no-such-run")
        page.wait_for_selector("text=There is no run with that id.")

    # ---------- the film ----------

    def test_case_has_no_side_panel(self):
        # A filmed case opens as its film alone, with no side panel; with its steps (&steps=1) it is the film with a
        # living storyboard: the film on the left, the rail, then the stage.
        page = self.open(f"run.html?run={FILMED}#p3/c2")
        page.wait_for_selector(".sb.captions .film video")
        self.assertEqual(page.locator(".panel").count(), 0)
        self.assertEqual(page.locator(".cap, .capbody, .compared").count(), 0)
        page = self.open(f"run.html?run={FILMED}&steps=1#p3/c2")
        page.wait_for_selector(".sb .film video")
        self.assertEqual(page.locator(".panel").count(), 0)
        self.assertEqual(page.locator(".cap, .capbody, .compared").count(), 0)
        film, rail, stage = (page.locator(f".sb {x}").bounding_box() for x in (".film", ".sb-rail", ".sb-stage"))
        self.assertLess(film["x"] + film["width"], rail["x"] + 1)
        self.assertLess(rail["x"] + rail["width"], stage["x"] + 1)

    def test_missing_film(self):
        page = self.open("run.html?run=shop-cart#p0/c1")
        page.wait_for_selector("text=The film of this case is not in the store")
        self.assertTrue(page.get_by_role("button", name="Play the film", exact=True).is_disabled())
        self.assertTrue(page.locator(".track input").is_disabled())
        # Its line in the left column gives no length for a film that cannot be shown.
        self.expect(page.locator(".pgroup[data-pid=p0] .vline[data-cid=c1] .dur")).to_have_text("no film")
        self.assertTrue(page.locator(".sb-list").is_visible(), "without the film, its steps are shown as a list")

    def test_screenshot_instead_of_film(self):
        page = self.open("run.html?run=dashboard-three-properties#p1/c1")
        page.wait_for_selector("text=No film was recorded for this case")
        self.assertIn("p1/c1/shot-1.png", page.locator(".film img").get_attribute("src"))

    # ---------- each kind of proof ----------

    def test_not_run_cards(self):
        page = self.open(f"run.html?run={FILMED}")
        d = doc(FILMED)
        for p in d["properties"]:
            if p["verdict"] != "not_run":
                continue
            card = page.get_by_role("button", name=claim_name(p), exact=True)
            group = page.locator(f".pgroup[data-pid={p['id']}]")
            self.assertEqual(group.locator(".notrun-line").inner_text(), "Not run in this run")
            # It has no variations to drop down.
            self.assertTrue(group.locator(".ptw").is_disabled())
            card.click()
            self.expect(page.locator("main")).to_contain_text("this run did not test it")
            self.assertEqual(group.locator(".vline").count(), 0)
            self.assertEqual(page.locator("main .vhead, main .sb").count(), 0)

    def test_regression_empty(self):
        page = self.open("run.html?run=shop-cart")
        new = page.get_by_role("button", name="New, 3", exact=True)
        reg = page.get_by_role("button", name="Regression, 0", exact=True)
        self.assertEqual(new.get_attribute("aria-pressed"), "true")
        reg.click()
        self.expect(reg).to_have_attribute("aria-pressed", "true")
        page.wait_for_selector("text=No earlier properties were re-checked in this run.")
        self.assertEqual(page.locator(".pcard").count(), 0)
        new.click()
        self.assertEqual(page.locator(".pcard").count(), 3)

    def test_places(self):
        page = self.open("run.html?run=shop-copy-three-places#p1/c1")
        page.get_by_role("button", name="Tested in 3 places", exact=True).click()
        self.expect(page.get_by_role("button", name="Tested in 3 places")).to_have_attribute("aria-pressed", "true")
        self.assertTrue(page.url.endswith("#p1"))
        self.assertEqual(page.locator(".place").count(), 3)
        text = page.locator(".stagecol").inner_text()
        for words in ["Found by the search", "Flagged: builds its request address from parts", "“Basic add from the also-bought suggestions raises subtotal by the displayed price” broke.",
                      "Not tried: POST /api/cart", "These ways in came from the code search."]:
            self.assertIn(words, text)
        self.assertEqual(page.locator(".place.broke").count(), 1)
        self.assert_unique_names(page)
        page.get_by_role("button", name="Watch the film of this case").click()
        self.expect(page.locator(".vline[aria-current=true]")).to_have_attribute("data-cid", "c3")
        self.assertTrue(page.url.endswith("#p1/c3"))
        page2 = self.open("run.html?run=lipsync-agent-version#g3")
        self.assertIn("came from a model reading the code", page2.locator(".stagecol").inner_text())
        self.assertNotIn("Found by the search", page2.locator(".stagecol").inner_text())
        self.assertIn("Files left out", self.open("run.html?run=lipsync-agent-version#g1").locator(".stagecol").inner_text())

    def open_patched(self, name, change, hash_, width=1440):
        d = doc(name)
        change(d)
        body = json.dumps({"id": "patched", "complete": True, "files_base": f"/v1/runs/{name}/files/", "document": d})
        page = self.browser.new_page(viewport={"width": width, "height": 1000})
        self.addCleanup(page.close)
        page.problems = []
        page.on("pageerror", lambda e: page.problems.append(f"page error: {e}"))
        page.route(re.compile(r"https://fonts\.(googleapis|gstatic)\.com/.*"), lambda r: r.fulfill(status=200, content_type="text/css", body=""))
        page.route(re.compile(r".*/v1/runs/patched$"), lambda r: r.fulfill(status=200, content_type="application/json", body=body))
        page.goto(self.base + f"run.html?run=patched{hash_}")
        page.wait_for_selector(".pcard")
        return page

    def test_property_from_claim_has_no_places(self):
        # A property planned from approved variations read no code, so it never shows where it was "found":
        # opening it with no variation in the address shows its default variation, and the address stays as typed.
        def g3(d):
            return next(p for p in d["properties"] if p["id"] == "g3")

        def marked(d):
            g3(d)["from_claim"] = True

        def older(d):                     # stored before from_claim existed: no code search, and a checked-against sentence
            g3(d)["checked_against"] = "The models the app loads at start."

        def two_ways(d):
            p = g3(d)
            p["from_claim"] = True
            p["searched"] = True          # even with a search recorded, from_claim wins
        for change in (marked, older, two_ways):
            for width in (1440, 390):
                page = self.open_patched("lipsync-agent-version", change, "#g3", width)
                self.expect(page.locator(".vline[aria-current=true]")).to_have_count(1)
                d = doc("lipsync-agent-version")
                cases = g3(d)["cases"]
                want = next((c for c in cases if c["outcome"] == "breaks"), None) or next((c for c in cases if c["outcome"] == "declined"), None) or cases[0]
                self.assertEqual(page.locator(".vline[aria-current=true]").get_attribute("data-cid"), want["id"])
                self.assertTrue(page.url.endswith("#g3"), page.url)
                text = page.locator("main").inner_text()
                for gone in ["Where a person can do this", "reading the code, not from", "Found by reading the code", "The first case"]:
                    self.assertNotIn(gone, text)
                self.assertEqual(page.locator(".place").count(), 0)
                self.assertEqual(page.locator(".places-btn").count(), 0)
                self.assertEqual(page.problems, [])
                # The same proof as clicking that variation's line in the left column.
                shown = page.locator(".stagecol").inner_text()
                page.locator(f".pgroup[data-pid=g3] .vline[data-cid={want['id']}]").click()
                self.expect(page.locator(".vline[aria-current=true]")).to_have_attribute("data-cid", want["id"])
                self.assertEqual(page.locator(".stagecol").inner_text(), shown)

        def not_from_claim(d):            # from_claim said false: the places view stays, as for any other property
            p = g3(d)
            p["from_claim"] = False
            p["checked_against"] = "The models the app loads at start."
        page = self.open_patched("lipsync-agent-version", not_from_claim, "#g3")
        page.wait_for_selector(".place")
        self.assertIn("Where a person can do this", page.locator("main").inner_text())

    # ---------- the feature summary ----------
    # review-page-claims (newer, with the new fields) and dashboard-one-property-filmed (older) are two runs of the
    # same feature: p1 and p2 come from the newer run, p3 from the older one, which last tested it.

    SUMMARY = "feature.html?project=groundtruth-cli&feature=review-page&view=claims"     # the summary of claims
    FILM = "feature.html?project=groundtruth-cli&feature=review-page"                       # the film-first page

    def test_summary_merges_runs(self):
        page = self.open(self.SUMMARY, wait=".claim")
        newer, older = doc("review-page-claims"), doc(FILMED)
        self.assertEqual(page.locator(".fverdict").inner_text(), "2 hold, 1 fails.")
        meta = page.locator(".fmeta").inner_text()
        self.assertIn("10 variations tried", meta)
        self.assertIn("Results are from 2 runs; each claim shows when it was last tested.", meta)
        self.assertEqual(page.locator("h1.goal").inner_text(), newer["goal"])
        self.assertEqual(page.locator(".fname").inner_text(), "review-page · groundtruth-cli")
        self.expect(page.get_by_role("button", name="Watch all 9 films")).to_have_count(1)
        claims = page.locator(".claim")
        self.assertEqual(claims.count(), 3)
        p1, p2, p3 = claims.nth(0), claims.nth(1), claims.nth(2)
        # The owner's words first, then the title with its verdict, then the sentence.
        self.assertEqual(p1.locator(".said").inner_text(), f"“{newer['properties'][0]['your_words']}”")
        self.assertIn("Claims each get a card", p1.locator("h2").inner_text())
        self.assertIn("Holds", p1.locator("h2").inner_text())
        self.assertEqual(p2.locator(".said").count(), 0)
        self.assertIn("Fails", p2.locator("h2").inner_text())
        # Only the claim whose result is older than the newest one used says when it was tested.
        self.assertEqual(p1.locator(".cwhen").count(), 0)
        self.assertIn("Tested Nov 10, 2026", p3.locator(".cwhen").inner_text())
        self.assertEqual(p3.locator(".vrow").count(), len(older["properties"][2]["cases"]))
        # Where each row's sentence comes from: the case's own sentence, else the property's approved variation,
        # else what the case stands for. An approved variation with no case is a row that cannot be opened.
        rows = p1.locator(".vrow")
        self.assertEqual([rows.nth(i).locator(".vs").inner_text() for i in range(3)],
                         ["A feature with one claim", "A feature with three claims, one of them changed after approval",
                          "A feature with no claims at all"])
        self.assertEqual(rows.nth(2).locator("button").count(), 0)
        self.assertIn("This variation was not run.", rows.nth(2).inner_text())
        r2 = p2.locator(".vrow")
        self.assertEqual(r2.nth(2).locator(".vs").inner_text(), "A regression saved before the change")
        self.assertEqual(r2.nth(0).locator(".why").inner_text(), "The Regression section was empty although the run recorded one regression.")
        self.assertEqual(r2.nth(1).locator(".why").inner_text(), "The page hides regressions older than a week. Is that intended?")
        self.assertIn("It is outside the claim's wording: This is about the list of runs", r2.nth(2).locator(".why").inner_text())
        self.assertEqual(p1.locator(".why").count(), 1)          # only the row that was not run
        shown = page.locator(".vs").all_inner_texts()
        for c in sum((p.get("cases") or [] for p in newer["properties"] + older["properties"]), []):
            self.assertFalse(any(c["id"].upper() in t or c["id"] == t for t in shown), c["id"])
        # Opening a row: its film, what it starts from, and a link to the run it came from. One open at a time.
        rows.nth(0).locator("button.vhead").click()
        opened = page.locator(".vopen:not([hidden])")
        self.expect(opened).to_have_count(1)
        self.assertEqual(opened.locator(".sb video").count(), 1)
        self.assertIn("Starts from: One feature with a single approved claim.", opened.inner_text())
        # Before play, the stage shows the first step: the data read, as a command and its real answer.
        self.assertIn("$ make-feature --claims 1 --approved true", opened.locator(".sb-now .term").inner_text())
        self.assertIn("feature: login", opened.locator(".sb-now .term").inner_text())
        self.assertEqual(opened.get_by_role("link", name="Open the full details").get_attribute("href"),
                         "run.html?run=review-page-claims#p1/one-claim")
        p3.locator("button.vhead").first.click()
        self.expect(page.locator(".vopen:not([hidden])")).to_have_count(1)
        self.assertEqual(rows.nth(0).locator("button").get_attribute("aria-expanded"), "false")
        self.assertEqual(page.locator(".vopen:not([hidden])").get_by_role("link", name="Open the full details").get_attribute("href"),
                         f"run.html?run={FILMED}#p3/c1")
        page.wait_for_function("document.querySelector('.vopen:not([hidden]) video').readyState >= 1")
        p3.locator("button.vhead").first.click()
        self.expect(page.locator(".vopen:not([hidden])")).to_have_count(0)
        # A row with no film opens to its steps as a list, and what was seen.
        r2.nth(1).locator("button").click()
        self.assertEqual(page.locator(".vopen:not([hidden]) .sb.nofilm .sb-list").count(), 1)
        self.assertIn("The page left out the regression from last week.", page.locator(".vopen:not([hidden]) .seen").inner_text())
        nc = page.locator(".notchecked")
        self.assertIn("No claim covers these, so nothing was verified about them.", nc.inner_text())
        self.assertEqual(nc.locator("li").all_inner_texts(), newer["not_checked"])
        self.assertEqual(page.locator("[data-later=walkthrough]").count(), 0)
        self.assert_unique_names(page)
        self.assert_plain_words(page)
        self.assertEqual(page.problems, [])
        # Every link back: the mark goes to the list of runs, and the run page links to this summary.
        self.assertEqual(page.locator(".brandlink").get_attribute("href"), "index.html")
        run = self.open(f"run.html?run={FILMED}")
        link = run.get_by_role("link", name="Summary of review-page")
        self.assertEqual(link.get_attribute("href"), "feature.html?project=groundtruth-cli&feature=review-page")
        side = run.locator("aside.side")
        self.assertLess(link.bounding_box()["y"], side.locator(".switch").bounding_box()["y"])

    def test_summary_merge_rule(self):
        page = self.open(self.SUMMARY, wait=".claim")
        got = page.evaluate("""async () => {
          const { mergeResults, verdictLine } = await import('/js/summary.js');
          const doc = (props) => ({ document: { ran_at: '', properties: props } });
          const P = (id, verdict, extra) => Object.assign({ id, verdict, cases: [] }, extra || {});
          const runs = [
            Object.assign(doc([P('a', 'not_run'), P('b', 'planned'), P('c', 'holds', { mark: 'new' })]), { id: 'r3' }),
            Object.assign(doc([P('a', 'fails'), P('b', 'not_run'), P('c', 'fails'), P('z', 'holds')]), { id: 'r2' }),
            Object.assign(doc([P('a', 'holds'), P('b', 'needs_decision')]), { id: 'r1' }),
          ];
          const rs = mergeResults(runs);
          return { ids: rs.map(r => r.id), from: rs.map(r => r.run && r.run.id), verdicts: rs.map(r => r.p.verdict),
                   mark: rs[2].p.mark, line: verdictLine(rs),
                   none: mergeResults([runs[0]]).map(r => r.run && r.run.id), allHold: verdictLine([{ run: 1, p: { verdict: 'holds' } }, { run: 1, p: { verdict: 'holds' } }]) };
        }""")
        self.assertEqual(got["ids"], ["a", "b", "c"])           # the newest run's properties, in its order
        self.assertEqual(got["from"], ["r2", "r1", "r3"])        # each from the newest run that tested it
        self.assertEqual(got["verdicts"], ["fails", "needs_decision", "holds"])
        self.assertEqual(got["mark"], "new")
        self.assertEqual(got["line"], "1 holds, 1 fails, 1 needs your decision.")
        self.assertEqual(got["none"], [None, None, "r3"])
        self.assertEqual(got["allHold"], "All 2 claims hold.")

    def test_summary_reads_only_what_it_needs(self):
        # Fetches stop once every claim has a result: here the newer run lacks p3, so both runs are read, no more.
        page = self.browser.new_page()
        self.addCleanup(page.close)
        seen = []
        page.on("request", lambda r: seen.append(r.url))
        page.goto(self.base + self.SUMMARY)
        page.wait_for_selector(".claim")
        reads = [u for u in seen if re.search(r"/v1/runs/[^/?]+$", u)]
        self.assertEqual([u.rsplit("/", 1)[1] for u in reads], ["review-page-claims", FILMED])
        self.assertTrue(any("project=groundtruth-cli&feature=review-page&limit=20" in u for u in seen))

    def test_summary_states(self):
        page = self.open("feature.html?project=nobody&feature=nothing", wait=".fstate")
        self.expect(page.locator(".fstate")).to_have_text("There are no runs of nothing in nobody in the store yet.")
        page = self.open("feature.html", wait=".fstate")
        self.expect(page.locator(".fstate")).to_contain_text("No feature chosen.")
        down = self.browser.new_page()
        self.addCleanup(down.close)
        down.route(re.compile(r".*/v1/runs\?.*"), lambda r: r.fulfill(status=500, content_type="application/json", body='{"error": "The store is down."}'))
        down.goto(self.base + self.SUMMARY)
        self.expect(down.locator(".fstate")).to_have_text("The results store did not answer: The store is down.")
        d = doc("review-page-claims")
        for p in d["properties"]:
            p["verdict"] = "not_run"
        body = json.dumps({"id": "review-page-claims", "complete": True, "files_base": "/x/", "document": d})
        idle = self.browser.new_page()
        self.addCleanup(idle.close)
        idle.route(re.compile(r".*/v1/runs\?.*"), lambda r: r.fulfill(status=200, content_type="application/json",
                                                                       body='{"runs": [{"id": "review-page-claims"}]}'))
        idle.route(re.compile(r".*/v1/runs/review-page-claims$"), lambda r: r.fulfill(status=200, content_type="application/json", body=body))
        idle.goto(self.base + self.SUMMARY)
        self.expect(idle.locator(".fstate")).to_have_text("None of this feature's claims has been tested yet: the last 1 run left every claim out.")

    def test_summary_narrow(self):
        page = self.open(self.SUMMARY, width=390, height=844, wait=".claim")
        page.locator(".claim").first.locator("button.vhead").first.click()
        page.wait_for_selector(".vopen:not([hidden]) .sb video")
        self.assertLessEqual(*page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]"))
        # On a phone the film is on top, the rail is a row of dots under it, and the stage is under that.
        sb = page.locator(".vopen:not([hidden]) .sb")
        film, stage = sb.locator(".film").bounding_box(), sb.locator(".sb-stage").bounding_box()
        self.assertLess(film["y"] + film["height"], stage["y"])
        ys = {round(b["y"] + b["height"] / 2) for b in (sb.locator(".sb-dot").nth(i).bounding_box() for i in range(sb.locator(".sb-dot").count()))}
        self.assertEqual(len(ys), 1, "the dots are not in one row")

    # ---------- the reel ----------

    def test_reel_order(self):
        page = self.open(self.SUMMARY, wait=".claim")
        plan = page.evaluate("""async () => {
          const { reelPlan } = await import('/js/reel.js');
          const p = (await (await fetch('/v1/runs/review-page-claims')).json()).document.properties;
          return reelPlan([{ p: p[0], base: '' }, { p: p[2], base: '' }, { p: p[1], base: '' }])
            .map(s => [s.kind, s.c.id, s.v, s.n, s.ms || 0]);
        }""")
        self.assertEqual(plan, [
            ["claim", "one-claim", 0, 5, 2500], ["intro", "one-claim", 0, 5, 2000], ["film", "one-claim", 0, 5, 0], ["result", "one-claim", 0, 5, 1500],
            ["intro", "three-claims", 1, 5, 2000], ["film", "three-claims", 1, 5, 0], ["result", "three-claims", 1, 5, 1500],
            # p3 is not run in this document, so it adds nothing; cases with no film get longer cards and no film.
            ["claim", "one-regression", 2, 5, 2500], ["intro", "one-regression", 2, 5, 2000], ["film", "one-regression", 2, 5, 0], ["result", "one-regression", 2, 5, 1500],
            ["intro", "week-old", 3, 5, 3000], ["result", "week-old", 3, 5, 2500],
            ["intro", "from-before", 4, 5, 3000], ["result", "from-before", 4, 5, 2500],
        ])
        # The whole feature's reel starts with the walkthrough; then each claim, with the keyboard.
        opener = page.get_by_role("button", name="Watch all 9 films")
        opener.click()
        reel = page.get_by_role("dialog")
        self.expect(reel).to_have_attribute("data-seg", "tour")
        self.expect(page.locator(".reel-progress")).to_have_text("Walkthrough · 10 variations follow")
        self.assertIn("Watch it being used", page.locator(".reel-stage").inner_text())
        page.keyboard.press("ArrowRight")
        self.expect(reel).to_have_attribute("data-seg", "claim")
        self.expect(page.locator(".reel-progress")).to_have_text("1 of 10 · Claims each get a card")
        self.assertIn("I want to see every claim I signed off on", page.locator(".reel-stage").inner_text())
        self.expect(reel).to_have_attribute("data-seg", "intro", timeout=4000)            # the title card moves on by itself
        self.assertIn("Starts from: One feature with a single approved claim.", page.locator(".reel-stage").inner_text())
        self.expect(reel).to_have_attribute("data-seg", "film", timeout=4000)
        page.wait_for_function("document.querySelector('.reel video') && !document.querySelector('.reel video').paused")
        self.assertTrue(page.evaluate("document.querySelector('.reel video').muted"))
        page.keyboard.press(" ")
        self.expect(reel).to_have_attribute("data-paused", "true")
        self.assertTrue(page.evaluate("document.querySelector('.reel video').paused"))
        page.keyboard.press(" ")
        self.expect(reel).to_have_attribute("data-paused", "false")
        page.keyboard.press("ArrowRight")
        self.expect(page.locator(".reel-progress")).to_have_text("2 of 10 · Claims each get a card")
        self.expect(reel).to_have_attribute("data-seg", "intro")
        page.keyboard.press("ArrowRight")
        self.expect(page.locator(".reel-progress")).to_have_text("3 of 10 · Regressions are listed")
        self.expect(reel).to_have_attribute("data-seg", "claim")
        page.keyboard.press("ArrowLeft")
        self.expect(page.locator(".reel-progress")).to_have_text("2 of 10 · Claims each get a card")
        page.keyboard.press("Escape")
        self.expect(page.locator(".reel")).to_have_count(0)
        self.assertEqual(page.evaluate("document.activeElement.textContent"), "Watch all 9 films")
        # One claim's reel, to its result card: a broken variation says why, in one line.
        page.evaluate("sessionStorage.setItem('gt-sb-hold', '1.5')")         # shorter holds in the reel's films
        page.get_by_role("button", name="Watch these 3").click()
        self.expect(page.locator(".reel-progress")).to_have_text("1 of 3 · Regressions are listed")
        page.get_by_role("button", name="Pause", exact=True).click()
        self.expect(reel).to_have_attribute("data-paused", "true")
        page.wait_for_timeout(2800)
        self.expect(reel).to_have_attribute("data-seg", "claim")                          # paused means paused
        page.get_by_role("button", name="Resume").click()
        self.expect(reel).to_have_attribute("data-seg", "film", timeout=8000)
        self.assertEqual(page.locator(".reel .sb .sb-stage").count(), 1)       # the same film with a living storyboard
        self.expect(reel).to_have_attribute("data-seg", "result", timeout=60000)
        self.assertEqual(page.locator(".reel-result").inner_text().replace("\n", ""),
                         "✗Broke: Regressions listed on the run page")
        page.get_by_role("button", name="Close").click()
        self.expect(page.locator(".reel")).to_have_count(0)
        self.assertEqual(page.problems, [])

    def test_film_shape_follows_viewport(self):
        def ratio(box):
            return box["width"] / box["height"]
        page = self.open("run.html?run=review-page-claims#p1/one-claim")       # 1280 by 800
        page.wait_for_selector(".film video")
        self.assertAlmostEqual(ratio(page.locator(".film").bounding_box()), 1280 / 800, delta=0.02)
        old = self.open(f"run.html?run={FILMED}#p3/c2")                          # no viewport: 900 by 700
        old.wait_for_selector(".film video")
        self.assertAlmostEqual(ratio(old.locator(".film").bounding_box()), 900 / 700, delta=0.02)
        summary = self.open(self.SUMMARY, wait=".claim")
        summary.locator(".claim").nth(0).locator("button.vhead").first.click()
        summary.wait_for_selector(".vopen:not([hidden]) .film")
        self.assertAlmostEqual(ratio(summary.locator(".vopen:not([hidden]) .film").bounding_box()), 1280 / 800, delta=0.02)
        summary.locator(".claim").nth(2).locator("button.vhead").first.click()                      # from the older run
        summary.wait_for_selector(".vopen:not([hidden]) .film")
        self.assertAlmostEqual(ratio(summary.locator(".vopen:not([hidden]) .film").bounding_box()), 900 / 700, delta=0.02)
        summary.get_by_role("button", name="Watch these 2").click()
        summary.keyboard.press("ArrowRight")     # skip the title card
        summary.locator(".reel").get_by_role("button", name="Pause").click()
        summary.evaluate("() => { }")
        summary.get_by_role("dialog").get_by_role("button", name="Resume").click()
        summary.wait_for_selector(".reel .film", timeout=8000)
        self.assertAlmostEqual(ratio(summary.locator(".reel .film").bounding_box()), 1280 / 800, delta=0.02)

    # ---------- comparisons and the walkthrough on the summary ----------

    def test_summary_comparison_line(self):
        page = self.open(self.SUMMARY, wait=".claim")
        p1, p2, p3 = (page.locator(".claim").nth(i) for i in range(3))
        self.assertEqual(p1.locator(".vrow").nth(0).locator(".cmpline").inner_text(), "3 comparisons matched")
        self.assertEqual(p1.locator(".vrow").nth(1).locator(".cmpline").count(), 0)
        self.assertEqual(p2.locator(".vrow").nth(0).locator(".cmpline").inner_text(),
                         "Did not match: Regressions listed on the run page. Expected 1 regression: Cart total adds up; found the Regression section is empty.")
        # A row that did not hold keeps its reason too.
        self.assertIn("The Regression section was empty", p2.locator(".vrow").nth(0).locator(".why").inner_text())
        self.assertEqual(p3.locator(".cmpline").count(), 0)
        self.assertNotIn("null", page.locator(".claims").inner_text())

    def serve_claims(self, change):
        """The summary, with the newer run's document changed."""
        d = doc("review-page-claims")
        change(d)
        body = json.dumps({"id": "review-page-claims", "complete": True, "files_base": "/v1/runs/review-page-claims/files/", "document": d})
        page = self.browser.new_page(viewport={"width": 1440, "height": 1000})
        self.addCleanup(page.close)
        page.route(re.compile(r".*/v1/runs/review-page-claims$"), lambda r: r.fulfill(status=200, content_type="application/json", body=body))
        page.goto(self.base + self.SUMMARY)
        page.wait_for_selector(".claim")
        return page

    def test_film_first_page(self):
        # With a film of the feature in use, the feature's page is that film, large, and little else.
        page = self.open(self.FILM, wait="section.walkthrough.bare .sb .film")
        self.assertEqual(page.locator(".fbar .fname").inner_text(), "review-page · groundtruth-cli")
        chip = page.locator(".fbar .fchip")
        self.assertEqual(chip.inner_text(), "2 hold, 1 fails")
        self.assertIn("bad", chip.get_attribute("class"))
        for gone in (".goal", ".fverdict", ".fmeta", ".claim", ".watch-all", ".wt-h", ".wt-note", ".started", ".fb"):
            self.assertEqual(page.locator(gone).count(), 0, gone)
        # What the walkthrough saw go wrong still shows, under the film.
        self.assertEqual(page.locator(".wt-warn").count(), 1)
        # The results come from two runs, so the one link goes to the summary of claims, which has them all.
        link = page.locator("a.fdetail")
        self.assertEqual(link.inner_text(), "Every claim and variation")
        self.assertEqual(link.get_attribute("href"), "feature.html?project=groundtruth-cli&feature=review-page&view=claims")
        # The film gets most of a wide window, and the page never scrolls sideways.
        wide_film = page.locator(".sb .film").first.bounding_box()["width"]
        self.assertGreater(wide_film, 800)
        self.assertFalse(page.evaluate("document.documentElement.scrollWidth > innerWidth"))
        self.assertEqual(page.problems, [])
        link.click()
        page.wait_for_selector(".claim")
        self.assertEqual(page.locator(".fverdict").inner_text(), "2 hold, 1 fails.")
        # On a phone the film is as wide as the screen allows, with its controls under it.
        narrow = self.open(self.FILM, width=390, height=844, wait="section.walkthrough.bare .sb .film")
        self.assertFalse(narrow.evaluate("document.documentElement.scrollWidth > innerWidth"))
        film, ctrl = narrow.locator(".sb .film").first.bounding_box(), narrow.locator(".sb .ctrl").first.bounding_box()
        self.assertGreater(film["width"], 280)
        self.assertGreater(ctrl["y"], film["y"] + film["height"] - 1)
        self.assertEqual(narrow.problems, [])

    def film_page(self, change=None, width=1440, height=900):
        # The film-first page, with the newest run's document changed first.
        d = doc("review-page-claims")
        if change:
            change(d)
        body = json.dumps({"id": "review-page-claims", "complete": True, "files_base": "/v1/runs/review-page-claims/files/", "document": d})
        page = self.browser.new_page(viewport={"width": width, "height": height})
        self.addCleanup(page.close)
        page.problems = []
        page.on("pageerror", lambda e: page.problems.append(str(e)))
        page.route(re.compile(r"https://fonts\.(googleapis|gstatic)\.com/.*"), lambda r: r.fulfill(status=200, content_type="text/css", body=""))
        page.route(re.compile(r".*/v1/runs/review-page-claims$"), lambda r: r.fulfill(status=200, content_type="application/json", body=body))
        page.goto(self.base + self.FILM)
        page.wait_for_selector("section.walkthrough.bare .sb .film")
        page.wait_for_function("document.querySelector('.sb video').readyState >= 1")
        return page

    # Every caption the film shows, in order, going along the scrub bar: "ok|", "bad|" or "|" and then the words.
    SWEEP = """() => {
        const sb = document.querySelector('.sb');
        const r = sb.querySelector('input[type=range]');
        const out = [];
        for (let i = 0; i <= 1000; i += 1) {
            r.value = String(i);
            r.dispatchEvent(new Event('input', { bubbles: true }));
            const c = (sb.dataset.captionMark || '') + '|' + (sb.dataset.caption || '');
            if (out[out.length - 1] !== c) out.push(c);
        }
        return out;
    }"""

    def test_film_first_page_has_one_caption(self):
        # The film alone, with one caption over it in the verifier's own words: what it is about to check, then what
        # it found. The reads before the film are typed in one terminal window, each with its sentence as the caption.
        def with_reads(d):
            d["walkthrough"]["starts_from"] = [{"tool": "make_feature", "input": {"claims": 2}, "returned": {"feature": "cart"}},
                                               {"tool": "list_runs", "input": {"feature": "cart"}, "returned": {"runs": ["r1", "r2"]}}]
        page = self.film_page(with_reads)
        sb = page.locator(".sb.captions")
        self.assertEqual(sb.count(), 1)
        for hidden in (".sb-rail", ".sb-stage", ".sb-opts", ".sb-list"):
            self.assertFalse(sb.locator(hidden).first.is_visible(), hidden)
        # Before play: an empty prompt in the terminal, and the first read's sentence.
        self.assertIn("on", sb.locator(".rterm").get_attribute("class").split())
        self.assertEqual(sb.locator(".rt-body").inner_text().strip(), "$")
        first = sb.get_attribute("data-caption")
        self.assertTrue(first and sb.locator(".sb-cap").inner_text() == first)
        got = page.evaluate(self.SWEEP)
        self.assertEqual(got[0], "|" + first)
        self.assertEqual(len(got), 9, got)
        self.assertEqual(got[2:], [
            "|Every claim you approved should get its own card here.",
            "ok|Three cards, one for each claim. That works.",
            "|",                                               # nothing was said before the next check
            "ok|The changed claim is marked",                  # recorded without what was said: the check's own name
            "|The regression that ran should be listed under Regression.",
            "bad|It is listed, with its result. That works.",
            "bad|1 of 3 checks did not match."])
        # At the end the terminal is gone, the mark is drawn, and the list of steps stays shut.
        self.assertNotIn("on", sb.locator(".rterm").get_attribute("class").split())
        self.assertEqual(sb.locator(".sb-cap .sb-capmark").inner_text().strip(), "✗")
        self.assertFalse(sb.locator(".sb-list").is_visible())
        # Each read is typed after the last one: at the end of the second read's turn both commands are there.
        length = float(sb.get_attribute("data-length"))
        page.evaluate("x => { const r = document.querySelector('.sb input[type=range]'); r.value = String(x); r.dispatchEvent(new Event('input', { bubbles: true })); }",
                      round(5.9 / length * 1000))
        typed = sb.locator(".rt-body").inner_text()
        self.assertIn("$ make-feature --claims 2", typed)
        self.assertIn("$ list-runs --feature cart", typed)
        self.assertEqual(sb.get_attribute("data-caption"), got[1][1:])
        # The film stops for three seconds at each check, and each read has three: the watched film is that much longer.
        self.assertAlmostEqual(length - float(sb.get_attribute("data-film")), 2 * 3 + 3 * 3, places=2)
        # Next and previous go between the reads and the checks.
        page.evaluate("document.querySelector('.sb input[type=range]').value = '0'; document.querySelector('.sb input[type=range]').dispatchEvent(new Event('input', { bubbles: true }))")
        nxt, prev = sb.locator("button.stepbtn.next"), sb.locator("button.stepbtn.prev")
        seen = []
        for _ in range(4):
            nxt.click()
            seen.append((sb.get_attribute("data-caption-mark") or "") + "|" + sb.get_attribute("data-caption"))
        self.assertEqual(seen, [got[1], got[3], got[5], got[7]])
        prev.click()
        self.assertEqual(sb.get_attribute("data-caption"), "The changed claim is marked")
        self.assertFalse(page.evaluate("document.documentElement.scrollWidth > innerWidth"))
        self.assertEqual(page.problems, [])

    def test_film_without_spoken_checks_keeps_the_storyboard(self):
        # An older run, whose checks were recorded without what was said, has nothing to caption with.
        def silent(d):
            for s_ in d["walkthrough"]["steps"]:
                s_.pop("say", None)
        page = self.film_page(silent)
        self.assertEqual(page.locator(".sb.captions").count(), 0)
        self.assertTrue(page.locator(".sb .sb-stage").first.is_visible())
        self.assertEqual(page.locator(".sb .sb-cap").count(), 0)
        self.assertEqual(page.problems, [])

    def test_film_first_page_links_to_the_run_when_one_run_tested_everything(self):
        # Without the older run, every result shown is from one run: the link is that run's own page.
        page = self.browser.new_page(viewport={"width": 1440, "height": 1000})
        self.addCleanup(page.close)
        page.route(re.compile(r"https://fonts\.(googleapis|gstatic)\.com/.*"), lambda r: r.fulfill(status=200, content_type="text/css", body=""))

        def only_newest(route):
            body = route.fetch().json()
            body["runs"] = body["runs"][:1]
            route.fulfill(json=body)
        page.route(re.compile(r".*/v1/runs\?.*"), only_newest)
        page.goto(self.base + self.FILM)
        page.wait_for_selector("a.fdetail")
        self.assertRegex(page.locator("a.fdetail").get_attribute("href"), r"^run\.html\?run=[\w-]+$")

    def test_walkthrough(self):
        page = self.open(self.SUMMARY, wait=".claim")
        wt = page.locator("section.walkthrough")
        self.assertEqual(wt.locator("h2").inner_text(), "Watch it being used")
        # Directly under the verdict line, before the claims.
        self.assertLess(page.locator(".fverdict").bounding_box()["y"], wt.bounding_box()["y"])
        self.assertLess(wt.bounding_box()["y"], page.locator(".claim").first.bounding_box()["y"])
        self.assertIn("each claim's result comes from its own variations below", wt.locator(".wt-note").inner_text().lower())
        self.assertIn("Starts from: A feature with three claims and one regression run.", wt.locator(".started").inner_text())
        # The third stop's comparison did not match, though its spoken words sound like success.
        self.assertEqual(wt.locator(".wt-warn").inner_text(),
                         "The walkthrough saw something that did not match: The regression is listed (Regressions are listed).")
        # Its film with a living storyboard: one dot per step, a stop's card with the claim, his words, what was said.
        sb = wt.locator(".sb")
        self.assertEqual(sb.locator(".sb-dot").count(), 9)
        self.assertEqual(sb.locator(".sb-dot.bad").count(), 2)          # the stop that did not match, and the result
        sb.get_by_role("button", name="All steps (9)").click()
        cards = sb.locator(".sb-list > li")
        self.assertEqual(cards.count(), 9)
        stop1 = cards.nth(2)
        self.assertIn("Stop 1 of 3: Claims each get a card.", stop1.inner_text())
        self.assertIn("\u201cI want to see every claim I signed off on, each on its own card\u201d", stop1.inner_text())
        self.assertIn("Every claim you approved should get its own card here.", stop1.inner_text())
        third = cards.nth(5)
        self.assertIn("Stop 3 of 3: Regressions are listed.", third.inner_text())
        self.assertEqual(third.locator(".said.bad .m.x").count(), 1)       # its words sound like success; it did not match
        self.assertIn("It is listed, with its result. That works.", third.locator(".said").inner_text())
        record = third.locator("details.careful")
        self.assertFalse(record.evaluate("d => d.open"))
        self.assertEqual(record.locator("summary").inner_text(), "The careful record")
        record.locator("summary").click()
        self.assertIn("1 regression: Cart total adds up", record.inner_text())
        self.assertIn("No regression listed; the section is empty", record.inner_text())
        self.assertIn("Reported that 1 of 3 stops did not match.", cards.nth(8).inner_text())
        self.assert_unique_names(page)
        self.assertEqual(page.problems, [])

        def all_match(d):
            for s_ in d["walkthrough"]["steps"]:
                if s_["kind"] == "compare":
                    s_["matches"] = True
        good = self.serve_claims(all_match)
        self.assertEqual(good.locator(".wt-warn").count(), 0)
        self.assertEqual(good.locator("section.walkthrough .sb-dot.bad").count(), 0)

        def stuck(d):
            d["walkthrough"]["outcome"] = "cant_run"
            d["walkthrough"]["observed"] = "The app did not start."
            all_match(d)
        page = self.serve_claims(stuck)
        self.assertEqual(page.locator(".wt-warn").inner_text(), "The walkthrough could not be run: The app did not start.")

    # ---------- the film with a living storyboard ----------

    # The run page with the film's steps beside it (&steps=1), the player these tests are about.
    ONE = "run.html?run=review-page-claims&steps=1#p1/one-claim"

    def story(self, page, js):
        return page.evaluate("""async (js) => {
          const story = await import('/js/story.js');
          const d = (await (await fetch('/v1/runs/review-page-claims')).json()).document;
          return (new Function('story', 'd', js))(story, d);
        }""", js)

    def test_steps_of_a_case(self):
        page = self.open(self.ONE)
        got = self.story(page, "return story.storySteps(d.properties[0].cases[0]).map(s => [s.kind, s.t, s.text, s.samePageAs || 0, !!s.still, s.still ? s.still.crop : null]);")
        self.assertEqual([g[0] for g in got], ["data", "browser", "situation", "browser", "compare", "browser", "compare", "browser", "compare", "result"])
        data, opened, situation = got[0], got[1], got[2]
        self.assertEqual(data[1], None)                                       # read before the film
        self.assertEqual(opened[2], "Opened the Runs page in a real browser.")
        self.assertTrue(situation[2].startswith("Checked it was in the right situation: The review page is open on login"))
        # Nothing happened between opening the page and the situation check, so its still would repeat the page.
        self.assertEqual(situation[3:5], [2, False])
        self.assertEqual(got[3][2], "Pointed at “Claims”.")
        self.assertEqual(got[6][2], "Compared \u201cThe card's title\u201d with what the claim says. It matches.")
        self.assertEqual(got[6][5], "green")                                   # a close-up of what it outlined
        self.assertTrue(all(g[4] for g in got if g[0] in ("browser", "compare")))
        self.assertEqual(got[9][2], "Reported that this variation held.")
        self.assertAlmostEqual(got[9][1], 6.2 + 0.75)                           # the result comes with the last step
        broke = self.story(page, "return story.storySteps(d.properties[1].cases[0]).slice(-1)[0];")
        self.assertEqual(broke["word"], "Broke")
        self.assertEqual(broke["reason"], "\u201cRegressions listed on the run page\u201d was not what the claim says.")
        # An older case with no comparisons: its actions and the result.
        old = page.evaluate("""async () => { const { storySteps } = await import('/js/story.js');
          const d = (await (await fetch('/v1/runs/dashboard-one-property-filmed')).json()).document;
          return storySteps(d.properties[2].cases[2]).map(s => s.kind); }""")
        self.assertEqual(set(old), {"browser", "result"})
        self.assertEqual(old[-1], "result")
        # The terminal card is the real returned record: the command from the tool and its input, then the answer.
        term = page.evaluate("""async () => { const { terminal } = await import('/js/story.js');
          const sf = { tool: 'menu_service_stations', input: { hall: 'north-hall' }, returned: { hall: 'north-hall', date: '2026-11-13', asked: 'the menu service, directly',
            meals: [{ meal: 'Breakfast', stations: ['A'] }, { meal: 'Dinner', start: '16:30:00', end: '20:00:00', stations: ['Honeycakes', 'Ember'] }] } };
          const el = terminal(sf, { found: 'Dinner showing' }); return [el.querySelector('.tbody').textContent, el.querySelector('summary').textContent]; }""")
        self.assertIn("$ menu-service-stations --hall north-hall", term[0])
        self.assertIn("Dinner 16:30–20:00, stations in order:", term[0])
        self.assertIn("Honeycakes", term[0])
        self.assertNotIn("Breakfast", term[0])
        self.assertEqual(term[1], "show all (1 more: Breakfast)")

    def test_steps_of_a_walkthrough_stop(self):
        page = self.open(self.ONE)
        got = self.story(page, "return story.tourSteps(d.walkthrough, id => d.properties.find(p => p.id === id)).map(s => ({kind: s.kind, text: s.text, words: s.words || '', says: (s.says || []).map(x => x.text), ok: s.ok}));")
        stops = [g for g in got if g["kind"] == "stop"]
        self.assertEqual([g["text"] for g in stops], ["Stop 1 of 3: Claims each get a card.",
                         "Stop 2 of 3: Clicking a claim's card shows the video for that claim.", "Stop 3 of 3: Regressions are listed."])
        self.assertEqual(stops[0]["words"], doc("review-page-claims")["properties"][0]["your_words"])
        self.assertEqual(stops[0]["says"], ["Every claim you approved should get its own card here."])
        self.assertEqual([g["ok"] for g in stops], [True, True, False])
        self.assertEqual(got[-1]["text"], "Reported that 1 of 3 stops did not match.")

    def sb_at(self, page, t):
        page.evaluate(f"document.querySelector('.sb video').currentTime = {t}")
        page.wait_for_function(f"Math.abs(document.querySelector('.sb video').currentTime - {t}) < 0.05")
        page.wait_for_timeout(120)
        return page.evaluate("""() => { const sb = document.querySelector('.sb');
          return { step: Number(sb.dataset.step), p: Number(getComputedStyle(sb.querySelector('.sb-marker')).getPropertyValue('--p')),
                   now: sb.querySelector('.sb-layer.on .sb-nowtext').textContent,
                   places: [...sb.querySelectorAll('.sb-dot')].map((d) => Number(d.style.getPropertyValue('--i'))),
                   frames: [...sb.querySelectorAll('.sb-dot')].map((d) => Number(d.dataset.frame)) }; }""")

    def test_rail_and_stage_follow_film_time(self):
        page = self.open(self.ONE)
        page.wait_for_function("document.querySelector('.sb video').readyState >= 1")
        sb = page.locator(".sb")
        self.assertEqual(sb.locator(".sb-dot").count(), 10)
        self.assertEqual(sb.locator(".sb-dot.ok").count(), 5)                  # the situation, three comparisons and the result
        # Before play the stage shows the data read, which came first.
        self.assertIn("Put in the starting data", sb.locator(".sb-layer.on .sb-nowtext").inner_text())
        # A browser step's moment is the page as it came to be a moment after its action: "Pointed at" (2.06) holds at
        # 2.4, just before the comparison at 2.5.
        a = self.sb_at(page, 2.3)
        self.assertEqual(a["step"], 3)
        self.assertEqual(a["frames"][3], 2.4)
        b = self.sb_at(page, 2.42)
        self.assertEqual(b["step"], 4)
        self.assertEqual(b["now"], "Pointed at “Claims”.")
        self.assertGreater(b["p"], a["p"])                                      # the marker moves between dots with time
        self.assertLess(b["p"], b["places"][4])
        c = self.sb_at(page, 2.55)
        self.assertEqual(c["step"], 5)
        # Between step 5's dot and step 6's, as far as the film is from step 5's frame to step 6's (no wait cut between).
        i5, i6 = c["places"][4], c["places"][5]
        f5, f6 = c["frames"][4], c["frames"][5]
        self.assertAlmostEqual(c["p"], i5 + (i6 - i5) * (2.55 - f5) / (f6 - f5), places=2)
        self.assertIn("It matches.", c["now"])
        self.assertEqual(sb.locator(".sb-layer.on .sb-now .still.closeup").count(), 0)  # no outline element here
        # The previous step shrunk above, the next one peeking below.
        self.assertIn("Pointed at", sb.locator(".sb-layer.on .sb-slot.prev").inner_text())
        self.assertIn("Clicked", sb.locator(".sb-layer.on .sb-slot.next").inner_text())
        self.assertEqual(page.problems, [])

    def test_clicking_a_dot(self):
        page = self.open(self.ONE)
        page.wait_for_function("document.querySelector('.sb video').readyState >= 1")
        page.get_by_role("button", name=re.compile(r"^Step 7: Compared .The card's title")).click()
        page.wait_for_function("Math.abs(document.querySelector('.sb video').currentTime - 4.7) < 0.05")
        self.expect(page.locator(".sb-layer.on .sb-k")).to_have_text(re.compile(r"^Step 7 of 10"))
        self.assertEqual(page.locator(".sb-dot[aria-current=step]").inner_text(), "7")
        # Its close-up keeps its space before the picture arrives, so nothing moves.
        self.assertEqual(page.locator(".sb-layer.on .sb-now .still.closeup").count(), 1)
        page.get_by_role("button", name=re.compile(r"^Step 1: ")).click()
        self.expect(page.locator(".sb-layer.on .sb-k")).to_have_text(re.compile(r"^Step 1 of 10 · before the film"))

    def test_storyboard_list_after_the_end(self):
        page = self.open(self.ONE)
        page.wait_for_function("document.querySelector('.sb video').readyState >= 1")
        sb = page.locator(".sb")
        self.assertTrue(sb.locator(".sb-list").is_hidden())
        self.sb_at(page, 6.6)
        page.get_by_role("button", name="Play the film", exact=True).click()
        self.expect(sb.locator(".sb-list")).to_be_visible(timeout=6000)
        cards = sb.locator(".sb-list > li")
        self.assertEqual(cards.count(), 10)
        self.assertIn("Reported that this variation held.", cards.nth(9).inner_text())
        self.assertIn("Held", cards.nth(9).locator(".rcard").inner_text())
        self.assertEqual(cards.nth(2).locator(".still").count(), 0)
        self.assertIn("The same page as step 2.", cards.nth(2).inner_text())
        # Each still's space is kept from the film's shape before the picture comes.
        box = cards.nth(1).locator(".still").bounding_box()
        self.assertAlmostEqual(box["width"] / box["height"], 1280 / 800, delta=0.03)
        self.expect(sb.locator(".sb-layer.on .sb-k")).to_have_text(re.compile(r"^Step 10 of 10"))
        # "All steps" hides and shows the list.
        sb.get_by_role("button", name="Hide the steps").click()
        self.assertTrue(sb.locator(".sb-list").is_hidden())
        sb.get_by_role("button", name="All steps (10)").click()
        self.assertTrue(sb.locator(".sb-list").is_visible())

    def test_stage_card_visible_while_playing(self):
        # At in-between widths the current card must be on screen beside the film, also in a pane that gets no
        # animation frames (a background browser pane), where it once stayed invisible.
        for width, height, frames in [(800, 465, True), (1024, 800, True), (800, 465, False), (1024, 800, False)]:
            for path, root in [(self.SUMMARY, "section.walkthrough .sb"), (self.ONE, ".sb")]:
                with self.subTest(width=width, frames=frames, path=path):
                    page = self.browser.new_page(viewport={"width": width, "height": height})
                    self.addCleanup(page.close)
                    if not frames:
                        page.add_init_script("window.requestAnimationFrame = () => 0;")
                    page.goto(self.base + path)
                    page.wait_for_selector(f"{root} video")
                    # Polled on a timer: with no animation frames, Playwright's own frame-based polling would stall too.
                    page.wait_for_function(f"document.querySelector('{root} video').readyState >= 1", polling=100)
                    page.locator(f"{root} video").evaluate("v => { v.currentTime = 2.3; v.play(); }")
                    page.wait_for_function(f"document.querySelector('{root} video').currentTime > 3.2", polling=100, timeout=15000)
                    page.wait_for_timeout(600)
                    got = page.evaluate("""(root) => { const sb = document.querySelector(root);
                      const layer = sb.querySelector('.sb-layer.on'); const now = layer.querySelector('.sb-now');
                      const b = now.getBoundingClientRect(); const f = sb.querySelector('.film').getBoundingClientRect();
                      return { op: Number(getComputedStyle(layer).opacity), x: b.x, w: b.width, h: b.height, text: now.innerText.trim(),
                               fw: f.width, sbw: sb.getBoundingClientRect().width, step: Number(sb.dataset.step), vw: innerWidth }; }""", root)
                    self.assertGreater(got["op"], 0.95, got)
                    self.assertGreater(got["w"], 180, got)
                    self.assertGreater(got["h"], 60, got)
                    self.assertTrue(got["text"].startswith("Step "), got)
                    self.assertLessEqual(got["x"] + got["w"], got["vw"] + 1, got)
                    self.assertGreater(got["step"], 1, got)                    # it moved on with the film
                    self.assertGreater(got["fw"], got["sbw"] * 0.5, got)       # the film uses the space it has

    # ---------- playing: holds, the marker, the stage and the page ----------

    # Every few milliseconds while a film plays: the film's time, the watched clock, the step shown (by what is drawn:
    # computed opacity and visibility), each dot's fill as drawn, the marker's and every dot's offset along the rail,
    # the scene shown in the film's frame, the scrub bar, and the boxes that must not move.
    SAMPLER = """([root, every]) => {
      const sb = document.querySelector(root);
      const v = sb.querySelector('video'), rail = sb.querySelector('.sb-rail'), line = rail.querySelector('.sb-line');
      const marker = sb.querySelector('.sb-marker'), dots = [...sb.querySelectorAll('.sb-dot')], fills = [...sb.querySelectorAll('.sb-fill')];
      const film = sb.querySelector('.film'), stage = sb.querySelector('.sb-stage'), foot = sb.querySelector('.sb-foot');
      const fill = sb.querySelector('.fill'), track = sb.querySelector('.track');
      const box = (e) => { const b = e.getBoundingClientRect(); return [b.x + scrollX, b.y + scrollY, b.width, b.height]; };
      const shown = (e) => { let o = 1; for (; e && e !== sb; e = e.parentElement) { const cs = getComputedStyle(e);
        if (cs.visibility === 'hidden' || cs.display === 'none') return 0; o *= Number(cs.opacity); } return o; };
      const scale = (e) => { const m = getComputedStyle(e).transform; return m === 'none' ? 1 : Number(m.slice(7, -1).split(',')[3]); };
      window.__sb = [];
      const take = () => {
        const r = rail.getBoundingClientRect(), hz = r.width > r.height;
        const at = (e) => { const b = e.getBoundingClientRect(); return hz ? b.left + b.width / 2 - r.left : b.top + b.height / 2 - r.top; };
        const m = marker.getBoundingClientRect(), ln = line.getBoundingClientRect();
        const cards = [...stage.querySelectorAll('.sb-layer')].map((e) => [Number(e.dataset.n), shown(e)]).filter((x) => x[1] > 0.01);
        const on = sb.querySelector('.sb-layer.on');
        const sc = [...sb.querySelectorAll('.scene')].filter((e) => shown(e) > 0.5);
        __sb.push({ t: v.currentTime, wall: performance.now(), vpaused: v.paused, playing: sb.dataset.playing === 'true',
          w: Number(sb.dataset.watched || 0), step: Number(sb.dataset.step || 0), state: sb.dataset.hold || '',
          m: at(marker), dots: dots.map(at), fills: fills.map(scale),
          off: hz ? (m.top + m.height / 2) - (ln.top + ln.height / 2) : (m.left + m.width / 2) - (ln.left + ln.width / 2),
          on: on ? Number(on.dataset.n) : 0, onShown: on ? shown(on) : 0, cover: 1 - cards.reduce((a, x) => a * (1 - x[1]), 1),
          scene: sc.map((e) => Number(e.dataset.n)), sceneBox: sc.length ? box(sc[0]) : null,
          fill: fill.getBoundingClientRect().width / track.getBoundingClientRect().width,
          film: box(film), stage: box(stage), foot: box(foot), list: !sb.querySelector('.sb-list').hidden,
          result: !!(on && on.querySelector('.rcard')) });
      };
      take();
      window.__sbTimer = setInterval(take, every);
    }"""

    def player(self, path, width=1280, height=800, frames=True, hold=None, stop=None, reduced=False, wait=".sb video"):
        """A page with a film, the hold and "Pause at each step" chosen as a person would have for the session."""
        page = self.browser.new_page(viewport={"width": width, "height": height}, reduced_motion="reduce" if reduced else "no-preference")
        self.addCleanup(page.close)
        page.problems = []
        page.on("pageerror", lambda e: page.problems.append(f"page error: {e}"))
        page.route(re.compile(r"https://fonts\.(googleapis|gstatic)\.com/.*"), lambda r: r.fulfill(status=200, content_type="text/css", body=""))
        if not frames:
            page.add_init_script("window.requestAnimationFrame = () => 0;")
        if hold is not None:
            page.add_init_script(f"sessionStorage.setItem('gt-sb-hold', '{hold}');")
        if stop is not None:
            page.add_init_script(f"sessionStorage.setItem('gt-sb-stop', '{1 if stop else 0}');")
        if path:
            page.goto(self.base + path)
            if wait:
                page.wait_for_selector(wait)
        return page

    def ready(self, page, root=".sb"):
        # Polled on a timer: with no animation frames, Playwright's own frame-based polling would stall too.
        page.wait_for_selector(f"{root} video")
        page.wait_for_function(f"document.querySelector('{root} video').readyState >= 2", polling=100)

    def play_through(self, page, root=".sb", row=False, every=25, during=None):
        """Plays a film from its start to its end and returns the samples, starting with the last one before play."""
        if row:
            page.wait_for_selector(".claim button.vhead")
            page.locator(".claim button.vhead").first.click()
        self.ready(page, root)
        page.locator(root).scroll_into_view_if_needed()
        page.wait_for_timeout(300)
        page.evaluate(self.SAMPLER, [root, every])
        page.wait_for_timeout(100)
        page.locator(f"{root} .play").click()
        if during:
            during(page)
        page.wait_for_function(f"(() => {{ const sb = document.querySelector('{root}'); return sb.dataset.playing === 'false' && !sb.querySelector('.sb-list').hidden; }})()",
                               polling=100, timeout=120000)
        page.wait_for_timeout(200)
        got = page.evaluate("clearInterval(__sbTimer), __sb")
        first = next(i for i, x in enumerate(got) if x["playing"])
        spans = page.evaluate(f"[...document.querySelectorAll('{root} .sb-dot')].map((d) => d.dataset.hold.split(',').map(Number))")
        whole = page.evaluate(f"(() => {{ const sb = document.querySelector('{root}'); return [Number(sb.dataset.length), Number(sb.dataset.film)]; }})()")
        return got[max(0, first - 1):], spans, whole

    @staticmethod
    def expected_marker(x, spans):
        """Where the marker belongs at a sample: on the current step's dot while it holds, and travelling to the next
        dot in proportion to the watched clock between the end of that hold and the start of the next."""
        cur = x["step"] - 1
        a, b = spans[cur]
        if x["w"] <= b + 1e-3 or cur + 1 >= len(spans):
            return x["dots"][cur]
        nb = spans[cur + 1][0]
        return x["dots"][cur] + (x["dots"][cur + 1] - x["dots"][cur]) * min(1, (x["w"] - b) / max(1e-6, nb - b))

    def assert_holds(self, got, spans, whole, what, resized=False):
        """What a viewer must see while a film plays with holds, measured from the samples; returns the hold lengths
        and the running time."""
        self.assertGreater(len(got), 40, what)
        n = len(spans)
        first = got[0]
        length, film = whole
        holds = []
        start = None
        for i in range(n):
            # A hold: from the moment it begins (the card of a later step is shown at that moment too) to the moment
            # its dot is full.
            s = next((k for k, x in enumerate(got) if x["step"] == i + 1 and x["state"] == "holding"), None)
            self.assertIsNotNone(s, (what, "no hold seen for step", i + 1))
            if i:
                shown_at = next(k for k, x in enumerate(got) if x["on"] == i + 1)
                self.assertLessEqual(abs(got[shown_at]["wall"] - got[s]["wall"]), 30, (what, "the card of step", i + 1, "did not change as its hold began"))
            e = next((k for k in range(s, len(got)) if got[k]["fills"][i] >= 0.999), None)
            self.assertIsNotNone(e, (what, "the dot of step", i + 1, "never filled"))
            want = spans[i][1] - spans[i][0]
            took = (got[e]["wall"] - got[s]["wall"]) / 1000
            holds.append(round(took, 2))
            self.assertLess(abs(took - want), 0.15, (what, "hold of step", i + 1, took, want))
            seg = got[s:e]
            if seg:
                # The film stands on its frame, the marker on the dot, and the fill only rises.
                self.assertLess(max(x["t"] for x in seg) - min(x["t"] for x in seg), 0.02, (what, "the film moved during the hold of step", i + 1))
                rest = [x["m"] - x["dots"][i] for x in seg]          # against its dot, which a resize may move
                self.assertLess(max(rest) - min(rest), 0.6, (what, "the marker moved during the hold of step", i + 1))
                for y, x in zip(seg, seg[1:]):
                    self.assertGreaterEqual(x["fills"][i], y["fills"][i] - 1e-3, (what, "the fill of step", i + 1, "went down"))
            # Before its hold a dot is empty, after it full.
            self.assertTrue(all(x["fills"][i] < 1e-3 for x in got[:s] if x["step"] - 1 < i), (what, "dot", i + 1, "filled early"))
            self.assertTrue(all(x["fills"][i] >= 0.999 for x in got[e:]), (what, "dot", i + 1, "emptied after its hold"))
        worst = dict(back=0.0, path=0.0)
        for k, x in enumerate(got):
            lo, hi = min(x["dots"][0], x["dots"][-1]), max(x["dots"][0], x["dots"][-1])
            self.assertTrue(lo - 1 <= x["m"] <= hi + 1, (what, "marker off the rail's span", x["w"]))
            self.assertLess(abs(x["off"]), 1, (what, "marker not centred on the line", x["w"]))
            worst["path"] = max(worst["path"], abs(self.expected_marker(x, spans) - x["m"]))
            self.assertEqual(x["on"], x["step"], (what, "the card shown is not the current step", x["w"]))
            self.assertGreater(x["onShown"], 0.99, (what, "the current card is not fully drawn", x["w"]))
            self.assertGreater(x["cover"], 0.99, (what, "the stage was empty for a moment", x["w"]))
            self.assertLess(abs(x["fill"] - x["w"] / length), 0.01, (what, "the scrub bar does not agree with the clock", x["w"]))
            # A step before the film is shown in the film's frame while it holds, and nothing covers the film after.
            if x["scene"]:
                self.assertEqual(x["scene"], [x["step"]], (what, "scene", x["scene"], "shown for step", x["step"]))
                self.assertLess(max(abs(a - b) for a, b in zip(x["sceneBox"], x["film"])), 2.5, (what, "the scene is not the film's frame"))
            if not resized:
                for key in ("film", "stage", "foot", "dots"):
                    self.assertLess(max(abs(a - b) for a, b in zip(x[key], first[key])), 0.6, (what, key, "moved", x["w"]))
            if k:
                y = got[k - 1]
                self.assertGreaterEqual(x["w"], y["w"] - 1e-3, (what, "the watched clock went back", x["w"]))
                self.assertGreaterEqual(x["step"], y["step"], (what, "the card went back", x["w"]))
                # (a resize places the dots again for the new rail; the marker moves with them, once)
                if not (resized and max(abs(a - b) for a, b in zip(x["dots"], y["dots"])) > 0.6):
                    worst["back"] = max(worst["back"], y["m"] - x["m"])
        self.assertLessEqual(worst["back"], 1, (what, "the marker went backwards", worst))
        self.assertLessEqual(worst["path"], 1.5, (what, "the marker is not where the clock says", worst))
        # The whole running time: the trimmed film, every hold, and a short travel wherever two steps have no film
        # between them; played in about that much time.
        # (a travel never lasts more than 0.4 seconds, so there is at most that much between two holds)
        travels = round(length - film - sum(b - a for a, b in spans), 3)
        self.assertTrue(-1e-3 <= travels <= 0.4 * (n - 1) + 1e-3, (what, "running time is not film + holds + travels", length, film, travels))
        start = next(k for k, x in enumerate(got) if x["playing"])
        stop = next(k for k, x in enumerate(got) if x["list"])
        ran = (got[stop]["wall"] - got[start]["wall"]) / 1000
        # (each time the film starts again after a hold it takes a moment to get going, so a play runs a little long)
        self.assertLess(abs(ran - length), max(1.0, 0.06 * length), (what, "ran", ran, "for a length of", length))
        end = got[-1]
        self.assertLess(abs(end["m"] - end["dots"][-1]), 1, (what, "the marker did not end on the last dot"))
        self.assertTrue(end["result"] and end["list"] and end["step"] == n, (what, "the end", end["step"], end["result"], end["list"]))
        return dict(holds=holds, length=length, film=film, travels=travels, ran=round(ran, 2), path=round(worst["path"], 2))

    def test_holds_at_every_step(self):
        # Every step holds for the chosen time while its dot fills, then the film plays on and the marker travels: a
        # case on the run page (its data read shown in the film's frame), the walkthrough, a variation row; at three
        # widths, with animation frames and without (a background pane).
        plays = [(self.ONE, ".sb", 1280, 800, True, None, False), (self.ONE, ".sb", 800, 465, False, 1.5, False),
                 (self.ONE, ".sb", 400, 800, True, 1.5, False), (self.SUMMARY, "section.walkthrough .sb", 800, 465, False, 1.5, False),
                 (self.SUMMARY, "section.walkthrough .sb", 400, 800, True, 1.5, False),
                 (self.SUMMARY, ".vopen:not([hidden]) .sb", 1280, 800, False, 1.5, True)]
        for path, root, width, height, frames, hold, row in plays:
            with self.subTest(path=path, root=root, width=width, frames=frames, hold=hold):
                page = self.player(path, width, height, frames, hold=hold, wait=None)
                got, spans, whole = self.play_through(page, root, row=row)
                if root == ".sb":
                    # The data read is the first step: its scene fills the film's frame before play and while it holds.
                    self.assertEqual(got[0]["scene"], [1])
                    self.assertTrue(all(x["scene"] == [1] for x in got if x["step"] == 1 and x["w"] < spans[0][1] - 0.05))
                    self.assertTrue(all(not x["scene"] for x in got if x["step"] > 1))
                got_ = self.assert_holds(got, spans, whole, (path, root, width, frames, hold))
                self.assertTrue(all(abs(h_ - (hold or 3)) < 0.15 for h_ in got_["holds"][1:]), got_)
                self.assertEqual(page.problems, [])

    def test_steps_before_the_film_each_hold_in_the_film(self):
        # A walkthrough that read two things first: each read is its own step, shown in the film's frame for its hold.
        reads = [{"tool": "make_feature", "input": {"claims": 2}, "returned": {"feature": "cart"}},
                 {"tool": "list_runs", "input": {"feature": "cart"}, "returned": {"runs": ["r1", "r2"]}}]
        d = doc("review-page-claims")
        d["walkthrough"]["starts_from"] = reads
        body = json.dumps({"id": "review-page-claims", "complete": True, "files_base": "/v1/runs/review-page-claims/files/", "document": d})
        page = self.player(None, 800, 465, frames=False, hold=1.5)
        page.route(re.compile(r".*/v1/runs/review-page-claims$"), lambda r: r.fulfill(status=200, content_type="application/json", body=body))
        page.goto(self.base + self.SUMMARY)
        root = "section.walkthrough .sb"
        got, spans, whole = self.play_through(page, root)
        self.assertEqual(len(spans), 11)                       # nine steps, and a step for each read
        self.assertEqual(got[0]["scene"], [1])
        for i in (1, 2):
            during = [x for x in got if x["step"] == i and spans[i - 1][0] + 0.05 < x["w"] < spans[i - 1][1] - 0.05]
            self.assertTrue(during and all(x["scene"] == [i] for x in during), (i, [x["scene"] for x in during][:5]))
        self.assertIn("$ list-runs --feature cart", page.locator(f"{root} .scene").nth(1).inner_text())
        result = self.assert_holds(got, spans, whole, "two reads")
        self.assertGreaterEqual(result["travels"], 0.4 - 1e-3)   # from the first read to the second: no film between them

    def test_pause_in_the_middle_of_a_hold(self):
        for width, height, frames in [(1280, 800, True), (400, 800, False)]:
            with self.subTest(width=width, frames=frames):
                self._pause_in_the_middle_of_a_hold(width, height, frames)

    def _pause_in_the_middle_of_a_hold(self, width, height, frames):
        page = self.player(self.ONE, width, height, frames)
        self.ready(page)
        page.locator(".sb .play").click()
        page.wait_for_function("(() => { const f = getComputedStyle(document.querySelectorAll('.sb-fill')[1]).transform; return f !== 'none' && Number(f.slice(7, -1).split(',')[3]) > 0.3; })()", polling=25, timeout=15000)
        page.get_by_role("button", name="Pause the film", exact=True).click()
        read = """() => { const sb = document.querySelector('.sb'); const f = getComputedStyle(sb.querySelectorAll('.sb-fill')[1]).transform;
          return { fill: Number(f.slice(7, -1).split(',')[3]), t: sb.querySelector('video').currentTime, w: Number(sb.dataset.watched), step: Number(sb.dataset.step) }; }"""
        a = page.evaluate(read)
        page.wait_for_timeout(800)
        b = page.evaluate(read)
        self.assertEqual((a["step"], a["fill"], a["t"], a["w"]), (b["step"], b["fill"], b["t"], b["w"]))     # frozen
        self.assertTrue(0.3 < a["fill"] < 0.9, a)
        page.get_by_role("button", name="Play the film", exact=True).click()
        t0 = page.evaluate("performance.now()")
        page.wait_for_function("(() => { const f = getComputedStyle(document.querySelectorAll('.sb-fill')[1]).transform; return f === 'none' || Number(f.slice(7, -1).split(',')[3]) > 0.999; })()", polling=20, timeout=10000)
        took = (page.evaluate("performance.now()") - t0) / 1000
        self.assertLess(abs(took - (1 - a["fill"]) * 3), 0.2, (took, a))      # it carried on from where it was

    def test_pause_at_each_step(self):
        # "Pause at each step": the film stops at every step, and goes on with play, the next-step button, or space.
        page = self.player(self.ONE, 800, 465, frames=False)
        self.ready(page)
        page.get_by_role("button", name="Pause at each step", exact=True).click()
        self.assertEqual(page.locator(".sb-stop").get_attribute("aria-pressed"), "true")
        self.assertEqual(page.evaluate("document.querySelector('.sb').dataset.step"), "1")
        page.locator(".sb .play").click()                   # play goes on from the first step, and stops at the next
        seen = []
        ways = ["play", "next", "space"]
        for k in range(2, 11):
            page.wait_for_function(f"document.querySelector('.sb').dataset.hold === 'waiting' && Number(document.querySelector('.sb').dataset.step) === {k}", polling=50, timeout=10000)
            a = page.evaluate("[document.querySelector('.sb').dataset.watched, document.querySelector('.sb video').currentTime]")
            page.wait_for_timeout(300)
            self.assertEqual(a, page.evaluate("[document.querySelector('.sb').dataset.watched, document.querySelector('.sb video').currentTime]"))
            self.assertTrue(page.evaluate(f"document.querySelectorAll('.sb-dot')[{k - 1}].classList.contains('waiting')"))
            seen.append(k)
            way = ways[k % 3]
            if way == "play":
                page.get_by_role("button", name="Play the film", exact=True).click()
            elif way == "next":
                page.get_by_role("button", name="Next step", exact=True).click()
            else:
                page.locator(".sb").focus()
                page.keyboard.press("Space")
        page.wait_for_function("!document.querySelector('.sb-list').hidden", polling=100, timeout=10000)
        self.assertEqual(seen, list(range(2, 11)))

    def test_previous_and_next_step(self):
        for width, height, frames in [(1280, 800, True), (400, 800, False)]:
            with self.subTest(width=width, frames=frames):
                self._previous_and_next_step(width, height, frames)

    def _previous_and_next_step(self, width, height, frames):
        page = self.player(self.ONE, width, height, frames)
        self.ready(page)
        state = """() => { const sb = document.querySelector('.sb'); const k = Number(sb.dataset.step);
          const f = getComputedStyle(sb.querySelectorAll('.sb-fill')[k - 1]).transform;
          return { step: k, w: Number(sb.dataset.watched), from: Number(sb.querySelectorAll('.sb-dot')[k - 1].dataset.hold.split(',')[0]),
                   fill: f === 'none' ? 1 : Number(f.slice(7, -1).split(',')[3]) }; }"""
        nxt = page.get_by_role("button", name="Next step", exact=True)
        prv = page.get_by_role("button", name="Previous step", exact=True)
        for click, want in [(nxt, 2), (nxt, 3), (prv, 2)]:
            click.click()
            x = page.evaluate(state)
            self.assertEqual(x["step"], want)
            self.assertAlmostEqual(x["w"], x["from"], delta=0.01)       # the start of that step's hold
            self.assertLess(x["fill"], 0.01)
        page.locator(".sb").focus()
        page.keyboard.press("ArrowRight")
        self.assertEqual(page.evaluate(state)["step"], 3)
        page.keyboard.press("ArrowLeft")
        self.assertEqual(page.evaluate(state)["step"], 2)
        # While playing, next lands at the start of the next hold and carries on filling it.
        page.get_by_role("button", name="Play the film", exact=True).click()
        nxt.click()
        x = page.evaluate(state)
        self.assertEqual(x["step"], 3)
        page.wait_for_timeout(600)
        y = page.evaluate(state)
        self.assertEqual(y["step"], 3)
        self.assertGreater(y["fill"], 0.1)

    def test_scrub_into_a_hold(self):
        for width, height, frames in [(1280, 800, True), (400, 800, False)]:
            with self.subTest(width=width, frames=frames):
                self._scrub_into_a_hold(width, height, frames)

    def _scrub_into_a_hold(self, width, height, frames):
        page = self.player(self.ONE, width, height, frames)
        self.ready(page)
        length = page.evaluate("Number(document.querySelector('.sb').dataset.length)")
        a, b = page.evaluate("document.querySelectorAll('.sb-dot')[4].dataset.hold.split(',').map(Number)")
        page.evaluate(f"(() => {{ const r = document.querySelector('.track input'); r.value = String(Math.round({(a + 0.4 * (b - a)) / length} * 1000)); r.dispatchEvent(new Event('input')); }})()")
        page.wait_for_function("!document.querySelector('.sb video').seeking", polling=50)
        x = page.evaluate("""() => { const sb = document.querySelector('.sb'); const f = getComputedStyle(sb.querySelectorAll('.sb-fill')[4]).transform;
          const r = sb.querySelector('.sb-rail').getBoundingClientRect(), m = sb.querySelector('.sb-marker').getBoundingClientRect(), d = sb.querySelectorAll('.sb-dot')[4].getBoundingClientRect();
          return { step: Number(sb.dataset.step), card: Number(sb.querySelector('.sb-layer.on').dataset.n), fill: Number(f.slice(7, -1).split(',')[3]),
                   t: sb.querySelector('video').currentTime, off: (m.top + m.height / 2) - (d.top + d.height / 2) }; }""")
        self.assertEqual((x["step"], x["card"]), (5, 5))
        self.assertAlmostEqual(x["fill"], 0.4, delta=0.03)
        self.assertAlmostEqual(x["t"], 2.5, delta=0.03)                 # the film stands on step 5's moment
        self.assertLess(abs(x["off"]), 1)

    def test_changing_the_hold_in_the_middle_of_one(self):
        for width, height, frames in [(1280, 800, True), (400, 800, False)]:
            with self.subTest(width=width, frames=frames):
                self._changing_the_hold_in_the_middle_of_one(width, height, frames)

    def _changing_the_hold_in_the_middle_of_one(self, width, height, frames):
        page = self.player(self.ONE, width, height, frames)
        self.ready(page)
        page.locator(".sb .play").click()
        fill = "(() => { const f = getComputedStyle(document.querySelectorAll('.sb-fill')[2]).transform; return f === 'none' ? 1 : Number(f.slice(7, -1).split(',')[3]); })()"
        page.wait_for_function(f"{fill} > 0.4", polling=25, timeout=20000)
        hold_btn = page.locator(".sb-hold")
        self.assertEqual(hold_btn.inner_text(), "Hold 3 s")
        before = page.evaluate(fill)
        hold_btn.click()
        after = page.evaluate(fill)
        self.assertEqual(hold_btn.inner_text(), "Hold 1.5 s")
        self.assertEqual(page.evaluate("document.querySelector('.sb').dataset.step"), "3")
        self.assertLess(abs(after - before), 0.05)                     # the same step, its hold as far along
        self.assertEqual(page.evaluate("sessionStorage.getItem('gt-sb-hold')"), "1.5")
        t0 = page.evaluate("performance.now()")
        page.wait_for_function(f"{fill} > 0.999", polling=20, timeout=10000)
        self.assertLess((page.evaluate("performance.now()") - t0) / 1000, (1 - after) * 1.5 + 0.25)
        hold_btn.click()
        self.assertEqual(hold_btn.inner_text(), "No hold")
        hold_btn.click()
        self.assertEqual(hold_btn.inner_text(), "Hold 3 s")

    def test_playing_across_a_cut_wait(self):
        # A wait of 3.9 seconds is cut from the film: the film jumps, but the watched clock, the marker and the scrub
        # bar go on at their pace, and every step still holds.
        def gap(d):
            for s_ in d["properties"][2]["cases"][1]["steps"]:
                if s_.get("at") is not None and 0.5 < s_["at"] < 4.6:
                    s_["at"] = None
        for width, height, frames in [(800, 465, True), (400, 800, False)]:
            with self.subTest(width=width, frames=frames):
                page = self.player(None, width, height, frames, hold=1.5)
                d = doc(FILMED)
                gap(d)
                body = json.dumps({"id": "patched", "complete": True, "files_base": f"/v1/runs/{FILMED}/files/", "document": d})
                page.route(re.compile(r".*/v1/runs/patched$"), (lambda b: lambda r: r.fulfill(status=200, content_type="application/json", body=b))(body))
                page.goto(self.base + "run.html?run=patched&steps=1#p3/c2")
                got, spans, whole = self.play_through(page)
                self.assertTrue(any(1.0 < y["t"] - x["t"] for x, y in zip(got, got[1:])), "the wait was not cut")
                self.assert_holds(got, spans, whole, ("cut", width, frames))
                # Dragging the scrub bar to its middle is the middle of the watched clock: never inside the cut wait.
                page.evaluate("""() => { const r = document.querySelector('.track input'); r.value = '500'; r.dispatchEvent(new Event('input')); }""")
                page.wait_for_function("!document.querySelector('.sb video').seeking", polling=100)
                x = page.evaluate("[Number(document.querySelector('.sb').dataset.watched), document.querySelector('.sb video').currentTime]")
                self.assertAlmostEqual(x[0], whole[0] / 2, delta=0.01 * whole[0])
                self.assertFalse(0.07 < x[1] < 3.95, x)

    def test_playing_through_a_resize(self):
        # The window goes from wide to phone width in the middle of a play: the rail turns into a row, and the marker
        # stays where the clock says the whole time.
        page = self.player(self.ONE, hold=1.5)
        def resize(p):
            p.wait_for_function("Number(document.querySelector('.sb').dataset.step) >= 4", polling=100, timeout=20000)
            p.set_viewport_size({"width": 400, "height": 800})
        got, spans, whole = self.play_through(page, during=resize)
        self.assertGreater(abs(got[0]["dots"][-1] - got[-1]["dots"][-1]), 1, "the rail did not change")
        self.assert_holds(got, spans, whole, "resize", resized=True)

    def test_old_card_goes_when_fades_never_run(self):
        # Where a fade never advances (a page that gets no frames), the card that was current must still go: it can
        # never stay on top of the new one. Fades are frozen here with the browser's own animation control.
        page = self.open(self.ONE, wait=".sb video")
        cdp = page.context.new_cdp_session(page)
        cdp.send("Animation.enable")
        cdp.send("Animation.setPlaybackRate", {"playbackRate": 0})
        page.wait_for_function("document.querySelector('.sb video').readyState >= 1", polling=100)
        page.get_by_role("button", name=re.compile(r"^Step 7: ")).click()
        page.wait_for_timeout(50)
        shown = "[...document.querySelectorAll('.sb-layer')].filter((e) => getComputedStyle(e).visibility === 'visible').map((e) => e.dataset.n)"
        self.assertIn("7", page.evaluate(shown))                                 # the new card is shown at once
        page.wait_for_timeout(600)
        self.assertEqual(page.evaluate(shown), ["7"])

    def test_choosing_a_step_puts_the_marker_on_its_dot(self):
        for width, height in [(1280, 800), (400, 800)]:
            for path, root in [(self.ONE, ".sb"), (self.SUMMARY, "section.walkthrough .sb")]:
                with self.subTest(path=path, width=width):
                    page = self.open(path, width=width, height=height, wait=f"{root} video")
                    page.wait_for_function(f"document.querySelector('{root} video').readyState >= 1")
                    got = page.evaluate("""async (root) => {
                      const sb = document.querySelector(root), v = sb.querySelector('video'), rail = sb.querySelector('.sb-rail');
                      const dots = [...sb.querySelectorAll('.sb-dot')], marker = sb.querySelector('.sb-marker');
                      const at = (e) => { const r = rail.getBoundingClientRect(), b = e.getBoundingClientRect(), hz = r.width > r.height;
                        return hz ? b.left + b.width / 2 - r.left : b.top + b.height / 2 - r.top; };
                      const out = [];
                      for (const [i, d] of dots.entries()) {
                        d.click();
                        await new Promise((r) => setTimeout(r, 120));
                        out.push({ n: i + 1, step: Number(sb.dataset.step), off: at(marker) - at(d), at: d.dataset.frame, t: v.currentTime });
                      }
                      return out; }""", root)
                    for x in got:
                        self.assertEqual(x["step"], x["n"], x)
                        self.assertLess(abs(x["off"]), 1, x)
                        if x["at"] != "before":
                            self.assertAlmostEqual(x["t"], float(x["at"]), delta=0.03)

    def test_reduced_motion(self):
        # Asked for less motion: the stage swaps its card at once, a holding dot shows a still "holding" state instead
        # of a rising fill and is full after, and the list does not fade in at the end.
        page = self.player(self.ONE, hold=1.5, reduced=True)
        self.ready(page)
        self.assertEqual(set(page.evaluate("[...document.querySelectorAll('.sb-layer, .sb-dot')].map((e) => getComputedStyle(e).transitionDuration)")), {"0s"})
        page.get_by_role("button", name=re.compile(r"^Step 7: ")).click()
        shown = page.evaluate("[...document.querySelectorAll('.sb-layer')].filter((e) => getComputedStyle(e).visibility === 'visible').map((e) => e.dataset.n)")
        self.assertEqual(shown, ["7"])
        page.get_by_role("button", name="Play the film", exact=True).click()
        page.wait_for_timeout(500)
        x = page.evaluate("""() => { const d = document.querySelectorAll('.sb-dot')[6]; const f = getComputedStyle(d.querySelector('.sb-fill')).transform;
          return [d.classList.contains('holding'), f === 'none' ? 1 : Number(f.slice(7, -1).split(',')[3]), document.querySelector('.sb').dataset.hold]; }""")
        self.assertEqual(x, [True, 0, "holding"])
        page.wait_for_function("document.querySelector('.sb').dataset.step === '8'", polling=50, timeout=10000)
        self.assertEqual(page.evaluate("getComputedStyle(document.querySelectorAll('.sb-fill')[6]).transform"), "matrix(1, 0, 0, 1, 0, 0)")
        page.evaluate("document.querySelector('.sb video').currentTime = 6.6")
        self.expect(page.locator(".sb-list")).to_be_visible(timeout=8000)
        self.assertEqual(page.evaluate("getComputedStyle(document.querySelector('.sb-list')).animationName"), "none")

    def test_no_film_shows_the_list(self):
        page = self.open("run.html?run=review-page-claims#p2/week-old")
        self.assertEqual(page.locator(".sb").count(), 0)          # the run page keeps its own sheet for a case with no film
        summary = self.open(self.SUMMARY, wait=".claim")
        summary.locator(".claim").nth(1).locator(".vrow").nth(1).locator("button.vhead").click()
        sb = summary.locator(".vopen:not([hidden]) .sb.nofilm")
        self.assertEqual(sb.locator("video").count(), 0)
        self.assertTrue(sb.locator(".sb-list").is_visible())
        self.assertIn("Reported that this variation needs your decision.", sb.locator(".sb-list").inner_text())

    # ---------- learning from real use: feedback, a claim's source, what was asked, what changed ----------

    def stored(self, page, run, prop, case=None):
        """The feedback the store keeps (the dev server's memory), read without going through the page."""
        return [x for x in self.server.RequestHandlerClass.feedback.list(run) if x["property"] == prop and x["case"] == case]

    def until_stored(self, run, test, timeout=10):
        import time
        end = time.time() + timeout
        while time.time() < end:
            if test(self.server.RequestHandlerClass.feedback.list(run)):
                return
            time.sleep(0.05)
        self.fail(f"the store never had it: {self.server.RequestHandlerClass.feedback.list(run)}")

    def test_feedback_on_a_claim(self):
        page = self.open(self.SUMMARY, wait=".claim")
        claim = page.locator(".claim").nth(1)                       # "Regressions are listed", from the newer run
        name = "the claim “Regressions are listed”"
        k = lambda word: claim.get_by_role("button", name=f"{word}: {name}", exact=True)
        for word in ("Not what I meant", "This result is wrong", "This is right"):
            self.assertEqual(k(word).get_attribute("aria-pressed"), "false")
        # Choosing one saves it at once, with the run the result came from, and offers a note.
        box = claim.locator(":scope > .fb")
        k("Not what I meant").click()
        self.expect(k("Not what I meant")).to_have_attribute("aria-pressed", "true")
        self.expect(box).to_have_attribute("data-chosen", "not_what_i_meant")       # saved
        self.assertEqual([(x["kind"], x["note"]) for x in self.stored(page, "review-page-claims", "p2")], [("not_what_i_meant", "")])
        note = claim.get_by_role("textbox", name=f"A note on {name}")
        self.expect(note).to_be_visible()
        # The note saves when the person presses Enter, and again when they leave the field.
        note.fill("I meant the regressions of this feature only")
        note.press("Enter")
        self.expect(note).to_have_value("I meant the regressions of this feature only")
        self.until_stored("review-page-claims", lambda xs: any(x["property"] == "p2" and x["note"].startswith("I meant") for x in xs))
        note.fill("Only this feature's regressions")
        note.press("Tab")
        self.until_stored("review-page-claims", lambda xs: any(x["property"] == "p2" and x["note"] == "Only this feature's regressions" for x in xs))
        self.assertEqual([(x["kind"], x["note"]) for x in self.stored(page, "review-page-claims", "p2")], [("not_what_i_meant", "Only this feature's regressions")])
        # Choosing another replaces it (the note goes with it); choosing the same again removes it.
        k("This is right").click()
        self.expect(k("This is right")).to_have_attribute("aria-pressed", "true")
        self.assertEqual(k("Not what I meant").get_attribute("aria-pressed"), "false")
        self.until_stored("review-page-claims", lambda xs: [x["kind"] for x in xs if x["property"] == "p2" and x["case"] is None] == ["right"])
        self.assertEqual([(x["kind"], x["note"]) for x in self.stored(page, "review-page-claims", "p2")], [("right", "Only this feature's regressions")])
        k("This is right").click()
        self.expect(k("This is right")).to_have_attribute("aria-pressed", "false")
        self.expect(box).to_have_attribute("data-chosen", "")
        self.expect(note).to_be_hidden()
        self.assertEqual(self.stored(page, "review-page-claims", "p2"), [])
        # A claim whose result came from the older run keeps its feedback with that run; by keyboard alone.
        third = page.locator(".claim").nth(2)
        b = third.get_by_role("button", name=re.compile(r"^This result is wrong: the claim "))
        b.focus()
        page.keyboard.press("Enter")
        self.expect(b).to_have_attribute("aria-pressed", "true")
        self.expect(third.locator(":scope > .fb")).to_have_attribute("data-chosen", "wrong_result")
        self.assertEqual([x["kind"] for x in self.stored(page, FILMED, "p3")], ["wrong_result"])
        self.assertEqual(self.stored(page, "review-page-claims", "p3"), [])
        self.assert_unique_names(page)
        self.assertEqual(page.problems, [])

    def test_feedback_on_a_variation(self):
        page = self.open(self.SUMMARY, wait=".claim")
        claim = page.locator(".claim").nth(1)
        row = claim.locator(".vrow").nth(0)
        # Feedback already stored loads with the page: a closed row says it in one quiet line.
        self.expect(row.locator(".vfb")).to_have_text("You said this result is wrong.")
        self.expect(page.locator(".claim").nth(0).get_by_role("button", name=re.compile(r"^This is right: the claim "))).to_have_attribute("aria-pressed", "true")
        row.locator("button.vhead").click()
        name = "the variation “One regression was run”"
        k = lambda word: row.get_by_role("button", name=f"{word}: {name}", exact=True)
        self.expect(k("This result is wrong")).to_have_attribute("aria-pressed", "true")
        self.expect(row.get_by_role("textbox", name=f"A note on {name}")).to_have_value("It is listed when I open the page myself.")
        box = row.locator(".fb")
        k("This is right").click()
        self.expect(k("This is right")).to_have_attribute("aria-pressed", "true")
        self.expect(box).to_have_attribute("data-chosen", "right")
        self.until_stored("review-page-claims", lambda xs: len([x for x in xs if x["case"] == "one-regression"]) == 1)
        self.assertEqual([(x["kind"], x["note"]) for x in self.stored(page, "review-page-claims", "p2", "one-regression")],
                         [("right", "It is listed when I open the page myself.")])
        k("This is right").click()
        self.expect(k("This is right")).to_have_attribute("aria-pressed", "false")
        self.expect(box).to_have_attribute("data-chosen", "")
        self.assertEqual(self.stored(page, "review-page-claims", "p2", "one-regression"), [])
        row.locator("button.vhead").click()
        self.expect(row.locator(".vfb")).to_have_text("")
        # The run page has the same controls for the claim and for the variation shown.
        run = self.open("run.html?run=review-page-claims#p1/one-claim")
        v = run.get_by_role("button", name="Not what I meant: the variation “A feature with one claim”", exact=True)
        self.expect(run.get_by_role("button", name="This is right: the claim “Claims each get a card”", exact=True)).to_have_attribute("aria-pressed", "true")
        v.click()
        self.expect(v).to_have_attribute("aria-pressed", "true")
        self.expect(run.locator(".stagecol .fb")).to_have_attribute("data-chosen", "not_what_i_meant")
        self.assertEqual([x["kind"] for x in self.stored(run, "review-page-claims", "p1", "one-claim")], ["not_what_i_meant"])
        self.assert_unique_names(run)

    def test_feedback_that_cannot_be_saved(self):
        for how in ("refused", "unreachable"):
            with self.subTest(how=how):
                page = self.open(self.SUMMARY, wait=".claim")
                def denier(how):
                    def deny(route):
                        if route.request.method == "GET":
                            return route.continue_()
                        if how == "refused":
                            return route.fulfill(status=500, content_type="application/json", body=json.dumps({"error": "The store is read-only today."}))
                        return route.abort()
                    return deny
                page.route(re.compile(r".*/v1/runs/[^/]+/feedback.*"), denier(how))
                b = page.locator(".claim").nth(1).get_by_role("button", name=re.compile(r"^This result is wrong: the claim "))
                b.click()
                said = page.locator(".claim").nth(1).locator(".fb-said")
                self.expect(said).to_have_text("Not saved: The store is read-only today." if how == "refused" else "Not saved: the results store could not be reached.")
                self.expect(b).to_have_attribute("aria-pressed", "false")         # it does not pretend
                self.assertEqual([x["id"] for x in self.server.RequestHandlerClass.feedback.list("review-page-claims")], ["seed0001", "seed0002"])

    def test_where_a_claim_came_from(self):
        page = self.open(self.SUMMARY, wait=".claim")
        claims = page.locator(".claim")
        # owner/repo#n becomes a GitHub link, under the claim's own words.
        one = claims.nth(0).locator(".source")
        self.assertEqual(one.inner_text(), "From issue #786")
        self.assertEqual(one.locator("a").get_attribute("href"), "https://github.com/acme/review-app/issues/786")
        self.assertTrue(claims.nth(0).evaluate("c => c.querySelector('.said').nextElementSibling.classList.contains('source')"))
        # A pull request's address keeps its own link; with no words, the line sits under the title.
        two = claims.nth(1).locator(".source")
        self.assertEqual(two.inner_text(), "From pull request #12")
        self.assertEqual(two.locator("a").get_attribute("href"), "https://github.com/acme/review-app/pull/12")
        # Words with no address stay plain text.
        self.assertEqual(claims.nth(2).locator(".source").inner_text(), "From an interview")
        self.assertEqual(claims.nth(2).locator(".source a").count(), 0)
        self.assertEqual(page.locator(".claim .source").count(), 3)
        run = self.open("run.html?run=review-page-claims#p1/one-claim")
        self.assertEqual(run.locator(".head .source").inner_text(), "From issue #786")
        # Every form a ref can take.
        got = page.evaluate("""async () => { const { sourceLine } = await import('/js/learn.js');
          const line = (kind, ref) => { const el = sourceLine({ source: { kind, ref, quote: '' } }); if (!el) return null; const a = el.querySelector('a');
            return [el.textContent, a ? a.getAttribute('href') : null]; };
          return [line('issue', 'acme/app#5'), line('pull request', 'acme/app#6'), line('issue', 'https://github.com/acme/app/issues/7'),
                  line('issue', 'see https://linear.app/acme/issue/ABC-9 for more'), line('issue', '#786'), line('issue', ''), line('pull request', ''),
                  line('interview', ''), line('interview', 'https://example.com/notes'), line('other', 'a call with Ana'), line('other', ''),
                  line('issue', 'javascript:alert(1)'), sourceLine({}), sourceLine({ source: null })]; }""")
        self.assertEqual(got, [["From issue #5", "https://github.com/acme/app/issues/5"], ["From pull request #6", "https://github.com/acme/app/pull/6"],
                               ["From issue #7", "https://github.com/acme/app/issues/7"], ["From issue linear.app/acme/issue/ABC-9", "https://linear.app/acme/issue/ABC-9"],
                               ["From issue #786", None], ["From an issue", None], ["From a pull request", None], ["From an interview", None],
                               ["From an interview", "https://example.com/notes"], ["From a call with Ana", None], None,
                               ["From issue javascript:alert(1)", None], None, None])

    def test_what_was_asked_and_what_changed(self):
        page = self.open(self.SUMMARY, wait=".claim")
        asked = page.locator("details.asked")
        self.assertFalse(asked.evaluate("d => d.open"))                       # closed until opened
        asked.locator("summary").click()
        self.assertEqual(asked.locator("summary").inner_text(), "What was asked for")
        pr = asked.get_by_role("link", name="Pull request #12: Review page: one card per claim")
        self.assertEqual(pr.get_attribute("href"), "https://github.com/acme/review-app/pull/12")
        self.assertEqual(asked.get_by_role("link", name="Issue #786: Claims should each get a card").get_attribute("href"),
                         "https://github.com/acme/review-app/issues/786")
        # Their text is shown as it was written: markdown and tags stay text.
        self.assertIn("**Bold in markdown** and <b>a tag</b> stay as plain text.", asked.inner_text())
        self.assertIn("<script>alert(1)</script>", asked.inner_text())
        self.assertEqual(asked.locator("b, script, strong").count(), 0)
        changes = page.locator("details.changes")
        self.assertFalse(changes.evaluate("d => d.open"))
        self.assertEqual(changes.locator("summary").inner_text(), "Changed before approval (4)")
        changes.locator("summary").click()
        items = changes.locator("li")
        self.assertEqual(items.count(), 4)
        self.assertEqual(items.nth(0).locator("p").first.inner_text(), "Variations of “Claims each get a card” were edited.")
        self.assertIn("Before: A feature with one claim", items.nth(0).inner_text())
        self.assertIn("After: A feature with one claim; a feature with three claims", items.nth(0).inner_text())
        self.assertEqual(items.nth(1).locator("p").first.inner_text(), "The title of “Regressions are listed” was edited.")
        self.assertEqual(items.nth(2).inner_text(), "A claim was added: “Clicking a claim's card shows the video for that claim”.")
        self.assertEqual(items.nth(3).inner_text(), "A claim was removed: “Each card shows when it ran”.")
        # The set-up record is never shown.
        self.assertNotIn("connect", page.locator("main").inner_text().lower().replace("connected", ""))
        # The run page has both in its run panel.
        run = self.open("run.html?run=review-page-claims#p1/one-claim")
        self.assertEqual(run.locator(".run details.asked summary").inner_text(), "What was asked for")
        self.assertEqual(run.locator(".run details.changes summary").inner_text(), "Changed before approval (4)")

    def test_nothing_new_for_a_run_without_them(self):
        def bare(d):
            for key in ("context", "draft_changes", "setup"):
                d.pop(key, None)
            for p in d["properties"]:
                p.pop("source", None)
        page = self.serve_claims(bare)
        self.assertEqual(page.locator(".source, details.learn, .learned").count(), 0)
        def empty(d):
            d["context"] = None
            d["draft_changes"] = []
            for p in d["properties"]:
                p["source"] = None
        page = self.serve_claims(empty)
        self.assertEqual(page.locator(".source, details.learn, .learned").count(), 0)
        run = self.open(f"run.html?run={FILMED}#p3/c2")
        self.assertEqual(run.locator(".source, details.learn").count(), 0)

    # ---------- summary polish ----------

    def summary_with_older(self, ran_at):
        older = doc(FILMED)
        older["ran_at"] = ran_at
        body = json.dumps({"id": FILMED, "complete": True, "files_base": f"/v1/runs/{FILMED}/files/", "document": older})
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.route(re.compile(rf".*/v1/runs/{FILMED}$"), lambda r: r.fulfill(status=200, content_type="application/json", body=body))
        page.goto(self.base + self.SUMMARY)
        page.wait_for_selector(".claim")
        return page

    def test_tested_line_only_when_an_hour_older(self):
        newest = doc("review-page-claims")["ran_at"]          # 2026-11-11T10:15:00
        self.assertEqual(newest, "2026-11-11T10:15:00")
        page = self.summary_with_older("2026-11-11T09:30:00")
        self.assertEqual(page.locator(".cwhen").count(), 0)
        page = self.summary_with_older("2026-11-11T09:14:00")
        self.assertEqual(page.locator(".cwhen").all_inner_texts(), ["Tested Nov 11, 2026, 9:14 AM."])

    def test_long_reason_has_more(self):
        page = self.open(self.SUMMARY, wait=".claim")
        row = page.locator(".claim").nth(2).locator(".vrow").nth(3)          # outside the wording, with a long note
        why = row.locator(".why")
        full = doc(FILMED)["properties"][2]["cases"][3]["observed"]
        self.assertGreater(len(full), 200)
        shown = why.locator(":scope > span").inner_text()
        self.assertLessEqual(len(shown), 202)
        self.assertTrue(shown.startswith("It is outside the claim's wording: "))
        more = why.locator("details.more")
        self.assertFalse(more.evaluate("d => d.open"))
        self.assertEqual(more.locator("summary").inner_text(), "More")
        more.locator("summary").click()
        self.assertIn(full.split(". ")[-1][:40], more.inner_text())
        # A short reason is shown whole, with no "More".
        short = page.locator(".claim").nth(1).locator(".vrow").nth(1).locator(".why")
        self.assertEqual(short.inner_text(), "The page hides regressions older than a week. Is that intended?")

    # ---------- films without dead time ----------

    def test_trimmed_film(self):
        # The film with its steps (&steps=1): its clock has a hold at each step.
        page = self.open(f"run.html?run={FILMED}&steps=1#p3/c2")
        got = page.evaluate("""async () => {
          const { filmEnd, skipTo, timeline } = await import('/js/film.js');
          const c = { steps: [{ clip: 0, at: 1 }, { clip: 0, at: 2 }, { clip: 0, at: 8 }, { clip: 0, at: null }, { clip: 1, at: 30 }] };
          const tl = timeline(c, 0, 20);
          const late = timeline({ steps: [{ clip: 0, at: 6 }, { clip: 0, at: 7 }] }, 0, 20);
          return { end: filmEnd(c, 0, 20), short: filmEnd(c, 0, 8.5), guess: filmEnd(c, 0, 0), none: filmEnd({ steps: [] }, 0, 12),
                   skip: skipTo(c, 0, 2.1), near: skipTo(c, 0, 6), before: skipTo(c, 0, 0), after: skipTo(c, 0, 8.2),
                   cuts: tl.cuts, length: tl.length, played: [3, 7.3, 8, 9.5].map((t) => +tl.played(t).toFixed(3)),
                   filmAt: [1.5, 2, 2.7, 4.2].map((p) => +tl.filmAt(p).toFixed(3)), late: [late.cuts, late.skip(0), late.filmAt(0)] };
        }""")
        # A wait longer than 2.5 seconds is cut, from the step before it to 0.7 seconds before the step after it, also
        # from a moment inside it; the watched clock stands still across the cut, and the watched time maps back to film
        # time on the far side of it. A film whose first step is late starts 0.7 seconds before that step.
        self.assertEqual(got, {"end": 9.5, "short": 8.5, "guess": 9.5, "none": 12, "skip": 7.3, "near": 7.3, "before": None, "after": None,
                               "cuts": [[2, 7.3]], "length": 4.2, "played": [2, 2, 2.7, 4.2], "filmAt": [1.5, 7.3, 8, 9.5],
                               "late": [[[0, 5.3]], 5.3, 5.3]})
        page.wait_for_function("document.querySelector('.film video').readyState >= 1")
        whole = page.evaluate("document.querySelector('.film video').duration")
        last = max(s["at"] for s in doc(FILMED)["properties"][2]["cases"][1]["steps"] if s.get("at") is not None)
        end = min(whole, last + 1.5)
        clock = f"{int(end) // 60}:{int(end) % 60:02d}"
        self.expect(page.locator(".vline[data-cid=c2] .dur")).to_have_text(clock)
        # The time beside the scrub bar is the watched clock: the trimmed film with a hold at each step.
        page.wait_for_function(f"Math.abs(Number(document.querySelector('.sb').dataset.film) - {end}) < 0.01")
        length, steps_ = page.evaluate("[Number(document.querySelector('.sb').dataset.length), document.querySelectorAll('.sb-dot').length]")
        self.assertAlmostEqual(length, end + 3 * steps_, delta=0.41 * steps_)
        self.expect(page.locator(".time")).to_have_text(re.compile(rf"/ {int(length) // 60}:{int(length) % 60:02d}$"))
        # Dragging the timeline to its end reaches the trimmed end, not the film's own end.
        page.evaluate("""() => { const r = document.querySelector('.track input'); r.value = '1000'; r.dispatchEvent(new Event('input')); }""")
        page.wait_for_function(f"Math.abs(document.querySelector('.film video').currentTime - {end}) < 0.1")
        # Played from a moment just before the end, it stops at the trimmed end.
        page.evaluate(f"document.querySelector('.film video').currentTime = {end - 0.6}")
        page.get_by_role("button", name="Play the film", exact=True).click()
        page.wait_for_function("!document.querySelector('.film video').paused")
        page.wait_for_function("document.querySelector('.film video').paused", timeout=4000)
        self.assertLess(abs(page.evaluate("document.querySelector('.film video').currentTime") - end), 0.15)
        self.assertGreater(whole - end, 0.5, "this film has no dead time to trim, so the test shows nothing")

    def test_long_wait_is_skipped(self):
        def gap(d):
            c = d["properties"][2]["cases"][1]
            for s in c["steps"]:
                if s.get("at") is not None and 0.5 < s["at"] < 4.6:
                    s["at"] = None              # the steps between 0.07 and 4.65 are gone: a wait of 3.9 seconds
        page = self.open_patched(FILMED, gap, "#p3/c2")
        page.wait_for_function("document.querySelector('.film video').readyState >= 1")
        page.evaluate("window.__t = []; const v = document.querySelector('.film video'); setInterval(() => __t.push(v.currentTime), 50)")
        page.get_by_role("button", name="Play the film", exact=True).click()
        page.wait_for_function("document.querySelector('.film video').currentTime > 4.2")
        ts = page.evaluate("__t")
        self.assertFalse(any(1.0 < t < 3.8 for t in ts), ts)

    # ---------- the run page in the owner's words ----------

    def test_run_page_names_and_setting(self):
        page = self.open("run.html?run=review-page-claims#p1/one-claim")
        line = page.locator(".vline[data-cid=one-claim]")
        self.assertIn("A feature with one claim", line.inner_text())
        self.assertNotIn("ONE-CLAIM", page.locator("body").inner_text())
        self.assertEqual(page.locator(".vline[data-cid=three-claims]").get_attribute("aria-label"),
                         "A feature with three claims, one of them changed after approval")
        self.assertEqual(page.title(), "Claims each get a card: A feature with one claim · groundtruth-cli · Groundtruth")
        # The film opens on the data it read, typed in its terminal, so the planner's sentence is not said again.
        self.assertEqual(page.locator(".stagecol .started").count(), 0)
        # With the film's steps beside it, the sentence is said, and the exact data put in is the storyboard's first step.
        page = self.open("run.html?run=review-page-claims&steps=1#p1/one-claim")
        started = page.locator(".stagecol .started")
        self.assertEqual(started.inner_text(), "Starts from: One feature with a single approved claim.")
        self.assertIn("$ make-feature --claims 1 --approved true", page.locator(".sb-now .term").inner_text())
        page.locator(".vline[data-cid=three-claims]").click()
        self.expect(page.locator(".stagecol .started")).to_have_count(0)
        self.expect(page.locator(".sb-now .term")).to_contain_text("$ make-feature --claims 3 --changed c2")

    def test_run_page_tree_of_variations(self):
        # The left column is a tree: each claim's arrow drops its variations down, or folds them away, and does nothing else.
        page = self.open("run.html?run=review-page-claims#p2/one-regression")
        p1, p2, p3 = (page.locator(f".pgroup[data-pid={x}]") for x in ("p1", "p2", "p3"))
        url = page.url
        # Opened on a variation, its claim is dropped down; a folded claim shows how its variations came out as marks.
        self.assertIn("open", p2.get_attribute("class").split())
        self.assertIn("current", p2.get_attribute("class").split())
        self.assertTrue(p2.locator(".vlist").is_visible())
        self.assertFalse(p2.locator(".marks").is_visible())
        self.assertFalse(p1.locator(".vlist").is_visible())
        self.assertTrue(p1.locator(".marks").is_visible())
        self.assertEqual(p1.locator(".m").count(), 2)
        self.assertTrue(p3.locator(".ptw").is_disabled())           # not run: nothing to drop down
        arrow = p2.locator(".ptw")
        self.assertEqual(arrow.get_attribute("aria-label"), "The variations of Regressions are listed")
        self.assertEqual(arrow.get_attribute("aria-controls"), p2.locator(".vlist").get_attribute("id"))
        self.assertEqual(arrow.get_attribute("aria-expanded"), "true")
        # Folding the chosen claim keeps the address and the selection; its marks say which variation is shown.
        arrow.click()
        self.expect(arrow).to_have_attribute("aria-expanded", "false")
        self.assertFalse(p2.locator(".vlist").is_visible())
        self.assertTrue(p2.locator(".marks").is_visible())
        self.assertEqual(p2.locator(".m.on").get_attribute("data-cid"), "one-regression")
        self.assertEqual(page.url, url)
        self.assertEqual(p2.locator(".pcard").get_attribute("aria-current"), "true")
        self.assertEqual(p2.locator(".vline[aria-current=true]").get_attribute("data-cid"), "one-regression")
        # Unfolding another claim, then the chosen one again: both are open, and still nothing else changed.
        p1.locator(".ptw").click()
        arrow.click()
        self.expect(p1.locator(".vlist")).to_be_visible()
        self.expect(p2.locator(".vlist")).to_be_visible()
        self.assertEqual(page.url, url)
        self.assertEqual(p1.locator(".pcard").get_attribute("aria-current"), "false")
        self.assertEqual(page.locator(".vline[aria-current=true]").count(), 1)
        self.assertEqual(page.locator("main .head h2").inner_text(), "Regressions are listed")
        self.assert_unique_names(page)
        # A variation's line: its number in the claim, its sentence, and how long its film is, or a short word when
        # there is no film to time.
        filmed, asked = p2.locator(".vline[data-cid=one-regression]"), p2.locator(".vline[data-cid=week-old]")
        self.assertEqual(filmed.locator(".vn").inner_text(), "V1")
        self.assertRegex(filmed.locator(".dur").inner_text(), r"^\d+:\d\d$")
        self.assertEqual(filmed.locator(".vnote").count(), 0)
        self.assertEqual(asked.locator(".vn").inner_text(), "V2")
        self.assertEqual(asked.locator(".vnote").inner_text(), "your call")
        self.assertEqual(asked.locator(".dur").count(), 0)
        self.assertEqual(p2.locator(".vline[data-cid=from-before] .vnote").inner_text(), "outside the wording")

        # The line above the proof: which variation, of how many, its sentence and how it came out.
        def vhead(v, of, words, out):
            head = page.locator(".stagecol > .vhead")
            self.expect(head.locator(".vchip")).to_have_text(v)
            self.assertTrue(head.evaluate("e => e === e.parentElement.firstElementChild"), "the line is not first")
            self.assertEqual(head.locator(".vof").inner_text(), of)
            self.assertEqual(head.locator("p").evaluate("p => p.lastChild.textContent"), words)
            self.assertEqual(head.locator(".vout").inner_text(), out)
        vhead("V1", "of 3", "One regression was run", "Broke")
        # Opened by its address, a filmed variation is its film alone with one caption, and it does not start playing.
        sb = page.locator(".stagecol > section.sb")
        self.assertIn("captions", sb.get_attribute("class").split())
        page.wait_for_function("document.querySelector('.sb video').readyState >= 1")
        page.wait_for_timeout(300)
        self.assertEqual(sb.locator("button.play").get_attribute("aria-label"), "Play the film")
        # Its steps are a list under it, closed until asked for.
        self.assertEqual(sb.locator(".sb-all").inner_text(), "All steps (7)")
        self.assertTrue(sb.locator(".sb-all").is_visible())
        self.assertFalse(sb.locator(".sb-list").is_visible())
        # It read no data first, so the planner's sentence is said above the film.
        self.assertEqual(page.locator(".stagecol .started").count(), 1)
        # Nothing was said aloud during it, so its captions are made from its sentence and what each check compared.
        self.assertEqual(page.evaluate(self.SWEEP), [
            "|Trying: One regression was run.",
            "ok|The run page opened on the right run: as the claim says.",
            "|Trying: One regression was run.",
            "bad|Regressions listed on the run page: not what the claim says.",
            "bad|1 of 2 checks did not match."])
        # A variation with no film: the same line, and no film.
        asked.click()
        vhead("V2", "of 3", "A regression run more than a week ago", "Needs your decision")
        self.assertEqual(page.locator(".stagecol .sb").count(), 0)
        # Clicking a filmed variation starts its film. This one read data first, which its terminal shows, so the
        # planner's sentence is not said.
        p1.locator(".vline[data-cid=one-claim]").click()
        self.expect(page.locator(".stagecol > section.sb.captions button.play")).to_have_attribute("aria-label", "Pause the film", timeout=10000)
        vhead("V1", "of 2", "A feature with one claim", "Held")
        self.assertEqual(page.locator(".stagecol .started").count(), 0)
        self.assertTrue(page.url.endswith("#p1/one-claim"))
        # Clicking a claim's name starts the film of the variation it opens on.
        p2.locator(".pcard").click()
        self.expect(page.locator(".vline[aria-current=true]")).to_have_attribute("data-cid", "one-regression")
        self.expect(page.locator(".stagecol > section.sb.captions button.play")).to_have_attribute("aria-label", "Pause the film", timeout=10000)
        self.assertEqual(page.problems, [])
        # With its steps beside it (&steps=1): the old storyboard, under the same line.
        steps = self.open("run.html?run=review-page-claims&steps=1#p2/one-regression")
        steps.wait_for_selector(".sb .film video")
        self.assertNotIn("captions", steps.locator(".stagecol > section.sb").get_attribute("class").split())
        self.assertTrue(steps.locator(".sb .sb-rail").is_visible())
        self.assertEqual(steps.locator(".stagecol > .vhead .vchip").inner_text(), "V1")
        self.assertEqual(steps.locator(".stagecol > .vhead .vout").inner_text(), "Broke")
        self.assertEqual(steps.locator(".stagecol .started").count(), 1)
        self.assertEqual(steps.problems, [])

    def test_film_with_no_check_is_captioned_with_its_sentence(self):
        # A filmed variation with no check on its film says what is being tried throughout, then how it came out.
        page = self.open(f"run.html?run={FILMED}#p3/c2")
        page.wait_for_selector(".sb.captions .film video")
        page.wait_for_function("document.querySelector('.sb video').readyState >= 1")
        p = doc(FILMED)["properties"][2]
        words = sentence(p, p["cases"][1]).rstrip(". ")
        # Along the scrub bar it says what is being tried, and at the very end how it came out.
        self.assertEqual(page.evaluate(self.SWEEP), [f"|Trying: {words}.", "ok|Held."])
        # Played to its end, it says how it came out.
        page.evaluate("() => { const r = document.querySelector('.sb input[type=range]'); r.value = '980'; r.dispatchEvent(new Event('input', { bubbles: true })); }")
        page.get_by_role("button", name="Play the film", exact=True).click()
        page.wait_for_function("document.querySelector('.sb').dataset.caption === 'Held.'", polling=100, timeout=15000)
        self.assertEqual(page.locator(".sb").get_attribute("data-caption-mark"), "ok")
        self.assertEqual(page.locator(".sb .sb-cap").inner_text().strip().splitlines()[-1].strip(), "Held.")
        self.assertEqual(page.problems, [])

    def test_phone_opens_on_the_named_property(self):
        page = self.open(f"run.html?run={FILMED}#p3/c2", width=390, height=844)
        page.wait_for_timeout(300)
        # The page is scrolled to the proof: the main area's top at the top of the screen, or, when the main area is
        # shorter than the screen (a film alone is), the page as far down as it goes with the whole main area in view.
        top, bottom, room = page.evaluate("""() => { const b = document.querySelector('main').getBoundingClientRect();
          return [b.top, b.bottom, document.documentElement.scrollHeight - innerHeight - scrollY]; }""")
        if abs(top) >= 5:
            self.assertGreater(top, 0)
            self.assertLess(abs(room), 2, "the page could scroll further down to the proof")
            self.assertLessEqual(bottom, 844 + 1)
        page = self.open(f"run.html?run={FILMED}", width=390, height=844)
        page.wait_for_timeout(300)
        self.assertEqual(page.evaluate("scrollY"), 0)

    def test_request_case(self):
        page = self.open("run.html?run=shop-cart#p2/a1c1")
        items = page.locator(".stagecol .sheet li")
        self.assertEqual(items.count(), 5)
        self.assertIn("POST /api/cart", items.nth(2).inner_text())
        self.assertIn("409", items.nth(2).inner_text())
        self.assertIn("No film: this case called the app directly", page.locator(".stagecol").inner_text())
        self.assertEqual(page.locator(".film").count(), 0)
        more = page.get_by_role("button", name="Show the whole answer to request 2")
        more.click()
        self.expect(page.locator(".toggle[aria-expanded=true]")).to_have_text("Show less of answer to request 2")

    def test_code_case(self):
        page = self.open("run.html?run=lipsync-agent-version#g3/c2")
        sheet = page.locator(".stagecol")
        self.assertEqual(sheet.locator("pre.code").count(), 3)
        self.assertIn("app.py, lines 24 to 27", sheet.inner_text())
        self.assertEqual(sheet.locator("pre.code").first.locator(".ln").first.inner_text(), "24")
        self.assertIn("This rests on reading the code, which is weaker proof than watching the app.", sheet.inner_text())
        self.assertEqual(page.locator(".panel").count(), 0)

    def test_measurement_case(self):
        page = self.open("run.html?run=lipsync-agent-version#g1/c1")
        pair = page.locator(".pair")
        self.assertIn("GPU driver not loaded", pair.locator(".box").first.inner_text())
        after = pair.locator(".box").nth(1)
        self.assertIn("GPU driver LOADED", after.inner_text())
        self.assertNotIn("network connections", after.inner_text())
        self.assertIn("broke", after.get_attribute("class"))
        page.wait_for_selector(".film")
        held = self.open("run.html?run=lipsync-fixed-version#g1/c1").locator(".pair .box").nth(1)
        self.assertNotIn("broke", held.get_attribute("class"))

    def test_declined_case(self):
        page = self.open("run.html?run=shop-cart#p0/c4")
        panel = page.locator(".stagecol")
        self.assertTrue(page.get_by_role("button", name="It is intended", exact=True).is_disabled())
        self.assertTrue(page.get_by_role("button", name="It is a bug", exact=True).is_disabled())
        codes = panel.locator("code").all_inner_texts()
        self.assertEqual(codes, ["groundtruth.py decide <repo> cart p0 c4 intended", "groundtruth.py decide <repo> cart p0 c4 bug"])
        self.assertIn("decide command", panel.inner_text())
        self.assertEqual(panel.locator(".box.warn").count(), 1)
        self.assertIn("Is this intended?", panel.locator(".box.warn").inner_text())

    def test_not_counted_case(self):
        page = self.open("run.html?run=dashboard-three-properties#p2/c1")
        self.assertIn("Could not run.", page.locator(".stagecol").inner_text())
        self.assertIn("could not read or count any Regression cards", page.locator(".stagecol").inner_text())

    def test_title_and_starting_data(self):
        d = doc("dashboard-three-properties")
        p = d["properties"][1]
        p["title"] = "Short name"
        held = next(c for c in p["cases"] if c["outcome"] == "holds")
        held["starts_from"] = [{"tool": "make_run", "input": {"new_tests": 6, "films": True, "verdicts": "holds,needs_decision"}, "because": "not shown",
                                "returned": {"run_id": "not-shown"}},
                               {"tool": "seed_user", "input": {"admin": False}, "because": "not shown", "error": "store said no"}]
        stuck = next(c for c in p["cases"] if c["outcome"] == "cant_run")
        stuck["data_request"] = {"state": "a run with two failing tests", "tool": {"name": "make_failing_run", "does": "x", "takes": {}}}
        body = json.dumps({"id": "patched", "complete": True, "files_base": "/v1/runs/dashboard-three-properties/files/", "document": d})
        page = self.browser.new_page(viewport={"width": 1440, "height": 1000})
        self.addCleanup(page.close)
        page.route(re.compile(r"https://fonts\.(googleapis|gstatic)\.com/.*"), lambda r: r.fulfill(status=200, content_type="text/css", body=""))
        page.route(re.compile(r".*/v1/runs/patched$"), lambda r: r.fulfill(status=200, content_type="application/json", body=body))
        page.goto(self.base + f"run.html?run=patched#{p['id']}/{held['id']}")
        page.wait_for_selector(".pcard")
        card = page.get_by_role("button", name="Short name", exact=True)
        self.assertEqual(card.locator(".st").inner_text(), "Short name")
        self.expect(page.locator("main .head h2")).to_have_text("Short name")
        self.assertEqual(page.locator("main .head .statement").inner_text(), p["statement"])
        line = page.locator(".started").inner_text()
        self.assertIn("make run · new tests 6 · films yes · verdicts holds,needs decision", line)
        self.assertIn("seed user · admin no", line)
        self.assertIn("could not be set up: store said no", line)
        self.assertNotIn("not shown", line)
        self.assertNotIn("{", line)
        self.assertEqual(page.locator(".outcome-box").count(), 0)
        page.locator(f".pgroup[data-pid={p['id']}] .vline[data-cid={stuck['id']}]").click()
        self.expect(page.locator(".outcome-box")).to_have_count(1)
        self.assertIn("It needed a run with two failing tests, and asks for a data tool called “make failing run”.",
                      page.locator(".outcome-box").inner_text())
        self.assertEqual(page.locator(".started").count(), 0)
        untitled = d["properties"][0]
        page.get_by_role("button", name=untitled["statement"], exact=True).click()
        self.expect(page.locator("main .head h2")).to_have_text(untitled["statement"])
        self.assertEqual(page.locator("main .head .statement").count(), 0)

    def test_dev_server_ranges(self):
        import urllib.error
        import urllib.request
        url = self.base + f"v1/runs/{FILMED}/files/p3/c2/clip-1.webm"
        size = (SAMPLES / "files" / FILMED / "p3/c2/clip-1.webm").stat().st_size
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Range": "bytes=100-199"})) as r:
            self.assertEqual(r.status, 206)
            self.assertEqual(r.headers["Content-Range"], f"bytes 100-199/{size}")
            self.assertEqual(len(r.read()), 100)
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Range": f"bytes={size - 10}-"})) as r:
            self.assertEqual(r.status, 206)
            self.assertEqual(len(r.read()), 10)
        with urllib.request.urlopen(self.base + "v1/runs") as r:
            self.assertEqual(len(json.loads(r.read())["runs"]), len(samples()))
        for bad in [f"v1/runs/{FILMED}/files/../shop-cart.json", f"v1/runs/{FILMED}/files/p3/%2e%2e/%2e%2e/x", "v1/runs/nope"]:
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(self.base + bad)
            self.assertEqual(e.exception.code, 404)

    def test_bad_hash(self):
        page = self.open("run.html?run=shop-cart#%")
        self.assertEqual(page.locator(".pcard[aria-current=true]").get_attribute("data-pid"), "p0")
        self.assertEqual(page.problems, [])

    # ---------- the address ----------

    def test_default_selection(self):
        page = self.open("run.html?run=shop-cart")
        self.assertEqual(page.locator(".pcard[aria-current=true]").get_attribute("data-pid"), "p0")
        self.assertEqual(page.locator(".vline[aria-current=true]").get_attribute("data-cid"), "c4")
        # The chosen claim is dropped down to show its variations; the others are folded.
        self.assertEqual(page.locator(".pgroup.open").evaluate_all("gs => gs.map(g => g.dataset.pid)"), ["p0"])
        self.assertTrue(page.locator(".vline[aria-current=true]").is_visible())
        page = self.open("run.html?run=dashboard-three-properties")
        self.assertEqual(page.locator(".pcard[aria-current=true]").get_attribute("data-pid"), "p1")
        self.assertEqual(page.locator(".vline[aria-current=true]").get_attribute("data-cid"), "c1")

    def test_reload_keeps_selection(self):
        page = self.open("run.html?run=shop-cart")
        page.locator(".pcard[data-pid=p2]").click()
        page.locator(".vline[data-cid=a1c1]").click()
        self.expect(page.locator(".vline[aria-current=true]")).to_have_attribute("data-cid", "a1c1")
        self.assertTrue(page.url.endswith("#p2/a1c1"))
        page.reload()
        page.wait_for_selector(".vline[aria-current=true]")
        self.assertEqual(page.locator(".vline[aria-current=true]").get_attribute("data-cid"), "a1c1")
        # Opened on a variation, the page drops its claim down, and only that one.
        self.assertEqual(page.locator(".pgroup.open").evaluate_all("gs => gs.map(g => g.dataset.pid)"), ["p2"])
        self.assertEqual(page.locator(".pgroup[data-pid=p2] .ptw").get_attribute("aria-expanded"), "true")
        self.assertTrue(page.locator(".vline[aria-current=true]").is_visible())
        page.goto(self.base + "run.html?run=shop-copy-three-places#p1")
        page.reload()
        page.wait_for_selector(".place")

    # ---------- run state ----------

    def run_with(self, change, complete=True):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        d = doc("shop-cart")
        change(d)
        body = json.dumps({"id": "shop-cart", "uploaded_at": d["ran_at"], "complete": complete,
                           "files_base": "/v1/runs/shop-cart/files/", "document": d})
        page.route(re.compile(r".*/v1/runs/shop-cart$"), lambda r: r.fulfill(status=200, content_type="application/json", body=body))
        page.goto(self.base + "run.html?run=shop-cart")
        page.wait_for_selector(".pcard")
        return page

    def test_error_and_incomplete(self):
        page = self.run_with(lambda d: d.update(error="the app stopped answering"))
        self.assertIn("This run did not finish: the app stopped answering", page.locator(".banner").inner_text())
        page = self.run_with(lambda d: None, complete=False)
        self.assertIn("upload is not complete", page.locator(".banner").inner_text())
        page = self.run_with(lambda d: None)
        self.assertEqual(page.locator(".banner").count(), 0)

    def test_approval(self):
        def flag(d):
            ps = d["properties"]
            for p in ps:
                p.update(approval="approved", approval_changed=[])
            ps[0].update(title="Adding raises the total", approval="changed", approval_changed=["statement", "variations"])
            ps[1].update(approval="new")
        page = self.run_with(flag)
        ps = doc("shop-cart")["properties"]
        self.assertEqual(page.locator(".banner").count(), 1)
        self.assertEqual(page.locator(".banner").inner_text(),
                         "Changed after it was approved: Adding raises the total (the sentence, the variations). "
                         f"Not approved yet: {ps[1].get('title') or ps[1]['id']}.")
        self.assertEqual(page.locator(".pcard[aria-current=true]").get_attribute("data-pid"), ps[0]["id"])
        self.assertEqual(page.locator("main .head .approval-note").inner_text(),
                         "Changed after it was approved: the sentence, the variations.")
        page.locator(f".pcard[data-pid={ps[1]['id']}]").click()
        self.expect(page.locator("main .head .approval-note")).to_have_text("Not approved yet.")
        page.locator(f".pcard[data-pid={ps[2]['id']}]").click()
        self.expect(page.locator("main .head .approval-note")).to_have_count(0)
        self.assert_plain_words(page)

        def all_approved(d):
            for p in d["properties"]:
                p.update(approval="approved", approval_changed=[])
        for change in (all_approved, lambda d: None):         # every property approved, or no approval information
            page = self.run_with(change)
            self.assertEqual(page.locator(".banner").count(), 0)
            self.assertEqual(page.locator(".approval-note").count(), 0)

    def test_untrusted_text(self):
        def poison(d):
            d["properties"][0]["statement"] = "<img src=x onerror=window.pwned=1>Adding"
        page = self.run_with(poison)
        self.assertEqual(page.locator("main .head h2").inner_text(), "<img src=x onerror=window.pwned=1>Adding")
        self.assertIsNone(page.evaluate("window.pwned"))


if __name__ == "__main__":
    unittest.main()
