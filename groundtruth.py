#!/usr/bin/env python3
"""Groundtruth: claims about what an app should do, approved by a person and checked against the running app.

Everyday commands (run from inside your app's folder; the folder and the feature are found when left out):
  login    [address] [--service URL] [--store URL] [--token T]   # save where the verifier and the results store are
  connect  <address> [--reset CMD] [--window WxH] [--project NAME] [--not-local]   # the app you already started
  draft    [--pr N|URL] [--issue N|URL] [--said FILE|-] [--feature NAME]          # draft claims from what was asked
  claims   [feature] [--full]                             # the claims in brief; --full adds your words, sources, what each is checked against
  approve  [feature]                                      # record that you approved the claims as they now stand
  verify   [feature] [--only ids] [--walkthrough] [--no-push] ...   # check the claims against the running app
  feedback <claim> [variation] --kind not_what_i_meant|wrong_result|right [--note TEXT]

Occasional:
  results  [feature] [--json]                             # each claim's latest result across the app's runs
  answer   <claim> <variation> intended|bug               # answer a question a run asked

For running Groundtruth itself (the older names still work, with their older arguments):
  register <repo> --url URL [--pid PID] [--reset CMD [--skip-reset-check]] [--routes SOURCE] [--project NAME] [--no-repo] [--no-pid] [--viewport WxH]
  summary  <repo> [run id] [--json]                       # one run in plain text (results shows the latest of each claim)
  decide   <repo> <feature> <property> <case> intended|bug   # the older answer
  push     <repo> [run id] [--store URL] [--project NAME] # upload a finished run to the store
  report   <repo> [run id]                                # the HTML report of a run
  serve    [--port 8790] [--model M] [--host H] [--data DIR]   # the verifier service ("hosted" half), as its own process
  store    [--port 8900] [--data DIR] [--host H]          # the results store and the pages
  users    add <name> [--limit DOLLARS] | list | limit <name> <DOLLARS|none> | remove <name> | token <name>   [--data DIR]
           # who may use a hosted Groundtruth (GT_HOSTED=1; see store/hosting.py), run where its data is kept
  up       [--service-port 8790] [--store-port 8900]      # start the service and the store if they are not running
  status   [--service-port 8790] [--store-port 8900]
  install-skills <project> [--remove]                     # copy skills/*/SKILL.md into <project>/.claude/skills/
  The everyday commands also take the app folder as their first word (claims <repo> <feature>, verify <repo> <feature>, ...).

Groundtruth doesn't build or start your app, and doesn't write its data tools: that is yours. It drafts claims,
records your approval, checks the claims against an app that is already running, and collects your feedback.
`verify` connects out to the verifier service and carries out the actions it asks for, through
a fixed tool list (toolserver/catalog.py). The service decides what to check and judges the result; it never gets
a path into this machine.

--viewport WIDTHxHEIGHT (for example 1280x800) is the size of the verifier's browser window and of every film: width
600 to 1920, height 500 to 1200, 900x700 when not given. Set at register; verify --viewport overrides it for one run.
--walkthrough adds one film of the whole feature in use, one stop per claim with variations; --walkthrough-only runs
just that film. It never changes a verdict.

--routes SOURCE tells the verifier where the list of API routes comes from:
  next | openapi:/openapi.json | file:routes.json | command:<shell command printing "METHOD /path" lines>
Without it, built-in readers are tried (Next.js build output and route files, /openapi.json).

Nothing is uploaded unless a store is configured (login, GT_STORE or --store) or you run `push` or `verify --push`.
The store's address comes from --store, else GT_STORE, else what login saved, else http://127.0.0.1:8900; its token
from GT_STORE_TOKEN, else what login saved, else .store/token beside this file.
`login` saves a service address, a store address and a token in ~/.groundtruth/cli.json (or GT_CONFIG; owner-only):
every command uses them when no flag or environment variable says otherwise, and `verify` then uploads by default.
The token goes to both the verifier (verify, draft) and the store (push, feedback). `login <address>` with one
address sets both: the store at the address and the verifier at <address>/verifier.
"""
import argparse
import json
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
DEFAULT_SERVICE = os.environ.get("GT_SERVICE", "http://127.0.0.1:8790")  # SERVICE_PORT
SERVICE_PORT, STORE_PORT = 8790, 8900
DEFAULT_STORE = f"http://127.0.0.1:{STORE_PORT}"
RUN_DIR = HERE / ".run"                 # logs and pid files of what `up` started
SKILLS_DIR = HERE / "skills"
LAUNCHER = HERE / "bin" / "groundtruth"
RESET_TIMEOUT = 120                     # as the tool server runs a reset (toolserver/catalog.py, reset_data)
DRAFT_POLL, DRAFT_LIMIT = 3, 15 * 60    # draft: seconds between asking whether it is done, and how long in all


def config_path() -> Path:
    """Where `login` saves its settings. Not ~/.groundtruth/config.json, which an older product uses on some machines
    and which is never read."""
    return Path(os.environ.get("GT_CONFIG") or Path.home() / ".groundtruth" / "cli.json")


def load_config() -> dict:
    """What `login` saved: {"service", "store", "token"}; {} when nothing was saved or it can't be read."""
    try:
        got = json.loads(config_path().read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(got, dict):
        return {}
    # Only plain text values count: a file left by something else with the same name is ignored, not crashed on.
    return {k: v for k, v in got.items() if isinstance(v, str)}


def service_address(given=None) -> str:
    """--service, else GT_SERVICE, else what login saved, else the local service."""
    return (given or os.environ.get("GT_SERVICE") or load_config().get("service") or f"http://127.0.0.1:{SERVICE_PORT}").rstrip("/")


def store_configured(given=None) -> bool:
    return bool(given or os.environ.get("GT_STORE") or load_config().get("store"))


def store_address(given=None) -> str:
    """--store, else GT_STORE, else what login saved, else the local store."""
    return (given or os.environ.get("GT_STORE") or load_config().get("store") or DEFAULT_STORE).rstrip("/")


def store_token(store=None) -> str:
    """The token: GT_STORE_TOKEN, else what login saved, else (only for a store on this machine) the file the local
    store wrote. Raises PushError when there is none."""
    from toolserver.push import PushError
    token = service_token()
    if token:
        return token
    f = HERE / ".store" / "token"
    if store and not is_local(urlparse(store).hostname):
        raise PushError(f"there is no token for the results store at {store}: run groundtruth.py login with the token you were given")
    if f.is_file() and f.read_text().strip():
        return f.read_text().strip()
    raise PushError(f"there is no token for the results store: set GT_STORE_TOKEN, or start the store once "
                    f"(groundtruth.py store) so it writes {f}")


def service_token() -> str:
    """The token sent to the verifier service, the same one the store takes: GT_STORE_TOKEN, else what login saved,
    else "" (a service on this machine needs none)."""
    return os.environ.get("GT_STORE_TOKEN", "").strip() or str(load_config().get("token") or "").strip()


def project_name(repo: Path, given=None) -> str:
    """The name an app's runs are kept under in the store: --project, else the name given at register, else the
    repo folder's name. One app should always land under one name."""
    if given:
        return given
    try:
        registered = json.loads(env_path(repo).read_text()).get("project")
    except Exception:  # noqa: BLE001
        registered = None
    return registered or Path(repo).name


def upload_step(run_dir: Path, repo: Path, store=None, project=None, say=print):
    """verify --push: upload the saved run. A failure is one printed line; it never changes a verdict or the exit
    code. Returns the dashboard address, or None."""
    try:
        from toolserver.push import push_run
        got = push_run(run_dir, repo, store_address(store), store_token(store_address(store)), project=project_name(repo, project), say=say)
    except Exception as e:  # noqa: BLE001
        say(f"Upload: failed ({e}). The run is saved here; upload it later with: groundtruth.py push {repo} {Path(run_dir).name}")
        return None
    save_pushed(run_dir, store_address(store), got)
    say(f"Dashboard: {got['url']}")
    if got.get("project") and got.get("feature"):
        say(f"Feature page: {feature_page(store_address(store), got['project'], got['feature'])}")
    return got["url"]


def feature_page(store: str, project: str, feature: str) -> str:
    """The address of a feature's summary page on the store a run was pushed to."""
    from urllib.parse import urlencode
    return f"{store.rstrip('/')}/feature.html?" + urlencode({"project": project, "feature": feature})


def save_pushed(run_dir: Path, store: str, got: dict):
    """What the store answered, kept beside the run so `summary` can give the dashboard address."""
    try:
        (Path(run_dir) / "pushed.json").write_text(json.dumps(
            {"store": store, "id": got.get("id"), "url": got.get("url"), "project": got.get("project"), "feature": got.get("feature"),
             "pushed_at": time.strftime("%Y-%m-%dT%H:%M:%S")}, indent=2) + "\n")
    except OSError:
        pass


def env_path(repo: Path) -> Path:
    return repo / ".groundtruth" / "env.json"


def _out(cmd, cwd=None):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=15).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def routes_source(text):
    if text == "next":
        return {"type": "next"}
    kind, _, rest = text.partition(":")
    if kind == "openapi":
        return {"type": "openapi", "path": rest or "/openapi.json"}
    if kind == "file" and rest:
        return {"type": "file", "path": rest}
    if kind == "command" and rest:
        return {"type": "command", "cmd": rest}
    sys.exit("--routes must be: next | openapi:/path | file:<repo-relative json> | command:<shell command>")


