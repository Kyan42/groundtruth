import html
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from report_props import write_report  # noqa: E402
except ImportError:  # after report_props.py is swapped in as report.py
    from report import write_report  # noqa: E402

XSS = "<script>alert(1)</script>"
XSS_ESC = "&lt;script&gt;alert(1)&lt;/script&gt;"
PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c6360000002000105fe02fea70000000049454e44ae426082")


def write_unit(run_dir: Path, pid: str, unit: str, receipts=None, shots=(), report=None):
    udir = run_dir / pid / unit
    udir.mkdir(parents=True, exist_ok=True)
    if receipts is not None:
        (udir / "receipts.jsonl").write_text("\n".join(json.dumps(x) for x in receipts) + "\n\n", encoding="utf-8")
    for s in shots:
        (udir / f"{s}.png").write_bytes(PNG)
    if report is not None:
        (udir / "report.json").write_text(json.dumps(report), encoding="utf-8")


def meta(sec, cost, turns=3, **kw):
    return {"seconds": sec, "cost_usd": cost, "turns": turns, "calls": 2, "rejected": [], "refused_tools": [],
            "tools": [], **kw}


def case(cid, outcome, what, door="browser", entry="e1", attempts=1, **res):
    return {"id": cid, "entry_point": entry, "door": door, "what": what, "represents": f"stands for {cid}",
            "result": {"outcome": outcome, "observed": f"observed {cid}", "screenshots": [], "excerpts": [],
                       "checks": [], "notes": [], "baseline": None, "meta": meta(2.0, 0.01, attempts=attempts), **res}}


def fields(verdict, held, planned, broke=(), declined=(), outside=(), not_run=(), questions=()):
    return {"verdict": verdict, "held": held, "planned": planned, "broke": list(broke), "declined": list(declined),
            "outside": list(outside), "not_run": list(not_run), "questions": list(questions)}


def plan(kind="behaviour", eps=None, **kw):
    return {"applies_to": "Applies-to text", "action": "Action text", "compares": "Compares text", "kind": kind,
            "how_found": "How-found text", "not_covered": "Not-covered text",
            "entry_points": eps or [{"id": "e1", "door": "browser", "where": "/page", "found_by": "seen on page"}],
            "code_refs": [], **kw}


