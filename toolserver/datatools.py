"""Data tools: small commands a project declares so a case can start from the data it needs.

The project lists them in <repo>/.groundtruth/data.yml (see DATATOOLS.md). This module does two plain jobs:

  load(repo)        read that file and check every rule, or raise DataToolsError with a message naming the tool
  run_tool(...)     run one declared command, with the inputs as JSON on standard input and nowhere else

Deciding whether a call is allowed at all (which unit, how many calls, what came before) is the catalog's job.
"""
import json
import math
import os
import re
import signal
import subprocess
from pathlib import Path

import yaml

FILE = Path(".groundtruth") / "data.yml"
SERVICES_FILE = Path(".groundtruth") / "services.yml"
MAX_SERVICES = 12
ALLOWED = ("read", "stand-in only", "off")
MAX_CHANGES = 120
MAX_TOOLS = 20
MAX_STRING = 500          # characters in one string input
MAX_DOES = 400            # characters in one `does`: the planner is shown all of it, so it has to stay short
MAX_OUTPUT = 2000         # characters of standard output
TIME_LIMIT = 60           # seconds; read at call time, so a test can lower it
TAIL = 300                # characters of the command's output kept in an error

NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,39}")
TYPES = ("string", "integer", "number", "boolean")
TOOL_KEYS = ("name", "does", "takes", "run", "works_on", "changes")
SERVICE_KEYS = ("name", "what", "allowed")
INPUT_KEYS = ("type", "optional", "does")


class DataToolsError(Exception):
    """The declarations file breaks a rule. The message says which tool and what is wrong, in plain words."""


def _problem(where, what):
    return DataToolsError(f"{FILE}: {where} {what}")


def load(repo) -> list:
    """The declared tools, each {"name", "does", "takes", "run"}, in file order.

    `takes` maps each input name to {"type", "optional", "does"} (optional is always true or false, does is "" when
    not given). No file means no tools. An empty file also means no tools. Anything that breaks a rule raises
    DataToolsError. The file is read directly: the code-reading tools hide this folder, this does not."""
    f = Path(repo) / FILE
    if not f.exists():
        return []
    try:
        raw = f.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise DataToolsError(f"{FILE} could not be read ({e}).") from None
    try:
        doc = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        line = getattr(getattr(e, "problem_mark", None), "line", None)
        where = f" near line {line + 1}" if line is not None else ""
        raise DataToolsError(f"{FILE} is not valid YAML{where}. A sentence that contains a colon followed by a space "
                             "has to be in double quotes.") from None
    if doc is None:
        return []
    if not isinstance(doc, dict):
        raise DataToolsError(f"{FILE} must start with `tools:` followed by a list of tools.")
    extra = [k for k in doc if k != "tools"]
    if extra:
        raise DataToolsError(f"{FILE} has an unknown key {extra[0]!r} at the top. Only `tools` is allowed.")
    tools = doc.get("tools")
    if tools is None:
        return []
    if not isinstance(tools, list):
        raise DataToolsError(f"{FILE}: `tools` must be a list, each item starting with `- name:`.")
    if len(tools) > MAX_TOOLS:
        raise DataToolsError(f"{FILE} declares {len(tools)} tools. At most {MAX_TOOLS} are allowed.")
    services = load_services(repo)
    out, seen = [], set()
    for i, t in enumerate(tools, 1):
        tool = _tool(t, i, seen)
        if "works_on" in t:
            tool["works_on"] = _works_on(tool, t.get("works_on"), services)
        out.append(tool)
    return out


def _key(name) -> str:
    return re.sub(r"\s+", " ", str(name)).strip().casefold()


