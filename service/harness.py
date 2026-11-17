"""The verifier harness: the loop around the model. Hosted.

One run = a list of properties about one registered app. A property is one plain sentence: a general rule. The
harness never sees the repo or the process. It gets the properties and a description of what the user's tool
server can do, sends tool calls down as actions, and gets results back. Each property is handled in two jobs:

  discovery   finds every way the property can be triggered and plans the cases. It gives no verdict. When the
              code can be read, the finding is done by plain code (service/trace.py): one conversation only says
              what to search for, every search hit gets a label from its own short model call, and a second
              conversation plans cases for the ways in the search produced, which it cannot add to or drop.
              Properties about the code itself, and apps whose code cannot be read, use one conversation that
              plans the cases on its own.
  cases       one conversation per case. It gets the property and the case, never the code (unless the property
              is about the code itself) and never an expected value, and reports what it observed.

The harness, not the model, enforces the rules:
  - a plan must cover every entry point it lists through the pages, stay within the case limit, and (when code can
    be read) list the code locations its trace rests on. Discovery has read the code, so it decides nothing about
    scope or outcome: it only says what to try;
  - a browser case has no way to call the API, and an API case has no browser: the way in is fixed by the plan;
  - a case result is accepted only if it matches what the conversation actually did;
  - for a property about how the app runs, the harness itself measures the process before the case touches the app;
  - the verdict comes from the table of cases, each judged by a conversation that never saw the code:
      any case that broke                      -> fails        (nothing can soften this)
      else any case the app openly declined    -> needs_decision, with that case's question for the owner
      else any planned case that was not run   -> cant_tell
      else at least one case held              -> holds        (cases outside the property's wording don't count)
      else                                     -> cant_tell    (nothing that was planned fell under the property)
  - properties that measure the process have their cases run first.

The model is reached through `claude -p` (the only authenticated path on this machine).
"""
import json
import os
import queue
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from . import prompts
from .trace import PAGE_REF, PAGE_REF_PROBLEM, TraceError, Tracer, anchor_problems, build_plan, cases_problems

HERE = Path(__file__).resolve().parent
VERSION = "3"
CLAUDE = os.environ.get("GT_CLAUDE", str(Path.home() / ".local/bin/claude"))

BROWSER = ["observe", "navigate", "click", "fill", "read_attribute", "click_and_watch", "wait", "screenshot", "media"]
MEASURE = ["process_info", "gpu_check", "cpu_sample", "net_connections"]
SOURCE = ["list_files", "find", "grep", "read_lines"]
CLAIM_BROWSER = ["hover", "look"]       # claims path only; they count as using the browser way in (compare never does)
KINDS, DOORS = ("behaviour", "measurement", "code"), ("browser", "api", "code")
OUTCOMES = ("holds", "breaks", "declined", "outside", "nothing_there", "cant_run")
ACTIONS = ("click", "fill", "click_and_watch", "http_request", "navigate")
MAX_PROPERTIES, MAX_CASES, CALL_TIMEOUT = 15, 12, 240
MAX_SETTING = 160
SAY_REFUSED = "say it in one or two short sentences, at most 110 characters; call say again for the next thought"
ID = re.compile(r"[A-Za-z0-9_-]{1,32}")


def discovery_tools(env):
    """Discovery looks; it never acts on the app's data."""
    return (SOURCE if env.get("has_repo") else []) + ["list_routes", "observe", "navigate"]


def case_tools(kind, door, env, from_claim=False):
    """The way in is fixed by the plan: a browser case cannot call the API, and an API case has no browser.
    Only a property about the code itself gets to read source when its cases are judged. A case planned from the
    owner's approved variations also records its comparisons (compare) and, through the pages, can point without
    clicking (hover) and look at a picture (look)."""
    if kind == "code":
        return SOURCE + ["match_each"]
    tools = BROWSER if door == "browser" else ["http_request"]
    tools = tools + (MEASURE if kind == "measurement" and env.get("has_pid") else [])
    if from_claim:
        tools = tools + (CLAIM_BROWSER if door == "browser" else []) + ["compare"]
    return tools


def validate_input(inp):
    props, env = inp.get("properties"), inp.get("env")
    walk = clean_walkthrough(inp.get("walkthrough"))
    if not isinstance(props, list) or not (0 if walk else 1) <= len(props) <= MAX_PROPERTIES:
        raise ValueError(f"a run needs 1 to {MAX_PROPERTIES} properties")
    out, ids = [], set()
    for p in props:
        ok = isinstance(p, dict) and isinstance(p.get("id"), str) and ID.fullmatch(p["id"]) and isinstance(p.get("statement"), str) and p["statement"].strip()
        if not ok or p["id"] in ids:
            raise ValueError("each property needs a unique id and a statement")
        if p["id"] == WALK:
            raise ValueError(f"no property may have the id {WALK}: it is kept for the feature's walkthrough")
        ids.add(p["id"])
        decisions = [{"question": str(d.get("question", "")), "answer": d["answer"]} for d in p.get("decisions") or []
                     if isinstance(d, dict) and d.get("answer") in ("intended", "bug")]
        out.append({"id": p["id"], "statement": p["statement"].strip(), "example": str(p.get("example") or ""), "decisions": decisions,
                    **claim_parts(p)})
    if not isinstance(env, dict) or not isinstance(env.get("url"), str):
        raise ValueError("env.url is required")
    services = clean_services(env.get("outside_services"))
    return {"feature": str(inp.get("feature", "")), "goal": str(inp.get("goal", "")), "properties": out,
            "plan_only": bool(inp.get("plan_only")), "walkthrough": walk, "feature_claims": clean_feature_claims(inp.get("feature_claims")),
            "env": {"url": env["url"], **{k: bool(env.get(k)) for k in ("has_pid", "has_repo", "has_reset", "has_routes")},
                    **dict(zip(("data_tools", "data_tools_blocked"), data_tools_env(env.get("data_tools"), env.get("has_reset")))),
                    **({"outside_services": services} if services else {})}}


WALK = "walkthrough"
TOUR = {"id": "tour", "what": "A person using the feature start to finish, on data that looks like ordinary use."}
MAX_CLAIMS = 20


def clean_walkthrough(w):
    """The run's optional walkthrough: the feature's goal and every claim of the feature that has variations. None
    when none was asked for."""
    if w is None:
        return None
    if not isinstance(w, dict) or not isinstance(w.get("goal", ""), str) or len(w.get("goal") or "") > 600:
        raise ValueError("walkthrough is {\"goal\": <at most 600 characters>, \"claims\": [...]}")
    claims = w.get("claims")
    if not isinstance(claims, list) or not 1 <= len(claims) <= MAX_CLAIMS:
        raise ValueError(f"a walkthrough needs 1 to {MAX_CLAIMS} claims")
    out, seen = [], set()
    for c in claims:
        ok = (isinstance(c, dict) and isinstance(c.get("id"), str) and ID.fullmatch(c["id"]) and c["id"] != WALK
              and isinstance(c.get("statement"), str) and c["statement"].strip()
              and all(isinstance(c.get(k) or "", str) for k in ("title", "checked_against")))
        if not ok or c["id"] in seen:
            raise ValueError("each walkthrough claim needs a unique short id and a statement")
        seen.add(c["id"])
        out.append({"id": c["id"], "title": (c.get("title") or "").strip()[:60], "statement": c["statement"].strip()[:2000],
                    "checked_against": (c.get("checked_against") or "").strip()[:600]})
    return {"goal": (w.get("goal") or "").strip(), "claims": out}


MAX_FEATURE_CLAIMS = 40


def clean_feature_claims(given):
    """Every claim of the feature (id, title, statement), so a claims-path planner and case know how the app is meant
    to work beyond the one claim being checked. [] when none was sent."""
    if given is None:
        return []
    if not isinstance(given, list) or len(given) > MAX_FEATURE_CLAIMS:
        raise ValueError(f"feature_claims is a list of at most {MAX_FEATURE_CLAIMS}")
    out = []
    for c in given:
        if not (isinstance(c, dict) and isinstance(c.get("id"), str) and ID.fullmatch(c["id"]) and isinstance(c.get("statement"), str)
                and c["statement"].strip() and isinstance(c.get("title") or "", str)):
            raise ValueError("each of feature_claims needs a short id and a statement")
        out.append({"id": c["id"], "title": (c.get("title") or "").strip()[:60], "statement": c["statement"].strip()[:2000]})
    return out