def full_run(root: Path) -> Path:
    run_dir = root / "full"
    run_dir.mkdir()
    props = [
        {   # holds, kind code; listed first so ordering is exercised
            "id": "pH", "statement": "Every handler checks the session.", **fields("holds", 1, 1),
            "plan": plan("code", eps=[{"id": "e1", "door": "code", "where": "src/**", "found_by": "grep"}],
                         code_refs=[{"path": "src/a.ts", "line": 12}, {"path": "src/b.ts", "line": 3}]),
            "discovery_meta": meta(10.0, 0.05),
            "cases": [case("c1", "holds", "Read every handler", door="code",
                           excerpts=[{"path": "src/a.ts", "start": 10, "end": 11, "why": "The guard is here",
                                      "lines": ["const u = requireUser();", f"// {XSS}"]}],
                           checks=[{"id": "check-1", "kind": "match_each", "checked": 4, "passed": 4, "violators": [],
                                    "rule": {"kind": "match_each", "glob": "src/**/*.ts",
                                             "must_contain": "requireUser"}}])],
        },
        {   # fails, with every other outcome present; listed in the reverse of the expected row order
            "id": "pF", "statement": f"Totals add up {XSS}",
            **fields("fails", 1, 5, broke=["c2"], declined=["c3"], outside=["c5"], not_run=["c4"],
                     questions=[{"case": "c3", "question": "Is refusing a negative quantity intended?",
                                 "draft_if_intended": "A negative quantity is refused.", "observed": "Refused"}]),
            "plan": plan(eps=[{"id": "e1", "door": "browser", "where": "/cart", "found_by": "button"},
                              {"id": "e2", "door": "api", "where": "POST /api/cart", "found_by": "route list"}]),
            "discovery_meta": meta(5.0, 0.02, rejected=["the plan had no cases"]),
            "cases": [case("c5", "outside", "OUTSIDEROW add zero", observed=f"OUTSIDE-OBS a zero quantity is not an addition {XSS}"),
                      case("c1", "holds", "HELDROW add one"),
                      case("c4", "cant_run", "CANTROW add four"),
                      case("c3", "declined", "DECLINEDROW add minus one", question="Is refusing a negative quantity intended?",
                           draft_if_intended="A negative quantity is refused."),
                      case("c2", "breaks", f"BROKEROW add two {XSS}", door="api", entry="e2",
                           observed=f"total was wrong {XSS}", screenshots=["shot-1", "shot-9"])],
        },
        {   # needs_decision
            "id": "pN", "statement": "Sold-out items are handled.",
            **fields("needs_decision", 1, 2, declined=["c1"],
                     questions=[{"case": "c1", "question": f"Is refusing a sold-out item intended? {XSS}",
                                 "draft_if_intended": "Adding a sold-out item is refused.", "observed": "Refused with 409"}]),
            "plan": plan(), "discovery_meta": meta(1.0, 0.01),
            "cases": [case("c2", "holds", "Add an in-stock item", attempts=2),
                      case("c1", "declined", "Try sold-out item", question="Is refusing a sold-out item intended?",
                           draft_if_intended="Adding a sold-out item is refused.", observed="Refused with 409")],
        },
        {   # cant_tell with harness-decided case and no door on the case (falls back to the plan)
            "id": "pC", "statement": "Search finds items.", **fields("cant_tell", 0, 1, not_run=["c1"]),
            "plan": plan(eps=[{"id": "e1", "door": "api", "where": "GET /api/search", "found_by": "routes"}]),
            "discovery_meta": meta(1.0, 0.01),
            "cases": [{"id": "c1", "entry_point": "e1", "what": "Search for kraken",
                       "result": {"outcome": "cant_run", "decided_by_harness": True,
                                  "observed": "The verifier never submitted an accepted result (timeout).",
                                  "meta": meta(3.0, 0.02, refused_tools=["shell"], attempts=2)}}],
        },
        {   # measurement, holds, with one case outside the wording
            "id": "pM", "statement": "Rendering uses the GPU.", **fields("holds", 1, 2, outside=["c2"]),
            "plan": plan("measurement"), "discovery_meta": meta(1.0, 0.01),
            "cases": [case("c1", "holds", "Measure while rendering", baseline=f"gpu_check:\nBASELINE idle {XSS}"),
                      case("c2", "outside", "Measure while idle")],
        },
        {   # planned only
            "id": "pP", "statement": "Planned property sentence.", **fields("planned", 0, 2, not_run=["c1", "c2"]),
            "plan": plan(), "discovery_meta": meta(1.0, 0.01),
            "cases": [case("c1", "planned", "Planned step one", observed="Planned only; not carried out."),
                      case("c2", "planned", "Planned step two", observed="Planned only; not carried out.")],
        },
        {   # no plan
            "id": "pX", "statement": "Unplannable property.", "verdict": "cant_tell", "cases": [],
            "no_plan_because": "the app had no such page", "discovery_meta": meta(4.0, 0.03),
        },
        {   # cant_tell: every case fell outside the wording
            "id": "pO", "statement": "Refunds are logged.", **fields("cant_tell", 0, 1, outside=["c1"]),
            "plan": plan(), "discovery_meta": meta(1.0, 0.01),
            "cases": [case("c1", "outside", "Refund a gift card")],
        },
    ]
    run = {"run": "RUN-123", "feature": "cart", "goal": "Shoppers collect items.",
           "env": {"url": "http://127.0.0.1:3100", "pid": 42, "repo": "/tmp/my repo", "reset": "node seed.mjs",
                   "routes": {"type": "next"},
                   "facts": {"commit": "abc1234", "uncommitted_changes": False, "platform": "macOS"}},
           "service": {"url": "http://127.0.0.1:8790", "run": "svc1", "version": "3", "model": "m"},
           "order": ["pM", "pH", "pF", "pN", "pC", "pP", "pX", "pO"], "aborted": False, "error": None,
           "properties": props}
    (run_dir / "run.json").write_text(json.dumps(run), encoding="utf-8")
    write_unit(run_dir, "pF", "discovery", receipts=[{"t": 1, "tool": "list_routes", "args": {}, "result": "3 routes"}])
    write_unit(run_dir, "pF", "c1", receipts=[{"t": 1, "tool": "navigate", "args": {"path": "/"}, "result": "ok"}])
    write_unit(run_dir, "pF", "c2", shots=["shot-1"], receipts=[
        {"t": 1, "tool": "http_request", "args": {"method": "POST"}, "result": f"200 {XSS}"},
        {"t": 2, "tool": "shell", "args": {"cmd": "ls"}, "result": "", "refused": True}])
    write_unit(run_dir, "pM", "c1", receipts=[
        {"t": 1, "tool": "gpu_check", "args": {}, "result": "GPU-BEFORE idle"},
        {"t": 2, "tool": "click", "args": {"selector": "#render"}, "result": "clicked"},
        {"t": 3, "tool": "gpu_check", "args": {}, "result": "GPU-AFTER busy"},
        {"t": 4, "tool": "observe", "args": {}, "result": "page text"}])
    return run_dir


