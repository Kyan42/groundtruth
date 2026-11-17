"""Helpers and checks for the properties gate (gates/props_gate.py).

Every check is a pure function over a run folder (run.json plus receipts), so the streams and
`props_gate.py --check-only` apply exactly the same code. Paths are module attributes (props_lib.X, apps.X) so a
dry run can point them elsewhere. No import-time side effects.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gates import apps  # noqa: E402

RESULTS_DIR = apps.REPO / "gates" / "results"
CBAY_RESET = f"{apps.NODE_BIN}/node scripts/seed.mjs --reset"

BROWSER_TOOLS = {"observe", "navigate", "click", "fill", "read_attribute", "click_and_watch", "wait", "screenshot"}
SOURCE_TOOLS = {"grep", "read_lines", "find", "list_files", "match_each"}
ACTION_TOOLS = {"click", "click_and_watch", "fill", "navigate", "http_request"}
REPEAT = re.compile(r"again|second|twice|already|two times|repeat", re.I)
TITLES = {"diving helmet": "l1", "ship in a bottle": "l2", "kraken": "l3", "sextant": "l4",
          "message in a bottle": "l5", "pirate chest": "l6", "swordfish": "l7", "lamp lens": "l8"}
LID = re.compile(r"\bl[1-8]\b", re.I)
SOLD_P2 = re.compile(r"\bl6\b|pirate chest|sold", re.I)
SOLD_P0 = re.compile(r"\bl6\b|pirate chest|sold[- ]out|sold out", re.I)
ADD_TO_CART = re.compile(r"add[\s-]*to[\s-]*cart", re.I)


# ---------------------------------------------------------------- recording
class Checks:
    """Collects checks for one stream and prints one PASS/FAIL line per check as it is made."""

    def __init__(self, stream: str):
        self.stream, self.items, self.notes = stream, [], []

    def add(self, label, prop, name, ok, detail=""):
        detail = " ".join(str(detail).split())[:240]
        self.items.append({"stream": self.stream, "label": label, "property": prop, "check": name,
                           "ok": bool(ok), "detail": detail})
        print(f"{'PASS' if ok else 'FAIL'}  {self.stream}  {label}  {prop}  {name}  {detail}", flush=True)
        return bool(ok)

    def note(self, label, prop, text):
        """A line for the reader, not a check: printed as NOTE and kept apart from the checks."""
        text = " ".join(str(text).split())
        self.notes.append({"stream": self.stream, "label": label, "property": prop, "note": text})
        print(f"NOTE  {self.stream}  {label}  {prop}  {text}", flush=True)

    @property
    def passed(self):
        return bool(self.items) and all(c["ok"] for c in self.items)


# ---------------------------------------------------------------- driving apps and the CLI
def start_next(app_dir: Path, port: int):
    """A cbay copy from its existing production build; ready when /api/health answers 200."""
    apps.free_port(port)
    env = dict(os.environ, PATH=apps.NODE_BIN + os.pathsep + os.environ.get("PATH", ""))
    proc = subprocess.Popen(["node", "node_modules/next/dist/bin/next", "start", "--port", str(port)], cwd=app_dir,
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    return apps._wait_ok(proc, f"http://127.0.0.1:{port}/api/health", 60, f"next app {Path(app_dir).name} on {port}")


def run_cli(args, timeout=120) -> str:
    return apps._run_cli([str(a) for a in args], timeout=timeout)


def register(app_dir: Path, url: str, reset: str | None = None, routes: str | None = None, no_pid: bool = False) -> str:
    args = ["register", str(app_dir), "--url", url] + (["--no-pid"] if no_pid else [])
    if reset:
        args += ["--reset", reset]
    if routes:
        args += ["--routes", routes]
    return run_cli(args, timeout=60)


def register_cbay(app_dir: Path, port: int) -> str:
    return register(app_dir, f"http://127.0.0.1:{port}", reset=CBAY_RESET, routes="next")


def verify(app_dir: Path, feature: str, only: str | None = None, plan_only: bool = False, timeout: int = 1800) -> Path:
    args = ["verify", str(app_dir), feature] + (["--only", only] if only else []) + (["--plan-only"] if plan_only else [])
    out = run_cli(args, timeout=timeout)
    m = re.search(r"^Run (\S+?):", out, re.M)
    if not m:
        raise RuntimeError(f"no 'Run <id>:' line in verify output:\n{out[-1500:]}")
    run_dir = Path(app_dir).resolve() / ".groundtruth" / "runs" / m.group(1)
    if not (run_dir / "run.json").is_file():
        raise RuntimeError(f"{run_dir}/run.json does not exist\n{out[-1500:]}")
    return run_dir


def decide(app_dir: Path, feature: str, prop: str, case: str, answer: str) -> str:
    return run_cli(["decide", str(app_dir), feature, prop, case, answer], timeout=60)


def feature_text(feature: str, goal: str, props: list[dict]) -> str:
    """The original layout (properties last, items at 2-space indent, trailing newline), because `decide` appends
    raw text at that indent."""
    lines = [f"feature: {feature}", f"goal: {json.dumps(goal)}", "properties:"]
    for p in props:
        lines += [f"  - id: {p['id']}", f"    statement: {json.dumps(p['statement'])}"]
    return "\n".join(lines) + "\n"


def write_feature(app_dir: Path, name: str, feature: str, src_yml: Path, ids: list[str]) -> Path:
    import yaml
    src = yaml.safe_load(Path(src_yml).read_text())
    props = [p for p in src.get("properties") or [] if p.get("id") in ids]
    if [p["id"] for p in props] != ids:
        raise RuntimeError(f"{src_yml} lacks some of {ids}")
    f = Path(app_dir) / ".groundtruth" / "features" / f"{name}.yml"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(feature_text(feature, src.get("goal") or "", props))
    return f


# ---------------------------------------------------------------- reading runs
def load_run(run_dir: Path) -> dict:
    try:
        return json.loads((Path(run_dir) / "run.json").read_text())
    except Exception as e:  # noqa: BLE001
        return {"_load_error": f"{type(e).__name__}: {e}", "properties": []}


def props_of(run: dict) -> dict:
    return {p.get("id"): p for p in run.get("properties") or [] if isinstance(p, dict)}


def plan_of(p: dict) -> dict:
    return p.get("plan") if isinstance(p.get("plan"), dict) else {}


def eps_of(p: dict) -> list:
    return [e for e in plan_of(p).get("entry_points") or [] if isinstance(e, dict)]


def browser_eps(p: dict) -> list:
    return [e for e in eps_of(p) if e.get("door") == "browser"]


def counted_browser_eps(p: dict) -> tuple[list, list, list]:
    """(browser entry points that count as ways in: status "found", or no status in an untraced plan;
        those marked "to check"; those "flagged")."""
    br = browser_eps(p)
    return ([e for e in br if e.get("status") in (None, "found")], [e for e in br if e.get("status") == "to check"],
            [e for e in br if e.get("status") == "flagged"])


def side_eps_note(to_check: list, flagged: list) -> str:
    out = [f"{n} {k}: " + " | ".join(f"{e.get('id')}: {str(e.get('where'))[:50]}" for e in es)
           for k, es in (("to check", to_check), ("flagged", flagged)) for n in [len(es)] if es]
    return ("; also " + "; ".join(out)) if out else ""


def plan_cases(p: dict) -> list:
    return [c for c in plan_of(p).get("cases") or [] if isinstance(c, dict)]


def run_cases(p: dict) -> list:
    """The carried-out table if present, else the plan's cases."""
    cs = [c for c in p.get("cases") or [] if isinstance(c, dict)]
    return cs or plan_cases(p)


