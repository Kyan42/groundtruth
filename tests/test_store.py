"""The results store: a real server on a free port, with its data in a temporary folder. No model."""
import http.client
import json
import os
import secrets
import stat
import tempfile
import unittest
from pathlib import Path

from store.db import token_hash
from store.server import start_in_background

CLIP = bytes(range(256)) * 40          # 10240 bytes
SHOT = b"\x89PNG\r\n\x1a\n" + b"x" * 92


def document(run="20261110-120000", goal="Carts work."):
    return {"schema": 1, "project": "shop", "feature": "cart", "goal": goal, "run": run, "ran_at": f"2026-11-10T{run[9:11]}:{run[11:13]}:{run[13:15]}",
            "totals": {"properties": {"holds": 1, "fails": 0}, "cases": {"holds": 2, "breaks": 0}}, "properties": []}


FILES = [{"path": "p1/c1/clip-1.webm", "kind": "clip", "size": len(CLIP)},
         {"path": "p1/c1/shot-1.png", "kind": "shot", "size": len(SHOT)}]


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"
        self.dash = Path(self.tmp.name) / "dashboard"
        self.dash.mkdir()
        self.start()

    def start(self):
        self.srv, self.url = start_in_background(self.data, 0, self.dash)
        self.port = self.srv.server_address[1]
        self.token = (self.data / "token").read_text().strip()

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()

    def tearDown(self):
        self.stop()
        self.tmp.cleanup()

    def req(self, method, path, body=None, token=None, headers=None):
        """One request, sent exactly as written (no path clean-up), answering (status, headers, bytes)."""
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = dict(headers or {})
        if token:
            h["Authorization"] = f"Bearer {token}"
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        out = (r.status, {k.lower(): v for k, v in r.getheaders()}, r.read())
        c.close()
        return out

    def js(self, *a, **k):
        status, headers, body = self.req(*a, **k)
        return status, json.loads(body) if body else None

    def upload(self, doc=None, token=None, files=FILES, send=True):
        token = token or self.token
        status, made = self.js("POST", "/v1/runs", {"document": doc or document(), "files": files}, token=token)
        self.assertEqual(status, 201, made)
        if send:
            for f in files:
                data = CLIP if f["path"].endswith(".webm") else SHOT
                s, _, b = self.req("PUT", f"/v1/runs/{made['id']}/files/{f['path']}", data, token=token)
                self.assertEqual(s, 204, b)
            s, done = self.js("POST", f"/v1/runs/{made['id']}/complete", {}, token=token)
            self.assertEqual((s, done), (200, {"missing": []}))
        return made


class FirstStart(StoreCase):
    def test_first_start_makes_the_user_and_an_owner_only_token_file_and_a_restart_keeps_them(self):
        f = self.data / "token"
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
        self.assertTrue(self.srv.store.made_token)
        with self.srv.store.conn() as c:
            users = c.execute("SELECT name, token_hash FROM users").fetchall()
        self.assertEqual([(u["name"], u["token_hash"]) for u in users], [("local", token_hash(self.token))])
        self.assertNotIn(self.token, (self.data / "store.db").read_bytes().decode("latin-1"))   # only the hash is kept
        first = self.token
        self.stop()
        self.start()
        self.assertFalse(self.srv.store.made_token)
        self.assertEqual(self.token, first)
        with self.srv.store.conn() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)

    def test_health(self):
        status, headers, body = self.req("GET", "/health")
        self.assertEqual((status, json.loads(body)), (200, {"ok": True, "store": 1}))
        self.assertEqual(headers["cache-control"], "no-store")


