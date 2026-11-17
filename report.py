"""Writes a run's human-readable report: what it ran against, then one section per property with its cases.

The page is one HTML file: inline CSS, screenshots as base64, no JavaScript. The one exception to "self-contained"
is a case's film: each clip stays a file in the case's folder and the page links to it by a path relative to
report.html, so the report's folder has to be kept whole for the films to play. Everything taken from run data is HTML-escaped. Every field is treated as possibly missing.
"""
import base64
import html
import json
import re
import shlex
from urllib.parse import quote
from pathlib import Path

VERDICTS = {"fails": "fails", "needs_decision": "needs your decision", "cant_tell": "can't tell",
            "holds": "holds", "planned": "planned only"}
VERDICT_ORDER = ["fails", "needs_decision", "cant_tell", "holds", "planned"]
OUTCOMES = {"breaks": "broke", "declined": "declined by the app", "outside": "outside the property",
            "cant_run": "could not run", "holds": "held", "planned": "planned"}
OUTCOME_CLASS = {"breaks": "fails", "declined": "needs_decision", "outside": "outside", "cant_run": "cant_tell",
                 "holds": "holds", "planned": "planned"}
OUTCOME_ORDER = ["breaks", "declined", "cant_run", "holds", "outside", "planned"]
CASE_DOORS = {"browser": "pages", "api": "direct API call", "code": "code"}
ENTRY_DOORS = {"browser": "through the pages", "api": "direct API call", "code": "files"}
KINDS = {"behaviour": "using the app", "measurement": "measuring the running process", "code": "reading the code"}
MEASURE_TOOLS = ["process_info", "gpu_check", "cpu_sample", "net_connections", "http_request",
                 "click", "click_and_watch", "navigate", "fill"]
MEAS_LIMIT, ALL_LIMIT = 700, 200

CSS = """
:root{--bg:#fbfaf8;--card:#fff;--ink:#1d1d1b;--mute:#6b6a66;--line:#e6e3dd;--code:#f4f2ee;
--ok:#1f7a4d;--okbg:#e5f4ec;--bad:#b3261e;--badbg:#fbe9e7;--meh:#8a6100;--mehbg:#fbf1d9;
--ask:#6a3fb5;--askbg:#efe8fb;--plan:#4a5a6a;--planbg:#e8edf2}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--card:#1f1f1d;--ink:#ecebe7;--mute:#a09e98;--line:#33322f;
--code:#272724;--ok:#6fd3a0;--okbg:#18392a;--bad:#ff8a80;--badbg:#3d1c1a;--meh:#f0c35a;--mehbg:#3a2e12;
--ask:#c9a8ff;--askbg:#2e2342;--plan:#a9bccf;--planbg:#25303a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,-apple-system,sans-serif}
main{max-width:980px;margin:0 auto;padding:32px 16px 64px}h1{font-size:22px;margin:0 0 4px}
.goal{color:var(--mute);margin:0 0 20px}.tally{display:flex;gap:8px;margin-bottom:24px;flex-wrap:wrap}
.pill{padding:3px 10px;border-radius:99px;font-size:13px;font-weight:600;white-space:nowrap}
.pill.small{padding:1px 8px;font-size:12px}
.holds{background:var(--okbg);color:var(--ok)}.fails{background:var(--badbg);color:var(--bad)}
.cant_tell{background:var(--mehbg);color:var(--meh)}.needs_decision{background:var(--askbg);color:var(--ask)}
.planned{background:var(--planbg);color:var(--plan)}.outside{background:var(--code);color:var(--mute)}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px 20px;margin-bottom:16px}
.env h2{font-size:17px;margin:0 0 8px}.env h3{font-size:14px;margin:14px 0 4px}
.env dl,dl.read{display:grid;grid-template-columns:max-content 1fr;gap:2px 14px;margin:0;font-size:14px}
.env dt,dl.read dt{color:var(--mute)}.env dd,dl.read dd{margin:0;overflow-wrap:anywhere}
.env ul{margin:4px 0;padding-left:20px;font-size:14px}
@media (max-width:560px){.env dl,dl.read{grid-template-columns:1fr}.env dd,dl.read dd{margin-bottom:6px}}
.head{display:flex;justify-content:space-between;gap:12px;align-items:baseline;flex-wrap:wrap}
.head h2{font-size:15px;margin:0;color:var(--mute)}
.statement{font-size:19px;font-weight:600;line-height:1.4;margin:6px 0 4px}
.example{font-size:14px;color:var(--mute);margin:0 0 6px}
.count{font-size:15px;margin:4px 0 12px}
img,video{max-width:100%;border:1px solid var(--line);border-radius:6px}
figure.clip{margin:10px 0}figure.clip figcaption{font-size:13px;color:var(--mute);margin-top:2px}
pre{background:var(--code);border-radius:6px;padding:10px 12px;overflow-x:auto;font:12.5px/1.5 ui-monospace,Menlo,monospace;margin:6px 0 4px;white-space:pre-wrap;overflow-wrap:anywhere}
.why{font-size:14px;margin:10px 0 0}.path{font:12px ui-monospace,Menlo,monospace;color:var(--mute);overflow-wrap:anywhere}
.sub{font-size:14px;font-weight:600;margin:16px 0 4px}
.warn{background:var(--badbg);color:var(--bad);border-radius:6px;padding:8px 12px;font-size:14px;margin:8px 0}
.caution{background:var(--mehbg);color:var(--meh);border-radius:6px;padding:8px 12px;font-size:14px;margin:8px 0}
.alert{background:var(--badbg);color:var(--bad);border:1px solid var(--bad);border-radius:10px;padding:12px 16px;margin-bottom:16px;font-weight:600}
.questions{background:var(--askbg);border:1px solid var(--ask);border-radius:8px;padding:10px 14px;margin:12px 0}
.questions h3{color:var(--ask);font-size:15px;margin:0 0 6px}.questions .q{font-weight:600;margin:10px 0 2px}
.questions p{font-size:14px;margin:2px 0}.questions code{font:12px ui-monospace,Menlo,monospace}
table{border-collapse:collapse;width:100%;font-size:13px;margin:6px 0}th,td{border-bottom:1px solid var(--line);padding:5px 8px;text-align:left;vertical-align:top;overflow-wrap:break-word}
th{color:var(--mute);font-weight:600}td code,.path code{font:12px ui-monospace,Menlo,monospace}
.scroll{overflow-x:auto;margin:6px 0}.scroll table.cases{min-width:820px}
table.cases td.id{white-space:nowrap;font-weight:600}
table.cases tr.breaks td{background:var(--badbg)}table.cases tr.declined td{background:var(--askbg)}
.note{font-size:14px;margin:4px 0}ul.plain{margin:4px 0;padding-left:20px;font-size:14px}
details{margin-top:10px;font-size:13px;border-top:1px solid var(--line);padding-top:6px}
summary{cursor:pointer;color:var(--mute)}.meta{font-size:12px;color:var(--mute);margin-top:10px}
.foot{font-size:13px;color:var(--mute);margin-top:14px;border-top:1px solid var(--line);padding-top:8px}
.refused{color:var(--bad);font-weight:600}
.chain{font-size:12.5px;color:var(--mute);overflow-wrap:anywhere}tr.chainrow td{border-bottom:1px solid var(--line);padding-top:0}
.limits{border-left:3px solid var(--meh);padding-left:10px;margin:10px 0}
table.hits td:nth-child(2){max-width:320px}
"""


