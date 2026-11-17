// The storyboard of a case: its steps in plain words, made by plain code from the run's record, each with a visual
// by its kind (a terminal card for a data read, a still from the film for a browser step, the whole page for the
// situation check, a close-up of the outlined part for a comparison, a result card). No model is involved.
import { h, clear } from "./dom.js";
import { fileURL } from "./api.js";

// ---------- plain words ----------

const titleCase = (s) => String(s || "").replace(/[-_]+/g, " ").replace(/\b\w/g, (m) => m.toUpperCase());
const lower1 = (s) => (s ? s[0].toLowerCase() + s.slice(1) : s);
const clockOf = (t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
const shortDate = (iso) => {
  const d = new Date(`${iso}T12:00:00`);
  return isNaN(d) ? String(iso || "") : d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
};
export const quote = (s, max = 70) => {
  const t = String(s || "").replace(/\s+/g, " ").trim();
  return `“${t.length > max ? `${t.slice(0, max - 1).trimEnd()}…` : t}”`;
};
const named = (rest) => {
  const a = rest.indexOf('"');
  const b = rest.lastIndexOf('"');
  return a >= 0 && b > a ? rest.slice(a + 1, b) : a >= 0 ? rest.slice(a + 1) : rest;
};
// The verifier's own name for what it checked, kept as it wrote it (it may start with a name, such as "Grubb").
const said = (what) => `\u201c${String(what || "").trim().replace(/[.\s]+$/, "")}\u201d`;
const firstSentence = (s, max = 200) => {
  const t = String(s || "").trim();
  const m = t.match(/^[\s\S]*?[.!?](?=\s|$)/);
  const one = m ? m[0] : t;
  return one.length > max ? `${one.slice(0, max - 1).trimEnd()}…` : one;
};

// What an action did, in the past tense; "" for waits and blind attempts, which a person need not see.
export function actionPast(words, firstOpen) {
  const w = String(words || "").trim();
  let m = w.match(/^(?:open|go to|navigate to) (\S+)/i);
  if (m) {
    const path = m[1].split("?")[0];
    const last = path.split("/").filter(Boolean).pop() || "";
    const page = path === "/" || /^index\.html$/i.test(last) ? "the front page" : `the ${titleCase(last.replace(/\.html?$/i, ""))} page`;
    return firstOpen ? `Opened ${page} in a real browser.` : `Went to ${page}.`;
  }
  m = w.match(/^click option (.+)$/i);
  if (m) return `Chose ${quote(named(m[1]))} from the menu.`;
  m = w.match(/^click combobox (.+)$/i);
  if (m) return `Opened the ${quote(named(m[1]))} menu.`;
  m = w.match(/^(click|double-click|point at|hover over) (.+)$/i);
  if (m) return `${/click/i.test(m[1]) ? "Clicked" : "Pointed at"} ${quote(named(m[2]))}.`;
  m = w.match(/^type "(.*)" into (.+)$/i);
  if (m) return `Typed ${quote(m[1], 30)} into ${quote(named(m[2]))}.`;
  m = w.match(/^fill (.+?) with "(.*)"$/i);
  if (m) return `Filled ${quote(named(m[1]))} with ${quote(m[2], 30)}.`;
  m = w.match(/^press "?([^"]+)"?$/i);
  if (m) return `Pressed ${m[1]}.`;
  m = w.match(/^(GET|POST|PUT|PATCH|DELETE) (\S+)/);
  if (m) return `Sent ${m[1]} ${m[2]} to the app directly.`;
  return "";
}

const isNav = (s) => s && s.kind === "action" && /^(open|go to|navigate)/i.test(String(s.words || ""));
const isWait = (s) => s && s.kind === "action" && /^(wait\b|try to\b)/i.test(String(s.words || ""));

function mealIn(text, meals) {
  for (const m of meals || []) if (m && m.meal && new RegExp(`\\b${m.meal}\\b`, "i").test(text || "")) return m;
  return null;
}

