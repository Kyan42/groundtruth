// The proof area for one case: what fills the stage, what sits under it, and the side panel.
// Built from parts, because one case can carry several kinds of proof at once.
import { h, clear } from "./dom.js";
import { clock, outcome, upper, plural, listWords, variationSentence } from "./words.js";
import { fileURL } from "./api.js";
import { isAction } from "./film.js";
import { createStoryboard } from "./storyboard.js";
import { feedbackControls } from "./feedback.js";

const NOT_COUNTED = new Set(["cant_run", "outside", "nothing_there"]);
const ROLES = "link|button|listitem|textbox|checkbox|combobox|tab|option|menuitem|radio|heading|img|cell|row|switch|searchbox|slider|spinbutton|card|item";

// Plain words for one step, with the address or the control picked out.
export function stepWords(words) {
  const text = String(words || "");
  let m = text.match(/^(open|go to|navigate to) (\S+)(.*)$/);
  if (m) return [`${m[1]} `, h("span", { class: "path" }, m[2]), m[3]];
  m = text.match(new RegExp(`^(click|double-click|hover over|fill|type into|press|select|check|uncheck|focus) (?:(?:${ROLES}) )?"(.*)$`));
  if (m) {
    const rest = m[2];
    const end = rest.lastIndexOf('"');
    const name = end >= 0 ? rest.slice(0, end) : rest;
    const tail = end >= 0 ? rest.slice(end + 1) : "";
    return [`${m[1]} `, h("b", { style: { fontWeight: "600" } }, name), tail];
  }
  m = text.match(/^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) (\S+)(.*)$/);
  if (m) return [`${m[1]} `, h("span", { class: "path" }, m[2]), m[3]];
  return [text];
}

export function caseKind(c) {
  if ((c.clips || []).length) return "film";
  if ((c.requests || []).length) return "requests";
  if ((c.excerpts || []).length) return "code";
  if (c.door === "browser" && citedShot(c)) return "shot";
  return "plain";
}

export function citedShot(c) {
  const shots = c.shots || [];
  return shots.filter((s) => s.cited).pop() || shots[shots.length - 1] || null;
}

// How a case is named to a person: by its variation's sentence, and only by its id when it has no words at all.
export function caseName(p, c) {
  const s = variationSentence(p, c);
  return s ? `the variation “${s}”` : `case ${upper(c.id)}`;
}

// ---------- stage parts ----------

function shotStage(c, base, name) {
  const shot = citedShot(c);
  const gone = h("div", { class: "gone", hidden: true }, "The screenshot of this variation is not in the store, so it cannot be shown.");
  const img = h("img", { src: fileURL(base, shot.file), alt: `Screenshot of ${name}${shot.label ? `, ${shot.label}` : ""}, which the case cited as evidence.` });
  img.addEventListener("error", () => { img.hidden = true; gone.hidden = false; });
  return [
    h("div", { class: "stage" }, h("div", { class: "film" }, img, gone)),
    h("p", { class: "note" }, "No film was recorded for this case, because it ran before filming; this is the screenshot it cited."),
  ];
}

function requestStage(c, highlight) {
  const items = (c.requests || []).map((r, i) => {
    const okStatus = Number(r.status) >= 200 && Number(r.status) < 300;
    const response = String(r.response ?? "");
    const short = response.length > 320 ? `${response.slice(0, 320)}…` : response;
    const answer = h("span", { class: "mid" }, short);
    let more = null;
    if (short !== response) {
      more = h("button", { type: "button", class: "toggle", "aria-expanded": "false" }, `Show the whole answer to request ${i + 1}`);
      more.addEventListener("click", () => {
        const open = more.getAttribute("aria-expanded") !== "true";
        answer.textContent = open ? response : short;
        more.setAttribute("aria-expanded", String(open));
        more.textContent = `${open ? "Show less of" : "Show the whole"} answer to request ${i + 1}`;
      });
    }
    const hl = highlight && !okStatus;
    return h("li", { class: "sec" },
      h("p", { class: "k" }, `Request ${i + 1}${r.at != null ? `, at ${clock(r.at)}` : ""}`),
      h("div", { class: `code req${hl ? " hl" : ""}` },
        h("span", { class: "l" }, h("b", { class: "ok" }, r.method || "?"), ` ${r.path || ""}`, r.body ? h("span", { class: "mid" }, ` ${r.body}`) : null),
        h("span", { class: "l" }, h("b", { class: okStatus ? "ok" : "warn" }, String(r.status ?? "no answer")), " ", answer)),
      more);
  });
  return h("div", { class: "stage" }, h("div", { class: "sheet" },
    h("h3", null, "What was sent, and what came back"),
    h("ol", { style: { listStyle: "none", margin: 0, padding: 0, display: "grid", gap: "14px" } }, items)));
}