def tour_problems(plan, ids):
    """Why a walkthrough's plan can't be accepted: its one case goes through the pages and names at least one claim
    of the feature in brackets, at the start of a stop."""
    tour = next((c for c in plan.get("cases") or [] if isinstance(c, dict) and c.get("id") == TOUR["id"]), None)
    if tour is None:
        return None                                  # the variations check says what is missing
    doors = {e.get("id"): e.get("door") for e in plan.get("entry_points") or [] if isinstance(e, dict)}
    if doors.get(tour.get("entry_point")) != "browser":
        return "the walkthrough is filmed through the pages: its case's entry point is a browser one"
    named = [i for i in stops(tour.get("what"), ids)]
    if not named:
        return (f'the walkthrough\'s "what" names no claim: start each stop with the claim\'s id in brackets, for example '
                f'"[{ids[0]}] ...". The ids: {", ".join(ids)}')
    return None


def stops(what, ids):
    """The claim ids written in brackets in a walkthrough's steps, in order, each once."""
    out = []
    for i in re.findall(r"\[([A-Za-z0-9_-]{1,32})\]", str(what or "")):
        if i in ids and i not in out:
            out.append(i)
    return out


def walk_problems(conv, outcome):
    """For a walkthrough whose outcome is holds or breaks: every stop it was planned with has a comparison."""
    walk = conv.get("walk") or {}
    done = {c.get("claim") for c in conv.get("compares") or []}
    missing = [i for i in stops(walk.get("what"), walk.get("ids") or []) if i not in done]
    if outcome in ("holds", "breaks") and missing:
        return (f"no comparison yet for: {', '.join(missing)}. Do those stops, or record a comparison for each that says in "
                '"found" why it could not be done (matches false), then submit')
    return None


def claim_parts(p):
    """The optional parts of a claim written before any code existed: where the right answer comes from, and the
    variations the owner approved. A property that carries variations is planned from them, never from the code."""
    out = {"checked_against": str(p.get("checked_against") or "").strip()[:600], "variations": []}
    given = p.get("variations")
    if given is None:
        return out
    if not isinstance(given, list) or not 1 <= len(given) <= MAX_CASES:
        raise ValueError(f"property {p['id']}: variations is a list of 1 to {MAX_CASES}")
    seen = set()
    for v in given:
        ok = (isinstance(v, dict) and isinstance(v.get("id"), str) and ID.fullmatch(v["id"]) and v["id"] != "discovery"
              and isinstance(v.get("what"), str) and v["what"].strip())
        if not ok or v["id"] in seen:
            raise ValueError(f"property {p['id']}: each variation needs a unique short id and a sentence in \"what\"")
        seen.add(v["id"])
        out["variations"].append({"id": v["id"], "what": v["what"].strip()[:400]})
    return out


def variation_problems(plan, variations):
    """Why a plan made from a claim can't be accepted: it must have exactly one case per approved variation, with the
    variation's id, so the results read one row per variation the owner approved."""
    if plan.get("kind") == "code":
        return "this claim was written before the code: it is tested through the running app, so kind is behaviour or measurement"
    want = [v["id"] for v in variations]
    have = [c.get("id") for c in plan.get("cases") or [] if isinstance(c, dict)]
    missing = [i for i in want if i not in have]
    if missing:
        return f"no case for variation {missing[0]}: plan exactly one case for each approved variation, with the variation's id as the case id"
    extra = [i for i in have if i not in want]
    if extra:
        return (f"case {extra[0]} is not one of the approved variations ({', '.join(want)}): on this path you plan exactly those "
                "and no others; put anything else you would have tested in not_covered")
    return None


def setting_problems(plan):
    """Why a plan made from a claim can't be accepted for want of each case's setting: one plain sentence, for a person
    about to watch the case's film, saying what it starts from."""
    for c in plan.get("cases") or []:
        if not isinstance(c, dict):
            continue
        s = c.get("setting")
        if not (isinstance(s, str) and s.strip()):
            return (f'case {c.get("id")} needs "setting": one plain sentence, at most {MAX_SETTING} characters, saying what the '
                    "case starts from, for a person about to watch its film")
        if len(s.strip()) > MAX_SETTING:
            return f'case {c.get("id")}: "setting" is longer than {MAX_SETTING} characters; say what the case starts from in one short sentence'
    return None


def _same_what(what):
    return re.sub(r"\s+", " ", str(what or "")).strip().casefold()


def compare_problems(compares, outcome):
    """The acceptance rule for a case planned from a claim whose outcome is holds or breaks: the outcome has to agree
    with the comparisons recorded, taking the latest one under each distinct "what"."""
    latest = {}
    for c in compares:
        latest[_same_what(c.get("what"))] = c
    if not latest:
        return "record each comparison this outcome rests on with compare, at the moment you make it, then submit again"
    failed = [c for c in latest.values() if c.get("matches") is False]
    if outcome == "holds" and failed:
        return (f'you recorded a comparison that did not match ("{failed[0].get("what")}"). If the property is not true here, the '
                "outcome is breaks. If you misread, record that comparison again as it really is, then submit")
    if outcome == "breaks" and not failed:
        return 'a "breaks" outcome needs the comparison that did not match: record it with compare (matches false), then submit'
    return None


def _text(d, key):
    return isinstance(d.get(key), str) and bool(d[key].strip())


# ---- starting state (DATATOOLS.md): data tools the project declared, run by plain code before a case starts
SETUP_TYPES = ("string", "integer", "number", "boolean")
MAX_DOES = 400          # the same limit the tool server holds data.yml to, so nothing a tool says is cut short
MAX_SETUP_STEPS = 6


