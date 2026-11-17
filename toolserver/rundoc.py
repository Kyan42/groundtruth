"""The run document: one finished run, shaped for the results store and the dashboard.

Built on this machine from a run folder: run.json plus each case's receipts, screenshots and films. The dashboard
renders from this document alone, so it never needs to know the verifier's own files.

What the document never carries: the repo's path, the process id, the process's command line, the machine's user
name, or any absolute path. Measurement output is cut down to its readings for that reason.

  build(run_dir, repo=None, project=None) -> (document, files)
      files: [{"path": "p3/c1/clip-1.webm", "kind": "clip" | "shot", "local": Path}], to be sent beside the document
"""
import json
import re
from datetime import datetime
from pathlib import Path

import plainwords

SCHEMA = 1
CASE_OUTCOMES = ("holds", "breaks", "declined", "outside", "nothing_there", "cant_run", "planned")
VERDICTS = ("holds", "fails", "needs_decision", "cant_tell", "planned", "not_run")
APPROVAL = ("approved", "changed", "new")


def _when(run_id: str):
    try:
        return datetime.strptime(run_id, "%Y%m%d-%H%M%S").isoformat()
    except ValueError:
        return None


def _reading(tool: str, text: str) -> str:
    """A measurement with everything that identifies the machine taken out."""
    text = str(text or "")
    if tool == "gpu_check":
        return re.sub(r"^pid \d+:\s*", "", text.strip())
    if tool == "process_info":          # "pid ppid cpu rss command..." per line: keep cpu and memory only
        out = []
        for line in text.strip().splitlines():
            m = re.match(r"\s*\d+\s+\d+\s+([\d.]+)\s+(\d+)\s", line + " ")
            if m:
                out.append(f"CPU {m.group(1)}%, memory {m.group(2)} KB")
        return "; ".join(out) or "no process found"
    if tool == "net_connections":
        n = max(0, len([l for l in text.strip().splitlines() if l.strip()]) - 1)
        return f"{n} open connection(s)"
    if tool == "cpu_sample":
        return re.sub(r"pid \d+:?\s*", "", text.strip())[:200]
    return text.strip()[:600]


def _baseline(text):
    """The harness's own before-measurement: "tool:\\n<output>" blocks -> readings."""
    if not text:
        return []
    out, blocks = [], re.split(r"(?m)^(process_info|gpu_check|net_connections|cpu_sample):\n", str(text))
    for i in range(1, len(blocks) - 1, 2):
        out.append({"at": None, "what": plainwords.words({"tool": blocks[i]}), "text": _reading(blocks[i], blocks[i + 1])})
    return out


def _request(r):
    a, res = r.get("args") or {}, str(r.get("result", ""))
    status, _, body = res.partition("\n")
    return {"method": a.get("method", "GET"), "path": a.get("path", ""), "body": str(a.get("body") or "")[:1000],
            "status": int(status) if status.strip().isdigit() else None, "response": (body if status.strip().isdigit() else res)[:2000]}


def _compare_step(r, at):
    """A comparison the verifier recorded, timed from the moment it was made (when its outline was drawn), not from
    when the receipt was written. None when the attempt failed: a failed attempt is not a step."""
    c = r.get("compared")
    if plainwords.failed(r) or not isinstance(c, dict):
        return None
    t = c.get("t") if isinstance(c.get("t"), (int, float)) else r.get("t")
    where = at(t) if isinstance(t, (int, float)) else None
    text = lambda k, n: str(c.get(k) or "")[:n]        # noqa: E731
    return {"clip": where[0] if where else None, "at": where[1] if where else None, "kind": "compare", "ok": True,
            "words": text("what", 200), "what": text("what", 200), "expected": text("expected", 200), "found": text("found", 200),
            "matches": c.get("matches") is True,
            "element": str(c["element"])[:300] if isinstance(c.get("element"), str) else None,
            "claim": c.get("claim") if isinstance(c.get("claim"), str) else None,
            "say": str(c.get("say") or "")[:110]}


