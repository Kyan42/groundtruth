"""Make the made-up test store's folder and its marker file, so the other scripts will work on it.

    python3 .groundtruth/tools/init_store.py        (from dashboard/)

Then start the store on it: groundtruth.py store --port 8901 --data dashboard/.groundtruth/test-store
The store writes its own database and token on first start; this script never writes a token.
"""
import sys

from teststore import MARKER, folder


def main():
    d = folder()
    if (d / "store.db").exists() and not (d / MARKER).exists():
        sys.exit(f"{d} already holds a store that is not marked as made-up, so it was left alone. "
                 "Use an empty folder for the test store.")
    try:
        made = not d.exists()
        d.mkdir(parents=True, exist_ok=True)
        d.chmod(0o700)
        m = d / MARKER
        if not m.exists():
            m.write_text("This folder holds a results store filled with made-up runs only. "
                         "reset_store.py and make_run.py refuse any folder without this file.\n")
    except OSError as e:
        sys.exit(f"Could not set up the test store folder {d}: {e.strerror or e}.")
    print(f"{'Made' if made else 'Checked'} the test store folder {d}, with its {MARKER} marker.")


if __name__ == "__main__":
    main()
