"""Which of a feature's claims the owner approved, as they now stand in the feature file.

`approve` writes <app>/.groundtruth/approved/<feature>.json: when, and for every property exactly the parts that
were approved. `compare` (pure) says per property whether the file still says what was approved:

  approved   every part is the same (whitespace aside)
  changed    with the parts that differ, in PARTS order
  new        in the file, not in the record

and {} when there is no record for the feature at all. Parts are compared as the runner sends them (claim_parts),
so a variation written as a bare sentence gets the same id on both sides.
"""
import json
import re
import time
from pathlib import Path

PARTS = ("statement", "title", "your_words", "checked_against", "variations")
# How each part is named to a person.
PART_WORDS = {"statement": "the sentence", "title": "the title", "your_words": "your own words",
              "checked_against": "what it is checked against", "variations": "the variations"}


class ClaimsError(Exception):
    pass


def record_path(repo: Path, feature: str) -> Path:
    return Path(repo) / ".groundtruth" / "approved" / f"{feature}.json"


def _norm(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def approved_parts(prop: dict, fname: str) -> dict:
    """The parts of one property as they would be approved, or ClaimsError in plain words."""
    from .runner import ServiceError, claim_parts
    pid = prop.get("id")
    statement = prop.get("statement")
    if not isinstance(statement, str) or not statement.strip():
        raise ClaimsError(f"{fname}: property {pid} has no `statement`. Each property is one plain sentence.")
    try:
        parts = claim_parts(prop, fname)
    except ServiceError as e:
        raise ClaimsError(str(e)) from None
    return {"statement": statement.strip(), **parts}


def read_claims(repo: Path, feature: str) -> tuple[dict, list]:
    """(the feature file as loaded, [(id, parts)] in file order), checked as the runner checks it."""
    import yaml
    f = Path(repo) / ".groundtruth" / "features" / f"{feature}.yml"
    if not f.is_file():
        raise ClaimsError(f"There is no claims file for {feature}: {f} does not exist.")
    try:
        spec = yaml.safe_load(f.read_text())
    except yaml.YAMLError as e:
        line = getattr(getattr(e, "problem_mark", None), "line", None)
        where = f" near line {line + 1}" if line is not None else ""
        raise ClaimsError(f"{f.name} is not valid YAML{where}. A sentence that contains a colon followed by a space "
                          "has to be in double quotes, or written on the line after `statement: >-`.") from None
    if not isinstance(spec, dict) or not isinstance(spec.get("properties"), list) or not spec["properties"]:
        raise ClaimsError(f"{f.name} has no properties. A property is one plain sentence: a general rule about the app.")
    out, seen = [], set()
    for n, p in enumerate(spec["properties"], 1):
        if not isinstance(p, dict) or not isinstance(p.get("id"), str) or not p["id"].strip():
            raise ClaimsError(f"{f.name}: property {n} has no `id`.")
        if p["id"] in seen:
            raise ClaimsError(f"{f.name}: the id {p['id']} is used twice. Each property needs its own id.")
        seen.add(p["id"])
        out.append((p["id"], approved_parts(p, f.name)))
    from .runner import ServiceError, claim_source, not_checked
    try:
        not_checked(spec, f.name)
        for p in spec["properties"]:
            claim_source(p, f.name)
    except ServiceError as e:
        raise ClaimsError(str(e)) from None
    questions(spec, f.name)
    return spec, out


def questions(spec: dict, fname: str) -> list:
    """The file's open `questions:` from a draft: each {"about", "question"}. ClaimsError in plain words otherwise."""
    given = spec.get("questions")
    if given is None:
        return []
    if not isinstance(given, list) or not all(isinstance(q, dict) and isinstance(q.get("question"), str) and q["question"].strip()
                                              and isinstance(q.get("about") or "", str) for q in given):
        raise ClaimsError(f"{fname}: `questions` is a list, each item with `about` (a claim id, or empty) and `question`. "
                          "Delete a question once you have answered it by editing the claims.")
    return [{"about": (q.get("about") or "").strip(), "question": q["question"].strip()} for q in given]


def sources(spec: dict) -> dict:
    """Each property's checked `source`, or None, by id (read_claims has already checked them)."""
    from .runner import claim_source
    return {p["id"]: claim_source(p, "") for p in spec["properties"] if isinstance(p, dict)}


# ---- the draft: the claims as first shown to the owner, so approval can say what they changed
def draft_path(repo: Path, feature: str) -> Path:
    return Path(repo) / ".groundtruth" / "drafts" / f"{feature}.json"


def snapshot(spec: dict, claims: list) -> dict:
    src = sources(spec)
    return {pid: {**parts, "source": src.get(pid)} for pid, parts in claims}


def keep_draft(repo: Path, feature: str, spec: dict, claims: list) -> bool:
    """Save the claims as first drafted, once: only when the feature has no approval record and no draft yet. Never
    raises. True when a draft was written now."""
    try:
        f = draft_path(repo, feature)
        if f.exists() or record_path(repo, feature).exists():
            return False
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"feature": feature, "drafted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                 "properties": snapshot(spec, claims)}, indent=2) + "\n")
        return True
    except (OSError, TypeError, ValueError):
        return False


def load_draft(repo: Path, feature: str):
    try:
        d = json.loads(draft_path(repo, feature).read_text())
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and isinstance(d.get("properties"), dict) else None


DRAFT_PARTS = ("title", "statement", "your_words", "checked_against", "variations", "source")


def _part_text(part, value) -> str:
    if part == "variations":
        return "\n".join(str(v.get("what") or "").strip() for v in value or [] if isinstance(v, dict))
    if part == "source":
        from .runner import source_words
        return source_words(value)
    return str(value or "").strip()


def draft_changes(draft: dict, now: dict) -> list:
    """What changed from the draft to what is being approved: claims edited part by part (with before and after
    text), added and removed. Variations are compared as their sentences, one per line."""
    out, before = [], draft.get("properties") or {}
    for pid, was in before.items():
        if pid not in now:
            out.append({"id": pid, "change": "removed", "part": None, "before": _part_text("statement", was.get("statement")), "after": ""})
            continue
        for part in DRAFT_PARTS:
            a, b = _part_text(part, was.get(part)), _part_text(part, now[pid].get(part))
            same = ([_norm(x) for x in a.split("\n")] == [_norm(x) for x in b.split("\n")]) if part == "variations" else _norm(a) == _norm(b)
            if not same:
                out.append({"id": pid, "change": "edited", "part": part, "before": a, "after": b})
    for pid, parts in now.items():
        if pid not in before:
            out.append({"id": pid, "change": "added", "part": None, "before": "", "after": _part_text("statement", parts.get("statement"))})
    return out


def load_record(repo: Path, feature: str):
    """The approval record, or None when there is none (or it can't be read)."""
    f = record_path(repo, feature)
    try:
        rec = json.loads(f.read_text())
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) and isinstance(rec.get("properties"), dict) else None