def door_of(p: dict, c: dict):
    if c.get("door"):
        return c["door"]
    return next((e.get("door") for e in eps_of(p) if e.get("id") == c.get("entry_point")), None)


def outcome(c: dict):
    r = c.get("result") if isinstance(c.get("result"), dict) else {}
    return r.get("outcome")


def what(c: dict) -> str:
    return str(c.get("what") or "")


def receipts(run_dir: Path, pid: str, cid: str) -> list:
    f = Path(run_dir) / str(pid) / str(cid) / "receipts.jsonl"
    out = []
    if f.is_file():
        for line in f.read_text().splitlines():
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(d, dict):
                out.append(d)
    return out


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _ids(v) -> list:
    return list(v) if isinstance(v, list) else []


def usage(run_dirs) -> dict:
    """Summed over every discovery and case conversation in the given runs (discovery_meta and discovery.meta are the
    same discovery: counted once). A discovery counts discovery_meta.conversations conversations (default 1: the old
    single-conversation discovery) and discovery_meta.labels labelling calls (default 0), reported as their own number
    `labels`; its cost_usd already includes the labelling. A case that took two conversations (meta.attempts == 2)
    counts as two. Seconds are summed only where they are a number below 3600; any other value is counted in
    seconds_ignored."""
    n, labels, cost, secs, second_attempts, ignored = 0, 0, 0.0, 0.0, 0, 0
    for rd in run_dirs:
        for p in props_of(load_run(rd)).values():
            metas = [("discovery", p.get("discovery_meta") or (p.get("discovery") or {}).get("meta"))]
            metas += [("case", c["result"].get("meta")) for c in p.get("cases") or []
                      if isinstance(c, dict) and isinstance(c.get("result"), dict)]
            for role, m in metas:
                if not (isinstance(m, dict) and m):
                    continue
                if role == "discovery":
                    conv, lab = m.get("conversations"), m.get("labels")
                    n += int(conv) if _num(conv) and conv >= 0 else 1
                    labels += int(lab) if _num(lab) and lab >= 0 else 0
                else:
                    att = m.get("attempts")
                    n += att if att in (1, 2) and not isinstance(att, bool) else 1
                    second_attempts += att == 2 and not isinstance(att, bool)
                if _num(m.get("cost_usd")):
                    cost += float(m["cost_usd"])
                s = m.get("seconds")
                if _num(s) and 0 <= s < 3600:
                    secs += float(s)
                elif s is not None:
                    ignored += 1
    return {"conversations": n, "labels": labels, "second_attempts": second_attempts, "cost_usd": round(cost, 4),
            "seconds": round(secs, 1), "seconds_ignored": ignored}


# ---------------------------------------------------------------- generic checks
def check_run_ok(chk: Checks, label: str, run: dict, expected_ids: list[str]) -> dict:
    """The run itself: loaded, no service error, not aborted, every expected property present. Returns props by id."""
    problems = [x for x in (run.get("_load_error"), run.get("error") and f"error: {run.get('error')}",
                            run.get("aborted") and "aborted") if x]
    chk.add(label, "-", "run_ok", not problems, "; ".join(map(str, problems)) or f"run {run.get('run')}")
    ps = props_of(run)
    for pid in expected_ids:
        if pid not in ps:
            chk.add(label, pid, "present", False, f"missing from run.json (have {sorted(map(str, ps))})")
    return ps


def check_verdict(chk, label, ps, pid, want):
    p = ps.get(pid)
    if p is None:
        return chk.add(label, pid, f"verdict_{want}", False, "property missing")
    note = (f"held {p.get('held')}/{p.get('planned')}, broke {p.get('broke')}, declined {p.get('declined')}, "
            f"outside {p.get('outside')}, not run {p.get('not_run')}")
    return chk.add(label, pid, f"verdict_{want}", p.get("verdict") == want, f"{p.get('verdict')}; {note}")


def check_kind(chk, label, ps, pid, want):
    p = ps.get(pid)
    got = plan_of(p).get("kind") if p else None
    return chk.add(label, pid, f"kind_{want}", got == want, f"plan kind {got}")


def property_checks(chk: Checks, label: str, run: dict):
    """The data shape, for every property of a run: no scope on plan cases, the counts add up, declined only where it
    can apply and always with its question and draft, and code_refs present when a repo is registered."""
    env = run.get("env") if isinstance(run.get("env"), dict) else {}
    repo_given = bool(env.get("repo")) or not env       # every gate stream registers a repo
    for pid, p in props_of(run).items():
        plan = plan_of(p)
        need_refs = repo_given and plan.get("from_claim") is not True     # a claim is planned without reading the code
        scoped = [c.get("id") for c in plan_cases(p) if "scope" in c]
        chk.add(label, pid, "no_scope_key", not scoped,
                f"plan cases with scope: {scoped}" if scoped else f"{len(plan_cases(p))} plan case(s), none with scope")
        held, planned = p.get("held"), p.get("planned")
        lists = {k: p.get(k) for k in ("broke", "declined", "outside", "not_run")}
        bad = [k for k, v in lists.items() if not isinstance(v, list)]
        if not (_num(held) and _num(planned)) or bad:
            chk.add(label, pid, "counts_add_up", False, f"held {held!r}, planned {planned!r}; not lists: {bad}")
        else:
            total = held + sum(len(v) for v in lists.values())
            chk.add(label, pid, "counts_add_up", total == planned,
                    f"held {held} + broke {len(lists['broke'])} + declined {len(lists['declined'])} + outside "
                    f"{len(lists['outside'])} + not_run {len(lists['not_run'])} = {total}; planned {planned}")
        declined = [c for c in run_cases(p) if outcome(c) == "declined"]
        if plan.get("kind") == "code":
            dids = [c.get("id") for c in declined] or _ids(p.get("declined"))
            chk.add(label, pid, "code_never_declined", not dids, f"declined code cases {dids}")
        if declined:
            missing = [c.get("id") for c in declined
                       if not (str(c["result"].get("question") or "").strip()
                               and str(c["result"].get("draft_if_intended") or "").strip())]
            chk.add(label, pid, "declined_have_question_and_draft", not missing,
                    f"missing question/draft: {missing}" if missing else f"{len(declined)} declined case(s), all complete")
        if need_refs and plan and plan.get("kind") in ("behaviour", "measurement"):
            traced = plan.get("traced") is True
            chk.add(label, pid, "was_traced", traced,
                    "plan.traced true" if traced else
                    f"fell back to the old discovery: {' '.join(str(plan.get('how_found') or '').split())[:180]!r}")
        if need_refs:
            refs = [r for r in plan.get("code_refs") or [] if isinstance(r, dict) and str(r.get("path") or "").strip()]
            chk.add(label, pid, "code_refs_present", bool(refs),
                    f"{len(refs)} code ref(s)" if refs else f"none (plan {'present' if plan else 'missing'})")


