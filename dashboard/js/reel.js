// The reel: a list of variations played back to back in a full-window overlay. Each claim opens with a title card
// (the owner's words and the title); each variation gets a card with its sentence, then its film from the start,
// then a short result card. A variation with no film gets its two cards, held a little longer.
import { h, clear, playIcon, pauseIcon } from "./dom.js";
import { clock, outcome, claimName, titleOf, filmShape, comparisonCount } from "./words.js";
import { fileURL } from "./api.js";
import { createStoryboard } from "./storyboard.js";
import { rowsFor, outcomeWord, reasonWords } from "./summary.js";
import { caseKind } from "./proof.js";

export const TIMES = { claim: 2500, intro: 2000, result: 1500, introNoFilm: 3000, resultNoFilm: 2500 };

// claims: [{p, base, viewport}] in order, optionally led by {walkthrough, base, viewport, claimTitle}. The segments
// of the reel, in order. Each carries v, the index of its variation (a claim's title card belongs to the claim's
// first variation; the walkthrough's segments have v -1), and n, how many variations the reel has.
export function reelPlan(claims) {
  const segs = [];
  let v = 0;
  for (const claim of claims || []) {
    if (claim.walkthrough) {
      const c = claim.walkthrough;
      if (caseKind(c) !== "film") continue;
      segs.push({ kind: "tour", v: -1, claim, c, ms: TIMES.claim });
      segs.push({ kind: "film", v: -1, claim, c, film: true });
      segs.push({ kind: "result", v: -1, claim, c, film: true, ms: TIMES.result });
      continue;
    }
    const cases = rowsFor(claim.p).filter((r) => r.c).map((r) => r.c);
    if (!cases.length) continue;
    cases.forEach((c, i) => {
      const film = caseKind(c) === "film";
      if (i === 0) segs.push({ kind: "claim", v, claim, c, ms: TIMES.claim });
      segs.push({ kind: "intro", v, claim, c, film, ms: film ? TIMES.intro : TIMES.introNoFilm });
      if (film) segs.push({ kind: "film", v, claim, c, film });
      segs.push({ kind: "result", v, claim, c, film, ms: film ? TIMES.result : TIMES.resultNoFilm });
      v += 1;
    });
  }
  for (const s of segs) s.n = v;
  return segs;
}

const oneLine = (s, max = 160) => {
  const first = String(s || "").split("\n").map((x) => x.trim()).find(Boolean) || "";
  return first.length > max ? `${first.slice(0, max - 1).trimEnd()}…` : first;
};

function setting(c) {
  return typeof c.setting === "string" ? c.setting.trim() : "";
}