def minimal_run(root: Path) -> Path:
    run_dir = root / "min"
    run_dir.mkdir()
    (run_dir / "run.json").write_text(json.dumps({"properties": [{}, {"id": "p9", "cases": [{}],
                                                                       "plan": {"entry_points": [{}]}}]}))
    return run_dir


class ReportPropsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.page = write_report(full_run(cls.root)).read_text(encoding="utf-8")
        cls.text = html.unescape(cls.page)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def section(self, pid):
        m = re.search(rf'<section class="card prop" id="prop-{pid}">(.*?)</section>', self.page, re.S)
        self.assertIsNotNone(m, pid)
        return m.group(1)

    def table(self, s):
        return s[s.index('<table class="cases">'):s.index("</table>", s.index('<table class="cases">'))]

    def test_self_contained_and_escaped(self):
        p = self.page
        self.assertNotIn("<script", p)
        self.assertNotIn("http://cdn", p)
        self.assertIn(f"Totals add up {XSS_ESC}", p)            # statement
        self.assertIn(f"BROKEROW add two {XSS_ESC}", p)         # what
        self.assertIn(f"total was wrong {XSS_ESC}", p)          # observed
        self.assertIn(f"200 {XSS_ESC}", p)                      # receipt result
        self.assertIn(f"// {XSS_ESC}", p)                       # excerpt line
        self.assertIn(f"BASELINE idle {XSS_ESC}", p)            # baseline
        self.assertIn(f"sold-out item intended? {XSS_ESC}", p)  # question

    def test_old_fields_gone(self):
        p = self.text
        for old in ("Scope", "inside the property", "not settled by its wording", "&lt;case&gt;", "<case>"):
            self.assertNotIn(old, p)

    def test_header_tally_and_env(self):
        p = self.page
        self.assertIn("<h1>cart</h1>", p)
        self.assertIn("Shoppers collect items.", p)
        tally = html.unescape(p[p.index('<div class="tally">'):p.index("</div>", p.index('<div class="tally">'))])
        order = [tally.index(f"{n} {w}</span>") for n, w in
                 [(1, "fails"), (1, "needs your decision"), (3, "can't tell"), (2, "holds"), (1, "planned only")]]
        self.assertEqual(order, sorted(order))
        self.assertIn("What this ran against", p)
        self.assertIn("<code>abc1234</code>, with no uncommitted changes.", p)
        self.assertIn("The properties' cases were carried out in this order: pM, pH, pF, pN, pC, pP, pX, pO.", self.text)
        self.assertIn("Properties that measure the running process have their cases carried out first, "
                      "before any other property's cases.", self.text)
        self.assertNotIn("before anything else touches the app", p)
        self.assertIn("Here that was: pM.", p)
        self.assertNotIn("claim", p.lower())
        self.assertNotIn("Measured first", p)

    def test_section_order(self):
        p = self.page
        pos = [p.index(f'id="prop-{x}"') for x in ["pF", "pN", "pC", "pX", "pO", "pH", "pM", "pP"]]
        self.assertEqual(pos, sorted(pos))

    def test_failing_property(self):
        s = self.section("pF")
        self.assertIn("1 of 5 cases broke the property. The app also declined the action in 1 case, so the property "
                      "could not be observed there. 1 of 5 cases could not be run. 1 planned case fell outside the "
                      "property&#x27;s wording and does not count.", s)
        self.assertIn("fails</span>", s)
        table = self.table(s)
        rows = [table.index(x) for x in ("BROKEROW", "DECLINEDROW", "CANTROW", "HELDROW", "OUTSIDEROW")]
        self.assertEqual(rows, sorted(rows))
        for label in ("broke", "declined by the app", "could not run", "held", "outside the property"):
            self.assertIn(f"{label}</span>", table)
        self.assertNotIn("<th>Scope</th>", s)
        self.assertIn("OUTSIDE-OBS a zero quantity is not an addition", table)   # outside rows show their observed text
        self.assertIn("Each case was carried out and judged by a conversation that had the property and the case, "
                      "but not the code.", s)
        self.assertLess(s.index("judged by a conversation"), s.index('<table class="cases">'))
        self.assertIn('<details class="case-ev" open id="ev-c2"><summary>Case c2: evidence</summary>', s)
        self.assertIn('<details class="case-ev" open id="ev-c3"><summary>Case c3: evidence</summary>', s)
        self.assertIn('<details class="case-ev" id="ev-c1"><summary>Case c1: evidence</summary>', s)
        self.assertIn('<details class="case-ev" id="ev-c5"><summary>Case c5: evidence</summary>', s)
        self.assertIn("data:image/png;base64,", s)
        self.assertIn("Screenshot shot-9 was listed but its file is missing.", s)
        self.assertIn("[refused, not run] shell(cmd=ls)", s)
        self.assertIn("All 2 tool calls, 1 refused", s)
        self.assertIn("direct API call", s)
        self.assertIn("through the pages", s)
        self.assertIn("The service refused a plan the verifier tried to submit, because: the plan had no cases", s)
        self.assertIn("list_routes() returned: 3 routes", s)
        self.assertIn("How discovery worked", s)
        self.assertIn("How the verifier read it", s)
        self.assertIn("Compares text", s)
        self.assertIn("<b>Not covered:</b> Not-covered text", s)
        self.assertIn("This property took 6 conversations (1 discovery plus the cases carried out), "
                      "15.0 seconds and $0.070 in all.", s)

    def test_needs_decision(self):
        s = self.section("pN")
        t = html.unescape(s)
        self.assertIn("The app declined the action in 1 case, so the property could not be observed there. "
                      "That needs your decision. 1 of 2 cases held.", t)
        self.assertIn("Questions for you", s)
        self.assertIn("What happened in that case: Refused with 409", s)
        self.assertIn("If this is intended, it would become its own property: <i>Adding a sold-out item is refused.</i>", s)
        self.assertIn('.venv/bin/python groundtruth.py decide "/tmp/my repo" cart pN c1 intended\n'
                      '.venv/bin/python groundtruth.py decide "/tmp/my repo" cart pN c1 bug', t)
        self.assertIn("<code>intended</code> takes that situation out of this property and adds the drafted sentence "
                      "as a property of its own; <code>bug</code> means a refusal there fails the property from now on.", s)
        self.assertLess(s.index("Questions for you"), s.index('<table class="cases">'))
        self.assertLess(self.table(s).index("declined by the app"), self.table(s).index("held</span>"))
        self.assertIn("This case needed a second attempt: the first conversation ended without a result.",
                      s[s.index('id="ev-c2"'):])
        self.assertNotIn("second attempt", s[s.index('id="ev-c1"'):s.index('id="ev-c2"')])

    def test_one_command_pair_per_question(self):
        t = html.unescape(self.section("pF"))
        self.assertIn('decide "/tmp/my repo" cart pF c3 intended\n', t)
        self.assertIn('decide "/tmp/my repo" cart pF c3 bug', t)
        self.assertEqual(t.count("groundtruth.py decide"), 2)

    def test_cant_tell(self):
        s = self.section("pC")
        self.assertIn("1 of 1 case could not be run.", s)
        self.assertIn("could not run</span>", s)
        self.assertIn("so the harness recorded it as could not run on its own", s)
        self.assertIn("not allowed to use: shell.", s)
        self.assertIn("<td>direct API call</td>", s)      # door taken from the plan's entry point
        self.assertIn("This case needed a second attempt", s)

    def test_nothing_under_the_property(self):
        s = html.unescape(self.section("pO"))
        self.assertIn("No planned case fell under the property, so nothing was shown. 1 planned case fell outside "
                      "the property's wording and does not count.", s)
        self.assertIn("outside the property</span>", s)

    def test_code_holds(self):
        s = self.section("pH")
        self.assertIn("1 of 1 case held. This rests on reading the code, which is weaker evidence than "
                      "observing the app.", s)
        self.assertIn("The guard is here", s)
        self.assertIn("  10  const u = requireUser();", s)
        self.assertIn("Every-item check (check-1)", s)
        self.assertIn("must contain <code>requireUser</code>", s)
        self.assertIn("<code>src/a.ts:12</code>, <code>src/b.ts:3</code>", s)
        self.assertIn("<td>files</td>", s)
        self.assertIn("<td>code</td>", s)
        self.assertNotIn("Measured by the verifier", s)

    def test_measurement(self):
        s = self.section("pM")
        self.assertIn("1 of 2 cases held. 1 planned case fell outside the property&#x27;s wording and does not count.", s)
        self.assertIn("Measurements and actions, in order", s)
        ev = s[s.index('id="ev-c1"'):]
        self.assertLess(ev.index("Measured by the verifier before this case touched the app"),
                        ev.index("Measurements and actions, in order"))
        self.assertIn("BASELINE idle", ev)
        a, b, c = s.index("GPU-BEFORE"), s.index("click(selector=#render)"), s.index("GPU-AFTER")
        self.assertLess(a, b)
        self.assertLess(b, c)
        self.assertIn("checked by measuring the running process", s)

    def test_planned_and_no_plan(self):
        s = self.section("pP")
        self.assertIn("2 cases were planned; none was carried out.", s)
        self.assertNotIn("could not be run", s)
        self.assertIn("planned only</span>", s)
        x = self.section("pX")
        self.assertIn("Discovery produced no plan", x)
        self.assertIn("the app had no such page", x)

    def test_final_line(self):
        self.assertRegex(self.page, r"The whole run took \d+ conversations, [\d.]+ seconds and \$[\d.]+\. Run RUN-123\.")

    def test_minimal(self):
        with tempfile.TemporaryDirectory() as t:
            page = html.unescape(write_report(minimal_run(Path(t))).read_text(encoding="utf-8"))
        self.assertIn("can't tell (no verdict recorded)", page)
        self.assertIn("1 of 1 case could not be run.", page)
        self.assertIn("Discovery produced no plan", page)
        self.assertIn("The order the properties' cases were carried out in was not recorded.", page)
        self.assertIn("Run id not recorded.", page)

    def test_missing_question_fields(self):
        with tempfile.TemporaryDirectory() as t:
            run_dir = Path(t) / "q"
            run_dir.mkdir()
            (run_dir / "run.json").write_text(json.dumps({"properties": [
                {"id": "p1", "verdict": "needs_decision", "questions": [{}, "junk"],
                 "cases": [{"id": "c1", "result": {"outcome": "declined"}}]}]}))
            page = html.unescape(write_report(run_dir).read_text(encoding="utf-8"))
        self.assertIn("(question not recorded)", page)
        self.assertIn('decide "<repo>" <feature> p1 <case> intended', page)
        self.assertIn("The app declined the action in 1 case", page)


