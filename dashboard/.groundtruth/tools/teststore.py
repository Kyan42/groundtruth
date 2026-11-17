"""Where the made-up test store lives, and the one check every script makes before touching it.

The folder is fixed: dashboard/.groundtruth/test-store/, next to this tools folder. Tests may point the scripts at a
temporary folder through GT_TEST_STORE_DIR instead. Either way the folder must hold a file named MADE-UP-DATA-ONLY,
which only init_store.py writes, so these scripts can never empty or fill a real store by accident.
Standard library only; works on Python 3.9.
"""
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT = HERE.parent / "test-store"
MARKER = "MADE-UP-DATA-ONLY"
ENV = "GT_TEST_STORE_DIR"


class Refused(Exception):
    """A plain sentence saying why the script stopped."""


def folder() -> Path:
    given = os.environ.get(ENV)
    return Path(given).resolve() if given else DEFAULT


def checked_folder() -> Path:
    """The test store's folder, only if it carries the marker file."""
    d = folder()
    if not d.is_dir():
        raise Refused(f"The test store folder {d} does not exist. Run python3 .groundtruth/tools/init_store.py first.")
    if not (d / MARKER).is_file():
        raise Refused(f"The folder {d} has no {MARKER} file, so it may not be a made-up test store; nothing was changed.")
    return d
