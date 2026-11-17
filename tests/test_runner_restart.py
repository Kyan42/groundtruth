"""A case that is tried a second time starts the way the first try did: the reset runs again and the receipts are kept."""
import json
import tempfile
import unittest
from pathlib import Path

from toolserver.runner import Runner


class Restart(unittest.TestCase):
    def test_opening_the_same_unfinished_case_again_resets_again_and_keeps_its_receipts(self):
        with tempfile.TemporaryDirectory() as d:
            repo, run_dir = Path(d) / "repo", Path(d) / "run"
            repo.mkdir(); run_dir.mkdir()
            env = {"url": "http://127.0.0.1:9", "repo": str(repo), "reset": "echo again >> resets.txt"}
            r = Runner(env, [{"id": "p1"}], run_dir, say=lambda s: None)
            try:
                first = r.execute({"tool": "begin_claim", "args": {"claim": "p1/c1", "reset": True}})
                again = r.execute({"tool": "begin_claim", "args": {"claim": "p1/c1", "reset": True}})
                self.assertEqual((first["isError"], again["isError"]), (False, False))
                self.assertEqual((repo / "resets.txt").read_text().split(), ["again", "again"])
                receipts = [json.loads(x) for x in (run_dir / "p1" / "c1" / "receipts.jsonl").read_text().splitlines()]
                self.assertEqual([x["tool"] for x in receipts], ["reset_data", "reset_data"])
                done = r.execute({"tool": "end_claim", "args": {"claim": "p1/c1", "report": {"role": "case", "outcome": "cant_run", "observed": "x"}}})
                self.assertFalse(done["isError"])
                # once a case is finished it cannot be opened again
                self.assertTrue(r.execute({"tool": "begin_claim", "args": {"claim": "p1/c1", "reset": True}})["isError"])
            finally:
                r.close()

    def test_a_reset_that_fails_on_the_second_try_blocks_the_case(self):
        with tempfile.TemporaryDirectory() as d:
            repo, run_dir = Path(d) / "repo", Path(d) / "run"
            repo.mkdir(); run_dir.mkdir()
            env = {"url": "http://127.0.0.1:9", "repo": str(repo), "reset": "test ! -e used && touch used"}
            r = Runner(env, [{"id": "p1"}], run_dir, say=lambda s: None)
            try:
                self.assertFalse(r.execute({"tool": "begin_claim", "args": {"claim": "p1/c1", "reset": True}})["isError"])
                again = r.execute({"tool": "begin_claim", "args": {"claim": "p1/c1", "reset": True}})
                self.assertTrue(again["isError"])
                self.assertIn("reset command failed", again["text"])
                self.assertTrue(r.execute({"tool": "observe", "args": {}})["isError"])
            finally:
                r.close()


if __name__ == "__main__":
    unittest.main()