def _situation_step(r, at):
    """Whether the case started in its variation's situation, written as a comparison (so the pages show it as one)
    with situation: true. None when the attempt failed."""
    c = r.get("situation")
    if plainwords.failed(r) or not isinstance(c, dict):
        return None
    t = c.get("t") if isinstance(c.get("t"), (int, float)) else r.get("t")
    where = at(t) if isinstance(t, (int, float)) else None
    return {"clip": where[0] if where else None, "at": where[1] if where else None, "kind": "compare", "ok": True,
            "words": "The starting situation", "what": "The starting situation", "expected": str(c.get("expected") or "")[:200],
            "found": str(c.get("found") or "")[:200], "matches": c.get("matches") is True, "element": None, "claim": None,
            "say": "", "situation": True}


def _say_step(r, at):
    """A spoken line of a walkthrough, timed from the moment it was said (when its outline was drawn). None when the
    attempt failed."""
    c = r.get("said")
    if plainwords.failed(r) or not isinstance(c, dict):
        return None
    t = c.get("t") if isinstance(c.get("t"), (int, float)) else r.get("t")
    where = at(t) if isinstance(t, (int, float)) else None
    text = str(c.get("text") or "")[:110]
    return {"clip": where[0] if where else None, "at": where[1] if where else None, "kind": "say", "ok": True,
            "words": text, "text": text, "element": str(c["element"])[:300] if isinstance(c.get("element"), str) else None,
            "claim": c.get("claim") if isinstance(c.get("claim"), str) else None}


def _case(run_dir: Path, pid: str, case: dict, variations=None) -> tuple[dict, list]:
    res = case.get("result") or {}
    unit, rel = run_dir / pid / case["id"], f"{pid}/{case['id']}"
    meta = res.get("meta") or {}
    clips = [{"file": f"{rel}/{c['file']}", "started": c.get("started")} for c in res.get("clips") or []
             if isinstance(c, dict) and re.fullmatch(r"clip-\d+\.webm", str(c.get("file"))) and (unit / c["file"]).is_file()]
    files = [{"path": c["file"], "kind": "clip", "local": run_dir / c["file"]} for c in clips]

    def at(t):                       # seconds into the film that was recording at that moment
        best = None
        for i, c in enumerate(clips):
            if c["started"] is not None and t >= c["started"] - 0.05:
                best = (i, round(max(0.0, t - c["started"]), 2))
        return best

    receipts = []
    f = unit / "receipts.jsonl"
    if f.is_file():
        for line in f.read_text(errors="replace").splitlines():
            try:
                receipts.append(json.loads(line))
            except ValueError:
                pass
    steps, requests, readings, media, shots = [], [], [], [], []
    labels = {}
    for r in receipts:
        tool, t = r.get("tool"), r.get("t")
        if tool in ("reset_data", None):
            continue
        if tool == "situation":
            step = _situation_step(r, at)
            if step:
                steps.append(step)
            continue
        if tool in ("compare", "say"):
            step = (_compare_step if tool == "compare" else _say_step)(r, at)
            if step:
                steps.append(step)
            continue
        where = at(t) if isinstance(t, (int, float)) else None
        ok = not plainwords.failed(r)
        k = plainwords.kind(tool)
        step = {"clip": where[0] if where else None, "at": where[1] if where else None, "kind": k,
                "words": plainwords.words(r), "ok": ok}
        if not ok:
            step["problem"] = re.sub(r"\s+", " ", str(r.get("result", "")))[:200]
        if k == "request":
            req = {**_request(r), "at": step["at"]}
            requests.append(req)
            step["status"] = req["status"]
        elif tool == "media":
            media.append({"at": step["at"], "text": str(r.get("result", ""))[:1500]})
        elif k == "reading":
            readings.append({"at": step["at"], "what": step["words"], "text": _reading(tool, r.get("result"))})
        elif tool in ("screenshot", "look") and ok:
            m = re.search(r"shot-\d+", str(r.get("result", "")))
            if m:
                labels[m.group(0)] = ((r.get("args") or {}).get("label"), step["at"])
        if k != "other":
            steps.append(step)
    for png in sorted(unit.glob("shot-*.png"), key=lambda p: int(re.sub(r"\D", "", p.stem) or 0)):
        label, when = labels.get(png.stem, (None, None))
        shots.append({"id": png.stem, "file": f"{rel}/{png.name}", "label": label, "at": when,
                      "cited": png.stem in (res.get("screenshots") or [])})
        files.append({"path": f"{rel}/{png.name}", "kind": "shot", "local": png})
    out = {
        "id": case["id"], "way_in": case.get("entry_point"), "door": case.get("door"),
        "what": case.get("what", ""), "stands_for": case.get("represents") or "",
        "variation": next((v["what"] for v in variations or [] if v.get("id") == case["id"]), ""),
        "setting": case.get("setting") if isinstance(case.get("setting"), str) else "",
        "outcome": ("nothing_there" if res.get("nothing_there") else res.get("outcome") or "cant_run"),
        "observed": res.get("observed") or "", "question": res.get("question") or None,
        "draft_if_intended": res.get("draft_if_intended") or None,
        "seconds": meta.get("seconds"), "attempts": meta.get("attempts") or 1,
        "clips": clips, "shots": shots, "steps": steps, "requests": requests,
        "readings": _baseline(res.get("baseline")) + readings, "media": media,
        "excerpts": [{k: e.get(k) for k in ("path", "start", "end", "why", "lines")} for e in res.get("excerpts") or [] if isinstance(e, dict)],
        "checks": res.get("checks") or [], "notes": res.get("notes") or [],
        # the starting state: what plain code set up before the case began, or the data tool that was missing
        "starts_from": [{k: s.get(k) for k in ("tool", "input", "because", "returned", "error") if k in s}
                        for s in res.get("starts_from") or [] if isinstance(s, dict)],
        "data_request": res.get("data_request") if isinstance(res.get("data_request"), dict) else None,
    }
    return out, files


