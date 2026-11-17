"""Tests for data tools on the tool server: reading .groundtruth/data.yml, and the catalog's data_tools and set_up.

No model and no browser. Run from the repo root:  <python> -m unittest tests.test_datatools -v
"""
import json
import shlex
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from toolserver import datatools  # noqa: E402
from toolserver.catalog import Catalog  # noqa: E402
from toolserver.datatools import DataToolsError, load  # noqa: E402
from toolserver.runner import Runner, registration_problem  # noqa: E402

PY = shlex.quote(sys.executable)

# A tool that writes what it received (standard input, its arguments, its environment) to a file, then prints an id.
RECORD = (f"{PY} -c 'import json,os,sys; d=sys.stdin.read(); "
          "open(\"got.json\",\"w\").write(json.dumps({\"stdin\": d, \"argv\": sys.argv, \"env\": dict(os.environ)})); "
          "print(json.dumps({\"id\": 7, \"page\": \"/listings/7\"}))'")
CHATTY = "printf '{\"x\": \"'; head -c 3000 /dev/zero | tr '\\0' a; printf '\"}'"
LOUD = f"{PY} -c 'print(\"B\" * 1000 + \"END\"); raise SystemExit(2)'"


def yml(tools_text: str) -> str:
    return "tools:\n" + tools_text


GOOD = yml(f"""  - name: make_listing
    does: Creates one listing and returns its id.
    takes:
      title: {{type: string}}
      price_cents: {{type: integer, does: The price in cents.}}
      weight: {{type: number, optional: true}}
      sold_out: {{type: boolean, optional: true}}
    run: {json.dumps(RECORD)}
  - name: fails
    does: Always fails.
    run: "echo half-made; echo broken >&2; exit 4"
  - name: slow
    does: Takes too long.
    run: "sleep 30; echo '{{}}'"
  - name: chatty
    does: Prints too much.
    run: {json.dumps(CHATTY)}
  - name: not_json
    does: Prints plain text.
    run: "echo made it"
  - name: a_list
    does: Prints a list, not an object.
    run: "echo '[1, 2]'"
  - name: no_inputs
    does: Takes nothing.
    run: "echo '{{\\"ok\\": true}}'"
""")


class LoaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        (self.repo / ".groundtruth").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text):
        (self.repo / ".groundtruth" / "data.yml").write_text(text)

    def bad(self, text, *words):
        self.write(text)
        with self.assertRaises(DataToolsError) as cm:
            load(self.repo)
        for w in words:
            self.assertIn(w, str(cm.exception))
        return str(cm.exception)

    def test_no_file_means_no_tools(self):
        self.assertEqual(load(self.repo), [])
        self.assertEqual(load(self.repo / "missing"), [])

    def test_empty_file_means_no_tools(self):
        self.write("")
        self.assertEqual(load(self.repo), [])
        self.write("tools: []\n")
        self.assertEqual(load(self.repo), [])

    def test_good_file(self):
        self.write(GOOD)
        tools = load(self.repo)
        self.assertEqual(tools[0]["name"], "make_listing")
        self.assertEqual(tools[0]["takes"]["price_cents"], {"type": "integer", "optional": False, "does": "The price in cents."})
        self.assertEqual(tools[0]["takes"]["sold_out"], {"type": "boolean", "optional": True, "does": ""})
        self.assertEqual(tools[1]["takes"], {})
        self.assertIn("run", tools[0])

    def test_bad_names(self):
        for name in ("1abc", "has-dash", "has space", "a" * 41, "''", "yes", "12"):
            self.bad(yml(f"  - name: {name}\n    does: x.\n    run: echo\n"), "name")
        self.bad(yml("  - does: x.\n    run: echo\n"), "tool number 1", "name")
        self.write(yml("  - name: " + "a" * 40 + "\n    does: x.\n    run: echo\n"))
        self.assertEqual(len(load(self.repo)), 1)

    def test_missing_does(self):
        self.bad(yml("  - name: t\n    run: echo\n"), "'t'", "does")
        self.bad(yml("  - name: t\n    does: ''\n    run: echo\n"), "'t'", "does")
        self.bad(yml("  - name: t\n    does: [a]\n    run: echo\n"), "'t'", "does")

    def test_unknown_type(self):
        self.bad(yml("  - name: t\n    does: x.\n    takes:\n      a: {type: date}\n    run: echo\n"), "'t'", "'a'", "date")
        self.bad(yml("  - name: t\n    does: x.\n    takes:\n      a: {}\n    run: echo\n"), "'t'", "type")
        self.bad(yml("  - name: t\n    does: x.\n    takes:\n      a: string\n    run: echo\n"), "'t'", "'a'")

    def test_unknown_keys(self):
        self.bad("tools: []\nextra: 1\n", "extra")
        self.bad(yml("  - name: t\n    does: x.\n    run: echo\n    env: {A: 1}\n"), "'t'", "env")
        self.bad(yml("  - name: t\n    does: x.\n    takes:\n      a: {type: string, default: x}\n    run: echo\n"),
                 "'t'", "'a'", "default")
        self.bad(yml("  - name: t\n    does: x.\n    takes:\n      a: {type: string, optional: maybe}\n    run: echo\n"),
                 "'t'", "optional")
        self.bad(yml("  - name: t\n    does: x.\n    takes:\n      1bad: {type: string}\n    run: echo\n"), "'t'", "1bad")

    def test_duplicate_name(self):
        self.bad(yml("  - name: t\n    does: x.\n    run: echo\n  - name: t\n    does: y.\n    run: echo\n"), "'t'", "twice")

    def test_more_than_20_tools(self):
        self.write(yml("".join(f"  - name: t{i}\n    does: x.\n    run: echo\n" for i in range(20))))
        self.assertEqual(len(load(self.repo)), 20)
        self.bad(yml("".join(f"  - name: t{i}\n    does: x.\n    run: echo\n" for i in range(21))), "21", "20")

    def test_run_missing_or_not_text(self):
        self.bad(yml("  - name: t\n    does: x.\n"), "'t'", "run")
        self.bad(yml("  - name: t\n    does: x.\n    run: [echo, hi]\n"), "'t'", "run")
        self.bad(yml("  - name: t\n    does: x.\n    run: 5\n"), "'t'", "run")

    def test_not_yaml_or_wrong_shape(self):
        self.bad("tools:\n  - name: t\n    does: [unclosed\n", "not valid YAML")
        self.bad("- name: t\n", "tools")
        self.bad("tools: {name: t}\n", "list")
        self.bad("tools:\n  - just a string\n", "tool number 1")

    def test_registration_check_reports_a_broken_file(self):
        self.write(yml("  - name: t\n    run: echo\n"))
        with mock.patch("urllib.request.urlopen"):
            problem = registration_problem({"url": "http://127.0.0.1:9", "repo": str(self.repo)})
            self.assertIn("'t'", problem)
            self.assertIn("does", problem)
            self.write(GOOD)
            self.assertIsNone(registration_problem({"url": "http://127.0.0.1:9", "repo": str(self.repo)}))
            self.assertIsNone(registration_problem({"url": "http://127.0.0.1:9"}))


class CatalogDataToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name).resolve()
        self.repo = root / "repo"
        (self.repo / ".groundtruth").mkdir(parents=True)
        (self.repo / ".groundtruth" / "data.yml").write_text(GOOD)
        self.env = {"url": "http://127.0.0.1:9", "repo": str(self.repo), "reset": "true"}
        self.dir = root / "unit"
        self.cat = Catalog(self.env, self.dir)
        self.cat.run("reset_data", {})          # a case's setup is only allowed after that case's own reset

    def tearDown(self):
        self.cat.close()
        self.tmp.cleanup()

    def assertRefused(self, res, *words):
        self.assertTrue(res["isError"], res)
        self.assertTrue(res["text"].startswith("refused: "), res)
        for w in words:
            self.assertIn(w, res["text"])

    def ok_input(self, **extra):
        return {"title": "Lamp", "price_cents": 1200, **extra}

    def receipts(self):
        return [json.loads(x) for x in (self.dir / "receipts.jsonl").read_text().splitlines()]

    # -------------------------------------------------- data_tools
    def test_data_tools_lists_without_commands(self):
        res = self.cat.run("data_tools", {})
        self.assertFalse(res["isError"], res)
        self.assertEqual([t["name"] for t in res["data"]][:2], ["make_listing", "fails"])
        self.assertEqual(set(res["data"][0]), {"name", "does", "takes"})
        self.assertIn("make_listing: Creates one listing", res["text"])
        self.assertNotIn("sleep", json.dumps(res) + json.dumps(self.receipts()))
        self.assertNotIn("got.json", json.dumps(res))

    def test_data_tools_empty_or_no_repo(self):
        (self.repo / ".groundtruth" / "data.yml").unlink()
        res = self.cat.run("data_tools", {})
        self.assertEqual((res["isError"], res["data"]), (False, []))
        res = Catalog({"url": "http://127.0.0.1:9"}, self.dir).run("data_tools", {})
        self.assertEqual((res["isError"], res["data"]), (False, []))

    def test_data_tools_broken_file_is_an_error(self):
        (self.repo / ".groundtruth" / "data.yml").write_text("tools: 3\n")
        res = self.cat.run("data_tools", {})
        self.assertTrue(res["isError"])
        self.assertIn("list", res["text"])

    # -------------------------------------------------- set_up: success
    def test_set_up_runs_and_records(self):
        res = self.cat.run("set_up", {"tool": "make_listing", "input": self.ok_input(sold_out=False, weight=1)})
        self.assertFalse(res["isError"], res)
        self.assertEqual(res["data"], {"id": 7, "page": "/listings/7"})
        self.assertIn('"id": 7', res["text"])
        got = json.loads((self.repo / "got.json").read_text())
        self.assertEqual(json.loads(got["stdin"]), {"title": "Lamp", "price_cents": 1200, "sold_out": False, "weight": 1})
        self.assertEqual(got["env"]["GROUNDTRUTH_APP_URL"], "http://127.0.0.1:9")
        r = self.receipts()[-1]
        self.assertEqual((r["tool"], r["args"]["tool"], r["args"]["input"]["title"]), ("set_up", "make_listing", "Lamp"))
        self.assertEqual(r["returned"], {"id": 7, "page": "/listings/7"})

    def test_integer_is_accepted_for_a_number_and_float_is_too(self):
        for w in (3, 2.5):
            self.assertFalse(self.cat.run("set_up", {"tool": "make_listing", "input": self.ok_input(weight=w)})["isError"])

    def test_tool_without_inputs(self):
        self.assertEqual(self.cat.run("set_up", {"tool": "no_inputs"})["data"], {"ok": True})
        self.assertEqual(self.cat.run("set_up", {"tool": "no_inputs", "input": None})["data"], {"ok": True})
        self.assertEqual(self.cat.run("set_up", {"tool": "no_inputs", "input": {}})["data"], {"ok": True})

    def test_quotes_newlines_and_shell_arrive_intact_on_stdin_only(self):
        tricky = 'He said "hi"\nit\'s $(touch pwned) `touch pwned2`; touch pwned3 \\ end'
        res = self.cat.run("set_up", {"tool": "make_listing", "input": self.ok_input(title=tricky)})
        self.assertFalse(res["isError"], res)
        got = json.loads((self.repo / "got.json").read_text())
        self.assertEqual(json.loads(got["stdin"])["title"], tricky)
        self.assertNotIn("pwned", json.dumps(got["argv"]) + json.dumps(got["env"]))
        self.assertFalse(any((self.repo / n).exists() for n in ("pwned", "pwned2", "pwned3")))

    # -------------------------------------------------- set_up: refusals
    def test_no_repo(self):
        cat = Catalog({"url": "http://127.0.0.1:9", "reset": "true"}, self.dir)
        self.assertRefused(cat.run("set_up", {"tool": "make_listing", "input": self.ok_input()}), "no repo")

    def test_not_declared(self):
        self.assertRefused(self.cat.run("set_up", {"tool": "drop_tables", "input": {}}), "drop_tables", "declared")
        (self.repo / ".groundtruth" / "data.yml").unlink()
        self.assertRefused(self.cat.run("set_up", {"tool": "make_listing", "input": self.ok_input()}), "declared")

    def test_broken_file_refused(self):
        (self.repo / ".groundtruth" / "data.yml").write_text("tools: 3\n")
        self.assertRefused(self.cat.run("set_up", {"tool": "make_listing", "input": self.ok_input()}), "list")

    def test_bad_inputs(self):
        cases = [
            ({"title": "x"}, "price_cents"),                                      # missing
            (self.ok_input(colour="red"), "colour"),                              # unknown
            (self.ok_input(price_cents="12"), "integer"),                         # wrong type
            (self.ok_input(price_cents=True), "integer"),                         # a boolean is not an integer
            (self.ok_input(price_cents=1.5), "integer"),                          # a fraction is not an integer
            (self.ok_input(weight=True), "number"),
            (self.ok_input(weight="1"), "number"),
            (self.ok_input(weight=float("nan")), "number"),
            (self.ok_input(sold_out=1), "boolean"),
            (self.ok_input(title=None), "string"),
            (self.ok_input(title="x" * 501), "501"),                              # over 500 characters
        ]
        for given, word in cases:
            with self.subTest(given=str(given)[:60]):
                self.assertRefused(self.cat.run("set_up", {"tool": "make_listing", "input": given}), word)
        self.assertRefused(self.cat.run("set_up", {"tool": "make_listing", "input": ["Lamp", 1200]}), "input")
        self.assertRefused(self.cat.run("set_up", {"tool": "make_listing", "input": {}, "run": "rm -rf /"}), "bad arguments")
        self.assertRefused(self.cat.run("set_up", {"tool": 5, "input": {}}), "tool")
        # exactly 500 is fine, and none of the refusals above counts as a call
        self.assertFalse(self.cat.run("set_up", {"tool": "make_listing", "input": self.ok_input(title="x" * 500)})["isError"])
        self.assertEqual(self.cat.set_ups, 1)
        self.assertTrue(all(r.get("refused") for r in self.receipts()[1:-1]))      # the first receipt is the reset

    def test_no_reset_registered(self):
        cat = Catalog({"url": "http://127.0.0.1:9", "repo": str(self.repo)}, self.dir)
        self.assertRefused(cat.run("set_up", {"tool": "make_listing", "input": self.ok_input()}), "no reset command")

    def test_discovery_unit(self):
        cat = Catalog(self.env, self.dir, discovery=True)
        self.assertRefused(cat.run("set_up", {"tool": "make_listing", "input": self.ok_input()}), "discovery")
        self.assertFalse(cat.run("data_tools", {})["isError"])

    def test_after_another_call(self):
        self.assertFalse(self.cat.run("reset_data", {})["isError"])
        self.assertFalse(self.cat.run("set_up", {"tool": "no_inputs"})["isError"])
        self.assertFalse(self.cat.run("list_files", {})["isError"])
        self.assertRefused(self.cat.run("set_up", {"tool": "no_inputs"}), "list_files")

    def test_refused_calls_do_not_count_but_failed_ones_do(self):
        self.assertRefused(self.cat.run("shell", {"cmd": "ls"}))                                  # unknown tool
        self.assertRefused(self.cat.run("read_lines", {"path": "../x", "start": 1, "end": 2}))    # refused by the tool
        self.assertFalse(self.cat.run("set_up", {"tool": "no_inputs"})["isError"])
        self.assertTrue(self.cat.run("read_lines", {"path": "nope.py", "start": 1, "end": 2})["text"].startswith("error"))
        self.assertRefused(self.cat.run("set_up", {"tool": "no_inputs"}), "read_lines")

    def test_a_tool_that_started_and_then_failed_counts_even_if_reported_as_bad_arguments(self):
        def breaks():
            raise TypeError("went wrong halfway")
        self.cat._tools["list_files"] = breaks
        self.assertRefused(self.cat.run("list_files", {}), "bad arguments")
        self.assertRefused(self.cat.run("set_up", {"tool": "no_inputs"}), "list_files")

    def test_data_tools_counts_as_another_call(self):
        self.cat.run("data_tools", {})
        self.assertRefused(self.cat.run("set_up", {"tool": "no_inputs"}), "data_tools")

    def test_at_most_six(self):
        for _ in range(5):
            self.assertFalse(self.cat.run("set_up", {"tool": "no_inputs"})["isError"])
        self.assertTrue(self.cat.run("set_up", {"tool": "fails"})["isError"])     # a failed call still counts
        self.assertRefused(self.cat.run("set_up", {"tool": "no_inputs"}), "6")

    # -------------------------------------------------- set_up: the command misbehaves
    def assertError(self, res, *words):
        self.assertTrue(res["isError"], res)
        self.assertTrue(res["text"].startswith("error: "), res)
        for w in words:
            self.assertIn(w, res["text"])

    def test_non_zero_exit(self):
        self.assertError(self.cat.run("set_up", {"tool": "fails"}), "exit 4", "half-made", "broken")

    def test_timeout_stops_every_process(self):
        with mock.patch.object(datatools, "TIME_LIMIT", 1):
            t0 = time.time()
            res = self.cat.run("set_up", {"tool": "slow"})
        self.assertLess(time.time() - t0, 10)
        self.assertError(res, "1 seconds")

    def test_output_over_the_limit(self):
        self.assertError(self.cat.run("set_up", {"tool": "chatty"}), "2000")

    def test_output_not_a_json_object(self):
        self.assertError(self.cat.run("set_up", {"tool": "not_json"}), "JSON object", "made it")
        self.assertError(self.cat.run("set_up", {"tool": "a_list"}), "JSON object")

    def test_error_carries_only_the_last_300_characters(self):
        (self.repo / ".groundtruth" / "data.yml").write_text(yml(
            f"  - name: loud\n    does: x.\n    run: {json.dumps(LOUD)}\n"))
        res = self.cat.run("set_up", {"tool": "loud"})
        self.assertError(res, "END")
        self.assertLess(res["text"].count("B"), 310)