// One plain sentence for a data tool's run, from its input and what it returned.
export function dataSentence(sf, situation) {
  const ret = sf.returned && typeof sf.returned === "object" ? sf.returned : {};
  const input = sf.input && typeof sf.input === "object" ? sf.input : {};
  if (sf.error) return `Tried to set up the starting data (${String(sf.tool || "a data tool").replace(/_/g, " ")}), which failed: ${sf.error}.`;
  if (typeof ret.asked === "string" && ret.asked) {
    const who = ret.asked.replace(/,?\s*directly$/i, "");
    const hall = titleCase(ret.hall || input.hall || "");
    const meal = mealIn(situation && situation.found, ret.meals) || (ret.meal ? { meal: ret.meal } : null);
    if (ret.found !== undefined && ret.meal) {
      return `Asked ${who} for a ${hall} meal listing ${String(input.stations || "").split(",").join(" and ")}: ${ret.meal.toLowerCase()} on ${shortDate(ret.date)}.`;
    }
    return `Asked ${who} directly what ${hall || "it"} lists${meal ? ` for ${meal.meal.toLowerCase()}` : ""}${ret.date ? ` on ${shortDate(ret.date)}` : ""}.`;
  }
  return `Put in the starting data: ${String(sf.tool || "a data tool").replace(/_/g, " ")}.`;
}

// ---------- the steps ----------

// A still shows what an action led to: a moment later, before the next thing happens.
function stillAfter(s, timed, i) {
  const next = timed.slice(i + 1).find((x) => x.kind === "action" || x.kind === "compare" || x.kind === "say");
  return Math.max(s.at, Math.min(s.at + 2.5, next ? next.at - 0.1 : s.at + 2.5));
}

// A whole-page still that would show the same page as the previous step's is left out: nothing but waits and looks
// happened between them.
function dropRepeats(out, timed) {
  let lastStill = null;
  for (const st of out) {
    if (!st.still) continue;
    // A comparison's picture is its evidence and always stays; it also starts a new page for the next check.
    if (st.kind === "compare" || st.kind === "stop" || st.still.crop) { lastStill = null; continue; }
    if (lastStill) {
      const between = timed.filter((s) => s.at > lastStill.t && s.at < st.t && s.kind === "action" && !isWait(s));
      if (!between.length && st.kind !== "browser") { st.samePageAs = lastStill.n; st.still = null; continue; }
    }
    lastStill = st;
  }
}

const RESULT = {
  holds: ["held", "Held"], breaks: ["broke", "Broke"], declined: ["needs your decision", "Needs your decision"],
  cant_run: ["could not run", "Could not run"], outside: ["was outside the claim's wording", "Outside the wording"],
  nothing_there: ["found nothing there to test", "Nothing there to test"], planned: ["is planned, not run yet", "Planned"],
};

function resultStep(c, timed) {
  const off = timed.find((s) => s.kind === "compare" && !s.situation && s.matches === false);
  const [verb, word] = RESULT[c.outcome] || ["ended", "Ended"];
  const cmps = timed.filter((s) => s.kind === "compare" && !s.situation);
  const reason = off ? `${said(off.what || off.words || "the page")} was not what the claim says.`
    : cmps.length && c.outcome === "holds" ? `${cmps.length === 1 ? "The comparison" : `All ${cmps.length} comparisons`} matched.`
      : firstSentence(c.observed) || "";
  const last = timed.length ? timed[timed.length - 1].at : 0;
  return { kind: "result", text: `Reported that this variation ${verb}.`, reason, t: last + 0.75, ok: c.outcome === "holds", word, outcome: c.outcome };
}

function timedOf(c) {
  const clip = Math.max(0, (c.clips || []).length - 1);
  return (c.steps || []).filter((s) => s && typeof s.at === "number" && isFinite(s.at) && (s.clip === clip || s.clip === undefined || !(c.clips || []).length))
    .map((s, i) => ({ s, i })).sort((a, b) => a.s.at - b.s.at || a.i - b.i).map((x) => x.s);
}

