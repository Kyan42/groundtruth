"""The trace's orchestration, with the real plain-code searches on a real app copy and a scripted stand-in for the
labelling model. This checks the algorithm (what gets followed, what becomes a way in); it says nothing about how
well a real model labels."""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from service.trace import Tracer, build_plan, cases_problems  # noqa: E402
from toolserver.catalog import Catalog  # noqa: E402

TWO_DOORS = Path(__file__).resolve().parent.parent / "gates" / "work" / "cbay-two-doors"


class FakeRun:
    """Stands in for the service's Run: searches go to a real Catalog, labels come from simple rules."""

    def __init__(self, repo, mislabel=None):
        self.cat = Catalog({"url": "http://127.0.0.1:9", "repo": str(repo)}, Path(tempfile.mkdtemp()))
        self.mislabel = mislabel or {}
        self.asked = []

    def call_local(self, tool, args):
        return self.cat.run(tool, args)

    def _claude(self, msg, system, extra, name, timeout=0):
        hit = re.search(r"Search hit at (\S+):(\d+)", msg)
        path, line = hit.group(1), int(hit.group(2))
        marked = next(l for l in msg.splitlines() if l.startswith(">>"))
        self.asked.append((path, line))
        if "Plain code thinks the marked line writes" in msg:
            label = "does_it" if ".push(" in marked else "other_write"
        elif "Is the marked line a use of that same" in msg:
            label = ("import" if "import " in marked else "definition" if "export function" in marked else
                     "same_name" if path.startswith("components/") else "calls")
        elif "Does the marked line send a request to" in msg:
            label = "calls" if "fetch(" in marked else "mention"
        else:
            label = "could_reach"
        label = self.mislabel.get((path, line), label)
        return {"result": json.dumps({"label": label, "reason": "scripted"}), "total_cost_usd": 0.0}


ANCHOR = {"applies_to": "in-stock items", "action": "add an item to the cart", "compares": "subtotal change against listing price",
          "kind": "behaviour", "data": "the cart's stored contents", "roots": [{"path": "lib/cart.ts", "line": 36}],
          "patterns": [r"\.cart\b"]}


@unittest.skipUnless(TWO_DOORS.exists(), "gates/work/cbay-two-doors is not built on this machine")
class TraceOnTwoDoors(unittest.TestCase):
    def trace(self, **kw):
        run = FakeRun(TWO_DOORS, **kw)
        t = Tracer(run, {"id": "p1", "statement": "Adding an in-stock item raises the subtotal by its price."}, "p1/discovery")
        return t.run_trace(ANCHOR), run

    def test_finds_both_ways_in_and_the_route(self):
        out, _ = self.trace()
        browser = {(w["file"], w["page"], w["status"]) for w in out["ways"] if w["door"] == "browser"}
        self.assertEqual(browser, {("components/AddToCartButton.tsx", "/listings/:id", "found"), ("components/QuickAddButton.tsx", "/", "found")})
        self.assertEqual([w["route"] for w in out["ways"] if w["door"] == "api"], ["POST /api/cart"])
        self.assertEqual([(r["path"], r["name"]) for r in out["roots"]], [("lib/cart.ts", "addToCart")])
        quick = next(w for w in out["ways"] if w["file"] == "components/QuickAddButton.tsx")
        self.assertEqual([l["path"] for l in quick["chain"]], ["components/QuickAddButton.tsx", "components/ListingCard.tsx", "app/page.tsx"])

    def test_every_hit_is_accounted_for(self):
        out, run = self.trace()
        labelled = [r for r in out["rows"] if r["step"] != "start"]
        self.assertEqual(len(labelled), len(run.asked))                  # one labelled row per hit, none dropped
        self.assertTrue(all(r["label"] and r["outcome"] for r in out["rows"]))
        self.assertTrue({"start", "callers", "routes"} <= {r["step"] for r in out["rows"]})

    def test_a_dismissed_caller_that_is_on_a_page_is_still_a_way_in(self):
        # The labeller wrongly calls the Quick add request a mere mention. Its file is on the home page, so it is kept, "to check".
        out, _ = self.trace(mislabel={("components/QuickAddButton.tsx", 23): "mention"})
        quick = [w for w in out["ways"] if w["file"] == "components/QuickAddButton.tsx"]
        self.assertEqual([(w["page"], w["status"]) for w in quick], [("/", "to check")])

    def test_the_planner_cannot_drop_or_add_a_way_in(self):
        out, _ = self.trace()
        ways = out["ways"]
        ids = [w["id"] for w in ways if w["door"] == "browser"]
        wheres = [{"id": i, "where": "a page and a button"} for i in ids]
        ok = {"wheres": wheres, "cases": [{"id": f"c{n}", "entry_point": i, "what": "add once"} for n, i in enumerate(ids)], "not_covered": ""}
        self.assertIsNone(cases_problems(ok, ways))
        self.assertIn("has no case", cases_problems({**ok, "cases": ok["cases"][:1]}, ways))
        self.assertIn("cannot add ways in", cases_problems({**ok, "cases": ok["cases"] + [{"id": "x", "entry_point": "e99", "what": "y"}]}, ways))
        many = ok["cases"] + [{"id": f"x{n}", "entry_point": ids[0], "what": "y"} for n in range(13)]
        self.assertIn("too many cases", cases_problems({**ok, "cases": many}, ways))
        api = next(w["id"] for w in ways if w["door"] == "api")
        direct = {**ok, "cases": ok["cases"] + [{"id": "d1", "entry_point": api, "what": "post to the route"}]}
        self.assertIn("calls the API directly", cases_problems(direct, ways, never=False))      # the proof is through the pages
        self.assertIsNone(cases_problems(direct, ways, never=True))                             # unless something must never happen
        self.assertIn("is also tried by calling each route directly", cases_problems(ok, ways, never=True))
        plan = build_plan(ANCHOR, out, ok)
        self.assertTrue(plan["traced"])
        self.assertEqual({e["status"] for e in plan["entry_points"]}, {"found"})
        self.assertTrue(plan["code_refs"])


