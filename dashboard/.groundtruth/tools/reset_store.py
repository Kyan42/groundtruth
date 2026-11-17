"""Empty the made-up test store: every run, file record and project goes; the user and its token stay.

    python3 .groundtruth/tools/reset_store.py        (from dashboard/)

The store has no delete route, so this opens its SQLite file directly. That is safe while the store is running: it
waits for any upload in progress and changes everything in one transaction. The film bytes (at most one copy of
each pool film, stored by hash) are left on disk. It works only on the test store folder, and only when that folder
holds the MADE-UP-DATA-ONLY marker.
"""
import sqlite3
import sys

from teststore import Refused, checked_folder


def main():
    try:
        d = checked_folder()
    except Refused as e:
        sys.exit(str(e))
    db = d / "store.db"
    if not db.is_file():
        print(f"Nothing to remove: the test store in {d} has not been started yet, so it has no database.")
        return
    try:
        c = sqlite3.connect(str(db), timeout=30, isolation_level=None)
        try:
            c.execute("PRAGMA busy_timeout=30000")
            c.execute("BEGIN IMMEDIATE")
            try:
                n_files = c.execute("DELETE FROM files").rowcount
                n_runs = c.execute("DELETE FROM runs").rowcount
                n_projects = c.execute("DELETE FROM projects").rowcount
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise
        finally:
            c.close()
    except sqlite3.Error as e:
        sys.exit(f"Could not empty the test store's database {db}: {e}.")
    print(f"Removed {n_runs} run(s), {n_files} file record(s) and {n_projects} project(s) from the test store in {d}; "
          "the token still works.")


if __name__ == "__main__":
    main()