def clean_tools(tools):
    """The declared data tools as the tool server described them, cut down to what the planner needs."""
    out = []
    for t in tools if isinstance(tools, list) else []:
        if not (isinstance(t, dict) and isinstance(t.get("name"), str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,39}", t["name"])):
            continue
        takes = {}
        for k, v in (t.get("takes") or {}).items() if isinstance(t.get("takes"), dict) else []:
            if isinstance(k, str) and isinstance(v, dict) and v.get("type") in SETUP_TYPES:
                takes[k] = {"type": v["type"], "optional": bool(v.get("optional")), "does": str(v.get("does") or "")[:MAX_DOES]}
        out.append({"name": t["name"], "does": str(t.get("does") or "")[:MAX_DOES], "takes": takes,
                    "reads_only": t.get("reads_only") is True,
                    "works_on": str(t["works_on"])[:40] if isinstance(t.get("works_on"), str) else None})
    return out[:20]


OWN_DATA = "the app's own data"
SERVICE_RULES = ("read", "stand-in only", "off")


def clean_services(given):
    """The outside services the project lists (services.yml), as the tool server described them. None when it lists
    none: then nothing about them is said or checked anywhere."""
    if given is None:
        return None
    if not isinstance(given, list) or len(given) > 12:
        raise ValueError("env.outside_services is a list of at most 12 services")
    out = []
    for sv in given:
        if not (isinstance(sv, dict) and isinstance(sv.get("name"), str) and 1 <= len(sv["name"].strip()) <= 40
                and isinstance(sv.get("what"), str) and sv["what"].strip() and sv.get("allowed") in SERVICE_RULES):
            raise ValueError('each outside service needs "name", "what" and "allowed" (read, stand-in only or off)')
        out.append({"name": sv["name"].strip(), "what": sv["what"].strip()[:200], "allowed": sv["allowed"]})
    return out or None


def data_tools_env(tools, has_reset):
    """What the planner may use, and whether tools that change data are blocked: with no reset command registered,
    only the tools that only read can be used."""
    tools = clean_tools(tools)
    if has_reset:
        return tools, False
    return [t for t in tools if t["reads_only"]], any(not t["reads_only"] for t in tools)


def _fits(value, kind):
    if kind == "string":
        return isinstance(value, str) and len(value) <= 500
    if kind == "boolean":
        return isinstance(value, bool)
    if isinstance(value, bool):
        return False
    return isinstance(value, int) if kind == "integer" else isinstance(value, (int, float))


def setup_problems(case, env):
    """Why a planned case's starting state can't be accepted; None if it can. A case gives at most one of
    starts_from (steps with declared data tools) and needs (a request for a tool that does not exist)."""
    cid, steps, needs = case.get("id"), case.get("starts_from"), case.get("needs")
    if steps in (None, []) and needs in (None, {}):
        return None
    if steps and needs:
        return f'case {cid} has both "starts_from" and "needs"; give one: steps with declared tools, or a request for a missing tool'
    tools = {t["name"]: t for t in (env or {}).get("data_tools") or []}
    if needs:
        tool = needs.get("tool") if isinstance(needs, dict) else None
        if not (isinstance(needs, dict) and _text(needs, "state") and isinstance(tool, dict) and _text(tool, "name") and _text(tool, "does")):
            return f'case {cid}: "needs" is {{"state": "<the starting state in plain words>", "tool": {{"name", "does", "takes"}}}}'
        if tool["name"] in tools:
            return f'case {cid} asks for a tool named {tool["name"]}, which is already declared: use it in "starts_from"'
        if "works_on" in tool:
            allowed = [sv["name"] for sv in (env or {}).get("outside_services") or []] + [OWN_DATA]
            if tool["works_on"] not in allowed:
                return (f'case {cid}: the tool it needs has "works_on": {json.dumps(tool["works_on"])}. It must be exactly one of: '
                        + ", ".join(json.dumps(a) for a in allowed))
        if "changes" in tool and not isinstance(tool["changes"], bool):
            return f'case {cid}: the tool it needs has a "changes" that is not true or false'
        return None
    if not isinstance(steps, list) or len(steps) > MAX_SETUP_STEPS:
        return f'case {cid}: "starts_from" is a list of at most {MAX_SETUP_STEPS} steps'
    blocked_note = " (data tools that change data cannot be used here, because no reset command is registered)" \
        if (env or {}).get("data_tools_blocked") else ""
    if not tools:
        return (f"case {cid} uses data tools, but none can be used here"
                + (" (no reset command is registered)" if (env or {}).get("data_tools_blocked") else "")
                + ': say what the case needs in "needs" instead')
    for i, s in enumerate(steps, 1):
        if not (isinstance(s, dict) and isinstance(s.get("tool"), str)):
            return f'case {cid}, starting step {i}: each step is {{"tool", "input", "because"}}'
        t = tools.get(s["tool"])
        if not t:
            return (f"case {cid}, starting step {i}: no data tool named {s['tool']} is declared (declared: {', '.join(tools)})"
                    f"{blocked_note}. " + 'If none of them can make the state, use "needs"')
        given = s.get("input") if s.get("input") is not None else {}
        if not isinstance(given, dict):
            return f"case {cid}, starting step {i}: input is an object"
        for name, spec in t["takes"].items():
            if name not in given:
                if not spec["optional"]:
                    return f"case {cid}, starting step {i}: {s['tool']} needs the input {name}"
            elif not _fits(given[name], spec["type"]):
                return f"case {cid}, starting step {i}: the input {name} of {s['tool']} must be a {spec['type']}"
        extra = [k for k in given if k not in t["takes"]]
        if extra:
            return f"case {cid}, starting step {i}: {s['tool']} takes no input named {extra[0]}"
        if not _text(s, "because"):
            return (f'case {cid}, starting step {i} needs "because": one sentence saying why the pages cannot make this '
                    "state in a few steps. If they can, drop the step and have the case do it through the pages")
    return None


def clean_setup(case):
    """A planned case's starting state, reduced to the agreed keys."""
    out = {}
    if isinstance(case.get("starts_from"), list) and case["starts_from"]:
        out["starts_from"] = [{"tool": s["tool"], "input": s.get("input") or {}, "because": s["because"].strip()} for s in case["starts_from"]]
    elif isinstance(case.get("needs"), dict) and case["needs"]:
        tool = case["needs"]["tool"]
        out["needs"] = {"state": case["needs"]["state"].strip(),
                        "tool": {"name": str(tool["name"]).strip()[:60], "does": str(tool["does"]).strip()[:300],
                                 "takes": tool.get("takes") if isinstance(tool.get("takes"), dict) else {},
                                 **({"works_on": tool["works_on"]} if "works_on" in tool else {}),
                                 **({"changes": tool["changes"]} if "changes" in tool else {})}}
    return out


def outside_service_problem(case, env):
    """Why a case can't be run because it would need an outside service changed or called for real, in plain words;
    None otherwise. Decided by plain code before anything else in the case."""
    tool = (case.get("needs") or {}).get("tool") or {}
    works_on = tool.get("works_on")
    sv = next((x for x in (env or {}).get("outside_services") or [] if x["name"] == works_on), None)
    if sv is None:
        return None
    if sv["allowed"] == "read":
        if tool.get("changes", True) is False:
            return None
        return (f"This case was not run: it needs the {sv['name']} to be in a state it is not in today. The {sv['name']} can only be "
                "read here, so that would take a stand-in for it, and this project has none.")
    return (f"This case was not run: it depends on the {sv['name']}, which is never called for real here. That would take a "
            "stand-in for it, and this project has none.")


def run_setup(case, call):
    """Carry out a case's starting steps, in order, through the tool server. -> (steps as carried out, problem or None).
    `call(tool, args)` is the harness's call to the tool server. The first step that fails stops the rest."""
    done = []
    for i, s in enumerate(case.get("starts_from") or [], 1):
        res = call("set_up", {"tool": s["tool"], "input": s.get("input") or {}})
        if res.get("isError"):
            done.append({**s, "error": str(res.get("text"))[:400]})
            return done, f"its starting state could not be set up: step {i} ({s['tool']}) failed: {str(res.get('text'))[:300]}"
        done.append({**s, "returned": res.get("data")})
    return done, None


def names_a_page_ref(text):
    return bool(PAGE_REF.search(text or ""))




def plan_problems(plan, env=None):
    """Why a submitted plan can't be accepted, as a message the model can act on; None if it can."""
    for key in ("applies_to", "action", "compares", "how_found"):
        if not _text(plan, key):
            return f"{key} is required"
    if not isinstance(plan.get("not_covered"), str):
        return "not_covered is required (say what you left out, or that nothing was)"
    if plan.get("kind") not in KINDS:
        return "kind must be behaviour, measurement or code"
    eps, cases = plan.get("entry_points"), plan.get("cases")
    if not isinstance(eps, list) or not eps:
        return "list at least one entry point"
    ep_ids = {}
    for e in eps:
        if not (isinstance(e, dict) and isinstance(e.get("id"), str) and ID.fullmatch(e["id"]) and e.get("door") in DOORS and _text(e, "where")):
            return 'each entry point needs "id", "door" (browser, api or code) and "where"'
        if e["id"] in ep_ids:
            return f"entry point id {e['id']} is used twice"
        if names_a_page_ref(e["where"]):
            return PAGE_REF_PROBLEM.format(what=f"entry point {e['id']}")
        ep_ids[e["id"]] = e["door"]
    if (plan["kind"] == "code") != all(d == "code" for d in ep_ids.values()) or (plan["kind"] != "code" and "code" in ep_ids.values()):
        return 'a property of kind code has only "code" entry points, and other kinds have none'
    if not isinstance(cases, list) or not 1 <= len(cases) <= MAX_CASES:
        return f"plan 1 to {MAX_CASES} cases; group situations that go down the same path and test representatives"
    seen = set()
    for c in cases:
        if not (isinstance(c, dict) and isinstance(c.get("id"), str) and ID.fullmatch(c["id"]) and c["id"] != "discovery"):
            return 'each case needs a short "id"'
        if c["id"] in seen:
            return f"case id {c['id']} is used twice"
        seen.add(c["id"])
        if c.get("entry_point") not in ep_ids:
            return f"case {c['id']} names an entry point that is not in entry_points"
        if not _text(c, "what"):
            return f'case {c["id"]} needs "what": the steps in plain words'
        if names_a_page_ref(c["what"]):
            return PAGE_REF_PROBLEM.format(what=f"case {c['id']}")
    for eid, door in ep_ids.items():
        if door == "browser" and not any(c["entry_point"] == eid for c in cases):
            return f"entry point {eid} is a way in through the pages but has no case; every such entry point is tested"
    refs = [r for r in plan.get("code_refs") or [] if isinstance(r, dict) and isinstance(r.get("path"), str) and r["path"].strip()]
    if (env or {}).get("has_repo") and not refs:
        return 'code_refs is required here: list the locations ({"path", "line"}) your trace rests on, so a reader can check it'
    return None


def clean_plan(plan):
    eps = [{"id": e["id"], "door": e["door"], "where": e["where"].strip(), "found_by": str(e.get("found_by") or "")} for e in plan["entry_points"]]
    cases = [{"id": c["id"], "entry_point": c["entry_point"], "what": c["what"].strip(), "represents": str(c.get("represents") or ""),
              "setting": c["setting"].strip() if isinstance(c.get("setting"), str) else "", **clean_setup(c)} for c in plan["cases"]]
    refs = [{"path": str(r.get("path")), "line": r.get("line")} for r in plan.get("code_refs") or [] if isinstance(r, dict) and r.get("path")]
    return {**{k: plan[k].strip() for k in ("applies_to", "action", "compares", "how_found", "not_covered")}, "kind": plan["kind"],
            "entry_points": eps, "cases": cases, "code_refs": refs[:40]}


def case_problems(conv, rep):
    """Why a submitted case result can't be accepted; None if it can. A case planned from a claim whose outcome is holds
    or breaks is also held, after every other check, to the comparisons it recorded."""
    why = _case_problems(conv, rep)
    if why or not conv.get("from_claim") or rep.get("outcome") not in ("holds", "breaks"):
        return why
    if conv.get("situation_check"):                 # a variation case: first, was it in its variation's situation?
        if conv.get("situation") is None:
            return "record whether the starting situation is the one this variation describes (situation) before you submit"
        if conv["situation"].get("matches") is not True:
            return ('the starting situation is not the one this variation describes, so this case has not been run: the outcome is '
                    '"cant_run", and observed says what the starting situation lacked')
    return compare_problems(conv.get("compares") or [], rep["outcome"]) or (walk_problems(conv, rep["outcome"]) if conv.get("walk") else None)


def _case_problems(conv, rep):
    out = rep.get("outcome")
    if out not in OUTCOMES:
        return "outcome must be holds, breaks, declined, outside, nothing_there or cant_run"
    if not str(rep.get("observed") or "").strip():
        return "observed is required: give the values you read"
    used = [c["tool"] for c in conv["calls"] if not c["isError"]]
    for sid in rep.get("screenshots") or []:
        if sid not in conv["shots"]:
            return f"unknown screenshot id {sid}"
    cited = []
    for cid in rep.get("checks") or []:
        if cid not in conv["checks"]:
            return f"unknown check id {cid}"
        cited.append(conv["checks"][cid])
    ex = rep.get("excerpts") or []
    if conv["kind"] != "code" and (ex or cited):
        return "excerpts and checks are only for properties about the code itself"
    if out in ("cant_run", "outside"):
        return None
    if out == "nothing_there":
        if conv.get("way_status") not in ("to check", "flagged"):
            # The search is sure this way in exists, so not finding its control is a case that did not run, never a pass.
            return ('"nothing_there" is only for a way in the plan was unsure of. This case\'s way in exists. Choose between two outcomes. '
                    'If this case\'s own situation leaves nothing to act on (for example the item it names does not exist) and the '
                    'property\'s sentence does not cover that situation, the outcome is "outside": say which words exclude it. '
                    'If the control should be there and you could not find or use it, the outcome is "cant_run": say what you looked at')
        # Recorded as a case that does not count, with the reason kept: nothing was there to test.
        rep["outcome"], rep["nothing_there"] = "outside", True
        rep["observed"] = "No way to do the action was found there. " + str(rep["observed"])
        return None
    if out == "declined":
        if conv["kind"] == "code":
            return '"declined" does not apply to a property about the code; use holds, breaks, outside or cant_run'
        if not (str(rep.get("question") or "").strip() and str(rep.get("draft_if_intended") or "").strip()):
            return 'a declined case needs "question" (what you would ask the owner) and "draft_if_intended" (the rule the app follows there, in one sentence)'
    if conv["kind"] == "code":
        if len(ex) > 3:
            return "cite at most 3 excerpts"
        read = {c["args"].get("path") for c in conv["calls"] if c["tool"] == "read_lines" and not c["isError"]}
        for e in ex:
            try:
                s, t, path, why = int(e["start"]), int(e["end"]), str(e["path"]), str(e.get("why") or "").strip()
            except (KeyError, TypeError, ValueError):
                return 'each excerpt needs "path", "start", "end" and "why"'
            if t - s + 1 > 15 or t < s:
                return f"excerpt {path}:{s}-{t} must be 1 to 15 lines"
            if not why:
                return f'excerpt {path}:{s}-{t} needs a "why"'
            if path not in read:
                return f"you haven't read {path} with read_lines; cite only lines you read"
        if not ex and not cited:
            return "cite at least one excerpt, or a match_each check id"
        if out == "holds" and any(c.get("checked", 0) == 0 for c in cited):
            return "a check that covered zero files proves nothing, so it can't support holds"
        if out == "holds" and any(c.get("violators") for c in cited):
            return "a cited check has violators, so the property can't hold on it"
        return None
    acted = ([t for t in used if t in BROWSER or (conv.get("from_claim") and t in CLAIM_BROWSER)] if conv["door"] == "browser"
             else [t for t in used if t == "http_request"])
    if not acted:
        return "you haven't used the way in this case names, so there is nothing observed to report; use it, or answer cant_run"
    if conv["kind"] == "measurement" and out in ("holds", "breaks"):
        last_measure = max((i for i, t in enumerate(used) if t in MEASURE), default=-1)
        last_action = max((i for i, t in enumerate(used) if t in ACTIONS), default=-1)
        if last_action < 0:
            return "carry out the case's action first; nothing has been triggered yet"
        if last_measure < last_action:        # always curable: the model can measure now and submit again
            return "measure the process again now, after your last action, then submit"
    return None


def decide_verdict(plan, cases):
    """The property's verdict, from the table of cases alone. See the rules at the top of this file."""
    by = {o: [c["id"] for c in cases if c["result"].get("outcome") == o] for o in ("holds", "breaks", "declined", "outside")}
    not_run = [c["id"] for c in cases if c["result"].get("outcome") not in ("holds", "breaks", "declined", "outside")]
    if by["breaks"]:
        verdict = "fails"
    elif by["declined"]:
        verdict = "needs_decision"
    elif not_run or not by["holds"]:
        verdict = "cant_tell"
    else:
        verdict = "holds"
    return {"verdict": verdict, "held": len(by["holds"]), "planned": len(cases), "broke": by["breaks"], "declined": by["declined"],
            "outside": by["outside"], "not_run": not_run,
            "questions": [{"case": c["id"], "question": c["result"].get("question", ""), "draft_if_intended": c["result"].get("draft_if_intended", ""),
                           "observed": c["result"].get("observed", "")} for c in cases if c["id"] in by["declined"]]}


# ---- drafting claims from what a person asked for (one conversation, no tools but its submit tool)
FEATURE_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+){0,7}")
MAX_DRAFT_CLAIMS, MAX_DRAFT_QUESTIONS, MAX_SAID = 8, 6, 8000