# ---------- ways in found by the code search ----------

def row(step, path, line, txt, label, reason, outcome):
    return {"step": step, "searched_for": "x", "path": path, "line": line, "text": txt, "label": label,
            "reason": reason, "outcome": outcome}


def traced_run(root: Path) -> Path:
    run_dir = root / "traced"
    run_dir.mkdir()
    chain3 = [{"path": "components/QuickAddButton.tsx", "note": "calls POST /api/cart at line 23"},
              {"path": "components/ProductCard.tsx", "line": 41, "note": "uses it (import)"},
              {"path": "app/products/page.tsx", "line": 9, "note": "uses it (import)"}]
    eps = [
        {"id": "e1", "door": "browser", "where": "Product list, the quick-add button", "status": "found",
         "found_by": "calls POST /api/cart at line 23", "page": "/products", "file": "components/QuickAddButton.tsx",
         "route": "POST /api/cart", "chain": chain3},
        {"id": "e2", "door": "browser", "where": "Cart page, the quantity box", "status": "found",
         "page": "/cart", "file": "app/cart/page.tsx",
         "chain": [{"path": "app/cart/page.tsx", "note": "calls POST /api/cart at line 5"}]},
        {"id": "e3", "door": "browser", "where": "Wishlist, the move-to-cart link", "status": "to check",
         "page": "/wishlist", "file": "components/Wish.tsx",
         "chain": [{"path": "components/Wish.tsx", "note": "mentions addItem at line 7, labelled mention"},
                   {"path": "app/wishlist/page.tsx", "line": 3, "note": "uses it (import)"}]},
        {"id": "e4", "door": "browser", "where": "Search results, the add button", "status": "flagged",
         "page": "/search", "file": "lib/client.ts",
         "chain": [{"path": "lib/client.ts", "note": "builds a request address from parts at line 12"},
                   {"path": "app/search/page.tsx", "line": 2, "note": "uses it (import)"}]},
        {"id": "a1", "door": "api", "where": "direct call to POST /api/cart", "status": "found",
         "route": "POST /api/cart", "file": "app/api/cart/route.ts"},
    ]
    trace = {
        "data": "the cart's line items", "patterns": [r"cart\.items", "lineItems"],
        "roots": [{"path": "lib/cart.ts", "name": "addItem", "line": 14}],
        "routes": [{"method": "POST", "path": "/api/cart", "file": "app/api/cart/route.ts", "inferred": False},
                   {"method": "PATCH", "path": "/api/cart", "file": "pages/api/cart.ts", "inferred": True}],
        "excluded": [{"file": "components/OldCart.tsx", "status": "found", "why": "calls POST /api/cart at line 4",
                      "because": "it is not shown on any page"}],
        "rows": [
            row("start", "lib/cart.ts", 15, f"cart.items.push(item) {XSS}", "start",
                "The line the trace was pointed at as carrying out the action.", "a starting point, inside addItem"),
            row("writers", "lib/merge.ts", 8, "cart.items = merged", "does_it", "merges into the cart",
                "another starting point, inside mergeCarts"),
            row("writers", "lib/seed.ts", 2, "cart.items = []", "other_write", "initial value",
                "writes it in another way; not followed"),
            row("writers", "lib/blog.ts", 3, "lineItems = post.items", "not_a_write", "a blog", "not a write"),
            row("callers", "app/api/cart/route.ts", 10, "addItem(body)", "calls", "calls it", "a caller, inside POST"),
            row("callers", "lib/cart.ts", 1, "import { addItem }", "import", "an import", "the definition or an import"),
            row("callers", "components/Wish.tsx", 7, "// addItem later", "mention", "a comment",
                "dismissed by its label; its file is still followed to the pages"),
            row("callers", "lib/other.ts", 5, "function addItem()", "same_name", "another addItem",
                "dismissed by its label; its file is still followed to the pages"),
            row("callers", "app/cart/page.tsx", 5, "addItem(id)", "calls", "the page calls it",
                "called by a page's own code; that page is a way in"),
            row("routes", "components/QuickAddButton.tsx", 23, "fetch('/api/cart', {method:'POST'})", "calls",
                "posts", "a caller; its file is followed to the pages"),
            row("routes", "components/OldCart.tsx", 4, "fetch('/api/cart')", "unlabelled", "no usable answer",
                "a caller; its file is followed to the pages"),
            row("routes", "app/api/cart/route.ts", 1, "export async function POST", "defines", "server",
                "the server side of the route"),
            row("routes", "lib/future.ts", 9, "post('/api/cart')", "brand_new_label", "a future label",
                "something new"),
            row("built addresses", "lib/client.ts", 12, "fetch(`/api/${name}`)", "could_reach", "cannot rule out",
                "flagged; its file is followed to the pages"),
            row("built addresses", "lib/img.ts", 4, "fetch(`/img/${id}`)", "cannot", "images",
                "goes somewhere else"),
        ],
        "notes": ["NOTE-ONE the search for lineItems was cut off at 150 of 300 hits."],
        "blind_spots": ["BLIND-ONE code generated at build time is not searched.",
                        "BLIND-TWO a request address read from configuration cannot be matched."],
        "labels": 12, "label_cost_usd": 0.024,
    }
    dmeta = {**meta(30.0, 0.30), "conversations": 2, "labels": 12}
    props = [
        {"id": "pT", "statement": "The cart total matches its items.", **fields("holds", 2, 2),
         "plan": plan(eps=eps, traced=True, how_found="TRACED-HOW summary", not_covered="Not-covered traced"),
         "trace": trace, "discovery_meta": dmeta,
         "cases": [case("c1", "holds", "Quick add"), case("c2", "holds", "Change quantity", entry="e2")]},
        {"id": "pU", "statement": "Untraced property.", **fields("holds", 1, 1),
         "plan": plan(how_found="UNTRACED-HOW a model read app/cart."), "discovery_meta": meta(5.0, 0.05),
         "cases": [case("c1", "holds", "Add one")]},
        {"id": "pE", "statement": "Empty trace property.", **fields("holds", 1, 1),
         "plan": plan(eps=[{"id": "e1", "door": "browser", "where": "/x", "status": "found"}, {}], traced=True),
         "trace": {}, "cases": [case("c1", "holds", "Do x")]},
    ]
    (run_dir / "run.json").write_text(json.dumps({"run": "RUN-T", "properties": props}), encoding="utf-8")
    return run_dir


class TracedWaysInTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.page = write_report(traced_run(Path(cls.tmp.name))).read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def section(self, pid):
        m = re.search(rf'<section class="card prop" id="prop-{pid}">(.*?)</section>', self.page, re.S)
        self.assertIsNotNone(m, pid)
        return m.group(1)

    def test_traced_sentence_and_ways_table(self):
        s = self.section("pT")
        t = html.unescape(s)
        self.assertIn("How the ways in were found, and what was left out", s)
        self.assertIn("The ways in were found by searching the code, not by a model's reading of it: plain code ran "
                      "each search, and every hit was labelled by its own short model call.", t)
        self.assertNotIn("No search checked that they are complete", t)
        self.assertNotIn("Ways in that were found", s)
        self.assertIn("<td>found by the search</td>", t)
        self.assertIn("<td>kept to be safe: a label dismissed it, but its file is on this page</td>", t)
        self.assertIn("<td>flagged: builds its request address from parts</td>", t)
        self.assertIn("<td>Product list, the quick-add button</td>", t)
        self.assertIn("<td>/products</td>", t)
        self.assertIn("<td>direct API call</td><td>POST /api/cart</td>", t)
        self.assertIn("<code>components/QuickAddButton.tsx</code> (calls POST /api/cart at line 23) is used by "
                      "<code>components/ProductCard.tsx:41</code> is used by <code>app/products/page.tsx:9</code>", t)
        self.assertNotIn("→", t)
        self.assertNotIn("-&gt;", s)

    def test_what_was_searched(self):
        t = html.unescape(self.section("pT"))
        self.assertIn("What was searched", t)
        self.assertIn("the cart's line items", t)
        self.assertIn(r"<code>cart\.items</code>, <code>lineItems</code>", t)
        self.assertIn("<code>addItem</code> at <code>lib/cart.ts:14</code>", t)
        self.assertIn("<code>PATCH /api/cart</code>, served by <code>pages/api/cart.ts</code> (inferred from the "
                      "addresses that file handles)", t)
        self.assertNotIn("<code>POST /api/cart</code>, served by <code>app/api/cart/route.ts</code> (inferred", t)

    def test_every_hit(self):
        s = self.section("pT")
        t = html.unescape(s)
        self.assertIn("Every search hit, and what became of it", s)
        det = s[s.index('<details class="hits">'):s.index("</details>", s.index('<details class="hits">'))]
        self.assertIn("<summary>The search started from 1 line that carries out the action, then found 14 search "
                      "hits: 5 led to a way in or to code that carries out the action, 5 were dismissed and 1 was not "
                      "shown on any page; the other 3 were definitions, imports, the server side of a route or not "
                      "recorded</summary>", html.unescape(det))
        heads = [det.index(h) for h in ("The lines the search started from",
                                        "Other lines that may carry out the same action",
                                        "Lines that name the code that carries out the action",
                                        "Lines that mention a route to it",
                                        "Places that build a request address from parts")]
        self.assertEqual(heads, sorted(heads))
        self.assertNotIn("Other search hits", det)
        for lab in ("the starting point", "carries out the action", "writes the data in another way",
                    "does not carry out the action", "calls it", "an import", "same name, different thing",
                    "a mention", "the server side of the route", "could reach it", "goes somewhere else",
                    "not labelled, so treated as relevant", "brand new label"):
            self.assertIn(f"<td>{lab}</td>", det, lab)
        for raw in ("does_it", "other_write", "not_a_write", "same_name", "could_reach", "unlabelled",
                    "brand_new_label", "<td>start</td>", "<td>calls</td>"):
            self.assertNotIn(raw, det, raw)
        self.assertIn("<code>lib/cart.ts:15</code>", det)
        self.assertIn(f"cart.items.push(item) {XSS_ESC}", det)
        self.assertNotIn("<script", self.page)
        self.assertIn("a starting point, inside addItem", t)

    def test_excluded_and_limits(self):
        s = self.section("pT")
        t = html.unescape(s)
        self.assertIn("Found by the search but not shown on any page", s)
        self.assertIn("<td><code>components/OldCart.tsx</code></td><td>calls POST /api/cart at line 4</td>"
                      "<td>not shown on any page, so no one can reach it through the app</td>", t)
        self.assertIn("Limits of this search", s)
        lim = s.index("Limits of this search")
        self.assertLess(s.index("BLIND-ONE"), s.index("BLIND-TWO"))
        self.assertLess(lim, s.index("BLIND-ONE"))
        self.assertLess(s.index("BLIND-TWO"), s.index("In this run"))
        self.assertLess(s.index("In this run"), s.index("NOTE-ONE"))
        self.assertLess(s.index("NOTE-ONE"), s.index("<b>Not covered:</b> Not-covered traced"))
        # nothing in the limits sits inside a collapsed block
        for marker in ("Limits of this search", "BLIND-ONE", "NOTE-ONE"):
            pos = s.index(marker)
            self.assertEqual(s[:pos].count("<details"), s[:pos].count("</details>"), marker)

    def test_discovery_and_counting(self):
        s = self.section("pT")
        self.assertIn("Finding the ways in and planning the cases took 2 conversations and 12 short labelling calls, "
                      "30.0 seconds and $0.300.", s)
        self.assertIn("This property took 4 conversations (2 discovery plus the cases carried out) plus 12 short "
                      "labelling calls, 34.0 seconds and $0.320 in all.", s)
        u = self.section("pU")
        self.assertIn("This property took 2 conversations (1 discovery plus the cases carried out), 7.0 seconds", u)
        self.assertIn("Finding the ways in and planning the cases took 1 conversation, 5.0 seconds and $0.050.", u)
        self.assertIn("The whole run took 7 conversations plus 12 short labelling calls, ", self.page)

    def test_untraced_fallback(self):
        s = self.section("pU")
        self.assertIn("The ways in below were found by a model reading the code. No search checked that they are "
                      "complete. UNTRACED-HOW a model read app/cart.", s)
        self.assertNotIn("Limits of this search", s)
        self.assertNotIn("Every search hit", s)
        self.assertIn("<b>Not covered:</b> Not-covered text", s)

    def test_empty_trace_and_missing_chain(self):
        s = html.unescape(self.section("pE"))
        self.assertIn("The ways in were found by searching the code", s)
        self.assertIn("The chain of files from this way in to its page was not recorded.", s)
        self.assertIn("No search hits were recorded", s)
        self.assertIn("Limits of this search", s)
        self.assertIn("No limits were recorded.", s)
        self.assertNotIn("Found by the search but not shown on any page", s)
        self.assertIn("This property took 1 conversation (the cases carried out; no discovery was recorded), ", s)


