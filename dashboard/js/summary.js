// What one feature's summary rests on, without drawing anything: which run each claim's result comes from, the
// rows of variations under a claim, and the plain sentences about the whole feature. The feature page and the list
// of features (runs.js) both take a feature's current state from mergeResults, so the two always agree.
import { variationSentence, plural } from "./words.js";

// How many of a feature's newest runs its current state is read from, on the feature page and on the list.
export const RUNS_READ = 10;

// A run tested a property when its verdict is a result, not "not run" or "planned".
export const tested = (p) => !!p && p.verdict !== "not_run" && p.verdict !== "planned";

// runs: [{id, files_base, document}], newest first. Every property of the newest document, each with the property
// as the newest run that tested it saw it, and that run (null when none of the runs tested it). A run summary from
// the store works too, as {id, document: {ran_at, properties: <its claims>}} (see foldFeatures).
export function mergeResults(runs) {
  const list = (runs || []).filter((r) => r && r.document);
  if (!list.length) return [];
  return (list[0].document.properties || []).map((p0) => {
    for (const run of list) {
      const p = (run.document.properties || []).find((x) => x.id === p0.id);
      if (tested(p)) return { id: p0.id, p, run };
    }
    return { id: p0.id, p: p0, run: null };
  });
}

export const complete = (results) => results.length > 0 && results.every((r) => r.run);

// One row per variation, in the order the cases ran; an approved variation with no case is a row of its own.
export function rowsFor(p) {
  const cases = (p && p.cases) || [];
  const rows = cases.map((c) => ({ id: c.id, c, sentence: variationSentence(p, c) }));
  for (const v of (p && p.variations) || []) {
    if (!v || cases.some((c) => c.id === v.id)) continue;
    rows.push({ id: v.id, c: null, sentence: typeof v.what === "string" ? v.what.trim() : "" });
  }
  return rows;
}

const WORDS = [
  ["holds", "hold", "holds"], ["fails", "fail", "fails"], ["needs_decision", "need your decision", "needs your decision"],
  ["cant_tell", "could not be told", "could not be told"], ["planned", "are planned", "is planned"],
  ["not_run", "were not tested", "was not tested"],
];

// How many claims have each verdict now; a claim none of the runs tested counts as "not_run".
export function countVerdicts(results) {
  const by = {};
  for (const r of results) {
    const v = r.run ? r.p.verdict : "not_run";
    by[v] = (by[v] || 0) + 1;
  }
  return by;
}

// "All 8 claims hold." or "6 hold, 1 fails, 1 could not be told."
export function verdictLine(results) {
  const n = results.length;
  const by = countVerdicts(results);
  if (n && by.holds === n) return n === 1 ? "The one claim holds." : `All ${n} claims hold.`;
  const parts = [];
  for (const [key, many, one] of WORDS) if (by[key]) parts.push(`${by[key]} ${by[key] === 1 ? one : many}`);
  const other = n - WORDS.reduce((a, [k]) => a + (by[k] || 0), 0);
  if (other > 0) parts.push(`${other} with no result`);
  return `${parts.join(", ")}.`;
}

// The runs the results come from, newest first.
export function runsUsed(results) {
  const seen = [];
  for (const r of results) if (r.run && !seen.includes(r.run)) seen.push(r.run);
  return seen.sort((a, b) => String(b.document.ran_at || "").localeCompare(String(a.document.ran_at || "")));
}

// The variations a property tried in its run: a run document lists its cases; a run summary counts them.
const triedIn = (p) => (Array.isArray(p.cases) ? p.cases.length : typeof p.variations === "number" ? p.variations : 0);

export function variationCount(results) {
  return results.reduce((a, r) => a + (r.run ? triedIn(r.p) : 0), 0);
}

// ---------- the list of features (index.html) ----------

// Run summaries from GET /v1/runs, newest first, folded into one entry per project and feature, in the order of
// each feature's newest run: { project, feature, runs (its summaries, newest first), results (as mergeResults gives
// them, from its newest RUNS_READ runs), lastChecked (when the newest result shown was tested, or null) }.
export function foldFeatures(summaries) {
  const byKey = new Map();
  for (const s of summaries || []) {
    if (!s || typeof s !== "object") continue;
    const key = JSON.stringify([s.project || "", s.feature || ""]);
    if (!byKey.has(key)) byKey.set(key, { project: s.project || "", feature: s.feature || "", runs: [] });
    byKey.get(key).runs.push(s);
  }
  return [...byKey.values()].map((f) => {
    const runs = f.runs.slice(0, RUNS_READ).map((s) => ({ id: s.id, summary: s,
      document: { ran_at: s.ran_at, properties: Array.isArray(s.claims) ? s.claims : [] } }));
    const results = mergeResults(runs);
    const used = runsUsed(results);
    return { ...f, results, lastChecked: used.length ? used[0].document.ran_at || null : null };
  });
}

// Newest checked first; a feature none of whose claims has been checked goes after them, newest run first.
export function byLastChecked(features) {
  const t = (iso) => { const n = Date.parse(iso || ""); return isNaN(n) ? null : n; };
  return features.map((f, i) => ({ f, i, at: t(f.lastChecked) })).sort((a, b) => {
    if (a.at !== null && b.at !== null && a.at !== b.at) return b.at - a.at;
    if ((a.at === null) !== (b.at === null)) return a.at === null ? 1 : -1;
    return a.i - b.i;
  }).map((x) => x.f);
}