def clean_draft_input(inp):
    """What a draft job is given: the pull request, its issues, the person's own words, the app's existing claims,
    its data tools and its outside services. At least one of a pull request, an issue or the person's words."""
    if not isinstance(inp, dict):
        raise ValueError("a draft needs an input object")
    text = lambda v, n: str(v or "")[:n]                                    # noqa: E731
    pr = inp.get("pull_request")
    pr = {"number": pr.get("number"), "title": text(pr.get("title"), 300), "body": text(pr.get("body"), 6000),
          "url": text(pr.get("url"), 500)} if isinstance(pr, dict) else None
    issues = [{"number": i.get("number"), "title": text(i.get("title"), 300), "body": text(i.get("body"), 6000),
               "url": text(i.get("url"), 500)} for i in (inp.get("issues") or [])[:5] if isinstance(i, dict)]
    said = text(inp.get("said"), MAX_SAID).strip()
    if not (pr or issues or said):
        raise ValueError("a draft needs a pull request, an issue or the person's own words")
    existing = [{"id": str(c.get("id")), "title": text(c.get("title"), 60)} for c in (inp.get("existing") or [])[:200]
                if isinstance(c, dict) and isinstance(c.get("id"), str)]
    return {"pull_request": pr, "issues": issues, "said": said, "existing": existing,
            "data_tools": clean_tools(inp.get("data_tools")), "outside_services": clean_services(inp.get("outside_services"))}


