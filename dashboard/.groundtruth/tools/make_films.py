"""Record the pool of ten made-up films that make_run.py attaches to its made-up cases.

Run once, from dashboard/, with a Python that has Playwright and Chromium:

    <venv>/bin/python .groundtruth/tools/make_films.py

Writes films/film-a.webm ... films/film-j.webm next to this file, each a plain page showing its name ("FILM A") in
very large letters for the whole clip (a thin bar grows along the bottom so every frame differs a little), 900 by 700, about 2, 2.5, 3 ... 6.5 seconds long. Then it measures each film
by loading it in Chromium and reading the video's own length, and writes films/films.json with those lengths.
"""
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
FILMS = HERE / "films"
LETTERS = "abcdefghij"
SIZE = {"width": 900, "height": 700}
STARTUP = 1.0      # seconds a recording runs beyond the wait (opening the page and closing the context), measured

PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;height:100%;background:{bg};}}
body{{display:flex;align-items:center;justify-content:center;font:900 190px/1 Helvetica,Arial,sans-serif;color:#111;}}
#bar{{position:fixed;left:0;bottom:0;height:14px;width:0;background:#111;animation:grow {secs}s linear forwards;}}
@keyframes grow{{to{{width:100%;}}}}
</style></head><body>FILM {name}<div id="bar"></div></body></html>"""
COLOURS = ["#ffe08a", "#b8e3ff", "#c9f2c0", "#ffc9c9", "#e2d1ff", "#ffd9b0", "#c7f0ec", "#f3f3a8", "#ffd1ec", "#d6dde6"]


def measure(page, path: Path) -> float:
    """The film's length as Chromium reports it. A film without a stated length is read to its end first."""
    page.goto(path.as_uri())
    page.wait_for_selector("video")
    return page.evaluate("""async () => {
        const v = document.querySelector('video');
        if (v.readyState < 1) await new Promise(r => v.addEventListener('loadedmetadata', r, {once: true}));
        if (!isFinite(v.duration)) {
            await new Promise(r => { v.addEventListener('durationchange', r, {once: true}); v.currentTime = 1e9; });
        }
        return v.duration;
    }""")


def main():
    FILMS.mkdir(exist_ok=True)
    lengths = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            for i, letter in enumerate(LETTERS):
                target = 2.0 + 0.5 * i
                tmp = Path(tempfile.mkdtemp(prefix="gt-film-"))
                try:
                    ctx = browser.new_context(viewport=SIZE, record_video_dir=str(tmp), record_video_size=SIZE)
                    page = ctx.new_page()
                    started = time.time()
                    page.set_content(PAGE.format(name=letter.upper(), bg=COLOURS[i], secs=target))
                    left = target - STARTUP - (time.time() - started)
                    if left > 0:
                        page.wait_for_timeout(left * 1000)
                    video = page.video
                    ctx.close()
                    video.save_as(FILMS / f"film-{letter}.webm")
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
            page = browser.new_page()
            for letter in LETTERS:
                f = FILMS / f"film-{letter}.webm"
                d = measure(page, f)
                if not isinstance(d, (int, float)) or d != d or d <= 0 or d == float("inf"):
                    sys.exit(f"Chromium could not tell the length of {f.name}.")
                lengths[f"film-{letter}"] = {"file": f.name, "seconds": round(d, 2), "bytes": f.stat().st_size}
        finally:
            browser.close()
    (FILMS / "films.json").write_text(json.dumps(lengths, indent=1) + "\n")
    for k, v in lengths.items():
        print(f"{v['file']}: {v['seconds']} s, {v['bytes']} bytes")


if __name__ == "__main__":
    main()