def load_services(repo):
    """The outside services the project lists in <repo>/.groundtruth/services.yml, each {"name", "what", "allowed"},
    in file order. None when there is no file (nothing changes then); [] when it lists none. Anything that breaks a
    rule raises DataToolsError in plain words."""
    f = Path(repo) / SERVICES_FILE
    if not f.exists():
        return None
    try:
        doc = yaml.safe_load(f.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as e:
        raise DataToolsError(f"{SERVICES_FILE} could not be read ({e}).") from None
    except yaml.YAMLError as e:
        line = getattr(getattr(e, "problem_mark", None), "line", None)
        where = f" near line {line + 1}" if line is not None else ""
        raise DataToolsError(f"{SERVICES_FILE} is not valid YAML{where}. A sentence that contains a colon followed by a space "
                             "has to be in double quotes.") from None
    if doc is None:
        return []
    if not isinstance(doc, dict) or [k for k in doc if k != "services"]:
        raise DataToolsError(f"{SERVICES_FILE} must start with `services:` followed by a list of services, and nothing else.")
    given = doc.get("services")
    if given is None:
        return []
    if not isinstance(given, list):
        raise DataToolsError(f"{SERVICES_FILE}: `services` must be a list, each item starting with `- name:`.")
    if len(given) > MAX_SERVICES:
        raise DataToolsError(f"{SERVICES_FILE} lists {len(given)} services. At most {MAX_SERVICES} are allowed.")
    out, seen = [], set()
    for i, sv in enumerate(given, 1):
        label = f"{SERVICES_FILE}: service number {i}"
        if not isinstance(sv, dict):
            raise DataToolsError(f"{label} is not a set of `name`, `what` and `allowed`.")
        extra = [k for k in sv if k not in SERVICE_KEYS]
        if extra:
            raise DataToolsError(f"{label} has an unknown key {extra[0]!r}. Only name, what and allowed are allowed.")
        name = sv.get("name")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40:
            raise DataToolsError(f"{label} has the name {name!r}. A name is 1 to 40 characters.")
        name = name.strip()
        label = f"{SERVICES_FILE}: service {name!r}"
        if _key(name) in seen:
            raise DataToolsError(f"{label} is listed twice. Each service is listed once.")
        seen.add(_key(name))
        what = sv.get("what")
        if not isinstance(what, str) or not what.strip() or len(what.strip()) > 200:
            raise DataToolsError(f"{label} needs `what`: one plain sentence, at most 200 characters, saying what it is and what calling it does.")
        allowed = sv.get("allowed")
        if allowed is False:            # YAML reads a bare `off` as false: that is the word the file wrote
            allowed = "off"
        if allowed not in ALLOWED:
            raise DataToolsError(f"{label} has `allowed: {allowed}`. It must be exactly read, stand-in only or off.")
        out.append({"name": name, "what": what.strip(), "allowed": allowed})
    return out


def _works_on(tool, given, services):
    """The service a tool works on, as the services file spells it, checked against what may be done with it."""
    label = f"tool {tool['name']!r}"
    if not isinstance(given, str) or not given.strip():
        raise _problem(label, "has a `works_on` that is not the name of a service.")
    if services is None:
        raise _problem(label, f"works on {given.strip()!r}, but there is no {SERVICES_FILE} listing the outside services.")
    sv = next((x for x in services if _key(x["name"]) == _key(given)), None)
    if sv is None:
        raise _problem(label, f"works on {given.strip()!r}, which is not a service listed in {SERVICES_FILE} "
                              f"(listed: {', '.join(x['name'] for x in services) or 'none'}).")
    if sv["allowed"] == "read" and not tool["reads_only"]:
        raise DataToolsError(f"{FILE}: {tool['name']} works on the {sv['name']}, which may only be read here: say \"changes: nothing\", or remove the tool")
    if sv["allowed"] in ("stand-in only", "off"):
        raise DataToolsError(f"{FILE}: {tool['name']} works on the {sv['name']}, which no tool may call here")
    return sv["name"]


def _tool(t, i, seen) -> dict:
    label = f"tool number {i}"
    if not isinstance(t, dict):
        raise _problem(label, "is not a set of `name`, `does`, `takes` and `run`.")
    name = t.get("name")
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise _problem(label, f"has the name {name!r}. A name is 1 to 40 letters, digits or underscores, "
                              "starting with a letter.")
    label = f"tool {name!r}"
    if name in seen:
        raise _problem(label, "is declared twice. Each name must be used once.")
    seen.add(name)
    extra = [k for k in t if k not in TOOL_KEYS]
    if extra:
        raise _problem(label, f"has an unknown key {extra[0]!r}. Only name, does, takes, run, works_on and changes are allowed.")
    does = t.get("does")
    if not isinstance(does, str) or not does.strip():
        raise _problem(label, "has no `does`. Add one plain sentence saying what the tool makes.")
    if len(does.strip()) > MAX_DOES:
        raise _problem(label, f"has a `does` of {len(does.strip())} characters. At most {MAX_DOES} are allowed, because the planner is shown all of it.")
    run = t.get("run")
    if not isinstance(run, str) or not run.strip():
        raise _problem(label, "has no `run`, or its `run` is not text. `run` is the shell command to run.")
    takes = t.get("takes")
    if takes is None:
        takes = {}
    if not isinstance(takes, dict):
        raise _problem(label, "has a `takes` that is not a set of inputs (each input name followed by its type).")
    inputs = {}
    for key, spec in takes.items():
        inputs[key] = _input(label, key, spec)
    changes = t.get("changes")
    if changes is not None and (not isinstance(changes, str) or not changes.strip() or len(changes.strip()) > MAX_CHANGES):
        raise _problem(label, f"has a `changes` that is not a short sentence (at most {MAX_CHANGES} characters). "
                              "Write `changes: nothing` for a tool that only reads.")
    reads_only = isinstance(changes, str) and changes.strip().casefold() == "nothing"
    return {"name": name, "does": does.strip(), "takes": inputs, "run": run, "reads_only": reads_only, "works_on": None}


def _input(label, key, spec) -> dict:
    if not isinstance(key, str) or not NAME.fullmatch(key):
        raise _problem(label, f"has an input named {key!r}. An input name is 1 to 40 letters, digits or "
                              "underscores, starting with a letter.")
    where = f"{label}, input {key!r},"
    if not isinstance(spec, dict):
        raise _problem(where, "must be written like {type: string}.")
    extra = [k for k in spec if k not in INPUT_KEYS]
    if extra:
        raise _problem(where, f"has an unknown key {extra[0]!r}. Only type, optional and does are allowed.")
    typ = spec.get("type")
    if typ not in TYPES:
        raise _problem(where, f"has the type {typ!r}. The type must be string, integer, number or boolean.")
    optional = spec.get("optional", False)
    if not isinstance(optional, bool):
        raise _problem(where, "has an `optional` that is not true or false.")
    does = spec.get("does", "")
    if does is None:
        does = ""
    if not isinstance(does, str):
        raise _problem(where, "has a `does` that is not text.")
    if len(does.strip()) > MAX_DOES:
        raise _problem(where, f"has a `does` of {len(does.strip())} characters. At most {MAX_DOES} are allowed, because the planner is shown all of it.")
    return {"type": typ, "optional": optional, "does": does.strip()}


def public(tools: list) -> list:
    """The tools as the catalog's data_tools lists them: never the command."""
    return [{"name": t["name"], "does": t["does"], "takes": t["takes"]} for t in tools]


def for_service(tools: list) -> list:
    """The tools as the run's env tells the verifier service: never the command, and whether each only reads and
    which outside service it works on (null for the app's own data)."""
    return [{**p, "reads_only": bool(t.get("reads_only")), "works_on": t.get("works_on")} for p, t in zip(public(tools), tools)]


def check_input(tool: dict, given: dict) -> None:
    """Raise ValueError, in plain words, when the inputs do not match what the tool takes."""
    takes = tool["takes"]
    for key in given:
        if key not in takes:
            allowed = ", ".join(takes) or "nothing"
            raise ValueError(f"{tool['name']} has no input named {key!r} (it takes: {allowed})")
    for key, spec in takes.items():
        if key not in given:
            if spec["optional"]:
                continue
            raise ValueError(f"{tool['name']} needs the input {key!r}")
        v, typ = given[key], spec["type"]
        if typ == "string":
            ok = isinstance(v, str)
        elif typ == "integer":
            ok = isinstance(v, int) and not isinstance(v, bool)
        elif typ == "number":
            ok = isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
        else:
            ok = isinstance(v, bool)
        if not ok:
            raise ValueError(f"the input {key!r} of {tool['name']} must be {'an' if typ == 'integer' else 'a'} {typ}, got {type(v).__name__} {v!r:.40}")
        if typ == "string" and len(v) > MAX_STRING:
            raise ValueError(f"the input {key!r} of {tool['name']} is {len(v)} characters long; "
                             f"at most {MAX_STRING} are allowed")


def _no_constants(word):
    raise ValueError(f"{word} is not allowed in JSON here")


def run_tool(tool: dict, given: dict, repo: Path, app_url: str) -> dict:
    """Run one declared command and return the JSON object it printed.

    The command runs through the shell in the repo folder, the same way the reset command does. The inputs go to it
    as one JSON object on standard input and nowhere else: never on the command line, never in the environment.
    It gets GROUNDTRUTH_APP_URL. It must finish within TIME_LIMIT seconds, exit 0 and print one JSON object of at
    most MAX_OUTPUT characters. Anything else raises RuntimeError with the last TAIL characters of its output."""
    limit = TIME_LIMIT
    env = {**os.environ, "GROUNDTRUTH_APP_URL": app_url}
    payload = json.dumps(given).encode()
    # Its own process group, so that on a timeout every process the command started is stopped, not only the shell.
    p = subprocess.Popen(tool["run"], shell=True, cwd=str(repo), env=env, stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        out, err = p.communicate(payload, timeout=limit)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        out, err = p.communicate()
        raise RuntimeError(f"{tool['name']} did not finish within {limit} seconds and was stopped. "
                           f"Its output ended with: {_tail(out, err)}") from None
    finally:
        if p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
            p.wait()
    text = out.decode("utf-8", errors="replace").strip()
    if p.returncode != 0:
        raise RuntimeError(f"{tool['name']} failed (exit {p.returncode}). Its output ended with: {_tail(out, err)}")
    if len(text) > MAX_OUTPUT:
        raise RuntimeError(f"{tool['name']} printed {len(text)} characters; at most {MAX_OUTPUT} are allowed. "
                           f"Its output ended with: {_tail(out, err)}")
    try:
        obj = json.loads(text, parse_constant=_no_constants)
    except ValueError:
        obj = None
    if not isinstance(obj, dict):
        raise RuntimeError(f"{tool['name']} must print one JSON object (like {{\"id\": 12}}) and nothing else. "
                           f"Its output ended with: {_tail(out, err)}")
    return obj


def _tail(out: bytes, err: bytes) -> str:
    both = ((out or b"") + (err or b"")).decode("utf-8", errors="replace")
    return repr(both[-TAIL:]) if both else "(nothing)"
