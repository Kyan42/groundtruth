// One feature (feature.html?project=<name>&feature=<name>). When a run filmed the feature in use, the page is that film,
// large, with one line saying how the feature came out and one link to every claim and variation. Otherwise, and with
// &view=claims, it is the summary: what was tested and whether it worked, in the owner's words, read top to bottom.
// Each claim's result is from the newest run that tested it, so a claim fixed and rerun on its own still shows beside
// the others.
import { h, clear, brandLink, signOut } from "./dom.js";
import { getJSON, getRun } from "./api.js";
import { verdict, outcome, when, plural, titleOf, claimName, viewportOf, filmShape, comparisons } from "./words.js";
import { caseKind, caseName, startedFrom } from "./proof.js";
import { createStoryboard } from "./storyboard.js";
import { tourSteps } from "./story.js";
import { mergeResults, complete, rowsFor, verdictLine, runsUsed, variationCount, fromRunsSentence, outcomeWord, reasonWords,
  RUNS_READ } from "./summary.js";
import { openReel } from "./reel.js";
import { feedbackStore, feedbackControls, saidWords } from "./feedback.js";
import { sourceLine, askedFor, draftChanges } from "./learn.js";

const MAX_DOCS = RUNS_READ;     // the list of features reads a feature's state from as many runs
const root = document.getElementById("app");
const params = new URLSearchParams(location.search);
const project = params.get("project") || "";
const feature = params.get("feature") || "";
let openRow = null;
let walkFilm = null;    // the walkthrough's player, paused while the reel plays     // the one variation open on the page: { li, btn, film }
const feedback = feedbackStore();   // what the person thinks of each result, kept with the run it came from

let wide = false;      // the film-first page uses more of the window
function page(...main) {
  clear(root).append(h("div", { class: wide ? "fpage wide" : "fpage" },
    h("header", { class: "ftop" }, brandLink(), signOut()),
    h("main", { class: "fmain", id: "main" }, ...main)));
}

function stateLine(text) {
  page(h("p", { class: "fname" }, [feature, project].filter(Boolean).join(" · ")), h("p", { class: "fstate", role: "status" }, text));
}

// ---------- loading ----------

async function load() {
  if (!project || !feature) {
    stateLine("No feature chosen. Open a run and follow its summary link, or choose a run from the list of test runs.");
    return null;
  }
  const q = `project=${encodeURIComponent(project)}&feature=${encodeURIComponent(feature)}&limit=20`;
  const listed = await getJSON(`/v1/runs?${q}`);
  const summaries = (listed && Array.isArray(listed.runs)) ? listed.runs : [];
  if (!summaries.length) return { runs: [], results: [] };
  const runs = [];
  let results = [];
  for (const s of summaries.slice(0, MAX_DOCS)) {
    const data = await getRun(s.id);
    if (!data || !data.document) continue;
    runs.push({ id: data.id || s.id, files_base: data.files_base || `/v1/runs/${encodeURIComponent(s.id)}/files/`, document: data.document });
    results = mergeResults(runs);
    // Stop once every claim has a result and a walkthrough has been found (or ten runs have been read).
    const tour = runs.some((x) => x.document.walkthrough && typeof x.document.walkthrough === "object" && x.document.walkthrough.id);
    if (complete(results) && tour) break;
  }
  return { runs, results };
}

// ---------- the approval banner, as on the run page ----------

const PART_WORDS = { statement: "the sentence", title: "the title", your_words: "your own words",
  checked_against: "what it is checked against", variations: "the variations" };
const changedWords = (p) => (Array.isArray(p.approval_changed) ? p.approval_changed : []).map((k) => PART_WORDS[k] || String(k).replace(/_/g, " ")).join(", ");

function banners(results) {
  const ps = results.filter((r) => r.run).map((r) => r.p);
  const named = (p) => titleOf(p) || p.id;
  const changed = ps.filter((p) => p.approval === "changed");
  const unapproved = ps.filter((p) => p.approval === "new");
  const said = [];
  if (changed.length) said.push(`Changed after it was approved: ${changed.map((p) => `${named(p)}${changedWords(p) ? ` (${changedWords(p)})` : ""}`).join("; ")}.`);
  if (unapproved.length) said.push(`Not approved yet: ${unapproved.map(named).join("; ")}.`);
  return said.length ? h("p", { class: "banner approval", role: "status" }, said.join(" ")) : null;
}