def case_checks(chk: Checks, label: str, run_dir: Path, run: dict | None = None):
    """The property-level shape checks, then how each carried-out case ran, from its receipts, its result.meta.tools
    and (measurement) its result.baseline."""
    run = run if run is not None else load_run(run_dir)
    property_checks(chk, label, run)
    for pid, p in props_of(run).items():
        kind = plan_of(p).get("kind")
        for c in p.get("cases") or []:
            if not isinstance(c, dict) or outcome(c) == "planned":
                continue
            cid, door = c.get("id"), door_of(p, c)
            res = c.get("result") if isinstance(c.get("result"), dict) else {}
            meta = res.get("meta") if isinstance(res.get("meta"), dict) else {}
            given = meta.get("tools")
            calls = [r.get("tool") for r in receipts(run_dir, pid, cid)]
            tag = f"{pid}/{cid}"
            if not isinstance(given, list):
                chk.add(label, tag, "tools_recorded", False, f"no result.meta.tools (outcome {outcome(c)})")
                given = []
            given = set(given)
            if door == "browser":
                bad = sorted({"http_request"} & given) + [t for t in calls if t == "http_request"][:1]
                chk.add(label, tag, "browser_case_no_api", not bad,
                        f"given/used http_request: {bad}" if bad else f"{len(calls)} receipts, no http_request")
            elif door == "api":
                bad = sorted(BROWSER_TOOLS & given)
                chk.add(label, tag, "api_case_no_browser", not bad, f"given {bad}" if bad else "no browser tools given")
            if kind in ("behaviour", "measurement"):
                bad = sorted(SOURCE_TOOLS & given) + sorted({t for t in calls if t in SOURCE_TOOLS})
                chk.add(label, tag, "no_source_tools", not bad, f"given/used {bad}" if bad else "none given or used")
            if kind == "measurement":
                # The service measures the process before the case starts, so this now holds for every outcome.
                first_act = next((i for i, t in enumerate(calls) if t in ACTION_TOOLS), None)
                gpu = [i for i, t in enumerate(calls) if t == "gpu_check"]
                ok = bool(gpu) and (first_act is None or gpu[0] < first_act)
                chk.add(label, tag, "gpu_check_before_action", ok,
                        f"{outcome(c)}; first gpu_check at {gpu[0] if gpu else None}, first action at {first_act}")
                base = res.get("baseline")
                chk.add(label, tag, "baseline_recorded", isinstance(base, str) and bool(base.strip()),
                        f"{len(base)} chars" if isinstance(base, str) else f"baseline {base!r}")


# ---------------------------------------------------------------- cbay plan checks
def listings_named(cases) -> tuple[set, int]:
    """Distinct listings across the cases' `what`: l-ids and titles mapped to the same id; also the literal sum."""
    ids, titles = set(), set()
    for c in cases:
        t = what(c).lower()
        ids |= {m.lower() for m in LID.findall(t)}
        titles |= {k for k in TITLES if k in t}
    return ids | {TITLES[k] for k in titles}, len(ids) + len(titles)


def cbay_plan_checks(chk: Checks, label: str, ps: dict, pids: list[str]):
    for pid in pids:
        p = ps.get(pid)
        if p is None:
            chk.add(label, pid, "plan", False, "property missing")
            continue
        plan = plan_of(p)
        if not plan:
            chk.add(label, pid, "plan", False, f"no plan ({p.get('no_plan_because')})")
            continue
        eps, cases = eps_of(p), plan_cases(p)
        br, to_check, flagged = counted_browser_eps(p)
        where = str(br[0].get("where") or "") if len(br) == 1 else ""
        chk.add(label, pid, "one_browser_entry_point_listing_add_to_cart",
                len(br) == 1 and "listing" in where.lower() and bool(ADD_TO_CART.search(where)),
                f"{len(br)} found browser entry point(s): " + " | ".join(str(e.get("where"))[:80] for e in br)
                + side_eps_note(to_check, flagged))
        paths = [str(r.get("path")) for r in plan.get("code_refs") or [] if isinstance(r, dict)]
        need = [s for s in ("lib/cart.ts", "AddToCartButton.tsx") if not any(x.endswith(s) for x in paths)]
        chk.add(label, pid, "code_refs_cart_and_button", not need, f"missing {need}; have {sorted(set(paths))}" if need else
                f"{len(paths)} refs")
        chk.add(label, pid, "case_count_1_to_12", 1 <= len(cases) <= 12, f"{len(cases)} cases")
        doors = {c.get("id"): door_of(p, c) for c in cases}
        if pid == "p1":
            api = [k for k, d in doors.items() if d == "api"]
            chk.add(label, pid, "no_api_case", not api, f"api cases {api}" if api else "none")
            brc = [k for k, d in doors.items() if d == "browser"]
            chk.add(label, pid, "two_browser_cases", len(brc) >= 2, f"browser cases {brc}")
            rep = [c.get("id") for c in cases if REPEAT.search(what(c))]
            chk.add(label, pid, "repeat_add_case", bool(rep), f"repeat-add cases {rep}")
            distinct, literal = listings_named(cases)
            chk.add(label, pid, "two_listings", len(distinct) >= 2,
                    f"distinct listings {sorted(distinct)} (literal id+title count {literal})")
        if pid == "p2":
            api_eps = [e.get("id") for e in eps if e.get("door") == "api"
                       and any(c.get("entry_point") == e.get("id") for c in cases)]
            chk.add(label, pid, "api_entry_point_with_case", bool(api_eps), f"api entry points with cases {api_eps}")
            sold = [c.get("id") for c in cases if doors.get(c.get("id")) == "browser" and SOLD_P2.search(what(c))]
            chk.add(label, pid, "browser_sold_out_case", bool(sold), f"browser cases on l6/sold {sold}")
        if pid == "p0":
            sold = [c for c in cases if SOLD_P0.search(what(c))]
            chk.add(label, pid, "sold_out_case", bool(sold), f"cases on l6/sold out {[c.get('id') for c in sold]}")


# ---------------------------------------------------------------- named checksets: (chk, label, run_dir) -> None
def cs_cases(chk, label, run_dir):
    case_checks(chk, label, run_dir)


def cs_cbay_plans(chk, label, run_dir):
    """Plan checks for whichever of p0/p1/p2 the run has, plus the per-case checks (for --check-only)."""
    run = load_run(run_dir)
    ps = check_run_ok(chk, label, run, [])
    cbay_plan_checks(chk, label, ps, [k for k in ("p0", "p1", "p2") if k in ps])
    case_checks(chk, label, run_dir, run)


def cs_cbay_p1(chk, label, run_dir):
    run = load_run(run_dir)
    ps = check_run_ok(chk, label, run, ["p1"])
    cbay_plan_checks(chk, label, ps, ["p1"])
    case_checks(chk, label, run_dir, run)


def cs_cbay_full(chk, label, run_dir):
    run = load_run(run_dir)
    ps = check_run_ok(chk, label, run, ["p0", "p1", "p2"])
    cbay_plan_checks(chk, label, ps, ["p0", "p1", "p2"])
    if check_verdict(chk, label, ps, "p0", "needs_decision"):
        p = ps["p0"]
        dec, brk = _ids(p.get("declined")), _ids(p.get("broke"))
        chk.add(label, "p0", "declined_and_none_broke", bool(dec) and not brk, f"declined {dec}, broke {brk}")
        by_id = {c.get("id"): c for c in run_cases(p)}
        off = [d for d in dec if not SOLD_P2.search(what(by_id.get(d, {})))]
        chk.add(label, "p0", "declined_cases_are_sold_out", bool(dec) and not off,
                f"declined {dec}; not about l6/Pirate Chest/sold: {off}"
                + "".join(f"; {d}: {what(by_id.get(d, {}))[:70]}" for d in dec[:3]))
    if check_verdict(chk, label, ps, "p1", "holds"):
        p = ps["p1"]
        held = p.get("held") if _num(p.get("held")) else 0
        chk.add(label, "p1", "held_ge_2_none_broke_or_declined",
                held >= 2 and not _ids(p.get("broke")) and not _ids(p.get("declined")),
                f"held {p.get('held')}, broke {p.get('broke')}, declined {p.get('declined')}")
    check_verdict(chk, label, ps, "p2", "holds")
    case_checks(chk, label, run_dir, run)