# ---------- cases where the search pointed at a place with no way to do the action ----------

def nothing_case(cid, what):
    c = case(cid, "outside", what, observed="No way to do the action was found there. NOTHING-OBS no button")
    c["result"]["nothing_there"] = True
    return c


class NothingThereTest(unittest.TestCase):
    def render(self, props):
        with tempfile.TemporaryDirectory() as t:
            run_dir = Path(t) / "n"
            run_dir.mkdir()
            (run_dir / "run.json").write_text(json.dumps({"properties": props}), encoding="utf-8")
            return html.unescape(write_report(run_dir).read_text(encoding="utf-8"))

    def test_counted_apart_from_outside(self):
        page = self.render([{"id": "p1", "statement": "Adding works.", **fields("holds", 1, 4, outside=["c2", "c3", "c4"]),
                             "plan": plan(),
                             "cases": [case("c1", "holds", "Add one"), nothing_case("c2", "Look on the wishlist"),
                                       nothing_case("c3", "Look on search"), case("c4", "outside", "Add zero")]}])
        self.assertIn("1 of 4 cases held. 1 planned case fell outside the property's wording and does not count. "
                      "In 2 places the search pointed at, there was no way to do the action, so there was nothing to "
                      "test there.", page)
        table = page[page.index('<table class="cases">'):page.index("</table>", page.index('<table class="cases">'))]
        self.assertEqual(table.count("nothing there to test</span>"), 2)
        self.assertEqual(table.count("outside the property</span>"), 1)
        self.assertIn("NOTHING-OBS no button", table)

    def test_only_nothing_there(self):
        page = self.render([{"id": "p1", "statement": "Adding works.", **fields("holds", 1, 2, outside=["c2"]),
                             "plan": plan(), "cases": [case("c1", "holds", "Add one"), nothing_case("c2", "Look")]}])
        self.assertIn("1 of 2 cases held. In 1 place the search pointed at, there was no way to do the action, so "
                      "there was nothing to test there.", page)
        self.assertNotIn("fell outside the property's wording", page)
        self.assertNotIn("outside the property</span>", page)

    def test_flag_without_outside_is_ignored(self):
        c = case("c1", "holds", "Add one")
        c["result"]["nothing_there"] = True
        page = self.render([{"id": "p1", "statement": "s", **fields("holds", 1, 1), "plan": plan(), "cases": [c]}])
        self.assertIn("held</span>", page)
        self.assertNotIn("nothing to test", page)


