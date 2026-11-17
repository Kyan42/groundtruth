"""Uploading a finished run: push_run against a real store on a free port, and verify --push's upload step."""
import io
import json
import os
import socket
import sys
import tempfile
import unittest
import urllib.request
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import groundtruth  # noqa: E402
from store.server import start_in_background  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rundoc import make_run  # noqa: E402
from toolserver.push import PushError, push_run  # noqa: E402


_NO_LOGIN = None


def setUpModule():
    """These tests never read what `login` saved on this machine: with a saved login the store address and token
    would be the real ones."""
    global _NO_LOGIN
    _NO_LOGIN = mock.patch.dict(os.environ, {"GT_CONFIG": str(Path(tempfile.gettempdir()) / "gt-test-no-saved-login.json")})
    _NO_LOGIN.start()


def tearDownModule():
    _NO_LOGIN.stop()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Push(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.repo, self.run = make_run(root / "work")
        self.srv, self.url = start_in_background(root / "data", 0, root / "dashboard")
        self.token = (root / "data" / "token").read_text().strip()
        self.said = []

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def get(self, path):
        with urllib.request.urlopen(self.url + path) as r:
            return r.read()

    def test_push_run_end_to_end(self):
        before = sorted(p.relative_to(self.run) for p in self.run.rglob("*"))
        got = push_run(self.run, self.repo, self.url, self.token, project="shop", say=self.said.append)
        self.assertEqual(got["url"], f"{self.url}/run.html?run={got['id']}")
        run = json.loads(self.get(f"/v1/runs/{got['id']}"))
        self.assertTrue(run["complete"])
        self.assertEqual((run["document"]["project"], run["document"]["feature"]), ("shop", "cart"))
        self.assertNotIn(self.tmp.name, json.dumps(run))
        self.assertEqual(self.get(run["files_base"] + "p1/c1/clip-1.webm"), b"webm")
        self.assertEqual(self.get(run["files_base"] + "p1/c1/shot-1.png"), b"png")
        self.assertEqual(sorted(p.relative_to(self.run) for p in self.run.rglob("*")), before)    # nothing written in the run
        self.assertIn("2 file(s)", self.said[0])
        again = push_run(self.run, self.repo, self.url, self.token, project="shop", say=self.said.append)
        self.assertEqual(again["id"], got["id"])

    def test_a_store_that_is_down_gives_one_clear_error(self):
        with self.assertRaises(PushError) as e:
            push_run(self.run, self.repo, f"http://127.0.0.1:{free_port()}", self.token, say=self.said.append)
        self.assertIn("couldn't reach the results store", str(e.exception))

    def test_a_wrong_token_gives_one_clear_error(self):
        with self.assertRaises(PushError) as e:
            push_run(self.run, self.repo, self.url, "wrong", say=self.said.append)
        self.assertIn("401", str(e.exception))
        self.assertIn("token", str(e.exception))

    def test_an_unfinished_run_is_not_sent(self):
        (self.run / "run.json").unlink()
        with self.assertRaises(PushError) as e:
            push_run(self.run, self.repo, self.url, self.token, say=self.said.append)
        self.assertIn("did not finish", str(e.exception))


class VerifyPushStep(unittest.TestCase):
    """verify --push: a failed upload is one line and changes neither the verdicts nor the exit code."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo, self.run = make_run(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_failed_upload_prints_one_line_and_returns(self):
        said = []
        with mock.patch.dict(os.environ, {"GT_STORE_TOKEN": "t"}):
            url = groundtruth.upload_step(self.run, self.repo, f"http://127.0.0.1:{free_port()}", say=said.append)
        self.assertIsNone(url)
        failed = [s for s in said if s.startswith("Upload: failed")]
        self.assertEqual(len(failed), 1, said)

    def test_no_token_is_said_plainly(self):
        said = []
        with mock.patch.dict(os.environ, {"GT_STORE_TOKEN": ""}), mock.patch.object(groundtruth, "HERE", Path(self.tmp.name)):
            self.assertIsNone(groundtruth.upload_step(self.run, self.repo, "http://127.0.0.1:1", say=said.append))
        self.assertIn("no token for the results store", said[-1])

    def test_verify_keeps_its_exit_code_when_the_upload_fails(self):
        """cmd_verify with the run itself faked: --push to a store that is down, --exit-code still 0 for all holding."""
        run_json = json.loads((self.run / "run.json").read_text())
        run_json["properties"] = [p for p in run_json["properties"] if p["verdict"] == "holds"]
        (self.run / "run.json").write_text(json.dumps(run_json))
        (self.repo / ".groundtruth" / "env.json").write_text("{}")
        args = mock.Mock(repo=str(self.repo), feature="cart", service="x", only=None, plan_only=False, exit_code=True,
                         push=True, store=f"http://127.0.0.1:{free_port()}")
        out = io.StringIO()
        with mock.patch("toolserver.runner.run_verify", return_value=self.run), \
                mock.patch("report.write_report", return_value=self.run / "report.html"), \
                mock.patch.dict(os.environ, {"GT_STORE_TOKEN": "t"}), redirect_stdout(out), \
                self.assertRaises(SystemExit) as e:
            groundtruth.cmd_verify(args)
        self.assertEqual(e.exception.code, 0)
        self.assertIn("Result: 1 hold, 0 fail", out.getvalue())
        self.assertEqual(sum(line.startswith("Upload: failed") for line in out.getvalue().splitlines()), 1)

    def test_store_address_and_token_order(self):
        with mock.patch.dict(os.environ, {"GT_STORE": "http://s:1/", "GT_STORE_TOKEN": "env-token"}):
            self.assertEqual(groundtruth.store_address("http://given:2"), "http://given:2")
            self.assertEqual(groundtruth.store_address(), "http://s:1")
            self.assertEqual(groundtruth.store_token(), "env-token")
        with mock.patch.dict(os.environ, {"GT_STORE": "", "GT_STORE_TOKEN": ""}), mock.patch.object(groundtruth, "HERE", Path(self.tmp.name)):
            self.assertEqual(groundtruth.store_address(), "http://127.0.0.1:8900")
            (Path(self.tmp.name) / ".store").mkdir()
            (Path(self.tmp.name) / ".store" / "token").write_text("file-token\n")
            self.assertEqual(groundtruth.store_token(), "file-token")


class ProjectName(unittest.TestCase):
    """One app should always land under one name in the store."""

    def test_the_name_given_at_register_is_used_unless_another_is_given(self):
        import json as _json
        import tempfile as _tempfile
        from pathlib import Path as _Path

        import groundtruth
        with _tempfile.TemporaryDirectory() as d:
            repo = _Path(d) / "app"
            (repo / ".groundtruth").mkdir(parents=True)
            self.assertEqual(groundtruth.project_name(repo), "app")                 # nothing registered: the folder's name
            (repo / ".groundtruth" / "env.json").write_text(_json.dumps({"url": "http://127.0.0.1:1", "project": "shop"}))
            self.assertEqual(groundtruth.project_name(repo), "shop")
            self.assertEqual(groundtruth.project_name(repo, "other"), "other")


if __name__ == "__main__":
    unittest.main()
