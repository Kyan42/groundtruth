"""The run document sent to the results store: what it carries, and what it must never carry."""
import json
import tempfile
import unittest
from pathlib import Path

from toolserver import rundoc


def make_run(root: Path):
    repo, run = root / "secret-repo-name", root / "secret-repo-name" / ".groundtruth" / "runs" / "20261110-120000"
    (repo / ".groundtruth" / "features").mkdir(parents=True)
    (repo / ".groundtruth" / "features" / "cart.yml").write_text(
        "feature: cart\nproperties:\n  - id: p1\n    statement: Adding raises the subtotal.\n"
        "  - id: p2\n    statement: A sold-out item can't be added.\n")
    unit = run / "p1" / "c1"
    unit.mkdir(parents=True)
    (unit / "clip-1.webm").write_bytes(b"webm")
    (unit / "shot-1.png").write_bytes(b"png")
    t0 = 1000.0
    receipts = [
        {"t": t0 - 2, "tool": "process_info", "args": {}, "result": "4242 1  0.0  14496 /Users/somebody/app/bin/python app.py 8765"},
        {"t": t0 - 2, "tool": "gpu_check", "args": {}, "result": "pid 4242: GPU driver not loaded"},
        {"t": t0 + 0.1, "tool": "navigate", "args": {"path": "/cart"}, "result": "Now at http://127.0.0.1:3100/cart"},
        {"t": t0 + 1.5, "tool": "click", "args": {"ref": "e12"}, "result": 'clicked button "Add to cart"'},
        {"t": t0 + 2.0, "tool": "click", "args": {"role": "button", "name": "Nope"}, "result": "error: Locator.click: Timeout"},
        {"t": t0 + 3.0, "tool": "http_request", "args": {"method": "POST", "path": "/api/cart", "body": "{\"listingId\":\"l6\"}"},
         "result": "409\n{\"error\":\"This item is no longer available.\"}"},
        {"t": t0 + 4.0, "tool": "screenshot", "args": {"label": "cart after"}, "result": "shot-1"},
    ]
    (unit / "receipts.jsonl").write_text("\n".join(json.dumps(r) for r in receipts))
    case = {"id": "c1", "entry_point": "e1", "door": "browser", "what": "Add an item.", "represents": "Basic add.",
            "result": {"outcome": "holds", "observed": "Subtotal $1 then $2.", "screenshots": ["shot-1"],
                       "baseline": "process_info:\n4242 1  0.0  14000 /Users/somebody/app/bin/python app.py\ngpu_check:\npid 4242: GPU driver not loaded",
                       "clips": [{"file": "clip-1.webm", "started": t0}, {"file": "../../x.webm", "started": t0}],
                       "meta": {"seconds": 9.5, "cost_usd": 0.04, "attempts": 1}}}
    gone = {"id": "c2", "entry_point": "e2", "door": "browser", "what": "Look for a control.", "represents": "",
            "result": {"outcome": "outside", "nothing_there": True, "observed": "No way to do the action was found there."}}
    run_json = {
        "run": "20261110-120000", "feature": "cart", "goal": "Carts work.",
        "env": {"url": "http://127.0.0.1:3100", "pid": 4242, "repo": str(repo), "reset": "npm run seed",
                "facts": {"commit": "abc1234", "command": "/Users/somebody/app/bin/node server.js", "started": "Tue", "platform": "macOS"}},
        "service": {"url": "http://127.0.0.1:8790", "run": "x", "version": "3", "model": "m"},
        "properties": [{"id": "p1", "statement": "Adding raises the subtotal.", "verdict": "holds",
                        "plan": {"kind": "behaviour", "traced": True, "applies_to": "items", "action": "add", "compares": "subtotal",
                                 "how_found": "search", "not_covered": "",
                                 "entry_points": [{"id": "e1", "door": "browser", "status": "found", "where": "Listing page", "page": "/listings/:id", "file": "components/Add.tsx"},
                                                  {"id": "e2", "door": "browser", "status": "to check", "where": "Header", "page": "/", "file": "components/Header.tsx"}]},
                        "trace": {"excluded": [{"file": "lib/old.ts", "why": "writes the cart", "because": "it is not shown on any page"}], "notes": ["a note"]},
                        "discovery_meta": {"seconds": 30, "cost_usd": 0.1},
                        "cases": [case, gone]}],
    }
    (run / "run.json").write_text(json.dumps(run_json))
    return repo, run