export function storySteps(c) {
  const timed = timedOf(c);
  const situation = timed.find((s) => s.kind === "compare" && s.situation) || null;
  const out = [];
  for (const sf of c.starts_from || []) if (sf && typeof sf === "object") out.push({ kind: "data", text: dataSentence(sf, situation), t: null, sfs: [sf], situation });
  let opened = false;
  timed.forEach((s, i) => {
    if (s.kind === "action" || s.kind === "request") {
      const text = actionPast(s.words, !opened);
      if (isNav(s)) opened = true;
      // `settle`: a moment after the action, the page as it came to be (where the film holds on this step).
      if (text) out.push({ kind: "browser", text, t: s.at, settle: stillAfter(s, timed, i), still: (c.clips || []).length ? { at: stillAfter(s, timed, i), crop: null } : null });
    } else if (s.kind === "compare" && s.situation) {
      out.push({ kind: "situation", text: `Checked it was in the right situation: ${String(s.found || s.what || "").replace(/\.$/, "")}.`, t: s.at, still: { at: s.at + 0.2, crop: null }, cmp: s, ok: s.matches !== false });
    } else if (s.kind === "compare") {
      const ok = s.matches !== false;
      out.push({ kind: "compare", text: `Compared ${said(s.what || s.words || "the page")} with what the claim says. ${ok ? "It matches." : "It does not match."}`,
        t: s.at, still: { at: s.at + 0.6, crop: s.element ? (ok ? "green" : "red") : null }, cmp: s, ok });
    } else if (s.kind === "say") {
      out.push({ kind: "say", text: String(s.text || s.words || ""), t: s.at, still: null });
    }
  });
  if (!(c.clips || []).length) for (const st of out) st.still = null;
  dropRepeats(out.map((x, i) => Object.assign(x, { n: i + 1 })), timed);
  out.push(resultStep(c, timed));
  out.forEach((x, i) => { x.n = i + 1; });
  return out;
}

// The stops of a walkthrough: every step from just after the previous stop's last comparison (or the film's start)
// up to and including its own last comparison; comparisons of the same claim in a row make one stop.
export function stopsOf(c) {
  const steps = timedOf(c);
  const cmps = steps.filter((s) => s.kind === "compare");
  const stops = [];
  let cur = [];
  for (const s of steps) {
    cur.push(s);
    if (s.kind !== "compare") continue;
    const next = cmps[cmps.indexOf(s) + 1];
    if (next && next.claim === s.claim) continue;
    const compares = cur.filter((x) => x.kind === "compare");
    stops.push({ n: stops.length + 1, claim: s.claim || null, steps: cur, compares, start: stops.length ? cur[0].at : 0, matched: compares.every((x) => x.matches !== false) });
    cur = [];
  }
  for (const st of stops) st.of = stops.length;
  return stops;
}