function codeStage(c) {
  const parts = (c.excerpts || []).map((x) => h("div", { class: "sec" },
    h("div", { class: "exhead" },
      h("span", { class: "mono ok wrap" }, x.start === x.end ? `${x.path}, line ${x.start}` : `${x.path}, lines ${x.start} to ${x.end}`),
      x.why ? h("span", { class: "mid", style: { fontSize: "13px" } }, x.why) : null),
    h("pre", { class: "code", "aria-label": `${x.path}, lines ${x.start} to ${x.end}` },
      (x.lines || []).map((line, i) => h("span", { class: "l" }, h("span", { class: "ln", "aria-hidden": "true" }, String((x.start || 1) + i)), line)))));
  const checks = (c.checks || []).map((k) => {
    const rule = k.rule || {};
    const said = [];
    if (rule.glob) said.push(`files matching ${rule.glob}`);
    if (rule.must_contain) said.push(`must contain ${rule.must_contain}`);
    if (rule.must_not_contain) said.push(`must not contain ${rule.must_not_contain}`);
    return h("li", null, `${said.join("; ")}. ${k.passed ?? 0} of ${k.checked ?? 0} files passed.`);
  });
  return h("div", { class: "stage" }, h("div", { class: "sheet" },
    h("h3", null, "The lines this case rests on"),
    parts,
    checks.length ? h("div", { class: "sec" }, h("p", { class: "k" }, "Checks run over every file"), h("ul", { class: "outlist wrap" }, checks)) : null,
    h("p", { class: "warn", style: { fontSize: "13px" } }, "This rests on reading the code, which is weaker proof than watching the app.")));
}

function plainStage(c) {
  const o = outcome(c.outcome);
  const title = NOT_COUNTED.has(c.outcome) ? `${o.long}.` : "Nothing was recorded on screen for this case.";
  return h("div", { class: "stage" }, h("div", { class: "sheet" },
    h("h3", null, title),
    h("p", null, c.observed || "The case left no words about what it saw."),
    NOT_COUNTED.has(c.outcome) ? h("p", { class: "mid" }, "This case does not count for or against the property.") : null,
    h("p", { class: "note" }, "No film or screenshot was kept for this case.")));
}

// Before and after readings. The readings taken after the case are the ones its steps list after
// its last action or request; every other reading was taken before (the first of each kind is shown).
export function readingPairs(c) {
  const rs = c.readings || [];
  const steps = c.steps || [];
  let lastAct = -1;
  steps.forEach((s, i) => { if (isAction(s)) lastAct = i; });
  const k = lastAct < 0 ? 0 : steps.slice(lastAct + 1).filter((s) => s.kind === "reading").length;
  const afterList = k ? rs.slice(rs.length - k) : [];
  const seen = new Set();
  const before = [];
  for (const r of rs.slice(0, rs.length - k)) {
    if (seen.has(r.what)) continue;
    seen.add(r.what);
    before.push(r);
  }
  return { before, after: afterList };
}

function readingsUnder(c) {
  const { before, after } = readingPairs(c);
  if (!before.length && !after.length) return null;
  const broke = c.outcome === "breaks";
  const was = new Map(before.map((r) => [r.what, r.text]));
  const list = (rs, mark) => rs.map((r) => {
    const changed = mark && was.has(r.what) && was.get(r.what) !== r.text;
    return h("div", { class: "reading" }, h("b", { class: changed ? "bad" : null }, r.text), h("span", { class: "mono mid" }, r.what));
  });
  return h("div", { class: "pair" },
    h("div", { class: "box" }, h("p", { class: "k" }, "Before the case touched the app"),
      before.length ? list(before, false) : h("p", { class: "mid" }, "No reading was taken before the case.")),
    h("div", { class: `box${broke ? " broke" : ""}` }, h("p", { class: "k" }, "After the case"),
      after.length ? list(after, broke) : h("p", { class: "mid" }, "No reading was taken after the case.")));
}