class RetryTests(unittest.TestCase):
    def test_second_attempt_of_a_case_may_set_up_again(self):
        with tempfile.TemporaryDirectory() as d:
            repo, run_dir = Path(d) / "repo", Path(d) / "run"
            (repo / ".groundtruth").mkdir(parents=True)
            run_dir.mkdir()
            (repo / ".groundtruth" / "data.yml").write_text(GOOD)
            env = {"url": "http://127.0.0.1:9", "repo": str(repo), "reset": "true"}
            r = Runner(env, [{"id": "p1"}], run_dir, say=lambda s: None)
            try:
                set_up = {"tool": "set_up", "args": {"tool": "no_inputs"}}
                self.assertFalse(r.execute({"tool": "begin_claim", "args": {"claim": "p1/c1", "reset": True}})["isError"])
                self.assertFalse(r.execute(set_up)["isError"])
                self.assertFalse(r.execute({"tool": "list_files", "args": {}})["isError"])
                self.assertTrue(r.execute(set_up)["text"].startswith("refused"))
                # the same unfinished case, opened again: a fresh start
                self.assertFalse(r.execute({"tool": "begin_claim", "args": {"claim": "p1/c1", "reset": True}})["isError"])
                self.assertFalse(r.execute(set_up)["isError"])
                # a discovery unit opened through the runner refuses set_up
                self.assertFalse(r.execute({"tool": "begin_claim", "args": {"claim": "p1/discovery", "reset": True}})["isError"])
                res = r.execute(set_up)
                self.assertTrue(res["text"].startswith("refused") and "discovery" in res["text"], res)
            finally:
                r.close()