// A walkthrough as steps: the data it read, each browser step, each stop (the claim's title, the owner's words,
// the spoken lines, the close-up), and the result. propOf(id) finds the claim.
export function tourSteps(tour, propOf) {
  const timed = timedOf(tour);
  const out = [];
  // Each data read before the film is its own step, so each gets its own scene and hold in the film.
  for (const sf of (tour.starts_from || []).filter((x) => x && typeof x === "object")) out.push({ kind: "data", text: dataSentence(sf, null), t: null, sfs: [sf] });
  let opened = false;
  timed.forEach((s, i) => {
    if (s.kind !== "action") return;
    const text = actionPast(s.words, !opened);
    if (isNav(s)) opened = true;
    if (text) out.push({ kind: "browser", text, t: s.at, settle: stillAfter(s, timed, i), still: { at: stillAfter(s, timed, i), crop: null } });
  });
  const stops = stopsOf(tour);
  for (const st of stops) {
    const p = propOf(st.claim) || {};
    const says = st.steps.filter((s) => s.kind === "say");
    const cmp = st.compares[st.compares.length - 1];
    const title = String((typeof p.title === "string" && p.title.trim()) || p.statement || "A claim").replace(/[.\s]+$/, "");
    out.push({ kind: "stop", t: (says[0] || cmp).at, stop: st, title, claim: st.claim, words: typeof p.your_words === "string" && p.your_words.trim() ? p.your_words : "",
      says, cmps: st.compares, cmp, ok: st.matched, text: `Stop ${st.n} of ${st.of}: ${title}.`,
      still: { at: cmp.at + 0.6, crop: cmp.element ? (cmp.matches === false ? "red" : "green") : null } });
  }
  const first = out.filter((x) => x.t === null);
  const rest = out.filter((x) => x.t !== null).sort((a, b) => a.t - b.t);
  const all = [...first, ...rest];
  all.forEach((x, i) => { x.n = i + 1; });
  dropRepeats(all, timed);
  const bad = stops.filter((s) => !s.matched).length;
  const last = timed.length ? timed[timed.length - 1].at : 0;
  all.push({ kind: "result", t: last + 0.75, ok: !bad && tour.outcome === "holds",
    text: !stops.length ? "Reported how the walkthrough went." : bad ? `Reported that ${bad} of ${stops.length} stops did not match.` : `Reported that all ${stops.length} stops matched.`,
    reason: tour.outcome === "cant_run" ? `It could not be run${tour.observed ? `: ${firstSentence(tour.observed)}` : "."}` : "Each claim's result comes from its own variations; this is a demonstration.",
    word: bad ? "Did not match" : tour.outcome === "holds" ? "Matched" : "Did not finish" });
  all.forEach((x, i) => { x.n = i + 1; });
  return all;
}

// ---------- stills from the film ----------

const players = new Map();
function grab(src, t) {
  let p = players.get(src);
  if (!p) {
    const v = document.createElement("video");
    v.muted = true;
    v.preload = "auto";
    v.src = src;
    const ready = new Promise((res, rej) => { v.addEventListener("loadeddata", res, { once: true }); v.addEventListener("error", rej, { once: true }); });
    p = { v, chain: ready };
    players.set(src, p);
  }
  const job = p.chain.then(() => new Promise((res) => {
    const v = p.v;
    const to = Math.max(0, Math.min(t, (v.duration || t) - 0.05));
    const done = () => {
      const cv = document.createElement("canvas");
      cv.width = v.videoWidth;
      cv.height = v.videoHeight;
      cv.getContext("2d").drawImage(v, 0, 0);
      res(cv);
    };
    if (Math.abs(v.currentTime - to) < 0.01 && v.readyState >= 2) done();
    else { v.addEventListener("seeked", done, { once: true }); v.currentTime = to; }
  }));
  p.chain = job.catch(() => {});
  return job;
}

const COLOURS = {
  red: (r, g, b) => r > 170 && g < 95 && b < 95,
  green: (r, g, b) => g > 130 && r < 120 && g - r > 50 && g - b > 20,
  blue: (r, g, b) => b > 170 && r < 110 && b - r > 80,
};

// The outline the verifier drew around what it compared, found by its long straight top and bottom edges.
export function outline(cv, colour) {
  const test = COLOURS[colour];
  if (!test) return null;
  const { width: w, height: hgt } = cv;
  if (!w || !hgt) return null;
  const px = cv.getContext("2d").getImageData(0, 0, w, hgt).data;
  const hit = (x, y) => { const i = (y * w + x) * 4; return test(px[i], px[i + 1], px[i + 2]); };
  const runs = [];
  for (let y = 0; y < hgt; y += 1) {
    let start = -1;
    let best = null;
    for (let x = 0; x <= w; x += 1) {
      const on = x < w && hit(x, y);
      if (on && start < 0) start = x;
      if (!on && start >= 0) { if (!best || x - start > best.len) best = { x0: start, x1: x - 1, len: x - start }; start = -1; }
    }
    if (best && best.len >= 80) runs.push({ y, ...best });
  }
  if (!runs.length) return null;
  const top = runs.reduce((a, r) => (r.len > a.len ? r : a));
  const same = runs.filter((r) => Math.abs(r.x0 - top.x0) < 6 && Math.abs(r.x1 - top.x1) < 6);
  const y0 = Math.min(...same.map((r) => r.y));
  const y1 = Math.max(...same.map((r) => r.y));
  if (y1 - y0 < 12) return null;
  return { x: Math.min(...same.map((r) => r.x0)), y: y0, w: Math.max(...same.map((r) => r.x1)) - Math.min(...same.map((r) => r.x0)) + 1, h: y1 - y0 + 1 };
}