// ---------- one variation ----------

function closeOpen() {
  if (!openRow) return;
  if (openRow.film) openRow.film.stop();
  clear(openRow.panel);
  openRow.panel.hidden = true;
  openRow.btn.setAttribute("aria-expanded", "false");
  openRow.li.classList.remove("open");
  openRow = null;
}

function detailsLink(r, c) {
  return h("a", { class: "details", href: `run.html?run=${encodeURIComponent(r.run.id)}#${encodeURIComponent(r.p.id)}/${encodeURIComponent(c.id)}` },
    "Open the full details");
}

function openVariation(row, r, li, btn, panel) {
  const c = row.c;
  const parts = [];
  const setting = typeof c.setting === "string" ? c.setting.trim() : "";
  // The film with its living storyboard; a case with no film shows its steps as a list, and what it saw.
  const film = createStoryboard(c, { base: r.run.files_base, viewport: viewportOf(r.run.document), compact: true,
    label: `The film of ${caseName(r.p, c)} and its steps` });
  if (setting) parts.push(h("p", { class: "started" }, h("span", { class: "k" }, "Starts from: "), setting));
  parts.push(film.el);
  if (!film.video && typeof c.observed === "string" && c.observed.trim()) parts.push(h("p", { class: "seen" }, c.observed));
  parts.push(detailsLink(r, c));
  parts.push(feedbackControls(feedback, { runId: r.run.id, prop: r.p.id, caseId: c.id, name: `the variation \u201c${row.sentence || c.id}\u201d` }));
  panel.append(...parts.filter(Boolean));
  panel.hidden = false;
  btn.setAttribute("aria-expanded", "true");
  li.classList.add("open");
  openRow = { li, btn, panel, film };
}

// A long reason (over about 200 characters) shows its first sentence, at most about 200 characters; the rest
// waits behind "More".
export function shortReason(text, max = 200) {
  const full = String(text || "").trim();
  if (full.length <= max) return { short: full, rest: "" };      // a short reason is shown whole
  const m = full.match(/^[\s\S]*?[.!?](?=\s|$)/);
  let short = m ? m[0] : full;
  let cut = false;
  if (short.length > max) {
    const at = short.lastIndexOf(" ", max - 1);
    short = short.slice(0, at > max / 2 ? at : max - 1);
    cut = true;
  }
  const rest = full.slice(short.length).trim();
  return { short: cut ? `${short.trimEnd()}…` : short, rest };
}

function reasonLine(text, tone) {
  const { short, rest } = shortReason(text);
  return h("div", { class: `why ${tone}` }, h("span", null, short),
    rest ? h("details", { class: "more" }, h("summary", null, "More"), h("p", null, rest)) : null);
}

// One quiet line about what the variation compared: how many matched, or the first that did not.
export function comparisonLine(c) {
  const all = comparisons(c);
  if (!all.length) return "";
  const off = all.find((s) => s.matches === false);
  if (!off) return `${all.length} ${all.length === 1 ? "comparison" : "comparisons"} matched`;
  const said = (x) => String(x || "").trim().replace(/[.\s]+$/, "");
  return `Did not match: ${said(off.what || off.words)}. Expected ${said(off.expected) || "nothing said"}; found ${said(off.found) || "nothing said"}.`;
}

function variationRow(row, r, idx) {
  const c = row.c;
  const o = c ? outcome(c.outcome) : { mark: "n", glyph: "–", tone: "mid" };
  const why = reasonWords(c);
  const showWhy = c ? c.outcome !== "holds" : true;
  const cmp = comparisonLine(c);
  const head = [
    h("span", { class: `m ${o.mark}`, "aria-hidden": "true" }, o.glyph),
    h("span", { class: "vtext" },
      h("span", { class: "sr" }, `${outcomeWord(c)}: `),
      h("span", { class: "vs" }, row.sentence || "A variation with no sentence")),
  ];
  const notes = (showWhy && why) || cmp
    ? h("div", { class: "vnotes" },
      showWhy && why ? reasonLine(why, o.tone) : null,
      cmp ? h("p", { class: `cmpline${cmp.startsWith("Did not") ? " bad" : ""}` }, cmp) : null)
    : null;
  const panelId = `open-${r.p.id}-${idx}`;
  const li = h("li", { class: `vrow${c ? "" : " notrun"}`, "data-outcome": c ? c.outcome : "not_run" });
  if (!c) {
    li.append(...[h("div", { class: "vhead" }, ...head), notes].filter(Boolean));
    return li;
  }
  const panel = h("div", { class: "vopen", id: panelId, hidden: true });
  filmShape(panel, viewportOf(r.run.document));
  // What the person said about this variation, in one quiet line (the controls are in the opened row).
  const chosen = h("p", { class: "vfb" });
  const showChosen = () => { chosen.textContent = saidWords(feedback.current(r.run.id, r.p.id, c.id)); };
  feedback.listen((id) => { if (id === r.run.id) showChosen(); });
  showChosen();
  const btn = h("button", { type: "button", class: "vhead", "aria-expanded": "false", "aria-controls": panelId }, ...head);
  btn.addEventListener("click", () => {
    const mine = openRow && openRow.li === li;
    closeOpen();
    if (!mine) openVariation(row, r, li, btn, panel);
  });
  li.append(...[btn, notes, chosen, panel].filter(Boolean));
  return li;
}