def _counts(cases):
    c = {k: 0 for k in CASE_OUTCOMES}
    for x in cases:
        c[x["outcome"] if x["outcome"] in c else "cant_run"] += 1
    return c


def _variations(given) -> list:
    return [{"id": str(v["id"]), "what": str(v["what"])} for v in given or [] if isinstance(v, dict) and v.get("id") and isinstance(v.get("what"), str)]


def _property(run_dir: Path, p: dict, sent=None) -> tuple[dict, list]:
    plan, trace = p.get("plan") or {}, p.get("trace") or {}
    variations = _variations(p.get("variations") or (sent or {}).get("variations"))
    cases, files = [], []
    for c in p.get("cases") or []:
        doc, fs = _case(run_dir, p["id"], c, variations)
        cases.append(doc)
        files += fs
    dm = p.get("discovery_meta") or {}
    out = {
        "id": p["id"], "title": "", "your_words": "", "statement": p.get("statement", ""), "example": p.get("example") or "", "group": "new",
        "checked_against": p.get("checked_against") or "", "approval": None, "approval_changed": [],
        "verdict": p.get("verdict") or "cant_tell", "kind": plan.get("kind"), "searched": bool(plan.get("traced")),
        "from_claim": bool(plan.get("from_claim")), "variations": variations,
        "read_as": {k: plan.get(k) or "" for k in ("applies_to", "action", "compares")},
        "how_found": plan.get("how_found") or "", "not_covered": plan.get("not_covered") or "",
        "no_plan_because": p.get("no_plan_because"),
        "ways_in": [{"id": e.get("id"), "door": e.get("door"), "status": e.get("status") or "found", "where": e.get("where", ""),
                     "page": e.get("page"), "route": e.get("route"), "file": e.get("file")} for e in plan.get("entry_points") or []],
        "left_out": [{"file": x.get("file"), "why": x.get("why"), "because": x.get("because")} for x in trace.get("excluded") or [] if isinstance(x, dict)],
        "search_notes": [str(n) for n in trace.get("notes") or []],
        "counts": _counts(cases),
        "seconds": round((dm.get("seconds") or 0) + sum(c["seconds"] or 0 for c in cases), 1),
        "cost_usd": round((dm.get("cost_usd") or 0) + sum(((c.get("result") or {}).get("meta") or {}).get("cost_usd") or 0 for c in p.get("cases") or []), 4),
        "cases": cases,
    }
    return out, files


def _feature_spec(repo: Path, feature: str, file_name=None) -> dict:
    """The feature file a run was made from. A file's name and the `feature:` inside it can differ, so look for the
    file the run names, then one named after the feature, then any file that declares that feature."""
    import yaml
    folder = repo / ".groundtruth" / "features"
    for name in (file_name, feature):
        f = folder / f"{name}.yml" if isinstance(name, str) and name else None
        if f is not None and f.is_file():
            return yaml.safe_load(f.read_text()) or {}
    for f in sorted(folder.glob("*.yml")):
        try:
            spec = yaml.safe_load(f.read_text()) or {}
        except Exception:  # noqa: BLE001
            continue
        if isinstance(spec, dict) and spec.get("feature") == feature:
            return spec
    raise FileNotFoundError(feature)


