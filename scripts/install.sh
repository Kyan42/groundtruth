#!/bin/sh
# Sets up the Groundtruth command on this machine: its own Python environment, three packages, and the browser the
# verifier drives. Safe to run again. Changes nothing outside this folder and the browser cache Playwright keeps.
#   ./scripts/install.sh
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$here"

py=""
for c in python3.13 python3.12 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
    py=$c
    break
  fi
done
if [ -z "$py" ]; then
  echo "Groundtruth needs Python 3.12 or newer, and none was found. Install it (python.org, or 'brew install python@3.12') and run this again." >&2
  exit 1
fi

echo "Using $($py -V) for Groundtruth's own environment (.venv)."
[ -x .venv/bin/python ] || "$py" -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt
echo "Getting the browser the verifier drives (Chromium, about 150 MB the first time)."
# (Playwright would otherwise delete browser versions it thinks nothing uses, which can break another project's tests.)
PLAYWRIGHT_SKIP_BROWSER_GC=1 .venv/bin/python -m playwright install chromium

if ! "$here/bin/groundtruth" --help >/dev/null 2>&1; then
  echo "The install finished but the command does not start. Run '$here/bin/groundtruth --help' to see why." >&2
  exit 1
fi
cat <<DONE

Groundtruth is installed. The command is:
  $here/bin/groundtruth

Next:
  1. Start the verifier and the results pages on this machine (needs Claude Code installed and signed in):
       $here/bin/groundtruth up
  2. Give your coding agent the skill, from your project's folder:
       $here/bin/groundtruth install-skills .                  # Claude Code
       $here/bin/groundtruth install-skills . --agent copilot  # GitHub Copilot
  3. Ask your agent to build something "and use groundtruth to verify it".
See README.md for the rest.
DONE