def esc(s) -> str:
    return html.escape("" if s is None else str(s))


def trunc(s, n: int) -> str:
    """Shorten for display, then escape (slice first so no entity is split)."""
    s = s if isinstance(s, str) else json.dumps(s, ensure_ascii=False)
    if len(s) > n:
        s = s[:n] + f"... ({len(s) - n} more characters not shown)"
    return esc(s)


def d(x) -> dict:
    return x if isinstance(x, dict) else {}


def lst(x) -> list:
    return x if isinstance(x, list) else []


def text(x) -> str:
    """A data value as display text (not escaped)."""
    if x is None:
        return ""
    return x if isinstance(x, str) else json.dumps(x, ensure_ascii=False)


def join_or(items) -> str:
    items = [str(i) for i in items]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " or " + items[-1]


def join_and(items) -> str:
    items = [str(i) for i in items]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def safe_name(x) -> bool:
    s = str(x)
    return bool(s) and "/" not in s and "\\" not in s and ".." not in s


def read_json(f: Path):
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def read_receipts(cdir: Path) -> list:
    f = cdir / "receipts.jsonl"
    out = []
    if not f.is_file():
        return out
    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            x = json.loads(line)
        except ValueError:
            out.append({"tool": "(unreadable receipt line)", "args": {}, "result": line})
            continue
        if isinstance(x, dict):
            out.append(x)
    return out


def fmt_args(args) -> str:
    if isinstance(args, dict):
        return ", ".join(f"{k}={v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)}"
                         for k, v in args.items() if v not in ("", None))
    return "" if args is None else json.dumps(args, ensure_ascii=False)


def num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def cost_txt(c) -> str:
    return f"${c:.3f}"


# ---------- the environment section ----------

def env_section(run: dict, props: list) -> str:
    env, svc = d(run.get("env")), d(run.get("service"))
    facts = d(env.get("facts"))
    rows = [("App address", esc(env.get("url") or "not recorded"))]
    pid = env.get("pid")
    rows.append(("Process id", esc(pid) if pid not in (None, "") else "none registered"))
    if facts.get("command"):
        rows.append(("Command line", f"<code>{esc(facts['command'])}</code>"))
    if facts.get("started"):
        rows.append(("Process started", esc(facts["started"])))
    commit = facts.get("commit")
    unc = facts.get("uncommitted_changes")
    if commit:
        c = f"<code>{esc(commit)}</code>"
        if unc is True:
            c += " <b>plus uncommitted changes</b>: the app that ran is not exactly this commit."
        elif unc is False:
            c += ", with no uncommitted changes."
        else:
            c += " (whether there were uncommitted changes was not recorded)."
    else:
        c = "not recorded"
        if unc is True:
            c += "; there were uncommitted changes."
    rows.append(("Commit", c))
    rows.append(("Platform", esc(facts.get("platform") or "not recorded")))
    rows.append(("Registered", esc(facts.get("registered_at") or "not recorded")))
    if env.get("repo"):
        rows.append(("Repository", f"<code>{esc(env['repo'])}</code>"))
    dl = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in rows)

    can = []
    if pid in (None, ""):
        can.append("No process id was registered, so nothing about the running process could be measured.")
    else:
        can.append("A process id was registered, so the running process could be measured.")
    if env.get("repo"):
        can.append("A repository was registered, so the code could be read.")
    else:
        can.append("No repository was registered, so the code could not be read.")
    if env.get("reset"):
        can.append(f"Each case started from clean data: the verifier ran this reset command before every case: "
                   f"<code>{esc(env['reset'])}</code>")
    else:
        can.append("No reset command was registered, so cases did not start from clean data; "
                   "one case's actions may have affected the next.")
    routes = env.get("routes")
    if routes:
        if isinstance(routes, dict) and routes.get("type"):
            extra = ", ".join(f"{k}: {v}" for k, v in routes.items() if k != "type")
            can.append(f"The list of routes was read from a registered source of type "
                       f"<code>{esc(routes['type'])}</code>" + (f" ({esc(extra)})" if extra else "") + ".")
        else:
            can.append(f"A source for the list of routes was registered: <code>{esc(text(routes))}</code>.")
    else:
        can.append("No source for the list of routes was registered.")

    srows = [("Address", esc(svc.get("url") or "not recorded")), ("Model", esc(svc.get("model") or "not recorded")),
             ("Version", esc(svc.get("version") or "not recorded"))]
    if svc.get("run"):
        srows.append(("Service run id", esc(svc["run"])))
    sdl = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in srows)

    order = [str(x) for x in lst(run.get("order"))]
    if order:
        order_txt = (f"<p class='note'>The properties' cases were carried out in this order: {esc(', '.join(order))}.</p>")
    else:
        order_txt = "<p class='note'>The order the properties' cases were carried out in was not recorded.</p>"
    meas = [str(p.get("id")) for p in props if d(prop_plan(p)).get("kind") == "measurement"]
    order_txt += ("<p class='note'>Properties that measure the running process have their cases carried out first, "
                  "before any other property's cases."
                  + (f" Here that was: {esc(', '.join(meas))}." if meas else " There were none in this run.") + "</p>")
    return (f'<section class="card env"><h2>What this ran against</h2>'
            f'<p class="note">A verdict is only about the environment it ran in.</p><dl>{dl}</dl>'
            f'<h3>What the verifier could and could not use here</h3><ul>'
            + "".join(f"<li>{x}</li>" for x in can) +
            f'</ul><h3>The verifier service</h3><dl>{sdl}</dl><h3>Order</h3>{order_txt}</section>')


# ---------- every-item checks (unchanged from the old report) ----------