class Tokens(StoreCase):
    def test_uploads_need_the_right_token(self):
        body = {"document": document(), "files": []}
        self.assertEqual(self.js("POST", "/v1/runs", body)[0], 401)
        self.assertEqual(self.js("POST", "/v1/runs", body, token="wrong")[0], 401)
        self.assertEqual(self.js("POST", "/v1/runs", body, headers={"Authorization": "Basic abc"})[0], 401)
        status, made = self.js("POST", "/v1/runs", body, token=self.token)
        self.assertEqual((status, made["missing"]), (201, []))
        self.assertEqual(self.js("PUT", f"/v1/runs/{made['id']}/files/a.png", b"x")[0], 401)
        self.assertEqual(self.js("POST", f"/v1/runs/{made['id']}/complete", {})[0], 401)

    def test_a_wrong_token_is_refused_on_reads_too(self):
        self.assertEqual(self.js("GET", "/v1/runs", token="wrong")[0], 401)

    def test_reads_without_a_token_only_by_a_local_host_name(self):
        self.assertEqual(self.js("GET", "/v1/runs", headers={"Host": "localhost:1"})[0], 200)
        self.assertEqual(self.js("GET", "/v1/runs", headers={"Host": "evil.example:80"})[0], 401)


class FullUpload(StoreCase):
    def test_upload_then_every_read_route(self):
        made = self.upload()
        rid = made["id"]
        self.assertEqual(made["missing"], [f["path"] for f in FILES])

        status, headers, body = self.req("GET", "/v1/projects")
        self.assertEqual(headers["cache-control"], "no-store")
        projects = json.loads(body)["projects"]
        self.assertEqual([(p["name"], p["runs"]) for p in projects], [("shop", 1)])
        self.assertEqual(projects[0]["latest"]["id"], rid)

        status, runs = self.js("GET", "/v1/runs?project=shop&feature=cart&limit=5")
        self.assertEqual(status, 200)
        self.assertEqual(runs["runs"], [{"id": rid, "project": "shop", "feature": "cart", "run": "20261110-120000",
                                         "ran_at": "2026-11-10T12:00:00", "uploaded_at": runs["runs"][0]["uploaded_at"],
                                         "complete": True, "properties": {"holds": 1, "fails": 0}, "cases": {"holds": 2, "breaks": 0},
                                         "commit": None, "claims": []}])
        self.assertEqual(projects[0]["latest"], runs["runs"][0])
        self.assertEqual(self.js("GET", "/v1/runs?project=other")[1], {"runs": []})
        self.assertEqual(self.js("GET", "/v1/runs?limit=0")[0], 400)

        status, run = self.js("GET", f"/v1/runs/{rid}")
        self.assertEqual((run["id"], run["complete"], run["files_base"]), (rid, True, f"/v1/runs/{rid}/files/"))
        self.assertEqual(run["document"], document())

        status, headers, body = self.req("GET", f"/v1/runs/{rid}/files/p1/c1/clip-1.webm")
        self.assertEqual((status, headers["content-type"], headers["accept-ranges"], body), (200, "video/webm", "bytes", CLIP))
        status, headers, body = self.req("GET", f"/v1/runs/{rid}/files/p1/c1/shot-1.png")
        self.assertEqual((status, headers["content-type"], body), (200, "image/png", SHOT))
        self.assertEqual(self.js("GET", "/v1/runs/nosuchrun")[0], 404)
        self.assertEqual(self.js("GET", f"/v1/runs/{rid}/files/p1/c1/other.png")[0], 404)
        self.assertEqual(self.js("GET", "/v1/nothing")[0], 404)

    def test_complete_says_what_is_missing(self):
        made = self.upload(send=False)
        status, body = self.js("POST", f"/v1/runs/{made['id']}/complete", {}, token=self.token)
        self.assertEqual((status, body["missing"]), (409, sorted(f["path"] for f in FILES)))
        self.assertFalse(self.js("GET", f"/v1/runs/{made['id']}")[1]["complete"])
        self.assertEqual(self.js("GET", f"/v1/runs/{made['id']}/files/p1/c1/shot-1.png")[0], 404)    # not arrived yet

    def test_uploading_the_same_run_again_replaces_it(self):
        first = self.upload()
        second = self.upload(document(goal="Carts work, second try."))
        self.assertEqual(first["id"], second["id"])
        with self.srv.store.conn() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM files").fetchone()[0], 2)
        self.assertEqual(self.js("GET", f"/v1/runs/{first['id']}")[1]["document"]["goal"], "Carts work, second try.")
        other = self.upload(document(run="20261110-130000"), send=False)
        self.assertNotEqual(other["id"], first["id"])
        self.assertEqual([r["run"] for r in self.js("GET", "/v1/runs")[1]["runs"]], ["20261110-130000", "20261110-120000"])