// [key, one, many]: what a number of claims with this verdict do, on the list page.
const CLAIM_WORDS = [
  ["holds", "holds", "hold"], ["fails", "fails", "fail"], ["needs_decision", "needs your answer", "need your answer"],
  ["cant_tell", "can't be told", "can't be told"],
];
const KNOWN = new Set([...CLAIM_WORDS.map(([k]) => k), "not_run"]);

function countWords(by, keys) {
  const out = [];
  for (const [key, one, many] of CLAIM_WORDS) if (keys.includes(key) && by[key]) out.push(`${by[key]} ${by[key] === 1 ? one : many}`);
  return out;
}

// "5 of 6 claims hold", "1 of 6 claims holds", "1 of 1 claim holds".
export function holdLine(results) {
  const n = results.length;
  const held = countVerdicts(results).holds || 0;
  if (!n) return "No claims";
  if (n === 1) return `${held} of 1 claim holds`;
  return `${held} of ${n} claims ${held === 1 ? "holds" : "hold"}`;
}

// Only what does not hold, in words: ["1 fails", "1 can't be told", "2 never run"].
export function openWords(results) {
  const by = countVerdicts(results);
  const out = countWords(by, ["fails", "needs_decision", "cant_tell"]);
  if (by.not_run) out.push(`${by.not_run} never run`);
  const other = Object.entries(by).filter(([k]) => !KNOWN.has(k)).reduce((a, [, v]) => a + v, 0);
  if (other) out.push(`${other} with no result`);
  return out;
}

// The feature's mark at the left: the worst of its current results.
export function featureMark(results) {
  const by = countVerdicts(results);
  if (by.fails) return { glyph: "✗", tone: "bad" };
  if (by.needs_decision) return { glyph: "?", tone: "warn" };
  if (by.cant_tell) return { glyph: "!", tone: "mid" };
  if (results.length && by.holds === results.length) return { glyph: "✓", tone: "ok" };
  return { glyph: "–", tone: "mid" };
}

// What one run covered, in positive terms: "checked 3 of 6 claims: 2 hold, 1 can't be told".
export function runCovered(summary) {
  const claims = (summary && Array.isArray(summary.claims) ? summary.claims : []).filter((c) => c && typeof c === "object");
  const checked = claims.filter(tested);
  const n = checked.length;
  const total = claims.length;
  if (!n) return "checked no claims";
  const by = {};
  for (const c of checked) by[c.verdict] = (by[c.verdict] || 0) + 1;
  const parts = countWords(by, CLAIM_WORDS.map(([k]) => k));
  const other = checked.filter((c) => !KNOWN.has(c.verdict)).length;
  if (other) parts.push(`${other} with no result`);
  if (total === 1) {
    const w = CLAIM_WORDS.find(([k]) => k === checked[0].verdict);
    return `checked its one claim: ${w ? `it ${w[1]}` : "it has no result"}`;
  }
  const what = n === total ? `checked all ${total} claims` : `checked ${n} of ${total} claims`;
  return `${what}: ${parts.join(", ")}`;
}

// One run as a line under its feature, a small copy of the feature's own row: its mark (the worst result among the
// claims it checked), how many of those hold ("0 of 2 claims hold"), and, when it left claims out, how many it checked.
export function runTally(summary) {
  const claims = (summary && Array.isArray(summary.claims) ? summary.claims : []).filter((c) => c && typeof c === "object");
  const checked = claims.filter(tested);
  const n = checked.length;
  const by = {};
  for (const c of checked) by[c.verdict] = (by[c.verdict] || 0) + 1;
  const held = by.holds || 0;
  const mark = by.fails ? { glyph: "✗", tone: "bad" } : by.needs_decision ? { glyph: "?", tone: "warn" }
    : by.cant_tell ? { glyph: "!", tone: "mid" } : n && held === n ? { glyph: "✓", tone: "ok" } : { glyph: "–", tone: "mid" };
  const words = !n ? "claims hold" : n === 1 ? "claim holds" : `claims ${held === 1 ? "holds" : "hold"}`;
  return { mark, count: n ? `${held} of ${n}` : "", words: n ? words : "no claims checked",
    left: n && n < claims.length ? `only ${n} of ${claims.length} checked` : "" };
}

export function fromRunsSentence(results) {
  const n = runsUsed(results).length;
  return n > 1 ? `Results are from ${plural(n, "run", "runs")}; each claim shows when it was last tested.` : "";
}

// The outcome of one variation in a word or two, in the owner's terms (a property is a "claim" to the owner).
const OUTCOME_WORDS = {
  holds: "Held", breaks: "Broke", declined: "Needs your decision", outside: "Outside the claim's wording",
  cant_run: "Could not run", nothing_there: "Nothing there to test", planned: "Planned, not run yet",
};
export const outcomeWord = (c) => (c ? OUTCOME_WORDS[c.outcome] || "No result" : "Not run");

const said = (x) => (typeof x === "string" ? x.trim() : "");

// Why a variation did not hold, in one or more plain sentences; "" when it held.
export function reasonWords(c) {
  if (!c) return "This variation was not run.";
  const seen = said(c.observed);
  switch (c.outcome) {
    case "holds": return "";
    case "breaks": return seen || "No reason was given.";
    case "declined": return said(c.question) || seen || "The app refused it. Is that what you meant it to do?";
    case "outside": return `It is outside the claim's wording${seen ? `: ${seen}` : "."}`;
    case "cant_run": return seen || "It could not run, and gave no reason.";
    case "nothing_there": return seen || "There was nothing there to test.";
    case "planned": return "It has not run yet.";
    default: return seen;
  }
}