function notCountedBox(c) {
  const o = outcome(c.outcome);
  if (c.outcome === "planned") {
    return h("div", { class: "box outcome-box" }, h("p", null, h("b", { style: { fontWeight: "600" } }, `${o.long}. `),
      h("span", { class: "mid" }, "This case has not run, so there is nothing to show yet.")));
  }
  return h("div", { class: "box outcome-box" }, h("p", null, h("b", { style: { fontWeight: "600" } }, `${o.long}. `),
    h("span", { class: "mid" }, c.observed || "The case gave no reason."), " ",
    h("span", { class: "mid" }, "It does not count for or against the property.")),
  dataRequestLine(c, true));
}

// One plain sentence for a case that could not run because no data tool could make its starting state.
function dataRequestLine(c, under) {
  const r = c.data_request;
  if (!r || typeof r !== "object") return null;
  const state = String(r.state || "").trim().replace(/\.+$/, "");
  const tool = r.tool && r.tool.name ? String(r.tool.name).replace(/_/g, " ") : "";
  if (!state && !tool) return null;
  const words = [state ? `It needed ${state}` : "It needed starting data no data tool could make"];
  if (tool) words.push(`, and asks for a data tool called “${tool}”`);
  return h("p", { class: "mid", style: under ? { marginTop: "6px" } : null }, `${words.join("")}.`);
}

function brokeBox(c) {
  return h("div", { class: "box broke outcome-box" }, h("p", null, h("b", { style: { fontWeight: "600" } }, "Broke. "),
    c.observed || "The case left no words about what it saw."));
}

// ---------- what data the case started from ----------

function valueWords(v) {
  if (v === true) return "yes";
  if (v === false) return "no";
  if (v === null || v === undefined || v === "") return "none";
  if (Array.isArray(v)) return v.map(valueWords).join(", ");
  if (typeof v === "object") return Object.entries(v).map(([k, x]) => `${k.replace(/_/g, " ")} ${valueWords(x)}`).join(", ");
  return String(v).replace(/_/g, " ");
}

export function startWords(step) {
  const parts = [String(step.tool || "a data tool").replace(/_/g, " ")];
  const input = step.input && typeof step.input === "object" ? step.input : {};
  for (const [k, v] of Object.entries(input)) parts.push(`${k.replace(/_/g, " ")} ${valueWords(v)}`);
  return parts.join(" · ");
}

// What the case started from. With the planner's plain sentence (setting), that sentence is shown and the exact
// data put in waits behind a closed disclosure; without it, the exact data is the line.
export function startedFrom(c) {
  const steps = (c.starts_from || []).filter((s) => s && typeof s === "object");
  const setting = typeof c.setting === "string" ? c.setting.trim() : "";
  const items = steps.map((s, i) => {
    const said = [i ? "; " : "", startWords(s)];
    if (s.error !== undefined && s.error !== null && s.error !== false) {
      said.push(h("span", { class: "bad" }, ` (this starting data could not be set up: ${String(s.error)})`));
    }
    return said;
  });
  if (setting) {
    return h("div", { class: "started" },
      h("p", null, h("span", { class: "k" }, "Starts from: "), setting),
      items.length ? h("details", { class: "exact" }, h("summary", null, "Exact data put in"), h("p", null, items)) : null);
  }
  if (!steps.length) return null;
  return h("p", { class: "started" }, h("span", { class: "k" }, "Started from "), items);
}

// ---------- side panel ----------

function sec(title, ...body) {
  return h("div", { class: "sec" }, h("h4", { class: "k" }, title), ...body);
}

function stepLabel(s, i, steps) {
  if (s.kind === "code") return s.ok === false ? "refused" : String(s.words || "").split(" ")[0] || "read";
  if (s.kind === "request") return s.status != null ? String(s.status) : "sent";
  if (s.kind === "reading") return steps.slice(0, i).some((x) => isAction(x)) ? "after" : "before";
  return "·";
}