def cs_cbay_plan_only(chk, label, run_dir):
    run = load_run(run_dir)
    ps = check_run_ok(chk, label, run, ["p0", "p1", "p2"])
    cbay_plan_checks(chk, label, ps, ["p0", "p1", "p2"])
    for pid in ("p0", "p1", "p2"):
        check_verdict(chk, label, ps, pid, "planned")
    property_checks(chk, label, run)


def cs_decide_first(chk, label, run_dir):
    run = load_run(run_dir)
    ps = check_run_ok(chk, label, run, ["p0"])
    if check_verdict(chk, label, ps, "p0", "needs_decision"):
        qs = [q for q in ps["p0"].get("questions") or [] if isinstance(q, dict)]
        chk.add(label, "p0", "has_question_with_draft", bool(qs) and bool(qs[0].get("case")) and bool(qs[0].get("draft_if_intended")),
                f"{len(qs)} question(s); first case {qs[0].get('case') if qs else None}")
    case_checks(chk, label, run_dir, run)


def cs_decide_second(chk, label, run_dir):
    run = load_run(run_dir)
    ps = check_run_ok(chk, label, run, ["p0", "p0-1"])
    check_verdict(chk, label, ps, "p0", "holds")
    check_verdict(chk, label, ps, "p0-1", "holds")
    case_checks(chk, label, run_dir, run)


def cs_qtycap(chk, label, run_dir):
    run = load_run(run_dir)
    ps = check_run_ok(chk, label, run, ["p1"])
    if check_verdict(chk, label, ps, "p1", "fails"):
        p = ps["p1"]
        by_id = {c.get("id"): c for c in run_cases(p)}
        broke = _ids(p.get("broke"))
        not_repeat = [b for b in broke if not REPEAT.search(what(by_id.get(b, {})))]
        chk.add(label, "p1", "broken_cases_are_repeat_adds", bool(broke) and not not_repeat,
                f"broke {broke}; not repeat-adds {not_repeat}" + "".join(f"; {b}: {what(by_id.get(b, {}))[:70]}" for b in broke[:3]))
    p = ps.get("p1") or {}
    held = [c.get("id") for c in run_cases(p) if outcome(c) == "holds"]
    chk.add(label, "p1", "some_case_holds", bool(held), f"held {held}")
    case_checks(chk, label, run_dir, run)


def _two_doors(chk, label, ps):
    """Returns (listing entry point id, quick entry point id), or Nones."""
    p = ps.get("p1") or {}
    br, to_check, flagged = counted_browser_eps(p)
    quick = [e for e in br if "quick" in str(e.get("where", "")).lower()]
    listing = [e for e in br if e not in quick and "listing" in str(e.get("where", "")).lower()
               and ADD_TO_CART.search(str(e.get("where", "")))]
    ok = len(br) == 2 and len(quick) == 1 and len(listing) == 1
    chk.add(label, "p1", "two_browser_entry_points_listing_and_quick", ok,
            f"{len(br)} found browser: " + " | ".join(f"{e.get('id')}: {str(e.get('where'))[:70]}" for e in br)
            + side_eps_note(to_check, flagged))
    if not ok:
        return None, None
    lid, qid = listing[0]["id"], quick[0]["id"]
    for name, eid in (("listing", lid), ("quick", qid)):
        n = [c.get("id") for c in run_cases(p) if c.get("entry_point") == eid]
        chk.add(label, "p1", f"{name}_entry_point_has_case", bool(n), f"{eid}: {n}")
    return lid, qid


def cs_twodoors(chk, label, run_dir):
    run = load_run(run_dir)
    ps = check_run_ok(chk, label, run, ["p1"])
    _two_doors(chk, label, ps)
    check_verdict(chk, label, ps, "p1", "holds")
    case_checks(chk, label, run_dir, run)


def cs_twodoors_broken(chk, label, run_dir):
    run = load_run(run_dir)
    ps = check_run_ok(chk, label, run, ["p1"])
    lid, qid = _two_doors(chk, label, ps)
    check_verdict(chk, label, ps, "p1", "fails")
    cases = run_cases(ps.get("p1") or {})
    by_id = {c.get("id"): c for c in cases}
    broke = _ids((ps.get("p1") or {}).get("broke"))
    if qid:
        off = [b for b in broke if (by_id.get(b) or {}).get("entry_point") != qid]
        chk.add(label, "p1", "broken_cases_all_quick_add", bool(broke) and not off, f"broken {broke}; not on {qid}: {off}")
        on_listing = [c for c in cases if c.get("entry_point") == lid]
        bad = [(c.get("id"), outcome(c)) for c in on_listing if outcome(c) != "holds"]
        chk.add(label, "p1", "listing_cases_all_hold", bool(on_listing) and not bad,
                f"{len(on_listing)} listing case(s); not holding {bad}")
    case_checks(chk, label, run_dir, run)


def _lipsync_kinds(chk, label, ps, pids):
    want = {"g1": "measurement", "g2": "behaviour", "g3": "code"}
    for pid in pids:
        check_kind(chk, label, ps, pid, want[pid])


def _lipsync_full(chk, label, run_dir, verdicts):
    run = load_run(run_dir)
    ps = check_run_ok(chk, label, run, ["g1", "g2", "g3"])
    for pid, v in verdicts.items():
        check_verdict(chk, label, ps, pid, v)
    _lipsync_kinds(chk, label, ps, ["g1", "g2", "g3"])
    order = run.get("order") or []
    chk.add(label, "g1", "measured_first", bool(order) and order[0] == "g1", f"order {order}")
    case_checks(chk, label, run_dir, run)


def cs_lipsync_agent(chk, label, run_dir):
    _lipsync_full(chk, label, run_dir, {"g1": "fails", "g2": "holds", "g3": "holds"})


def cs_lipsync_fixed(chk, label, run_dir):
    _lipsync_full(chk, label, run_dir, {"g1": "holds", "g2": "holds", "g3": "holds"})


def cs_lipsync_control(chk, label, run_dir):
    run = load_run(run_dir)
    ps = check_run_ok(chk, label, run, ["g3"])
    check_verdict(chk, label, ps, "g3", "fails")
    _lipsync_kinds(chk, label, ps, ["g3"])
    case_checks(chk, label, run_dir, run)


# ---------------------------------------------------------------- the unseen stream
MORE_PROPS = [
    {"id": "p3", "statement": "Watching an item raises the watchlist count in the header by one, and un-watching it "
                              "lowers the count by one."},
    {"id": "p4", "statement": "Buying an in-stock item with Buy It Now leads to an order page whose total equals the "
                              "price shown on that item's listing."},
]
UNSEEN_IDS = ["p1", "p2", "p3", "p4"]
VARIANT_VERDICTS = ("holds", "fails", "needs_decision", "cant_tell")


def cs_unseen_ref(chk, label, run_dir):
    """The clean reference: p3 and p4 on the original cbay both hold."""
    run = load_run(run_dir)
    ps = check_run_ok(chk, label, run, ["p3", "p4"])
    for pid in ("p3", "p4"):
        check_verdict(chk, label, ps, pid, "holds")
    case_checks(chk, label, run_dir, run)


def cs_unseen_variant(chk, label, run_dir):
    """A variant with an unknown planted change: generic checks only (its expected verdicts are in the sealed key, and
    the change may legitimately alter entry points or code locations). Each property must get a carried-out verdict."""
    run = load_run(run_dir)
    ps = check_run_ok(chk, label, run, UNSEEN_IDS)
    for pid in UNSEEN_IDS:
        v = (ps.get(pid) or {}).get("verdict")
        chk.add(label, pid, "has_carried_out_verdict", v in VARIANT_VERDICTS, f"verdict {v}")
    case_checks(chk, label, run_dir, run)


