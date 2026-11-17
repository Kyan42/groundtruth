// The film with a living storyboard: the film on the left; beside it a progress rail of numbered dots with a marker;
// and a fixed stage showing the current step's card, the previous step shrunk above and the next one peeking below,
// cross-fading as the film moves on. When the film ends (or on "All steps") the whole storyboard opens underneath as
// a list. A case with no film shows the list alone. Used on the run page, in the feature page's walkthrough and rows,
// and in the reel.
//
// How it plays: the film holds at every step (3 seconds unless the person chose otherwise) while that step's dot fills
// up, so a pause always shows what it is for and how long it lasts; then the film plays on and the marker travels
// along the rail to the next dot during the real action. Fill a dot (reading time), travel a segment (action time),
// fill the next dot. A step that happened before the film (a data read) is shown in the film's own frame while it
// holds. With "Pause at each step" on, the film stops at each step until the person goes on.
//
// Everything is drawn from one clock, the time a viewer has watched (film.js: the film with its long waits cut out and
// the holds put in). The player moves that clock (from the film's own time while it plays, from the wall clock while
// it holds) and draws the rail, the fills, the stage, the scrub bar and the time from it. Dots and marker are placed as
// fractions of the rail, so a change of layout moves them together. The rail is as long as the film column, and the
// stage keeps every step's layer in one place, as tall as the tallest, so nothing changes size while the film plays.
// Drawing runs on animation frames when there are any, and on a timer and the film's own time updates when there are
// not (a page in a background pane gets no frames).
import { h, clear, playIcon, pauseIcon } from "./dom.js";
import { fileURL } from "./api.js";
import { clock, filmShape } from "./words.js";
import { timeline } from "./film.js";
import { storySteps, visual, scene, mark, okWord, loadStills, readsTerminal, captionsOf } from "./story.js";

const reduced = () => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
const ROOM = 28;            // pixels between two dots' centres, when the rail has room for it
const TICK = 40;            // milliseconds between draws when there are no animation frames
const FADE = 250;           // milliseconds a layer takes to fade out (style.css)
const HOLDS = [3, 1.5, 0];  // the hold choices, in the order the hold button cycles through them
const LEAST = 1.5;          // a step before the film holds at least this long: it has no film of its own
const READ = 3;             // with captions: seconds each read before the film gets in the terminal
const CHECK = 3;            // with captions: seconds the film stops when a check lands, to read what was found
const KEEP = { hold: "gt-sb-hold", stop: "gt-sb-stop" };   // remembered for the session
const HINT = "gt-sb-hint";  // the hint about shorter holds was seen (remembered in this browser)

const remembered = (key, fallback) => { try { const v = sessionStorage.getItem(key); return v === null ? fallback : v; } catch { return fallback; } };
const remember = (key, value) => { try { sessionStorage.setItem(key, String(value)); } catch { /* not kept */ } };
const hintSeen = () => { try { return localStorage.getItem(HINT) === "1"; } catch { return true; } };
const seeHint = () => { try { localStorage.setItem(HINT, "1"); } catch { /* not kept */ } };

// When each step happens in the film: null for a step before the film, and no step later than the film's last moment.
export function stepTimes(steps, end) {
  return steps.map((s) => (s.t === null ? null : end > 0 ? Math.min(s.t, Math.max(0, end - 0.05)) : s.t));
}

// Where the dots sit along the rail, from 0 to 1. `at` is when each step is reached on the travel clock (the watched
// clock without the holds), in order; `floor` is the least gap between two dots, as a share of the rail. Each gap is
// its share of the time, or the floor when that is less, so the marker keeps one pace everywhere but where steps come
// too close together for their dots.
export function dotPlaces(at, floor) {
  const n = at.length;
  if (n < 2) return n ? [0] : [];
  const even = 1 / (n - 1);
  const least = Math.min(even, Math.max(0, floor));
  const gaps = at.slice(1).map((x, i) => Math.max(0, x - at[i]));
  const total = gaps.reduce((a, b) => a + b, 0);
  let sizes = gaps.map(() => even);
  if (total > 0 && least < even) {
    const sum = (k) => gaps.reduce((a, g) => a + Math.max(least, k * g), 0);
    let lo = 0;
    let hi = 1 / total;
    for (let i = 0; i < 60; i += 1) { const mid = (lo + hi) / 2; if (sum(mid) < 1) lo = mid; else hi = mid; }
    sizes = gaps.map((g) => Math.max(least, hi * g));
  }
  const all = sizes.reduce((a, b) => a + b, 0);
  const out = [0];
  for (const s of sizes) out.push(Math.min(1, out[out.length - 1] + s / all));
  out[n - 1] = 1;
  return out;
}

const markOf = (s) => (s.kind === "compare" || s.kind === "situation" || s.kind === "stop" || s.kind === "result" ? (s.ok ? "ok" : "bad") : "");

