// One run (run.html?run=<id>). On the left, the claims: each has an arrow that drops down its variations, and a
// variation is what is clicked to see its proof. On the right, the chosen claim and the chosen variation's proof
// (its film, when it was filmed, starts playing when the variation is chosen). The selection lives in the address:
// #p3/c2 for a case, #p3 for where property p3 was tested.
import { h, clear, brandLink, signOut } from "./dom.js";
import { getRun } from "./api.js";
import { verdict, outcome, countsSentence, marksLabel, clock, when, upper, plural, titleOf, claimName, fromClaim, variationSentence, viewportOf, filmShape } from "./words.js";
import { caseBody, placesBody, caseKind, triedWays, variationShort } from "./proof.js";
import { estimateLength } from "./film.js";
import { feedbackStore, feedbackControls } from "./feedback.js";
import { sourceLine, askedFor, draftChanges } from "./learn.js";

const root = document.getElementById("app");
const state = { id: null, doc: null, base: "", group: "new", film: null, shownProperty: null, els: {},
  open: new Set(),      // the claims whose variations are dropped down in the left column
  openedFor: null,      // the selection the left column last opened a claim for
  autoplay: false };    // set when a variation is chosen by hand: its film starts playing
const feedback = feedbackStore();   // what the person thinks of a claim's result, or a variation's, in this run

// ---------- choosing what to show ----------

const props = () => state.doc.properties || [];
const findProp = (id) => props().find((p) => p.id === id) || null;

function defaultProperty() {
  const all = props();
  return all.find((p) => p.verdict === "fails") || all.find((p) => p.verdict === "needs_decision") || all[0] || null;
}

function defaultCase(p) {
  const cs = (p && p.cases) || [];
  return cs.find((c) => c.outcome === "breaks") || cs.find((c) => c.outcome === "declined") || cs[0] || null;
}