def with_claims(run, verdicts, commit="20f4e86"):
    """A run document of shop/cart whose properties have these verdicts ({id: (verdict, number of cases)})."""
    doc = document(run)
    doc["ran_against"] = {"commit": commit}
    doc["properties"] = [{"id": pid, "title": f"  Claim {pid}  ", "statement": f"The {pid} claim.", "verdict": v,
                          "variations": [{"id": f"v{i}", "what": "x"} for i in range(9)],      # approved; not what was tried
                          "cases": [{"id": f"v{i}", "outcome": "holds"} for i in range(n)]}
                         for pid, (v, n) in verdicts.items()]
    return doc


class ClaimsInSummaries(StoreCase):
    """Each run summary lists its claims in brief, so the list page can fold a feature's runs with one request."""

    def test_each_summary_lists_its_claims_and_commit(self):
        first = with_claims("20261115-105926", {"c1": ("holds", 6), "c2": ("cant_tell", 5), "c3": ("cant_tell", 5)})
        recheck = with_claims("20261115-110849", {"c1": ("not_run", 0), "c2": ("holds", 5), "c3": ("cant_tell", 4)}, commit="abc1234")
        self.upload(first, files=[], send=False)
        self.upload(recheck, files=[], send=False)
        runs = self.js("GET", "/v1/runs?project=shop&feature=cart")[1]["runs"]
        self.assertEqual([r["run"] for r in runs], ["20261115-110849", "20261115-105926"])
        self.assertEqual(runs[0]["commit"], "abc1234")
        self.assertEqual(runs[0]["claims"], [{"id": "c1", "title": "Claim c1", "verdict": "not_run", "variations": 0},
                                             {"id": "c2", "title": "Claim c2", "verdict": "holds", "variations": 5},
                                             {"id": "c3", "title": "Claim c3", "verdict": "cant_tell", "variations": 4}])
        self.assertEqual([c["verdict"] for c in runs[1]["claims"]], ["holds", "cant_tell", "cant_tell"])
        self.assertEqual(self.js("GET", "/v1/projects")[1]["projects"][0]["latest"]["claims"], runs[0]["claims"])
        # Uploading the same run again replaces its claims too.
        self.upload(with_claims("20261115-110849", {"c1": ("fails", 2)}, commit=""), files=[], send=False)
        again = self.js("GET", "/v1/runs?limit=1")[1]["runs"][0]
        self.assertEqual((again["commit"], again["claims"]), (None, [{"id": "c1", "title": "Claim c1", "verdict": "fails", "variations": 2}]))

    def test_odd_documents_still_list(self):
        doc = document("20261115-120000")
        doc["properties"] = ["not a property", {"id": "p1"}, {"id": "p2", "title": 7, "verdict": None, "cases": "x"}]
        doc["ran_against"] = "nowhere"
        self.upload(doc, files=[], send=False)
        run = self.js("GET", "/v1/runs")[1]["runs"][0]
        self.assertEqual((run["commit"], run["claims"]), (None, [{"id": "p1", "title": "", "verdict": None, "variations": 0},
                                                                  {"id": "p2", "title": "", "verdict": None, "variations": 0}]))

    def test_a_store_made_before_claims_fills_them_from_its_documents(self):
        # A store from before this change: its runs table has no claims or commit columns.
        self.stop()
        import sqlite3
        db = self.data / "store.db"
        c = sqlite3.connect(db)
        c.executescript("ALTER TABLE runs DROP COLUMN claims; ALTER TABLE runs DROP COLUMN commit_sha;")
        old = with_claims("20261115-105926", {"c1": ("holds", 6), "c2": ("cant_tell", 5)})
        uid = c.execute("SELECT id FROM users").fetchone()[0]
        c.execute("INSERT INTO projects (user_id, name) VALUES (?, 'shop')", (uid,))
        pid = c.execute("SELECT id FROM projects").fetchone()[0]
        c.execute("INSERT INTO runs (id, user_id, project_id, feature, run, ran_at, uploaded_at, complete, properties, cases, document) "
                  "VALUES ('old1', ?, ?, 'cart', ?, ?, 'then', 1, '{}', '{}', ?)", (uid, pid, old["run"], old["ran_at"], json.dumps(old)))
        c.commit()
        self.assertNotIn("claims", [r[1] for r in c.execute("PRAGMA table_info(runs)")])
        c.close()
        self.start()
        run = self.js("GET", "/v1/runs")[1]["runs"][0]
        self.assertEqual(run["id"], "old1")
        self.assertEqual(run["commit"], "20f4e86")
        self.assertEqual(run["claims"], [{"id": "c1", "title": "Claim c1", "verdict": "holds", "variations": 6},
                                         {"id": "c2", "title": "Claim c2", "verdict": "cant_tell", "variations": 5}])
        # A second start finds nothing left to fill.
        self.stop()
        self.start()
        self.assertEqual(self.js("GET", "/v1/runs")[1]["runs"][0]["claims"], run["claims"])