// opts: { base, viewport, steps (else made from the case), label (the film's spoken name), autoplay, onEnd,
//         scope (names the controls when a page has two), compact, stepStop (false: never stop at steps, no toggle),
//         captions (true: the film alone, with one caption over it, when the case has the words for it),
//         standIn ({ intent, ok, result }: with captions, the words to caption a film with when nothing was said
//         aloud during it; story.js: captionsOf), autoplay (true: starts playing once it can) }
//
// With captions the film plays straight through with no rail and no stage. The reads before the film are typed one
// after another in a terminal window over the film's first frame (READ seconds each). Over the film one caption says
// what the verifier is about to check while it gets there; when the check lands the film stops for CHECK seconds on
// what was checked and the caption says what was found; then the next thing it is about to check. The words are the
// verifier's own from the run (story.js: captionsOf). The previous and next buttons go between reads and checks.
export function createStoryboard(c, opts = {}) {
  const steps = opts.steps || storySteps(c);
  const n = steps.length;
  const clips = c.clips || [];
  const hasFilm = clips.length > 0;
  const clip = clips.length - 1;
  const src = hasFilm ? fileURL(opts.base || "", clips[clip].file) : "";
  const vp = opts.viewport || { width: 900, height: 700 };
  const of = opts.scope ? ` of ${opts.scope}` : "";
  const ctx = { src, viewport: vp, folded: false };
  const caps = opts.captions && hasFilm ? captionsOf(c, opts.standIn) : null;
  const el = filmShape(h("section", { class: `sb${opts.compact ? " compact" : ""}${caps ? " captions" : ""}`, "aria-label": opts.label || "The film and its steps" }), vp);
  const listEl = h("ol", { class: "sb-list" });
  const listItems = steps.map((s, i) => {
    const jumpBtn = hasFilm ? h("button", { type: "button", class: "sb-at", "aria-label": `Play step ${s.n}${of} in the film` }, s.t === null ? "before" : clock(Math.max(0, s.t))) : null;
    const li = h("li", { class: `sbcard ${s.kind}`, "data-n": String(s.n) },
      h("div", { class: "sbhead" }, h("span", { class: `num ${markOf(s)}` }, String(s.n)),
        h("p", { class: "sbtext" }, s.ok !== undefined && s.kind !== "result" ? mark(s.ok) : null, s.ok !== undefined && s.kind !== "result" ? okWord(s.ok) : null, s.text),
        jumpBtn),
      h("div", { class: "sbbody" }));
    if (jumpBtn) jumpBtn.addEventListener("click", () => { api.seekStep(i, true); el.scrollIntoView({ block: "start", behavior: reduced() ? "auto" : "smooth" }); });
    return { li, s, built: false };
  });
  const fillList = () => {
    for (const x of listItems) {
      if (x.built) continue;
      const v = visual(x.s, { ...ctx, folded: false });
      if (v) x.li.querySelector(".sbbody").append(v);
      x.built = true;
    }
  };
  listItems.forEach((x) => listEl.append(x.li));

  if (!hasFilm) {
    fillList();
    el.classList.add("nofilm");
    el.append(h("p", { class: "note" }, "No film was recorded for this case. These are its steps, from the run's record."), listEl);
    return { el, video: null, steps, play() {}, pause() {}, stop() {}, get playing() { return false; }, seekStep() {} };
  }

  // ---------- the film ----------
  const video = h("video", { preload: "metadata", playsinline: true, muted: true, disablePictureInPicture: true });
  video.src = src;
  const gone = h("div", { class: "gone", hidden: true }, "The film of this case is not in the store, so it cannot be shown. Its steps are below.");
  // A step before the film is shown in the film's frame while it holds.
  const scenes = steps.map((s) => (!caps && s.t === null && s.kind === "data" ? scene(s) : null));
  const sceneBox = h("div", { class: "sb-scenes", "aria-live": "polite" }, scenes.filter(Boolean));
  // With captions: the reads' terminal window, and the one caption.
  const readAt = caps ? steps.map((s, i) => (s.t === null && s.kind === "data" ? i : -1)).filter((i) => i >= 0) : [];
  const term = readAt.length ? readsTerminal(readAt.map((i) => steps[i])) : null;
  const capBox = caps ? h("div", { class: "sb-cap", "aria-live": "polite" }) : null;
  let capWin = [];           // each check's caption on the watched clock: what was found shows from `from` to `to`
  let capKey = null;
  let termOn = null;         // whether the terminal is showing
  let termGone = 0;
  const filmBox = h("div", { class: "film" }, video, sceneBox, term && term.el, capBox, gone);
  const playBtn = h("button", { type: "button", class: "play", "aria-label": `Play the film${of}` }, playIcon());
  const prevBtn = h("button", { type: "button", class: "stepbtn prev", "aria-label": `Previous step${of}` }, h("span", { "aria-hidden": "true" }, "‹"));
  const nextBtn = h("button", { type: "button", class: "stepbtn next", "aria-label": `Next step${of}` }, h("span", { "aria-hidden": "true" }, "›"));
  const fill = h("div", { class: "fill" });
  const range = h("input", { type: "range", min: "0", max: "1000", step: "1", value: "0", "aria-label": `Film position${of}` });
  const track = h("div", { class: "track" }, h("div", { class: "rail" }), fill, range);
  const time = h("div", { class: "time", "aria-hidden": "true" }, "0:00 / 0:00");
  const holdBtn = h("button", { type: "button", class: "sb-opt sb-hold" });
  const stopBtn = h("button", { type: "button", class: "sb-opt sb-stop", "aria-pressed": "false" }, "Pause at each step");
  // The first time: where to make the holds shorter, said once beside the hold button.
  const hintBtn = h("button", { type: "button", class: "sb-opt", "aria-label": `Make each hold 1.5 seconds${of}` }, "Make it 1.5 s");
  const hint = h("span", { class: "sb-hint" }, "Each step holds 3 s while its dot fills. ", hintBtn);
  const allBtn = h("button", { type: "button", class: "toggle sb-all", "aria-expanded": "false" }, `All steps (${n})`);

  // ---------- the rail ----------
  const marker = h("span", { class: "sb-marker", "aria-hidden": "true" });
  const done = h("span", { class: "sb-done", "aria-hidden": "true" });
  const dots = steps.map((s, i) => {
    // The fill rises inside the dot while its step holds; a step that went wrong fills red.
    const d = h("button", { type: "button", class: `sb-dot ${markOf(s)}`, "aria-label": `Step ${s.n}${of}: ${s.text}` },
      h("span", { class: "sb-fill", "aria-hidden": "true" }), h("span", { class: "sb-num" }, String(s.n)));
    d.addEventListener("click", () => api.seekStep(i, false));
    return d;
  });
  const fills = dots.map((d) => d.firstChild);
  const rail = h("div", { class: "sb-rail", role: "group", "aria-label": `Steps${of}` }, h("span", { class: "sb-line", "aria-hidden": "true" }), done, marker, ...dots);

  // ---------- the stage ----------
  // Every step has its layer (the step before, its card, the step after) in the stage from the start, all in one
  // place; the current one is shown. A layer that stops being current fades out on top of the new one, which is
  // there at once (so a page that gets no frames still shows it). The stage is as tall as its tallest layer, so it
  // never changes size and nothing under it moves.
  const slot = (i, cls, word) => {
    const s = steps[i];
    if (!s) return h("div", { class: `sb-slot ${cls} empty`, "aria-hidden": "true" });
    const b = h("button", { type: "button", class: `sb-slot ${cls}`, "aria-label": `${word} step ${s.n}${of}: ${s.text}` },
      h("span", { class: `num ${markOf(s)}` }, String(s.n)), h("span", { class: "sb-slottext" }, s.text));
    b.addEventListener("click", () => api.seekStep(i, false));
    return b;
  };
  const layers = steps.map((s, i) => {
    const k = h("p", { class: "sb-k" });
    const card = h("div", { class: `sb-now ${s.kind}` }, k,
      h("p", { class: "sb-nowtext" }, s.ok !== undefined && s.kind !== "result" ? mark(s.ok) : null, s.ok !== undefined && s.kind !== "result" ? okWord(s.ok) : null, s.text),
      // A data read shows its command and real answer from the start; the box keeps one height and scrolls when the
      // answer is long (style.css).
      caps ? null : visual(s, { ...ctx, folded: false, lazy: true }));
    const layer = h("div", { class: "sb-layer", "data-n": String(s.n) }, slot(i - 1, "prev", "Previous"), card, slot(i + 1, "next", "Next"));
    return { layer, k, i };
  });
  const stage = h("div", { class: "sb-stage", "aria-live": "polite" }, layers.map((x) => x.layer));

  const showStop = opts.stepStop !== false && !caps;
  el.append(
    h("div", { class: "sb-top" },
      h("div", { class: "sb-media" },
        h("div", { class: "sb-filmcol" }, filmBox,
          h("div", { class: "ctrl" }, prevBtn, playBtn, nextBtn, track, time),
          h("div", { class: "sb-opts" }, hint, holdBtn, showStop ? stopBtn : null)),
        rail),
      stage),
    h("div", { class: "sb-foot" }, allBtn),
    listEl);
  listEl.hidden = true;
  el.tabIndex = 0;

  // ---------- the watched clock ----------
  let hold = HOLDS.includes(Number(remembered(KEEP.hold, "3"))) ? Number(remembered(KEEP.hold, "3")) : 3;
  let stepStop = showStop && remembered(KEEP.stop, "0") === "1";
  let tl;                    // the timeline with its parts and holds (film.js)
  let times = [];            // each step's film time (null: before the film)
  let frames = [];           // the film time each step holds on
  let places = [];           // each dot's place on the rail, 0 to 1
  let fitted = 0;            // the rail length the dots were placed for (0: not measured yet)
  let w = 0;                 // the watched clock, in seconds
  let pi = 0;                // the part of the timeline the clock is in
  let playing = false;       // the person wants it to play (the film itself is paused while a step holds)
  let waiting = false;       // stopped at a step until the person goes on ("Pause at each step")
  let ended = false;
  let fresh = true;          // nothing has been played or chosen yet
  let anchor = { w: 0, at: 0 };   // while holding: the clock and the wall time when the hold (re)started
  let ownSeek = null;        // the film time the player itself last asked for, until the film gets there
  let moved = false;         // someone else moved the film, and the clock has not taken it in yet
  let shown = -1;
  let raf = 0;
  let timer = 0;
  let said = "";
  let hintGone = false;      // the person used the hold choice here, so the hint has done its work

  const now = () => performance.now();
  const holdOf = (i) => tl.holds[i];
  // The step showing at watched time x: the last one whose hold has begun (the first before any has).
  const stepAt = (x) => { let cur = 0; tl.holds.forEach(([a], i) => { if (a <= x + 1e-6) cur = i; }); return cur; };
  // The part the clock is in at x: the first that ends after x (the last when x is the end).
  const partAt = (x) => { const k = tl.parts.findIndex((p) => p.to > x + 1e-9); return k < 0 ? tl.parts.length - 1 : k; };
  // The part holding step i (with captions a step has no hold of its own: then the part its moment is in).
  const holdPart = (i) => { const k = tl.parts.findIndex((p) => p.kind === "hold" && p.step === i); return k < 0 ? partAt(holdOf(i)[0]) : k; };
  // The watched clock at film time t (the start of the next part of film when t was cut out).
  const watchedAt = (t) => {
    let last = 0;
    for (const q of tl.parts) {
      if (q.kind !== "play") continue;
      if (t < q.film0) return q.from;
      if (t <= q.film1) return q.from + (t - q.film0);
      last = q.to;
    }
    return last;
  };

  function seekFilm(t) {
    if (Math.abs((video.currentTime || 0) - t) < 0.02) return;
    ownSeek = t;
    video.currentTime = t;
  }

  // The film time each step holds on. A browser step holds on the page as it came to be a moment after its action
  // (the frame its card's still shows), never on a page still loading: the film plays through the action to there.
  // A moment inside a cut wait moves to the end of the cut, and no step holds later than the next one or the end.
  function holdFrames(base) {
    const out = times.map((t, i) => {
      if (t === null) return null;
      const s = steps[i];
      let f = s.kind === "browser" && typeof s.settle === "number" ? Math.max(t, s.settle) : t;
      for (const [a, b] of base.cuts) if (f >= a && f < b) f = b;
      return base.end > 0 ? Math.min(f, Math.max(t, base.end - 0.05)) : f;
    });
    for (let i = n - 2; i >= 0; i -= 1) if (out[i] !== null && out[i + 1] !== null && out[i] > out[i + 1]) out[i] = Math.max(times[i], out[i + 1]);
    return out;
  }

  // Place the dots for a rail `len` pixels long (0 when not known: then they keep three quarters of an even gap).
  // A dot's place follows the travel clock (the watched clock without the holds), so the marker travels at one pace.
  function place(len) {
    fitted = len;
    const travel = [];
    let sum = 0;
    tl.holds.forEach(([a], i) => { if (i) sum += a - tl.holds[i - 1][1]; travel.push(sum); });
    places = dotPlaces(travel, len > 0 ? ROOM / len : 0.75 / Math.max(1, n - 1));
    dots.forEach((d, i) => d.style.setProperty("--i", places[i].toFixed(5)));
    // Dots shrink to fit when the gaps are narrow; then only the current one is big enough to show its number.
    let gap = Infinity;
    for (let i = 1; i < n; i += 1) gap = Math.min(gap, places[i] - places[i - 1]);
    const px = len > 0 && n > 1 ? gap * len : n > 14 ? 12 : 24;
    const size = Math.max(6, Math.min(24, px - 4));
    rail.classList.toggle("small", size < 18);
    rail.style.setProperty("--dot", `${size.toFixed(1)}px`);
    rail.style.setProperty("--dotk", (size / 20).toFixed(3));
  }

  // Build the clock again (the film's length became known, or the hold changed). The clock keeps its place by step
  // and film time: the same step with its hold as far along, or the same moment of film.
  function retime() {
    const was = tl ? { step: stepAt(w), inHold: tl.parts[pi] && tl.parts[pi].kind !== "play", frac: 0, film: video.currentTime || 0 } : null;
    if (was && was.inHold) { const [a, b] = holdOf(was.step); was.frac = b > a ? Math.min(1, Math.max(0, (w - a) / (b - a))) : 0; }
    const dur = isFinite(video.duration) ? video.duration : 0;
    const base = timeline(c, clip, dur);
    times = stepTimes(steps, base.end);
    frames = holdFrames(base);
    tl = caps
      ? timeline(c, clip, dur, { steps: frames, hold: 0, least: READ, travel: 0, join: true, pauses: caps.checks.map((k) => ({ at: k.show, len: CHECK })) })
      : timeline(c, clip, dur, { steps: frames, hold: stepStop ? 0 : hold, least: stepStop ? 0 : LEAST });
    if (caps) {
      capWin = caps.checks.map((k, j) => ({ from: Math.min(watchedAt(k.t), tl.pauses[j].from), to: tl.pauses[j].to }));
      capKey = null;
    }
    for (const x of layers) x.k.textContent = `Step ${steps[x.i].n} of ${n}${steps[x.i].t === null ? " · before the film" : ` · ${clock(Math.max(0, times[x.i]))}`}`;
    // Each dot says when its step happens in the film, the frame it holds on, and its hold on the watched clock (read
    // by the page tests).
    dots.forEach((d, i) => {
      d.dataset.at = times[i] === null ? "before" : times[i].toFixed(3);
      d.dataset.frame = frames[i] === null ? "before" : frames[i].toFixed(3);
      d.dataset.hold = `${tl.holds[i][0].toFixed(3)},${tl.holds[i][1].toFixed(3)}`;
    });
    el.dataset.length = tl.length.toFixed(3);
    el.dataset.film = tl.film.toFixed(3);
    place(fitted);
    if (!was) return;
    if (ended) { w = tl.length; pi = tl.parts.length - 1; return; }
    if (fresh) { w = 0; pi = 0; return; }
    if (was.inHold) {
      const [a, b] = holdOf(was.step);
      moveTo(a + was.frac * (b - a), holdPart(was.step));
    } else {
      // The same moment of film, in the part of the same step (the film before the first hold belongs to step 1).
      const k = tl.parts.findIndex((p) => p.kind === "play" && p.film0 <= was.film + 1e-6 && was.film <= p.film1 + 1e-6 && stepAt(p.from + 1e-4) === was.step);
      if (k >= 0) moveTo(tl.parts[k].from + (was.film - tl.parts[k].film0), k);
      else moveTo(holdOf(was.step)[1], partAt(holdOf(was.step)[1]));
    }
  }

  // Put the clock at x, in part k, with the film on the right frame; carry on if playing.
  function moveTo(x, k) {
    w = Math.max(0, Math.min(tl.length, x));
    pi = Math.max(0, Math.min(k, tl.parts.length - 1));
    const p = tl.parts[pi];
    if (!p) return;
    if (p.kind === "play") seekFilm(p.film0 + Math.max(0, Math.min(w - p.from, p.film1 - p.film0)));
    else seekFilm(p.film);
    if (playing && !waiting) start();
  }

  // Start the part the clock is in (the person wants it to play).
  function start() {
    const p = tl.parts[pi];
    if (!p) { finish(); return; }
    anchor = { w, at: now() };
    if (p.kind === "play") { seekFilm(p.film0 + Math.max(0, w - p.from)); video.play().catch(() => {}); }
    else { video.pause(); seekFilm(p.film); }
    wake();
  }

  // Into the next part: a hold begins (and with "Pause at each step" on, waits there), or the film plays on.
  function next() {
    for (;;) {
      pi += 1;
      const p = tl.parts[pi];
      if (!p) { finish(); return; }
      w = p.from;
      if (p.kind === "hold" && stepStop) {
        waiting = true;
        video.pause();
        seekFilm(p.film);
        setPlaying();
        return;
      }
      if (p.kind !== "play" && p.to - p.from <= 1e-6) continue;      // a hold of no length: straight on
      if (p.kind === "play") seekFilm(p.film0);
      else { video.pause(); seekFilm(p.film); }
      start();
      return;
    }
  }

  // The film was moved by someone else: the clock goes to that moment of film (in the latest part that shows it).
  function adopt(t) {
    moved = false;
    fresh = false;
    ended = false;
    waiting = false;
    let k = -1;
    tl.parts.forEach((q, j) => { if (q.kind === "play" && q.film0 - 1e-6 <= t && t <= q.film1 + 1e-6) k = j; });
    if (k < 0) k = tl.parts.findIndex((q) => q.kind === "play" && q.film0 >= t);
    if (k < 0) return;
    w = tl.parts[k].from + Math.max(0, t - tl.parts[k].film0);
    pi = k;
    if (playing) start();
  }

  // One step of the clock while playing: from the film's time while it plays, from the wall clock while it holds.
  function advance() {
    if (!playing || waiting) return false;
    const p = tl.parts[pi];
    if (!p) { finish(); return false; }
    if (p.kind === "play") {
      if (video.seeking) return true;          // the film is getting to a moment: wait for it to be there
      const t = video.currentTime || 0;
      // Someone else moved the film (its "seeked" may not have come yet): the clock goes there. A film that simply got
      // past a step's moment (a late look) still holds there below.
      if (moved) { adopt(t); render(); return playing && !waiting; }
      w = Math.min(p.to, p.from + Math.max(0, t - p.film0));
      if (t >= p.film1 - 0.01 || video.ended) {
        // Reached a step's moment (or a cut): hold exactly there.
        if (Math.abs(t - p.film1) > 0.03) { video.pause(); seekFilm(p.film1); }
        next();
      }
    } else {
      w = Math.min(p.to, anchor.w + (now() - anchor.at) / 1000);
      if (w >= p.to - 1e-6) next();
    }
    render();
    return playing && !waiting;
  }

  function finish() {
    idle();
    playing = false;
    waiting = false;
    ended = true;
    video.pause();
    seekFilm(tl.end);
    w = tl.length;
    pi = tl.parts.length - 1;
    setPlaying();
    render();
    if (!caps) openList(true, true);
    seeHint();                 // seen once is enough (it stays here until the page is left, so nothing moves now)
    if (opts.onEnd) opts.onEnd();
  }

  // ---------- drawing ----------
  // The rail's length, read now (no waiting for a frame); the dots are placed again if it changed.
  function fit() {
    const wd = rail.offsetWidth;
    const hg = rail.offsetHeight;
    const len = wd > hg ? wd : hg;
    if (len > 0 && Math.abs(len - fitted) >= 2) place(len);
  }

  function show(i) {
    if (i === shown) return;
    if (shown >= 0) {
      // The old layer fades out (style.css); this timer makes sure it is gone even where the fade never runs (a page
      // that gets no frames), so it can never stay on top of the new one.
      const old = layers[shown];
      old.layer.classList.remove("on");
      clearTimeout(old.gone);
      old.gone = setTimeout(() => old.layer.classList.add("gone"), FADE + 60);
    }
    shown = i;
    clearTimeout(layers[i].gone);
    layers[i].layer.classList.remove("gone");
    layers[i].layer.classList.add("on");
    if (!caps) {
      loadStills(layers[i].layer);
      if (layers[i + 1]) loadStills(layers[i + 1].layer);     // ready before it is needed
    }
    dots.forEach((d, k) => { d.classList.toggle("now", k === i); d.classList.toggle("passed", k < i); d.setAttribute("aria-current", k === i ? "step" : "false"); });
  }

  function render() {
    if (el.isConnected) fit();
    const cur = ended ? n - 1 : stepAt(w);
    const [a, b] = holdOf(cur);
    const p = tl.parts[pi] || {};
    const holding = !ended && p.kind === "hold" && w < b - 1e-6 && !waiting;
    // How full each dot is: a dot fills while its step holds and stays full after. Asked for less motion, it shows a
    // still "holding" state for the hold and is full after.
    const still = reduced();
    fills.forEach((f, i) => {
      let v = i < cur ? 1 : i > cur ? 0 : ended ? 1 : waiting ? 0 : b > a ? Math.min(1, Math.max(0, (w - a) / (b - a))) : w >= a - 1e-6 ? 1 : 0;
      if (still && i === cur && holding) v = 0;
      f.style.transform = `scaleY(${v.toFixed(4)})`;
      dots[i].classList.toggle("lit", v >= 0.55);
    });
    dots.forEach((d, i) => {
      d.classList.toggle("waiting", i === cur && waiting);
      d.classList.toggle("holding", i === cur && holding && still);
    });
    // The marker rests on the dot while its step holds, and travels to the next dot while the film acts.
    let y = places[cur];
    if (!ended && w > b + 1e-6 && cur + 1 < n) {
      const nb = holdOf(cur + 1)[0];
      y = places[cur] + (places[cur + 1] - places[cur]) * Math.min(1, (w - b) / Math.max(1e-6, nb - b));
    }
    const v = y.toFixed(5);
    marker.style.setProperty("--p", v);
    done.style.setProperty("--p", v);
    // The scene of a step before the film covers the film while it holds (and before the film starts).
    const sceneOn = steps[cur].t === null && p.kind !== "play" && !ended;
    scenes.forEach((s, i) => { if (s) s.classList.toggle("on", sceneOn && i === cur); });
    if (caps) {
      // The terminal covers the film while a read has its turn, and drops away when the film starts.
      if (term) {
        if (sceneOn !== termOn) {
          // It fades out (style.css); the timer takes it away even where the fade never runs (a page with no frames).
          termOn = sceneOn;
          clearTimeout(termGone);
          term.el.classList.remove("gone");
          term.el.classList.toggle("on", sceneOn);
          if (!sceneOn) termGone = setTimeout(() => term.el.classList.add("gone"), 520);
        }
        if (sceneOn) term.draw(readAt.indexOf(cur), w - a, b - a, still);
      }
      const cap = sceneOn ? { say: steps[cur].text } : captionNow();
      const key = cap ? `${cap.mark ? (cap.ok ? "ok" : "bad") : ""}|${cap.say}` : "";
      if (key !== capKey) {
        capKey = key;
        clear(capBox);
        if (cap && cap.say) capBox.append(h("p", null, cap.mark ? h("span", { class: `sb-capmark ${cap.ok ? "ok" : "bad"}`, "aria-hidden": "true" }, cap.ok ? "✓ " : "✗ ") : null,
          cap.mark ? h("span", { class: "sr" }, cap.ok ? "Matches. " : "Does not match. ") : null, cap.say));
        el.dataset.caption = cap && cap.say ? cap.say : "";
        el.dataset.captionMark = cap && cap.mark ? (cap.ok ? "ok" : "bad") : "";
      }
    }
    const u = tl.length ? Math.min(1, w / tl.length) : 0;
    fill.style.width = `${(u * 100).toFixed(3)}%`;
    if (document.activeElement !== range) range.value = String(Math.round(u * 1000));
    const words = `${clock(w)} / ${clock(tl.length)}`;
    if (words !== said) {
      said = words;
      time.textContent = words;
      range.setAttribute("aria-valuetext", `${clock(w)} of ${clock(tl.length)}`);
    }
    show(cur);
    el.dataset.step = String(cur + 1);
    el.dataset.watched = w.toFixed(3);
    el.dataset.hold = waiting ? "waiting" : holding ? "holding" : "";
  }

  // The caption at the clock, over the film: what is about to be checked; from the check until the film goes on, what
  // was found; after the last check, how the checks came out.
  function captionNow() {
    for (let j = 0; j < capWin.length; j += 1) {
      const k = caps.checks[j];
      if (w < capWin[j].from - 1e-6) return k.intent ? { say: k.intent } : null;
      if (w < capWin[j].to - 1e-6) return { say: k.verdict, ok: k.ok, mark: true };
    }
    // A film with no check on it says what is being tried until its end (played to, or scrubbed to).
    if (!capWin.length && !ended && w < tl.length - 0.05) return caps.lead ? { say: caps.lead } : null;
    return caps.end.text ? { say: caps.end.text, ok: caps.end.ok, mark: true } : null;
  }

  // With captions, previous and next go between the reads and the checks.
  function jump(d) {
    const at = [...readAt.map((i) => holdOf(i)[0]), ...capWin.map((z) => z.from)];
    const x = d > 0 ? at.find((v) => v > w + 0.05) : [...at].reverse().find((v) => v < w - 0.3);
    if (x === undefined && d > 0) return;
    fresh = false;
    ended = false;
    waiting = false;
    moveTo(x === undefined ? 0 : x, partAt(x === undefined ? 0 : x));
    render();
  }

  function setPlaying() {
    const on = playing && !waiting;
    clear(playBtn).append(on ? pauseIcon() : playIcon());
    playBtn.setAttribute("aria-label", on ? `Pause the film${of}` : `Play the film${of}`);
    el.dataset.playing = String(on);
  }

  function setOptions() {
    hint.hidden = hintSeen() || hold !== 3 || stepStop || hintGone;
    holdBtn.textContent = hold ? `Hold ${hold} s` : "No hold";
    holdBtn.setAttribute("aria-label", `Hold at each step${of}: ${hold ? `${hold} seconds` : "none"}. Change it`);
    holdBtn.disabled = stepStop;
    stopBtn.setAttribute("aria-pressed", String(stepStop));
    stopBtn.setAttribute("aria-label", `Pause at each step${of}`);
  }

  function openList(on, arriving) {
    if (on) fillList();
    const was = !listEl.hidden;
    listEl.hidden = !on;
    listEl.classList.toggle("arrive", on && !was && !!arriving);
    allBtn.setAttribute("aria-expanded", String(on));
    allBtn.textContent = on ? "Hide the steps" : `All steps (${n})`;
  }

  function idle() {
    if (raf) cancelAnimationFrame(raf);
    if (timer) clearTimeout(timer);
    raf = 0;
    timer = 0;
  }

  // Draws on the next animation frame, or after TICK milliseconds if no frame comes first.
  function tick() {
    idle();
    if (advance()) wake();
  }
  function wake() {
    if (!raf) raf = requestAnimationFrame(tick) || 0;
    if (!timer) timer = setTimeout(tick, TICK);
  }

  const onResize = () => { if (el.isConnected) render(); };
  window.addEventListener("resize", onResize);

  video.addEventListener("loadedmetadata", () => {
    retime();
    // Before play: the film area shows the first step: its scene when it came before the film, else its own frame.
    if (fresh && frames[0] !== null && !video.seeking && (video.currentTime || 0) < 0.01) seekFilm(frames[0]);
    render();
    if (opts.autoplay) api.play();
  });
  video.addEventListener("ended", () => { if (!ended && playing) finish(); });
  // A seek the player did not ask for (someone set the film's time): the clock goes to that moment of film.
  video.addEventListener("seeking", () => {
    if (!(ownSeek !== null && Math.abs((video.currentTime || 0) - ownSeek) < 0.01)) moved = true;
  });
  video.addEventListener("seeked", () => {
    const t = video.currentTime || 0;
    // (the film may have played on a little by the time it says it got there)
    const mine = !moved && ownSeek !== null && t >= ownSeek - 0.01 && t <= ownSeek + 0.15;
    ownSeek = null;
    if (mine) { render(); return; }
    if (!tl) return;
    adopt(t);
    render();
  });
  // The film started by someone else: the player plays from where its clock is.
  video.addEventListener("play", () => {
    if (playing || !tl) return;
    playing = true;
    waiting = false;
    fresh = false;
    setPlaying();
    if (!video.seeking) start();       // else it starts once the seek it is waiting for is taken in (below)
  });
  video.addEventListener("timeupdate", () => { if (!playing || waiting) render(); else advance(); });
  video.addEventListener("error", () => {
    video.hidden = true;
    sceneBox.hidden = true;
    gone.hidden = false;
    playBtn.disabled = true;
    prevBtn.disabled = true;
    nextBtn.disabled = true;
    range.disabled = true;
    openList(true);
  });
  video.addEventListener("click", () => playBtn.click());
  video.style.cursor = "pointer";
  sceneBox.addEventListener("click", () => playBtn.click());
  if (term) term.el.addEventListener("click", () => playBtn.click());
  playBtn.addEventListener("click", () => { if (playing && !waiting) api.pause(); else api.play(); });
  prevBtn.addEventListener("click", () => (caps ? jump(-1) : api.seekStep(Math.max(0, stepAt(w) - (ended ? 0 : 1)), false)));
  // Next: the start of the next step's hold; at the last step, on to the end.
  nextBtn.addEventListener("click", () => {
    if (ended) return;
    if (caps) { jump(1); return; }
    const cur = stepAt(w);
    if (cur + 1 < n) api.seekStep(cur + 1, false);
    else if (waiting) api.play();
  });
  range.addEventListener("input", () => {
    fresh = false;
    ended = false;
    waiting = false;
    const x = (Number(range.value) / 1000) * tl.length;
    moveTo(x, partAt(x));
    render();
  });
  hintBtn.addEventListener("click", () => {
    seeHint();
    hintGone = true;
    hold = 1.5;
    remember(KEEP.hold, hold);
    setOptions();
    retime();
    render();
    holdBtn.focus();
  });
  holdBtn.addEventListener("click", () => {
    seeHint();
    hintGone = true;
    hold = HOLDS[(HOLDS.indexOf(hold) + 1) % HOLDS.length];
    remember(KEEP.hold, hold);
    setOptions();
    retime();
    render();
  });
  stopBtn.addEventListener("click", () => {
    stepStop = !stepStop;
    remember(KEEP.stop, stepStop ? "1" : "0");
    if (!stepStop && waiting) { waiting = false; }
    setOptions();
    retime();
    setPlaying();
    if (playing && !waiting) start();
    render();
  });
  allBtn.addEventListener("click", () => openList(listEl.hidden));
  el.addEventListener("keydown", (e) => {
    // (a key the page already used, such as the reel's arrows and space, is not used again here)
    if (e.defaultPrevented || e.target === range || e.altKey || e.ctrlKey || e.metaKey) return;
    if (e.key === "ArrowRight") { e.preventDefault(); nextBtn.click(); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); prevBtn.click(); }
    else if (e.key === " " && (e.target === el || e.target === video)) { e.preventDefault(); playBtn.click(); }
  });

  const api = {
    el,
    video,
    steps,
    get playing() { return playing && !waiting; },
    get current() { return shown; },
    play() {
      fresh = false;
      if (ended) { ended = false; playing = true; waiting = false; moveTo(0, 0); setPlaying(); render(); return; }
      if (waiting) {
        // Go on from the step it stopped at.
        waiting = false;
        playing = true;
        setPlaying();
        next();
        render();
        return;
      }
      playing = true;
      setPlaying();
      start();
      render();
    },
    pause() {
      if (!playing) return;
      if (!waiting) advance();          // the clock as it is now, then hold it there
      playing = false;
      idle();
      video.pause();
      setPlaying();
      render();
    },
    // Move to the start of step i's hold. Played on when `play` is true or when the film was already playing; with
    // "Pause at each step" on, it waits there.
    seekStep(i, play) {
      if (!steps[i]) return;
      fresh = false;
      ended = false;
      const k = holdPart(i);
      if (play) playing = true;
      waiting = stepStop && (playing || play);
      if (waiting) { idle(); video.pause(); }
      moveTo(holdOf(i)[0], k);
      if (waiting) seekFilm(tl.parts[k].film);
      setPlaying();
      render();
    },
    stop() { idle(); clearTimeout(termGone); for (const x of layers) clearTimeout(x.gone); window.removeEventListener("resize", onResize); video.pause(); video.removeAttribute("src"); video.load(); },
  };
  retime();
  setOptions();
  setPlaying();
  render();
  return api;
}