// Close-ups are cut to this shape, so their space can be kept before the picture arrives.
export const CLOSEUP = 3;     // wide by 1 high

const cache = new Map();
export function stillURL(src, at, crop) {
  const key = `${src}@${at}@${crop}`;
  if (!cache.has(key)) {
    cache.set(key, grab(src, at).then((cv) => {
      const box = crop ? outline(cv, crop) : null;
      let out = cv;
      if (box) {
        // A window of the close-up shape around the outline, as tight as fits it with some room.
        let w2 = Math.min(cv.width, Math.max(box.w + 80, (box.h + 60) * CLOSEUP));
        let h2 = Math.min(cv.height, w2 / CLOSEUP);
        if (h2 < box.h + 40) { h2 = Math.min(cv.height, box.h + 40); w2 = Math.min(cv.width, h2 * CLOSEUP); }
        const x = Math.max(0, Math.min(cv.width - w2, box.x + box.w / 2 - w2 / 2));
        const y = Math.max(0, Math.min(cv.height - h2, box.y + box.h / 2 - h2 / 2));
        out = document.createElement("canvas");
        out.width = Math.round(w2);
        out.height = Math.round(h2);
        out.getContext("2d").drawImage(cv, x, y, w2, h2, 0, 0, out.width, out.height);
      }
      return { url: out.toDataURL("image/jpeg", 0.86), cropped: !!box };
    }));
  }
  return cache.get(key);
}

// ---------- visuals ----------

const mark = (ok) => h("span", { class: `m ${ok ? "" : "x"}`, "aria-hidden": "true" }, ok ? "✓" : "✗");
const okWord = (ok) => h("span", { class: "sr" }, ok ? "Matches. " : "Does not match. ");

function pair(cmp) {
  return h("div", { class: `xf ${cmp.matches === false ? "off" : "on"}` },
    h("div", null, h("p", { class: "k" }, "Expected"), h("p", null, cmp.expected || "not said")),
    h("div", null, h("p", { class: "k" }, "Found"), h("p", null, cmp.found || "not said")));
}

// A command line built from the tool's name and input, then the answer it really returned: the part that matters
// first, all of it behind "show all".
export function terminal(sf, situation) {
  const { cmd, lines, more, ret } = termParts(sf, situation);
  return h("div", { class: "term", role: "group", "aria-label": `Data read before the film: ${cmd}` },
    h("div", { class: "tbar", "aria-hidden": "true" }, h("i"), h("i"), h("i"), h("span", null, "read before the film")),
    h("pre", { class: "tbody" }, h("span", { class: "tl cmd" }, h("span", { class: "tp" }, "$ "), cmd), ...lines),
    h("details", { class: "tall" }, h("summary", null, more ? `show all (${more})` : "show all"), h("pre", null, JSON.stringify(ret, null, 2))));
}

// A data read shown in the film's own frame, at the film's size, while its step holds: what was asked and what came
// back, before the browser appears. The film has nothing of its own for it.
export function scene(step) {
  const sf = (step.sfs || [])[0] || {};
  const { cmd, lines } = termParts(sf, step.situation);
  return h("div", { class: "scene", "data-n": String(step.n), role: "img", "aria-label": `Before the film: ${step.text}` },
    h("p", { class: "scene-k" }, "Before the film"),
    h("p", { class: "scene-say" }, step.text),
    h("pre", { class: "scene-term" }, h("span", { class: "tl cmd" }, h("span", { class: "tp" }, "$ "), cmd), ...lines));
}