# ---------- which run `groundtruth.py report` picks ----------

class ReportCommandTest(unittest.TestCase):
    def setUp(self):
        import groundtruth
        self.gt = groundtruth
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name).resolve()
        self.runs = self.repo / ".groundtruth" / "runs"

    def tearDown(self):
        self.tmp.cleanup()

    def run_cmd(self, run=None):
        import argparse
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.gt.cmd_report(argparse.Namespace(repo=str(self.repo), run=run))
        return out.getvalue().strip()

    def make(self, name, finished=True):
        d_ = self.runs / name
        d_.mkdir(parents=True)
        if finished:
            (d_ / "run.json").write_text(json.dumps({"run": name, "properties": []}), encoding="utf-8")
        return d_

    def test_newest_finished_run(self):
        self.make("20261110-100000")
        done = self.make("20261110-110000")
        self.make("20261110-120000", finished=False)
        self.assertEqual(self.run_cmd(), str(done / "report.html"))
        self.assertTrue((done / "report.html").is_file())

    def test_no_finished_run(self):
        with self.assertRaises(SystemExit) as e:
            self.run_cmd()
        self.assertIn("No runs yet", str(e.exception.code))
        self.make("20261110-120000", finished=False)
        with self.assertRaises(SystemExit) as e:
            self.run_cmd()
        self.assertIn("No runs yet", str(e.exception.code))

    def test_named_run_unfinished_or_missing(self):
        self.make("20261110-120000", finished=False)
        for name in ("20261110-120000", "nope"):
            with self.assertRaises(SystemExit) as e:
                self.run_cmd(name)
            self.assertEqual(str(e.exception.code),
                             f"Run {name} did not finish or does not exist, so there is no report to write for it.")
        done = self.make("20261110-130000")
        self.assertEqual(self.run_cmd("20261110-130000"), str(done / "report.html"))


if __name__ == "__main__":
    unittest.main()