def rule_sentence(kind, rule: dict) -> str:
    if kind == "match_each" or rule.get("kind") == "match_each":
        glob = rule.get("glob")
        parts = []
        if rule.get("must_contain") is not None:
            parts.append(f"must contain <code>{esc(rule['must_contain'])}</code>")
        if rule.get("must_not_contain") is not None:
            parts.append(f"must not contain <code>{esc(rule['must_not_contain'])}</code>")
        where = f"every file matching <code>{esc(glob)}</code>" if glob else "every file (pattern not recorded)"
        return f"Checked {where}: each file {' and '.join(parts) or 'had a rule that was not recorded'}."
    if kind != "probe_each" and not any(k in rule for k in ("methods", "path_prefix", "expect_status")):
        return "The rule for this check was not recorded."
    methods = rule.get("methods")
    what = f"a {esc(join_or(methods))}" if methods else "each route's own method"
    body = rule.get("body")
    withs = ["with no body" if body in (None, "") else f"with the body <code>{trunc(body, 200)}</code>"]
    headers = d(rule.get("headers"))
    if headers:
        withs.append("the headers " + ", ".join(f"<code>{esc(k)}: {esc(v)}</code>" for k, v in headers.items()))
    prefix = rule.get("path_prefix")
    to = f"every route under {esc(prefix)}" if prefix else "every listed route"
    exp = lst(rule.get("expect_status"))
    exp_txt = f"; expected {esc(join_or(exp))}" if exp else "; the expected status was not recorded"
    return f"Sent {what} {' and '.join(withs)} to {to}{exp_txt}."


def check_block(ch: dict) -> str:
    ch = d(ch)
    kind = ch.get("kind") or d(ch.get("rule")).get("kind")
    rule = d(ch.get("rule"))
    out = [f'<div class="sub">Every-item check' + (f' ({esc(ch.get("id"))})' if ch.get("id") else "") + '</div>',
           f'<p class="note">{rule_sentence(kind, rule)}</p>']
    srcs = lst(ch.get("sources"))
    if srcs:
        items = []
        for s in srcs:
            s = d(s)
            p = s.get("paths")
            n = len(p) if isinstance(p, list) else p
            bits = [esc(s.get("detail"))] if s.get("detail") else []
            if n is not None:
                bits.append(f"{esc(n)} paths")
            items.append(f"<li><b>{esc(s.get('name') or 'unnamed source')}</b>"
                         + (f": {', '.join(bits)}" if bits else "") + "</li>")
        out.append(f'<p class="note">Where the list came from:</p><ul class="plain">{"".join(items)}</ul>')
    elif kind == "probe_each":
        out.append('<p class="note">Where the list of routes came from was not recorded.</p>')
    agree = ch.get("agree")
    if agree is False:
        out.append('<div class="caution">The sources did not give the same list, so the list may be incomplete: '
                   'some items may never have been checked.</div>')
    elif agree is True:
        out.append('<p class="note">The independent sources gave the same list.</p>')
    elif kind == "probe_each" and srcs:
        out.append('<p class="note">Whether the sources agreed was not recorded.</p>')
    checked, passed = ch.get("checked"), ch.get("passed")
    out.append(f'<p class="note">Checked {esc(checked if checked is not None else "an unrecorded number of")} '
               f'items; {esc(passed if passed is not None else "an unrecorded number")} passed.</p>')
    if checked == 0:
        out.append('<div class="warn">Zero items were checked, so this check proves nothing.</div>')
    viol = lst(ch.get("violators"))
    if viol:
        if kind == "match_each" or (kind is None and all("why" in d(v) for v in viol)):
            rows = "".join(f"<tr><td><code>{esc(d(v).get('path'))}</code></td><td>{esc(d(v).get('why'))}</td></tr>"
                           for v in viol)
            out.append(f"<table><caption class='path'>Items that broke the rule</caption>"
                       f"<tr><th>File</th><th>Why</th></tr>{rows}</table>")
        else:
            rows = ""
            for v in viol:
                v = d(v)
                st = v.get("status")
                st_txt = esc(st) if st is not None else "no response"
                if v.get("error"):
                    st_txt += f" ({trunc(v['error'], 200)})"
                rows += f"<tr><td>{esc(v.get('method'))}</td><td><code>{esc(v.get('path'))}</code></td><td>{st_txt}</td></tr>"
            out.append(f"<table><caption class='path'>Items that broke the rule</caption>"
                       f"<tr><th>Method</th><th>Path</th><th>Status returned</th></tr>{rows}</table>")
    elif checked:
        out.append('<p class="note">No item broke the rule.</p>')
    skipped = lst(ch.get("skipped"))
    if skipped:
        rows = "".join(f"<tr><td>{esc(d(s).get('method'))}</td><td><code>{esc(d(s).get('path'))}</code></td>"
                       f"<td>{esc(d(s).get('why'))}</td></tr>" for s in skipped)
        out.append(f"<table><caption class='path'>Items skipped (not checked)</caption>"
                   f"<tr><th>Method</th><th>Path</th><th>Why skipped</th></tr>{rows}</table>")
    return "".join(out)


# ---------- reading a property ----------

def prop_plan(p: dict) -> dict:
    return d(p.get("plan")) or d(d(p.get("discovery")).get("plan"))


def prop_dir(run_dir: Path, p: dict):
    pid = p.get("id")
    return run_dir / str(pid) if safe_name(pid or "") else None


def case_result(case: dict, pdir) -> dict:
    """The case's result from run.json, else from the case folder's report.json."""
    r = d(case.get("result"))
    if r:
        return r
    cid = case.get("id")
    if pdir is not None and safe_name(cid or ""):
        return d(read_json(pdir / str(cid) / "report.json"))
    return {}


def outcome_of(r: dict) -> str:
    o = r.get("outcome")
    return o if o in OUTCOMES else "cant_run"


def nothing_there(r: dict) -> bool:
    """The case looked where the search pointed and found no way to do the action; recorded as outside."""
    return r.get("nothing_there") is True and outcome_of(r) == "outside"


def outcome_words(r: dict) -> str:
    return "nothing there to test" if nothing_there(r) else OUTCOMES[outcome_of(r)]


def discovery_meta(p: dict, pdir) -> dict:
    m = d(p.get("discovery_meta")) or d(d(p.get("discovery")).get("meta"))
    if not m and pdir is not None:
        m = d(d(read_json(pdir / "discovery" / "report.json")).get("meta"))
    return m


def case_door(case: dict, plan: dict):
    door = case.get("door")
    if door:
        return door
    for e in lst(plan.get("entry_points")):
        if d(e).get("id") is not None and d(e).get("id") == case.get("entry_point"):
            return d(e).get("door")
    return None


def ids_of(p: dict, key: str, cases: list, test) -> list:
    """A list of case ids from the property record, else worked out from the cases themselves."""
    v = p.get(key)
    return v if isinstance(v, list) else [c.get("id") for c in cases if test(outcome_of(c["_r"]))]


