"""The dashboard's data tool make_run and its made-up test store: a real store on a free port, with its data in a
temporary folder that GT_TEST_STORE_DIR points the scripts at. No model; the real test-store folder is never touched."""
import http.client
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from store.server import start_in_background
from toolserver import datatools

ROOT = Path(__file__).resolve().parent.parent
DASH = ROOT / "dashboard"
TOOLS = DASH / ".groundtruth" / "tools"
SAMPLE = DASH / "samples" / "dashboard-three-properties.json"
FILMS = json.loads((TOOLS / "films" / "films.json").read_text())
MARKER = "MADE-UP-DATA-ONLY"


def script(name, data_dir, stdin="", url=None):
    env = {k: v for k, v in os.environ.items() if k not in ("GT_TEST_STORE_DIR", "GROUNDTRUTH_APP_URL")}
    env["GT_TEST_STORE_DIR"] = str(data_dir)
    if url:
        env["GROUNDTRUTH_APP_URL"] = url
    return subprocess.run(["python3", str(TOOLS / name)], cwd=str(DASH), input=stdin, capture_output=True,
                          text=True, env=env, timeout=60)


class DataToolCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "test-store"
        r = script("init_store.py", self.data)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.srv, self.url = start_in_background(self.data, 0, DASH)
        self.port = self.srv.server_address[1]
        self.env = mock.patch.dict(os.environ, {"GT_TEST_STORE_DIR": str(self.data)})
        self.env.start()
        self.tool = datatools.load(DASH)[0]

    def tearDown(self):
        self.env.stop()
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def get(self, path):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", path)
        r = c.getresponse()
        out = (r.status, r.read())
        c.close()
        return out

    def run_doc(self, rid):
        status, body = self.get(f"/v1/runs/{rid}")
        self.assertEqual(status, 200, body)
        return json.loads(body)

    def make(self, **given):
        datatools.check_input(self.tool, given)
        return datatools.run_tool(self.tool, given, DASH, self.url)

    # ------------------------------------------------------------ the declaration
    def test_data_yml_loads(self):
        self.assertEqual(self.tool["name"], "make_run")
        self.assertEqual(self.tool["run"], "python3 .groundtruth/tools/make_run.py")
        self.assertEqual(set(self.tool["takes"]), {"new_tests", "rerun_tests", "variations_each", "verdicts", "films"})
        self.assertFalse(self.tool["takes"]["new_tests"]["optional"])
        self.assertTrue(self.tool["takes"]["films"]["optional"])

    # ------------------------------------------------------------ a normal call
    def test_normal_call_shape_and_document(self):
        out = self.make(new_tests=2, rerun_tests=1, verdicts="holds,fails,needs_decision")
        rid = out["run_id"]
        self.assertEqual(out["page"], f"/run.html?run={rid}")
        self.assertEqual(out["url"], f"{self.url}/run.html?run={rid}")
        self.assertEqual([(t["id"], t["tab"], t["sentence"], t["verdict"]) for t in out["tests"]],
                         [("t1", "New", "Made-up test 1", "holds"), ("t2", "New", "Made-up test 2", "fails"),
                          ("t3", "Regression", "Made-up test 3", "needs_decision")])
        self.assertEqual(out["tests"][1]["variations"],
                         [f"c1 holds film-b {FILMS['film-b']['seconds']:g}s", f"c2 breaks film-c {FILMS['film-c']['seconds']:g}s",
                          f"c3 holds film-d {FILMS['film-d']['seconds']:g}s"])
        run = self.run_doc(rid)
        self.assertTrue(run["complete"])
        doc = run["document"]
        self.assertEqual((doc["project"], doc["feature"]), ("made-up", "made-up"))
        props = doc["properties"]
        self.assertEqual([p["group"] for p in props], ["new", "new", "regression"])
        self.assertEqual([p["verdict"] for p in props], ["holds", "fails", "needs_decision"])
        self.assertEqual([len(p["cases"]) for p in props], [3, 3, 3])
        self.assertEqual([[c["outcome"] for c in p["cases"]] for p in props],
                         [["holds"] * 3, ["holds", "breaks", "holds"], ["holds", "declined", "holds"]])
        declined = props[2]["cases"][1]
        self.assertTrue(declined["question"] and declined["draft_if_intended"])
        self.assertEqual(doc["totals"]["properties"], {"holds": 1, "fails": 1, "needs_decision": 1, "cant_tell": 0, "planned": 0, "not_run": 0})
        self.assertEqual(doc["totals"]["cases"], {"holds": 7, "breaks": 1, "declined": 1, "outside": 0, "nothing_there": 0, "cant_run": 0, "planned": 0})
        self.assertEqual(props[1]["counts"]["breaks"], 1)
        # Every film the answer names is served at <test>/<case>/<film>.webm, with the pool film's size.
        for t in out["tests"]:
            for line in t["variations"]:
                cid, _, film, _ = line.split(" ")
                status, body = self.get(f"/v1/runs/{rid}/files/{t['id']}/{cid}/{film}.webm")
                self.assertEqual(status, 200)
                self.assertEqual(len(body), (TOOLS / "films" / f"{film}.webm").stat().st_size)
        # Each case's film and steps agree with the answer; steps sit inside the film.
        for p in props:
            for c in p["cases"]:
                film = c["clips"][0]["file"]
                self.assertTrue(film.startswith(f"{p['id']}/{c['id']}/film-"))
                length = FILMS[film.rsplit("/", 1)[1][:-5]]["seconds"]
                self.assertTrue(all(s["clip"] == 0 and 0 < s["at"] < length for s in c["steps"]))
        # The different variations of one test have different films.
        for p in props:
            names = [c["clips"][0]["file"].rsplit("/", 1)[1] for c in p["cases"]]
            self.assertEqual(len(set(names)), len(names))

    def test_keys_match_a_real_document(self):
        sample = json.loads(SAMPLE.read_text())
        out = self.make(new_tests=1, rerun_tests=1, verdicts="fails,not_run")
        doc = self.run_doc(out["run_id"])["document"]
        self.assertLessEqual(set(sample), set(doc))
        self.assertLessEqual(set(sample["ran_against"]), set(doc["ran_against"]))
        self.assertLessEqual(set(sample["totals"]), set(doc["totals"]))
        sp = next(p for p in sample["properties"] if p["cases"])
        self.assertLessEqual(set(sp), set(doc["properties"][0]))
        self.assertLessEqual(set(doc["properties"][0]), set(sp) | {"group"})
        self.assertLessEqual(set(sp["cases"][0]), set(doc["properties"][0]["cases"][0]))
        self.assertLessEqual(set(sp["cases"][0]["steps"][0]), set(doc["properties"][0]["cases"][0]["steps"][0]))
        notrun = doc["properties"][1]
        self.assertEqual((notrun["cases"], notrun["kind"], notrun["verdict"]), ([], None, "not_run"))

    def test_each_verdict_word(self):
        out = self.make(new_tests=5, rerun_tests=0, verdicts="holds,fails,needs_decision,cant_tell,not_run")
        doc = self.run_doc(out["run_id"])["document"]
        got = {p["verdict"]: [c["outcome"] for c in p["cases"]] for p in doc["properties"]}
        self.assertEqual(got, {"holds": ["holds"] * 3, "fails": ["holds", "breaks", "holds"],
                               "needs_decision": ["holds", "declined", "holds"],
                               "cant_tell": ["holds", "cant_run", "holds"], "not_run": []})
        self.assertEqual(out["tests"][4]["variations"], [])
        # With one variation, the special one is the only one.
        out = self.make(new_tests=0, rerun_tests=3, variations_each=1, verdicts="fails,needs_decision,cant_tell")
        doc = self.run_doc(out["run_id"])["document"]
        self.assertEqual([[c["outcome"] for c in p["cases"]] for p in doc["properties"]], [["breaks"], ["declined"], ["cant_run"]])
        self.assertEqual([p["group"] for p in doc["properties"]], ["regression"] * 3)
        self.assertEqual([t["tab"] for t in out["tests"]], ["Regression"] * 3)

    def test_no_films(self):
        out = self.make(new_tests=1, rerun_tests=0, films=False)
        self.assertEqual(out["tests"][0]["variations"], ["c1 holds no film", "c2 holds no film", "c3 holds no film"])
        doc = self.run_doc(out["run_id"])
        self.assertTrue(doc["complete"])
        for c in doc["document"]["properties"][0]["cases"]:
            self.assertEqual(c["clips"], [])
            self.assertTrue(all(s["clip"] is None and s["at"] is None for s in c["steps"]))

    def test_two_calls_make_two_runs(self):
        a = self.make(new_tests=1, rerun_tests=0)
        b = self.make(new_tests=1, rerun_tests=0)
        self.assertNotEqual(a["run_id"], b["run_id"])
        status, body = self.get("/v1/runs?project=made-up")
        self.assertEqual(len(json.loads(body)["runs"]), 2)

    def test_largest_shapes_fit(self):
        for new, rerun, each in ((1, 1, 10), (6, 0, 3), (0, 6, 8), (6, 6, 1), (5, 0, 10)):
            words = ",".join(["needs_decision"] * (new + rerun))
            r = script("make_run.py", self.data, json.dumps({"new_tests": new, "rerun_tests": rerun, "variations_each": each,
                                                              "verdicts": words}), self.url)
            self.assertEqual(r.returncode, 0, (new, rerun, each, r.stderr))
            self.assertLessEqual(len(r.stdout.strip()), 1900)
        # The biggest shape the inputs allow is still made, and described in the shorter form: no films, but every
        # test with its tab and verdict, how many variations it has and which one did not hold.
        r = script("make_run.py", self.data, json.dumps({"new_tests": 6, "rerun_tests": 6, "variations_each": 10,
                                                          "verdicts": "holds,fails," + ",".join(["needs_decision"] * 10)}), self.url)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLessEqual(len(r.stdout.strip()), 1900)
        big = json.loads(r.stdout)
        self.assertIn("fewer tests", big["note"])
        self.assertEqual([(t["id"], t["tab"], t["verdict"], t["variations"]) for t in big["tests"][:3]],
                         [("t1", "New", "holds", "10, all hold"), ("t2", "New", "fails", "10, c2 breaks, the rest hold"),
                          ("t3", "New", "needs_decision", "10, c2 declined, the rest hold")])
        self.assertEqual([t["tab"] for t in big["tests"]], ["New"] * 6 + ["Regression"] * 6)
        status, body = self.get("/v1/runs/" + big["run_id"])
        self.assertEqual(len(json.loads(body)["document"]["properties"]), 12)

    # ------------------------------------------------------------ refusals
    def test_bad_inputs(self):
        bad = [
            {"rerun_tests": 1},
            {"new_tests": 7, "rerun_tests": 0},
            {"new_tests": 0, "rerun_tests": 0},
            {"new_tests": 1, "rerun_tests": -1},
            {"new_tests": True, "rerun_tests": 1},
            {"new_tests": 1.5, "rerun_tests": 1},
            {"new_tests": 1, "rerun_tests": 1, "variations_each": 0},
            {"new_tests": 1, "rerun_tests": 1, "variations_each": 11},
            {"new_tests": 1, "rerun_tests": 1, "verdicts": "holds,broken"},
            {"new_tests": 1, "rerun_tests": 0, "verdicts": "holds,fails"},
            {"new_tests": 1, "rerun_tests": 0, "verdicts": 3},
            {"new_tests": 1, "rerun_tests": 0, "films": "yes"},
            {"new_tests": 1, "rerun_tests": 0, "colour": "red"},
        ]
        for given in bad:
            r = script("make_run.py", self.data, json.dumps(given), self.url)
            self.assertEqual(r.returncode, 1, given)
            self.assertEqual(r.stdout, "", given)
            self.assertTrue(r.stderr.strip().endswith("."), (given, r.stderr))
            self.assertLess(len(r.stderr), 300)
        for raw in ("not json", "[1, 2]", ""):
            r = script("make_run.py", self.data, raw, self.url)
            self.assertEqual(r.returncode, 1, raw)
        r = script("make_run.py", self.data, json.dumps({"new_tests": 1, "rerun_tests": 0}))
        self.assertEqual(r.returncode, 1)
        self.assertIn("GROUNDTRUTH_APP_URL", r.stderr)
        status, body = self.get("/v1/runs")
        self.assertEqual(json.loads(body)["runs"], [])
        with self.assertRaises(RuntimeError):
            self.make(new_tests=9, rerun_tests=0)

    def test_without_the_marker_both_refuse(self):
        (self.data / MARKER).unlink()
        r = script("make_run.py", self.data, json.dumps({"new_tests": 1, "rerun_tests": 0}), self.url)
        self.assertEqual(r.returncode, 1)
        self.assertIn(MARKER, r.stderr)
        r = script("reset_store.py", self.data)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(MARKER, r.stderr)
        other = Path(self.tmp.name) / "elsewhere"
        r = script("reset_store.py", other)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(other.exists())
        status, body = self.get("/v1/runs")
        self.assertEqual(json.loads(body)["runs"], [])
        # init_store never marks a folder that already holds a store without the marker.
        r = script("init_store.py", self.data)
        self.assertEqual(r.returncode, 1)
        self.assertFalse((self.data / MARKER).exists())

    def test_reset_empties_and_keeps_the_token(self):
        self.make(new_tests=1, rerun_tests=1)
        self.make(new_tests=2, rerun_tests=0)
        token = (self.data / "token").read_text()
        r = script("reset_store.py", self.data)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Removed 2 run(s), 12 file record(s) and 1 project(s)", r.stdout)
        self.assertEqual(json.loads(self.get("/v1/runs")[1])["runs"], [])
        self.assertEqual(json.loads(self.get("/v1/projects")[1])["projects"], [])
        self.assertEqual((self.data / "token").read_text(), token)
        out = self.make(new_tests=1, rerun_tests=0, verdicts="fails")
        self.assertTrue(self.run_doc(out["run_id"])["complete"])
        self.assertEqual(len(json.loads(self.get("/v1/runs")[1])["runs"]), 1)

    def test_the_default_folder_is_next_to_the_tools(self):
        sys.path.insert(0, str(TOOLS))
        try:
            import teststore
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("GT_TEST_STORE_DIR", None)
                self.assertEqual(teststore.folder(), DASH / ".groundtruth" / "test-store")
        finally:
            sys.path.remove(str(TOOLS))
            sys.modules.pop("teststore", None)


if __name__ == "__main__":
    unittest.main()