def _viewport(vp) -> dict:
    """The size of the verifier's window in this run; 900 by 700 for runs made before it could be set."""
    if isinstance(vp, dict) and all(isinstance(vp.get(k), int) and not isinstance(vp.get(k), bool) for k in ("width", "height")):
        return {"width": vp["width"], "height": vp["height"]}
    return {"width": 900, "height": 700}


def _walkthrough(run_dir: Path, w) -> tuple:
    """The walkthrough as a case like any other, with id "tour"; None when none was asked for. One that was asked for
    but could not be planned or started is still there, as a case that could not run, with the reason."""
    if not isinstance(w, dict):
        return None, [], 0.0, 0.0
    case = w.get("case") if isinstance(w.get("case"), dict) else None
    if case is None:
        reason = w.get("no_plan_because") or "it could not be planned"
        case = {"id": "tour", "result": {"outcome": "cant_run", "observed": f"The walkthrough could not be run: {reason}"}}
    doc, files = _case(run_dir, "walkthrough", {**case, "id": "tour"})
    doc["variation"] = ""
    dm = w.get("discovery_meta") if isinstance(w.get("discovery_meta"), dict) else {}
    cost = (dm.get("cost_usd") or 0) + (((case.get("result") or {}).get("meta") or {}).get("cost_usd") or 0)
    return doc, files, (dm.get("seconds") or 0) + (doc["seconds"] or 0), cost


def _draft_changes(approval):
    """What changed between the claims as first drafted and as approved, from the approval record as it was when the
    run started: None when no draft was kept (or there was no record), [] when nothing changed."""
    if not isinstance(approval, dict) or not isinstance(approval.get("draft_changes"), list):
        return None
    keys = ("id", "change", "part", "before", "after")
    return [{k: c.get(k) for k in keys} for c in approval["draft_changes"] if isinstance(c, dict)]


def _setup(log: Path):
    """How setting up went, from <app>/.groundtruth/log.jsonl: when the first command ran, how many commands came
    before the first verify, and which of those failed. None when there is no log."""
    try:
        lines = [json.loads(x) for x in log.read_text().splitlines() if x.strip()]
    except (OSError, ValueError):
        return None
    lines = [x for x in lines if isinstance(x, dict) and x.get("command")]
    if not lines:
        return None
    first_run = next((i for i, x in enumerate(lines) if x["command"] == "verify"), len(lines))
    before = lines[:first_run]
    return {"first_command_at": lines[0].get("at"), "commands_before_first_run": len(before),
            "failed_commands": [{"command": str(x["command"]), "error": str(x.get("error") or "")[:200]} for x in before if x.get("ok") is False]}