// ---------- one claim ----------

const HOUR = 3600 * 1000;
const stamp = (run) => {
  const t = Date.parse(run && run.document && run.document.ran_at);
  return isNaN(t) ? null : t;
};

function claimBlock(r, newestRun, runsCount, newestDoc) {
  const p = r.p;
  // Where the claim came from: the newest run's own word on it (older runs may not have kept it).
  const fresh = ((newestDoc && newestDoc.properties) || []).find((x) => x && x.id === p.id);
  const words = typeof p.your_words === "string" ? p.your_words.trim() : "";
  const title = titleOf(p);
  const v = r.run ? verdict(p.verdict) : { long: "Not tested", tone: "mid", glyph: "–" };
  const parts = [];
  if (words) parts.push(h("blockquote", { class: "said" }, `“${words}”`));
  const source = sourceLine(fresh && fresh.source ? fresh : p);
  if (words && source) parts.push(source);
  parts.push(h("h2", { class: "ctitle" },
    h("span", { class: "cname" }, title || p.statement || "A claim with no sentence"),
    h("span", { class: `cverdict ${v.tone}` }, h("span", { class: "glyph", "aria-hidden": "true" }, v.glyph), v.long)));
  if (!words && source) parts.push(source);
  if (title && p.statement) parts.push(h("p", { class: "cstatement" }, p.statement));
  if (!r.run) {
    parts.push(h("p", { class: "cwhen" }, `Not tested in ${runsCount === 1 ? "the one run" : `any of the last ${plural(runsCount, "run", "runs")}`} of this feature.`));
    return h("article", { class: "claim", id: `claim-${p.id}` }, parts);
  }
  // Said only when the result is more than an hour older than the newest result on the page.
  const mine = stamp(r.run);
  const newest = stamp(newestRun);
  if (r.run !== newestRun && (mine === null || newest === null || newest - mine > HOUR)) {
    parts.push(h("p", { class: "cwhen" }, `Tested ${when(r.run.document.ran_at)}.`));
  }
  const rows = rowsFor(p);
  if (rows.length) {
    parts.push(h("h3", { class: "tried-k" }, "Tried:"),
      h("ul", { class: "tried" }, rows.map((row, i) => variationRow(row, r, i))));
  } else {
    parts.push(h("p", { class: "cwhen" }, p.no_plan_because ? `No variations were tried: ${p.no_plan_because}` : "No variations were tried."));
  }
  const n = rows.filter((x) => x.c).length;
  if (n) {
    const watch = h("button", { type: "button", class: "btn small watch" }, `Watch these ${n}`);
    watch.addEventListener("click", () => { closeOpen(); if (walkFilm) walkFilm.pause(); openReel([{ p, base: r.run.files_base, viewport: viewportOf(r.run.document) }], { title: claimName(p) }); });
    parts.push(h("div", { class: "btns" }, watch));
  }
  parts.push(feedbackControls(feedback, { runId: r.run.id, prop: p.id, name: `the claim \u201c${claimName(p)}\u201d` }));
  return h("article", { class: "claim", id: `claim-${p.id}` }, parts);
}

// ---------- the walkthrough ----------

function claimTitleFor(results) {
  return (s) => {
    const r = results.find((x) => x.id === s.claim);
    return r ? claimName(r.p) : "";
  };
}