export function openReel(claims, opts = {}) {
  const segs = reelPlan(claims);
  if (!segs.length) return null;
  const opener = document.activeElement;
  const total = segs[0].n;
  const startOf = new Map();
  segs.forEach((s, i) => { if (!startOf.has(s.v)) startOf.set(s.v, i); });

  let at = 0;
  let paused = false;
  let timer = 0;
  let timerEnds = 0;
  let remaining = 0;
  let video = null;
  let dir = null;          // the current film with its living storyboard
  let finished = false;

  const progress = h("p", { class: "reel-progress", "aria-live": "polite" });
  const closeBtn = h("button", { type: "button", class: "btn small reel-close" }, "Close");
  const stage = h("div", { class: "reel-stage" });
  const prevBtn = h("button", { type: "button", class: "btn small" }, "Previous variation");
  const pauseBtn = h("button", { type: "button", class: "btn small reel-pause", "aria-label": "Pause" }, pauseIcon(), h("span", null, "Pause"));
  const nextBtn = h("button", { type: "button", class: "btn small" }, "Next variation");
  const box = h("div", {
    class: "reel", role: "dialog", "aria-modal": "true", "aria-label": opts.title ? `Films: ${opts.title}` : "Films",
  },
  h("div", { class: "reel-top" }, progress, closeBtn),
  stage,
  h("div", { class: "reel-ctrl" }, prevBtn, pauseBtn, nextBtn));

  function clearTimer() {
    if (timer) clearTimeout(timer);
    timer = 0;
  }

  function startTimer(ms) {
    clearTimer();
    remaining = ms;
    if (paused) return;
    timerEnds = Date.now() + ms;
    timer = setTimeout(() => { timer = 0; go(at + 1); }, ms);
  }

  function dropVideo() {
    if (dir) dir.stop();
    dir = null;
    if (video) {
      video.pause();
      video.removeAttribute("src");
      video.load();
    }
    video = null;
  }

  function card(cls, ...kids) {
    return h("div", { class: `reel-card ${cls}` }, ...kids);
  }

  function draw(seg) {
    clear(stage);
    const c = seg.c;
    const p = seg.claim.p;
    box.dataset.seg = seg.kind;
    box.dataset.cid = c.id;
    box.dataset.pid = p ? p.id : "";
    progress.textContent = seg.v < 0 ? `Walkthrough · ${total} ${total === 1 ? "variation" : "variations"} follow` : `${seg.v + 1} of ${total} · ${claimName(p)}`;
    if (seg.kind === "tour") {
      const s = setting(c);
      stage.append(card("claim",
        h("p", { class: "reel-title big" }, "Watch it being used"),
        h("p", { class: "reel-k" }, "A demonstration of the feature, start to finish. Each claim's result comes from its own variations."),
        s ? h("p", { class: "reel-setting" }, `Starts from: ${s}`) : null));
      startTimer(seg.ms);
    } else if (seg.kind === "claim") {
      const words = typeof p.your_words === "string" ? p.your_words.trim() : "";
      stage.append(card("claim",
        words ? h("blockquote", { class: "reel-said" }, `“${words}”`) : null,
        h("p", { class: words ? "reel-title" : "reel-title big" }, titleOf(p) || p.statement || "")));
      startTimer(seg.ms);
    } else if (seg.kind === "intro") {
      const s = setting(c);
      stage.append(card("intro",
        h("p", { class: "reel-k" }, "Variation"),
        h("p", { class: "reel-sentence" }, rowsFor(p).find((r) => r.c === c).sentence),
        s ? h("p", { class: "reel-setting" }, `Starts from: ${s}`) : null,
        seg.film ? null : h("p", { class: "reel-k" }, "No film was recorded for this variation.")));
      startTimer(seg.ms);
    } else if (seg.kind === "film") {
      // The same film with a living storyboard as on the pages, playing on its own and moving on when it ends.
      const done = () => { if (dir === d && segs[at] === seg) go(at + 1); };
      // The reel plays straight on: it holds at each step like the pages, but never stops there for the person.
      const d = createStoryboard(c, { base: seg.claim.base, viewport: seg.claim.viewport, steps: seg.claim.steps || undefined,
        autoplay: !paused, onEnd: done, label: "The film and its steps", stepStop: false });
      dir = d;
      video = d.video;
      d.el.classList.add("in-reel");
      stage.append(d.el);
      d.video.addEventListener("error", () => { if (dir === d) startTimer(TIMES.result); });
    } else if (seg.kind === "result") {
      const o = outcome(c.outcome);
      // The reason in one line: the first comparison that did not match, else the case's own words.
      const off = (c.steps || []).find((x) => x.kind === "compare" && x.matches === false);
      const why = c.outcome !== "holds" && off ? String(off.what || off.words || "") : reasonWords(c);
      const count = comparisonCount(c);
      stage.append(card(`result ${o.tone}`,
        h("p", { class: "reel-result" }, h("span", { class: `m ${o.mark}`, "aria-hidden": "true" }, o.glyph),
          h("span", null, why && c.outcome !== "outside" ? `${outcomeWord(c)}: ${oneLine(why, 90)}` : outcomeWord(c))),
        count ? h("p", { class: "reel-setting" }, count) : null));
      startTimer(seg.ms);
    }
  }

  function showEnd() {
    finished = true;
    clearTimer();
    dropVideo();
    clear(stage);
    box.dataset.seg = "end";
    progress.textContent = `${total} of ${total} · done`;
    const again = h("button", { type: "button", class: "btn go" }, "Watch again");
    again.addEventListener("click", () => go(0));
    stage.append(card("end", h("p", { class: "reel-title" }, `That was ${total === 1 ? "the one variation" : `all ${total} variations`}.`),
      h("div", { class: "btns" }, again)));
    again.focus();
  }

  function go(i) {
    clearTimer();
    dropVideo();
    if (i >= segs.length) { showEnd(); return; }
    finished = false;
    at = Math.max(0, i);
    draw(segs[at]);
  }

  function setPaused(on) {
    paused = on;
    clear(pauseBtn).append(on ? playIcon() : pauseIcon(), h("span", null, on ? "Resume" : "Pause"));
    pauseBtn.setAttribute("aria-label", on ? "Resume" : "Pause");
    box.dataset.paused = String(on);
    if (finished) return;
    if (on) {
      if (timer) { remaining = Math.max(0, timerEnds - Date.now()); clearTimer(); }
      if (dir) dir.pause();
    } else {
      if (dir && video && !video.hidden) dir.play();       // the storyboard carries on from where it was
      else if (segs[at].kind !== "film" || (video && video.hidden)) startTimer(remaining || 300);
    }
  }

  const next = () => go(finished ? segs.length : (startOf.get(segs[at].v + 1) ?? segs.length));
  const prev = () => {
    if (finished) { go(startOf.get(total - 1) ?? 0); return; }
    const v = segs[at].v;
    const here = startOf.get(v);
    go(at > here + 1 || !startOf.has(v - 1) ? here : startOf.get(v - 1));
  };

  function close() {
    clearTimer();
    dropVideo();
    document.removeEventListener("keydown", onKey, true);
    box.remove();
    document.body.classList.remove("reel-open");
    if (opener && typeof opener.focus === "function") opener.focus();
    if (opts.onClose) opts.onClose();
  }

  function onKey(e) {
    if (e.key === "Escape") { e.preventDefault(); close(); return; }
    if (e.key === " " || e.key === "Spacebar") {
      if (e.target instanceof HTMLButtonElement && box.contains(e.target) && e.target !== pauseBtn) return;     // another focused button keeps its own space
      e.preventDefault();
      setPaused(!paused);
      return;
    }
    if (e.key === "ArrowRight") { e.preventDefault(); next(); return; }
    if (e.key === "ArrowLeft") { e.preventDefault(); prev(); return; }
    if (e.key === "Tab") {
      const els = [...box.querySelectorAll("button:not([disabled])")];
      if (!els.length) return;
      const first = els[0];
      const last = els[els.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      else if (!box.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
    }
  }

  closeBtn.addEventListener("click", close);
  pauseBtn.addEventListener("click", () => setPaused(!paused));
  nextBtn.addEventListener("click", next);
  prevBtn.addEventListener("click", prev);
  document.addEventListener("keydown", onKey, true);
  document.body.classList.add("reel-open");
  document.body.append(box);
  go(0);
  pauseBtn.focus();       // so the space bar pauses from the start
  return { close, el: box };
}