function stepsSection(c, film, opts) {
  const all = c.steps || [];
  const shown = (s) => s.kind !== "look";
  const hiddenCount = all.filter((s) => !shown(s)).length;
  const list = h("ol", { class: "steps" });
  let showAll = false;
  const rows = [];
  function draw() {
    clear(list);
    rows.length = 0;
    all.forEach((s, i) => {
      if (!showAll && !shown(s)) return;
      const timed = typeof s.at === "number" && s.clip !== null && s.clip !== undefined;
      let lab;
      if (timed && film && opts.jump) {
        lab = h("button", { type: "button", class: "jump", "aria-label": `${clock(s.at)} in the film, step ${i + 1}: ${s.words}` }, clock(s.at));
        lab.addEventListener("click", () => film.seek(s.clip, s.at));
        if (film.failed) lab.disabled = true;
      } else if (timed) {
        lab = h("span", { class: "lab" }, clock(s.at));
      } else {
        lab = h("span", { class: "lab" }, stepLabel(s, i, all));
      }
      const words = h("span", { class: "words" }, stepWords(s.words),
        s.kind === "reading" && !timed && stepLabel(s, i, all) === "before" && (c.clips || []).length ? h("span", { class: "mid" }, " (taken by Groundtruth itself, before the case starts)") : null,
        s.ok === false ? h("span", { class: "prob" }, ` (${s.problem || "this step failed"})`) : null);
      const li = h("li", { class: "step" }, lab, words);
      rows.push({ li, s });
      list.append(li);
    });
    if (film) mark(film.clip, film.video.currentTime || 0);
  }
  function mark(clip, t) {
    let on = null;
    for (const r of rows) if (r.s.clip === clip && typeof r.s.at === "number" && r.s.at <= t + 0.05) on = r;
    for (const r of rows) r.li.classList.toggle("on", r === on);
  }
  if (film) film.onTime(mark);
  if (film) film.onFail(() => list.querySelectorAll("button.jump").forEach((b) => { b.disabled = true; }));
  draw();
  let toggle = null;
  if (hiddenCount) {
    toggle = h("button", { type: "button", class: "toggle", "aria-expanded": "false" }, `Show every step (${hiddenCount} more)`);
    toggle.addEventListener("click", () => {
      showAll = !showAll;
      toggle.setAttribute("aria-expanded", String(showAll));
      toggle.textContent = showAll ? "Show fewer steps" : `Show every step (${hiddenCount} more)`;
      draw();
    });
  }
  const onlyCode = all.length && all.every((s) => s.kind === "code" || s.kind === "look");
  if (!all.length) return sec("What was done", h("p", { class: "mid" }, "No steps were recorded."));
  return sec(onlyCode ? "What was read" : "What was done", list, toggle);
}

function mediaSummary(text) {
  const lines = String(text || "").split("\n").map((l) => l.trim()).filter(Boolean);
  const parsed = lines.map((l) => l.match(/^\d+\.\s*(video|audio)\s+source\s+(\S+?);\s*length\s+([\d.]+)\s*s\b.*?;\s*(visible|hidden)\s*$/i)).filter(Boolean);
  if (!parsed.length) return null;
  const visible = parsed.filter((m) => m[4].toLowerCase() === "visible");
  if (!visible.length) return "no visible player";
  return visible.map((m) => `${m[2].split("/").pop()}, ${m[3]} s`).join("; ");
}

function mediaSection(c, film) {
  const steps = c.steps || [];
  const rows = (c.media || []).map((r, i) => {
    let before = null;
    for (const s of steps) if (s.kind === "action" && typeof s.at === "number" && typeof r.at === "number" && s.at <= r.at + 0.01) before = s;
    const when = before ? ["After ", stepWords(before.words)] : i === 0 ? "Before any action" : `Reading ${i + 1}`;
    const summary = mediaSummary(r.text);
    const li = h("li", { class: "step media" },
      h("span", { class: "mid words" }, typeof r.at === "number" ? `${clock(r.at)} · ` : "", when),
      summary ? h("span", { class: "val" }, summary) : h("span", { class: "val", style: { whiteSpace: "pre-wrap", textAlign: "left" } }, r.text));
    return { li, r };
  });
  if (film) film.onTime((clip, t) => {
    let on = null;
    for (const x of rows) if (typeof x.r.at === "number" && x.r.at <= t + 0.05) on = x;
    for (const x of rows) x.li.classList.toggle("on", x === on);
  });
  return sec("What the player had loaded",
    h("ol", { class: "steps" }, rows.map((x) => x.li)),
    h("p", { class: "note", style: { paddingTop: "6px" } }, "This says which file the visible player had loaded, not what the picture looked like."));
}