def variant_complete(run_dir, ids=None) -> tuple[bool, str]:
    """Whether a variant run finished well enough to be compared with the key (ids: UNSEEN_IDS by default)."""
    ids = UNSEEN_IDS if ids is None else ids
    if run_dir is None:
        return False, "the run did not produce a run folder"
    run = load_run(run_dir)
    if run.get("_load_error") or run.get("error") or run.get("aborted"):
        return False, f"run problem: {run.get('_load_error') or run.get('error') or 'aborted'}"
    ps = props_of(run)
    bad = [pid for pid in ids if (ps.get(pid) or {}).get("verdict") not in VARIANT_VERDICTS]
    return (not bad), (f"no carried-out verdict for {bad}" if bad else "complete")


def open_key(path: Path) -> dict:
    """The only place the sealed answer file is read."""
    return json.loads(Path(path).read_text())


# ---------------------------------------------------------------- the ways stream
WAYS_IDS = ["p1"]
WAYS_SCORE_CHECKS = ("ways_in_found", "no_invented_ways_in", "every_way_in_has_a_case", "flagged_as_required",
                     "decoy_not_a_way_in", "verdict_matches_key", "was_traced")
_PARAM = re.compile(r"^(:[^/]+|\[[^/]+\])$")


def norm_page(page) -> str | None:
    """A page path for comparison: no query, no trailing slash, every :name or [name] segment the same parameter."""
    if not isinstance(page, str) or not page.strip():
        return None
    path = page.strip().split("?")[0].split("#")[0]
    segs = [":param" if _PARAM.match(x) else x for x in path.split("/") if x]
    return "/" + "/".join(segs)


def norm_file(path) -> str:
    p = str(path or "").strip()
    while p.startswith("./"):
        p = p[2:]
    return p


def ep_matches(ep: dict, way: dict) -> bool:
    """A plan entry point matches a key way in: same page (normalised) and one of the key's keywords in its `where`."""
    page = norm_page(ep.get("page"))
    if page is None or page != norm_page(way.get("page")):
        return False
    where = " ".join(str(ep.get("where") or "").lower().split())
    return any(" ".join(str(k).lower().split()) in where for k in way.get("keywords") or [] if str(k).strip())


def _ep_brief(e: dict) -> dict:
    return {"id": e.get("id"), "status": e.get("status"), "page": e.get("page"), "where": e.get("where"), "file": e.get("file")}


def score_ways(chk: Checks, label: str, run_dir, entry) -> dict:
    """Scores one ways copy against its key entry; adds the WAYS_SCORE_CHECKS (and broke_on_the_right_way_in when the
    property fails on a named way in) and returns the score row. Pure: reads only the run folder and `entry`."""
    run = load_run(run_dir)
    p = props_of(run).get("p1") or {}
    entry = entry if isinstance(entry, dict) else {}
    ways = [w for w in entry.get("ways_in") or [] if isinstance(w, dict)]
    br = browser_eps(p)
    found_eps, to_check, flagged = counted_browser_eps(p)
    row = {"kind": entry.get("kind"), "key_ways_in": len(ways)}
    if not entry:
        chk.add(label, "p1", "key_entry", False, "the key has no entry for this copy")

    # ways_in_found
    hit = {w.get("label"): [e.get("id") for e in br if ep_matches(e, w)] for w in ways}
    missed = [w.get("label") for w in ways if not hit[w.get("label")]]
    row.update(found=len(ways) - len(missed), missed=len(missed), missed_labels=missed)
    chk.add(label, "p1", "ways_in_found", bool(ways) and not missed,
            (f"missed {missed}; " if missed else "") + "; ".join(f"{w.get('label')} <- {hit[w.get('label')]}" for w in ways)
            if ways else "the key lists no ways in")

    # no_invented_ways_in
    matches_any = lambda e: any(ep_matches(e, w) for w in ways)  # noqa: E731
    invented = [_ep_brief(e) for e in found_eps if not matches_any(e)]
    side = [e for e in to_check + flagged if not matches_any(e)]
    row.update(invented=len(invented), invented_eps=invented, to_check=len(to_check), flagged=len(flagged),
               unmatched_side=len(side))
    chk.add(label, "p1", "no_invented_ways_in", not invented,
            (f"invented: " + " | ".join(f"{e['id']} {e['page']} {str(e['where'])[:50]} ({e['file']})" for e in invented) + "; "
             if invented else f"{len(found_eps)} found browser entry point(s), all in the key; ")
            + f"{len(side)} 'to check'/'flagged' entry point(s) match no key way in (not failures)")

    # every_way_in_has_a_case
    cases = [c for c in p.get("cases") or [] if isinstance(c, dict)] or plan_cases(p)
    bad = []
    for e in br:
        mine = [c for c in cases if c.get("entry_point") == e.get("id")]
        if not mine:
            bad.append(f"{e.get('id')}: no case")
        else:
            planned = [c.get("id") for c in mine if outcome(c) in (None, "planned")]
            if planned:
                bad.append(f"{e.get('id')}: not carried out {planned}")
    chk.add(label, "p1", "every_way_in_has_a_case", bool(br) and not bad,
            "; ".join(bad) if bad else f"{len(br)} browser entry point(s), each with a carried-out case" if br
            else "no browser entry points")

    # flagged_as_required
    need = [norm_file(x) for x in entry.get("must_be_flagged") or []]
    by_file = {}
    for e in br:
        by_file.setdefault(norm_file(e.get("file")), []).append(e)
    unflagged = [x for x in need if not any(e.get("status") in ("flagged", "found") for e in by_file.get(x, []))]
    row["flag_missing"] = unflagged
    chk.add(label, "p1", "flagged_as_required", not unflagged,
            f"not flagged or found: {unflagged}" if unflagged else
            (f"{len(need)} required: " + "; ".join(f"{x} -> {[(e.get('id'), e.get('status')) for e in by_file.get(x, [])]}"
                                                    for x in need) if need else "nothing required"))

    # decoy_not_a_way_in
    trace = p.get("trace") if isinstance(p.get("trace"), dict) else {}
    excluded = {norm_file(x.get("file")) for x in trace.get("excluded") or [] if isinstance(x, dict)}
    decoys = [norm_file(x) for x in entry.get("must_not_be_a_way_in") or []]
    as_found = {x: [e.get("id") for e in found_eps if norm_file(e.get("file")) == x] for x in decoys}
    wrong = [x for x in decoys if as_found[x]]
    row["decoys_found"] = wrong
    chk.add(label, "p1", "decoy_not_a_way_in", not wrong,
            "; ".join(f"{x}: {'found as ' + str(as_found[x]) if as_found[x] else 'not a found way in'}, "
                      f"{'in' if x in excluded else 'not in'} trace.excluded"
                      + (f", other statuses {[(e.get('id'), e.get('status')) for e in by_file.get(x, [])]}" if by_file.get(x) and not as_found[x] else "")
                      for x in decoys) or "no decoy listed")

    # verdict_matches_key (+ broke_on_the_right_way_in)
    want, got = entry.get("expected_verdict"), p.get("verdict")
    row.update(expected_verdict=want, verdict=got)
    chk.add(label, "p1", "verdict_matches_key", want is not None and want == got,
            f"expected {want}, got {got}; held {p.get('held')}/{p.get('planned')}, broke {p.get('broke')}")
    bw = entry.get("broken_way_in")
    if bw is not None and got == "fails":
        way = next((w for w in ways if w.get("label") == bw), None)
        eps_by_id = {e.get("id"): e for e in eps_of(p)}
        by_case = {c.get("id"): c for c in cases}
        broke = _ids(p.get("broke"))
        off = [b for b in broke if not (way and ep_matches(eps_by_id.get((by_case.get(b) or {}).get("entry_point"), {}), way))]
        ok = way is not None and bool(broke) and not off
        row["broke_on_the_right_way_in"] = ok
        chk.add(label, "p1", "broke_on_the_right_way_in", ok,
                f"no key way in labelled {bw!r}" if way is None else
                f"broke {broke}; not on {bw!r}: "
                + str([(b, (by_case.get(b) or {}).get("entry_point")) for b in off]))

    # was_traced
    plan = plan_of(p)
    rows = trace.get("rows")
    traced = plan.get("traced") is True and isinstance(rows, list) and bool(rows)
    row["traced"] = traced
    chk.add(label, "p1", "was_traced", traced,
            f"plan.traced {plan.get('traced')!r}, {len(rows) if isinstance(rows, list) else 'no'} trace row(s)"
            + ("" if plan.get("traced") is True else f"; how_found {' '.join(str(plan.get('how_found') or '').split())[:120]!r}"))
    row["what"], row["how_to_see_it"] = entry.get("what"), entry.get("how_to_see_it")
    return row


