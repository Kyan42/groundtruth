// Every word a person reads about verdicts and outcomes. Internal values never reach the screen.

export const VERDICT = {
  holds: { long: "Holds", short: "Holds", tone: "ok", glyph: "✓" },
  fails: { long: "Fails", short: "Fails", tone: "bad", glyph: "✗" },
  needs_decision: { long: "Needs your decision", short: "Your call", tone: "warn", glyph: "?" },
  cant_tell: { long: "Can't tell", short: "Can't tell", tone: "mid", glyph: "!" },
  planned: { long: "Planned, not run yet", short: "Planned", tone: "mid", glyph: "·" },
  not_run: { long: "Not run in this run", short: "Not run", tone: "mid", glyph: "–" },
};

export function verdict(v) {
  return VERDICT[v] || { long: "Unknown", short: "Unknown", tone: "mid", glyph: "·" };
}

// mark: class of the small square; tile: the short word on a tile when there is no length to show.
export const OUTCOME = {
  holds: { long: "Held", mark: "", glyph: "✓", tone: "ok", tile: "held", dot: "" },
  breaks: { long: "Broke", mark: "x", glyph: "✗", tone: "bad", tile: "broke", dot: "x" },
  declined: { long: "Needs your decision", mark: "q", glyph: "?", tone: "warn", tile: "your call", dot: "q" },
  outside: { long: "Outside the property's wording", mark: "n", glyph: "–", tone: "mid", tile: "outside the wording", dot: "n" },
  nothing_there: { long: "Nothing there to test", mark: "n", glyph: "–", tone: "mid", tile: "nothing there", dot: "n" },
  cant_run: { long: "Could not run", mark: "n", glyph: "!", tone: "mid", tile: "could not run", dot: "n" },
  planned: { long: "Planned, not run yet", mark: "n", glyph: "·", tone: "mid", tile: "planned", dot: "n" },
};

export function outcome(o) {
  return OUTCOME[o] || { long: "Unknown", mark: "n", glyph: "·", tone: "mid", tile: "unknown", dot: "n" };
}

// "1 held", "2 broke" ... used to describe counts in sentences.
const COUNT_WORDS = {
  holds: "held", breaks: "broke", declined: "need your decision", outside: "outside the wording",
  nothing_there: "found nothing there to test", cant_run: "could not run", planned: "planned, not run yet",
};
const ONE_WORDS = { declined: "needs your decision" };

export function countsSentence(counts, total) {
  const held = counts.holds || 0;
  const parts = [`${held} of ${total} ${total === 1 ? "case" : "cases"} held`];
  for (const key of ["breaks", "declined", "cant_run", "outside", "nothing_there", "planned"]) {
    const n = counts[key] || 0;
    if (n) parts.push(`${n} ${n === 1 && ONE_WORDS[key] ? ONE_WORDS[key] : COUNT_WORDS[key]}`);
  }
  return parts.join(", ");
}

export function marksLabel(cases) {
  const n = cases.length;
  if (!n) return "No cases";
  const by = {};
  for (const c of cases) by[c.outcome] = (by[c.outcome] || 0) + 1;
  const parts = Object.entries(by).map(([k, v]) => `${v} ${v === 1 && ONE_WORDS[k] ? ONE_WORDS[k] : COUNT_WORDS[k] || "unknown"}`);
  return `${n} ${n === 1 ? "case" : "cases"}: ${parts.join(", ")}`;
}

export function clock(seconds) {
  if (seconds === null || seconds === undefined || !isFinite(seconds) || seconds < 0) return "–:––";
  const s = Math.floor(seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function when(iso) {
  if (!iso) return "unknown time";
  const d = new Date(iso);
  if (isNaN(d)) return String(iso);
  return d.toLocaleString("en-US", { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" });
}

export function upper(id) {
  return String(id || "").toUpperCase();
}

export function plural(n, one, many) {
  return `${n} ${n === 1 ? one : many}`;
}

export function listWords(items) {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

// ---------- names for claims and variations ----------

// The owner's few-word name for a property, or "" when it has none (older runs have no titles).
export const titleOf = (p) => (p && typeof p.title === "string" ? p.title.trim() : "");

// What a person calls a property: its title, else its sentence.
export function claimName(p) {
  return titleOf(p) || (p && typeof p.statement === "string" ? p.statement.trim() : "") || "";
}

// A property planned from a claim: the owner approved its variations and one case was planned for each, without
// reading any code. Runs stored before from_claim existed are told apart by having no code search and a
// "checked against" sentence.
export function fromClaim(p) {
  if (!p) return false;
  if (p.from_claim === true) return true;
  return p.from_claim === undefined && p.searched === false && typeof p.checked_against === "string" && p.checked_against !== "";
}

const text = (x) => (typeof x === "string" ? x.trim() : "");

// The sentence for one variation: the owner's approved sentence, else the matching approved variation of the
// property, else what the case stands for, else what it planned to do.
export function variationSentence(p, c) {
  if (!c) return "";
  const own = text(c.variation);
  if (own) return own;
  const v = ((p && p.variations) || []).find((x) => x && x.id === c.id);
  return text(v && v.what) || text(c.stands_for) || text(c.what);
}

// ---------- film size ----------

// The size of the films of one run: its browser window, or 900 by 700 for runs made before that was recorded.
export function viewportOf(doc) {
  const v = doc && doc.ran_against && doc.ran_against.viewport;
  const ok = (n) => typeof n === "number" && isFinite(n) && n >= 100 && n <= 10000;
  return v && ok(v.width) && ok(v.height) ? { width: v.width, height: v.height } : { width: 900, height: 700 };
}

// Gives an element (and everything in it) the shape of those films.
export function filmShape(el, vp) {
  if (!el || !vp) return el;
  el.style.setProperty("--fw", String(vp.width));
  el.style.setProperty("--fh", String(vp.height));
  return el;
}

// ---------- comparisons ----------

// The comparisons a case made about its claim; the check that it was in the right situation is not one of them.
export const comparisons = (c) => ((c && c.steps) || []).filter((s) => s && s.kind === "compare" && !s.situation);

// "3 of 3 comparisons matched", "1 of 3 did not match: <what>", or "" when there are none.
export function comparisonCount(c) {
  const all = comparisons(c);
  if (!all.length) return "";
  const off = all.filter((s) => s.matches === false);
  if (!off.length) return `${all.length} of ${all.length} ${all.length === 1 ? "comparison" : "comparisons"} matched`;
  return `${off.length} of ${all.length} did not match: ${String(off[0].what || off[0].words || "").trim()}`;
}