function questionBox(c, ctx) {
  const cmd = (answer) => `groundtruth.py decide <repo> ${ctx.feature} ${ctx.pid} ${c.id} ${answer}`;
  const status = h("p", { class: "note", role: "status" });
  const line = (answer, words) => {
    const code = h("code", null, cmd(answer));
    const copy = h("button", { type: "button", class: "btn small", "aria-label": `Copy the command for ${words}` }, "Copy");
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(cmd(answer));
        status.textContent = `Copied the command for ${words}.`;
      } catch {
        const range = document.createRange();
        range.selectNodeContents(code);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        status.textContent = "The command is selected; copy it with your keyboard.";
      }
    });
    return h("div", { class: "cmd" }, code, copy);
  };
  return h("div", { class: "box warn", style: { display: "grid", gap: "8px" } },
    h("h4", { style: { margin: 0, fontWeight: 600, fontSize: "14px" } }, "Is this intended?"),
    h("p", null, c.question || "The app refused this case. Is that what you meant it to do?"),
    c.draft_if_intended ? h("p", { class: "mid" }, "If it is, this is added as a property of its own: ", h("span", { style: { color: "var(--ink)" } }, c.draft_if_intended)) : null,
    h("div", { class: "btns" },
      h("button", { type: "button", class: "btn", disabled: true }, "It is intended"),
      h("button", { type: "button", class: "btn", disabled: true }, "It is a bug")),
    h("p", { class: "mid" }, "These buttons cannot record an answer yet. For now the answer is given with the decide command:"),
    line("intended", "“It is intended”"),
    line("bug", "“It is a bug”"),
    status);
}

export function panel(c, ctx, film, opts = {}) {
  const o = outcome(c.outcome);
  const title = opts.title || "This case";
  const parts = [
    h("div", { class: "top" },
      h("h3", null, title),
      h("span", { class: `pill ${o.tone}` }, o.long)),
  ];
  if (c.outcome === "declined" && !opts.brief) parts.push(questionBox(c, ctx));
  parts.push(sec("What was seen", h("p", null, c.observed || "The case left no words about what it saw.")));
  if (NOT_COUNTED.has(c.outcome) && !opts.brief) {
    parts.push(sec("What this means for the property", h("p", { class: "mid" },
      c.outcome === "cant_run"
        ? "A property is only told when every planned case ran. This one could not, so it does not count either way."
        : c.outcome === "outside"
          ? "This case turned out to be outside what the property's sentence covers, so it does not count either way."
          : "There was nothing there to test, so this case does not count either way.")));
  }
  if ((c.media || []).length && !opts.brief) parts.push(mediaSection(c, film));
  parts.push(stepsSection(c, film, { jump: !opts.brief }));
  if (opts.action) parts.push(h("div", { class: "btns" }, opts.action));
  if (c.door === "api" && !(c.clips || []).length && !opts.brief) {
    parts.push(sec("Why there is no film", h("p", { class: "mid" },
      "A person can skip the page and call this address directly. A property that says something can't happen is tried that way too, so nothing was on screen.")));
  }
  if ((c.readings || []).length && !opts.brief) {
    parts.push(sec("How far this goes", h("p", { class: "mid" },
      `A measurement only speaks for the machine it ran on${ctx.platform ? `: ${ctx.platform}` : ""}.`)));
  }
  parts.push(sec("What it stands for", h("p", { class: "mid" }, c.stands_for || c.what || "Not given.")));
  const sf = variationSentence(ctx && ctx.prop, c);
  return h("aside", { class: "panel", "aria-label": `${title}${sf ? `: ${sf}` : ""}` }, parts);
}

// ---------- the whole proof area for one case ----------

// Which variation is on the page, in one line above its proof: its short name (V1, V2, ... in the claim's order, as the
// list of variations has it), its sentence, and how it came out.
export const variationShort = (p, c) => `V${((p && p.cases) || []).indexOf(c) + 1}`;

function variationLine(p, c) {
  const o = outcome(c.outcome);
  const n = ((p && p.cases) || []).length;
  return h("div", { class: "vhead" },
    h("span", { class: "vchip" }, variationShort(p, c)), h("span", { class: "vof" }, `of ${n}`),
    h("p", null, h("span", { class: `glyph ${o.tone}`, "aria-hidden": "true" }, o.glyph), variationSentence(p, c) || `Case ${upper(c.id)}`),
    h("span", { class: `vout ${o.tone}` }, o.long));
}