// The data reads before a film as one terminal window over the film's first frame: each command is typed after the
// last one, and its answer (the first lines of it) comes out under it. draw(i, u, len) shows read i at u seconds into
// its turn of len seconds, with the earlier reads finished above it; `still` shows it finished at once. What is shown
// depends only on those, so going back or jumping shows exactly what playing would have.
export function readsTerminal(reads) {
  const all = reads.map((s) => {
    const { cmd, lines } = termParts((s.sfs || [])[0] || {}, s.situation);
    return { cmd, text: s.text, lines: lines.length > 6 ? [...lines.slice(0, 5), h("span", { class: "tl more" }, `… ${lines.length - 5} more lines`)] : lines };
  });
  const body = h("pre", { class: "rt-body" });
  const el = h("div", { class: "rterm", role: "img" },
    h("div", { class: "rt-win" }, h("div", { class: "rt-bar", "aria-hidden": "true" }, h("i"), h("i"), h("i"), h("span", null, "before the film")), body));
  const cursor = () => h("span", { class: "rt-cur", "aria-hidden": "true" });
  const prompt = (...kids) => h("span", { class: "tl cmd" }, h("span", { class: "tp" }, "$ "), ...kids);
  let drawn = "";
  function draw(i, u, len, still) {
    const rd = all[i];
    if (!rd) return;
    const n = rd.lines.length;
    // A moment at the prompt, the command typed, a beat, the answer out line by line, then time to read.
    const lead = Math.min(0.3, 0.1 * len);
    const type = Math.max(0.001, Math.min(0.42 * len, rd.cmd.length * 0.03));
    const out = Math.max(0.001, Math.min(0.2 * len, n * 0.05));
    const typed = still ? rd.cmd.length : u < lead ? 0 : Math.min(rd.cmd.length, Math.floor(((u - lead) / type) * rd.cmd.length));
    const ran = still || u >= lead + type + 0.12;
    const shown = still ? n : !ran ? 0 : Math.min(n, Math.max(1, Math.ceil(((u - lead - type - 0.12) / out) * n)));
    const key = `${i}|${typed}|${shown}|${ran}`;
    if (key === drawn) return;
    drawn = key;
    el.setAttribute("aria-label", `Before the film: ${rd.text}`);
    const blocks = all.slice(0, i + 1).map((x, k) => {
      const now = k === i;
      return h("span", { class: "tl rd" }, prompt(now ? x.cmd.slice(0, typed) : x.cmd, now && !ran ? cursor() : null),
        (now ? x.lines.slice(0, shown) : x.lines).map((l) => l.cloneNode(true)));
    });
    if (ran && shown >= n) blocks.push(h("span", { class: "tl rd" }, prompt(cursor())));
    clear(body).append(...blocks);
    body.scrollTop = body.scrollHeight;
    el.dataset.typed = String(typed);
    el.dataset.read = String(i + 1);
  }
  return { el, draw };
}

