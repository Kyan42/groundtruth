import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from toolserver.routes import format_routes, list_routes  # noqa: E402

CBAY = Path(os.environ.get("GT_SAMPLE_SHOP") or Path(__file__).resolve().parent.parent.parent / "cbay" / "app")     # the sample shop, kept outside this repository
DEAD = "http://127.0.0.1:1"


def write(root: Path, rel: str, text: str = "") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def make_next_tree(root: Path, manifest_keys=None) -> None:
    write(root, "app/(shop)/api/cart/route.ts",
          "export async function GET() {}\nexport function POST(req) {}\n")
    write(root, "app/api/orders/[id]/route.ts", "export const GET = async () => {}\n")
    write(root, "app/api/items/route.js",
          "async function PUT() {}\nfunction DELETE() {}\nexport { PUT, DELETE }\n")
    write(root, "app/api/docs/[...slug]/route.ts", "// export function GET() {}\nconst x = 1\n")
    write(root, "pages/api/legacy.ts", "export default function handler() {}\n")
    write(root, "app/node_modules/x/route.ts", "export function GET() {}\n")
    write(root, "app/.hidden/route.ts", "export function GET() {}\n")
    if manifest_keys is not None:
        manifest = {k: "app" + k + ".js" for k in manifest_keys}
        manifest["/page"] = "app/page.js"
        write(root, ".next/server/app-paths-manifest.json", json.dumps(manifest))


AGREEING = ["/(shop)/api/cart/route", "/api/orders/[id]/route", "/api/items/route",
            "/api/docs/[...slug]/route"]


def pairs(result):
    return [(r["method"], r["path"]) for r in result["routes"]]


class OpenAPIHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/openapi.json":
            body = json.dumps({"openapi": "3.0.0", "paths": {
                "/api/cart": {"get": {}, "post": {}, "parameters": []},
                "/api/orders/{id}": {"get": {}}}}).encode()
        elif self.path == "/notapi.json":
            body = b'{"hello": "world"}'
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class NextReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_files_only(self):
        make_next_tree(self.root)
        r = list_routes(self.root, DEAD, {"type": "next"})
        self.assertEqual(pairs(r), [
            ("GET", "/api/cart"), ("POST", "/api/cart"),
            ("ANY", "/api/docs/:slug*"),
            ("DELETE", "/api/items"), ("PUT", "/api/items"),
            ("ANY", "/api/legacy"),
            ("GET", "/api/orders/:id"),
        ])
        by = {(x["method"], x["path"]): x for x in r["routes"]}
        self.assertTrue(by[("GET", "/api/orders/:id")]["params"])
        self.assertTrue(by[("ANY", "/api/docs/:slug*")]["params"])
        self.assertFalse(by[("GET", "/api/cart")]["params"])
        self.assertEqual(by[("GET", "/api/cart")]["sources"], ["next-files"])
        self.assertTrue(r["agree"])  # no manifest, nothing to disagree with
        self.assertIn("no .next", r["note"])

    def test_agreeing_manifest(self):
        make_next_tree(self.root, AGREEING)
        r = list_routes(self.root, DEAD, {"type": "next"})
        self.assertTrue(r["agree"], r["disagreements"])
        self.assertEqual(r["disagreements"], [])
        self.assertEqual(len(r["routes"]), 7)
        by = {(x["method"], x["path"]): x for x in r["routes"]}
        self.assertEqual(by[("POST", "/api/cart")]["sources"], ["next-build", "next-files"])
        self.assertEqual(by[("ANY", "/api/legacy")]["sources"], ["next-files"])
        self.assertEqual([s["name"] for s in r["sources"]], ["next-build", "next-files"])
        self.assertEqual(r["sources"][0]["paths"], 4)
        text = format_routes(r)
        self.assertTrue(text.startswith("7 routes from next-build, next-files (sources agree)"))
        self.assertIn("GET /api/orders/:id [has parameters]", text)
        self.assertIn("\nPOST /api/cart\n", text)

    def test_disagreeing_manifest(self):
        make_next_tree(self.root, ["/(shop)/api/cart/route", "/api/ghost/route",
                                   "/api/orders/[id]/route", "/api/docs/[...slug]/route"])
        r = list_routes(self.root, DEAD, {"type": "next"})
        self.assertFalse(r["agree"])
        self.assertIn("/api/ghost: in next-build, missing from next-files", r["disagreements"])
        self.assertIn("/api/items: in next-files, missing from next-build", r["disagreements"])
        self.assertEqual(len(r["disagreements"]), 2)
        self.assertIn(("ANY", "/api/ghost"), pairs(r))
        self.assertIn("SOURCES DISAGREE", format_routes(r).splitlines()[0])

    def test_autodetect_next(self):
        make_next_tree(self.root, AGREEING)
        r = list_routes(self.root, DEAD)
        self.assertEqual(len(r["routes"]), 7)
        self.assertTrue(r["agree"])

    def test_zero_routes(self):
        r = list_routes(self.root, DEAD)
        self.assertEqual(r["routes"], [])
        self.assertEqual(r["sources"], [])
        self.assertIn("no route source found", r["note"])
        self.assertEqual(format_routes(r).splitlines()[0], "0 routes: no route source found")
        r2 = list_routes(None, DEAD)
        self.assertEqual(r2["routes"], [])

    def test_manifest_without_routes(self):
        write(self.root, ".next/server/app-paths-manifest.json",
              json.dumps({"/page": "app/page.js", "/favicon.ico/route": "app/favicon.ico/route.js"}))
        r = list_routes(self.root, DEAD, {"type": "next"})
        self.assertEqual(r["routes"], [])
        self.assertTrue(r["agree"])
        first = format_routes(r).splitlines()[0]
        self.assertTrue(first.startswith("0 routes:"), first)
        self.assertNotIn("sources agree", first)

    def test_file_source(self):
        write(self.root, "routes.json", json.dumps([{"method": "post", "path": "/api/a"},
                                                    {"method": "GET", "path": "/api/b/{id}"}]))
        r = list_routes(self.root, DEAD, {"type": "file", "path": "routes.json"})
        self.assertEqual(pairs(r), [("POST", "/api/a"), ("GET", "/api/b/:id")])
        self.assertIn("only one source", format_routes(r))
        with self.assertRaises(ValueError):
            list_routes(self.root, DEAD, {"type": "file", "path": "../outside.json"})
        with self.assertRaises(ValueError):
            list_routes(self.root, DEAD, {"type": "file", "path": "/etc/passwd"})
        missing = list_routes(self.root, DEAD, {"type": "file", "path": "nope.json"})
        self.assertEqual(missing["routes"], [])
        self.assertIn("unavailable", missing["note"])

    def test_command_source(self):
        r = list_routes(self.root, DEAD, {"type": "command",
                                          "cmd": "printf 'GET /api/x\\nDELETE /api/y\\n'"})
        self.assertEqual(pairs(r), [("GET", "/api/x"), ("DELETE", "/api/y")])
        failed = list_routes(self.root, DEAD, {"type": "command", "cmd": "exit 3"})
        self.assertEqual(failed["routes"], [])
        self.assertIn("exited 3", failed["note"])

    def test_invalid_source(self):
        for bad in ({"type": "nonsense"}, {"type": "command"}, {"type": "file"}, "next"):
            with self.assertRaises(ValueError):
                list_routes(self.root, DEAD, bad)
        with self.assertRaises(ValueError):
            list_routes(None, DEAD, {"type": "next"})