export function caseBody(c, ctx) {
  const kind = caseKind(c);
  const name = caseName(ctx.prop, c);
  // What a reader scans first sits above the proof: what data the case started from, and one box that explains any
  // outcome other than held. The plain stage already says what was seen and whether it counts, so there only the
  // question (or a data request) is added.
  // A filmed case's starting data is in its storyboard (the first step); only the planner's sentence is said here.
  // The film itself opens on the data the case read first, typed in a terminal, so the planner's sentence is said only
  // when a filmed case read nothing.
  const setting = typeof c.setting === "string" ? c.setting.trim() : "";
  const reads = (c.starts_from || []).some((sf) => sf && typeof sf === "object");
  const col = [variationLine(ctx.prop, c),
    kind === "film" ? (setting && (!reads || ctx.steps) ? h("p", { class: "started" }, h("span", { class: "k" }, "Starts from: "), setting) : null) : startedFrom(c)];
  if (c.outcome === "declined") col.push(questionBox(c, ctx));
  else if (c.outcome === "breaks" && kind !== "plain") col.push(brokeBox(c));
  else if ((NOT_COUNTED.has(c.outcome) || c.outcome === "planned") && kind !== "plain") col.push(notCountedBox(c));
  else if (kind === "plain" && c.outcome !== "holds") {
    const dr = dataRequestLine(c);
    if (dr) col.push(h("div", { class: "box outcome-box" }, dr));
  }
  let film = null;
  if (kind === "film") {
    // The film alone, large, with one caption, as the walkthrough has it. Nothing is said aloud during a variation, so
    // the caption is made from the variation's sentence and what each check compared (story.js: captionsOf).
    // ctx.steps asks for the film with its steps beside it instead (run.html?...&steps=1), as the summary of claims has it.
    const sentence = variationSentence(ctx.prop, c).replace(/[.\s]+$/, "");
    film = ctx.steps
      ? createStoryboard(c, { base: ctx.base, viewport: ctx.viewport, label: `The film of ${caseName(ctx.prop, c)} and its steps` })
      : createStoryboard(c, { base: ctx.base, viewport: ctx.viewport, label: `The film of ${caseName(ctx.prop, c)}`, captions: true,
        autoplay: !!ctx.autoplay, standIn: { intent: sentence ? `Trying: ${sentence}.` : "", ok: c.outcome === "holds", result: `${outcome(c.outcome).long}.` } });
    col.push(film.el);
  } else if (kind === "shot") {
    col.push(...shotStage(c, ctx.base, name));
  } else if (kind === "requests") {
    col.push(requestStage(c, true));
    col.push(h("p", { class: "note" }, "No film: this case called the app directly, without the pages, so nothing was on screen."));
  } else if (kind === "code") {
    col.push(codeStage(c));
    col.push(h("p", { class: "note" }, "No film: this case never opened a page. Each excerpt is at most 15 lines."));
  } else {
    col.push(plainStage(c));
  }
  if (kind !== "requests" && (c.requests || []).length) col.push(requestStage(c, true));
  if (kind !== "code" && (c.excerpts || []).length) col.push(codeStage(c));
  const readings = readingsUnder(c);
  if (readings) col.push(readings);
  // What the person thinks of this variation's result, last and quiet.
  if (ctx.feedback && ctx.feedback.runId) {
    col.push(feedbackControls(ctx.feedback.store, { runId: ctx.feedback.runId, prop: ctx.prop.id, caseId: c.id, name }));
  }
  const body = h("div", { class: "body" },
    h("section", { class: "stagecol", "aria-label": `Proof for ${name}` }, col));
  return { body, film };
}

// ---------- "Tested in N places" ----------

const STATUS_WORDS = {
  found: { searched: "Found by the search", read: "Found by reading the code", tone: "ok" },
  flagged: { searched: "Flagged: builds its request address from parts", read: "Flagged: builds its request address from parts", tone: "warn" },
  "to check": { searched: "Kept to be safe", read: "Kept to be safe", tone: "mid" },
};