function readHash() {
  let raw = "";
  try {
    raw = decodeURIComponent((location.hash || "").replace(/^#/, ""));
  } catch {
    raw = "";
  }
  const [pid, cid] = raw.split("/");
  let p = pid ? findProp(pid) : null;
  if (!p) {
    p = defaultProperty();
    const c = defaultCase(p);
    return { p, c, places: false };
  }
  if (cid) {
    const c = (p.cases || []).find((x) => x.id === cid);
    if (c) return { p, c, places: false };
    return { p, c: defaultCase(p), places: false };
  }
  // "#p3": the places view, when the property has ways in and cases and was not planned from a claim;
  // otherwise its default case, with the address left as it is.
  if (!fromClaim(p) && (p.ways_in || []).length && (p.cases || []).length) return { p, c: null, places: true };
  return { p, c: defaultCase(p), places: false };
}

function hashFor(p, c) {
  return c ? `#${p.id}/${c.id}` : `#${p.id}`;
}

function go(hash) {
  if (location.hash === hash) show();
  else location.hash = hash;
}

// ---------- sidebar ----------

// Names a person (or a screen reader) finds things by: the claim's title or sentence, the variation's sentence.
// Two things with the same words get a number, so no two controls share a name.
function numbered(names) {
  const seen = {};
  const total = {};
  for (const n of names) total[n] = (total[n] || 0) + 1;
  return names.map((n) => {
    if (total[n] < 2) return n;
    seen[n] = (seen[n] || 0) + 1;
    return `${n} (${seen[n]})`;
  });
}

const cardName = (p) => claimName(p) || `Claim ${upper(p.id)}`;
const tileName = (p, c) => variationSentence(p, c) || `Case ${upper(c.id)}`;
function cardNames() {
  const all = props();
  const names = numbered(all.map(cardName));
  return new Map(all.map((p, i) => [p, names[i]]));
}
function tileNames(p) {   // the variations' names
  const cs = p.cases || [];
  const names = numbered(cs.map((c) => tileName(p, c)));
  return new Map(cs.map((c, i) => [c, names[i]]));
}

// Whether the owner approved the property as it was run. Runs made before approvals, or with no approval record,
// carry nothing here, and then nothing is said.
const PART_WORDS = { statement: "the sentence", title: "the title", your_words: "your own words",
  checked_against: "what it is checked against", variations: "the variations" };
const changedWords = (p) => (Array.isArray(p.approval_changed) ? p.approval_changed : []).map((k) => PART_WORDS[k] || String(k).replace(/_/g, " ")).join(", ");

function approvalLine(p) {
  if (p.approval === "changed") {
    const parts = changedWords(p);
    return `Changed after it was approved${parts ? `: ${parts}` : ""}.`;
  }
  if (p.approval === "new") return "Not approved yet.";
  return "";
}

// How long a variation's film is, or the short word for a variation with nothing to time.
function variationLength(c) {
  const o = outcome(c.outcome);
  const kind = caseKind(c);
  if (["cant_run", "outside", "nothing_there", "planned"].includes(c.outcome)) return o.tile;
  if (kind === "film") {
    const len = estimateLength(c, c.clips.length - 1);
    return len ? clock(len) : "film";
  }
  if (kind === "requests") return plural(c.requests.length, "request", "requests");
  if (kind === "code") return plural(c.excerpts.length, "excerpt", "excerpts");
  if (kind === "shot") return "screenshot";
  return o.tile;
}

// One variation in the left column: its short name, how it came out, its sentence and how long its film is.
function variationRow(p, c, label) {
  const o = outcome(c.outcome);
  const len = variationLength(c);
  const timed = /^\d+:\d\d$/.test(len);
  const btn = h("button", {
    type: "button", class: "vline", "aria-current": "false", "data-cid": c.id,
    "aria-label": label, "aria-describedby": `cd-${p.id}-${c.id}`,
  },
  h("span", { class: "vn", "aria-hidden": "true" }, variationShort(p, c)),
  h("span", { class: `glyph ${o.tone}`, "aria-hidden": "true" }, o.glyph),
  h("span", { class: "sf" }, variationSentence(p, c), timed ? null : h("span", { class: "vnote" }, len)),
  timed ? h("span", { class: "dur" }, len) : null,
  h("span", { class: "sr", id: `cd-${p.id}-${c.id}` }, `${o.long}.`));
  btn.addEventListener("click", () => {
    state.autoplay = true;
    go(hashFor(p, c));
    revealMain();
  });
  return h("li", null, btn);
}

// One claim in the left column: an arrow that drops down its variations, its name (which chooses it and its first
// variation) and how it came out. Folded away, it shows how its variations came out as small marks.
function card(p, label) {
  const v = verdict(p.verdict);
  const cases = p.cases || [];
  const notRun = p.verdict === "not_run";
  const descId = `pd-${p.id}`;
  const listId = `pv-${p.id}`;
  const name = titleOf(p) || p.statement || "No sentence given";
  const arrow = h("button", { type: "button", class: "ptw", "aria-expanded": "false", "aria-controls": listId, disabled: !cases.length,
    "aria-label": `The variations of ${label}` }, h("span", { class: "chev", "aria-hidden": "true" }));
  arrow.addEventListener("click", () => {
    if (state.open.has(p.id)) state.open.delete(p.id);
    else state.open.add(p.id);
    markSelection();
  });
  const btn = h("button", {
    type: "button", class: `pcard${notRun ? " notrun" : ""}`, "aria-current": "false",
    "aria-label": label, "aria-describedby": descId,
    "data-pid": p.id,
  },
  h("span", { class: "st" }, name),
  h("span", { class: `v ${v.tone}` }, notRun ? "" : v.short),
  h("span", { class: "sr", id: descId }, `${v.long}. ${notRun ? "" : `${marksLabel(cases)}.`}`));
  btn.addEventListener("click", () => {
    state.open.add(p.id);
    state.autoplay = true;
    markSelection();
    go(hashFor(p, defaultCase(p)));
    revealMain();
  });
  const folded = notRun ? h("span", { class: "notrun-line" }, "Not run in this run")
    : h("span", { class: "marks", "aria-hidden": "true" }, cases.map((c) => h("span", { class: `m ${outcome(c.outcome).mark}`, "data-cid": c.id }, outcome(c.outcome).glyph)));
  const names = tileNames(p);
  const list = h("ul", { class: "vlist", id: listId, hidden: true, "aria-label": `The variations tried for ${label}` },
    cases.map((c) => variationRow(p, c, names.get(c))));
  return h("div", { class: "pgroup", "data-pid": p.id }, h("div", { class: "phead" }, arrow, btn), folded, list);
}

function revealMain() {
  const side = state.els.side;
  const main = state.els.main;
  if (side && main && main.offsetTop > side.offsetTop + 10) main.scrollIntoView({ block: "start" });
}

function drawCards() {
  const holder = state.els.cards;
  clear(holder);
  const inGroup = props().filter((p) => (p.group || "new") === state.group);
  if (!inGroup.length) {
    holder.append(h("p", { class: "empty-note" }, state.group === "regression"
      ? "No earlier properties were re-checked in this run."
      : "This run has no new properties."));
    return;
  }
  const names = cardNames();
  holder.append(...inGroup.map((p) => card(p, names.get(p))));
  markSelection();
}

function sidebar() {
  const d = state.doc;
  const all = props();
  const newCount = all.filter((p) => (p.group || "new") === "new").length;
  const regCount = all.filter((p) => p.group === "regression").length;
  const sw = (group, word, n) => {
    const b = h("button", { type: "button", "aria-pressed": String(state.group === group), "aria-label": `${word}, ${n}` }, word, h("i", null, String(n)));
    b.addEventListener("click", () => {
      state.group = group;
      state.els.switch.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      drawCards();
    });
    return b;
  };
  state.els.switch = h("div", { class: "switch", role: "group", "aria-label": "Which properties" },
    sw("new", "New", newCount), sw("regression", "Regression", regCount));
  state.els.cards = h("nav", { class: "cards", "aria-label": "Properties" });
  const summary = d.project && d.feature
    ? h("a", { class: "summary-link", href: `feature.html?project=${encodeURIComponent(d.project)}&feature=${encodeURIComponent(d.feature)}` },
      `Summary of ${d.feature}`)
    : null;
  const totals = d.totals || {};
  const pc = totals.properties || {};
  const cc = totals.cases || {};
  const nCases = Object.values(cc).reduce((a, b) => a + (Number(b) || 0), 0);
  const ra = d.ran_against || {};
  const kv = (k, v) => h("div", { class: "kv" }, h("span", null, k), h("b", null, v));
  const side = h("aside", { class: "side" },
    h("div", { class: "brand" }, brandLink(), signOut()),
    h("h2", { class: "sr" }, "Properties"),
    summary,
    state.els.switch,
    state.els.cards,
    h("section", { class: "run", "aria-label": "This run" },
      h("h2", null, "This run"),
      kv("App", `${d.project || "Unnamed app"} · ${d.feature || "unnamed feature"}`),
      kv("Commit", ra.commit ? `${ra.commit}${ra.uncommitted_changes ? ", with uncommitted changes" : ""}` : "unknown"),
      kv("Properties", `${pc.holds || 0} of ${all.length} hold`),
      kv("Cases", `${cc.holds || 0} of ${nCases} held`),
      kv("Ran", when(d.ran_at)),
      // What was asked for and what changed before approval, closed until opened (absent when there is nothing).
      askedFor(d.context), draftChanges(d.draft_changes, d.properties)));
  state.els.side = side;
  return side;
}

function markSelection() {
  const sel = state.sel;
  if (!sel) return;
  for (const g of state.els.cards.querySelectorAll(".pgroup")) {
    const on = !!sel.p && g.dataset.pid === sel.p.id;
    const open = state.open.has(g.dataset.pid);
    g.classList.toggle("current", on);
    g.classList.toggle("open", open);
    g.querySelector(".pcard").setAttribute("aria-current", String(on));
    g.querySelector(".ptw").setAttribute("aria-expanded", String(open));
    g.querySelector(".vlist").hidden = !open;
    g.querySelectorAll(".m").forEach((m) => m.classList.toggle("on", on && !!sel.c && m.dataset.cid === sel.c.id));
    g.querySelectorAll(".vline").forEach((b) => b.setAttribute("aria-current", String(on && !!sel.c && b.dataset.cid === sel.c.id)));
  }
}

// ---------- main area ----------

function propertyHead(p) {
  // What a reader needs at a glance: what the property is, how it came out, and how many cases stand behind that.
  const v = verdict(p.verdict);
  const cases = p.cases || [];
  const status = [h("span", { class: `verdict-word ${v.tone}` }, v.long)];
  if (p.verdict !== "not_run") {
    status.push(h("span", { class: "mid" }, cases.length ? countsSentence(p.counts || {}, cases.length) : "No cases"));
    // The list of places a code search found is only offered where a search was made.
    if (p.searched && !fromClaim(p) && (p.ways_in || []).length && cases.length) {
      const n = triedWays(p).length || p.ways_in.length;
      const b = h("button", { type: "button", class: "places-btn", "aria-pressed": "false" }, `Tested in ${plural(n, "place", "places")}`);
      b.addEventListener("click", () => {
        if (b.getAttribute("aria-pressed") === "true") go(hashFor(p, defaultCase(p)));
        else go(`#${p.id}`);
      });
      state.els.placesBtn = b;
      status.push(b);
    }
  }
  const title = titleOf(p);
  const approval = approvalLine(p);
  return h("div", { class: "head" },
    h("h2", null, title || p.statement || "No sentence given"),
    h("div", { class: "meta" }, status),
    approval ? h("p", { class: "approval-note" }, approval) : null,
    title && p.statement ? h("p", { class: "statement" }, p.statement) : null,
    sourceLine(p),
    p.verdict !== "not_run" ? feedbackControls(feedback, { runId: state.id, prop: p.id, name: `the claim \u201c${claimName(p)}\u201d` }) : null);
}

function drawProperty(p) {
  const main = state.els.main;
  clear(main);
  state.els.placesBtn = null;
  main.append(propertyHead(p));
  const cases = p.cases || [];
  if (p.verdict === "not_run") {
    main.append(h("div", { class: "body" }, h("div", { class: "stagecol" }, h("div", { class: "box" },
      h("p", null, h("b", { style: { fontWeight: 600 } }, "Not run in this run. "),
        "This property is in the feature file, but this run did not test it, so it has no cases and no proof. Run it to see whether it holds.")))));
    state.els.body = null;
    return;
  }
  if (!cases.length) {
    main.append(h("div", { class: "body" }, h("div", { class: "stagecol" }, h("div", { class: "box" },
      h("p", null, p.no_plan_because ? `No cases were planned: ${p.no_plan_because}` : "No cases were run for this property.")))));
  }
  state.els.body = h("div");
  main.append(state.els.body);
}

function banners() {
  const out = [];
  const d = state.doc;
  if (d.error) out.push(h("p", { class: "banner bad", role: "status" }, `This run did not finish: ${d.error}`));
  else if (d.aborted) out.push(h("p", { class: "banner bad", role: "status" }, "This run was stopped before it finished, so some properties may have no cases."));
  if (state.complete === false) out.push(h("p", { class: "banner", role: "status" }, "This run's upload is not complete, so some films and screenshots may be missing."));
  const named = (p) => titleOf(p) || p.id;
  const changed = props().filter((p) => p.approval === "changed");
  const unapproved = props().filter((p) => p.approval === "new");
  const said = [];
  if (changed.length) said.push(`Changed after it was approved: ${changed.map((p) => `${named(p)}${changedWords(p) ? ` (${changedWords(p)})` : ""}`).join("; ")}.`);
  if (unapproved.length) said.push(`Not approved yet: ${unapproved.map(named).join("; ")}.`);
  if (said.length) out.push(h("p", { class: "banner approval", role: "status" }, said.join(" ")));
  return out;
}

function ctx(p) {
  return { base: state.base, feature: state.doc.feature || "<feature>", pid: p.id, prop: p, platform: (state.doc.ran_against || {}).platform || "",
    viewport: viewportOf(state.doc), feedback: { store: feedback, runId: state.id }, autoplay: state.autoplay,
    steps: new URLSearchParams(location.search).get("steps") === "1" };      // &steps=1: the film with its steps beside it
}

function show() {
  const sel = readHash();
  state.sel = sel;
  if (state.film) { state.film.stop(); state.film = null; }
  if (!sel.p) {
    clear(state.els.main).append(h("p", { class: "state" }, "This run has no properties."));
    return;
  }
  if (sel.p !== state.shownProperty) {
    drawProperty(sel.p);
    state.shownProperty = sel.p;
  }
  // Choosing a claim, or one of its variations, drops that claim's variations down in the left column.
  const chosen = `${sel.p.id}/${sel.c ? sel.c.id : ""}`;
  if (state.openedFor !== chosen) { state.open.add(sel.p.id); state.openedFor = chosen; }
  markSelection();
  if (state.els.placesBtn) state.els.placesBtn.setAttribute("aria-pressed", String(sel.places));
  if (!state.els.body) return;
  clear(state.els.body);
  if (sel.places) {
    const featured = defaultCase(sel.p);
    state.els.body.append(placesBody(sel.p, ctx(sel.p), featured, (c) => go(hashFor(sel.p, c))));
  } else if (sel.c) {
    const { body, film } = caseBody(sel.c, ctx(sel.p));
    state.film = film;
    state.els.body.append(body);
    // A film that is not in the store has no length to give: its line in the left column says so once that is known.
    const line = [...state.els.cards.querySelectorAll(".vline[aria-current=true] .dur")][0];
    if (film && film.video && line) film.video.addEventListener("error", () => { line.textContent = "no film"; });
  }
  state.autoplay = false;
  const what = [cardName(sel.p), sel.c ? tileName(sel.p, sel.c) : ""].filter(Boolean).join(": ");
  document.title = `${what} · ${state.doc.project || "Run"} · Groundtruth`;
}

async function main() {
  const params = new URLSearchParams(location.search);
  state.id = params.get("run");
  if (!state.id) {
    clear(root).append(h("div", { class: "list-page" }, h("header", { class: "list-top" }, brandLink(), signOut()),
      h("main", null, h("h1", null, "No run chosen"), h("p", { class: "plain" }, "Choose a run from the list of test runs."))));
    return;
  }
  let data;
  try {
    data = await getRun(state.id);
  } catch (e) {
    clear(root).append(h("div", { class: "list-page" }, h("header", { class: "list-top" }, brandLink(), signOut()),
      h("main", null, h("h1", null, "This run could not be opened"), h("p", { class: "plain" }, e.message))));
    return;
  }
  state.doc = (data && data.document) || {};
  state.base = (data && data.files_base) || `/v1/runs/${encodeURIComponent(state.id)}/files/`;
  state.complete = data ? data.complete : true;
  const d = state.doc;
  state.els.main = filmShape(h("main", { class: "main", id: "main" }), viewportOf(d));
  const app = h("div", { class: "app" },
    h("h1", { class: "sr" }, `Run of ${d.project || "an app"}, feature ${d.feature || "unnamed"}, ${when(d.ran_at)}`),
    ...banners(), sidebar(), state.els.main);
  clear(root).append(app);
  drawCards();
  feedback.load(state.id);
  window.addEventListener("hashchange", show);
  show();
  // An address that names a property opens on its proof, also on a narrow screen where the list comes first.
  if ((location.hash || "").length > 1) revealMain();
}

main();