// What the verifier said during a walkthrough, as the film's captions: before each check, what it is about to check
// (the last thing it said for that claim; nothing when it said nothing for it); when the check lands, what it found.
// A check recorded without what was said shows its careful record: the name of the check when it matched, what was
// found when it did not. So every word is the verifier's own from the run, but the last line, which counts the checks.
// `show` is the moment of film a check is looked at: a little after it, when its outline is drawn, and before the
// next action changes the page. null when there are no checks or none has what was said (older runs): then there is
// nothing to caption with, unless `standIn` is given.
// A variation's film has nothing said aloud. Given `standIn` ({ intent, ok, result }), its captions are made from
// words the run already has: `intent` ("Trying: ...", the variation's sentence) before each check, and each check's
// name with whether it was as the claim says. With no check on film, `lead` is the intent for the whole film and the
// last line is `result`.
export function captionsOf(c, standIn) {
  const timed = timedOf(c);
  const cmps = timed.filter((s) => s.kind === "compare" && !s.situation);
  const words = (x) => (typeof x === "string" ? x.trim() : "");
  const spoken = cmps.some((x) => words(x.say));
  if (!spoken && !standIn) return null;
  const bare = (x) => words(x).replace(/[.\s]+$/, "");
  const said = timed.filter((s) => s.kind === "say" && words(s.words || s.text));
  const acts = timed.filter((s) => s.kind === "action" || s.kind === "request");
  const checks = cmps.map((x) => {
    const intent = [...said].reverse().find((s) => s.at <= x.at && s.claim === x.claim) || null;
    const next = acts.find((s) => s.at > x.at);
    const ok = x.matches !== false;
    // A film whose checks say nothing aloud: what was compared, and whether it was as the claim says. What was expected
    // and found is too long for a caption (it is in the film's list of steps).
    const plain = bare(x.what || x.words);
    const made = plain ? `${plain}: ${ok ? "as the claim says" : "not what the claim says"}.` : ok ? "As the claim says." : "Not what the claim says.";
    return { t: x.at, show: next ? Math.min(x.at + 0.6, Math.max(x.at, next.at - 0.05)) : x.at + 0.6, ok,
      intent: intent ? words(intent.words || intent.text) : spoken ? "" : words(standIn.intent),
      verdict: words(x.say) || (spoken ? words(ok ? x.what || x.words : x.found) || words(x.what || x.words) : made) };
  });
  const n = checks.length;
  const bad = checks.filter((k) => !k.ok).length;
  // With no check on film at all, the caption is what is being tried, then how it came out.
  if (!n) return { checks, lead: words(standIn.intent), end: { ok: !!standIn.ok, text: words(standIn.result) } };
  const text = n === 1 ? (bad ? "The check did not match." : "The check matched.") : bad ? `${bad} of ${n} checks did not match.` : `All ${n} checks matched.`;
  return { checks, end: { ok: !bad, text } };
}

// The command line and the answer's lines of one data read.
function termParts(sf, situation) {
  const input = sf.input && typeof sf.input === "object" ? sf.input : {};
  const cmd = [String(sf.tool || "data-tool").replace(/_/g, "-"),
    ...Object.entries(input).map(([k, v]) => `--${k.replace(/_/g, "-")} ${/\s|,/.test(String(v)) ? `"${typeof v === "object" ? JSON.stringify(v) : v}"` : typeof v === "object" ? JSON.stringify(v) : v}`)].join(" ");
  const ret = sf.returned && typeof sf.returned === "object" ? sf.returned : {};
  const lines = [];
  for (const [k, v] of Object.entries(ret)) if (v === null || typeof v !== "object") lines.push(h("span", { class: "tl" }, h("span", { class: "tk" }, `${k}: `), String(v)));
  const list = (title, items) => {
    lines.push(h("span", { class: "tl" }, h("span", { class: "tk" }, title)));
    items.slice(0, 12).forEach((x, i) => lines.push(h("span", { class: "tl in" }, h("span", { class: "tn" }, String(i + 1).padStart(2, " ")), ` ${typeof x === "object" ? JSON.stringify(x) : x}`)));
    if (items.length > 12) lines.push(h("span", { class: "tl in" }, `… ${items.length - 12} more`));
  };
  let more = "";
  if (Array.isArray(ret.meals)) {
    const meal = mealIn(situation && situation.found, ret.meals);
    const shown = meal ? [meal] : ret.meals.slice(0, 1);
    for (const m of shown) list(`${m.meal}${m.start ? ` ${m.start.slice(0, 5)}–${String(m.end || "").slice(0, 5)}` : ""}, stations in order:`, m.stations || []);
    const rest = ret.meals.filter((m) => !shown.includes(m));
    if (rest.length) more = `${rest.length} more: ${rest.map((m) => m.meal).join(", ")}`;
  }
  for (const [k, v] of Object.entries(ret)) {
    if (!Array.isArray(v) || k === "meals") continue;
    if (v.every((x) => x === null || typeof x !== "object")) list(`${k}:`, v);
    else lines.push(h("span", { class: "tl" }, h("span", { class: "tk" }, `${k}: `), `${v.length} ${v.length === 1 ? "entry" : "entries"}`));
  }
  if (sf.error) lines.push(h("span", { class: "tl err" }, `error: ${sf.error}`));
  return { cmd, lines, more, ret };
}