class RunDocument(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo, self.run = make_run(Path(self.tmp.name))
        self.doc, self.files = rundoc.build(self.run, repo=self.repo, project="shop")

    def tearDown(self):
        self.tmp.cleanup()

    def test_it_never_carries_the_repo_path_the_process_id_or_a_command_line(self):
        text = json.dumps(self.doc)
        for secret in ("secret-repo-name", "/Users/", "4242", "server.js", "app.py", "npm run seed", str(self.tmp.name)):
            self.assertNotIn(secret, text, secret)
        self.assertEqual(self.doc["ran_against"]["commit"], "abc1234")
        self.assertTrue(self.doc["ran_against"]["could_measure"] and self.doc["ran_against"]["had_reset"])

    def test_every_property_the_owner_wrote_is_there_run_or_not(self):
        self.assertEqual([(p["id"], p["verdict"]) for p in self.doc["properties"]], [("p1", "holds"), ("p2", "not_run")])
        self.assertEqual(self.doc["properties"][1]["statement"], "A sold-out item can't be added.")
        self.assertEqual(self.doc["totals"]["properties"]["not_run"], 1)

    def test_steps_are_in_plain_words_and_timed_from_the_start_of_the_film(self):
        c = self.doc["properties"][0]["cases"][0]
        by_words = {s["words"]: s for s in c["steps"]}
        self.assertEqual(by_words["open /cart"]["at"], 0.1)
        self.assertEqual(by_words['click button "Add to cart"']["at"], 1.5)
        self.assertEqual(by_words["measure the app's process"]["at"], None)            # before the film started
        failed = next(s for s in c["steps"] if not s["ok"])
        self.assertIn("Timeout", failed["problem"])
        self.assertNotIn("e12", json.dumps(c["steps"]))                                 # no page references

    def test_requests_readings_films_and_screenshots(self):
        c = self.doc["properties"][0]["cases"][0]
        self.assertEqual(c["requests"], [{"method": "POST", "path": "/api/cart", "body": '{"listingId":"l6"}', "status": 409,
                                          "response": '{"error":"This item is no longer available."}', "at": 3.0}])
        self.assertEqual([r["text"] for r in c["readings"]],
                         ["CPU 0.0%, memory 14000 KB", "GPU driver not loaded", "CPU 0.0%, memory 14496 KB", "GPU driver not loaded"])
        self.assertEqual(c["clips"], [{"file": "p1/c1/clip-1.webm", "started": 1000.0}])    # a path in a clip's name is never followed
        self.assertEqual(c["shots"], [{"id": "shot-1", "file": "p1/c1/shot-1.png", "label": "cart after", "at": 4.0, "cited": True}])
        self.assertEqual(sorted((f["path"], f["kind"]) for f in self.files), [("p1/c1/clip-1.webm", "clip"), ("p1/c1/shot-1.png", "shot")])
        self.assertTrue(all(f["local"].is_file() for f in self.files))

    def test_outcomes_ways_in_and_what_was_left_out(self):
        p = self.doc["properties"][0]
        self.assertEqual([c["outcome"] for c in p["cases"]], ["holds", "nothing_there"])
        self.assertEqual(p["counts"]["nothing_there"], 1)
        self.assertEqual([(w["id"], w["status"]) for w in p["ways_in"]], [("e1", "found"), ("e2", "to check")])
        self.assertEqual(p["left_out"], [{"file": "lib/old.ts", "why": "writes the cart", "because": "it is not shown on any page"}])
        self.assertEqual(p["cases"][0]["stands_for"], "Basic add.")
        self.assertEqual((p["seconds"], p["cost_usd"]), (39.5, 0.14))


if __name__ == "__main__":
    unittest.main()


class StartingState(unittest.TestCase):
    def test_the_document_carries_what_was_set_up_and_what_was_asked_for(self):
        with tempfile.TemporaryDirectory() as d:
            repo, run = make_run(Path(d))
            data = json.loads((run / "run.json").read_text())
            cases = data["properties"][0]["cases"]
            cases[0]["result"]["starts_from"] = [{"tool": "make_listing", "input": {"price_cents": 120000}, "because": "No such listing.",
                                                  "returned": {"id": "l9"}, "junk": "dropped"}]
            cases[1]["result"]["data_request"] = {"state": "An old order.", "tool": {"name": "make_order", "does": "Creates one.", "takes": {}}}
            (run / "run.json").write_text(json.dumps(data))
            doc, _ = rundoc.build(run, repo=repo, project="shop")
            c1, c2 = doc["properties"][0]["cases"]
            self.assertEqual(c1["starts_from"], [{"tool": "make_listing", "input": {"price_cents": 120000}, "because": "No such listing.",
                                                  "returned": {"id": "l9"}}])
            self.assertIsNone(c1["data_request"])
            self.assertEqual(c2["data_request"]["tool"]["name"], "make_order")
            self.assertEqual(c2["starts_from"], [])


class ShortTitles(unittest.TestCase):
    def test_the_title_sent_with_the_run_is_kept_and_an_older_run_takes_the_files(self):
        with tempfile.TemporaryDirectory() as d:
            repo, run = make_run(Path(d))
            pid = json.loads((run / "run.json").read_text())["properties"][0]["id"]
            doc, _ = rundoc.build(run, repo=repo, project="shop")
            self.assertEqual(doc["properties"][0]["title"], "")                       # no title anywhere: the page shows the statement
            self.assertEqual(doc["properties"][0]["your_words"], "")
            (run / "sent.json").write_text(json.dumps({"properties": [{"id": pid, "title": "Subtotal follows the price",
                                                                       "your_words": "the subtotal should go up by what the item costs"}]}))
            doc, _ = rundoc.build(run, repo=repo, project="shop")
            self.assertEqual(doc["properties"][0]["title"], "Subtotal follows the price")
            self.assertEqual(doc["properties"][0]["your_words"], "the subtotal should go up by what the item costs")
            self.assertIn("checked_against", doc["properties"][0])

    def test_a_run_made_before_titles_existed_takes_the_feature_files_title(self):
        import yaml
        with tempfile.TemporaryDirectory() as d:
            repo, run = make_run(Path(d))
            feature = json.loads((run / "run.json").read_text())["feature"]
            f = repo / ".groundtruth" / "features" / f"{feature}.yml"
            spec = yaml.safe_load(f.read_text())
            for sp in spec["properties"]:
                sp["title"] = "Title for " + sp["id"]
            f.write_text(yaml.safe_dump(spec))
            doc, _ = rundoc.build(run, repo=repo, project="shop")
            self.assertEqual([p["title"] for p in doc["properties"]], ["Title for " + p["id"] for p in doc["properties"]])