def cs_ways_run(chk, label, run_dir):
    """A ways copy, before the key: generic checks only; p1 must get a carried-out verdict."""
    run = load_run(run_dir)
    ps = check_run_ok(chk, label, run, WAYS_IDS)
    v = (ps.get("p1") or {}).get("verdict")
    chk.add(label, "p1", "has_carried_out_verdict", v in VARIANT_VERDICTS, f"verdict {v}")
    case_checks(chk, label, run_dir, run)


def cs_ways_score(chk, label, run_dir, entry):
    """For --check-only with a key entry file: the scoring only."""
    score_ways(chk, label, run_dir, entry)


# ---------------------------------------------------------------- the data stream (data tools, DATATOOLS.md)
DATA_TOOL_NAMES = {"set_up", "data_tools"}     # catalog tools a case conversation must never be given
BIG_PRICE_CENTS = 100_000                      # the planted bug: a listing at $1,000.00 or more
REQ_LISTING = re.compile(r"listing|item|product", re.I)
REQ_PRICE = re.compile(r"price|priced|cost|cents|\$\s?\d|\b1,?000\b", re.I)


def declared_tools(run_dir) -> dict:
    """The tools in the app's .groundtruth/data.yml, by name (a run folder is <app>/.groundtruth/runs/<id>). The run
    does not record them, so the file is read as it is now; missing or unreadable means none."""
    f = Path(run_dir).resolve().parent.parent / "data.yml"
    try:
        import yaml
        tools = (yaml.safe_load(f.read_text()) or {}).get("tools") or []
    except Exception:  # noqa: BLE001
        return {}
    return {t["name"]: t for t in tools if isinstance(t, dict) and isinstance(t.get("name"), str)}


def _result(c: dict) -> dict:
    return c.get("result") if isinstance(c.get("result"), dict) else {}


def steps_of(c: dict) -> list:
    """The case's starting steps as carried out (result.starts_from), else as planned on the case itself."""
    v = _result(c).get("starts_from")
    if not isinstance(v, list):
        v = c.get("starts_from")
    return [s for s in v if isinstance(s, dict)] if isinstance(v, list) else []


def data_request_of(c: dict):
    v = _result(c).get("data_request")
    return v if v else c.get("data_request") or None


def _step_failed(s: dict) -> bool:
    return bool(s.get("error"))


def not_started(c: dict) -> bool:
    """Could not run before any conversation: a data request, or a starting step that failed."""
    return outcome(c) == "cant_run" and (bool(data_request_of(c)) or any(_step_failed(s) for s in steps_of(c)))


def big_listing_step(s: dict) -> bool:
    """A make_listing step that made an in-stock listing at $1,000.00 or more."""
    inp = s.get("input") if isinstance(s.get("input"), dict) else {}
    price = inp.get("price_cents")
    return (s.get("tool") == "make_listing" and _num(price) and price >= BIG_PRICE_CENTS
            and inp.get("sold_out") is not True and not _step_failed(s))


def _step_brief(s: dict) -> str:
    return f"{s.get('tool')}({json.dumps(s.get('input'), sort_keys=True)[:80]})"


def _all_steps(p: dict) -> list:
    return [(c.get("id"), s) for c in run_cases(p) for s in steps_of(c)]


def _segments(calls: list, attempts) -> list:
    """The receipts of each attempt. A second attempt starts again with a reset, so with attempts == 2 the receipts are
    split before every reset_data; with one attempt they are one segment (a reset mid-case does not excuse a set_up)."""
    if attempts != 2:
        return [calls]
    segs = [[]]
    for t in calls:
        if t == "reset_data" and segs[-1]:
            segs.append([])
        segs[-1].append(t)
    return segs


def data_case_checks(chk: Checks, label: str, run_dir: Path, run: dict):
    """The usual case checks for every case that had a conversation, then for every case: the conversation was not
    given set_up or data_tools, and no set_up call came after the case's first other call (per attempt)."""
    trimmed = dict(run, properties=[dict(p, cases=[c for c in p.get("cases") or [] if not (isinstance(c, dict) and not_started(c))])
                                    for p in run.get("properties") or [] if isinstance(p, dict)])
    case_checks(chk, label, run_dir, trimmed)
    for pid, p in props_of(run).items():
        for c in p.get("cases") or []:
            if not isinstance(c, dict) or outcome(c) == "planned":
                continue
            cid, tag = c.get("id"), f"{pid}/{c.get('id')}"
            meta = _result(c).get("meta") if isinstance(_result(c).get("meta"), dict) else {}
            given = meta.get("tools")
            if not isinstance(given, list):
                chk.add(label, tag, "no_data_tool_in_conversation", not_started(c),
                        "not started (no conversation)" if not_started(c) else f"no result.meta.tools (outcome {outcome(c)})")
            else:
                bad = sorted(DATA_TOOL_NAMES & set(given))
                chk.add(label, tag, "no_data_tool_in_conversation", not bad,
                        f"conversation given {bad}" if bad else f"{len(given)} tools given, no set_up or data_tools")
            calls = [str(r.get("tool")) for r in receipts(run_dir, pid, cid)]
            late = []
            for n, seg in enumerate(_segments(calls, meta.get("attempts")), 1):
                first = next((i for i, t in enumerate(seg) if t not in ("reset_data", "set_up")), None)
                if first is not None:
                    late += [f"attempt {n}: set_up at {i} after {seg[first]} at {first}"
                             for i, t in enumerate(seg) if t == "set_up" and i > first]
            chk.add(label, tag, "set_up_only_before_case", not late,
                    "; ".join(late) if late else f"{calls.count('set_up')} set_up of {len(calls)} receipts, none after another call")


def _sold_out_case(c: dict) -> bool:
    return bool(SOLD_P0.search(what(c))) or any(
        s.get("tool") == "make_listing" and isinstance(s.get("input"), dict) and s["input"].get("sold_out") is True
        for s in steps_of(c))