class Ranges(StoreCase):
    def setUp(self):
        super().setUp()
        self.path = f"/v1/runs/{self.upload()['id']}/files/p1/c1/clip-1.webm"

    def get(self, rng):
        return self.req("GET", self.path, headers={"Range": rng})

    def test_first_bytes(self):
        status, h, body = self.get("bytes=0-99")
        self.assertEqual((status, h["content-range"], h["content-length"], body), (206, f"bytes 0-99/{len(CLIP)}", "100", CLIP[:100]))

    def test_a_middle_range(self):
        status, h, body = self.get("bytes=5000-5009")
        self.assertEqual((status, h["content-range"], body), (206, f"bytes 5000-5009/{len(CLIP)}", CLIP[5000:5010]))
        status, h, body = self.get("bytes=10000-")
        self.assertEqual((status, body), (206, CLIP[10000:]))
        status, h, body = self.get("bytes=10000-999999")          # an end past the file is cut to the file
        self.assertEqual((status, h["content-range"], body), (206, f"bytes 10000-10239/{len(CLIP)}", CLIP[10000:]))

    def test_a_suffix_range(self):
        status, h, body = self.get("bytes=-16")
        self.assertEqual((status, h["content-range"], body), (206, f"bytes {len(CLIP) - 16}-{len(CLIP) - 1}/{len(CLIP)}", CLIP[-16:]))

    def test_an_unsatisfiable_range(self):
        status, h, body = self.get(f"bytes={len(CLIP)}-")
        self.assertEqual((status, h["content-range"]), (416, f"bytes */{len(CLIP)}"))
        self.assertEqual(self.get("bytes=-0")[0], 416)

    def test_several_ranges_or_nonsense_get_the_whole_file(self):
        self.assertEqual(self.get("bytes=0-1,5-6")[:3:2], (200, CLIP))
        self.assertEqual(self.get("pages=1")[:3:2], (200, CLIP))