def build(run_dir: Path, repo: Path | None = None, project: str | None = None):
    run_dir = Path(run_dir)
    run = json.loads((run_dir / "run.json").read_text())
    env = run.get("env") or {}
    facts = env.get("facts") or {}
    # A property's short title is the owner's, as it was sent with the run. The verifier never saw or judged by it.
    try:
        sent = {sp.get("id"): sp for sp in json.loads((run_dir / "sent.json").read_text()).get("properties") or [] if isinstance(sp, dict)}
    except Exception:  # noqa: BLE001 -- older runs have no sent.json, or none with titles
        sent = {}
    props, files = [], []
    for p in run.get("properties") or []:
        doc, fs = _property(run_dir, p, sent.get(p.get("id")))
        props.append(doc)
        files += fs
    feature = run.get("feature", "")
    for doc in props:
        for key in ("title", "your_words"):
            said = (sent.get(doc["id"]) or {}).get(key)
            doc[key] = said.strip() if isinstance(said, str) else ""
    # Every property the owner wrote gets a place, run or not: a card for each is the point.
    if repo is not None:
        try:
            import yaml
            spec = _feature_spec(Path(repo), feature, run.get("feature_file"))
            have = {p["id"]: p for p in props}
            ordered = []
            for sp in spec.get("properties") or []:
                if not isinstance(sp, dict) or "id" not in sp:
                    continue
                spec_title = sp["title"].strip() if isinstance(sp.get("title"), str) else ""
                spec_words = sp["your_words"].strip() if isinstance(sp.get("your_words"), str) else ""
                if sp["id"] in have:                              # a run made before the file had these takes today's
                    have[sp["id"]]["title"] = have[sp["id"]]["title"] or spec_title
                    have[sp["id"]]["your_words"] = have[sp["id"]]["your_words"] or spec_words
                ordered.append(have.pop(sp["id"], None) or {
                    "id": sp["id"], "title": spec_title, "your_words": spec_words, "statement": str(sp.get("statement", "")), "example": str(sp.get("example") or ""),
                    "checked_against": str(sp.get("checked_against") or ""), "approval": None, "approval_changed": [],
                    "group": "new", "verdict": "not_run", "kind": None, "searched": False, "from_claim": False, "variations": [],
                    "read_as": {"applies_to": "", "action": "", "compares": ""}, "how_found": "", "not_covered": "",
                    "no_plan_because": None, "ways_in": [], "left_out": [], "search_notes": [],
                    "counts": {k: 0 for k in CASE_OUTCOMES}, "seconds": 0, "cost_usd": 0, "cases": []})
            props = ordered + list(have.values())
        except Exception:  # noqa: BLE001 -- the feature file is optional here; the run's own properties still stand
            pass
    # Whether each property still read as the owner approved it, as compared when the run started (never recomputed
    # from today's file). No record at the time: null for every property.
    approved = ((run.get("approval") or {}).get("properties") or {}) if isinstance(run.get("approval"), dict) else {}
    for doc in props:
        a = approved.get(doc["id"]) if isinstance(approved.get(doc["id"]), dict) else {}
        doc["approval"] = a.get("state") if a.get("state") in APPROVAL else None
        doc["approval_changed"] = [str(x) for x in a.get("changed") or []] if doc["approval"] == "changed" else []
    walk, walk_files, walk_seconds, walk_cost = _walkthrough(run_dir, run.get("walkthrough"))
    files += walk_files
    sources = run.get("sources") if isinstance(run.get("sources"), dict) else {}
    for doc in props:                   # where each claim came from, as the file said when the run started
        src = sources.get(doc["id"])
        doc["source"] = {k: str(src.get(k) or "") for k in ("kind", "ref", "quote")} if isinstance(src, dict) and src.get("kind") else None
    verdicts = {v: sum(p["verdict"] == v for p in props) for v in VERDICTS}
    case_counts = {k: sum(p["counts"][k] for p in props) for k in CASE_OUTCOMES}
    name = project or (Path(repo).name if repo is not None else (Path(env["repo"]).name if env.get("repo") else "project"))
    doc = {
        "schema": SCHEMA, "project": name, "feature": feature, "goal": run.get("goal") or "",
        "run": run.get("run"), "ran_at": _when(str(run.get("run"))),
        "ran_against": {"url": env.get("url"), "commit": facts.get("commit"),
                        "uncommitted_changes": bool(facts.get("uncommitted_changes")), "platform": facts.get("platform"),
                        "process_started": facts.get("started"), "registered_at": facts.get("registered_at"),
                        "could_measure": "pid" in env, "could_read_code": "repo" in env, "had_reset": bool(env.get("reset")),
                        "viewport": _viewport(run.get("viewport") or env.get("viewport"))},
        "verifier": {k: (run.get("service") or {}).get(k) for k in ("version", "model")},
        "error": run.get("error"), "aborted": bool(run.get("aborted")),
        "totals": {"properties": verdicts, "cases": case_counts,
                   "seconds": round(sum(p["seconds"] for p in props) + walk_seconds, 1),
                   "cost_usd": round(sum(p["cost_usd"] for p in props) + walk_cost, 4)},
        "properties": props,
        "not_checked": [str(x)[:200] for x in run.get("not_checked") or [] if isinstance(x, str)][:12],
        "context": run.get("context") if isinstance(run.get("context"), dict) else None,
        "draft_changes": _draft_changes(run.get("approval")),
        "setup": _setup(run_dir.parent.parent / "log.jsonl"),
        "walkthrough": walk,
    }
    return doc, files