def count_line(p: dict, cases: list, plan: dict) -> str:
    verdict = p.get("verdict") if p.get("verdict") in VERDICTS else "cant_tell"
    outs = [outcome_of(c["_r"]) for c in cases]
    total = p.get("planned") if isinstance(p.get("planned"), int) and not isinstance(p.get("planned"), bool) else len(cases)
    held = p.get("held") if isinstance(p.get("held"), int) and not isinstance(p.get("held"), bool) else outs.count("holds")
    broke = ids_of(p, "broke", cases, lambda o: o == "breaks")
    declined = ids_of(p, "declined", cases, lambda o: o == "declined")
    nothing = [c.get("id") for c in cases if nothing_there(c["_r"])]
    outside = [x for x in ids_of(p, "outside", cases, lambda o: o == "outside") if x not in nothing]
    not_run = ids_of(p, "not_run", cases, lambda o: o not in ("holds", "breaks", "declined", "outside"))
    of = lambda n: f"{n} of {total} cases" if total != 1 else f"{n} of 1 case"  # noqa: E731

    def declined_txt(n, also=False):
        return (f"The app {'also ' if also else ''}declined the action in {plural(n, 'case', 'cases')}, "
                f"so the property could not be observed there.")

    def tail(skip_declined=False):
        bits = []
        if declined and not skip_declined:
            bits.append(declined_txt(len(declined), also=True))
        if not_run:
            bits.append(f"{of(len(not_run))} could not be run.")
        if outside:
            n = len(outside)
            bits.append(f"{plural(n, 'planned case', 'planned cases')} fell outside the property's wording and "
                        f"{'does' if n == 1 else 'do'} not count.")
        if nothing:
            bits.append(f"In {plural(len(nothing), 'place', 'places')} the search pointed at, there was no way to do "
                        f"the action, so there was nothing to test there.")
        return bits

    if verdict == "planned":
        bits = [f"{plural(total, 'case was', 'cases were')} planned; none was carried out." if total
                else "No cases were planned."]
    elif not total:
        bits = ["No cases were carried out, so nothing was shown either way."]
    elif verdict == "fails":
        bits = [f"{of(len(broke))} broke the property."] + tail()
    elif verdict == "needs_decision":
        bits = [declined_txt(len(declined)) + " That needs your decision."]
        if held:
            bits.append(f"{of(held)} held.")
        bits += tail(skip_declined=True)
    elif verdict == "holds":
        bits = [f"{of(held)} held."] + tail()
        if plan.get("kind") == "code":
            bits.append("This rests on reading the code, which is weaker evidence than observing the app.")
    elif not_run:      # can't tell, because some planned cases were not run
        bits = [f"{of(len(not_run))} could not be run."]
        if held:
            bits.append(f"{held} held.")
        bits += [b for b in tail() if "could not be run" not in b]
    elif not held and not broke:
        bits = ["No planned case fell under the property, so nothing was shown."] + tail()
    else:
        bits = [f"{of(held)} held."] + tail()
    return f'<p class="count">{esc(" ".join(bits))}</p>'


# ---------- one case's evidence ----------

def clip_block(cid: str, clips: list, cdir, rel) -> str:
    """Each film of the case, linked by a path relative to report.html (never embedded). Only files that exist."""
    if cdir is None or rel is None:
        return ""
    kept = [c for c in (d(x) for x in clips)
            if isinstance(c.get("file"), str) and re.fullmatch(r"clip-\d+\.webm", c["file"]) and (cdir / c["file"]).is_file()]
    out = []
    for i, c in enumerate(kept):
        try_word = "" if len(kept) == 1 else (" (first try)" if i == 0 else " (second try)" if i == 1 else f" (try {i + 1})")
        cap = (f"Film of case {esc(cid)}{try_word}: the app's page as the verifier drove it. The arrow is drawn by the "
               "verifier to show where it clicked; a red ring marks each click.")
        out.append(f'<figure class="clip"><video controls preload="metadata" src="{esc(quote(rel + "/" + c["file"]))}">'
                   f'</video><figcaption>{cap}</figcaption></figure>')
    return "".join(out)


def case_details(case: dict, r: dict, cdir, kind, rel=None) -> str:
    cid = text(case.get("id")) or "(no id)"
    m = d(r.get("meta"))
    rec = read_receipts(cdir) if cdir is not None else []
    parts = []
    baseline = text(r.get("baseline"))
    if baseline.strip():
        parts.append('<div class="sub">Measured by the verifier before this case touched the app</div>'
                     f'<pre>{trunc(baseline, 4000)}</pre>')
    if m.get("attempts") == 2:
        parts.append('<p class="note">This case needed a second attempt: the first conversation ended without a '
                     'result.</p>')
    for sid in lst(r.get("screenshots")):
        png = cdir / f"{sid}.png" if (cdir is not None and safe_name(sid)) else None
        if png is not None and png.is_file():
            parts.append(f'<img alt="Screenshot {esc(sid)} for case {esc(cid)}" src="data:image/png;base64,'
                         f'{base64.b64encode(png.read_bytes()).decode()}">')
        else:
            parts.append(f'<p class="note">Screenshot {esc(sid)} was listed but its file is missing.</p>')
    parts.append(clip_block(cid, lst(r.get("clips")), cdir, rel))

    exs = lst(r.get("excerpts"))
    if exs:
        parts.append('<div class="sub">Code excerpts</div>')
    for ex in exs:
        ex = d(ex)
        start = ex.get("start") if isinstance(ex.get("start"), int) else 1
        lines = [text(ln) for ln in lst(ex.get("lines"))]
        code = "\n".join(f"{start + i:4}  {ln}" for i, ln in enumerate(lines))
        rng = (f"lines {esc(ex.get('start'))} to {esc(ex.get('end'))}" if ex.get("start") is not None
               else "line range not recorded")
        parts.append(f'<p class="why">{esc(ex.get("why") or "")}</p>'
                     f'<div class="path">{esc(ex.get("path") or "path not recorded")}, {rng}</div><pre>{esc(code)}</pre>')

    for ch in lst(r.get("checks")):
        parts.append(check_block(ch))

    if kind == "measurement":
        meas = [x for x in rec if x.get("tool") in MEASURE_TOOLS]
        if meas:
            parts.append('<div class="sub">Measurements and actions, in order</div>')
            for x in meas:
                mark = ' <span class="refused">(refused: this call was not run)</span>' if x.get("refused") else ""
                parts.append(f'<div class="path">{esc(x.get("tool"))}({esc(fmt_args(x.get("args")))}){mark}</div>'
                             f'<pre>{trunc(x.get("result", ""), MEAS_LIMIT)}</pre>')
        else:
            parts.append('<p class="note">No measurement receipts were found for this case.</p>')

    notes = [n for n in lst(r.get("notes")) if n]
    if notes:
        parts.append('<div class="sub">Notes</div><ul class="plain">'
                     + "".join(f"<li>{esc(text(n))}</li>" for n in notes) + "</ul>")
    if r.get("decided_by_harness"):
        parts.append('<div class="caution">The verifier never submitted an accepted result for this case, so the '
                     'harness recorded it as could not run on its own.</div>')
    for rj in lst(m.get("rejected")):
        parts.append(f'<p class="note">The harness refused a result the verifier tried to submit, because: '
                     f'{esc(text(rj))}</p>')
    refused = lst(m.get("refused_tools"))
    if refused:
        parts.append(f'<p class="note">Tools the verifier asked for but was not allowed to use: '
                     f'{esc(", ".join(text(x) for x in refused))}.</p>')
    parts.append(tool_calls(rec))
    parts.append(f'<div class="meta">{meta_line(m)}</div>')
    is_open = " open" if outcome_of(r) in ("breaks", "declined") else ""
    return (f'<details class="case-ev"{is_open} id="ev-{esc(cid)}"><summary>Case {esc(cid)}: evidence</summary>'
            + "".join(parts) + "</details>")