// What happened at one place, in plain words: a single case by its sentence, several by how many.
function ranSentence(p, cases) {
  const by = {};
  for (const c of cases) (by[c.outcome] = by[c.outcome] || []).push(c);
  const words = { holds: "held", breaks: "broke", declined: "needs your decision", outside: "was outside the property's wording", nothing_there: "found nothing there to test", cant_run: "could not run", planned: "is planned" };
  const plurals = { declined: "need your decision", outside: "were outside the property's wording", planned: "are planned" };
  return Object.entries(by).map(([k, cs]) => {
    if (cs.length === 1) {
      const sf = variationSentence(p, cs[0]);
      return `${sf ? `“${sf}”` : "One case"} ${words[k] || "ran"}.`;
    }
    return `${cs.length} cases ${plurals[k] || words[k] || "ran"}.`;
  }).join(" ");
}

export function triedWays(p) {
  const cases = p.cases || [];
  return (p.ways_in || []).filter((w) => cases.some((c) => c.way_in === w.id));
}

export function placesBody(p, ctx, featured, onOpen) {
  const cases = p.cases || [];
  const tried = triedWays(p);
  const untried = (p.ways_in || []).filter((w) => !tried.includes(w));
  const places = tried.map((w) => {
    const here = cases.filter((c) => c.way_in === w.id);
    const st = STATUS_WORDS[w.status] || { searched: w.status || "", read: w.status || "", tone: "mid" };
    const broke = here.some((c) => c.outcome === "breaks");
    const loc = [w.page && `page ${w.page}`, w.route && `route ${w.route}`, w.file && `file ${w.file}`].filter(Boolean).join(" · ");
    return h("li", { class: `place${broke ? " broke" : ""}` },
      h("div", { class: "row" },
        h("span", { class: "where" }, w.where || w.id),
        h("span", { class: `v ${st.tone}` }, p.searched ? st.searched : st.read)),
      loc ? h("span", { class: "loc mono" }, loc) : null,
      h("div", { class: "ran" },
        h("span", { class: "marks", "aria-hidden": "true" }, here.map((c) => h("span", { class: `m ${outcome(c.outcome).mark}` }, outcome(c.outcome).glyph))),
        h("span", { class: broke ? "bad" : "mid", style: { fontSize: "13px" } }, ranSentence(p, here))));
  });
  const extra = [];
  if (untried.length) {
    extra.push(h("p", { class: "note" }, `Not tried: ${untried.map((w) => w.route || w.where || w.id).join("; ")}. ${untried.some((w) => w.door === "api") ? "A direct call is only made when a property says something can never happen." : ""}`));
  }
  if ((p.left_out || []).length) {
    extra.push(h("div", { class: "sec" }, h("h4", { class: "k" }, "Files left out"),
      h("ul", { class: "outlist wrap" }, p.left_out.map((x) => h("li", null, h("span", { class: "mono" }, x.file), `: ${x.why}${x.because ? `; ${x.because}` : ""}.`)))));
  } else {
    extra.push(h("p", { class: "note" }, "No file was left out."));
  }
  extra.push(h("p", { class: "note" }, h("b", { style: { fontWeight: 600 } },
    p.searched ? "These ways in came from the code search. " : "These ways in came from a model reading the code, not from the code search. "),
    p.how_found || ""));
  for (const n of p.search_notes || []) extra.push(h("p", { class: "note" }, n));
  if (p.not_covered) extra.push(h("p", { class: "note" }, h("b", { style: { fontWeight: 600 } }, "Not covered: "), p.not_covered));
  const sheet = h("div", { class: "stage" }, h("div", { class: "sheet" },
    h("h3", null, "Where a person can do this, and what happened there"),
    places.length ? h("ul", { style: { listStyle: "none", margin: 0, padding: 0, display: "grid", gap: "10px" } }, places) : h("p", { class: "mid" }, "No case was run at any of the ways in."),
    extra));
  let side = null;
  if (featured) {
    const title = featured.outcome === "breaks" ? "The case that broke" : featured.outcome === "declined" ? "The case that needs your decision" : "The first case";
    const open = h("button", { type: "button", class: "btn go", onclick: () => onOpen(featured) },
      (featured.clips || []).length ? "Watch the film of this case" : "Open this case");
    side = panel(featured, ctx, null, { title, brief: true, action: open });
  }
  return h("div", { class: "body" }, h("section", { class: "stagecol", "aria-label": "Where the property was tested" }, sheet), side);
}

export { plural };