class DescriptionsStayShort(unittest.TestCase):
    def test_a_does_over_the_limit_is_an_error_in_plain_words(self):
        import tempfile
        from toolserver import datatools
        for where, text in (("tool", "tools:\n  - name: t\n    does: " + "x" * 401 + "\n    run: 'true'\n"),
                            ("input", "tools:\n  - name: t\n    does: Makes a thing.\n    takes:\n      n:\n        type: integer\n        does: "
                             + "x" * 401 + "\n    run: 'true'\n")):
            with self.subTest(where=where), tempfile.TemporaryDirectory() as d:
                (Path(d) / ".groundtruth").mkdir()
                (Path(d) / ".groundtruth" / "data.yml").write_text(text)
                with self.assertRaises(datatools.DataToolsError) as e:
                    datatools.load(Path(d))
                self.assertIn("401 characters. At most 400 are allowed", str(e.exception))


class SetUpNeedsItsOwnReset(unittest.TestCase):
    """Setting up data is only allowed in a case that has just run its own reset, so a case reopened without one
    cannot add data on top of whatever is there."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name).resolve()
        self.repo = root / "repo"
        (self.repo / ".groundtruth").mkdir(parents=True)
        (self.repo / ".groundtruth" / "data.yml").write_text(GOOD)
        self.env = {"url": "http://127.0.0.1:9", "repo": str(self.repo), "reset": "true"}
        self.run_dir = root / "run"
        self.run_dir.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_refused_before_the_reset_and_allowed_after_it(self):
        cat = Catalog(self.env, self.run_dir / "unit")
        try:
            first = cat.run("set_up", {"tool": "no_inputs"})
            self.assertTrue(first["isError"])
            self.assertIn("without running the reset", first["text"])
            cat.run("reset_data", {})
            self.assertFalse(cat.run("set_up", {"tool": "no_inputs"})["isError"])
        finally:
            cat.close()

    def test_a_case_reopened_without_a_reset_cannot_set_up(self):
        from toolserver.runner import Runner
        r = Runner(self.env, [{"id": "p1"}], self.run_dir, say=lambda s: None)
        try:
            self.assertFalse(r.execute({"tool": "begin_claim", "args": {"claim": "p1/c1", "reset": True}})["isError"])
            self.assertFalse(r.execute({"tool": "set_up", "args": {"tool": "no_inputs"}})["isError"])
            self.assertFalse(r.execute({"tool": "begin_claim", "args": {"claim": "p1/c1", "reset": False}})["isError"])
            again = r.execute({"tool": "set_up", "args": {"tool": "no_inputs"}})
            self.assertTrue(again["isError"])
            self.assertIn("without running the reset", again["text"])
        finally:
            r.close()


if __name__ == "__main__":
    unittest.main()