def tool_calls(rec: list) -> str:
    if not rec:
        return '<p class="note">No tool calls were recorded.</p>'
    rows = []
    for x in rec:
        ref = "[refused, not run] " if x.get("refused") else ""
        rows.append(f"{ref}{esc(x.get('tool'))}({esc(fmt_args(x.get('args')))}) returned: "
                    f"{trunc(x.get('result', ''), ALL_LIMIT)}")
    nref = sum(1 for x in rec if x.get("refused"))
    extra = f", {nref} refused" if nref else ""
    return (f'<div class="sub">All {len(rec)} tool calls{extra}</div>'
            f'<pre>{(chr(10) * 2).join(rows)}</pre>')


def meta_line(m: dict) -> str:
    bits = [f"{esc(m['seconds'])} seconds" if num(m.get("seconds")) is not None else "time not recorded",
            f"{esc(m['turns'])} turns" if num(m.get("turns")) is not None else "turns not recorded"]
    bits.append(cost_txt(m["cost_usd"]) if num(m.get("cost_usd")) is not None else "cost not recorded")
    return ", ".join(bits)


# ---------- property sections ----------

STATUS_WORDS = {"found": "found by the search",
                "to check": "kept to be safe: a label dismissed it, but its file is on this page",
                "flagged": "flagged: builds its request address from parts"}
# The steps of the search in service/trace.py (Tracer.run_trace), in the order it runs them.
STEPS = [("start", "The lines the search started from"),
         ("writers", "Other lines that may carry out the same action"),
         ("callers", "Lines that name the code that carries out the action"),
         ("routes", "Lines that mention a route to it"),
         ("built addresses", "Places that build a request address from parts")]
# Every label service/trace.py can record (LABEL_QUESTIONS in service/prompts.py, plus "start" and "unlabelled").
LABEL_WORDS = {"start": "the starting point",
               "does_it": "carries out the action", "other_write": "writes the data in another way",
               "not_a_write": "does not carry out the action",
               "calls": "calls it", "definition": "the definition", "import": "an import",
               "same_name": "same name, different thing", "mention": "a mention",
               "defines": "the server side of the route", "could_reach": "could reach it",
               "cannot": "goes somewhere else", "unlabelled": "not labelled, so treated as relevant",
               # labels from the older search design, so older runs still read plainly
               "changes": "changes it", "reads": "only reads it", "setup": "set-up or reset", "unrelated": "unrelated"}
# What became of a hit, by step and label: kept (led on), dismissed, or neither (a definition, import or server side).
KEPT_LABELS = {"start": ("start",), "writers": ("does_it", "unlabelled"), "callers": ("calls", "unlabelled"),
               "routes": ("calls", "unlabelled"), "built addresses": ("could_reach", "unlabelled")}
DISMISSED_LABELS = {"writers": ("other_write", "not_a_write"), "callers": ("same_name", "mention"),
                    "routes": ("mention",), "built addresses": ("cannot",)}


def label_words(label) -> str:
    """Plain words for a hit's label; an unknown label is shown with its underscores turned into spaces."""
    lab = text(label)
    if not lab:
        return "not labelled"
    return LABEL_WORDS.get(lab) or lab.replace("_", " ")


def hit_fate(r: dict) -> str:
    """'kept', 'dismissed' or 'other' for one row of the search record."""
    step, lab = r.get("step"), text(r.get("label"))
    if lab in KEPT_LABELS.get(step, ()):
        return "kept"
    if lab in DISMISSED_LABELS.get(step, ()):
        return "dismissed"
    return "other"
TRACED_SENTENCE = ("The ways in were found by searching the code, not by a model's reading of it: plain code ran each "
                   "search, and every hit was labelled by its own short model call.")
UNTRACED_SENTENCE = ("The ways in below were found by a model reading the code. No search checked that they are "
                     "complete.")


def prop_trace(p: dict) -> dict:
    return d(p.get("trace")) or d(d(p.get("discovery")).get("trace"))


def where_line(path, line) -> str:
    return f"{esc(path or 'file not recorded')}" + (f":{esc(line)}" if line not in (None, "") else "")


def chain_html(e: dict) -> str:
    links = [d(x) for x in lst(e.get("chain"))]
    if not links:
        return '<div class="chain">The chain of files from this way in to its page was not recorded.</div>'
    first = links[0]
    note = text(first.get("note"))
    bits = [f"<code>{where_line(first.get('path'), first.get('line'))}</code>" + (f" ({esc(note)})" if note else "")]
    for x in links[1:]:
        bits.append(f"<code>{where_line(x.get('path'), x.get('line'))}</code>")
    return '<div class="chain">' + " is used by ".join(bits) + "</div>"


def ways_table(eps: list) -> str:
    rows = []
    for e in eps:
        door = e.get("door")
        st = e.get("status")
        status = STATUS_WORDS.get(st) or (text(st) if st else (
            f"found by: {text(e.get('found_by'))}" if e.get("found_by") else "not recorded"))
        page = text(e.get("page")) or (text(e.get("route")) if door == "api" else "") or "not recorded"
        rows.append(f"<tr><td>{esc(e.get('id'))}</td>"
                    f"<td>{esc(ENTRY_DOORS.get(door, door or 'not recorded'))}</td>"
                    f"<td>{esc(page)}</td><td>{esc(text(e.get('where')) or 'not recorded')}</td>"
                    f"<td>{esc(status)}</td></tr>")
        if door == "browser":
            rows.append(f'<tr class="chainrow"><td></td><td colspan="4">{chain_html(e)}</td></tr>')
    return (f'<div class="scroll"><table class="ways"><tr><th>Id</th><th>Way in</th><th>Page</th>'
            f'<th>What a person does there</th><th>Status</th></tr>{"".join(rows)}</table></div>')