def cmd_register(a):
    repo = Path(a.repo).resolve()
    u = urlparse(a.url)
    if u.scheme not in ("http", "https") or not u.hostname:
        sys.exit("--url must be a full address such as http://127.0.0.1:3000")
    port = u.port or (443 if u.scheme == "https" else 80)
    env = {"url": a.url.rstrip("/")}
    if not a.no_pid:
        pid = a.pid
        if pid is None:
            out = _out(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"]).split()
            if not out:
                sys.exit(f"Nothing is listening on port {port}. Start the app first, then register it.")
            pid = int(out[0])
        env["pid"] = pid
    if not a.no_repo:
        env["repo"] = str(repo)
    if a.reset:
        env["reset"] = a.reset
    if a.routes:
        env["routes"] = routes_source(a.routes)
    if a.project:
        env["project"] = a.project      # the name this app's runs are kept under in the results store
    if getattr(a, "viewport", None):
        from toolserver.runner import ServiceError, viewport_of
        try:
            env["viewport"] = viewport_of(a.viewport)   # the verifier's window, and the size of every film
        except ServiceError as e:
            sys.exit(f"--viewport: {e}")
    # What the verdicts will be about: recorded now, shown at the top of every report.
    facts = {"registered_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "platform": platform.platform()}
    if "pid" in env:
        facts["command"] = _out(["ps", "-o", "command=", "-p", str(env["pid"])])
        facts["started"] = _out(["ps", "-o", "lstart=", "-p", str(env["pid"])])
    commit = _out(["git", "rev-parse", "--short", "HEAD"], cwd=repo)
    if commit:
        facts["commit"] = commit
        facts["uncommitted_changes"] = bool(_out(["git", "status", "--porcelain"], cwd=repo))
    env["facts"] = facts
    if "reset" in env and not a.skip_reset_check:
        problem = try_reset(env["reset"], str(repo) if env.get("repo") else None)
        if problem:
            sys.exit(f"{problem}\nNothing was registered. Fix the command, or register with --skip-reset-check to store it without trying it.")
    replaced = env_path(repo).exists()
    env_path(repo).parent.mkdir(parents=True, exist_ok=True)
    env_path(repo).write_text(json.dumps(env, indent=2))
    can = ["browser and API"] + (["process measurements"] if "pid" in env else []) + (["code reading"] if "repo" in env else [])
    lacks = ([] if "pid" in env else ["no process id: nothing can be measured"]) + ([] if "repo" in env else ["no repo: code can't be read"]) \
        + ([] if "reset" in env else ["no reset command: cases won't start from clean data"])
    print(f"Registered {env['url']}" + (f" (pid {env['pid']})" if "pid" in env else "") + f" for {repo.name}")
    print(f"  the verifier can use: {', '.join(can)}")
    for line in lacks:
        print(f"  {line}")
    if "reset" in env:
        print(f"  reset command, run in the repo before every case ({'not tried now' if a.skip_reset_check else 'tried once now; it worked'}): {env['reset']}")
    if replaced:
        print("  this replaces the earlier registration for this repo")


def try_reset(cmd: str, cwd):
    """Run the reset command once, the way the tool server does. None when it worked, else what went wrong."""
    try:
        r = subprocess.run(cmd, shell=True, cwd=cwd, timeout=RESET_TIMEOUT, capture_output=True, text=True)
    except subprocess.TimeoutExpired as e:
        out = "".join(x.decode(errors="replace") if isinstance(x, bytes) else (x or "") for x in (e.stdout, e.stderr))
        return (f"The reset command did not finish within {RESET_TIMEOUT} seconds." +
                (f" The last of its output:\n{out[-300:]}" if out.strip() else " It printed nothing."))
    if r.returncode != 0:
        out = (r.stdout or "") + (r.stderr or "")
        return (f"The reset command failed (exit {r.returncode})." +
                (f" The last of its output:\n{out[-300:]}" if out.strip() else " It printed nothing."))
    return None


def cmd_serve(a):
    from service.server import serve
    serve(a.port, a.model, getattr(a, "host", None), data_dir=getattr(a, "data", None))


def cmd_verify(a):
    repo = Path(a.repo).resolve()
    if not env_path(repo).exists():
        sys.exit("No app registered. Start it, then: groundtruth.py register <repo> --url ...")
    from report import write_report
    from toolserver.runner import ServiceError, run_verify
    try:
        run_dir = run_verify(repo, a.feature, service_address(a.service), only=a.only, headed=os.environ.get("GT_HEADED") == "1",
                             say=lambda s: print(s, flush=True), plan_only=a.plan_only, viewport=getattr(a, "viewport", None),
                             walkthrough=getattr(a, "walkthrough", False), walkthrough_only=getattr(a, "walkthrough_only", False),
                             no_context=getattr(a, "no_context", False), pr=getattr(a, "pr", None), token=service_token() or None)
    except ServiceError as e:
        hint = "\nStart the service first: groundtruth.py serve" if "couldn't reach the verifier service" in str(e) else ""
        sys.exit(f"{e}{hint}")
    run = json.loads((run_dir / "run.json").read_text())
    verdicts = [p.get("verdict") for p in run["properties"]]
    if run.get("error"):
        print(f"The run did not finish: {run['error']}")
    print(f"Result: {verdicts.count('holds')} hold, {verdicts.count('fails')} fail, "
          f"{verdicts.count('needs_decision')} need your decision, {verdicts.count('cant_tell')} can't tell"
          + (f", {verdicts.count('planned')} planned only" if "planned" in verdicts else ""))
    print(f"Details: {run_dir / 'run.json'}")
    try:
        print(f"Report: {write_report(run_dir)}")
    except Exception as e:  # noqa: BLE001  (the run's results are saved either way)
        print(f"Report: could not be built ({type(e).__name__}: {e})")
    # Upload when asked, or by default when a store is configured (login, GT_STORE or --store), unless --no-push.
    if a.push or (store_configured(a.store) and not getattr(a, "no_push", False)):
        upload_step(run_dir, repo, a.store, project=a.project, say=lambda s: print(s, flush=True))
    try:
        print("")
        print(summary_text(run_summary(repo, run_dir.name)))
    except Exception as e:  # noqa: BLE001 -- the run is saved either way
        print(f"Summary: could not be built ({type(e).__name__}: {e})")
    if a.exit_code:      # 0 only when every property holds; 1 when any fails; 2 for anything else, a run that broke included
        sys.exit(1 if "fails" in verdicts else 0 if verdicts and all(x == "holds" for x in verdicts) and not run.get("error") else 2)


def cmd_decide(a):
    """Answer a question the last run asked about one case: is what the app does there intended, or a bug?

    "bug": the situation is inside the property, so the property fails on it from now on.
    "intended": the situation is outside the property, and the sentence the verifier drafted for it is added to the
    feature file as a property of its own, so the accepted behaviour is checked too."""
    repo = Path(a.repo).resolve()
    runs_dir = repo / ".groundtruth" / "runs"
    question = None
    for run_dir in sorted(runs_dir.iterdir(), reverse=True) if runs_dir.exists() else []:
        f = run_dir / "run.json"
        run = json.loads(f.read_text()) if f.exists() else {}
        if a.feature and run.get("feature") != a.feature:
            continue
        for p in run.get("properties", []):
            for q in p.get("questions") or []:
                if p["id"] == a.property and q["case"] == a.case:
                    question = q
        if question:
            if not a.feature:                   # `answer`: the feature is the run's
                a.feature = run.get("feature_file") or run.get("feature")
            break
    if not question:
        sys.exit(f"No open question about {a.property}/{a.case} in the latest run" + (f" of {a.feature}" if a.feature else "")
                 + " that asked one.")
    dfile = repo / ".groundtruth" / "decisions" / f"{a.feature}.json"
    decided = json.loads(dfile.read_text()) if dfile.exists() else {}
    if any(d.get("question") == question["question"] for d in decided.get(a.property, [])):
        sys.exit(f"That question about {a.property} was already answered. Edit {dfile} to change the answer.")
    decided.setdefault(a.property, []).append({"case": a.case, "question": question["question"], "observed": question["observed"],
                                               "answer": a.answer, "decided_at": time.strftime("%Y-%m-%d")})
    dfile.parent.mkdir(parents=True, exist_ok=True)
    dfile.write_text(json.dumps(decided, indent=2))
    print(f"Recorded: {a.property}/{a.case} is {a.answer}.")
    if a.answer == "intended" and question.get("draft_if_intended"):
        import yaml
        ffile = repo / ".groundtruth" / "features" / f"{a.feature}.yml"
        ids = {p["id"] for p in yaml.safe_load(ffile.read_text())["properties"]}
        new_id = next(f"{a.property}-{n}" for n in range(1, 100) if f"{a.property}-{n}" not in ids)
        with open(ffile, "a") as f:       # appended as text so the file's comments and layout survive
            f.write(f"  - id: {new_id}\n    statement: {json.dumps(question['draft_if_intended'])}\n"
                    f"    came_from: Your decision on {a.property}/{a.case}, {time.strftime('%Y-%m-%d')}\n")
        print(f"Added property {new_id}: {question['draft_if_intended']}")
        print("Edit the sentence in the feature file if it isn't what you meant.")


def cmd_report(a):
    repo = Path(a.repo).resolve()
    runs_dir = repo / ".groundtruth" / "runs"
    if a.run:
        run_dir = runs_dir / a.run
        if not (run_dir / "run.json").is_file():
            sys.exit(f"Run {a.run} did not finish or does not exist, so there is no report to write for it.")
    else:
        # verify creates a run's folder before the run finishes, so only folders with a run.json count
        runs = sorted(p for p in runs_dir.iterdir() if (p / "run.json").is_file()) if runs_dir.is_dir() else []
        if not runs:
            sys.exit("No runs yet. Run: groundtruth.py verify <repo> <feature>")
        run_dir = runs[-1]
    from report import write_report
    print(write_report(run_dir))


def cmd_store(a):
    from store.server import serve
    serve(a.port, Path(a.data).resolve() if a.data else None, host=getattr(a, "host", None))


def cmd_users(a):
    """Who may use this Groundtruth, run on the machine that keeps the data (GT_DATA, or --data, or .store here)."""
    from store.db import Store, UserError, dollars
    from store.hosting import data_dir
    folder = data_dir(a.data)
    if a.action != "add" and not (folder / "store.db").is_file():
        sys.exit(f"There is no store in {folder} yet. Start it once, or give --data, or set GT_DATA.")
    store = Store(folder, hosted=True)          # never makes the local user; the store does that itself, locally

    def money(x):
        if x is None or str(x).strip().lower() == "none":
            return None
        try:
            v = float(str(x).strip().lstrip("$"))
        except ValueError:
            sys.exit(f"{x} is not an amount of US dollars. Give a number such as 50, or none.")
        return v

    try:
        if a.action == "add":
            if not a.name:
                sys.exit("Give the new user's name: groundtruth.py users add <name> [--limit DOLLARS]")
            token = store.add_user(a.name, money(a.limit))
            print(f"Added {a.name}" + (f", with a limit of {dollars(money(a.limit))} a month." if a.limit not in (None, "none") else
                                       ", with no spending limit."))
            print(f"Their token (shown only this once; only its hash is kept): {token}")
            print("They sign in with: groundtruth.py login <address> --token <token>")
        elif a.action == "list":
            rows = store.list_users()
            if not rows:
                print("No users yet. Add one with: groundtruth.py users add <name> --limit DOLLARS")
            for r in rows:
                print(f"{r['name']}: limit {'none' if r['limit'] is None else dollars(r['limit'])}, "
                      f"spent {dollars(r['spent_this_month'])} this month on {r['verifier_runs_this_month']} "
                      f"run{'s' if r['verifier_runs_this_month'] != 1 else ''}, {r['stored_runs']} stored "
                      f"run{'s' if r['stored_runs'] != 1 else ''}")
        elif a.action == "limit":
            if not a.name or a.value is None:
                sys.exit("Give a name and an amount: groundtruth.py users limit <name> <DOLLARS|none>")
            store.set_limit(a.name, money(a.value))
            v = money(a.value)
            print(f"{a.name} now has " + ("no spending limit." if v is None else f"a limit of {dollars(v)} a month."))
        elif a.action == "remove":
            if not a.name:
                sys.exit("Give the name: groundtruth.py users remove <name>")
            gone = store.remove_user(a.name)
            print(f"Removed {a.name}, their sign-ins and their spending record ({dollars(gone['spent_total'])} over "
                  f"{gone['sessions_recorded']} model sessions).")
        elif a.action == "token":
            if not a.name:
                sys.exit("Give the name: groundtruth.py users token <name>")
            token = store.new_token(a.name)
            print(f"New token for {a.name} (shown only this once). The old one, and every page signed in with it, no longer work:")
            print(token)
    except UserError as e:
        sys.exit(str(e))


def finished_run(repo: Path, run_id=None) -> Path:
    runs_dir = repo / ".groundtruth" / "runs"
    if run_id:
        run_dir = runs_dir / run_id
        if not (run_dir / "run.json").is_file():
            sys.exit(f"Run {run_id} did not finish or does not exist.")
        return run_dir
    runs = sorted(p for p in runs_dir.iterdir() if (p / "run.json").is_file()) if runs_dir.is_dir() else []
    if not runs:
        sys.exit("No finished runs yet. Run: groundtruth.py verify <repo> <feature>")
    return runs[-1]


FEEDBACK_KINDS = ("not_what_i_meant", "wrong_result", "right")


def cmd_feedback(a):
    """Tell the store what a person thought of one claim (and optionally one variation) in a pushed run."""
    import urllib.error
    import urllib.request
    repo = Path(a.repo).resolve()
    run_dir = finished_run(repo, a.run)
    try:
        pushed = json.loads((run_dir / "pushed.json").read_text())
    except (OSError, ValueError):
        pushed = {}
    if not isinstance(pushed, dict) or not pushed.get("id"):
        sys.exit(f"Run {run_dir.name} was never pushed to a store, so there is nowhere to keep feedback on it. "
                 f"Push it first: groundtruth.py push {repo} {run_dir.name}")
    store = store_address(a.store or pushed.get("store"))
    body = {"property": a.claim, "kind": a.kind, "note": a.note or ""}
    if a.variation:
        body["case"] = a.variation
    headers = {"Content-Type": "application/json"}
    try:
        headers["Authorization"] = f"Bearer {store_token(store)}"
    except Exception:  # noqa: BLE001 -- a store on this machine takes feedback without a token
        pass
    from toolserver.push import open_no_redirect
    req = urllib.request.Request(f"{store}/v1/runs/{pushed['id']}/feedback", data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with open_no_redirect(req, timeout=30) as r:
            got = json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error") or e.reason
        except Exception:  # noqa: BLE001
            msg = e.reason
        sys.exit(f"The store did not keep the feedback: {msg}")
    except (urllib.error.URLError, OSError) as e:
        sys.exit(f"Couldn't reach the results store at {store} ({getattr(e, 'reason', e)}).")
    print(f"Feedback kept for {a.claim}" + (f"/{a.variation}" if a.variation else "") + f" in run {run_dir.name}: {got.get('kind')}.")


def cmd_push(a):
    from toolserver.push import PushError, push_run
    repo = Path(a.repo).resolve()
    run_dir = finished_run(repo, a.run)
    try:
        got = push_run(run_dir, repo, store_address(a.store), store_token(store_address(a.store)), project=project_name(repo, a.project))
    except PushError as e:
        hint = "\nStart the store first: groundtruth.py store" if "couldn't reach the results store" in str(e) else ""
        sys.exit(f"Upload failed: {e}{hint}")
    save_pushed(run_dir, store_address(a.store), got)
    print(f"Uploaded. Dashboard: {got['url']}")


# ---------------------------------------------------------------- for a coding agent: claims, approval, services, summary, skills

def claims_brief(repo: Path, feature: str) -> str:
    """The short printout, and the default: each claim's title, its sentence and its variations. Approval is one line
    at the top, and a claim says its own state only when it differs from the rest. Questions stay, because they block
    approval; everything else is counted in one closing line that names --full."""
    from toolserver import approval
    from toolserver.runner import not_checked
    spec, claims = approval.read_claims(repo, feature)
    record = approval.load_record(repo, feature)
    states = {pid: (st or {}).get("state") or "new" for pid, st in approval.compare(claims, record).items()}
    kinds = [states.get(pid, "new") for pid, _ in claims]
    n = len(claims)
    count = f"{n} claim{'s' if n != 1 else ''}"
    when = str((record or {}).get("approved_at") or "")[:10]
    if kinds and all(k == "approved" for k in kinds):
        head, mixed = f"{count}, approved" + (f" {when}" if when else ""), False
    elif not record or all(k != "approved" and k != "changed" for k in kinds):
        head, mixed = f"{count}, not approved yet", False
    else:
        off = sum(1 for k in kinds if k != "approved")
        head, mixed = f"{count}, {off} not as approved" + (f" on {when}" if when else ""), True
    out = [f"{spec.get('feature') or feature}: {head}"]
    for i, (pid, parts) in enumerate(claims, 1):
        mark = ""
        if mixed and states.get(pid, "new") != "approved":
            mark = "  (changed since it was approved)" if states.get(pid) == "changed" else "  (not approved yet)"
        out += ["", f"{i}. {parts.get('title') or pid}{mark}", f"   {parts['statement']}"]
        out += [f"   - {v['what']}" for v in parts.get("variations") or []]
    asked = approval.questions(spec, f"{feature}.yml")
    if asked:
        out += ["", "Questions the draft could not answer:"]
        out += ["   - " + (f"({q['about']}) " if q["about"] else "") + q["question"] for q in asked]
    unchecked = not_checked(spec, f"{feature}.yml")
    more = "your words, where each claim came from, what each is checked against"
    if unchecked:
        more += f", and {len(unchecked)} thing{'s' if len(unchecked) != 1 else ''} these claims do not check"
    out += ["", f"Not shown: {more}. Add --full to see them."]
    return "\n".join(out)


def claims_text(repo: Path, feature: str, full: bool = True) -> str:
    """The feature's claims as they are on disk, for an agent to paste to its owner word for word. The full printout;
    claims_brief is the short one."""
    if not full:
        return claims_brief(repo, feature)
    from toolserver import approval
    spec, claims = approval.read_claims(repo, feature)
    record = approval.load_record(repo, feature)
    states = approval.compare(claims, record)
    raw = {p.get("id"): p for p in spec["properties"] if isinstance(p, dict)}
    from toolserver.runner import source_words
    sources = approval.sources(spec)
    out = [f"Claims for {spec.get('feature') or feature} (.groundtruth/features/{feature}.yml)"]
    if record:
        out.append(f"Last approved: {str(record.get('approved_at') or 'unknown').replace('T', ' ')}")
    goal = str(spec.get("goal") or "").strip()
    if goal:
        out.append(f"Goal: {goal}")
    for n, (pid, parts) in enumerate(claims, 1):
        st = states.get(pid) or {}
        state = ("approved" if st.get("state") == "approved" else
                 f"changed since it was approved ({approval.words(st.get('changed') or [])})" if st.get("state") == "changed" else
                 "not approved yet")
        out += ["", f"{n}. {pid}" + (f": {parts['title']}" if parts.get("title") else ""), f"   Approval: {state}"]
        if parts.get("your_words"):
            out.append(f"   Your words: “{parts['your_words']}”")
        if sources.get(pid):
            out.append(f"   Source: {source_words(sources[pid])}")
        out.append(f"   Sentence: {parts['statement']}")
        if parts.get("checked_against"):
            out.append(f"   Checked against: {parts['checked_against']}")
        if parts.get("variations"):
            out.append("   Variations:")
            out += [f"     - {v['id']}: {v['what']}" for v in parts["variations"]]
        if (raw.get(pid) or {}).get("example"):
            out.append(f"   Example: {str(raw[pid]['example']).strip()}")
    asked = approval.questions(spec, f"{feature}.yml")
    if asked:
        out += ["", "Questions the draft could not answer:"]
        out += [f"   - " + (f"({q['about']}) " if q["about"] else "") + q["question"] for q in asked]
    from toolserver.runner import not_checked
    unchecked = not_checked(spec, f"{feature}.yml")
    if unchecked:
        out += ["", "What these claims do not check:"] + [f"   - {x}" for x in unchecked]
    return "\n".join(out)


def cmd_claims(a):
    from toolserver.approval import ClaimsError
    repo = Path(a.repo).resolve()
    try:
        print(claims_text(repo, a.feature, full=bool(getattr(a, 'full', False))))
    except ClaimsError as e:
        sys.exit(str(e))
    # The first time a feature's claims are shown, before any approval, keep them as drafted, so `approve` can say
    # what the owner changed.
    from toolserver import approval
    try:
        spec, claims = approval.read_claims(repo, a.feature)
        approval.keep_draft(repo, a.feature, spec, claims)
    except ClaimsError:
        pass


def cmd_approve(a):
    from toolserver import approval
    repo = Path(a.repo).resolve()
    try:
        spec, claims = approval.read_claims(repo, a.feature)
    except approval.ClaimsError as e:
        sys.exit(f"{e}\nNothing was approved.")
    asked = approval.questions(spec, f"{a.feature}.yml")
    if asked:
        sys.exit(f"{a.feature}.yml still has {len(asked)} question{'s' if len(asked) != 1 else ''} the draft could not answer. "
                 "Answer each by editing the claims, delete it from `questions:`, then approve again.\nNothing was approved.")
    draft = approval.load_draft(repo, a.feature)
    changes = approval.draft_changes(draft, approval.snapshot(spec, claims)) if draft else None
    approval.write_record(repo, a.feature, claims, changes)
    for pid, parts in claims:
        n = len(parts.get("variations") or [])
        print(f"Approved {pid}" + (f": {parts['title']}" if parts.get("title") else "")
              + (f" ({n} variation{'s' if n != 1 else ''})" if n else ""))
    print("No draft was kept for these claims, so there is nothing to compare with." if changes is None else
          f"{len(changes)} change{'s' if len(changes) != 1 else ''} since the claims were first drafted.")


def health(port: int, timeout=2.0):
    """What answers /health on 127.0.0.1:<port>: ("none", None) nothing listens; ("service", body); ("store", body);
    ("other", what it said)."""
    import socket
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as r:
            raw = r.read(4096)
    except urllib.error.HTTPError as e:
        return "other", f"it answered /health with {e.code}"
    except urllib.error.URLError as e:
        if isinstance(e.reason, ConnectionRefusedError):
            return "none", None
        return "other", f"it did not answer /health ({e.reason})"
    except (socket.timeout, TimeoutError):
        return "other", f"it did not answer /health within {timeout:g} seconds"
    except (ConnectionError, OSError) as e:
        return "other", f"it did not answer /health ({e})"
    try:
        body = json.loads(raw)
    except ValueError:
        return "other", "it answered /health with something that is not the service or the store"
    if isinstance(body, dict) and body.get("ok") is True and "version" in body:
        return "service", body
    if isinstance(body, dict) and body.get("ok") is True and body.get("store"):
        return "store", body
    return "other", "it answered /health with something that is not the service or the store"


NAMES = {"service": "Verifier service", "store": "Results store"}
_STARTED = []


def bring_up(service_port=SERVICE_PORT, store_port=STORE_PORT, run_dir: Path = None, store_data=None, wait=20.0, say=print) -> bool:
    """Start whichever of the two is not answering, detached, and wait for it. Never stops or restarts anything."""
    run_dir = Path(run_dir or RUN_DIR)
    ok = True
    for role, port in (("service", service_port), ("store", store_port)):
        name, url = NAMES[role], f"http://127.0.0.1:{port}"
        got, body = health(port)
        if got == role:
            say(f"{name}: already running at {url}" + (f" (version {body.get('version')})" if role == "service" else ""))
            continue
        if got != "none":
            other = NAMES.get(got)
            say(f"{name}: port {port} is taken by " + (f"the {other.lower()}, not the {name.lower()}" if other else f"something else: {body}")
                + f". Nothing was started. Stop it, or choose another port with --{role}-port.")
            ok = False
            continue
        run_dir.mkdir(parents=True, exist_ok=True)
        log = run_dir / f"{role}-{port}.log"
        cmd = [sys.executable, str(HERE / "groundtruth.py"), "serve" if role == "service" else "store", "--port", str(port)]
        if role == "store" and store_data:
            cmd += ["--data", str(store_data)]
        with open(log, "w") as fh:
            proc = subprocess.Popen(cmd, cwd=str(HERE), stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    start_new_session=True)
        _STARTED.append(proc)            # kept so Python does not warn about a child that is meant to outlive it
        (run_dir / f"{role}-{port}.pid").write_text(f"{proc.pid}\n")
        deadline, state = time.time() + wait, None
        while time.time() < deadline:
            if proc.poll() is not None:
                state = "exited"
                break
            if health(port, timeout=1.0)[0] == role:
                state = "up"
                break
            time.sleep(0.2)
        if state == "up":
            say(f"{name}: started at {url} (pid {proc.pid}, log {log})")
            continue
        ok = False
        tail = log.read_text(errors="replace")[-300:].strip() if log.is_file() else ""
        if state == "exited":
            say(f"{name}: did not start (it stopped with exit {proc.returncode}). The end of its log ({log}):\n{tail}")
        else:
            say(f"{name}: started (pid {proc.pid}) but did not answer at {url} within {wait:g} seconds. It was left running; see {log}")
    return ok


def status_lines(service_port=SERVICE_PORT, store_port=STORE_PORT) -> tuple[list, bool]:
    lines, ok = [], True
    for role, port in (("service", service_port), ("store", store_port)):
        name, url = NAMES[role], f"http://127.0.0.1:{port}"
        got, body = health(port)
        if got == role:
            lines.append(f"{name}: running at {url}" + (f", version {body.get('version')}" + (f", model {body['model']}" if body.get("model") else "")
                                                      if role == "service" else ""))
            continue
        ok = False
        if got == "none":
            lines.append(f"{name}: not running (nothing answers on port {port})")
        elif got in NAMES:
            lines.append(f"{name}: not running; port {port} has the {NAMES[got].lower()} on it")
        else:
            lines.append(f"{name}: not running; port {port} is taken by something else ({body})")
    return lines, ok


def cmd_up(a):
    if not bring_up(a.service_port, a.store_port):
        sys.exit(1)


def cmd_status(a):
    lines, ok = status_lines(a.service_port, a.store_port)
    print("\n".join(lines))
    if not ok:
        sys.exit(1)


VERDICT_WORDS = {"holds": "holds", "fails": "fails", "needs_decision": "needs your decision", "cant_tell": "can't tell",
                 "planned": "planned, not run yet", "not_run": "not run in this run"}
OUTCOME_WORDS = {"holds": "held", "breaks": "broke", "declined": "needs your decision", "outside": "outside the property's wording",
                 "nothing_there": "nothing there to test", "cant_run": "could not run", "planned": "planned, not run yet"}


WALK_WORDS = {"holds": "held", "breaks": "something did not match"}


class SummaryError(Exception):
    pass


def run_summary(repo: Path, run_id=None) -> dict:
    """One finished run, shaped for an agent to relay. SummaryError (plain words) when there is no finished run."""
    from toolserver import approval, rundoc
    runs_dir = repo / ".groundtruth" / "runs"
    if run_id:
        run_dir = runs_dir / run_id
        if not run_dir.is_dir():
            raise SummaryError(f"There is no run {run_id} in {runs_dir}.")
    else:
        # the newest run, finished or not: an agent asking while a run goes on must not get the run before it
        runs = sorted(p for p in runs_dir.iterdir() if p.is_dir()) if runs_dir.is_dir() else []
        if not runs:
            raise SummaryError("There are no runs yet. Run: groundtruth verify <app folder> <feature>")
        run_dir = runs[-1]
    if not (run_dir / "run.json").is_file():
        raise SummaryError(f"Run {run_dir.name} has no results yet: it is still running, or it stopped before saving them "
                           f"(there is no run.json in {run_dir}).")
    doc, _files = rundoc.build(run_dir, repo=repo)
    try:
        sent = {p.get("id"): p for p in json.loads((run_dir / "sent.json").read_text()).get("properties") or [] if isinstance(p, dict)}
    except Exception:  # noqa: BLE001
        sent = {}
    props, not_held, flags = [], [], []
    for p in doc["properties"]:
        cases = p.get("cases") or []
        unit = "variations" if (sent.get(p["id"]) or {}).get("variations") else "cases"
        props.append({"id": p["id"], "title": p.get("title") or "", "statement": p.get("statement") or "", "verdict": p["verdict"],
                      "verdict_words": VERDICT_WORDS.get(p["verdict"], p["verdict"]),
                      "held": sum(c["outcome"] == "holds" for c in cases), "of": len(cases), "unit": unit,
                      "approval": p.get("approval"), "approval_changed": p.get("approval_changed") or []})
        if p.get("approval") == "changed":
            flags.append(f"changed since it was approved: {p['id']} ({approval.words(p.get('approval_changed') or [])})")
        elif p.get("approval") == "new":
            flags.append(f"not approved yet: {p['id']}")
        for c in cases:
            if c["outcome"] in ("holds", "planned"):
                continue
            reason = (c.get("question") if c["outcome"] == "declined" and c.get("question") else c.get("observed")) or ""
            not_held.append({"property": p["id"], "case": c["id"], "outcome": c["outcome"],
                             "outcome_words": OUTCOME_WORDS.get(c["outcome"], c["outcome"]),
                             "reason": re.sub(r"\s+", " ", str(reason)).strip()[:300], "is_question": c["outcome"] == "declined" and bool(c.get("question")),
                             "data_request": c.get("data_request")})
    try:
        pushed = json.loads((run_dir / "pushed.json").read_text())
    except (OSError, ValueError):
        pushed = {}
    walk = doc.get("walkthrough")
    walkthrough = None
    if isinstance(walk, dict):
        outcome = walk.get("outcome")
        walkthrough = {"outcome": outcome, "words": WALK_WORDS.get(outcome, "could not run"),
                       "reason": "" if outcome in WALK_WORDS else re.sub(r"\s+", " ", str(walk.get("observed") or "")).strip()[:300]}
    feature_url = None
    if isinstance(pushed, dict) and pushed.get("store"):
        feature_url = feature_page(pushed["store"], pushed.get("project") or project_name(repo), pushed.get("feature") or doc["feature"])
    return {"run": doc["run"], "feature": doc["feature"], "ran_at": doc.get("ran_at"), "error": doc.get("error"), "aborted": doc.get("aborted"),
            "walkthrough": walkthrough, "feature_page": feature_url, "not_checked": doc.get("not_checked") or [],
            "properties_by_verdict": {k: v for k, v in doc["totals"]["properties"].items() if v},
            "cases_held": doc["totals"]["cases"].get("holds", 0), "cases": sum(doc["totals"]["cases"].values()),
            "properties": props, "not_held": not_held, "approval_flags": flags,
            "dashboard": pushed.get("url") if isinstance(pushed, dict) else None, "folder": str(run_dir)}


def _takes(takes) -> str:
    if not isinstance(takes, dict) or not takes:
        return "nothing"
    out = []
    for k, v in takes.items():
        if isinstance(v, dict):
            bits = [str(v.get("type") or "")] + (["optional"] if v.get("optional") else [])
            out.append(f"{k} ({', '.join(b for b in bits if b)})" + (f": {v['does']}" if v.get("does") else ""))
        else:
            out.append(f"{k} ({v})")
    return "; ".join(out)


def summary_text(s: dict) -> str:
    counts = ", ".join(f"{n} {VERDICT_WORDS.get(v, v)}" for v, n in s["properties_by_verdict"].items()) or "no properties"
    when = (s.get("ran_at") or "").replace("T", " ")
    out = [f"Run {s['run']} of {s['feature']}" + (f", {when}" if when else "") + f": {counts}; {s['cases_held']} of {s['cases']} cases held."]
    if s.get("error"):
        out.append(f"The run did not finish: {s['error']}")
    elif s.get("aborted"):
        out.append("The run was stopped before it finished.")
    out.append("Properties:")
    for p in s["properties"]:
        name = p["title"] or p["statement"].strip().rstrip(".")
        line = f"  {name} ({p['id']}): {p['verdict_words']}"
        if p["verdict"] != "not_run":
            line += f", {p['held']} of {p['of']} {p['unit'] if p['of'] != 1 else p['unit'][:-1]} held"
        out.append(line + ".")
    if s["not_held"]:
        out.append("Did not hold:")
        for c in s["not_held"]:
            line = f"  {c['property']}/{c['case']}: {c['outcome_words']}."
            dr = c.get("data_request")
            if dr:
                tool = dr.get("tool") if isinstance(dr.get("tool"), dict) else {}
                state = str(dr.get("state") or "not given").strip().rstrip(".")
                line += (f" The starting state it needed: {state}. It asks for a data tool, {tool.get('name') or '(unnamed)'}: "
                         f"{str(tool.get('does') or '').strip() or 'no description.'} Takes: {_takes(tool.get('takes'))}.")
            elif c["reason"]:
                line += (" Question: " if c["is_question"] else " ") + c["reason"]
            out.append(line)
            if c["is_question"]:
                out.append(f"    To answer: groundtruth answer {c['property']} {c['case']} intended   (or: bug)")
    if s.get("not_checked"):
        out.append("Not checked by these claims:")
        out += [f"  {x}" for x in s["not_checked"]]
    if s["approval_flags"]:
        out.append("Approval:")
        out += [f"  {f}" for f in s["approval_flags"]]
    if s.get("walkthrough"):
        w = s["walkthrough"]
        out.append(f"Walkthrough: {w['words']}." + (f" {w['reason']}" if w.get("reason") else ""))
    if s.get("dashboard"):
        out.append(f"Dashboard: {s['dashboard']}")
    if s.get("feature_page"):
        out.append(f"Feature page: {s['feature_page']}")
    return "\n".join(out)


def cmd_summary(a):
    try:
        s = run_summary(Path(a.repo).resolve(), a.run)
    except SummaryError as e:
        sys.exit(str(e))
    print(json.dumps(s, indent=2) if a.json else summary_text(s))


PLACEHOLDER = "{{GROUNDTRUTH}}"


def _prune(folder: Path):
    """Remove a folder's empty subfolders and then the folder itself, when nothing else is in them."""
    for d in sorted((p for p in folder.rglob("*") if p.is_dir()), reverse=True) + [folder]:
        try:
            d.rmdir()
        except OSError:
            pass


RETIRED_SKILLS = ("groundtruth-feature", "groundtruth-verify")      # replaced by the one `groundtruth` skill
# Where each coding agent looks for a project's skills (the SKILL.md format is the same for all of them). GitHub
# Copilot also reads .claude/skills; .github/skills is its own place and works on versions that do not.
SKILL_HOMES = {"claude": ".claude/skills", "copilot": ".github/skills", "agents": ".agents/skills"}


def install_skills(project: Path, skills_dir: Path = None, launcher: Path = None, remove=False, say=print, agent="claude") -> bool:
    """Copy each skill's folder (SKILL.md and anything beside it, such as references/) into the project's skills folder
    for `agent` (<project>/.claude/skills/ unless told otherwise), with the launcher's path written into the text
    files. Skills from before the single `groundtruth` skill are taken out."""
    import shutil
    skills_dir, launcher = Path(skills_dir or SKILLS_DIR), Path(launcher or LAUNCHER)
    project = Path(project).resolve()
    if not project.is_dir():
        say(f"There is no folder at {project}.")
        return False
    found = sorted(p.parent for p in skills_dir.glob("*/SKILL.md") if p.is_file()) if skills_dir.is_dir() else []
    if not found:
        say(f"There are no skills to {'remove' if remove else 'install'}: {skills_dir} has no <name>/SKILL.md files yet.")
        return False
    if agent not in SKILL_HOMES:
        say(f"There is no skills folder known for \"{agent}\". Use one of: {', '.join(SKILL_HOMES)}.")
        return False
    target = project / SKILL_HOMES[agent]
    if remove:
        gone = 0
        for src in found:
            dest = target / src.name
            if (dest / "SKILL.md").is_file():
                for f in sorted((p for p in src.rglob("*") if p.is_file()), reverse=True):   # only what was installed
                    (dest / f.relative_to(src)).unlink(missing_ok=True)
                _prune(dest)
                gone += 1
                say(f"Removed {dest / 'SKILL.md'}")
        if not gone:
            say(f"Nothing to remove: none of the {len(found)} skill(s) are in {target}.")
        return True
    for old in RETIRED_SKILLS:
        if old not in {s.name for s in found} and (target / old / "SKILL.md").is_file():
            (target / old / "SKILL.md").unlink()
            _prune(target / old)
            say(f"Removed {target / old} (replaced by the groundtruth skill)")
    for src in found:
        for f in sorted(p for p in src.rglob("*") if p.is_file()):
            dest = target / src.name / f.relative_to(src)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if f.suffix == ".md":
                dest.write_text(f.read_text().replace(PLACEHOLDER, str(launcher)))
            else:
                shutil.copyfile(f, dest)
            say(f"Wrote {dest}")
    if agent != "claude":
        return True
    say(f"To let the agent run Groundtruth without asking each time, add this line to \"permissions\" > \"allow\" in "
        f"{project / '.claude' / 'settings.local.json'} (this command does not change that file):")
    say(f"  \"Bash({launcher} *)\"")
    return True


def cmd_install_skills(a):
    if not install_skills(Path(a.project), remove=a.remove, agent=a.agent):
        sys.exit(1)


# ---------------------------------------------------------------- the everyday commands: finding the app and the feature

def _git_root():
    out = _out(["git", "rev-parse", "--show-toplevel"])
    return Path(out) if out else None


def find_app(given=None, create=False) -> Path:
    """The app folder: the one given, else the nearest folder from here up that has .groundtruth/. For a command that
    may be the first (connect, draft), else the git repository's root, else this folder."""
    if given:
        return Path(given).resolve()
    here = Path.cwd().resolve()
    for folder in (here, *here.parents):
        if (folder / ".groundtruth").is_dir():
            return folder
    if create:
        return (_git_root() or here).resolve()
    sys.exit("This folder is not part of an app Groundtruth knows. Go to your app's folder and run: groundtruth connect <address>")


def find_feature(repo: Path, given=None) -> str:
    """The feature: the one given, else the app's only claims file. With several, list them and stop."""
    if given:
        return given
    names = sorted(p.stem for p in (repo / ".groundtruth" / "features").glob("*.yml"))
    if len(names) == 1:
        return names[0]
    if not names:
        sys.exit("This app has no claims yet. Draft some with: groundtruth draft")
    sys.exit("This app has more than one set of claims; say which: " + ", ".join(names))


def _is_folder(x: str) -> bool:
    return x in (".", "..") or "/" in x or os.sep in x or (Path(x).is_dir() and (Path(x) / ".groundtruth").is_dir())


def _resolve(a):
    """Fill a.repo and the command's other positionals from the words given, for the commands that may leave out the
    app folder (and the feature). An explicit folder still works: it is the first word when it names one."""
    words = list(getattr(a, "words", None) or [])
    kind = a.positionals
    if kind == "connect":
        folder = words.pop(0) if len(words) > 1 or (words and _is_folder(words[0]) and "://" not in words[0]) else None
        a.address = words[0] if words else None
        if not a.address:
            sys.exit("Say where the app is running: groundtruth connect http://localhost:3000")
        a.repo = str(find_app(folder, create=True))
        return
    if kind == "draft":
        a.repo = str(find_app(words[0] if words else None, create=True))
        return
    folder = None
    need = {"feature": 1, "feedback": 2, "answer": 3}[kind]
    if words and (len(words) > need or (_is_folder(words[0]) and len(words) >= 1 and kind != "feature") or
                  (kind == "feature" and _is_folder(words[0]))):
        folder = words.pop(0)
    repo = find_app(folder)
    a.repo = str(repo)
    if kind == "feature":
        a.feature = find_feature(repo, words[0] if words else getattr(a, "feature_flag", None))
    elif kind == "feedback":
        if not words:
            sys.exit("Say which claim: groundtruth feedback <claim> [variation] --kind ...")
        a.claim, a.variation = words[0], (words[1] if len(words) > 1 else None)
    elif kind == "answer":
        if len(words) != 3 or words[2] not in ("intended", "bug"):
            sys.exit("Say the claim, the variation and your answer: groundtruth answer <claim> <variation> intended|bug")
        a.property, a.case, a.answer, a.feature = words[0], words[1], words[2], getattr(a, "feature_flag", None)


# ---------------------------------------------------------------- login

LOCAL_NAMES = ("localhost", "127.0.0.1", "::1")


def is_local(host: str) -> bool:
    host = (host or "").lower().strip("[]")
    return host in LOCAL_NAMES or host.endswith(".localhost") or host.endswith(".test")


def cmd_login(a):
    """Save where the verifier and the store are, and the token for both. One address (a hosted Groundtruth) sets
    both: the store at the address and the verifier at <address>/verifier, which the store forwards."""
    import getpass
    import urllib.error
    import urllib.request
    from toolserver.push import open_no_redirect
    one = (a.address or "").strip().rstrip("/")
    if one and "://" not in one:
        one = f"https://{one}"
    service = service_address(a.service or (f"{one}/verifier" if one else None))
    store = store_address(a.store or one or None)
    token = a.token
    if token is None:
        token = getpass.getpass("Token (leave empty for a service on this machine): ") if sys.stdin.isatty() else sys.stdin.readline()
    token = (token or "").strip()
    if not token and not is_local(urlparse(service).hostname):
        sys.exit(f"{service} is not on this machine, so it needs a token. Give it with --token, or type it when asked.")
    req = urllib.request.Request(f"{service}/health", headers={"Authorization": f"Bearer {token}"} if token else {})
    try:
        with open_no_redirect(req, timeout=10) as r:
            ok = json.loads(r.read()).get("ok") is True
    except urllib.error.HTTPError as e:
        sys.exit(f"The verifier service at {service} did not accept that token ({e.code}). Nothing was saved.")
    except (urllib.error.URLError, OSError, ValueError) as e:
        sys.exit(f"Couldn't reach the verifier service at {service} ({getattr(e, 'reason', e)}). Nothing was saved.")
    if not ok:
        sys.exit(f"{service} answered, but not as a Groundtruth verifier service. Nothing was saved.")
    if token and not is_local(urlparse(store).hostname):      # a store elsewhere: check it takes the same token
        req = urllib.request.Request(f"{store}/v1/session", headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
        try:
            with open_no_redirect(req, timeout=10) as r:
                json.loads(r.read())
        except urllib.error.HTTPError as e:
            sys.exit(f"The results store at {store} did not accept that token ({e.code}). Nothing was saved.")
        except (urllib.error.URLError, OSError, ValueError) as e:
            sys.exit(f"Couldn't reach the results store at {store} ({getattr(e, 'reason', e)}). Nothing was saved.")
    f = config_path()
    f.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(f.parent, 0o700)
    except OSError:
        pass
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as out:
        out.write(json.dumps({"service": service, "store": store, "token": token}, indent=2) + "\n")
    os.chmod(f, 0o600)
    print(f"Logged in to the verifier at {service}. Results will be kept at {store}. Saved in {f}.")


# ---------------------------------------------------------------- connect

def cmd_connect(a):
    """The app you already started: where it answers, how to reset its data, its window size. No process lookup."""
    import urllib.error
    import urllib.request
    from toolserver import datatools
    from toolserver.runner import ServiceError, viewport_of
    repo = Path(a.repo).resolve()
    url = a.address if "://" in a.address else f"http://{a.address}"
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        sys.exit("Give the app's address, such as http://localhost:3000")
    if not is_local(u.hostname) and not a.not_local:
        sys.exit(f"{u.hostname} is not on this machine. The verifier clicks things, fills in forms and resets data, so it should "
                 "not be pointed at a site real people use. If this is a copy only you use, add --not-local.")
    try:
        urllib.request.urlopen(url, timeout=5).close()
    except urllib.error.HTTPError:
        pass                                   # it answered; any status means the app is up
    except Exception as e:  # noqa: BLE001
        sys.exit(f"Nothing answers at {url} ({getattr(e, 'reason', e)}). Start your app, then connect again.")
    env = {"url": url.rstrip("/"), "repo": str(repo)}
    if a.reset:
        env["reset"] = a.reset
    if a.window:
        try:
            env["viewport"] = viewport_of(a.window)
        except ServiceError as e:
            sys.exit(f"--window: {e}")
    if a.project:
        env["project"] = a.project
    facts = {"registered_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "platform": platform.platform()}
    commit = _out(["git", "rev-parse", "--short", "HEAD"], cwd=repo)
    if commit:
        facts["commit"] = commit
        facts["uncommitted_changes"] = bool(_out(["git", "status", "--porcelain"], cwd=repo))
    env["facts"] = facts
    if a.reset and not a.skip_reset_check:
        problem = try_reset(a.reset, str(repo))
        if problem:
            sys.exit(f"{problem}\nNothing was saved. Fix the command, or connect with --skip-reset-check to save it without trying it.")
    problems, tools, services = [], [], None
    try:
        services = datatools.load_services(repo)
    except datatools.DataToolsError as e:
        problems.append(str(e))
    try:
        tools = datatools.load(repo)
    except datatools.DataToolsError as e:
        problems.append(str(e))
    replaced = env_path(repo).exists()
    env_path(repo).parent.mkdir(parents=True, exist_ok=True)
    env_path(repo).write_text(json.dumps(env, indent=2))
    size = env.get("viewport") or {"width": 900, "height": 700}
    readers = sum(1 for t in tools if t.get("reads_only"))
    print(f"Connected to {env['url']} for {repo.name}" + (" (this replaces the earlier connection)." if replaced else "."))
    print(f"The verifier's browser window will be {size['width']} by {size['height']}.")
    if tools:
        print(f"{len(tools)} data tool{'s' if len(tools) != 1 else ''} declared, {readers} of them only read"
              f"{'s' if readers == 1 else ''}.")
    else:
        print("No data tools are declared (.groundtruth/data.yml), so cases start from whatever data the app has.")
    if a.reset:
        print(f"Reset command (tried once now{' ' if not a.skip_reset_check else ', skipped: '}"
              f"{'it worked' if not a.skip_reset_check else 'not tried'}): every case starts from clean data, "
              "and data tools that change data can be used.")
    else:
        print("No reset command: cases don't start from clean data, and only data tools that only read can be used.")
    if services:
        print("Outside services listed: " + ", ".join(f"{s['name']} ({s['allowed']})" for s in services) + ".")
    for p in problems:
        print(f"Problem: {p}")


# ---------------------------------------------------------------- draft

def _existing_claims(repo: Path) -> list:
    import yaml
    out = []
    for f in sorted((repo / ".groundtruth" / "features").glob("*.yml")):
        try:
            spec = yaml.safe_load(f.read_text()) or {}
        except Exception:  # noqa: BLE001
            continue
        for p in spec.get("properties") or [] if isinstance(spec, dict) else []:
            if isinstance(p, dict) and isinstance(p.get("id"), str):
                out.append({"id": p["id"], "title": str(p.get("title") or "")})
    return out


def draft_input(repo: Path, pr=None, issue=None, said_text=""):
    """What a draft is made from. -> (input for the service, words saying where it came from)"""
    from toolserver import context, datatools
    pull, issues = None, []
    if issue and not pr:
        one = context.fetch_issue(repo, issue)
        if one is None:
            sys.exit(f"Couldn't read issue {issue} with gh. Check that gh is installed and logged in, and that the issue exists.")
        issues = [one]
    else:
        got = context.fetch(repo, pr=pr)
        if got is None and pr:
            sys.exit(f"Couldn't read pull request {pr} with gh. Check that gh is installed and logged in, and that it exists.")
        if got:
            pull, issues = got["pull_request"], got["issues"]
        if issue:
            one = context.fetch_issue(repo, issue)
            if one and all(i["number"] != one["number"] for i in issues):
                issues.append(one)
    if not (pull or issues or said_text):
        sys.exit("Nothing to draft from: this branch has no pull request that gh can read, and no --pr, --issue or --said was given.")
    try:
        tools = datatools.for_service(datatools.load(repo))
        services = datatools.load_services(repo)
    except datatools.DataToolsError as e:
        sys.exit(f"{e} Fix the file, then draft again.")
    origin = ([f"pull request #{pull['number']}"] if pull else []) + [f"issue #{i['number']}" for i in issues] \
        + (["your own words"] if said_text else [])
    words = ", ".join(origin[:-1]) + (" and " if len(origin) > 1 else "") + origin[-1]
    return {"pull_request": pull, "issues": issues, "said": said_text, "existing": _existing_claims(repo),
            "data_tools": tools, "outside_services": services}, words


def cmd_draft(a):
    import urllib.error
    import urllib.request
    from toolserver import draftfile
    repo = Path(a.repo).resolve()
    said = ""
    if a.said:
        said = sys.stdin.read() if a.said == "-" else Path(a.said).read_text(errors="replace")
        said = said.strip()
        if len(said) > 8000:
            sys.exit(f"--said is {len(said)} characters long; at most 8,000 are taken. Keep the part about this change.")
    inp, origin = draft_input(repo, a.pr, a.issue, said)
    service = service_address(a.service)
    print(f"Drafting claims from {origin}...", flush=True)
    from toolserver.push import open_no_redirect
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    token = service_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    def ask(path, body=None):
        req = urllib.request.Request(f"{service}{path}", data=None if body is None else json.dumps(body).encode(), headers=headers,
                                     method="GET" if body is None else "POST")
        try:
            with open_no_redirect(req, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("message") or e.reason
            except Exception:  # noqa: BLE001
                msg = e.reason
            sys.exit(str(msg) if e.code in (401, 402, 403) else f"The verifier service could not draft claims: {msg}")
        except (urllib.error.URLError, OSError, ValueError) as e:
            sys.exit(f"Couldn't reach the verifier service at {service} ({getattr(e, 'reason', e)}). Start it with: groundtruth.py serve")

    # The service answers at once with the draft's id and drafts in the background; ask every few seconds, so no
    # request waits long (a hosting platform closes connections that stay silent).
    job = ask("/v1/drafts", {"input": inp}).get("draft")
    if not isinstance(job, str) or not re.fullmatch(r"\w{1,64}", job):
        sys.exit("The verifier service did not start a draft (it gave no draft id). Is it an older version?")
    deadline = time.monotonic() + DRAFT_LIMIT
    while True:
        got = ask(f"/v1/drafts/{job}")
        if got.get("state") == "done":
            break
        if got.get("state") == "failed":
            sys.exit(f"The verifier service could not draft claims: {got.get('message') or 'it did not say why'}")
        if time.monotonic() > deadline:
            sys.exit(f"The draft took longer than {DRAFT_LIMIT // 60} minutes, so it was given up. Try again, or draft from less text.")
        time.sleep(DRAFT_POLL)
    draft = got.get("draft") or {}
    feature = a.feature or draft.get("feature")
    if not feature or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", feature):
        sys.exit("The feature needs a short name: give one with --feature.")
    if not draft.get("claims"):
        print("The draft has no claims: the text gave nothing that using the app could check.")
        for q in draft.get("questions") or []:
            print(f"Question: {q['question']}")
        for x in draft.get("not_checked") or []:
            print(f"Not checkable here: {x}")
        print("Nothing was written.")
        return
    path, added, kept = draftfile.write(repo, feature, draft, origin)
    if added:
        print(f"Added {len(added)} claim{'s' if len(added) != 1 else ''} to {path.relative_to(repo)}: {', '.join(added)}.")
    if kept:
        print(f"Left alone, because the file already has them: {', '.join(kept)}.")
    print("")
    a.feature = feature
    cmd_claims(a)


# ---------------------------------------------------------------- results

def results(repo: Path, feature: str) -> dict:
    """Each claim's latest result across the app's runs of this feature (a run that did not run a claim is skipped)."""
    runs_dir = repo / ".groundtruth" / "runs"
    runs = sorted((p for p in runs_dir.iterdir() if (p / "run.json").is_file()), reverse=True) if runs_dir.is_dir() else []
    latest, order = {}, []
    for run_dir in runs:
        try:
            run = json.loads((run_dir / "run.json").read_text())
        except ValueError:
            continue
        if feature not in (run.get("feature_file"), run.get("feature")):
            continue
        s = run_summary(repo, run_dir.name)
        for p in s["properties"]:
            if p["id"] not in order:
                order.append(p["id"])
            if p["id"] not in latest and p["verdict"] != "not_run":
                latest[p["id"]] = {**p, "run": s["run"], "ran_at": s.get("ran_at"),
                                   "not_held": [c for c in s["not_held"] if c["property"] == p["id"]], "feature_page": s.get("feature_page")}
    if not order:
        raise SummaryError(f"There are no runs of {feature} yet. Run: groundtruth verify {feature}")
    return {"feature": feature, "claims": [latest.get(pid) or {"id": pid, "verdict": "not_run", "verdict_words": "not run yet"} for pid in order]}


def results_text(r: dict) -> str:
    out = [f"Latest results for {r['feature']}:"]
    for p in r["claims"]:
        name = p.get("title") or (p.get("statement") or "").strip().rstrip(".") or p["id"]
        if p["verdict"] == "not_run":
            out.append(f"  {name} ({p['id']}): not run yet.")
            continue
        out.append(f"  {name} ({p['id']}): {p['verdict_words']}, {p['held']} of {p['of']} held (run {p['run']}).")
        for c in p["not_held"]:
            out.append(f"    {c['case']}: {c['outcome_words']}." + (f" {c['reason']}" if c.get("reason") else ""))
            if c.get("is_question"):
                out.append(f"    To answer: groundtruth answer {p['id']} {c['case']} intended   (or: bug)")
    return "\n".join(out)


def cmd_results(a):
    try:
        r = results(Path(a.repo).resolve(), a.feature)
    except SummaryError as e:
        sys.exit(str(e))
    print(json.dumps(r, indent=2) if a.json else results_text(r))


def cmd_answer(a):
    cmd_decide(a)


EVERYDAY = ("login", "connect", "draft", "claims", "approve", "verify", "feedback")
OCCASIONAL = ("results", "answer")
ITSELF = ("register", "summary", "decide", "push", "report", "serve", "store", "users", "up", "status", "install-skills")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="groundtruth", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(required=True, metavar="command")

    def everyday(name, fn, positionals, help_text, words="[words ...]"):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("words", nargs="*", metavar=words)
        p.set_defaults(fn=fn, positionals=positionals)
        return p

    # the everyday commands, in the order --help lists them
    c = sub.add_parser("login", help="save where the verifier and the results store are")
    c.add_argument("address", nargs="?", help="one address for both (a hosted Groundtruth)")
    c.add_argument("--service"); c.add_argument("--store"); c.add_argument("--token"); c.set_defaults(fn=cmd_login)
    c = everyday("connect", cmd_connect, "connect", "the app you already started", "[folder] address")
    c.add_argument("--reset"); c.add_argument("--window"); c.add_argument("--project"); c.add_argument("--not-local", action="store_true")
    c.add_argument("--skip-reset-check", action="store_true")
    c = everyday("draft", cmd_draft, "draft", "draft claims from a pull request, an issue or your own words", "[folder]")
    c.add_argument("--pr"); c.add_argument("--issue"); c.add_argument("--said"); c.add_argument("--feature"); c.add_argument("--service")
    c.add_argument("--full", action="store_true")
    c = everyday("claims", cmd_claims, "feature", "the claims in brief (--full for everything)", "[folder] [feature]")
    c.add_argument("--full", action="store_true")
    everyday("approve", cmd_approve, "feature", "record that you approved the claims", "[folder] [feature]")
    v = everyday("verify", cmd_verify, "feature", "check the claims against the running app", "[folder] [feature]")
    v.add_argument("--only"); v.add_argument("--service"); v.add_argument("--exit-code", action="store_true"); v.add_argument("--plan-only", action="store_true")
    v.add_argument("--push", action="store_true"); v.add_argument("--no-push", action="store_true"); v.add_argument("--store"); v.add_argument("--project")
    v.add_argument("--viewport"); v.add_argument("--walkthrough", action="store_true"); v.add_argument("--walkthrough-only", action="store_true")
    v.add_argument("--no-context", action="store_true"); v.add_argument("--pr")
    f = everyday("feedback", cmd_feedback, "feedback", "what you thought of a claim's result", "[folder] claim [variation]")
    f.add_argument("--kind", required=True, choices=FEEDBACK_KINDS); f.add_argument("--note", default=""); f.add_argument("--run")
    f.add_argument("--store")
    # occasional
    c = everyday("results", cmd_results, "feature", "each claim's latest result", "[folder] [feature]")
    c.add_argument("--json", action="store_true")
    c = everyday("answer", cmd_answer, "answer", "answer a question a run asked", "[folder] claim variation intended|bug")
    c.add_argument("--feature", dest="feature_flag")
    # for running Groundtruth itself: the older names, unchanged
    r = sub.add_parser("register", help="(older) connect, with a process to measure"); r.add_argument("repo"); r.add_argument("--url", required=True); r.add_argument("--pid", type=int)
    r.add_argument("--reset"); r.add_argument("--routes"); r.add_argument("--project"); r.add_argument("--no-repo", action="store_true"); r.add_argument("--no-pid", action="store_true"); r.add_argument("--viewport"); r.set_defaults(fn=cmd_register)
    r.add_argument("--skip-reset-check", action="store_true")
    c = sub.add_parser("summary", help="(older) one run in plain text"); c.add_argument("repo"); c.add_argument("run", nargs="?"); c.add_argument("--json", action="store_true")
    c.set_defaults(fn=cmd_summary)
    d = sub.add_parser("decide", help="(older) answer"); d.add_argument("repo"); d.add_argument("feature"); d.add_argument("property"); d.add_argument("case")
    d.add_argument("answer", choices=["intended", "bug"]); d.set_defaults(fn=cmd_decide)
    pu = sub.add_parser("push", help="upload a finished run"); pu.add_argument("repo"); pu.add_argument("run", nargs="?"); pu.add_argument("--store")
    pu.add_argument("--project"); pu.set_defaults(fn=cmd_push)
    p = sub.add_parser("report", help="the HTML report of a run"); p.add_argument("repo"); p.add_argument("run", nargs="?"); p.set_defaults(fn=cmd_report)
    s = sub.add_parser("serve", help="the verifier service"); s.add_argument("--port", type=int, default=8790); s.add_argument("--model", default="claude-sonnet-5-5"); s.set_defaults(fn=cmd_serve)
    s.add_argument("--host"); s.add_argument("--data")
    st = sub.add_parser("store", help="the results store and the pages"); st.add_argument("--port", type=int, default=8900); st.add_argument("--data"); st.set_defaults(fn=cmd_store)
    st.add_argument("--host")
    u = sub.add_parser("users", help="who may use a hosted Groundtruth, and their spending limits")
    u.add_argument("action", choices=["add", "list", "limit", "remove", "token"]); u.add_argument("name", nargs="?")
    u.add_argument("value", nargs="?", metavar="DOLLARS|none"); u.add_argument("--limit"); u.add_argument("--data"); u.set_defaults(fn=cmd_users)
    for name, fn in (("up", cmd_up), ("status", cmd_status)):
        c = sub.add_parser(name, help="start the service and the store" if name == "up" else "whether they are running")
        c.add_argument("--service-port", type=int, default=SERVICE_PORT)
        c.add_argument("--store-port", type=int, default=STORE_PORT); c.set_defaults(fn=fn)
    c = sub.add_parser("install-skills", help="copy the agent skills into a project"); c.add_argument("project"); c.add_argument("--remove", action="store_true")
    c.add_argument("--agent", choices=sorted(SKILL_HOMES), default="claude", help="which coding agent's skills folder to use (default: claude)"); c.set_defaults(fn=cmd_install_skills)
    a = ap.parse_args(argv)
    sys.path.insert(0, str(HERE))
    if getattr(a, "positionals", None):
        _resolve(a)
    run_logged(a)


def run_logged(a):
    """Run one command, then add one line to <app>/.groundtruth/log.jsonl when it names an app folder that has one:
    the command, when it started, how long it took, whether it worked and the first line of its error. Never its
    arguments or output. Writing the log never fails the command."""
    started, t0, error = time.strftime("%Y-%m-%dT%H:%M:%S"), time.monotonic(), None
    try:
        a.fn(a)
    except SystemExit as e:
        if e.code not in (None, 0):
            error = str(e.code) if not isinstance(e.code, int) else f"exit {e.code}"
        raise
    except BaseException as e:
        error = f"{type(e).__name__}: {e}"
        raise
    finally:
        log_command(a, started, time.monotonic() - t0, error)


def log_command(a, started, seconds, error):
    try:
        repo = getattr(a, "repo", None)
        name = getattr(a.fn, "__name__", "").removeprefix("cmd_").replace("_", "-")
        folder = Path(repo).resolve() / ".groundtruth" if repo else None
        if not folder or not folder.is_dir():
            return
        line = {"command": name, "at": started, "seconds": round(seconds, 2), "ok": error is None,
                "error": (str(error).strip().splitlines() or [""])[0][:200] if error else None}
        with open(folder / "log.jsonl", "a") as f:
            f.write(json.dumps(line) + "\n")
    except Exception:  # noqa: BLE001 -- the log never fails a command
        pass


if __name__ == "__main__":
    main()