function walkthroughBlock(tour, run, results, bare = false) {
  const titleOfStop = claimTitleFor(results);
  const off = comparisons(tour).filter((s) => s.matches === false);
  const parts = bare ? [] : [h("h2", { class: "wt-h", id: "wt-h" }, "Watch it being used")];
  const said = (x) => String(x || "").trim().replace(/[.\s]+$/, "");
  for (const s of off) {
    const about = titleOfStop(s);
    parts.push(h("p", { class: "wt-warn" }, `The walkthrough saw something that did not match: ${said(s.what || s.words)}${about ? ` (${about})` : ""}.`));
  }
  if (tour.outcome && tour.outcome !== "holds") {
    const seen = said(tour.observed);
    if (tour.outcome === "cant_run") parts.push(h("p", { class: "wt-warn" }, `The walkthrough could not be run${seen ? `: ${seen}` : ""}.`));
    else if (!off.length) parts.push(h("p", { class: "wt-warn" }, `The walkthrough did not go as planned${seen ? `: ${seen}` : ""}.`));
  }
  if (!bare) parts.push(h("p", { class: "wt-note" }, "This is a demonstration of the feature in use. Each claim's result comes from its own variations below."));
  const props = (run.document.properties || []);
  const propOf = (id) => props.find((p) => p.id === id) || ((results.find((x) => x.id === id) || {}).p) || null;
  const film = createStoryboard(tour, { base: run.files_base, viewport: viewportOf(run.document), steps: tourSteps(tour, propOf),
    scope: "the walkthrough", label: "The film of the feature being used, start to finish, and its stops", captions: bare });
  walkFilm = film;
  const setting = typeof tour.setting === "string" ? tour.setting.trim() : "";
  if (setting && !bare) parts.push(h("p", { class: "started" }, h("span", { class: "k" }, "Starts from: "), setting));
  // On the film-first page the film comes first, so it and its controls fit the window; what went wrong is under it.
  if (bare) return h("section", { class: "walkthrough bare", "aria-label": "The film of the feature being used", id: "walkthrough" }, film.el, ...parts);
  parts.push(film.el);
  return h("section", { class: "walkthrough", "aria-labelledby": "wt-h", id: "walkthrough" }, ...parts);
}

// ---------- the page ----------

// The film-first page: one line saying which feature and how it came out, the film of the feature in use as large as
// the window allows, what was not checked, and one link to every claim and variation. That link is the run's own page
// when one run tested everything shown, and the summary of claims (&view=claims) when the results span several runs.
function drawFilm(runs, results, used) {
  wide = true;
  document.title = `${feature} · ${project} · Groundtruth`;
  const newestDoc = runs[0].document;
  const line = used.length ? verdictLine(results).replace(/\.$/, "") : "Not tested yet";
  const allHold = /^All\b/.test(line) || /^1 claim holds$/.test(line);
  const claimsURL = `feature.html?project=${encodeURIComponent(project)}&feature=${encodeURIComponent(feature)}&view=claims`;
  const detail = h("a", { class: "btn fdetail", href: used.length === 1 ? `run.html?run=${encodeURIComponent(used[0].id)}` : claimsURL },
    "Every claim and variation");
  const bar = h("div", { class: "fbar" },
    h("p", { class: "fname" }, `${feature} · ${project}`),
    h("span", { class: allHold ? "fchip ok" : "fchip bad" }, line),
    detail);
  const tourRun = runs.find((x) => x.document.walkthrough && typeof x.document.walkthrough === "object" && x.document.walkthrough.id);
  const film = walkthroughBlock(tourRun.document.walkthrough, tourRun, results, true);
  const nc = (Array.isArray(newestDoc.not_checked) ? newestDoc.not_checked : []).filter((x) => typeof x === "string" && x.trim());
  const unchecked = nc.length ? h("section", { class: "notchecked slim", "aria-labelledby": "nc-h" },
    h("h2", { id: "nc-h" }, "Not checked"),
    h("ul", null, nc.map((x) => h("li", null, x)))) : null;
  page(bar, film, unchecked);
}