// A still, its space kept from the film's shape (or the close-up shape) so nothing moves when it arrives. A lazy one
// is drawn from the film only when loadStills is called on something holding it.
const loaders = new WeakMap();
function pictureOf(src, still, alt, vp, lazy) {
  const box = h("div", { class: `still${still.crop ? " closeup" : ""}`, style: still.crop ? null : { aspectRatio: `${vp.width} / ${vp.height}` } },
    h("span", { class: "loading" }, ""));
  const img = h("img", { alt });
  const load = () => {
    loaders.delete(box);
    stillURL(src, still.at, still.crop).then((r) => {
      img.src = r.url;
      img.dataset.cropped = r.cropped ? "yes" : "no";
      clear(box).append(img);
    }).catch(() => { clear(box).append(h("span", { class: "loading" }, "This picture could not be drawn from the film.")); });
  };
  if (lazy) { box.dataset.lazy = "yes"; loaders.set(box, load); } else load();
  return box;
}

// Draws the lazy stills inside `el` that are not drawn yet.
export function loadStills(el) {
  for (const box of el.querySelectorAll(".still[data-lazy]")) {
    const load = loaders.get(box);
    if (load) load();
  }
}

// The visual of one step. ctx: { src (the film, or ""), viewport, folded (terminal shows its first lines), lazy
// (stills wait for loadStills) }.
export function visual(step, ctx) {
  const vp = ctx.viewport || { width: 900, height: 700 };
  const picture = (src, still, alt) => pictureOf(src, still, alt, vp, !!ctx.lazy);
  if (step.kind === "data") {
    const t = h("div", { class: `terms${ctx.folded ? " folded" : ""}` }, step.sfs.map((sf) => terminal(sf, step.situation)));
    return t;
  }
  if (step.kind === "browser" || step.kind === "situation") {
    if (step.still && ctx.src) return picture(ctx.src, step.still, step.text, vp);
    if (step.samePageAs) return h("p", { class: "same" }, `The same page as step ${step.samePageAs}.`);
    return null;
  }
  if (step.kind === "compare") {
    return h("div", { class: "cmpvis" },
      ctx.src ? picture(ctx.src, step.still, `Close-up of the ${lower1(String(step.cmp.what || "part compared"))}`, vp) : null,
      step.cmp.element ? h("p", { class: "showed" }, h("span", { class: "k" }, "The page showed "), quote(step.cmp.element, 160)) : null,
      pair(step.cmp));
  }
  if (step.kind === "stop") {
    return h("div", { class: "stopvis" },
      step.words ? h("p", { class: "words" }, `“${step.words}”`) : null,
      step.says.length ? h("ul", { class: "says" }, step.says.map((s) => h("li", null, s.text || s.words))) : null,
      ctx.src ? picture(ctx.src, step.still, `Close-up for ${step.title}`, vp) : null,
      h("p", { class: `said ${step.ok ? "ok" : "bad"}` }, mark(step.ok), okWord(step.ok), (step.cmp.say || "").trim() || step.cmp.what || ""),
      h("details", { class: "careful" }, h("summary", null, "The careful record"), step.cmps.map(pair)));
  }
  if (step.kind === "result") {
    return h("div", { class: `rcard ${step.ok ? "ok" : "bad"}` }, h("span", { class: "big", "aria-hidden": "true" }, step.ok ? "✓" : "✗"),
      h("div", null, h("p", { class: "rword" }, step.word), step.reason ? h("p", { class: "rwhy" }, step.reason) : null));
  }
  return null;
}

export { mark, okWord, clockOf, fileURL };