def cs_data_clean(chk, label, run_dir):
    """The clean copy with data tools: p1 holds, nothing broke, every starting step used a declared tool and returned."""
    run = load_run(run_dir)
    ps = check_run_ok(chk, label, run, ["p1"])
    p = ps.get("p1") or {}
    declined, broke = _ids(p.get("declined")), _ids(p.get("broke"))
    by_id = {c.get("id"): c for c in run_cases(p)}
    if p.get("verdict") == "needs_decision" and not broke and declined and all(_sold_out_case(by_id.get(d, {})) for d in declined):
        # The known false question: an in-stock property asked about a sold-out case. Its own name, so it can be told apart.
        chk.add(label, "p1", "known_false_question_sold_out", False,
                f"needs_decision only on sold-out case(s) {declined}" + "".join(f"; {d}: {what(by_id.get(d, {}))[:70]}" for d in declined[:3]))
    else:
        check_verdict(chk, label, ps, "p1", "holds")
    chk.add(label, "p1", "none_broke", bool(p) and not broke, f"broke {broke}" if p else "property missing")
    declared, steps = declared_tools(run_dir), _all_steps(p)
    bad = [f"{cid}: {_step_brief(s)} " + ("not declared" if s.get("tool") not in declared else
                                          f"error {str(s.get('error'))[:60]!r}" if _step_failed(s) else "returned nothing")
           for cid, s in steps if s.get("tool") not in declared or _step_failed(s) or not s.get("returned")]
    chk.add(label, "p1", "steps_declared_and_returned", not bad,
            "; ".join(bad) if bad else f"{len(steps)} step(s), all declared ({sorted(declared)}) and returned")
    data_case_checks(chk, label, run_dir, run)


def cs_data_bug(chk, label, run_dir):
    """The copy where a listing of $1,000.00 or more enters the cart at the wrong amount: p1 fails, and only on cases
    that started from a made listing at that price."""
    run = load_run(run_dir)
    ps = check_run_ok(chk, label, run, ["p1"])
    check_verdict(chk, label, ps, "p1", "fails")
    p = ps.get("p1") or {}
    by_id = {c.get("id"): c for c in run_cases(p)}
    broke = _ids(p.get("broke"))
    big = {b: [s for s in steps_of(by_id.get(b, {})) if big_listing_step(s)] for b in broke}
    note = "; ".join(f"{b}: {[_step_brief(s) for s in steps_of(by_id.get(b, {}))] or 'no steps'}" for b in broke[:4])
    chk.add(label, "p1", "a_break_on_made_big_listing", any(big.values()), f"broke {broke}; {note}")
    off = [b for b in broke if not big[b]]
    chk.add(label, "p1", "every_break_on_made_big_listing", bool(broke) and not off,
            f"broke {broke}; without a make_listing step at $1,000.00 or more: {off}")
    steps = _all_steps(p)
    no_why = [f"{cid}: {_step_brief(s)}" for cid, s in steps if not str(s.get("because") or "").strip()]
    chk.add(label, "p1", "every_step_has_because", not no_why,
            f"steps without because: {no_why}" if no_why else f"{len(steps)} step(s), each with a because")
    data_case_checks(chk, label, run_dir, run)


def cs_data_no_tool(chk, label, run_dir):
    """The bug copy with no tool that can make a listing: p1 is neither held nor failed, a case asked for the tool
    (a data_request about a listing and a price), and no step called an undeclared tool."""
    run = load_run(run_dir)
    ps = check_run_ok(chk, label, run, ["p1"])
    p = ps.get("p1") or {}
    v = p.get("verdict")
    chk.add(label, "p1", "verdict_neither_holds_nor_fails", bool(p) and isinstance(v, str) and v not in ("holds", "fails"),
            f"{v}; held {p.get('held')}/{p.get('planned')}, broke {p.get('broke')}, not run {p.get('not_run')}" if p else "property missing")
    reqs = {c.get("id"): json.dumps(data_request_of(c), ensure_ascii=False) for c in run_cases(p)
            if outcome(c) == "cant_run" and data_request_of(c)}
    good = [cid for cid, t in reqs.items() if REQ_LISTING.search(t) and REQ_PRICE.search(t)]
    chk.add(label, "p1", "request_for_listing_at_price", bool(good),
            f"requests naming a listing and a price: {good}" if good else
            f"no such request; could-not-run requests: " + ("; ".join(f"{k}: {t[:90]}" for k, t in reqs.items()) or "none"))
    declared, steps = declared_tools(run_dir), _all_steps(p)
    bad = [f"{cid}: {_step_brief(s)}" for cid, s in steps if s.get("tool") not in declared]
    chk.add(label, "p1", "no_step_for_undeclared_tool", not bad,
            f"undeclared: {bad}" if bad else f"{len(steps)} step(s), all with declared tools ({sorted(declared)})")
    data_case_checks(chk, label, run_dir, run)


# ---------------------------------------------------------------- the claims streams (planted bugs in the dashboard page)
CLAIM_IDS = ("tabs", "result", "film")
CLAIMS_FILE = Path(".groundtruth") / "features" / "claims.yml"
NOT_COUNTED = ("declined", "outside", "cant_run")       # never a catch; listed in the clean copy's notes
TEST_STORE_MARKER = "MADE-UP-DATA-ONLY"
STORE_RESET = "python3 .groundtruth/tools/reset_store.py"


class InProcessStore:
    """The results store serving one dashboard copy (page folder = the copy, data = its .groundtruth/test-store), run
    in this process on 127.0.0.1:<port>. Shaped like a Popen (poll, terminate, wait, kill) so apps.stop and
    Stream.stop_all can stop it."""

    def __init__(self, app_dir: Path, port: int):
        from store.server import start_in_background
        data = Path(app_dir) / ".groundtruth" / "test-store"
        if not (data / TEST_STORE_MARKER).is_file():
            raise RuntimeError(f"{data} has no {TEST_STORE_MARKER} marker; rebuild the copy with "
                               "gates/variants/build_dash_copies.py")
        if apps._listening_pids(port):
            raise RuntimeError(f"port {port} is already in use by {apps._listening_pids(port)}; not taking it over")
        self.srv, self.url = start_in_background(data, port, Path(app_dir))
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        if self.returncode is None:
            self.srv.shutdown()
            self.srv.server_close()
            self.returncode = 0

    kill = terminate

    def wait(self, timeout=None):
        return self.returncode


def reset_test_store(app_dir: Path) -> str:
    """The copy's own reset command, run from the copy, as the tool server runs it."""
    env = {k: v for k, v in os.environ.items() if k != "GT_TEST_STORE_DIR"}
    r = subprocess.run(STORE_RESET, shell=True, cwd=app_dir, env=env, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"{STORE_RESET} failed in {app_dir}: {(r.stdout + r.stderr)[-300:]}")
    return r.stdout.strip()


def claim_variations(run_dir) -> tuple[dict, str]:
    """The variation ids of each claim in the app's .groundtruth/features/claims.yml (a run folder is
    <app>/.groundtruth/runs/<id>), read with the runner's own claim reader; ({}, why) when it cannot be read."""
    f = Path(run_dir).resolve().parent.parent / "features" / "claims.yml"
    try:
        import yaml
        from toolserver.runner import claim_parts
        props = (yaml.safe_load(f.read_text()) or {}).get("properties") or []
        out = {p.get("id"): [v["id"] for v in claim_parts(p, f.name).get("variations") or []]
               for p in props if isinstance(p, dict)}
    except Exception as e:  # noqa: BLE001
        return {}, f"could not read {f}: {type(e).__name__}: {e}"
    return out, f"read {f}"


def carried_cases(p: dict) -> list:
    return [c for c in p.get("cases") or [] if isinstance(c, dict)]