function draw(runs, results) {
  const newestDoc = runs[0].document;
  const used = runsUsed(results);
  const tried = variationCount(results);
  document.title = `${feature} · ${project} · Groundtruth`;
  const head = [
    h("p", { class: "fname" }, `${feature} · ${project}`),
    h("h1", { class: "goal" }, newestDoc.goal || `The ${feature} feature`),
  ];
  // The film of the feature in use is the page when there is one; &view=claims asks for the summary of claims.
  const filmed = runs.find((x) => x.document.walkthrough && typeof x.document.walkthrough === "object" && x.document.walkthrough.id);
  if (params.get("view") !== "claims" && used.length && filmed && caseKind(filmed.document.walkthrough) === "film") {
    return drawFilm(runs, results, used);
  }
  const banner = banners(results);
  if (!used.length) {
    page(...head, banner, h("p", { class: "fstate", role: "status" },
      `None of this feature's claims has been tested yet: the last ${plural(runs.length, "run", "runs")} left every claim out.`));
    return;
  }
  const newestUsed = used[0];
  const films = results.filter((r) => r.run).reduce((a, r) => a + (r.p.cases || []).filter((c) => caseKind(c) === "film").length, 0);
  const meta = [`${plural(tried, "variation", "variations")} tried`, `newest result from ${when(newestUsed.document.ran_at)}`].join(" · ");
  // What was asked for (the newest run that read its pull request) and what changed before approval (the newest run
  // that kept a draft): each closed until opened, and absent when there is nothing to say.
  const asked = runs.find((x) => x.document.context);
  const drafted = runs.find((x) => x.document.draft_changes != null);
  const learned = [asked ? askedFor(asked.document.context) : null,
    drafted ? draftChanges(drafted.document.draft_changes, drafted.document.properties) : null].filter(Boolean);
  const top = [
    ...head,
    banner,
    h("p", { class: "fverdict" }, verdictLine(results)),
    h("p", { class: "fmeta" }, `${meta}.`, fromRunsSentence(results) ? h("span", { class: "fruns" }, ` ${fromRunsSentence(results)}`) : null),
    learned.length ? h("div", { class: "learned" }, learned) : null,
  ];
  // The walkthrough: a film of the feature being used start to finish, from the newest fetched run that has one.
  const tourRun = runs.find((x) => x.document.walkthrough && typeof x.document.walkthrough === "object" && x.document.walkthrough.id);
  const tour = tourRun ? tourRun.document.walkthrough : null;
  if (tour) top.push(walkthroughBlock(tour, tourRun, results));
  const reelClaims = results.filter((r) => r.run).map((r) => ({ p: r.p, base: r.run.files_base, viewport: viewportOf(r.run.document) }));
  const tourFilm = tour && caseKind(tour) === "film";
  if (tourFilm) {
    const props = tourRun.document.properties || [];
    const propOf = (id) => props.find((p) => p.id === id) || ((results.find((x) => x.id === id) || {}).p) || null;
    reelClaims.unshift({ walkthrough: tour, base: tourRun.files_base, viewport: viewportOf(tourRun.document), steps: tourSteps(tour, propOf) });
  }
  const nFilms = films + (tourFilm ? 1 : 0);
  if (tried) {
    const all = h("button", { type: "button", class: "btn go watch-all" }, nFilms ? `Watch all ${nFilms} films` : `Watch all ${tried} variations`);
    all.addEventListener("click", () => { closeOpen(); if (walkFilm) walkFilm.pause(); openReel(reelClaims, { title: feature }); });
    top.push(h("div", { class: "btns" }, all));
  }
  const blocks = results.map((r) => claimBlock(r, newestUsed, runs.length, newestDoc));
  const rest = [];
  const nc = (Array.isArray(newestDoc.not_checked) ? newestDoc.not_checked : []).filter((x) => typeof x === "string" && x.trim());
  if (nc.length) {
    rest.push(h("section", { class: "notchecked", "aria-labelledby": "nc-h" },
      h("h2", { id: "nc-h" }, "Not checked"),
      h("p", null, "No claim covers these, so nothing was verified about them."),
      h("ul", null, nc.map((x) => h("li", null, x)))));
  }
  page(...top, h("section", { class: "claims", "aria-label": "The claims" }, blocks), ...rest);
  for (const run of used) feedback.load(run.id);
}

async function main() {
  let got;
  try {
    got = await load();
  } catch (e) {
    stateLine(`The results store did not answer: ${e.message}`);
    return;
  }
  if (!got) return;
  if (!got.runs.length) {
    stateLine(`There are no runs of ${feature} in ${project} in the store yet.`);
    return;
  }
  draw(got.runs, got.results);
}

main();
