import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from toolserver.trace import (  # noqa: E402
    address_literals, climb, computed_addresses, context, enclosing, exported_names,
    looks_like_write, pages, referencers, search,
)

REPO = Path(__file__).resolve().parents[1]
CBAY = REPO / "gates" / "work" / "cbay-two-doors"
DEMO = REPO / "demo"


def write(root: Path, rel: str, text: str = "") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


class Tmp(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def w(self, rel, text=""):
        write(self.root, rel, text)


# ------------------------------------------------------------------ search / context

class SearchTests(Tmp):
    def test_hits_sorted_and_stripped(self):
        self.w("b.py", "x = 1\n    foo()  \n")
        self.w("a/z.js", "foo();\nbar();\nfoo()\n")
        r = search(self.root, r"foo")
        self.assertEqual([(h["path"], h["line"], h["text"]) for h in r["hits"]],
                         [("a/z.js", 1, "foo();"), ("a/z.js", 3, "foo()"), ("b.py", 2, "foo()")])
        self.assertEqual(r["total"], 3)
        self.assertFalse(r["truncated"])

    def test_text_cut_to_200(self):
        self.w("a.txt", "foo" + "x" * 500)
        self.assertEqual(len(search(self.root, "foo")["hits"][0]["text"]), 200)

    def test_truncation_counts_total(self):
        self.w("a.txt", "hit\n" * 10)
        r = search(self.root, "hit", max_hits=3)
        self.assertEqual(len(r["hits"]), 3)
        self.assertEqual(r["total"], 10)
        self.assertTrue(r["truncated"])

    def test_hidden_skipped_and_oversized_and_binary(self):
        for rel in ("node_modules/x.js", "__pycache__/x.py", ".next/x.js", "dist/x.js", "build/x.js",
                    ".env", ".hidden/x.js", "src/.secret.js"):
            self.w(rel, "needle\n")
        self.w("big.js", "needle\n" + "x" * 1_100_000)
        (self.root / "bin.dat").write_bytes(b"needle\x00\x01")
        self.w("src/ok.js", "needle\n")
        self.assertEqual([h["path"] for h in search(self.root, "needle")["hits"]], ["src/ok.js"])

    def test_symlink_out_of_repo_not_followed(self):
        with tempfile.TemporaryDirectory() as other:
            write(Path(other), "x.js", "needle\n")
            try:
                os.symlink(other, self.root / "linked")
                os.symlink(Path(other) / "x.js", self.root / "file.js")
            except OSError:
                self.skipTest("symlinks not available")
            self.assertEqual(search(self.root, "needle")["total"], 0)

    def test_invalid_regex(self):
        with self.assertRaises(ValueError):
            search(self.root, "(unclosed")

    def test_context(self):
        self.w("a.py", "".join(f"l{i}\n" for i in range(1, 41)))
        r = context(self.root, "a.py", 20)
        self.assertEqual((r["start"], r["end"], len(r["lines"])), (13, 27, 15))
        self.assertEqual(r["lines"][7], "l20")
        r = context(self.root, "a.py", 2, radius=30)
        self.assertEqual((r["start"], r["end"]), (1, 9))
        self.assertEqual(context(self.root, "a.py", 40)["end"], 40)

    def test_context_rejects_outside_and_hidden(self):
        self.w(".env", "SECRET=1\n")
        self.w("node_modules/x.js", "x\n")
        for bad in ("../etc/passwd", "/etc/hosts", ".env", "node_modules/x.js"):
            with self.assertRaises(ValueError):
                context(self.root, bad, 1)


# ------------------------------------------------------------------ enclosing

class EnclosingTests(Tmp):
    def enc(self, rel, line):
        r = enclosing(self.root, rel, line)
        return r["name"], r["kind"], r["line"]

    def test_js_nested_arrow_class(self):
        self.w("a.ts", "\n".join([
            "import x from 'y';",                                    # 1
            "export function outer({ a }: { a: string }) {",         # 2
            "  const s = `x ${ { y: 1 }.y } }{`;",                   # 3
            "  async function inner() {",                            # 4
            "    return 1;",                                         # 5
            "  }",                                                   # 6
            "  return inner;",                                       # 7
            "}",                                                     # 8
            "const arrow = async (n: number) => {",                  # 9
            "  // a } brace in a comment",                           # 10
            "  return n + 1;",                                       # 11
            "};",                                                    # 12
            "class Store extends Base {",                            # 13
            "  private items = [];",                                 # 14
            "  add(item: string): void {",                           # 15
            "    this.items.push(item);",                            # 16
            "  }",                                                   # 17
            "  handle = (e) => {",                                   # 18
            "    go(e);",                                            # 19
            "  };",                                                  # 20
            "}",                                                     # 21
            "const short = (x) => x * 2;",                           # 22
            "let after = 1;",                                        # 23
            "function typed(): { ok: boolean } {",                   # 24
            "  return { ok: true };",                                # 25
            "}",                                                     # 26
            "export default function () {",                          # 27
            "  return 0;",                                           # 28
            "}",                                                     # 29
            "const Comp = () => (",                                  # 30
            "  <p>Don't stop</p>",                                   # 31
            ");",                                                    # 32
            "const f2 = function (a) {",                             # 33
            "  return a;",                                           # 34
            "};",                                                    # 35
        ]) + "\n")
        self.assertEqual(self.enc("a.ts", 1), (None, "module", None))
        self.assertEqual(self.enc("a.ts", 3), ("outer", "function", 2))
        self.assertEqual(self.enc("a.ts", 5), ("inner", "function", 4))
        self.assertEqual(self.enc("a.ts", 7), ("outer", "function", 2))
        self.assertEqual(self.enc("a.ts", 11), ("arrow", "function", 9))
        self.assertEqual(self.enc("a.ts", 14), ("Store", "class", 13))
        self.assertEqual(self.enc("a.ts", 16), ("add", "function", 15))
        self.assertEqual(self.enc("a.ts", 19), ("handle", "function", 18))
        self.assertEqual(self.enc("a.ts", 22), ("short", "function", 22))
        self.assertEqual(self.enc("a.ts", 23), (None, "module", None))
        self.assertEqual(self.enc("a.ts", 25), ("typed", "function", 24))
        self.assertEqual(self.enc("a.ts", 28), ("default", "function", 27))
        self.assertEqual(self.enc("a.ts", 31), ("Comp", "function", 30))
        self.assertEqual(self.enc("a.ts", 34), ("f2", "function", 33))

    def test_js_broken_braces_prefers_outermost(self):
        self.w("b.js", "function top() {\n  function inner() {\n    x();\n")
        r = enclosing(self.root, "b.js", 3)
        self.assertEqual((r["name"], r["approx"]), ("top", True))

    def test_python(self):
        self.w("m.py", "\n".join([
            "import os",                       # 1
            "class A:",                        # 2
            "    x = 1",                       # 3
            "    def meth(self):",             # 4
            "        def helper():",           # 5
            "            return 1",            # 6
            "        return helper()",         # 7
            "",                                # 8
            "if True:",                        # 9
            "    y = 2",                       # 10
            "async def run():",                # 11
            "    await go()",                  # 12
        ]) + "\n")
        self.assertEqual(self.enc("m.py", 1), (None, "module", None))
        self.assertEqual(self.enc("m.py", 3), ("A", "class", 2))
        self.assertEqual(self.enc("m.py", 6), ("helper", "function", 5))
        self.assertEqual(self.enc("m.py", 7), ("meth", "function", 4))
        self.assertEqual(self.enc("m.py", 10), (None, "module", None))
        self.assertEqual(self.enc("m.py", 12), ("run", "function", 11))

    def test_html(self):
        self.w("static/index.html", "<html></html>\n")
        self.assertEqual(self.enc("static/index.html", 1), (None, "page", None))


# ------------------------------------------------------------------ exported_names

class ExportedNamesTests(Tmp):
    def test_js(self):
        self.w("a.ts", "\n".join([
            "export function f() {}",
            "export async function g() {}",
            "export const C = 1;",
            "export class K {}",
            "function local() {}",
            "const A = 1, B = 2;",
            "export { A, B as Bee };",
            "export default function Main() {}",
        ]) + "\n")
        names = exported_names(self.root, "a.ts")
        for n in ("f", "g", "C", "K", "A", "Bee", "default", "Main"):
            self.assertIn(n, names)
        self.assertNotIn("local", names)
        self.assertNotIn("B", names)

    def test_python(self):
        self.w("p.py", "def a():\n    def inner(): pass\nclass B:\n    def m(self): pass\nasync def c(): pass\n")
        self.assertEqual(exported_names(self.root, "p.py"), ["a", "B", "c"])


# ------------------------------------------------------------------ pages

class PagesTests(Tmp):
    def test_app_router(self):
        for rel in ("app/page.tsx", "app/(shop)/cart/page.tsx", "app/blog/[slug]/page.jsx",
                    "app/docs/[...slug]/page.tsx", "app/@modal/login/page.tsx", "app/api/x/route.ts",
                    "app/layout.tsx", "src/app/about/page.js"):
            self.w(rel, "export default function P() {}\n")
        self.assertEqual(pages(self.root), [
            {"file": "app/page.tsx", "url": "/"},
            {"file": "src/app/about/page.js", "url": "/about"},
            {"file": "app/blog/[slug]/page.jsx", "url": "/blog/:slug"},
            {"file": "app/(shop)/cart/page.tsx", "url": "/cart"},
            {"file": "app/docs/[...slug]/page.tsx", "url": "/docs/:slug*"},
            {"file": "app/@modal/login/page.tsx", "url": "/login"},
        ])

    def test_pages_router(self):
        for rel in ("pages/index.tsx", "pages/about.tsx", "pages/_app.tsx", "pages/api/hello.ts",
                    "src/pages/posts/[id].tsx"):
            self.w(rel, "x\n")
        self.assertEqual(pages(self.root), [
            {"file": "pages/index.tsx", "url": "/"},
            {"file": "pages/about.tsx", "url": "/about"},
            {"file": "src/pages/posts/[id].tsx", "url": "/posts/:id"},
        ])

    def test_plain_html(self):
        for rel in ("static/index.html", "static/docs/help.html", "web/public/index.html",
                    "other/page.html", "templates/admin/index.html"):
            self.w(rel, "<html></html>\n")
        self.assertEqual(pages(self.root), [
            {"file": "static/index.html", "url": "/"},
            {"file": "web/public/index.html", "url": "/"},
            {"file": "templates/admin/index.html", "url": "/admin"},
            {"file": "static/docs/help.html", "url": "/docs/help.html"},
            {"file": "other/page.html", "url": "/other/page.html"},
        ])


# ------------------------------------------------------------------ referencers / climb

class ReferencersTests(Tmp):
    def test_js_imports_aliases_and_uses(self):
        self.w("lib/util.ts", "export function helper() {}\nexport default function Main() {}\n")
        self.w("components/Widget.tsx", "\n".join([
            "import Renamed, {",                # 1
            "  helper as h,",                   # 2
            "} from '@/lib/util';",             # 3
            "export function Widget() {",       # 4
            "  h();",                           # 5
            "  return <Renamed />;",            # 6
            "}",
        ]) + "\n")
        self.w("components/index.ts", "import * as U from '../lib/util';\nU.helper();\n")
        self.w("src/x.js", "const u = require('../lib/util.js');\n")
        self.w("other.ts", "import { helper } from './somewhere-else';\nhelper();\n")
        refs = referencers(self.root, "lib/util.ts")
        got = {(r["path"], r["line"], r["via"]) for r in refs}
        self.assertIn(("components/Widget.tsx", 3, "import"), got)
        self.assertIn(("components/Widget.tsx", 5, "h"), got)
        self.assertIn(("components/Widget.tsx", 6, "Renamed"), got)
        self.assertIn(("components/index.ts", 1, "import"), got)
        self.assertIn(("components/index.ts", 2, "U"), got)
        self.assertIn(("src/x.js", 1, "import"), got)
        self.assertFalse(any(p == "other.ts" or p == "lib/util.ts" for p, _, _ in got))
        self.assertEqual(refs, sorted(refs, key=lambda r: (r["path"], r["line"], r["via"])))

    def test_js_index_and_src_alias_and_dynamic(self):
        self.w("src/components/Button/index.tsx", "export function Button() {}\n")
        self.w("src/app/page.tsx", "import { Button } from '@/components/Button';\nexport default () => <Button />;\n")
        self.w("src/lazy.ts", "const B = dynamic(() => import('./components/Button'));\n")
        self.w("src/reexport.ts", "export { Button } from './components/Button/index';\n")
        paths = {r["path"] for r in referencers(self.root, "src/components/Button/index.tsx")}
        self.assertEqual(paths, {"src/app/page.tsx", "src/lazy.ts", "src/reexport.ts"})

    def test_python_package_and_relative(self):
        self.w("pkg/__init__.py", "")
        self.w("pkg/mod.py", "def work():\n    return 1\n")
        self.w("main.py", "from pkg import mod\nmod.work()\n")
        self.w("cli.py", "import pkg.mod as m\nm.work()\n")
        self.w("pkg/sibling.py", "from .mod import work\nwork()\n")
        self.w("unrelated.py", "import os\nwork()\n")
        got = {(r["path"], r["line"], r["via"]) for r in referencers(self.root, "pkg/mod.py")}
        self.assertEqual(got, {("main.py", 1, "import"), ("main.py", 2, "mod"), ("main.py", 2, "work"),
                               ("cli.py", 1, "import"), ("cli.py", 2, "m"), ("cli.py", 2, "work"),
                               ("pkg/sibling.py", 1, "import"), ("pkg/sibling.py", 2, "work")})

    def test_html_script_tag(self):
        self.w("static/index.html", '<html><script src="/js/app.js"></script></html>\n')
        self.w("static/js/app.js", "fetch('/api/x');\n")
        refs = referencers(self.root, "static/js/app.js")
        self.assertEqual([(r["path"], r["via"]) for r in refs], [("static/index.html", "script")])
        r = climb(self.root, "static/js/app.js")
        self.assertEqual(r["pages"], [{"file": "static/index.html", "url": "/"}])


class ClimbTests(Tmp):
    def setUp(self):
        super().setUp()
        self.w("app/layout.tsx", "import { Nav } from '@/components/Nav';\nexport default function L({children}) {\n  return <div><Nav />{children}</div>;\n}\n")
        self.w("app/(shop)/layout.tsx", "export default function S({children}) { return children; }\n")
        self.w("app/page.tsx", "import { Card } from '../components/Card';\nexport default function H() {\n  return <Card />;\n}\n")
        self.w("app/(shop)/cart/page.tsx", "export default function C() { return null; }\n")
        self.w("app/about/page.tsx", "export default function A() { return null; }\n")
        self.w("components/Card.tsx", "import Btn from './Btn';\nexport function Card() {\n  return <Btn />;\n}\n")
        self.w("components/Btn.tsx", "export default function Button() {\n  return <button />;\n}\n")
        self.w("components/Nav.tsx", "export function Nav() { return null; }\n")
        self.w("components/Orphan.tsx", "export function Orphan() { return null; }\n")

    def test_component_used_only_as_jsx_through_default_alias(self):
        r = climb(self.root, "components/Btn.tsx")
        self.assertTrue(r["reaches_page"])
        self.assertEqual(r["pages"], [{"file": "app/page.tsx", "url": "/"}])
        self.assertEqual([[(l["path"], l["line"], l["via"]) for l in c] for c in r["chains"]],
                         [[("components/Card.tsx", 3, "Btn"), ("app/page.tsx", 3, "Card")]])

    def test_layout_covers_every_page_under_it(self):
        r = climb(self.root, "components/Nav.tsx")
        self.assertEqual([p["url"] for p in r["pages"]], ["/", "/about", "/cart"])
        self.assertEqual(r["chains"][0][-1]["path"], "app/layout.tsx")
        r = climb(self.root, "app/(shop)/layout.tsx")
        self.assertEqual([p["url"] for p in r["pages"]], ["/cart"])

    def test_page_start_and_dead_end(self):
        r = climb(self.root, "app/about/page.tsx")
        self.assertEqual((r["chains"], [p["url"] for p in r["pages"]]), ([[]], ["/about"]))
        r = climb(self.root, "components/Orphan.tsx")
        self.assertEqual((r["chains"], r["pages"], r["reaches_page"]), ([], [], False))
        self.assertEqual(r["dead_ends"], ["components/Orphan.tsx"])

    def test_depth_limit_sets_truncated(self):
        r = climb(self.root, "components/Btn.tsx", max_depth=1)
        self.assertTrue(r["truncated"])
        self.assertFalse(r["reaches_page"])

    def test_chain_cap(self):
        self.w("lib/core.ts", "export function core() {}\n")
        for i in range(60):
            self.w(f"app/p{i}/page.tsx", "import { core } from '@/lib/core';\ncore();\n")
        r = climb(self.root, "lib/core.ts")
        self.assertEqual(len(r["chains"]), 50)
        self.assertTrue(r["truncated"])


# ------------------------------------------------------------------ looks_like_write

class WriteTests(unittest.TestCase):
    def test_writes(self):
        for text, name in [
            ("cart = []", "cart"), ("cart.total = 5", "cart"), ("cart[i] = x", "cart"),
            ("cart[a[0]] = x", "cart"), ("count += 1", "count"), ("count -= 1", "count"),
            ("x ||= y", "x"), ("count++", "count"), ("--count", "count"), ("delete cart.items[k]", "cart"),
            ("del cache[key]", "cache"), ("state.cart.push({ id })", "state.cart"),
            ("this.state.cart.splice(0, 1)", "state.cart"), ("const last = items.pop()", "items"),
            ("items.sort()", "items"), ("m.set(k, v)", "m"), ("s.add(1)", "s"), ("lst.append(1)", "lst"),
            ("d.setdefault('k', [])", "d"), ("f.write(data)", "f"), ("user.save()", "user"),
            ("d.update(x=1)", "d"), ("[a, total] = pair", "total"), ("({ listingId } = body)", "listingId"),
            ("total, rest = pair", "total"), ("Object.assign(cart, patch)", "cart"),
            ("writeState(state)", "state"), ("x: int = 3", "x"), ("existing.quantity += 1", "existing"),
            ("state.cart = []", "state"), ("for item in items:", "item"),
        ]:
            with self.subTest(text=text, name=name):
                self.assertTrue(looks_like_write(text, name))

    def test_reads(self):
        for text, name in [
            ("if (cart == null) {", "cart"), ("if (cart === other) {", "cart"), ("cart => cart.id", "cart"),
            ("const n = cart.length;", "cart"), ("const existing = state.cart.find((l) => l.id === id);", "state.cart"),
            ("let total = count + 1", "count"), ("type T = typeof cart", "cart"), ("return cart.items", "cart"),
            ("if (a <= count) {}", "count"), ("if (a >= count) {}", "count"), ("x != count", "count"),
            ("console.log(cart)", "cart"), ("for (const line of state.cart) {", "state.cart"),
            ("unrelated = 1", "cart"), ("const cartTotal = 1", "cart"), ("len(items)", "items"),
        ]:
            with self.subTest(text=text, name=name):
                self.assertFalse(looks_like_write(text, name))


# ------------------------------------------------------------------ addresses

class AddressTests(Tmp):
    def test_computed_js(self):
        self.w("a.ts", "\n".join([
            "fetch('/api/plain');",                        # 1 not computed
            "fetch(`/api/items/${id}`);",                  # 2 template
            "fetch('/api/' + id);",                        # 3 concat
            "fetch(url);",                                 # 4 variable
            "fetch(buildUrl(id));",                        # 5 call
            "axios.post(`/api/${x}`, body);",              # 6
            "axios('/api/plain');",                        # 7 not computed
            "$.ajax(endpoint);",                           # 8
            "new URL(path, base);",                        # 9
            "navigator.sendBeacon(u, data);",              # 10
            "xhr.open('POST', target);",                   # 11 second arg
            "xhr.open('GET', '/api/plain');",              # 12 not computed
            "fetch(",                                      # 13 first arg on next line
            "  apiBase + '/x',",                           # 14
            ");",                                          # 15
            "<form action={submitOrder}>",                 # 16
            "<form action=\"/plain\">",                    # 17 not computed
            "dialog.open();",                              # 18 not XHR
        ]) + "\n")
        got = {r["line"]: r["why"] for r in computed_addresses(self.root)}
        self.assertEqual(sorted(got), [2, 3, 4, 5, 6, 8, 9, 10, 11, 13, 16])
        self.assertIn("template", got[2])
        self.assertIn("concatenation", got[3])
        self.assertIn("variable", got[4])
        self.assertIn("function call", got[5])
        self.assertIn("second argument", got[11])
        self.assertIn("form action", got[16])

    def test_computed_python_and_html(self):
        self.w("c.py", "\n".join([
            "requests.get('http://x/api')",                # 1 no
            "requests.get(f'{BASE}/api/{id}')",            # 2 f-string
            "httpx.post('/api/%s' % id)",                  # 3 %
            "urlopen('/api/{}'.format(id))",               # 4 format
            "requests.put(BASE + '/x')",                   # 5 concat
            "requests.delete(url)",                        # 6 variable
            "requests.request('GET', url)",                # 7 second argument
            "requests.request('GET', '/plain')",           # 8 no
        ]) + "\n")
        self.w("static/index.html", "<script>\nfetch('/api/' + x);\n</script>\n")
        got = {(r["path"], r["line"]): r["why"] for r in computed_addresses(self.root)}
        self.assertEqual(sorted(got), [("c.py", 2), ("c.py", 3), ("c.py", 4), ("c.py", 5), ("c.py", 6),
                                       ("c.py", 7), ("static/index.html", 2)])
        self.assertIn("f-string", got[("c.py", 2)])
        self.assertIn("%", got[("c.py", 3)])
        self.assertIn("format", got[("c.py", 4)])

    def test_address_literals(self):
        self.w("a.ts", "fetch('/api/cart');\nrouter.push(`/orders/${id}`);\nconst u = 'http://x/y';\n"
                       "import x from './y';\nconst c = '// no';\n<Link href=\"/\">\n")
        got = [(r["line"], r["address"]) for r in address_literals(self.root, "a.ts")]
        self.assertEqual(got, [(1, "/api/cart"), (2, "/orders/${id}"), (6, "/")])


# ------------------------------------------------------------------ real projects (read-only)

@unittest.skipUnless(CBAY.is_dir(), f"real project missing: {CBAY}")
class CbayTests(unittest.TestCase):
    def test_search_addToCart(self):
        r = search(CBAY, r"\baddToCart\b")
        self.assertEqual([(h["path"], h["line"]) for h in r["hits"]], [
            ("app/api/cart/route.ts", 3), ("app/api/cart/route.ts", 20),
            ("components/AddToCartButton.tsx", 11), ("components/AddToCartButton.tsx", 40),
            ("lib/cart.ts", 22),
        ])
        self.assertFalse(r["truncated"])

    def test_search_api_cart(self):
        r = search(CBAY, r"/api/cart")
        self.assertEqual([(h["path"], h["line"]) for h in r["hits"]],
                         [("components/AddToCartButton.tsx", 15), ("components/QuickAddButton.tsx", 23)])

    def test_enclosing(self):
        r = enclosing(CBAY, "lib/cart.ts", 36)
        self.assertEqual((r["name"], r["kind"], r["line"]), ("addToCart", "function", 22))
        r = enclosing(CBAY, "components/AddToCartButton.tsx", 15)
        self.assertEqual((r["name"], r["kind"], r["line"], r["approx"]), ("addToCart", "function", 11, False))
        r = enclosing(CBAY, "components/AddToCartButton.tsx", 40)
        self.assertEqual(r["name"], "AddToCartButton")

    def test_pages(self):
        self.assertEqual(pages(CBAY), [
            {"file": "app/page.tsx", "url": "/"},
            {"file": "app/cart/page.tsx", "url": "/cart"},
            {"file": "app/listings/[id]/page.tsx", "url": "/listings/:id"},
            {"file": "app/orders/[id]/page.tsx", "url": "/orders/:id"},
        ])

    def test_climb_quick_add(self):
        r = climb(CBAY, "components/QuickAddButton.tsx")
        self.assertEqual([p["url"] for p in r["pages"]], ["/"])
        self.assertEqual([[(l["path"], l["line"]) for l in c] for c in r["chains"]],
                         [[("components/ListingCard.tsx", 27), ("app/page.tsx", 36)]])

    def test_climb_add_to_cart_button(self):
        r = climb(CBAY, "components/AddToCartButton.tsx")
        self.assertEqual([p["url"] for p in r["pages"]], ["/listings/:id"])
        self.assertEqual(r["chains"][0][-1]["line"], 46)

    def test_climb_header_through_layout(self):
        r = climb(CBAY, "components/Header.tsx")
        self.assertEqual([p["url"] for p in r["pages"]], ["/", "/cart", "/listings/:id", "/orders/:id"])
        self.assertEqual([[l["path"] for l in c] for c in r["chains"]], [["app/layout.tsx"]])

    def test_climb_cart_lib_and_seed(self):
        r = climb(CBAY, "lib/cart.ts")
        self.assertTrue(r["reaches_page"])
        firsts = {c[0]["path"] for c in r["chains"]}
        self.assertEqual(firsts, {"app/cart/page.tsx", "components/Header.tsx"})
        self.assertIn("app/api/cart/route.ts", r["dead_ends"])
        r = climb(CBAY, "scripts/seed.mjs")
        self.assertEqual((r["pages"], r["reaches_page"], r["chains"]), ([], False, []))


    def test_addresses_and_writes(self):
        self.assertEqual(computed_addresses(CBAY), [])  # every fetch in cbay uses a plain literal
        got = [(r["line"], r["address"]) for r in address_literals(CBAY, "components/AddToCartButton.tsx")]
        self.assertEqual(got, [(15, "/api/cart")])
        lines = (CBAY / "lib/cart.ts").read_text().splitlines()
        self.assertTrue(looks_like_write(lines[35], "state.cart"))    # line 36: push
        self.assertFalse(looks_like_write(lines[31], "state.cart"))   # line 32: find
        self.assertTrue(looks_like_write(lines[33], "existing"))      # line 34: += 1


@unittest.skipUnless(DEMO.is_dir(), f"real project missing: {DEMO}")
class DemoTests(unittest.TestCase):
    def test_pages(self):
        self.assertEqual(pages(DEMO), [{"file": "static/index.html", "url": "/"}])

    def test_search_render_route(self):
        paths = {h["path"] for h in search(DEMO, "/api/render")["hits"]}
        self.assertEqual(paths, {"static/index.html", "app.py"})

    def test_referencers_render(self):
        self.assertIn("app.py", {r["path"] for r in referencers(DEMO, "render.py")})

    def test_enclosing_render(self):
        lines = (DEMO / "render.py").read_text().splitlines()
        start = next(i for i, l in enumerate(lines, 1) if l.startswith("def render("))
        r = enclosing(DEMO, "render.py", start + 2)
        self.assertEqual((r["name"], r["kind"], r["line"]), ("render", "function", start))


if __name__ == "__main__":
    unittest.main()