def searched_block(tr: dict) -> str:
    out = ['<div class="sub">What was searched</div><dl class="read">']
    out.append(f"<dt>What it is about in the code</dt><dd>{esc(text(tr.get('data')) or 'not recorded')}</dd>")
    pats = [text(x) for x in lst(tr.get("patterns"))]
    out.append("<dt>Search patterns</dt><dd>" + (", ".join(f"<code>{esc(x)}</code>" for x in pats) or "not recorded")
               + "</dd>")
    roots = [d(r) for r in lst(tr.get("roots"))]
    out.append("<dt>The code that carries out the action</dt><dd>" + ("; ".join(
        f"<code>{esc(text(r.get('name')) or 'top-level code')}</code> at <code>{where_line(r.get('path'), r.get('line'))}</code>"
        for r in roots) or "not recorded") + "</dd>")
    routes = [d(r) for r in lst(tr.get("routes"))]
    items = []
    for r in routes:
        s = f"<code>{esc(' '.join(x for x in (text(r.get('method')), text(r.get('path'))) if x) or 'route not recorded')}</code>"
        if r.get("file"):
            s += f", served by <code>{esc(r.get('file'))}</code>"
        if r.get("inferred"):
            s += " (inferred from the addresses that file handles)"
        items.append(s)
    out.append("<dt>Routes that reach it</dt><dd>" + ("; ".join(items) or "none found") + "</dd></dl>")
    return "".join(out)


def hit_counts(rows: list, excluded) -> str:
    """One line summing up the rows shown below it. Starting points are counted on their own; the other rows are
    search hits, each kept, dismissed, not shown on any page, or a definition, import or server side."""
    ex_files = {text(d(x).get("file")) for x in lst(excluded)} if isinstance(excluded, list) else None
    starts = kept = dismissed = unseen = rest = 0
    for r in rows:
        step, fate, path = r.get("step"), hit_fate(r), text(r.get("path"))
        if step == "start":
            starts += 1
            continue
        # rows whose own file became a candidate way in; if that file reached no page, it was left out
        # (a writer that carries out the action becomes one when it is top-level code or a page's own code)
        creates = ((step == "callers" and fate == "dismissed") or (step == "routes" and text(r.get("label")) != "defines")
                   or (step in ("built addresses", "writers") and fate == "kept"))
        if ex_files is not None and creates and path in ex_files:
            unseen += 1
        elif fate == "kept":
            kept += 1
        elif fate == "dismissed":
            dismissed += 1
        else:
            rest += 1
    n = len(rows) - starts
    were = lambda k: f"{k} was" if k == 1 else f"{k} were"  # noqa: E731
    lead = (f"The search started from {plural(starts, 'line', 'lines')} that carry out the action"
            if starts != 1 else "The search started from 1 line that carries out the action")
    if not n:
        s = (lead + "; it found no other lines") if starts else "No search hits were recorded"
        return s
    bits = [f"{kept} led to a way in or to code that carries out the action", f"{were(dismissed)} dismissed"]
    if unseen:
        bits.append(f"{were(unseen)} not shown on any page")
    s = f"{plural(n, 'search hit', 'search hits')}: " + join_and(bits)
    if rest:
        s += (f"; the other {rest} {'was a definition, an import, the server side of a route or not recorded' if rest == 1 else 'were definitions, imports, the server side of a route or not recorded'}")
    return (f"{lead}, then found {s[0].lower()}{s[1:]}" if starts else s)


def hits_block(tr: dict) -> str:
    rows = [d(r) for r in lst(tr.get("rows"))]
    groups = [(k, h, [r for r in rows if r.get("step") == k]) for k, h in STEPS]
    known = {k for k, _ in STEPS}
    other = [r for r in rows if r.get("step") not in known]
    if other:
        groups.append((None, "Other search hits", other))
    body = []
    for _, head, rs in groups:
        if not rs:
            continue
        trs = "".join(
            f"<tr><td><code>{where_line(r.get('path'), r.get('line'))}</code></td>"
            f"<td><code>{trunc(text(r.get('text')), 120)}</code></td>"
            f"<td>{esc(label_words(r.get('label')))}</td>"
            f"<td>{esc(text(r.get('reason')))}</td><td>{esc(text(r.get('outcome')) or 'not recorded')}</td></tr>"
            for r in rs)
        body.append(f'<div class="sub">{head}</div><div class="scroll"><table class="hits"><tr><th>Where</th>'
                    f'<th>The line of code</th><th>Label</th><th>Reason</th><th>What became of it</th></tr>{trs}'
                    f'</table></div>')
    if not body:
        body.append('<p class="note">No search hits were recorded.</p>')
    return ('<div class="sub">Every search hit, and what became of it</div>'
            f'<details class="hits"><summary>{esc(hit_counts(rows, tr.get("excluded")))}</summary>'
            + "".join(body) + "</details>")


def excluded_block(tr: dict) -> str:
    ex = [d(x) for x in lst(tr.get("excluded"))]
    if not ex:
        return ""
    trs = "".join(f"<tr><td><code>{esc(text(x.get('file')) or 'file not recorded')}</code></td>"
                  f"<td>{esc(text(x.get('why')) or 'not recorded')}</td>"
                  f"<td>not shown on any page, so no one can reach it through the app</td></tr>" for x in ex)
    return ('<div class="sub">Found by the search but not shown on any page</div><div class="scroll"><table class="excluded">'
            f'<tr><th>File</th><th>Why the search found it</th><th>What became of it</th></tr>{trs}</table></div>')


def limits_block(tr: dict) -> str:
    out = ['<div class="limits"><div class="sub">Limits of this search</div>']
    bs = [text(x) for x in lst(tr.get("blind_spots")) if x]
    out.append('<ul class="plain">' + "".join(f"<li>{esc(x)}</li>" for x in bs) + "</ul>" if bs
               else '<p class="note">No limits were recorded.</p>')
    notes = [text(x) for x in lst(tr.get("notes")) if x]
    if notes:
        out.append('<div class="sub">In this run</div><ul class="plain">'
                   + "".join(f"<li>{esc(x)}</li>" for x in notes) + "</ul>")
    out.append("</div>")
    return "".join(out)


