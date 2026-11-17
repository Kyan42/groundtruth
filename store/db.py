"""The store's data: one SQLite file and a folder of files named by their sha256, under one data folder.

    <data>/store.db     users, projects, runs, files (see CONTRACT.md)
    <data>/files/<sha>  the bytes of each film and screenshot
    <data>/token        the local user's token (owner-only), written on first start (not in hosted mode)

Every query that reads or changes runs takes a user id, so one user never sees another's runs. The same file also
holds who may sign in (users, sessions) and what each user's verifier sessions cost (spend), which the verifier
service reads and writes in hosted mode (service/server.py).
"""
import hashlib
import json
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

LOCAL_USER = "local"
USER_NAME = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}"
SESSION_DAYS = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    token_hash TEXT NOT NULL UNIQUE,
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    UNIQUE (user_id, name)
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    project_id INTEGER NOT NULL REFERENCES projects(id),
    feature TEXT NOT NULL,
    run TEXT NOT NULL,
    ran_at TEXT,
    uploaded_at TEXT NOT NULL,
    complete INTEGER NOT NULL DEFAULT 0,
    properties TEXT NOT NULL,   -- count per verdict, as JSON
    cases TEXT NOT NULL,        -- count per case outcome, as JSON
    document TEXT NOT NULL,     -- the whole run document, as JSON
    claims TEXT,                -- each property's id, title, verdict and number of cases, as JSON (see claims_of)
    commit_sha TEXT,            -- ran_against.commit, or NULL
    UNIQUE (user_id, project_id, feature, run)
);
CREATE TABLE IF NOT EXISTS files (
    run_id TEXT NOT NULL REFERENCES runs(id),
    path TEXT NOT NULL,
    kind TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size INTEGER NOT NULL,
    sha256 TEXT,                -- empty until the bytes have arrived
    PRIMARY KEY (run_id, path)
);
CREATE TABLE IF NOT EXISTS feedback (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    run_id TEXT NOT NULL REFERENCES runs(id),
    property TEXT NOT NULL,
    case_id TEXT,
    kind TEXT NOT NULL,         -- not_what_i_meant | wrong_result | right
    note TEXT NOT NULL,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS spend (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,   -- no foreign key: rows are deleted with the user (remove_user)
    job TEXT NOT NULL,          -- the verifier's run or draft id
    kind TEXT NOT NULL,         -- run | draft
    cost_usd REAL NOT NULL,     -- one model session's cost, as `claude -p` reported it
    at TEXT NOT NULL            -- UTC, YYYY-MM-DDTHH:MM:SSZ
);
CREATE INDEX IF NOT EXISTS spend_user_at ON spend (user_id, at);
CREATE TABLE IF NOT EXISTS sessions (
    id_hash TEXT PRIMARY KEY,   -- sha256 of the cookie's random id; the id itself is never stored
    user_id INTEGER NOT NULL REFERENCES users(id),
    created TEXT NOT NULL,
    expires REAL NOT NULL       -- unix seconds
);
"""
FEEDBACK_KINDS = ("not_what_i_meant", "wrong_result", "right")


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def month_start():
    """The start of this calendar month, in UTC, in the form spend.at uses."""
    return time.strftime("%Y-%m-01T00:00:00Z", time.gmtime())


def dollars(x) -> str:
    """50 -> "$50", 12.5 -> "$12.50"."""
    x = float(x)
    return f"${x:,.0f}" if x == int(x) else f"${x:,.2f}"


def limit_message(limit) -> str:
    return f"You have reached this month's limit of {dollars(limit)} on this verifier. Ask whoever runs it to raise it."


def new_token_text() -> str:
    """A random token that never starts with "-", so `login --token <it>` always parses."""
    while True:
        token = secrets.token_urlsafe(32)
        if not token.startswith("-"):
            return token


class UserError(Exception):
    """A plain sentence for the person managing users."""


def claims_of(document: dict) -> list:
    """Each property of a run document, small enough to list many runs at once: its id, title, verdict, and how many
    variations (cases) it tried in that run. The list page folds these into each feature's current state."""
    out = []
    for p in (document.get("properties") if isinstance(document, dict) else None) or []:
        if not isinstance(p, dict):
            continue
        title, cases = p.get("title"), p.get("cases")
        out.append({"id": str(p.get("id") or ""), "title": title.strip()[:200] if isinstance(title, str) else "",
                    "verdict": p.get("verdict") if isinstance(p.get("verdict"), str) else None,
                    "variations": len(cases) if isinstance(cases, list) else 0})
    return out


def commit_of(document: dict):
    ran = document.get("ran_against") if isinstance(document, dict) else None
    commit = ran.get("commit") if isinstance(ran, dict) else None
    return commit if isinstance(commit, str) and commit else None


class Store:
    def __init__(self, data_dir, hosted=False):
        """hosted: no local user and no token file are made; users are added with `groundtruth.py users add`."""
        self.data = Path(data_dir).resolve()
        self.files = self.data / "files"
        self.db_path = self.data / "store.db"
        self.token_path = self.data / "token"
        self.data.mkdir(parents=True, exist_ok=True)
        os.chmod(self.data, 0o700)
        self.files.mkdir(exist_ok=True)
        with self.conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)
        self._add_columns()
        self._add_claims()
        self.made_token = False if hosted else self._local_user()

    def _add_columns(self):
        """Columns added after a store was first made. Both processes may open the same file at once when hosted, so
        the check and the change happen inside one write transaction."""
        c = self.connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            have = {r["name"] for r in c.execute("PRAGMA table_info(runs)")}
            for col in ("claims", "commit_sha"):
                if col not in have:
                    c.execute(f"ALTER TABLE runs ADD COLUMN {col} TEXT")
            if "monthly_limit" not in {r["name"] for r in c.execute("PRAGMA table_info(users)")}:
                # US dollars per calendar month (UTC); NULL means no limit
                c.execute("ALTER TABLE users ADD COLUMN monthly_limit REAL")
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
        finally:
            c.close()

    def _add_claims(self):
        """Stores made before run summaries carried each claim: fill the columns from each stored document once, so
        earlier uploads list like new ones without being uploaded again."""
        with self.conn() as c:
            todo = [r["id"] for r in c.execute("SELECT id FROM runs WHERE claims IS NULL")]
            for rid in todo:
                row = c.execute("SELECT document FROM runs WHERE id=?", (rid,)).fetchone()
                try:
                    doc = json.loads(row["document"])
                except ValueError:
                    doc = {}
                c.execute("UPDATE runs SET claims=?, commit_sha=? WHERE id=?", (json.dumps(claims_of(doc)), commit_of(doc), rid))

    def connect(self) -> sqlite3.Connection:
        """A fresh connection: each request uses its own, so threads never share one."""
        c = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        return c

    @contextmanager
    def conn(self):
        c = self.connect()
        try:
            yield c
        finally:
            c.close()

    def _local_user(self) -> bool:
        """Make the local user and its token on first start. If the token file was deleted, make a new token
        (the old one stops working). Returns True when a new token was written."""
        with self.conn() as c:
            row = c.execute("SELECT id FROM users WHERE name=?", (LOCAL_USER,)).fetchone()
            if row and self.token_path.is_file():
                return False
            token = new_token_text()
            if row:
                c.execute("UPDATE users SET token_hash=? WHERE id=?", (token_hash(token), row["id"]))
            else:
                c.execute("INSERT INTO users (name, token_hash, created) VALUES (?, ?, ?)", (LOCAL_USER, token_hash(token), now()))
        fd = os.open(self.token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(token + "\n")
        return True

    # ------------------------------------------------------------------ users
    def user_by_token(self, token: str):
        with self.conn() as c:
            row = c.execute("SELECT id FROM users WHERE token_hash=?", (token_hash(token),)).fetchone()
        return row["id"] if row else None

    def local_user(self):
        with self.conn() as c:
            return c.execute("SELECT id FROM users WHERE name=?", (LOCAL_USER,)).fetchone()["id"]

    def user_name(self, user_id):
        with self.conn() as c:
            row = c.execute("SELECT name FROM users WHERE id=?", (user_id,)).fetchone()
        return row["name"] if row else None

    # ------------------------------------------------------------------ managing users (groundtruth.py users ...)
    def _uid(self, c, name):
        row = c.execute("SELECT id FROM users WHERE name=?", (name,)).fetchone()
        if not row:
            raise UserError(f"There is no user called {name}.")
        return row["id"]

    @staticmethod
    def _check_limit(limit):
        if limit is not None and (not isinstance(limit, (int, float)) or isinstance(limit, bool) or not 0 <= limit < 1e9):
            raise UserError("A limit is a number of US dollars, 0 or more.")
        return None if limit is None else float(limit)

    def add_user(self, name, limit=None) -> str:
        """-> the new token, which is shown once; only its hash is kept."""
        if not isinstance(name, str) or not re.fullmatch(USER_NAME, name):
            raise UserError("A user name is 1 to 64 letters, digits, '.', '_' or '-', starting with a letter or digit.")
        limit = self._check_limit(limit)
        token = new_token_text()
        with self.conn() as c:
            try:
                c.execute("INSERT INTO users (name, token_hash, created, monthly_limit) VALUES (?, ?, ?, ?)",
                          (name, token_hash(token), now(), limit))
            except sqlite3.IntegrityError:
                raise UserError(f"There is already a user called {name}.") from None
        return token

    def set_limit(self, name, limit):
        limit = self._check_limit(limit)
        with self.conn() as c:
            c.execute("UPDATE users SET monthly_limit=? WHERE id=?", (limit, self._uid(c, name)))

    def new_token(self, name) -> str:
        """A new token for the user; the old one and every signed-in page stop working at once."""
        token = new_token_text()
        with self.conn() as c:
            uid = self._uid(c, name)
            c.execute("UPDATE users SET token_hash=? WHERE id=?", (token_hash(token), uid))
            c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        if name == LOCAL_USER and self.token_path.is_file():     # the local user's token file follows
            fd = os.open(self.token_path, os.O_WRONLY | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(token + "\n")
        return token

    def remove_user(self, name) -> dict:
        """Refused while the user has stored runs (they would be left with no owner). Otherwise removes the user, their
        sessions, their (empty) projects and their spending record. -> what the spending record held"""
        c = self.connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            uid = self._uid(c, name)
            runs = c.execute("SELECT COUNT(*) FROM runs WHERE user_id=?", (uid,)).fetchone()[0]
            if runs:
                raise UserError(f"{name} has {runs} stored run{'s' if runs != 1 else ''}, which would be left with no owner, so "
                                f"{name} was not removed. To stop {name} signing in and keep the runs, issue a new token "
                                f"(users token {name}) and don't pass it on.")
            spent = c.execute("SELECT COALESCE(SUM(cost_usd), 0), COUNT(*) FROM spend WHERE user_id=?", (uid,)).fetchone()
            for sql in ("DELETE FROM sessions WHERE user_id=?", "DELETE FROM feedback WHERE user_id=?",
                        "DELETE FROM projects WHERE user_id=?", "DELETE FROM spend WHERE user_id=?", "DELETE FROM users WHERE id=?"):
                c.execute(sql, (uid,))
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
        finally:
            c.close()
        return {"spent_total": round(spent[0], 4), "sessions_recorded": spent[1]}

    def list_users(self) -> list:
        start = month_start()
        with self.conn() as c:
            return [{"name": r["name"], "limit": r["monthly_limit"], "spent_this_month": round(r["spent"], 4),
                     "verifier_runs_this_month": r["vruns"], "stored_runs": r["runs"]}
                    for r in c.execute(
                        "SELECT u.name, u.monthly_limit, "
                        "(SELECT COALESCE(SUM(cost_usd), 0) FROM spend s WHERE s.user_id=u.id AND s.at>=?) AS spent, "
                        "(SELECT COUNT(DISTINCT job) FROM spend s WHERE s.user_id=u.id AND s.kind='run' AND s.at>=?) AS vruns, "
                        "(SELECT COUNT(*) FROM runs r WHERE r.user_id=u.id) AS runs FROM users u ORDER BY u.name", (start, start))]

    # ------------------------------------------------------------------ spending (the verifier service, hosted)
    def record_spend(self, user_id, job, kind, cost_usd):
        if not cost_usd:
            return
        with self.conn() as c:
            c.execute("INSERT INTO spend (user_id, job, kind, cost_usd, at) VALUES (?, ?, ?, ?, ?)",
                      (user_id, job, kind, float(cost_usd), utc_now()))

    def spent_this_month(self, user_id) -> float:
        with self.conn() as c:
            return c.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM spend WHERE user_id=? AND at>=?",
                             (user_id, month_start())).fetchone()[0]

    def over_limit(self, user_id):
        """The plain sentence to refuse with when this month's spend has reached the user's limit, else None."""
        with self.conn() as c:
            row = c.execute("SELECT monthly_limit FROM users WHERE id=?", (user_id,)).fetchone()
        if row is None:             # removed while a run was going on: nothing more is spent for them
            return "This user no longer exists on this verifier."
        if row["monthly_limit"] is None:
            return None
        return limit_message(row["monthly_limit"]) if self.spent_this_month(user_id) >= row["monthly_limit"] else None

    # ------------------------------------------------------------------ page sign-in sessions
    def new_session(self, user_id, days=SESSION_DAYS) -> str:
        """-> the random id for the cookie; only its hash is stored."""
        sid = secrets.token_urlsafe(32)
        with self.conn() as c:
            c.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
            c.execute("INSERT INTO sessions (id_hash, user_id, created, expires) VALUES (?, ?, ?, ?)",
                      (token_hash(sid), user_id, now(), time.time() + days * 86400))
        return sid

    def session_user(self, sid):
        if not sid:
            return None
        with self.conn() as c:
            row = c.execute("SELECT user_id FROM sessions WHERE id_hash=? AND expires>?", (token_hash(sid), time.time())).fetchone()
        return row["user_id"] if row else None

    def end_session(self, sid):
        if sid:
            with self.conn() as c:
                c.execute("DELETE FROM sessions WHERE id_hash=?", (token_hash(sid),))

    # ------------------------------------------------------------------ uploads
    def put_run(self, user_id, document: dict, files: list) -> tuple[str, list]:
        """Create the run, or replace an earlier upload of the same project, feature and run id (same id kept).
        files: [{"path", "kind", "size", "content_type"}], already checked. Returns (id, paths still to send)."""
        project = str(document["project"])
        props = json.dumps((document.get("totals") or {}).get("properties") or {})
        cases = json.dumps((document.get("totals") or {}).get("cases") or {})
        c = self.connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT OR IGNORE INTO projects (user_id, name) VALUES (?, ?)", (user_id, project))
            pid = c.execute("SELECT id FROM projects WHERE user_id=? AND name=?", (user_id, project)).fetchone()["id"]
            row = c.execute("SELECT id FROM runs WHERE user_id=? AND project_id=? AND feature=? AND run=?",
                            (user_id, pid, str(document["feature"]), str(document["run"]))).fetchone()
            values = (document.get("ran_at"), now(), props, cases, json.dumps(document), json.dumps(claims_of(document)),
                      commit_of(document))
            if row:
                rid = row["id"]
                c.execute("DELETE FROM files WHERE run_id=?", (rid,))
                c.execute("UPDATE runs SET ran_at=?, uploaded_at=?, complete=0, properties=?, cases=?, document=?, claims=?, "
                          "commit_sha=? WHERE id=? AND user_id=?", values + (rid, user_id))
            else:
                rid = secrets.token_hex(8)
                c.execute("INSERT INTO runs (id, user_id, project_id, feature, run, ran_at, uploaded_at, complete, properties, cases, "
                          "document, claims, commit_sha) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)",
                          (rid, user_id, pid, str(document["feature"]), str(document["run"])) + values)
            c.executemany("INSERT INTO files (run_id, path, kind, content_type, size, sha256) VALUES (?, ?, ?, ?, ?, NULL)",
                          [(rid, f["path"], f["kind"], f["content_type"], f["size"]) for f in files])
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
        finally:
            c.close()
        return rid, [f["path"] for f in files]

    def declared(self, user_id, run_id, path):
        """The file row for a path this user's run declared, or None (also None for another user's run)."""
        with self.conn() as c:
            return c.execute("SELECT f.* FROM files f JOIN runs r ON r.id=f.run_id WHERE r.id=? AND r.user_id=? AND f.path=?",
                             (run_id, user_id, path)).fetchone()

    def file_arrived(self, user_id, run_id, path, sha):
        with self.conn() as c:
            c.execute("UPDATE files SET sha256=? WHERE run_id=? AND path=? AND run_id IN (SELECT id FROM runs WHERE user_id=?)",
                      (sha, run_id, path, user_id))

    def complete(self, user_id, run_id):
        """None when the run is not this user's; else the paths still missing (the run is marked complete when none are)."""
        c = self.connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            if not c.execute("SELECT 1 FROM runs WHERE id=? AND user_id=?", (run_id, user_id)).fetchone():
                c.execute("ROLLBACK")
                return None
            missing = [r["path"] for r in c.execute("SELECT path FROM files WHERE run_id=? AND sha256 IS NULL ORDER BY path", (run_id,))]
            if not missing:
                c.execute("UPDATE runs SET complete=1 WHERE id=? AND user_id=?", (run_id, user_id))
            c.execute("COMMIT")
            return missing
        finally:
            c.close()

    # ------------------------------------------------------------------ reads
    @staticmethod
    def _summary(row) -> dict:
        return {"id": row["id"], "project": row["project"], "feature": row["feature"], "run": row["run"],
                "ran_at": row["ran_at"], "uploaded_at": row["uploaded_at"], "complete": bool(row["complete"]),
                "properties": json.loads(row["properties"]), "cases": json.loads(row["cases"]),
                "commit": row["commit_sha"], "claims": json.loads(row["claims"] or "[]")}

    _SUMMARY = ("SELECT r.id, p.name AS project, r.feature, r.run, r.ran_at, r.uploaded_at, r.complete, r.properties, r.cases, "
                "r.claims, r.commit_sha FROM runs r JOIN projects p ON p.id=r.project_id WHERE r.user_id=?")
    _NEWEST = " ORDER BY COALESCE(r.ran_at, r.uploaded_at) DESC, r.uploaded_at DESC"

    def runs(self, user_id, project=None, feature=None, limit=50) -> list:
        sql, args = self._SUMMARY, [user_id]
        if project:
            sql, args = sql + " AND p.name=?", args + [project]
        if feature:
            sql, args = sql + " AND r.feature=?", args + [feature]
        with self.conn() as c:
            return [self._summary(r) for r in c.execute(sql + self._NEWEST + " LIMIT ?", args + [limit])]

    def projects(self, user_id) -> list:
        out = []
        with self.conn() as c:
            for p in c.execute("SELECT p.id, p.name, COUNT(r.id) AS n FROM projects p LEFT JOIN runs r ON r.project_id=p.id "
                               "WHERE p.user_id=? GROUP BY p.id ORDER BY p.name", (user_id,)).fetchall():
                latest = c.execute(self._SUMMARY + " AND p.id=?" + self._NEWEST + " LIMIT 1", (user_id, p["id"])).fetchone()
                out.append({"name": p["name"], "runs": p["n"], "latest": self._summary(latest) if latest else None})
        return out

    # ------------------------------------------------------------------ feedback
    @staticmethod
    def _feedback(row) -> dict:
        return {"id": row["id"], "property": row["property"], "case": row["case_id"], "kind": row["kind"], "note": row["note"],
                "at": row["at"]}

    def feedback(self, user_id, run_id):
        """This user's feedback on one of their runs, oldest first; None when the run is not theirs."""
        with self.conn() as c:
            if not c.execute("SELECT 1 FROM runs WHERE id=? AND user_id=?", (run_id, user_id)).fetchone():
                return None
            return [self._feedback(r) for r in c.execute(
                "SELECT * FROM feedback WHERE run_id=? AND user_id=? ORDER BY at, rowid", (run_id, user_id))]

    def add_feedback(self, user_id, run_id, prop, case, kind, note) -> dict:
        fid = secrets.token_hex(8)
        with self.conn() as c:
            c.execute("INSERT INTO feedback (id, user_id, run_id, property, case_id, kind, note, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                      (fid, user_id, run_id, prop, case, kind, note, now()))
            return self._feedback(c.execute("SELECT * FROM feedback WHERE id=?", (fid,)).fetchone())

    def delete_feedback(self, user_id, run_id, fid) -> bool:
        with self.conn() as c:
            return c.execute("DELETE FROM feedback WHERE id=? AND run_id=? AND user_id=?", (fid, run_id, user_id)).rowcount == 1

    def run(self, user_id, run_id):
        with self.conn() as c:
            row = c.execute("SELECT id, uploaded_at, complete, document FROM runs WHERE id=? AND user_id=?", (run_id, user_id)).fetchone()
        if not row:
            return None
        return {"id": row["id"], "uploaded_at": row["uploaded_at"], "complete": bool(row["complete"]),
                "files_base": f"/v1/runs/{row['id']}/files/", "document": json.loads(row["document"])}
