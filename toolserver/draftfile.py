"""Writing drafted claims into a claims file as text, so it reads like one written by hand.

    write(repo, feature, draft, origin, when=None) -> (path, added ids, ids left alone)

A new file is built from a template. An existing file is never rewritten: new claims are inserted after its last
claim, new questions and not-checked sentences after its own (or in a new list at the end), and a claim whose id the
file already has is left exactly as it is. Every string is written as a plain YAML scalar when that is safe, and as
a double-quoted one otherwise, so the file always reads back to the same values.
"""
import json
import re
import time
from pathlib import Path

import yaml

_PLAIN = re.compile(r"[A-Za-z0-9(][^\n]*")
_RISKY = re.compile(r":\s|\s#|:$|^[-?:,\[\]{}#&*!|>'\"%@`]|^(true|false|yes|no|on|off|null|~)$|^[\d.+-]+$", re.IGNORECASE)


def scalar(text) -> str:
    """One string as YAML: plain when it reads back unchanged, double-quoted otherwise."""
    s = str(text if text is not None else "").strip()
    if s and _PLAIN.fullmatch(s) and not _RISKY.search(s) and s == s.strip():
        try:
            if yaml.safe_load(s) == s:
                return s
        except yaml.YAMLError:
            pass
    return json.dumps(s, ensure_ascii=False)


def claim_block(c: dict, indent: str = "  ") -> str:
    """One drafted claim as a list item, in the order the hand-written files use."""
    i, j = indent, indent + "  "
    lines = [f"{i}- id: {scalar(c['id'])}"]
    for key in ("title", "your_words"):
        if c.get(key):
            lines.append(f"{j}{key}: {scalar(c[key])}")
    src = c.get("source") or {}
    if src:
        lines.append(f"{j}source:")
        lines.append(f"{j}  kind: {scalar(src.get('kind'))}")
        for key in ("ref", "quote"):
            if src.get(key):
                lines.append(f"{j}  {key}: {scalar(src[key])}")
    lines.append(f"{j}statement: {scalar(c['statement'])}")
    if c.get("checked_against"):
        lines.append(f"{j}checked_against: {scalar(c['checked_against'])}")
    if c.get("variations"):
        lines.append(f"{j}variations:")
        for v in c["variations"]:
            if isinstance(v, dict):
                lines += [f"{j}  - id: {scalar(v['id'])}", f"{j}    what: {scalar(v['what'])}"]
            else:
                lines.append(f"{j}  - {scalar(v)}")
    return "\n".join(lines) + "\n"


def question_block(q: dict, indent: str = "  ") -> str:
    return f"{indent}- about: {scalar(q.get('about') or '')}\n{indent}  question: {scalar(q['question'])}\n"


def heading(origin: str, when: str) -> str:
    return f"# Drafted by Groundtruth on {when} from {origin}. Not approved yet: read it, edit it, then run approve.\n"


def render(feature: str, draft: dict, origin: str, when: str) -> str:
    out = [heading(origin, when), f"feature: {scalar(feature)}\n"]
    if draft.get("goal"):
        out.append(f"goal: {scalar(draft['goal'])}\n")
    out.append("properties:\n" if draft.get("claims") else "properties: []\n")
    out += [claim_block(c) for c in draft.get("claims") or []]
    if draft.get("questions"):
        out.append("questions:\n")
        out += [question_block(q) for q in draft["questions"]]
    if draft.get("not_checked"):
        out.append("not_checked:\n")
        out += [f"  - {scalar(x)}\n" for x in draft["not_checked"]]
    return "".join(out)


def _top_keys(lines):
    """Line index of each top-level key."""
    return {m.group(1): n for n, line in enumerate(lines) if (m := re.match(r"([A-Za-z_][A-Za-z0-9_]*):", line))}


def _block_end(lines, start):
    """Index just after the block that starts at the top-level key on line `start` (trailing blank lines excluded)."""
    end = len(lines)
    for n in range(start + 1, len(lines)):
        if re.match(r"[A-Za-z_#]", lines[n]) and not lines[n].startswith("#"):
            end = n
            break
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return end


def _item_indent(lines, start, end, default="  "):
    for n in range(start + 1, end):
        m = re.match(r"(\s*)- ", lines[n])
        if m:
            return m.group(1)
    return default


def _insert(lines, key, blocks, default_indent="  "):
    """Add list items to a top-level list, after its last item, or as a new list at the end."""
    if not blocks:
        return lines
    keys = _top_keys(lines)
    if key in keys:
        start = keys[key]
        if re.match(rf"{key}:\s*\[\s*\]\s*$", lines[start]):              # an empty flow list: make it a block list
            lines[start] = f"{key}:\n"
        end = _block_end(lines, start)
        indent = _item_indent(lines, start, end, default_indent)
        text = "".join(b(indent) for b in blocks)
        return lines[:end] + [text] + lines[end:]
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    return lines + [f"{key}:\n", "".join(b(default_indent) for b in blocks)]


def merge(text: str, draft: dict, origin: str, when: str):
    """New claims, questions and not-checked sentences added to an existing claims file. -> (text, added, left alone)"""
    spec = yaml.safe_load(text) or {}
    have = {p.get("id") for p in spec.get("properties") or [] if isinstance(p, dict)}
    new = [c for c in draft.get("claims") or [] if c["id"] not in have]
    kept = [c["id"] for c in draft.get("claims") or [] if c["id"] in have]
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    note = heading(origin, when).rstrip("\n")
    claims = [lambda ind, note=note: f"{ind}{note}\n"] if new else []
    claims += [lambda ind, c=c: claim_block(c, ind) for c in new]
    lines = _insert(lines, "properties", claims)
    asked = {(q.get("about"), q.get("question")) for q in spec.get("questions") or [] if isinstance(q, dict)}
    lines = _insert(lines, "questions", [lambda ind, q=q: question_block(q, ind) for q in draft.get("questions") or []
                                         if (q.get("about"), q.get("question")) not in asked])
    unchecked = [x for x in spec.get("not_checked") or [] if isinstance(x, str)]
    room = max(0, 12 - len(unchecked))
    extra = [x for x in draft.get("not_checked") or [] if x not in unchecked][:room]
    lines = _insert(lines, "not_checked", [lambda ind, x=x: f"{ind}- {scalar(x)}\n" for x in extra])
    return "".join(lines), [c["id"] for c in new], kept


def write(repo, feature: str, draft: dict, origin: str, when=None):
    when = when or time.strftime("%Y-%m-%d %H:%M")
    f = Path(repo) / ".groundtruth" / "features" / f"{feature}.yml"
    f.parent.mkdir(parents=True, exist_ok=True)
    if f.exists():
        text, added, kept = merge(f.read_text(), draft, origin, when)
    else:
        text, added, kept = render(feature, draft, origin, when), [c["id"] for c in draft.get("claims") or []], []
    yaml.safe_load(text)                    # never leave a file that does not read back
    f.write_text(text)
    return f, added, kept
