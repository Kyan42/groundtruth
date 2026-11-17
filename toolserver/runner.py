"""The user's side of a verify run: the hands.

It makes every connection itself (nothing reaches into this machine): it sends the claims and a description of
what this machine can do, then carries out the actions the hosted verifier asks for, one at a time, through the
catalog, and posts the results back. The hosted side is treated as untrusted: every action is checked here before
it runs, and the evidence that ends up in the report (screenshots, cited code lines, check results) is taken from
this machine, not from what the hosted side says it saw.
"""
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

VERSION = "3"


class ServiceError(Exception):
    pass


def _send(url, body=None, method="POST", timeout=120, token=None):
    from .push import open_no_redirect      # a redirect is never followed, so the token goes only where it was told
    headers = {"Content-Type": "application/json", "User-Agent": f"groundtruth-proof/{VERSION}"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(), method=method, headers=headers)
    try:
        with open_no_redirect(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("message") or e.reason
        except Exception:  # noqa: BLE001
            msg = e.reason
        if e.code in (401, 402, 403):        # who may use it, and the spending limit: the service's own words
            raise ServiceError(str(msg)) from None
        raise ServiceError(f"the verifier service answered {e.code}: {msg}") from None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise ServiceError(f"couldn't reach the verifier service at {url.split('/v1')[0]} ({getattr(e, 'reason', e)})") from None


def describe(env):
    """What the service is told about this machine: the app's address and what can be done here. Never a path."""
    tools, services = [], None
    if env.get("repo"):
        try:        # the declared data tools, never their commands; a broken file is reported by registration_problem
            from . import datatools
            tools = datatools.for_service(datatools.load(Path(env["repo"])))
            services = datatools.load_services(Path(env["repo"]))
        except Exception:  # noqa: BLE001
            tools, services = [], None
    out = {"url": env["url"], "has_pid": env.get("pid") is not None, "has_repo": bool(env.get("repo")),
           "has_reset": bool(env.get("reset")), "has_routes": bool(env.get("routes")), "data_tools": tools}
    if services is not None:            # the outside services the project lists: only when it has a services file
        out["outside_services"] = services
    return out


def registration_problem(env):
    """Why this registration can't be verified against right now, or None. A restarted app has a new process id:
    measuring the old one would report on a process that is gone."""
    import os
    import subprocess
    try:
        urllib.request.urlopen(env["url"], timeout=5).close()
    except urllib.error.HTTPError:
        pass                                   # it answered; any status means the app is up
    except Exception as e:  # noqa: BLE001
        return f"nothing answers at {env['url']} ({getattr(e, 'reason', e)}). Start the app, then register it again."
    pid = env.get("pid")
    if pid is not None:
        try:
            os.kill(int(pid), 0)
        except ProcessLookupError:
            return f"the registered process (pid {pid}) is no longer running. Register the app again after a restart."
        except PermissionError:
            pass
        started = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
        was = (env.get("facts") or {}).get("started")
        if was and started and was != started:
            return f"pid {pid} is now a different process than the one registered. Register the app again."
    if env.get("repo"):
        # A broken data tools file is reported now, before anything runs, not halfway through a run.
        from .datatools import DataToolsError, load, load_services
        try:
            load_services(Path(env["repo"]))
            load(Path(env["repo"]))
        except DataToolsError as e:
            return f"{e} Fix the file, then run verify again."
    return None


VIEWPORT = {"width": 900, "height": 700}
WIDTHS, HEIGHTS = (600, 1920), (500, 1200)


def viewport_of(given) -> dict:
    """The verifier's browser window size, from "1280x800" or {"width", "height"}. ServiceError in plain words when it
    is not a size the verifier can use."""
    w = h = None
    if isinstance(given, str):
        m = re.fullmatch(r"\s*(\d{1,5})\s*[xX×]\s*(\d{1,5})\s*", given)
        if m:
            w, h = int(m.group(1)), int(m.group(2))
    elif isinstance(given, dict) and all(isinstance(given.get(k), int) and not isinstance(given.get(k), bool) for k in ("width", "height")):
        w, h = given["width"], given["height"]
    if w is None or not (WIDTHS[0] <= w <= WIDTHS[1] and HEIGHTS[0] <= h <= HEIGHTS[1]):
        raise ServiceError(f"the window size {given if isinstance(given, str) else json.dumps(given)} can't be used. Give it as "
                           f"WIDTHxHEIGHT, for example 1280x800: a width from {WIDTHS[0]} to {WIDTHS[1]} and a height from "
                           f"{HEIGHTS[0]} to {HEIGHTS[1]}.")
    return {"width": w, "height": h}


def refuse(why):
    return {"text": f"refused: {why}", "isError": True, "data": None}


class Runner:
    """Carries out one run's actions. One unit of work is open at a time (a property's discovery, or one of its
    cases); each gets its own folder, browser and receipts: <run>/<property id>/<discovery | case id>/."""

    UNIT = re.compile(r"([A-Za-z0-9_-]{1,32})/([A-Za-z0-9_-]{1,32})")

    def __init__(self, env, properties, run_dir: Path, headed=False, say=print, walkthrough=False):
        self.env, self.props, self.run_dir, self.headed, self.say = env, {p["id"]: p for p in properties}, run_dir, headed, say
        if walkthrough:                 # the walkthrough's own units: walkthrough/discovery and walkthrough/tour
            self.props["walkthrough"] = {"id": "walkthrough"}
        self.catalog, self.unit, self.reports, self.blocked = None, None, {}, None

    def _note(self, act, res):
        """Refusals made before any unit's catalog exists still leave a receipt."""
        with open(self.run_dir / "runner.jsonl", "a") as f:
            f.write(json.dumps({"t": round(time.time(), 2), "tool": act.get("tool") if isinstance(act, dict) else None,
                                "args": repr(act.get("args") if isinstance(act, dict) else act)[:300],
                                "result": res["text"], "refused": True}) + "\n")
        return res

    def execute(self, act):
        if not isinstance(act, dict) or not isinstance(act.get("tool"), str) or not isinstance(act.get("args") or {}, dict):
            return self._note(act, refuse("malformed action"))
        tool, args = act["tool"], act.get("args") or {}
        if tool == "begin_claim":
            m = self.UNIT.fullmatch(args.get("claim")) if isinstance(args.get("claim"), str) else None
            if not m or m.group(1) not in self.props or args["claim"] in self.reports:
                return self._note(act, refuse("not a property of this run, or already finished"))
            self.close()
            from .catalog import Catalog
            # A case's browser is filmed; a discovery only looks, so it is not filmed and may not set up data.
            discovery = m.group(2) == "discovery"
            self.unit, self.catalog = args["claim"], Catalog(self.env, self.run_dir / m.group(1) / m.group(2), headed=self.headed,
                                                             record=not discovery, discovery=discovery)
            if args.get("reset") and self.env.get("reset"):   # every case starts from clean data when a reset is registered
                res = self.catalog.run("reset_data", {})
                if res["isError"]:                            # never run a case on data that was meant to be clean and isn't
                    self.blocked = f"the registered reset command failed ({res['text']})"
                    return {"text": self.blocked, "isError": True, "data": None}
                return {"text": "ready; reset done", "isError": False, "data": None}
            return {"text": "ready", "isError": False, "data": None}
        if self.catalog is None:
            return self._note(act, refuse("nothing is open"))
        if self.blocked and tool != "end_claim":      # the unit stays open only so its "could not run" result can be saved
            return self._note(act, refuse(f"this unit cannot run: {self.blocked}"))
        if tool == "end_claim":
            if args.get("claim") != self.unit or not isinstance(args.get("report"), dict):
                return self._note(act, refuse("only the open unit can be finished, with a report"))
            self.catalog.close()        # first: a clip is only complete once the browser closes, and the report lists it
            rep = self.catalog.finish(dict(args["report"]))
            self.reports[self.unit] = rep
            if rep.get("role") == "discovery":
                plan = rep.get("plan") or {}
                cases = plan.get("cases") or []
                self.say(f"  {self.unit}: " + (f"{len(cases)} cases through {len(plan.get('entry_points') or [])} entry point(s), kind {plan.get('kind')}"
                                               if plan else f"no plan ({rep.get('no_plan_because')})"))
            else:
                self.say(f"  {self.unit} {rep.get('outcome')}: {str(rep.get('observed'))[:150]}")
            self.close()
            return {"text": "saved", "isError": False, "data": rep}
        return self.catalog.run(tool, args)

    def close(self):
        if self.catalog is not None:
            self.catalog.close()
        self.catalog, self.unit, self.blocked = None, None, None


def claim_parts(p: dict, fname: str) -> dict:
    """The optional parts of a claim: `title` (a few words to scan by) and `your_words` (what the owner said when
    first describing it, word for word, so they recognise it later); the verifier never judges by either,
    `checked_against` (where the right answer comes from) and `variations` (the situations the owner approved, each a
    sentence or {id, what}). A property that has variations is planned from
    them, without reading the code. Problems are reported in plain words before anything is sent."""
    out, pid = {}, p.get("id")
    title = p.get("title")
    if title is not None:
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 60:
            raise ServiceError(f"{fname}: property {pid} has a `title` that is not a short phrase. A title is a few words, at most 60 characters; "
                               "the full sentence stays in `statement`.")
        out["title"] = title.strip()
    words = p.get("your_words")
    if words is not None:
        if not isinstance(words, str) or not words.strip() or len(words.strip()) > 300:
            raise ServiceError(f"{fname}: property {pid} has a `your_words` that is not one or two sentences (at most 300 characters). "
                               "It is what you said when you first described this, word for word.")
        out["your_words"] = words.strip()
    against = p.get("checked_against")
    if against is not None:
        if not isinstance(against, str) or not against.strip():
            raise ServiceError(f"{fname}: property {pid} has a `checked_against` that is not a sentence. It says where the right answer comes from.")
        out["checked_against"] = against.strip()
    given = p.get("variations")
    if given is None:
        return out
    if not isinstance(given, list) or not 1 <= len(given) <= 12:
        raise ServiceError(f"{fname}: property {pid} needs 1 to 12 variations, each on its own line starting with `- `.")
    seen, clean = set(), []
    for n, v in enumerate(given, 1):
        if isinstance(v, str):
            v = {"id": f"v{n}", "what": v}
        what = v.get("what") if isinstance(v, dict) else None
        if not isinstance(what, str) or not what.strip():
            raise ServiceError(f"{fname}: property {pid}, variation {n} is not a sentence. Write each variation as one plain sentence.")
        vid = str(v.get("id") or f"v{n}")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", vid) or vid == "discovery" or vid in seen:
            raise ServiceError(f"{fname}: property {pid}, variation {n} has the id {vid!r}. An id is 1 to 32 letters, digits, "
                               "hyphens or underscores, used once.")
        seen.add(vid)
        clean.append({"id": vid, "what": what.strip()})
    out["variations"] = clean
    return out


def not_checked(feature: dict, fname: str) -> list:
    """The feature file's optional top-level `not_checked:` list: what the owner was told no claim covers, at most 12
    plain sentences of at most 200 characters each. It is shown with the results, and never sent to the verifier."""
    given = feature.get("not_checked") if isinstance(feature, dict) else None
    if given is None:
        return []
    if not isinstance(given, list) or len(given) > 12:
        raise ServiceError(f"{fname}: `not_checked` is a list of at most 12 sentences, each on its own line starting with `- `.")
    out = []
    for n, item in enumerate(given, 1):
        if not isinstance(item, str) or not item.strip() or len(item.strip()) > 200:
            raise ServiceError(f"{fname}: item {n} of `not_checked` is not one plain sentence of at most 200 characters. "
                               "Each item says one thing no claim checks.")
        out.append(item.strip())
    return out


SOURCE_KINDS = ("issue", "pull request", "interview", "other")


def claim_source(p: dict, fname: str):
    """A claim's optional `source`: where it came from ({kind, ref, quote}), or None. Plain-words errors otherwise.
    It is never sent to the verifier service and is not part of the approval comparison."""
    given, pid = p.get("source"), p.get("id")
    if given is None:
        return None
    if not isinstance(given, dict) or [k for k in given if k not in ("kind", "ref", "quote")]:
        raise ServiceError(f"{fname}: property {pid} has a `source` that is not a set of `kind`, `ref` and `quote`.")
    if given.get("kind") not in SOURCE_KINDS:
        raise ServiceError(f"{fname}: property {pid} has a source `kind` of {given.get('kind')!r}. It must be exactly issue, "
                           "pull request, interview or other.")
    out = {"kind": given["kind"]}
    for key, most, what in (("ref", 200, "where it is (an issue like owner/repo#123, or an address)"),
                            ("quote", 600, "the sentence the claim was taken from")):
        v = given.get(key)
        if v is None:
            out[key] = ""
            continue
        if not isinstance(v, (str, int)) or isinstance(v, bool) or len(str(v).strip()) > most:
            raise ServiceError(f"{fname}: property {pid} has a source `{key}` that is not {what} of at most {most} characters.")
        out[key] = str(v).strip()
    return out


def source_words(src) -> str:
    """A source in a few words: `issue acme/dining-app#786: "<quote>"`."""
    if not isinstance(src, dict):
        return ""
    return src["kind"] + (f" {src['ref']}" if src.get("ref") else "") + (f': "{src["quote"]}"' if src.get("quote") else "")


def walkthrough_input(feature: dict, fname: str) -> dict:
    """What the service is sent for the walkthrough: the feature's goal and every property that has variations, in file
    order (all of them, whatever --only says: the walkthrough is of the feature)."""
    claims = []
    for p in feature.get("properties") or []:
        if isinstance(p, dict) and p.get("variations"):
            parts = claim_parts(p, fname)
            claims.append({"id": p["id"], "title": parts.get("title", ""), "statement": str(p.get("statement") or "").strip(),
                           "checked_against": parts.get("checked_against", "")})
    if not claims:
        raise ServiceError(f"{fname} has no claim with variations, so there is nothing for a walkthrough to show. A walkthrough "
                           "has one stop for each claim whose variations you approved.")
    return {"goal": str(feature.get("goal") or "").strip(), "claims": claims}


def run_verify(repo: Path, feature_name: str, service: str, only=None, headed=False, say=print, plan_only=False,
               viewport=None, walkthrough=False, walkthrough_only=False, no_context=False, pr=None, token=None) -> Path:
    env = json.loads((repo / ".groundtruth" / "env.json").read_text())
    # The window size: this run's --viewport, else the registered one, else 900 by 700. env.json is never changed here.
    size = viewport_of(viewport) if viewport is not None else viewport_of(env["viewport"]) if env.get("viewport") is not None else dict(VIEWPORT)
    env = {**env, "viewport": size}
    ffile = repo / ".groundtruth" / "features" / f"{feature_name}.yml"
    if not ffile.exists():
        raise ServiceError(f"no feature file at {ffile}")
    try:
        feature = yaml.safe_load(ffile.read_text())
    except yaml.YAMLError as e:
        line = getattr(getattr(e, "problem_mark", None), "line", None)
        where = f" near line {line + 1}" if line is not None else ""
        raise ServiceError(f"{ffile.name} is not valid YAML{where}. A sentence that contains a colon followed by a space "
                           "has to be in double quotes, or written on the line after `statement: >-`.") from None
    if not isinstance(feature, dict) or "properties" not in feature:
        raise ServiceError(f"{feature_name}.yml has no properties. A property is one plain sentence: a general rule about the app.")
    unchecked = not_checked(feature, ffile.name)       # kept as the file said when the run started; never sent
    # Where each claim came from, as the file said when the run started: kept with the run, never sent.
    sources = {p["id"]: claim_source(p, ffile.name) for p in feature["properties"] if isinstance(p, dict) and isinstance(p.get("id"), str)}
    sources = {k: v for k, v in sources.items() if v}
    if any(isinstance(p, dict) and p.get("id") == "walkthrough" for p in feature["properties"]):
        raise ServiceError(f"{ffile.name}: a property has the id walkthrough, which is kept for the feature's walkthrough. Give it another id.")
    walk = walkthrough_input(feature, ffile.name) if (walkthrough or walkthrough_only) else None
    decided = {}
    dfile = repo / ".groundtruth" / "decisions" / f"{feature_name}.json"
    if dfile.exists():
        decided = json.loads(dfile.read_text())
    props = [{"id": p["id"], "statement": p["statement"], "example": p.get("example", ""), "decisions": decided.get(p["id"], []),
              **claim_parts(p, ffile.name)}
             for p in feature["properties"] if not only or p["id"] in only.split(",")]
    skipped = []
    if walkthrough_only:                # no property's cases run; each is listed as not run
        skipped, props = props, []
    elif not props:
        raise ServiceError(f"no property in {feature_name}.yml matches --only {only}" if only else f"{feature_name}.yml has no properties")
    problem = registration_problem(env)
    if problem:
        raise ServiceError(problem)
    # When the owner approved this feature's claims, say which no longer read as approved. The run goes ahead either
    # way; the comparison is kept with the run, so the page shows what was true when it ran. Nothing here is sent.
    from . import approval
    record, approval_state = approval.load_record(repo, feature_name), None
    if record:
        states = approval.compare([(p["id"], {k: p[k] for k in approval.PARTS if k in p}) for p in props], record)
        for line in approval.flag_lines(states):
            say(line)
        approval_state = {"approved_at": record.get("approved_at"), "properties": states,
                          "draft_changes": record.get("draft_changes")}
    run_id = time.strftime("%Y%m%d-%H%M%S")
    run_dir = repo / ".groundtruth" / "runs" / run_id
    run_dir.mkdir(parents=True)
    service = service.rstrip("/")
    say(f"Run {run_id}: {len(props)} {'property' if len(props) == 1 else 'properties'}"
        + (" and the walkthrough" if walk else "") + f" against {env['url']}, window {size['width']}x{size['height']}")
    # What the pull request and its issues said, read with the user's own `gh` login; kept with the run, never sent.
    context = None
    if not no_context:
        from .context import fetch
        context = fetch(repo, pr=pr)
        if context is None:
            say("No pull request was found to keep with this run" + (f" ({pr})" if pr else "") + ".")
    inp = {"feature": feature["feature"], "goal": feature.get("goal", ""), "env": describe(env), "properties": props,
           "plan_only": bool(plan_only)}
    if walk:
        inp["walkthrough"] = walk
    if walk or any(p.get("variations") for p in props):
        # the claims path: every claim of the feature, whatever --only says, so each is planned knowing the rest
        inp["feature_claims"] = [{"id": p["id"], "title": str(p.get("title") or "").strip()[:60], "statement": str(p["statement"]).strip()}
                                 for p in feature["properties"]
                                 if isinstance(p, dict) and isinstance(p.get("id"), str) and re.fullmatch(r"[A-Za-z0-9_-]{1,32}", p["id"])
                                 and str(p.get("statement") or "").strip()][:40]
    (run_dir / "sent.json").write_text(json.dumps(inp, indent=2))      # exactly what left this machine at the start
    rid = _send(f"{service}/v1/runs", {"input": inp, "client": VERSION}, token=token)["run"]
    runner, results = Runner(env, props, run_dir, headed=headed, say=say, walkthrough=bool(walk)), []
    try:
        while True:
            out = _send(f"{service}/v1/runs/{rid}/results", {"results": results}, token=token)
            results = []
            if "report" in out:
                break
            for act in out.get("actions", []):
                results.append({"id": act.get("id") if isinstance(act, dict) else None, **runner.execute(act)})
    except BaseException:
        try:
            _send(f"{service}/v1/runs/{rid}", method="DELETE", timeout=10, token=token)
        except ServiceError:
            pass
        raise
    finally:
        runner.close()
    rep = out["report"] or {}
    properties = []
    for p in rep.get("properties") or []:
        # The table and the verdict come from the service; the evidence in each row comes from this machine.
        for c in p.get("cases") or []:
            c["result"] = runner.reports.get(f"{p['id']}/{c['id']}") or c.get("result") or {}
        p["discovery"] = runner.reports.get(f"{p['id']}/discovery") or {}
        properties.append(p)
        note = {"holds": f"{p.get('held')} of {p.get('planned')} cases", "fails": f"broke: {', '.join(p.get('broke') or [])}",
                "needs_decision": f"your call on: {', '.join(p.get('declined') or [])}",
                "cant_tell": f"not run: {', '.join(p.get('not_run') or []) or 'no plan'}",
                "planned": f"{p.get('planned')} cases planned, none carried out"}.get(p.get("verdict"), "")
        say(f"  {p['id']} {p.get('verdict')} ({note}): {p['statement'][:90]}")
    for p in skipped:
        properties.append({"id": p["id"], "statement": p["statement"], "example": p.get("example", ""),
                           "checked_against": p.get("checked_against") or "", "verdict": "not_run", "cases": [],
                           "no_plan_because": "only the walkthrough was run"})
    wt = None
    if walk:
        wt = rep.get("walkthrough") if isinstance(rep.get("walkthrough"), dict) else {
            "plan": None, "discovery_meta": {}, "case": None,
            "no_plan_because": f"the run did not finish ({rep.get('error')})" if rep.get("error") else "the service sent no walkthrough"}
        if isinstance(wt.get("case"), dict):            # the evidence comes from this machine, as for every case
            wt["case"]["result"] = runner.reports.get("walkthrough/tour") or wt["case"].get("result") or {}
        wt["discovery"] = runner.reports.get("walkthrough/discovery") or {}
        tour = (wt.get("case") or {}).get("result") or {}
        say(f"  walkthrough {tour.get('outcome') or 'not run'}" + (f": {wt.get('no_plan_because')}" if not wt.get("case") else ""))
    stopped = rep.get("stopped_by_limit") if isinstance(rep.get("stopped_by_limit"), dict) else None
    if stopped:
        say(f"The run stopped early. {stopped.get('message')}")
        if stopped.get("cases_not_run"):
            say(f"  Not run for that reason: {', '.join(stopped['cases_not_run'])}")
        if stopped.get("properties_not_planned"):
            say(f"  Not planned for that reason: {', '.join(stopped['properties_not_planned'])}")
    summary = {"run": run_id, "feature": feature["feature"], "feature_file": feature_name, "goal": feature.get("goal", ""), "env": env,
               "service": {"url": service, "run": rid, **(rep.get("service") or {})}, "order": rep.get("order"),
               "aborted": rep.get("aborted", False), "error": rep.get("error"), "not_checked": unchecked, "viewport": size,
               "properties": properties, "walkthrough": wt, "sources": sources, "context": context}
    if stopped:
        summary["stopped_by_limit"] = stopped
    if approval_state is not None:
        summary["approval"] = approval_state
    (run_dir / "run.json").write_text(json.dumps(summary, indent=2))
    return run_dir