class Paths(StoreCase):
    def test_escapes_in_declared_paths_are_refused(self):
        for bad in ("../x.png", "a/../../x.png", "/etc/x.png", "a\\b.png", "a\x00.png", "a//b.png", "./a.png", ""):
            status, body = self.js("POST", "/v1/runs", {"document": document(), "files": [{"path": bad, "kind": "shot", "size": 1}]},
                                   token=self.token)
            self.assertEqual(status, 400, (bad, body))

    def test_escapes_and_undeclared_paths_in_the_url_are_refused(self):
        rid = self.upload()["id"]
        for bad in ("../../store.db", "%2e%2e/%2e%2e/store.db", "p1/c1/%00.png", "p1%5Cc1%5Cshot-1.png", "%2Fetc%2Fpasswd"):
            self.assertEqual(self.js("GET", f"/v1/runs/{rid}/files/{bad}")[0], 400, bad)
            self.assertEqual(self.js("PUT", f"/v1/runs/{rid}/files/{bad}", b"x", token=self.token)[0], 400, bad)
        self.assertEqual(self.js("PUT", f"/v1/runs/{rid}/files/p1/c1/shot-2.png", b"x", token=self.token)[0], 404)

    def test_files_over_their_declared_size_or_over_50_mb_are_refused(self):
        made = self.upload(send=False)
        status, body = self.js("PUT", f"/v1/runs/{made['id']}/files/p1/c1/shot-1.png", SHOT + b"more", token=self.token)
        self.assertEqual(status, 413, body)
        status, body = self.js("PUT", f"/v1/runs/{made['id']}/files/p1/c1/shot-1.png", SHOT[:10], token=self.token)
        self.assertEqual(status, 400, body)
        big = [{"path": "big.webm", "kind": "clip", "size": 50 * 1024 * 1024 + 1}]
        self.assertEqual(self.js("POST", "/v1/runs", {"document": document(), "files": big}, token=self.token)[0], 413)

    def test_a_document_over_5_mb_is_refused(self):
        doc = document(goal="x" * (5 * 1024 * 1024))
        self.assertEqual(self.js("POST", "/v1/runs", {"document": doc, "files": []}, token=self.token)[0], 413)


class TwoUsers(StoreCase):
    def test_a_second_user_cannot_read_or_write_the_first_users_run(self):
        rid = self.upload()["id"]
        other = secrets.token_urlsafe(16)
        with self.srv.store.conn() as c:
            c.execute("INSERT INTO users (name, token_hash, created) VALUES ('other', ?, 'now')", (token_hash(other),))
        self.assertEqual(self.js("GET", f"/v1/runs/{rid}", token=other)[0], 404)
        self.assertEqual(self.js("GET", f"/v1/runs/{rid}/files/p1/c1/shot-1.png", token=other)[0], 404)
        self.assertEqual(self.js("GET", "/v1/runs", token=other)[1], {"runs": []})
        self.assertEqual(self.js("GET", "/v1/projects", token=other)[1], {"projects": []})
        self.assertEqual(self.js("PUT", f"/v1/runs/{rid}/files/p1/c1/shot-1.png", SHOT, token=other)[0], 404)
        self.assertEqual(self.js("POST", f"/v1/runs/{rid}/complete", {}, token=other)[0], 404)
        # the same project, feature and run uploaded by the other user is a run of its own, not a replacement
        theirs = self.upload(token=other, send=False)
        self.assertNotEqual(theirs["id"], rid)
        self.assertEqual(self.js("GET", f"/v1/runs/{rid}")[1]["complete"], True)
        self.assertEqual(len(self.js("GET", "/v1/runs")[1]["runs"]), 1)


class Static(StoreCase):
    def test_before_the_dashboard_is_built(self):
        status, h, body = self.req("GET", "/")
        self.assertEqual((status, body.decode().strip()), (200, "The dashboard is not built yet."))

    def test_dashboard_files_and_no_escape(self):
        (self.dash / "index.html").write_text("<h1>runs</h1>")
        (self.dash / "run.html").write_text("<h1>one run</h1>")
        status, h, body = self.req("GET", "/")
        self.assertEqual((status, body, h["content-type"]), (200, b"<h1>runs</h1>", "text/html; charset=utf-8"))
        self.assertEqual(self.req("GET", "/run.html?run=abc")[2], b"<h1>one run</h1>")
        (Path(self.tmp.name) / "groundtruth.py").write_text("secret")
        for bad in ("/../groundtruth.py", "/%2e%2e/groundtruth.py", "/..%2fgroundtruth.py", "/nothing.html"):
            status, h, body = self.req("GET", bad)
            self.assertEqual(status, 404, bad)
            self.assertNotIn(b"secret", body)

    def test_the_real_dashboard_folder_is_the_default(self):
        from store.server import make_server
        srv = make_server(Path(self.tmp.name) / "d2", 0, quiet=True)
        try:
            self.assertEqual(srv.RequestHandlerClass.dashboard, (Path(__file__).resolve().parent.parent / "dashboard"))
        finally:
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