def ways_in(plan: dict, tr: dict) -> str:
    out = ['<div class="sub">How the ways in were found, and what was left out</div>']
    traced = plan.get("traced") is True
    if traced:
        out.append(f'<p class="note">{TRACED_SENTENCE}</p>')
    else:
        out.append(f'<p class="note">{UNTRACED_SENTENCE}'
                   + (f" {esc(text(plan['how_found']))}" if plan.get("how_found") else "") + "</p>")
    eps = [d(e) for e in lst(plan.get("entry_points"))]
    out.append(ways_table(eps) if eps else '<p class="note">No ways in were recorded.</p>')
    if traced or tr:
        if not tr:
            out.append('<p class="note">The record of the search was not kept, so its hits cannot be shown.</p>')
        out.append(searched_block(tr))
        out.append(hits_block(tr))
        out.append(excluded_block(tr))
        out.append(limits_block(tr))
    out.append(f'<p class="note"><b>Not covered:</b> {esc(text(plan.get("not_covered")) or "not recorded")}</p>')
    refs = [d(r) for r in lst(plan.get("code_refs"))]
    if refs:
        items = [f"<code>{esc(r.get('path'))}" + (f":{esc(r.get('line'))}" if r.get("line") is not None else "")
                 + "</code>" for r in refs]
        out.append(f'<p class="note">Code it points to: {", ".join(items)}</p>')
    return "".join(out)


def shell_dq(x: str) -> str:
    """Wrap in double quotes for a POSIX shell, escaping what double quotes don't protect."""
    return '"' + "".join("\\" + ch if ch in '\\"$`' else ch for ch in x) + '"'


def questions_block(p: dict, run: dict) -> str:
    qs = [d(q) for q in lst(p.get("questions"))]
    if not qs:
        return ""
    env = d(run.get("env"))
    repo = shell_dq(text(env.get("repo"))) if text(env.get("repo")) else '"<repo>"'
    feat = shlex.quote(text(run.get("feature"))) if text(run.get("feature")) else "<feature>"
    pid = shlex.quote(text(p.get("id"))) if text(p.get("id")) else "<property>"
    out = ['<div class="questions"><h3>Questions for you</h3>',
           '<p>In each case below the app openly declined the action, so the property could not be observed there. '
           '<code>intended</code> takes that situation out of this property and adds the drafted sentence as a '
           'property of its own; <code>bug</code> means a refusal there fails the property from now on.</p>']
    for q in qs:
        cid = text(q.get("case"))
        out.append(f'<p class="q">Case {esc(cid or "(not recorded)")}: '
                   f'{esc(text(q.get("question")) or "(question not recorded)")}</p>')
        out.append(f'<p>What happened in that case: {esc(text(q.get("observed")) or "not recorded")}</p>')
        out.append(f'<p>If this is intended, it would become its own property: '
                   f'<i>{esc(text(q.get("draft_if_intended")) or "(no sentence was drafted)")}</i></p>')
        cmd = f'.venv/bin/python groundtruth.py decide {repo} {feat} {pid} {shlex.quote(cid) if cid else "<case>"}'
        out.append(f'<pre>{esc(cmd)} intended\n{esc(cmd)} bug</pre>')
    out.append("</div>")
    return "".join(out)


def case_table(cases: list, plan: dict) -> str:
    rows = []
    for c in cases:
        r = c["_r"]
        o = outcome_of(r)
        door = case_door(c, plan)
        rows.append(f'<tr class="{o}"><td class="id">{esc(c.get("id"))}</td>'
                    f'<td>{esc(CASE_DOORS.get(door, door or "not recorded"))}</td>'
                    f'<td>{esc(text(c.get("what")) or "not recorded")}</td>'
                    f'<td>{esc(text(c.get("represents")) or "not recorded")}</td>'
                    f'<td><span class="pill small {OUTCOME_CLASS[o]}">{outcome_words(r)}</span></td>'
                    f'<td>{esc(text(r.get("observed")) or "nothing recorded")}</td></tr>')
    return ('<div class="scroll"><table class="cases"><tr><th>Case</th><th>Way in</th><th>What was done</th>'
            '<th>What it stands for</th><th>Outcome</th><th>What was observed</th></tr>'
            + "".join(rows) + "</table></div>")


def discovery_counts(dm: dict, tr: dict):
    """Discovery conversations (1 unless recorded) and short labelling calls."""
    c = num(dm.get("conversations"))
    convs = int(c) if c is not None else 1
    lab = num(dm.get("labels"))
    if lab is None:
        lab = num(tr.get("labels"))
    return convs, int(lab or 0)


def labels_txt(n: int) -> str:
    return f" plus {plural(n, 'short labelling call', 'short labelling calls')}" if n else ""


def discovery_block(p: dict, pdir, dm: dict, plan: dict, tr: dict) -> str:
    rec = read_receipts(pdir / "discovery") if pdir is not None else []
    parts = []
    convs, labels = discovery_counts(dm, tr)
    secs = f"{esc(dm['seconds'])} seconds" if num(dm.get("seconds")) is not None else "time not recorded"
    cost = cost_txt(dm["cost_usd"]) if num(dm.get("cost_usd")) is not None else "cost not recorded"
    parts.append(f'<p class="note">Finding the ways in and planning the cases took '
                 f'{plural(convs, "conversation", "conversations")}'
                 + (f" and {plural(labels, 'short labelling call', 'short labelling calls')}" if labels else "")
                 + f", {secs} and {cost}.</p>")
    if num(tr.get("label_cost_usd")) is not None and labels:
        parts.append(f'<p class="note">The labelling calls cost {cost_txt(tr["label_cost_usd"])}.</p>')
    for rj in lst(dm.get("rejected")):
        parts.append(f'<p class="note">The service refused a plan the verifier tried to submit, because: '
                     f'{esc(text(rj))}</p>')
    refused = lst(dm.get("refused_tools"))
    if refused:
        parts.append(f'<p class="note">Tools the verifier asked for but was not allowed to use: '
                     f'{esc(", ".join(text(x) for x in refused))}.</p>')
    parts.append(tool_calls(rec))
    parts.append(f'<div class="meta">{meta_line(dm)}</div>')
    intro = ("Before any case was carried out, plain code searched the app's code for the ways in, a short model "
             "call labelled each search hit, and the verifier planned the cases." if plan.get("traced") is True else
             "Before any case was carried out, the verifier explored the app and its code to work out the ways in "
             "and plan the cases.")
    return ('<details class="discovery"><summary>How discovery worked</summary>'
            f'<p class="note">{intro}</p>' + "".join(parts) + "</details>")