class OpenAPITests(unittest.TestCase):
    def setUp(self):
        self.server = HTTPServer(("127.0.0.1", 0), OpenAPIHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def test_registered_openapi(self):
        r = list_routes(None, self.url, {"type": "openapi", "path": "/openapi.json"})
        self.assertEqual(pairs(r), [("GET", "/api/cart"), ("POST", "/api/cart"),
                                    ("GET", "/api/orders/:id")])
        self.assertTrue(r["routes"][2]["params"])
        self.assertEqual(r["sources"][0]["name"], "openapi")

    def test_autodetect_openapi_quiet(self):
        r = list_routes(Path(self.tmp.name), self.url)
        self.assertEqual(len(r["routes"]), 3)
        dead = list_routes(Path(self.tmp.name), DEAD)
        self.assertEqual(dead["routes"], [])
        self.assertIn("no route source found", dead["note"])

    def test_missing_openapi(self):
        r = list_routes(None, self.url, {"type": "openapi", "path": "/missing.json"})
        self.assertEqual(r["routes"], [])
        self.assertIn("HTTP 404", r["note"])

    def test_non_openapi_json(self):
        r = list_routes(None, self.url, {"type": "openapi", "path": "/notapi.json"})
        self.assertEqual(r["routes"], [])
        self.assertIn("unavailable", r["note"])

    def test_rejects_non_app_relative_path(self):
        for bad in ("http://evil.example/openapi.json", "//evil.example/x",
                    "openapi.json", "@evil.example/x"):
            with self.assertRaises(ValueError, msg=bad):
                list_routes(None, self.url, {"type": "openapi", "path": bad})

    def test_openapi_disagrees_with_next(self):
        root = Path(self.tmp.name)
        write(root, "app/api/cart/route.ts", "export function GET() {}\n")
        r = list_routes(root, self.url)
        self.assertFalse(r["agree"])
        self.assertIn("/api/orders/:id: in openapi, missing from next-files", r["disagreements"])
        by = {(x["method"], x["path"]): x for x in r["routes"]}
        self.assertEqual(by[("GET", "/api/cart")]["sources"], ["next-files", "openapi"])


@unittest.skipUnless(CBAY.is_dir() and (CBAY / ".next").is_dir(), "cbay app or its .next build missing")
class RealAppTest(unittest.TestCase):
    def test_cbay_routes(self):
        r = list_routes(CBAY, DEAD, {"type": "next"})
        self.assertEqual(pairs(r), [("GET", "/api/cart"), ("POST", "/api/cart"),
                                    ("GET", "/api/health"), ("POST", "/api/orders"),
                                    ("POST", "/api/watchlist")])
        self.assertTrue(r["agree"], r["disagreements"])


if __name__ == "__main__":
    unittest.main()