def write_record(repo: Path, feature: str, claims: list, changes=None) -> dict:
    """changes: what changed since the draft (draft_changes), or None when no draft was kept."""
    rec = {"feature": feature, "approved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "properties": {pid: parts for pid, parts in claims}, "draft_changes": changes}
    f = record_path(repo, feature)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(rec, indent=2) + "\n")
    return rec


def _same(part, a, b) -> bool:
    if part == "variations":
        def key(vs):
            return {str(v.get("id")): _norm(v.get("what")) for v in vs or [] if isinstance(v, dict)}
        return key(a) == key(b)
    return _norm(a) == _norm(b)


def compare(claims: list, record) -> dict:
    """claims: [(id, parts)] as read_claims gives them; record: as load_record gives it, or None.
    Returns {id: {"state": "approved" | "changed" | "new", "changed": [part, ...]}}, or {} without a record."""
    if not record:
        return {}
    approved = record.get("properties") or {}
    out = {}
    for pid, parts in claims:
        was = approved.get(pid)
        if not isinstance(was, dict):
            out[pid] = {"state": "new", "changed": []}
            continue
        diff = [k for k in PARTS if not _same(k, parts.get(k), was.get(k))]
        out[pid] = {"state": "changed" if diff else "approved", "changed": diff}
    return out


def words(changed: list) -> str:
    return ", ".join(PART_WORDS.get(k, k) for k in changed)


def flag_lines(states: dict) -> list:
    """One plain line per property that is changed or new."""
    out = []
    for pid, s in states.items():
        if s.get("state") == "changed":
            out.append(f"changed since it was approved: {pid} ({words(s.get('changed') or [])})")
        elif s.get("state") == "new":
            out.append(f"not approved yet: {pid}")
    return out