def draft_problems(rep, existing_ids):
    """Why a drafted set of claims can't be accepted, in words the model can act on; None if it can. The claims are
    held to the same rules as the claims file (toolserver/runner.py), plus the draft's own limits."""
    from toolserver.runner import ServiceError, claim_parts, claim_source, not_checked
    if not isinstance(rep.get("feature"), str) or not FEATURE_NAME.fullmatch(rep["feature"]):
        return 'feature is a short name: lowercase words joined by hyphens, such as "menu-stations"'
    if not isinstance(rep.get("goal"), str) or not rep["goal"].strip() or len(rep["goal"]) > 600:
        return "goal is one or two sentences, at most 600 characters"
    claims, questions = rep.get("claims"), rep.get("questions")
    if not isinstance(questions, list) or len(questions) > MAX_DRAFT_QUESTIONS:
        return f"questions is a list of at most {MAX_DRAFT_QUESTIONS}"
    if not isinstance(claims, list) or len(claims) > MAX_DRAFT_CLAIMS or (not claims and not questions):
        return (f"claims is a list of 1 to {MAX_DRAFT_CLAIMS}; when the text gives you nothing checkable, give no claims and one "
                "question asking what the change is meant to do")
    seen = set()
    for n, c in enumerate(claims, 1):
        if not isinstance(c, dict) or not isinstance(c.get("id"), str) or not ID.fullmatch(c["id"]) or c["id"] in (WALK, "discovery"):
            return f"claim {n} needs an id: 1 to 32 letters, digits, hyphens or underscores"
        cid = c["id"]
        if cid in seen or cid in existing_ids:
            return f"the id {cid} is already used: give each new claim an id of its own that the app does not already have"
        seen.add(cid)
        for key in ("title", "your_words", "statement", "checked_against"):
            if not isinstance(c.get(key), str) or not c[key].strip():
                return f"claim {cid} needs {key}"
        if not isinstance(c.get("source"), dict):
            return f'claim {cid} needs source: {{"kind", "ref", "quote"}}'
        if not isinstance(c.get("variations"), list) or not c["variations"]:
            return f"claim {cid} needs variations: three to six situations worth trying"
        try:
            claim_parts(c, "the draft")
            claim_source(c, "the draft")
        except ServiceError as e:
            return str(e).replace("the draft: property", "claim")
    for q in questions:
        if not (isinstance(q, dict) and isinstance(q.get("question"), str) and q["question"].strip() and len(q["question"]) <= 300
                and isinstance(q.get("about", ""), str)):
            return 'each question is {"about": "<a claim id, or empty>", "question": "<at most 300 characters>"}'
        if q.get("about") and q["about"] not in seen | set(existing_ids):
            return f'question about {q["about"]}: "about" is the id of a claim, or empty'
    try:
        not_checked({"not_checked": rep.get("not_checked") or []}, "the draft")
    except ServiceError as e:
        return str(e)
    return None


class Draft:
    """One draft job: a single model conversation, run the way a run's conversations are (Run.converse), with no tool
    but submit_draft. Nothing is stored but its usage."""

    def __init__(self, inp, model, workdir: Path, internal_url: str):
        self.input = clean_draft_input(inp)
        self.existing_ids = {c["id"] for c in self.input["existing"]}
        self.id, self.token = secrets.token_hex(6), secrets.token_hex(16)
        self.model, self.internal_url = model, internal_url
        self.dir = workdir / self.id
        self.dir.mkdir(parents=True)
        self.conv, self.aborted = None, False
        self.progress = {"unit": None}

    # The same conversation machinery as a run's: these are Run's own methods, used on this job.
    def converse(self, *a, **k):
        return Run.converse(self, *a, **k)

    def submit(self, unit, rep):
        return Run.submit(self, unit, rep)

    def _claude(self, *a, **k):
        return Run._claude(self, *a, **k)

    def tool_call(self, unit, tool, args):
        return {"text": "refused: a draft has no tools but submit_draft"}

    def work(self):
        """-> (the accepted draft or None, usage, why there is none)"""
        rep, meta, cause = self.converse("draft/claims", "draft", [], prompts.DRAFT, prompts.draft_message(self.input), max_turns=12)
        usage = {k: meta.get(k) for k in ("seconds", "cost_usd", "turns")} | {"rejected": meta.get("rejected") or []}
        if rep is not None:
            rep = {"feature": rep["feature"], "goal": rep["goal"].strip(), "claims": rep["claims"],
                   "questions": [{"about": q.get("about") or "", "question": q["question"].strip()} for q in rep["questions"]],
                   "not_checked": [str(x).strip() for x in rep.get("not_checked") or []]}
        return rep, usage, cause


