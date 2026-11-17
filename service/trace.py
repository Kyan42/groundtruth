"""Tracing the ways in: plain code searches, a model only points at the start and labels what the search found.

  1. anchor    one conversation reads the code and points at the exact lines that carry out the property's action
               (for a cart, the line that writes the item into the stored cart), and gives search patterns for the
               NAME of the data those lines write. This is the one judgement the rest hangs on.
  2. writers   plain code searches for that name and keeps the lines that look like a write and are not inside the
               code already pointed at. Each gets its own short model call: is this the same action by another
               route? A yes adds it to the starting points. This is how a second way of doing the action that
               bypasses the first is found.
  3. callers   from each starting function, plain code searches for the function's name; each hit is labelled
               (calls / definition / import / same name / mention), and callers are followed the same way until a
               file that serves an API route, or a page.
  4. routes    plain code searches for each route's address; each hit is labelled (calls / defines / mention).
  5. pages     from every file that mentions a route, plain code follows "who uses this file" up to the pages.
               A file that reaches a page becomes a way in WHATEVER its label says (a dismissed one is marked
               "to check"); a file that reaches no page is excluded and listed. Places that build a request address
               from parts are found separately and become "flagged" ways in unless clearly unrelated.

Every labelled hit ends up in the accounting table with its label, the reason and what became of it, so nothing
the search found can be silently dropped. What this cannot see is listed in BLIND_SPOTS.
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor

from . import prompts

# A page reference such as [ref=e12] is only good for one look at a page, so it must never end up in a plan.
PAGE_REF = re.compile(r"\bref(?:erence)?s?\b\W{0,3}(?:f\d+)?e\d+\b", re.I)
PAGE_REF_PROBLEM = ("{what} names a page reference (such as e12). A reference changes every time a page is looked at: "
                    "describe the control in plain words instead (what it says, where it is on the page)")

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
MAX_DATA_HITS, MAX_SYMBOL_HITS, MAX_ROUTE_HITS, MAX_SYMBOLS, MAX_LABELS, MAX_PAGES_PER_FILE = 80, 60, 60, 25, 120, 3
BLIND_SPOTS = [
    "A request address that is built from parts is only found if the code that builds it matches one of the known request forms.",
    "Callers outside this repo (another service, a mobile app, a scheduled job elsewhere) are not searched.",
    "A way in that should exist but was never written cannot be found by searching the code.",
    "The search goes by name. Code reached only through a framework convention or a name built at run time has no name to follow.",
    "A second place that writes the same data is only noticed if the write is visible on one line under the data's own name; a write through another variable name, or by replacing a whole containing object, can be missed.",
]


class TraceError(Exception):
    pass


def anchor_problems(rep):
    for key in ("applies_to", "action", "compares"):
        if not (isinstance(rep.get(key), str) and rep[key].strip()):
            return f"{key} is required"
    if rep.get("kind") not in ("behaviour", "measurement", "code"):
        return "kind must be behaviour, measurement or code"
    if not isinstance(rep.get("never"), bool):
        return "never must be true or false"
    if rep["kind"] == "code":
        return None
    if not (isinstance(rep.get("data"), str) and rep["data"].strip()):
        return "data is required: name the stored data (or the operation) the action is about, in plain words"
    roots = rep.get("roots")
    if not isinstance(roots, list) or not 1 <= len(roots) <= 6:
        return 'give 1 to 6 roots: {"path", "line"} of each line that itself carries out the action'
    for r in roots:
        if not (isinstance(r, dict) and isinstance(r.get("path"), str) and isinstance(r.get("line"), int) and not isinstance(r["line"], bool)):
            return 'each root needs "path" (a file) and "line" (a number)'
    pats = rep.get("patterns")
    if not isinstance(pats, list) or not 1 <= len(pats) <= 3 or not all(isinstance(p, str) and p.strip() for p in pats):
        return "give 1 to 3 search patterns for the name of the data"
    for p in pats:
        try:
            re.compile(p)
        except re.error as e:
            return f"pattern {p!r} is not a valid regular expression ({e})"
    return None


class Tracer:
    def __init__(self, run, prop, unit):
        self.run, self.prop, self.unit = run, prop, unit
        self.rows, self.notes, self.labels_used, self.cost = [], [], 0, 0.0

    # ---- the two kinds of work: plain code on the user's machine, and one small model call per hit
    def op(self, op, **args):
        res = self.run.call_local("trace", {"op": op, **args})
        if res["isError"]:
            raise TraceError(f"{op}: {res['text']}")
        return res.get("data") or {}

    def _label_one(self, n, kind, tracing, what, hit):
        allowed = list(prompts.LABEL_QUESTIONS[kind][1])
        msg = prompts.label_message(kind, self.prop, tracing, what, hit, hit["lines"])
        for attempt in (1, 2):
            res = self.run._claude(msg, prompts.LABEL, ["--max-turns", "1"], f"{self.unit.replace('/', '-')}-label-{n}-{attempt}", timeout=120)
            self.cost += res.get("total_cost_usd") or 0
            try:
                out = json.loads(re.search(r"\{.*\}", res.get("result", ""), re.S).group(0))
                if out.get("label") in allowed:
                    return {"label": out["label"], "reason": str(out.get("reason", ""))[:300]}
            except Exception:  # noqa: BLE001
                pass
        return {"label": "unlabelled", "reason": "The labelling call gave no usable answer, so this hit is treated as relevant."}

    def label_all(self, kind, tracing, what, hits):
        """One model call per hit, a few at a time. More hits than the budget allows stops the whole search (TraceError):
        hits are never dropped silently, and the run falls back to a model reading the code and says so."""
        for h in hits:                                   # the lines around each hit, fetched by plain code (15 at most)
            ctx = self.op("context", path=h["path"], line=h["line"])
            h["lines"] = [(ctx["start"] + i, s) for i, s in enumerate(ctx["lines"])]
        room = max(0, MAX_LABELS - self.labels_used)
        if len(hits) > room:
            raise TraceError(f"the search matched more lines than can be labelled ({self.labels_used + len(hits)} so far, limit {MAX_LABELS}); "
                             "what the property is about was probably named too broadly")
        start = self.labels_used
        self.labels_used += len(hits)
        with ThreadPoolExecutor(6) as ex:
            return list(ex.map(lambda p: self._label_one(start + p[0], kind, tracing, what, p[1]), enumerate(hits)))

    def row(self, step, what, hit, label, outcome):
        self.rows.append({"step": step, "searched_for": what, "path": hit["path"], "line": hit["line"], "text": hit.get("text", "")[:200],
                          "label": label["label"], "reason": label["reason"], "outcome": outcome})

    # ---- the trace
    def run_trace(self, anchor):
        data, action, roots = anchor["data"], anchor["action"], {}
        measuring = anchor["kind"] == "measurement"
        # 1. the lines the anchor pointed at
        for r in anchor["roots"]:
            enc = self.op("enclosing", path=r["path"], line=r["line"])
            ctx = self.op("context", path=r["path"], line=r["line"], )
            text = ctx["lines"][r["line"] - ctx["start"]].strip() if ctx["start"] <= r["line"] <= ctx["end"] else ""
            roots.setdefault((r["path"], enc.get("name")), {"path": r["path"], "name": enc.get("name"), "line": enc.get("line") or r["line"], "depth": 0})
            self.rows.append({"step": "start", "searched_for": data, "path": r["path"], "line": r["line"], "text": text[:200], "label": "start",
                              "reason": "The line the trace was pointed at as carrying out the action.",
                              "outcome": f"a starting point, inside {enc.get('name') or 'top-level code'}"})
        # 2. other writers of the same data, which the roots above do not go through
        hits, seen, reads = [], set(), 0
        for pat in anchor["patterns"]:
            found = self.op("search", pattern=pat)
            if found.get("truncated"):
                self.notes.append(f"The search for {pat!r} was cut off at {len(found['hits'])} of {found['total']} hits.")
            for h in found["hits"]:
                if (h["path"], h["line"]) in seen:
                    continue
                seen.add((h["path"], h["line"]))
                m = re.search(pat, h["text"])
                h = {**h, "pattern": pat, "matched": m.group(0) if m else pat}
                if not measuring and not self.op("looks_like_write", text=h["text"], name=h["matched"]).get("write"):
                    reads += 1                      # plain code sees no write on this line: counted, not labelled
                    continue
                enc = self.op("enclosing", path=h["path"], line=h["line"])
                if (h["path"], enc.get("name")) in roots:
                    continue                        # inside code that is already a starting point
                hits.append({**h, "enclosing": enc})
        if len(hits) > MAX_DATA_HITS:
            raise TraceError(f"the search patterns matched {len(hits)} lines that look like writes, more than the {MAX_DATA_HITS} that can be examined")
        if reads:
            self.notes.append(f"{reads} lines mention {data} without writing it (as far as plain code can tell); they were not followed.")
        kind = "operation" if measuring else "writer"
        what = data if measuring else data
        labels = self.label_all(kind, f"other places that carry out the action \"{action}\" on {data}", what, hits)
        for h, lab in zip(hits, labels):
            enc = h["enclosing"]
            if lab["label"] in ("does_it", "unlabelled"):
                roots.setdefault((h["path"], enc.get("name")), {"path": h["path"], "name": enc.get("name"), "line": enc.get("line") or h["line"], "depth": 0})
                self.row("writers", data, h, lab, f"another starting point, inside {enc.get('name') or 'top-level code'}")
            else:
                self.row("writers", data, h, lab, "writes it in another way; not followed" if lab["label"] == "other_write" else "not a write")

        # 3. follow each function's name upward until a route or a page
        page_files = {p["file"]: p["url"] for p in self.op("pages").get("pages", [])}
        routes, candidates, frontier, done = {}, {}, list(roots.values()), set()

        def candidate(path, status, why, line=None):
            order = {"found": 0, "flagged": 1, "to check": 2}
            cur = candidates.get(path)
            if cur is None or order[status] < order[cur["status"]]:
                candidates[path] = {"status": status, "why": why, "line": line}

        while frontier:
            sym = frontier.pop(0)
            key = (sym["path"], sym["name"])
            if key in done:
                continue
            done.add(key)
            served = self.op("route_of", path=sym["path"]).get("routes", [])
            certain = [r for r in served if r.get("certain")]
            if certain:                                   # this file IS a route: callers are found by its address, not its name
                for r in certain:
                    if sym["name"] in METHODS and r["method"] not in (sym["name"], "ANY"):
                        continue
                    routes.setdefault((r["method"], r["path"]), {"method": r["method"], "path": r["path"], "file": sym["path"], "inferred": False})
                continue
            if sym["path"] in page_files:                 # a page that does it itself, with no route in between
                candidate(sym["path"], "found", f"the page's own code does it ({sym['name'] or 'top-level code'})")
            if sym["name"] is None:
                candidate(sym["path"], "found", "top-level code in this file does it")
                continue
            if len(done) > MAX_SYMBOLS or sym["depth"] >= 6:
                self.notes.append(f"Stopped following {sym['name']} ({sym['path']}): the trace reached its depth or size limit.")
                continue
            found = self.op("search", pattern=rf"\b{re.escape(sym['name'])}\b")
            uses = [h for h in found["hits"] if not (h["path"] == sym["path"] and h["line"] == sym["line"])]
            if len(uses) > MAX_SYMBOL_HITS:
                self.notes.append(f"The name {sym['name']} appears on {len(uses)} lines, too many to follow by search; its callers were not traced.")
                continue
            tracing = f"every use of `{sym['name']}`, defined at {sym['path']}:{sym['line']}, which is part of the code that changes {data}"
            labels = self.label_all("symbol", tracing, sym["name"], uses)
            called = False
            for h, lab in zip(uses, labels):
                if lab["label"] in ("calls", "unlabelled"):
                    called = True
                    enc = self.op("enclosing", path=h["path"], line=h["line"])
                    if h["path"] in page_files and sym["path"] not in page_files:
                        if sym["path"].rsplit(".", 1)[-1] in ("tsx", "jsx", "vue", "svelte", "html"):
                            # A page shows a component. The way in is that component on that page, not the page as well.
                            candidate(sym["path"], "found", f"{sym['name']} does it and is shown on a page ({h['path']}:{h['line']})")
                            self.row("callers", sym["name"], h, lab, "shown on a page; the way in is this component on that page")
                        else:
                            # A page's own code calls the logic directly, with no route in between.
                            candidate(h["path"], "found", f"the page's own code calls {sym['name']} at line {h['line']}")
                            self.row("callers", sym["name"], h, lab, "called by a page's own code; that page is a way in")
                        continue
                    frontier.append({"path": h["path"], "name": enc.get("name"), "line": enc.get("line") or h["line"], "depth": sym["depth"] + 1})
                    self.row("callers", sym["name"], h, lab, f"a caller, inside {enc.get('name') or 'top-level code'}")
                elif lab["label"] in ("same_name", "mention"):
                    candidate(h["path"], "to check", f"mentions {sym['name']} at line {h['line']}, labelled {lab['label'].replace('_', ' ')}")
                    self.row("callers", sym["name"], h, lab, "dismissed by its label; its file is still followed to the pages")
                else:
                    self.row("callers", sym["name"], h, lab, "the definition or an import")
            if not called:                                # nothing calls it by name: a framework may. Use the addresses its file handles.
                # Only for server-side code (a file no page shows), and never a page's own address or the bare "/".
                server_side = sym["path"] not in page_files and not self.op("climb", path=sym["path"]).get("reaches_page")
                inferred = [r for r in served if not r.get("certain") and server_side
                            and r["path"].strip("/") and r["path"] not in page_files.values()]
                for r in inferred:
                    routes.setdefault((r["method"], r["path"]), {"method": r["method"], "path": r["path"], "file": sym["path"], "inferred": True})
                if not inferred and sym["path"] not in page_files:
                    self.notes.append(f"Nothing calls {sym['name']} ({sym['path']}) by name and its file handles no address, so no way in was found through it.")

        # 4. every place each route's address appears
        for (method, path), r in routes.items():
            literal = re.escape(path.split("/:")[0])
            found = self.op("search", pattern=literal + r"(?![A-Za-z0-9_-])")
            uses = [h for h in found["hits"] if h["path"] != r["file"]]
            name = f"{method} {path}"
            if len(uses) > MAX_ROUTE_HITS:
                self.notes.append(f"The address of {name} appears on {len(uses)} lines, too many to follow by search; its callers were not traced.")
                continue
            labels = self.label_all("route", f"every caller of the route {name}, which reaches the code that changes {data}", name, uses)
            for h, lab in zip(uses, labels):
                if lab["label"] in ("calls", "unlabelled"):
                    candidate(h["path"], "found", f"calls {name} at line {h['line']}", h["line"])
                    candidates[h["path"]]["route"] = name
                    self.row("routes", name, h, lab, "a caller; its file is followed to the pages")
                elif lab["label"] == "defines":
                    self.row("routes", name, h, lab, "the server side of the route")
                else:
                    candidate(h["path"], "to check", f"mentions {name} at line {h['line']}, labelled a mention")
                    candidates[h["path"]].setdefault("route", name)
                    self.row("routes", name, h, lab, "dismissed by its label; its file is still followed to the pages")

        # 4b. request addresses built from parts, which the search for a literal address cannot match
        if routes:
            built = [h for h in self.op("computed_addresses").get("hits", []) if candidates.get(h["path"], {}).get("status") != "found"]
            names = ", ".join(f"{m} {p}" for m, p in routes)
            labels = self.label_all("computed", f"requests whose address is built from parts, which could reach {names}", names, built)
            for h, lab in zip(built, labels):
                if lab["label"] in ("could_reach", "unlabelled"):
                    candidate(h["path"], "flagged", f"builds a request address from parts at line {h['line']}", h["line"])
                    self.row("built addresses", names, h, lab, "flagged; its file is followed to the pages")
                else:
                    self.row("built addresses", names, h, lab, "goes somewhere else")

        # 5. from every candidate file up to the pages. A file that reaches a page is a way in whatever its label said.
        ways, excluded = [], []
        traced_files = {r["path"] for r in roots.values()} | {r["file"] for r in routes.values()}
        candidates = {p: c for p, c in candidates.items() if not (c["status"] == "to check" and p in traced_files)}
        for path, c in sorted(candidates.items(), key=lambda kv: ({"found": 0, "flagged": 1, "to check": 2}[kv[1]["status"]], kv[0])):
            up = self.op("climb", path=path)
            if not up.get("reaches_page"):
                excluded.append({"file": path, "status": c["status"], "why": c["why"], "because": "it is not shown on any page"})
                continue
            if len(up["pages"]) > MAX_PAGES_PER_FILE:
                self.notes.append(f"{path} is shown on {len(up['pages'])} pages; cases are planned on the first {MAX_PAGES_PER_FILE}.")
            for pg in up["pages"][:MAX_PAGES_PER_FILE if c["status"] == "found" else 1]:
                chain = next((ch for ch in up["chains"] if ch and ch[-1]["path"] == pg["file"]), up["chains"][0] if up["chains"] else [])
                ways.append({"door": "browser", "status": c["status"], "page": pg["url"], "page_file": pg["file"], "file": path,
                             "route": c.get("route"), "why": c["why"],
                             "chain": [{"path": path, "line": c.get("line"), "note": c["why"]}] + [{"path": l["path"], "line": l["line"], "note": f"uses it ({l.get('via', '')})"} for l in chain]})
        # a "to check" file that only sits in the chain of a found one adds nothing: its page is already covered by that way in
        covered = {(w["page"], l["path"]) for w in ways if w["status"] == "found" for l in w["chain"]}
        ways = [w for w in ways if not (w["status"] == "to check" and (w["page"], w["file"]) in covered)]
        for i, w in enumerate(ways, 1):
            w["id"] = f"e{i}"
        api = [{"id": f"a{i}", "door": "api", "status": "found", "route": f"{r['method']} {r['path']}", "file": r["file"],
                "why": "serves the route" + (" (inferred from the addresses this file handles)" if r["inferred"] else "")}
               for i, r in enumerate(routes.values(), 1)]
        return {"data": data, "patterns": anchor["patterns"], "roots": list(roots.values()), "routes": list(routes.values()),
                "ways": ways + api, "excluded": excluded, "rows": self.rows, "notes": self.notes, "blind_spots": BLIND_SPOTS,
                "labels": self.labels_used, "label_cost_usd": round(self.cost, 4)}


def cases_problems(rep, ways, never=False):
    """Why a submitted set of cases can't be accepted; None if it can. The ways in are fixed by the trace.
    The API is called directly only for a property that says something must never happen."""
    by_id = {w["id"]: w for w in ways}
    wheres = {w.get("id"): w.get("where") for w in rep.get("wheres") or [] if isinstance(w, dict)}
    for w in ways:
        if w["door"] == "browser" and not (isinstance(wheres.get(w["id"]), str) and wheres[w["id"]].strip()):
            return f'describe way in {w["id"]} in "wheres": the page and the control, in plain words'
        if PAGE_REF.search(str(wheres.get(w["id"]) or "")):
            return PAGE_REF_PROBLEM.format(what=f"way in {w['id']}")
    cases = rep.get("cases")
    if not isinstance(cases, list) or not cases:
        return "plan at least one case"
    seen = set()
    for c in cases:
        if not (isinstance(c, dict) and isinstance(c.get("id"), str) and re.fullmatch(r"[A-Za-z0-9_-]{1,32}", c["id"]) and c["id"] != "discovery"):
            return 'each case needs a short "id"'
        if c["id"] in seen:
            return f"case id {c['id']} is used twice"
        seen.add(c["id"])
        if c.get("entry_point") not in by_id:
            return f"case {c['id']} names a way in that is not in the list; you cannot add ways in"
        if by_id[c["entry_point"]]["door"] == "api" and not never:
            return (f"case {c['id']} calls the API directly, but this property does not say anything must never happen; "
                    "its proof is through the pages only")
        if not (isinstance(c.get("what"), str) and c["what"].strip()):
            return f'case {c["id"]} needs "what": the steps in plain words'
        if PAGE_REF.search(c["what"]):
            return PAGE_REF_PROBLEM.format(what=f"case {c['id']}")
    for w in ways:
        if w["door"] == "browser" and not any(c["entry_point"] == w["id"] for c in cases):
            return f"way in {w['id']} ({w['page']}) has no case; every way in through the pages gets at least one"
        if w["door"] == "api" and never and not any(c["entry_point"] == w["id"] for c in cases):
            return f"way in {w['id']} ({w['route']}) has no case; a never-property is also tried by calling each route directly"
    browser = sum(w["door"] == "browser" for w in ways)
    if len(cases) > browser + 12:
        return f"too many cases: at most one basic case per way in ({browser}) plus 12 for branches and values"
    if not isinstance(rep.get("not_covered"), str):
        return "not_covered is required (say what you left out, or that nothing was)"
    return None


def build_plan(anchor, trace, rep):
    """The plan handed to the case step and shown in the report: the trace's ways in, with the planner's descriptions and cases."""
    wheres = {w["id"]: w["where"].strip() for w in rep["wheres"] if isinstance(w, dict) and isinstance(w.get("where"), str)}
    eps = []
    for w in trace["ways"]:
        default = f"{w['page']} ({w['file']})" if w["door"] == "browser" else f"direct call to {w['route']}"
        eps.append({"id": w["id"], "door": w["door"], "where": wheres.get(w["id"]) or default, "status": w["status"],
                    "found_by": w["why"], "page": w.get("page"), "file": w.get("file"), "route": w.get("route"), "chain": w.get("chain", [])})
    refs = [{"path": r["path"], "line": r["line"]} for r in trace["roots"]] + \
           [{"path": l["path"], "line": l.get("line")} for w in trace["ways"] for l in w.get("chain", [])]
    found = sum(1 for w in trace["ways"] if w["door"] == "browser")
    how = (f"Plain code searched the codebase for {', '.join(trace['patterns'])} ({trace['data']}), then for the name of each function "
           f"that changes it, then for the address of each route that reaches it, and followed every file that mentions one up to the "
           f"pages. {trace['labels']} search hits were each labelled by their own model call. This gave {found} way(s) in through the "
           f"pages and {len(trace['routes'])} route(s); {len(trace['excluded'])} file(s) were excluded because no page shows them.")
    cases = [{"id": c["id"], "entry_point": c["entry_point"], "what": c["what"].strip(), "represents": str(c.get("represents") or "")} for c in rep["cases"]]
    return {"applies_to": anchor["applies_to"].strip(), "action": anchor["action"].strip(), "compares": anchor["compares"].strip(),
            "kind": anchor["kind"], "how_found": how, "not_covered": rep["not_covered"].strip(), "entry_points": eps, "cases": cases,
            "code_refs": refs[:40], "traced": True}