def check_one_case_per_variation(chk: Checks, label: str, ps: dict, pid: str, variations: tuple[dict, str]):
    want, why = variations[0].get(pid), variations[1]
    p = ps.get(pid)
    if want is None:
        return chk.add(label, pid, "one_case_per_variation", False, f"no variations for {pid} ({why})")
    if p is None:
        return chk.add(label, pid, "one_case_per_variation", False, "claim missing from the run")
    got = [c.get("id") for c in carried_cases(p)]
    dup = sorted({x for x in got if got.count(x) > 1}, key=str)
    missing, extra = [v for v in want if v not in got], [x for x in got if x not in want]
    return chk.add(label, pid, "one_case_per_variation", not (dup or missing or extra),
                   f"cases {got}; variations {want}" + (f"; missing {missing}" if missing else "")
                   + (f"; not a variation {extra}" if extra else "") + (f"; twice {dup}" if dup else ""))


def ran_steps(c: dict, tool: str = "make_run") -> list:
    """The case's starting steps that ran without error, from result.starts_from only (never the plan)."""
    v = _result(c).get("starts_from")
    if not isinstance(v, list):
        return []
    return [s for s in v if isinstance(s, dict) and s.get("tool") == tool and isinstance(s.get("input"), dict)
            and not _step_failed(s) and s.get("returned") is not None]


def _whole(inp: dict, key: str, default=None):
    v = inp.get(key, default)
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def made_tests(inp: dict) -> list | None:
    """The verdict word of every test a make_run input makes, New tests first (unlisted tests hold), or None."""
    new, rerun = _whole(inp, "new_tests"), _whole(inp, "rerun_tests")
    if new is None or rerun is None:
        return None
    raw = inp.get("verdicts", "")
    words = [w.strip() for w in raw.split(",")] if isinstance(raw, str) and raw.strip() else []
    return (words + ["holds"] * (new + rerun))[:new + rerun]


def able_regression_short(inp: dict) -> bool:
    """dash-regression-short drops the run's last rerun test: it shows when there is at least one."""
    rerun = _whole(inp, "rerun_tests")
    return rerun is not None and rerun >= 1


def able_single_holds(inp: dict) -> bool:
    """dash-single-holds shows a one-variation test that fails as holding on its card."""
    tests = made_tests(inp)
    return _whole(inp, "variations_each", 3) == 1 and tests is not None and "fails" in tests


def able_other_test_film(inp: dict) -> bool:
    """dash-other-test-film plays the first test's film for the same variation id of any later test. Tests that are
    not_run have no variations, so it takes two tests with variations (and films)."""
    tests = made_tests(inp)
    return inp.get("films", True) is not False and tests is not None and sum(w != "not_run" for w in tests) >= 2


def able_case(c: dict, able) -> bool:
    return any(able(s["input"]) for s in ran_steps(c))


def _inputs_brief(c: dict) -> str:
    v = _result(c).get("starts_from")
    steps = [s for s in v if isinstance(s, dict)] if isinstance(v, list) else []
    return "; ".join(f"{s.get('tool')}({json.dumps(s.get('input'), sort_keys=True)})"
                     + (f" error {str(s.get('error'))[:60]!r}" if _step_failed(s) else "") for s in steps) or "no starting steps"


def _case_text(c: dict) -> str:
    r = _result(c)
    return " ".join(str(r.get("question") or r.get("observed") or "").split())[:200]


def cs_claims_clean(chk, label, run_dir):
    """The clean copy, all three claims: one case per variation, nothing broke, tabs and result hold; film's verdict
    is a note (its "pointer visible" cannot be checked: the verifier is never shown a film's picture)."""
    run = load_run(run_dir)
    ps = check_run_ok(chk, label, run, list(CLAIM_IDS))
    variations = claim_variations(run_dir)
    for pid in CLAIM_IDS:
        check_one_case_per_variation(chk, label, ps, pid, variations)
    for pid in CLAIM_IDS:
        p = ps.get(pid)
        broke = sorted({c.get("id") for c in carried_cases(p or {}) if outcome(c) == "breaks"} | set(_ids((p or {}).get("broke"))), key=str)
        chk.add(label, pid, "none_broke", p is not None and not broke, f"broke {broke}" if p is not None else "claim missing")
    check_verdict(chk, label, ps, "tabs", "holds")
    check_verdict(chk, label, ps, "result", "holds")
    film = ps.get("film") or {}
    chk.note(label, "film", f"verdict {film.get('verdict')} (reported, not judged); held {film.get('held')}/{film.get('planned')}")
    for pid, p in ps.items():
        for c in carried_cases(p):
            if outcome(c) in NOT_COUNTED:
                chk.note(label, f"{pid}/{c.get('id')}", f"{outcome(c)}: {_case_text(c)}")
    data_case_checks(chk, label, run_dir, run)


def claims_bug_checkset(target: str, able):
    """The checkset for one broken copy: its target claim must break, only where the bug was able to show."""
    def cs(chk, label, run_dir):
        run = load_run(run_dir)
        ps = check_run_ok(chk, label, run, [target])
        check_one_case_per_variation(chk, label, ps, target, claim_variations(run_dir))
        rows = [(c.get("id"), outcome(c), able_case(c, able), c) for c in carried_cases(ps.get(target) or {})]
        breaks = [r for r in rows if r[1] == "breaks"]
        caught = [r[0] for r in breaks if r[2]]
        chk.add(label, target, "bug_caught", bool(caught),
                f"broke where able to show: {caught}" if caught else
                f"no break where the bug was able to show; broke {[r[0] for r in breaks]}; able to show "
                f"{[r[0] for r in rows if r[2]]}; outcomes {[(r[0], r[1]) for r in rows]}")
        off = [r[0] for r in breaks if not r[2]]
        chk.add(label, target, "no_break_where_bug_cannot_show", not off,
                f"broke where the bug could not show: {off}" if off else f"{len(breaks)} break(s), all where able to show")
        check_verdict(chk, label, ps, target, "fails")
        for cid, out, ok, c in rows:
            chk.note(label, f"{target}/{cid}", f"{out}; able to show {'yes' if ok else 'no'}; {_inputs_brief(c)}")
        data_case_checks(chk, label, run_dir, run)
    cs.__name__ = f"cs_claims_bug_{target}"
    cs.__doc__ = f"A broken dashboard copy: claim {target} fails, only on cases whose starting data let the bug show."
    return cs


# checkset -> (copy under gates/work, target claim, able-to-show test over one make_run input)
CLAIM_BUGS = {
    "claims-regression-short": ("dash-regression-short", "tabs", able_regression_short),
    "claims-single-holds": ("dash-single-holds", "result", able_single_holds),
    "claims-other-test-film": ("dash-other-test-film", "film", able_other_test_film),
}


# checksets that also take one key entry (a JSON file given on the command line)
KEYED_CHECKSETS = {"ways-score": cs_ways_score}


CHECKSETS = {
    "cases": cs_cases, "cbay-plans": cs_cbay_plans, "cbay-p1": cs_cbay_p1, "cbay-full": cs_cbay_full,
    "cbay-plan-only": cs_cbay_plan_only, "cbay-decide-first": cs_decide_first, "cbay-decide-second": cs_decide_second,
    "qtycap": cs_qtycap, "twodoors": cs_twodoors, "twodoors-broken": cs_twodoors_broken,
    "lipsync-agent": cs_lipsync_agent, "lipsync-fixed": cs_lipsync_fixed, "lipsync-control": cs_lipsync_control,
    "unseen-ref": cs_unseen_ref, "unseen-variant": cs_unseen_variant, "ways-run": cs_ways_run,
    "data-clean": cs_data_clean, "data-bug": cs_data_bug, "data-no-tool": cs_data_no_tool,
    "claims-clean": cs_claims_clean,
    **{name: claims_bug_checkset(target, able) for name, (_copy, target, able) in CLAIM_BUGS.items()},
}