class Run:
    # Set by the hosted service (service/server.py), never by the harness itself: on_cost is called with each model
    # session's cost as the session finishes; limit_reached() answers a plain sentence once the user's monthly spending
    # limit is reached (else None), and is asked before each discovery, each case and the walkthrough.
    on_cost = limit_reached = None
    stopped_by_limit = None

    def __init__(self, inp, model, workdir: Path, internal_url: str):
        self.input = validate_input(inp)
        self.env = self.input["env"]
        self.id, self.token = secrets.token_hex(6), secrets.token_hex(16)
        self.model, self.internal_url = model, internal_url
        self.dir = workdir / self.id
        self.dir.mkdir(parents=True)
        self.pending, self.waiting, self.lock = queue.Queue(), {}, threading.Lock()
        self.n = 0
        self.conv = None
        self.report, self.done, self.aborted = None, threading.Event(), False
        self.progress = {"unit": None, "properties": len(self.input["properties"]), "done": 0}

    # ---- the wire: actions go down to the user's tool server, results come back
    def call_local(self, tool, args, timeout=CALL_TIMEOUT):
        if self.aborted:
            return {"text": "error: the run was aborted", "isError": True, "data": None}
        with self.lock:
            self.n += 1
            aid = f"a{self.n}"
            slot = self.waiting[aid] = {"event": threading.Event(), "result": None}
        self.pending.put({"id": aid, "tool": tool, "args": args})
        if not slot["event"].wait(timeout):
            self.aborted = True     # the tool server stopped answering: nothing more can be observed
            return {"text": "error: the tool server did not answer", "isError": True, "data": None}
        return slot["result"]

    def take_actions(self, wait_s=20):
        deadline, out = time.time() + wait_s, []
        while time.time() < deadline and not out:
            try:
                out.append(self.pending.get(timeout=0.2))
            except queue.Empty:
                if self.done.is_set():
                    break
        while True:
            try:
                out.append(self.pending.get_nowait())
            except queue.Empty:
                return out

    def give_results(self, results):
        for r in results or []:
            slot = self.waiting.pop(r.get("id"), None)
            if slot:
                slot["result"] = {"text": str(r.get("text", "")), "isError": bool(r.get("isError")), "data": r.get("data")}
                slot["event"].set()

    # ---- what the model's tool list (service/proxy.py) calls
    def tool_call(self, unit, tool, args):
        conv = self.conv
        if not conv or conv["unit"] != unit:
            return {"text": "error: this conversation is over"}
        if tool not in conv["tools"]:
            conv["refused"].append(tool)
            return {"text": f"refused: {tool} is not available in this conversation"}
        if tool == "compare":
            args = dict(args)
            if conv.get("door") == "browser" and not str(args.get("ref") or "").strip():
                # Through the pages a comparison always points at something, so the film shows what was compared.
                conv["calls"].append({"tool": tool, "args": args, "isError": True})
                return {"text": "refused: give ref, the element that shows the thing compared (from the latest observe); when the "
                                "point is that something is missing, the area where it would be"}
            ids = (conv.get("walk") or {}).get("ids")
            if ids is None:
                args.pop("claim", None)          # only a walkthrough's comparisons say which claim they are about
                args.pop("say", None)            # nor what was said with them
            elif args.get("claim") not in ids:
                conv["calls"].append({"tool": tool, "args": args, "isError": True})
                return {"text": f"refused: claim is the id written in brackets at the start of the stop; it is one of: {', '.join(ids)}"}
            elif args["claim"] not in conv.setdefault("said", set()):
                # Within a stop of a walkthrough: first say what this part should do, then check it.
                conv["calls"].append({"tool": tool, "args": args, "isError": True})
                return {"text": "refused: first say, in one or two short spoken sentences, what this part of the app should do "
                                "(say, with this stop's claim), then check it"}
            elif not str(args.get("say") or "").strip():
                conv["calls"].append({"tool": tool, "args": args, "isError": True})
                return {"text": f"refused: {SAY_REFUSED}"}
        if tool == "compare" and conv.get("situation_check") and conv.get("situation") is None:
            conv["calls"].append({"tool": tool, "args": args, "isError": True})
            return {"text": "refused: first record whether the starting situation is the one this variation describes (situation), then check"}
        if tool == "read_tool":
            if conv.get("reads", 0) >= 6:
                conv["calls"].append({"tool": tool, "args": args, "isError": True})
                return {"text": "refused: you have used your six reads: plan with what you know"}
            conv["reads"] = conv.get("reads", 0) + 1
        if tool == "say":
            args = dict(args)
            ids = (conv.get("walk") or {}).get("ids") or []
            if args.get("claim") not in ids:
                conv["calls"].append({"tool": tool, "args": args, "isError": True})
                return {"text": f"refused: claim is the id written in brackets at the start of the stop; it is one of: {', '.join(ids)}"}
        res = self.call_local(tool, args)
        conv["calls"].append({"tool": tool, "args": args, "isError": res["isError"]})
        if not res["isError"]:
            if tool in ("screenshot", "click_and_watch", "look"):
                conv["shots"].update(re.findall(r"shot-\d+", res["text"]))
            data = res.get("data") if isinstance(res.get("data"), dict) else {}
            if tool == "match_each" and data.get("id"):
                conv["checks"][data["id"]] = {k: data.get(k) for k in ("kind", "checked", "passed", "violators")}
            if tool == "say":
                conv.setdefault("said", set()).add(args["claim"])
            if tool == "situation":                  # the latest one counts
                conv["situation"] = {"matches": data.get("matches", args.get("matches"))}
            if tool == "compare":
                conv["said"] = set()                 # the next comparison needs its own spoken line first
                conv.setdefault("compares", []).append({"what": data.get("what", args.get("what")),
                                                        "matches": data.get("matches", args.get("matches")),
                                                        "claim": data.get("claim", args.get("claim"))})
            if tool == "look" and isinstance(data.get("image"), dict):
                return {"text": res["text"], "image": data["image"]}      # the picture goes on to the model, for this tool only
        return {"text": res["text"]}

    def submit(self, unit, rep):
        conv = self.conv
        if not conv or conv["unit"] != unit:
            return {"text": "error: this conversation is over"}
        if conv["report"] is not None:
            return {"text": "already accepted; you're done"}
        role = conv["role"]
        if role == "discovery":
            why = plan_problems(rep, conv.get("env") or self.env) or (
                (variation_problems(rep, conv["variations"]) or setting_problems(rep)) if conv.get("variations") else None) or (
                tour_problems(rep, conv["walk"]["ids"]) if conv.get("walk") else None)
        elif role == "anchor":
            why = anchor_problems(rep) or self._kind_nudge(conv, rep) or self._patterns_problem(rep)
        elif role == "planner":
            why = cases_problems(rep, conv["ways"], conv.get("never", False))
        elif role == "draft":
            why = draft_problems(rep, getattr(self, "existing_ids", set()))
        else:
            why = case_problems(conv, rep)
        if not why and role in ("discovery", "planner"):      # the same check of each case's starting state on both planning paths
            why = next((p for p in (setup_problems(c, self.env) for c in rep.get("cases") or [] if isinstance(c, dict)) if p), None)
        if why:
            conv["rejected"].append(why)
            return {"text": f"not accepted: {why}"}
        conv["report"] = clean_plan(rep) if role == "discovery" else rep
        return {"text": "saved; you're done"}

    def _kind_nudge(self, conv, rep):
        """Once per conversation: when the property's sentence names a source file that exists in the repo and the
        answer is not kind code, ask for a second look. The model may keep its answer; the second submission stands."""
        if rep.get("kind") == "code" or conv.get("nudged"):
            return None
        named = re.findall(r"[\w./-]+\.(?:py|ts|tsx|js|jsx|mjs|rb|go|rs|java|html|css)\b", conv.get("statement") or "")
        if not named:
            return None
        files = (self.call_local("list_files", {}).get("text") or "").splitlines()
        hit = next((n for n in named if any(f == n or f.endswith("/" + n) for f in files)), None)
        if not hit:
            return None
        conv["nudged"] = True
        return (f"the property's sentence names the source file {hit}. A property about what the code defines, reuses or "
                "how it is organised is kind code, and using the app cannot settle it. If you are sure this one is about "
                "what the app does or how it runs, submit the same answer again.")

    def _patterns_problem(self, rep):
        """Roots that are not real lines, and search patterns that match nothing or far too much, are sent back before any tracing starts."""
        for r in rep.get("roots") or []:
            res = self.call_local("trace", {"op": "context", "path": r["path"], "line": r["line"]})
            data = res.get("data") or {}
            if res["isError"] or not data.get("start", 0) <= r["line"] <= data.get("end", -1):
                return f"root {r['path']}:{r['line']} is not a line in the code ({res['text'][:100]})"
        for pat in rep.get("patterns") or []:
            res = self.call_local("trace", {"op": "search", "pattern": pat})
            total = (res.get("data") or {}).get("total")
            if res["isError"] or total is None:
                return f"the search for {pat!r} failed ({res['text'][:120]})"
            if total == 0:
                return f"pattern {pat!r} matches no line in the code; look at how the code names the data"
            if total > 80:
                return (f"pattern {pat!r} matches {total} lines, too many to examine; give the name of the stored data itself, "
                        "as the code writes it, not a common word")
        return None

    # ---- the model
    def _claude(self, prompt, system, extra, name, timeout=900):
        cmd = [CLAUDE, "-p", prompt, "--model", self.model, "--output-format", "json", "--no-session-persistence",
               "--system-prompt", system, "--tools", "", "--setting-sources", "", "--strict-mcp-config", *extra]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, cwd=self.dir, timeout=timeout, stdin=subprocess.DEVNULL)
            out, err = p.stdout, p.stderr
        except subprocess.TimeoutExpired:
            out, err = "", "timed out"
        try:
            res = json.loads(out)
        except json.JSONDecodeError:
            res = {"error": (err or out)[-800:]}
        (self.dir / f"{name}.json").write_text(json.dumps(res, indent=2))
        if getattr(self, "on_cost", None) and res.get("total_cost_usd"):      # a Draft borrows this method
            self.on_cost(res["total_cost_usd"])
        return res

    def _limit_stop(self):
        """The spending limit's sentence once it is reached (it then stays reached for this run), else None."""
        if self.stopped_by_limit is None and self.limit_reached is not None:
            self.stopped_by_limit = self.limit_reached()
        return self.stopped_by_limit

    def converse(self, unit, role, tools, system, message, kind=None, door=None, max_turns=30, ways=None, never=False, statement=None,
                 way_status=None, variations=None, env=None, from_claim=False, walk=None, situation_check=False):
        self.conv = conv = {"unit": unit, "role": role, "tools": set(tools), "kind": kind, "door": door, "calls": [],
                            "shots": set(), "checks": {}, "report": None, "rejected": [], "refused": [], "ways": ways, "never": never,
                            "statement": statement, "way_status": way_status, "variations": variations, "env": env,
                            "from_claim": bool(from_claim), "compares": [], "walk": walk,
                            "situation_check": bool(situation_check), "situation": None, "reads": 0}
        self.progress["unit"] = unit
        name = unit.replace("/", "-") + ("" if role in ("discovery", "case") else f"-{role}")
        mcp = {"mcpServers": {"gt": {"command": sys.executable, "args": [str(HERE / "proxy.py")], "env": {
            "GT_SERVICE": self.internal_url, "GT_RUN": self.id, "GT_TOKEN": self.token, "GT_UNIT": unit,
            "GT_ROLE": role, "GT_TOOLS": json.dumps(tools)}}}}
        (self.dir / f"{name}.mcp.json").write_text(json.dumps(mcp))
        finish = {"discovery": "submit_plan", "case": "submit_case", "anchor": "submit_anchor", "planner": "submit_cases",
                  "draft": "submit_draft"}[role]
        allowed = [f"mcp__gt__{t}" for t in tools + [finish]]
        t0 = time.time()
        res = self._claude(message, system, ["--mcp-config", str(self.dir / f"{name}.mcp.json"), "--allowedTools", *allowed,
                                             "--max-turns", str(max_turns)], name)
        self.conv = None
        cause = None
        if conv["report"] is None:
            cause = "the run was aborted" if self.aborted else str(res.get("error") or res.get("subtype") or "it stopped without one")[:200]
        meta = {"seconds": round(time.time() - t0, 1), "cost_usd": res.get("total_cost_usd"), "turns": res.get("num_turns"),
                "calls": len(conv["calls"]), "rejected": conv["rejected"], "refused_tools": conv["refused"], "tools": tools}
        return conv["report"], meta, cause

    def discover_from_claim(self, prop, system_extra="", message_extra="", walk=None):
        """Plan one case per variation the owner approved, without reading the code. The claim says what must be true and
        where the right answer comes from; the planner works out, from the running app and the declared data tools, how
        to test each variation. -> (plan, meta, why there is no plan, None)"""
        unit = f"{prop['id']}/discovery"
        env = {**self.env, "has_repo": False}          # no code reading on this path, even when a repo is registered
        self.call_local("begin_claim", {"claim": unit, "reset": False})
        reading = ["read_tool"] if any(t.get("reads_only") for t in env.get("data_tools") or []) else []
        plan, meta, cause = self.converse(unit, "discovery", discovery_tools(env) + CLAIM_BROWSER + reading,
                                          prompts.DISCOVERY + prompts.STARTING_STATE + prompts.FROM_CLAIM + system_extra,
                                          prompts.discovery_message(prop, env, prop["decisions"], None if walk else self.others(prop["id"]))
                                          + message_extra, max_turns=45,
                                          variations=prop["variations"], env=env, walk=walk)
        total = {"seconds": meta.get("seconds") or 0, "cost_usd": meta.get("cost_usd") or 0, "turns": meta.get("turns") or 0,
                 "calls": meta.get("calls") or 0, "rejected": meta.get("rejected") or [], "refused_tools": meta.get("refused_tools") or [],
                 "conversations": 1}
        if plan:
            plan["from_claim"] = True
        self.call_local("end_claim", {"claim": unit, "report": {"role": "discovery", "plan": plan, "trace": None,
                                                                "no_plan_because": None if plan else cause, "meta": total}})
        return plan, total, None if plan else cause, None

    def discover(self, prop):
        """Find the ways in and plan the cases for one property. -> (plan, meta, why there is no plan, trace)"""
        if prop.get("variations"):
            return self.discover_from_claim(prop)
        env, unit = self.env, f"{prop['id']}/discovery"
        self.call_local("begin_claim", {"claim": unit, "reset": False})
        looking = discovery_tools(env)
        plan = trace = cause = fell_back = None
        total = {"seconds": 0.0, "cost_usd": 0.0, "turns": 0, "calls": 0, "rejected": [], "refused_tools": [], "conversations": 0}

        def add(meta):
            total["conversations"] += 1
            for k in ("seconds", "cost_usd", "turns", "calls"):
                total[k] = round(total[k] + (meta.get(k) or 0), 4)
            total["rejected"] += meta.get("rejected") or []
            total["refused_tools"] += meta.get("refused_tools") or []

        anchor = None
        if env.get("has_repo"):
            anchor, meta, cause = self.converse(unit, "anchor", looking, prompts.ANCHOR, prompts.anchor_message(prop, env), max_turns=40,
                                                statement=prop["statement"])
            add(meta)
            if anchor is None:
                fell_back = (f"The start of the code search could not be settled ({cause}), so the ways in below were found by a model "
                             "reading the code, with no check that the search was complete.")
            if anchor and anchor["kind"] != "code":
                try:
                    tracer = Tracer(self, prop, unit)
                    trace = tracer.run_trace(anchor)
                    total["cost_usd"] = round(total["cost_usd"] + trace["label_cost_usd"], 4)
                    total["labels"] = trace["labels"]
                    if not trace["ways"]:
                        raise TraceError("the search found no way in")
                    rep, meta, cause = self.converse(unit, "planner", looking, prompts.PLANNER + prompts.STARTING_STATE,
                                                     prompts.planner_message(prop, env, anchor, trace["ways"], trace["roots"], trace["routes"]),
                                                     max_turns=45, ways=trace["ways"], never=bool(anchor.get("never")))
                    add(meta)
                    if not rep:
                        raise TraceError(f"the case planner gave no accepted plan ({cause})")
                    plan = build_plan(anchor, trace, rep)
                    planned = {c.get("id"): c for c in rep["cases"]}
                    for c in plan["cases"]:
                        c.update(clean_setup(planned.get(c["id"]) or {}))
                except TraceError as e:
                    total["cost_usd"] = round(total["cost_usd"] + tracer.cost, 4) if trace is None else total["cost_usd"]
                    trace = None        # a search that was not completed is not shown as if it had been
                    fell_back = f"The code search could not be completed ({e}), so the ways in below were found by a model reading the code, with no check that the search was complete."
        if plan is None and (anchor is None or anchor["kind"] == "code" or fell_back or not env.get("has_repo")):
            # properties about the code itself, apps whose code can't be read, and a trace that could not be completed
            plan, meta, cause = self.converse(unit, "discovery", looking, prompts.DISCOVERY + prompts.STARTING_STATE,
                                              prompts.discovery_message(prop, env, prop["decisions"]), max_turns=45)
            add(meta)
            if plan and fell_back:
                plan["how_found"] = fell_back + " " + plan["how_found"]
        self.call_local("end_claim", {"claim": unit, "report": {"role": "discovery", "plan": plan, "trace": trace,
                                                                "no_plan_because": None if plan else cause, "meta": total}})
        return plan, total, None if plan else cause, trace

    def others(self, pid):
        """The feature's claims other than this one, for a claims-path planner and case."""
        return [c for c in self.input.get("feature_claims") or [] if c["id"] != pid]

    def run_case(self, prop, plan, case, unit, walk=None, walk_message=""):
        """Carry out one planned case: open it on clean data, set up its starting state, hold the conversation (with
        one more try when it ends without a result), and save what came of it. -> the case with its result"""
        env = self.env
        entry = next(e for e in plan["entry_points"] if e["id"] == case["entry_point"])
        kind, door = plan["kind"], entry["door"]
        from_claim = bool(plan.get("from_claim"))
        started = self.call_local("begin_claim", {"claim": unit, "reset": True})
        rep, meta, baseline = None, {}, None
        if started["isError"]:        # e.g. the registered reset command failed: the case would run on unknown data
            rep = {"outcome": "cant_run", "decided_by_harness": True, "observed": f"The case could not be started: {started['text']}"}
        else:
            system = prompts.CASE + (prompts.CASE_CODE if kind == "code" else prompts.CASE_BROWSER if door == "browser" else prompts.CASE_API) \
                + (prompts.CASE_MEASURE if kind == "measurement" else "") \
                + ((prompts.CASE_FROM_CLAIM + ("" if walk else prompts.CASE_SITUATION)
                    + (prompts.CASE_FROM_CLAIM_BROWSER if door == "browser" else "")) if from_claim else "") \
                + ("\n" + prompts.WALKTHROUGH_CASE if walk else "") + "\n" + prompts.SHARED
            spent = {"seconds": 0.0, "cost_usd": 0.0, "turns": 0}
            setup, why = [], "it stopped without one"
            for attempt in (1, 2):    # a conversation that ends without any result gets one more try
                if attempt == 2:      # which starts the way the first did: clean data, a fresh browser, a fresh baseline
                    again = self.call_local("begin_claim", {"claim": unit, "reset": True})
                    if again["isError"]:
                        rep = {"outcome": "cant_run", "decided_by_harness": True,
                               "observed": f"The first try gave no result and the case could not be started again: {again['text']}"}
                        break
                # The starting state comes first: right after the reset, before anything else touches the app.
                outside = outside_service_problem(case, env)
                if outside:             # it would need a real outside service changed (or called): never done here
                    rep = {"outcome": "cant_run", "decided_by_harness": True, "observed": outside}
                    break
                if case.get("needs"):
                    why_not = ("the project's data tools cannot be used, because no reset command is registered"
                               if env.get("data_tools_blocked") else "no declared data tool can make its starting state")
                    rep = {"outcome": "cant_run", "decided_by_harness": True, "data_request": case["needs"],
                           "observed": f"This case was not run: {why_not}. Needed: {case['needs']['state']}"}
                    break
                setup, problem = run_setup(case, self.call_local)
                if problem:
                    rep = {"outcome": "cant_run", "decided_by_harness": True, "observed": f"This case was not run: {problem}"}
                    break
                if kind == "measurement" and env.get("has_pid"):
                    # Measured by the harness, before this case touches the app, so a baseline always exists.
                    baseline = "\n".join(f"{t}:\n{self.call_local(t, {})['text']}" for t in ("process_info", "gpu_check", "net_connections"))
                others = self.others(prop["id"]) if from_claim and not walk else None
                variation = next((v["what"] for v in prop.get("variations") or [] if v["id"] == case["id"]), None) \
                    if from_claim and not walk else None
                message = prompts.case_message(prop, plan, case, entry, env, baseline, setup, others, services=from_claim,
                                               variation=variation) + (walk_message if walk else "")
                rep, meta, why = self.converse(unit, "case", case_tools(kind, door, env, from_claim) + (["say"] if walk else [])
                                               + (["situation"] if from_claim and not walk else []), system, message, kind=kind,
                                               door=door, way_status=entry.get("status"), from_claim=from_claim,
                                               max_turns=150 if walk else 30, walk={**walk, "what": case["what"]} if walk else None,
                                               situation_check=from_claim and not walk)
                for k in spent:       # the first attempt's time and cost count too
                    spent[k] += meta.get(k) or 0
                meta.update(attempts=attempt, seconds=round(spent["seconds"], 1), cost_usd=spent["cost_usd"], turns=spent["turns"])
                if rep is not None or self.aborted:
                    break
            if rep is None:
                rep = {"outcome": "cant_run", "decided_by_harness": True,
                       "observed": f"The verifier never submitted an accepted result ({why})."}
            if setup:
                rep["starts_from"] = setup      # as carried out, with what each step returned
        out = self.call_local("end_claim", {"claim": unit, "report": {"role": "case", **rep, "baseline": baseline, "meta": meta}})
        return {**case, "door": door, "result": out.get("data") or rep}

    def walk(self):
        """The feature's walkthrough, after every property: one filmed case of the whole feature in use, with one stop
        per claim. It never touches any property's verdict. -> the report's "walkthrough", or None when none was asked for"""
        w = self.input.get("walkthrough")
        if not w:
            return None
        out = {"plan": None, "discovery_meta": {}, "no_plan_because": None, "case": None}
        if self.input["plan_only"] or self.aborted or self._limit_stop():
            out["no_plan_because"] = "the run was plan-only" if self.input["plan_only"] else \
                "the run ended before the walkthrough" if self.aborted else f"It was not run: {self.stopped_by_limit}"
            return out
        ids = [c["id"] for c in w["claims"]]
        prop = {"id": WALK, "statement": w["goal"] or "The feature, used start to finish.", "example": "", "decisions": [],
                "checked_against": "The data put in for this walkthrough.", "variations": [dict(TOUR)]}
        claims = prompts.walkthrough_claims(w["claims"])
        plan, meta, cause, _ = self.discover_from_claim(
            prop, prompts.WALKTHROUGH_PLAN, "\n\nThe claims this feature must keep, one stop for each:\n" + claims, walk={"ids": ids})
        out.update(plan=plan, discovery_meta=meta, no_plan_because=cause)
        if plan and not self.aborted and not self._limit_stop():
            tour = next(c for c in plan["cases"] if c["id"] == TOUR["id"])
            out["case"] = self.run_case(prop, plan, tour, f"{WALK}/{TOUR['id']}", walk={"ids": ids},
                                        walk_message="\n\nThe claims, by the id written at each stop:\n" + claims)
        elif plan:
            out["no_plan_because"] = "the run ended before the walkthrough was carried out"
        return out

    def work(self):
        try:
            props, env = self.input["properties"], self.env
            plans = {}
            for prop in props:                                   # 1. discovery: look, never act
                if self.aborted or self._limit_stop():
                    break
                plans[prop["id"]] = self.discover(prop)
            # 2. cases: properties that measure the process go first, before anything else exercises the app
            order = sorted(props, key=lambda p: 0 if (plans.get(p["id"], (None,))[0] or {}).get("kind") == "measurement" else 1)
            results = {}
            for prop in order:
                plan, dmeta, cause, trace = plans.get(prop["id"], (None, {}, self.stopped_by_limit or "the run ended before discovery", None))
                cases = []
                for case in (plan or {}).get("cases", []):
                    if self.input["plan_only"]:
                        cases.append({**case, "door": next(e["door"] for e in plan["entry_points"] if e["id"] == case["entry_point"]),
                                      "result": {"outcome": "planned", "observed": "Planned only; not carried out."}})
                        continue
                    if self.aborted:
                        cases.append({**case, "result": {"outcome": "cant_run", "observed": "The run ended before this case was carried out."}})
                        continue
                    if self._limit_stop():
                        cases.append({**case, "result": {"outcome": "cant_run", "spending_limit": True,
                                                         "observed": f"This case was not run: {self.stopped_by_limit}"}})
                        continue
                    cases.append(self.run_case(prop, plan, case, f"{prop['id']}/{case['id']}"))
                self.progress["done"] += 1
                results[prop["id"]] = {"id": prop["id"], "statement": prop["statement"], "example": prop["example"],
                                       "checked_against": prop.get("checked_against") or "",
                                       "plan": plan, "no_plan_because": cause, "discovery_meta": dmeta, "trace": trace, "cases": cases,
                                       **decide_verdict(plan or {}, cases)}
                if self.input["plan_only"]:
                    results[prop["id"]]["verdict"] = "planned" if plan else "cant_tell"
            walkthrough = self.walk()            # 3. the walkthrough, if one was asked for: it decides no verdict
            self.report = {"run": self.id, "service": {"version": VERSION, "model": self.model}, "aborted": self.aborted,
                           "order": [p["id"] for p in order],
                           "properties": [results.get(p["id"], {"id": p["id"], "statement": p["statement"], "verdict": "cant_tell",
                                                                "cases": [], "no_plan_because": "the run ended first"}) for p in props]}
            if walkthrough is not None:
                self.report["walkthrough"] = walkthrough
            if self.stopped_by_limit:
                self.report["stopped_by_limit"] = {
                    "message": self.stopped_by_limit,
                    "cases_not_run": [f"{pid}/{c['id']}" for pid, r in results.items() for c in r["cases"] if c["result"].get("spending_limit")],
                    "properties_not_planned": [p["id"] for p in props if p["id"] not in plans]}
        except Exception as e:  # noqa: BLE001
            self.report = {"run": self.id, "error": f"{type(e).__name__}: {e}", "properties": []}
        finally:
            self.done.set()


def default_workdir():
    return Path(tempfile.mkdtemp(prefix="gt-service-"))