def prop_totals(p: dict, pdir, cases: list, dm: dict, tr: dict):
    """Conversations, seconds, cost and labelling calls for one property. Discovery counts if there is any trace of
    it (as many conversations as recorded, else 1); a case counts if it has a meta record or a receipts file (cases
    written after an abort have neither)."""
    convs, secs, cost, dconvs, labels = 0, 0.0, 0.0, 0, 0
    if dm or p.get("discovery") or tr or (pdir is not None and (pdir / "discovery").is_dir()):
        dconvs, labels = discovery_counts(dm, tr)
        convs += dconvs
    secs += num(dm.get("seconds")) or 0
    cost += num(dm.get("cost_usd")) or 0
    for c in cases:
        m = d(c["_r"].get("meta"))
        cdir = pdir / str(c.get("id")) if (pdir is not None and safe_name(c.get("id") or "")) else None
        if m or (cdir is not None and (cdir / "receipts.jsonl").is_file()):
            convs += 1
        secs += num(m.get("seconds")) or 0
        cost += num(m.get("cost_usd")) or 0
    return convs, secs, cost, dconvs, labels


def section(p: dict, run: dict, run_dir: Path):
    pid = text(p.get("id")) or "(no id)"
    pdir = prop_dir(run_dir, p)
    plan = prop_plan(p)
    kind = plan.get("kind")
    raw = p.get("verdict")
    verdict = raw if raw in VERDICTS else "cant_tell"
    pill = esc(VERDICTS[verdict]) if raw in VERDICTS else (
        f"can't tell (recorded as {esc(raw)})" if raw else "can't tell (no verdict recorded)")
    cases = []
    for c in lst(p.get("cases")):
        c = dict(d(c))
        c["_r"] = case_result(c, pdir)
        cases.append(c)
    rank = {o: i for i, o in enumerate(OUTCOME_ORDER)}
    idx = sorted(range(len(cases)), key=lambda i: (rank[outcome_of(cases[i]["_r"])], i))
    cases = [cases[i] for i in idx]
    dm = discovery_meta(p, pdir)
    tr = prop_trace(p)

    parts = [f'<section class="card prop" id="prop-{esc(pid)}"><div class="head"><h2>Property {esc(pid)}</h2>'
             f'<span class="pill {verdict}">{pill}</span></div>'
             f'<p class="statement">{esc(text(p.get("statement")) or "(the property sentence was not recorded)")}</p>']
    if p.get("example"):
        parts.append(f'<p class="example">Example given by the owner: {esc(text(p["example"]))}</p>')
    parts.append(count_line(p, cases, plan))

    if plan:
        rows = [("Applies to", plan.get("applies_to")), ("The action", plan.get("action")),
                ("What is compared", plan.get("compares"))]
        dl = "".join(f"<dt>{k}</dt><dd>{esc(text(v)) or 'not recorded'}</dd>" for k, v in rows)
        how = f" It is checked by {esc(KINDS[kind])}." if kind in KINDS else ""
        parts.append(f'<div class="sub">How the verifier read it</div><dl class="read">{dl}</dl>'
                     + (f'<p class="note">{how.strip()}</p>' if how else ""))
    parts.append(questions_block(p, run))
    if plan:
        parts.append(ways_in(plan, tr))
    else:
        why = text(p.get("no_plan_because"))
        parts.append('<div class="caution">Discovery produced no plan, so no cases were carried out for this property.'
                     + (f" The reason given: {esc(why)}" if why else " No reason was recorded.") + '</div>')

    if cases:
        parts.append('<div class="sub">Cases</div><p class="note">Each case was carried out and judged by a '
                     'conversation that had the property and the case, but not the code.</p>')
        parts.append(case_table(cases, plan))
        for c in cases:
            cdir = pdir / str(c.get("id")) if (pdir is not None and safe_name(c.get("id") or "")) else None
            rel = f"{pid}/{c.get('id')}" if cdir is not None else None
            parts.append(case_details(c, c["_r"], cdir, kind, rel))
    elif plan:
        parts.append('<p class="note">The plan listed no cases.</p>')

    parts.append(discovery_block(p, pdir, dm, plan, tr))
    convs, secs, cost, dconvs, labels = prop_totals(p, pdir, cases, dm, tr)
    disc = f"{dconvs} discovery plus the cases carried out" if dconvs else "the cases carried out; no discovery was recorded"
    parts.append(f'<div class="foot">This property took {plural(convs, "conversation", "conversations")} '
                 f'({disc}){labels_txt(labels)}, {secs:.1f} seconds and {cost_txt(cost)} in all.</div>'
                 if convs else '<div class="foot">No conversations were recorded for this property.</div>')
    parts.append("</section>")
    return "".join(parts), (convs, secs, cost, labels)


def ordered(props: list) -> list:
    rank = {v: i for i, v in enumerate(VERDICT_ORDER)}
    idx = sorted(range(len(props)), key=lambda i: (rank.get(props[i].get("verdict"), rank["cant_tell"]), i))
    return [props[i] for i in idx]


def write_report(run_dir: Path) -> Path:
    run_dir = Path(run_dir)
    run = d(json.loads((run_dir / "run.json").read_text(encoding="utf-8")))
    props = [d(p) for p in lst(run.get("properties"))]
    counts = {}
    for p in props:
        v = p.get("verdict") if p.get("verdict") in VERDICTS else "cant_tell"
        counts[v] = counts.get(v, 0) + 1
    tally = "".join(f'<span class="pill {k}">{counts[k]} {VERDICTS[k]}</span>' for k in VERDICT_ORDER if k in counts)
    if not props:
        tally = '<span class="note">No properties were recorded.</span>'
    alerts = ""
    if run.get("aborted"):
        alerts += ('<div class="alert">This run was stopped before it finished. '
                   'Cases marked could not run were never carried out.</div>')
    if run.get("error"):
        alerts += f'<div class="alert">This run reported an error: {trunc(run["error"], 2000)}</div>'
    body, tc, ts, tcost, tl = [], 0, 0.0, 0.0, 0
    for p in ordered(props):
        html_, (c, s, k, lb) = section(p, run, run_dir)
        body.append(html_)
        tc, ts, tcost, tl = tc + c, ts + s, tcost + k, tl + lb
    title = run.get("feature") or "Groundtruth proof"
    final = (f'The whole run took {plural(tc, "conversation", "conversations")}{labels_txt(tl)}, {ts:.1f} seconds and '
             f'{cost_txt(tcost)}. Run {esc(run.get("run") or "id not recorded")}.')
    page = (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="color-scheme" content="light dark">'
            f'<title>Groundtruth proof: {esc(title)}</title><style>{CSS}</style></head><body><main>'
            f'<h1>{esc(title)}</h1><p class="goal">{esc(run.get("goal") or "")}</p>'
            f'<div class="tally">{tally}</div>{alerts}{env_section(run, props)}{"".join(body)}'
            f'<div class="meta">{final}</div></main></body></html>')
    out = run_dir / "report.html"
    out.write_text(page, encoding="utf-8")
    return out