class TraceFindsAWriterThatBypassesTheFirst(unittest.TestCase):
    """A made-up app with two separate functions that add to the cart, each behind its own route and page. The trace
    is pointed at only one of them; the search for other writers of the same data has to find the second."""

    FILES = {
        "lib/store.ts": "export function readState() { return JSON.parse('{}'); }\nexport function writeState(s: unknown) {}\n",
        "lib/cart.ts": "import { readState, writeState } from './store';\nexport function addToCart(id: string) {\n  const state = readState();\n  state.cart.push({ id, quantity: 1 });\n  writeState(state);\n}\nexport function cartCount() {\n  return readState().cart.length;\n}\n",
        "lib/bundle.ts": "import { readState, writeState } from './store';\nexport function addBundle(ids: string[]) {\n  const s = readState();\n  for (const id of ids) s.cart.push({ id, quantity: 1 });\n  writeState(s);\n}\n",
        "lib/reset.ts": "import { readState, writeState } from './store';\nexport function emptyCart() {\n  const s = readState();\n  s.cart = [];\n  writeState(s);\n}\n",
        "app/api/cart/route.ts": "import { addToCart } from '@/lib/cart';\nexport async function POST(request: Request) {\n  addToCart('x');\n  return new Response('ok');\n}\n",
        "app/api/bundle/route.ts": "import { addBundle } from '@/lib/bundle';\nexport async function POST(request: Request) {\n  addBundle(['x', 'y']);\n  return new Response('ok');\n}\n",
        "components/Add.tsx": "'use client';\nexport function Add() {\n  return <button onClick={() => fetch('/api/cart', { method: 'POST' })}>Add</button>;\n}\n",
        "components/Bundle.tsx": "'use client';\nexport function Bundle() {\n  return <button onClick={() => fetch('/api/bundle', { method: 'POST' })}>Add bundle</button>;\n}\n",
        "components/Unused.tsx": "'use client';\nexport function Unused() {\n  return <button onClick={() => fetch('/api/cart', { method: 'POST' })}>Old add</button>;\n}\n",
        "app/page.tsx": "import { Add } from '@/components/Add';\nexport default function Home() {\n  return <Add />;\n}\n",
        "app/cart/page.tsx": "import { Bundle } from '@/components/Bundle';\nimport { cartCount } from '@/lib/cart';\nexport default function CartPage() {\n  return <div>{cartCount()}<Bundle /></div>;\n}\n",
    }

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        for rel, text in self.FILES.items():
            f = Path(self.tmp.name) / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text)

    def tearDown(self):
        self.tmp.cleanup()

    def test_second_writer_and_its_page_are_found_and_dead_code_is_excluded(self):
        run = FakeRun(Path(self.tmp.name))
        anchor = {**ANCHOR, "roots": [{"path": "lib/cart.ts", "line": 4}]}
        out = Tracer(run, {"id": "p1", "statement": "Adding an item raises the subtotal by its price."}, "p1/discovery").run_trace(anchor)
        self.assertEqual({(r["path"], r["name"]) for r in out["roots"]}, {("lib/cart.ts", "addToCart"), ("lib/bundle.ts", "addBundle")})
        browser = {(w["file"], w["page"], w["status"]) for w in out["ways"] if w["door"] == "browser"}
        self.assertEqual(browser, {("components/Add.tsx", "/", "found"), ("components/Bundle.tsx", "/cart", "found")})
        self.assertEqual({w["route"] for w in out["ways"] if w["door"] == "api"}, {"POST /api/cart", "POST /api/bundle"})
        self.assertEqual([e["file"] for e in out["excluded"]], ["components/Unused.tsx"])          # calls the route, shown nowhere
        other = [r for r in out["rows"] if r["step"] == "writers" and r["label"] == "other_write"]
        self.assertEqual([r["path"] for r in other], ["lib/reset.ts"])                              # empties the cart: a write, not this action


if __name__ == "__main__":
    unittest.main()
