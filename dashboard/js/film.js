// Timing of a case's film: where it is treated as ending, which waits are skipped, and the still a tile shows. The
// player itself is the storyboard (storyboard.js).

export const isAction = (s) => s.kind === "action" || s.kind === "request";

// Films run on after the last thing happens, and stall while the verifier thinks. A film is treated as ending
// TAIL seconds after its last timed step, and a wait longer than GAP between two timed steps (or before the first)
// is cut, from the step before it to LEAD seconds before the step after it. A film with no timed steps plays whole.
export const TAIL = 1.5;
export const GAP = 2.5;
export const LEAD = 0.7;

// The moments, in seconds into film `clip`, at which a step of the case happened, in order.
export function filmMoments(c, clip) {
  return (c.steps || []).filter((s) => s.clip === clip && typeof s.at === "number" && isFinite(s.at) && s.at >= 0)
    .map((s) => s.at).sort((a, b) => a - b);
}

// Where film `clip` is treated as ending. `duration` is the film's own length when known (else 0).
export function filmEnd(c, clip, duration) {
  const m = filmMoments(c, clip);
  const whole = isFinite(duration) && duration > 0 ? duration : 0;
  if (!m.length) return whole;
  const end = m[m.length - 1] + TAIL;
  return whole ? Math.min(whole, end) : end;
}

// The film as it is watched: its end, the waits cut out of it (fixed spans [from, to) of film time), and the clock
// a viewer sees. played(t) is how much of the film has been watched by film time t: it stands still inside a cut,
// so a late look at the clock never makes anything jump. filmAt(p) is the film time p seconds into the watching.
// `film` (and `length`) is the trimmed film's watched length. With opts.steps, the clock also holds at each step
// (holdsOf below): then `length` is the whole watched length, holds included, and `parts` and `holds` say where.
export function timeline(c, clip, duration, opts = {}) {
  const end = filmEnd(c, clip, duration);
  const m = filmMoments(c, clip);
  const cuts = [];
  const add = (a, b) => { b = Math.min(b, end); if (b - a > 0.05 && a < end) cuts.push([a, b]); };
  if (m.length && m[0] > GAP) add(0, m[0] - LEAD);
  for (let i = 0; i + 1 < m.length; i += 1) if (m[i + 1] - m[i] > GAP) add(m[i], m[i + 1] - LEAD);
  const played = (t) => {
    let p = Math.max(0, Math.min(t, end || t));
    for (const [a, b] of cuts) if (t > a) p -= Math.min(t, b) - a;
    return Math.max(0, p);
  };
  const length = end ? played(end) : 0;
  // The parts that are watched, each with how much was watched before it.
  const kept = [];
  let from = 0;
  let before = 0;
  for (const [a, b] of cuts) { kept.push({ from, before }); before += a - from; from = b; }
  kept.push({ from, before });
  const filmAt = (p) => {
    const q = Math.max(0, Math.min(p, length));
    // At a seam between two parts, the later part: the wait in between is not watched.
    for (let i = kept.length - 1; i >= 0; i -= 1) if (q >= kept[i].before - 1e-9) return Math.min(end, kept[i].from + (q - kept[i].before));
    return 0;
  };
  // Where playback at film time t should jump to, or null to keep playing.
  const skip = (t) => {
    for (const [a, b] of cuts) if (t >= a && t < b - 0.01) return b;
    return null;
  };
  const out = { end, cuts, length, film: length, played, filmAt, skip };
  if (opts.steps) Object.assign(out, holdsOf(opts.steps, cuts, end, opts));
  return out;
}

// The film as it is watched with a hold at each step: a list of parts, each a span [from, to) of the watched clock.
// "play" shows film time film0 to film1 (the cut waits left out); "hold" stops on one frame (`film`) while step
// `step` is read; "travel" stops on one frame for a moment while the marker goes from one dot to the next, used only
// where two steps have less than TRAVEL seconds of film between them (reads before the film, steps at one moment or
// close together), so the marker never jumps.
// `steps` is each step's film time, or null for a step before the film; `hold` is the hold in seconds; a step before
// the film holds at least `least` seconds (it has no film of its own, so with no hold it would not be seen at all).
// opts.travel changes the least travel (0: none). opts.pauses is a list of { at, len }: moments of film where the
// picture stops for `len` seconds with no step of its own (the captioned film stops when a check lands); they come
// back as `pauses`, each { at, from, to } on the watched clock, in the order given. opts.join leaves out holds of no
// length and joins the film on either side of them, so the film is only ever split at a cut, a hold or a pause.
export const HOLD = 3;
export const TRAVEL = 0.4;
function holdsOf(steps, cuts, end, opts) {
  const hold = Math.max(0, opts.hold ?? HOLD);
  const least = Math.max(0, opts.least ?? 0);
  const travel = Math.max(0, opts.travel ?? TRAVEL);
  const asked = (opts.pauses || []).map((q, k) => ({ k, at: Math.max(0, end > 0 ? Math.min(q.at, end) : q.at), len: Math.max(0, q.len) }))
    .sort((x, y) => x.at - y.at || x.k - y.k);
  const pauses = asked.map(() => null);
  let pz = 0;
  const parts = [];
  const holds = [];
  let w = 0;
  let f = cuts.length && cuts[0][0] <= 0 ? cuts[0][1] : 0;          // the film's first watched moment
  const play = (a, b) => { parts.push({ kind: "play", from: w, to: w + (b - a), film0: a, film1: b }); w += b - a; };
  const playTo = (b) => {
    let pos = f;
    for (const [a, c] of cuts) {
      if (c <= pos) continue;
      if (a >= b) break;
      if (a > pos) play(pos, a);
      pos = Math.max(pos, c);
    }
    if (b > pos) play(pos, b);
    f = Math.max(f, b);
  };
  // The pauses up to film time `limit`, each after the film has played to its moment.
  const pauseTo = (limit) => {
    for (; pz < asked.length && asked[pz].at <= limit; pz += 1) {
      const q = asked[pz];
      if (q.at > f) playTo(q.at);
      pauses[q.k] = { at: q.at, from: w, to: w + q.len };
      if (q.len > 0) parts.push({ kind: "pause", from: w, to: w + q.len, film: f });
      w += q.len;
    }
  };
  steps.forEach((t, i) => {
    if (t !== null) pauseTo(Math.min(t, end || t));
    const before = w;
    if (t !== null && t > f) playTo(Math.min(t, end || t));
    const moved = w - before;
    if (i > 0 && moved < travel - 1e-9) { parts.push({ kind: "travel", from: w, to: w + travel - moved, film: f }); w += travel - moved; }
    const len = t === null ? Math.max(hold, least) : hold;
    holds.push([w, w + len]);
    parts.push({ kind: "hold", step: i, from: w, to: w + len, film: f });
    w += len;
  });
  pauseTo(Infinity);
  if (end > f) playTo(end);
  if (!opts.join) return { parts, holds, pauses, length: w };
  const joined = [];
  for (const p of parts) {
    if (p.kind !== "play" && p.to - p.from <= 1e-9) continue;
    const q = joined[joined.length - 1];
    if (p.kind === "play" && q && q.kind === "play" && Math.abs(q.film1 - p.film0) < 1e-9 && Math.abs(q.to - p.from) < 1e-9) { q.to = p.to; q.film1 = p.film1; continue; }
    joined.push(p);
  }
  return { parts: joined, holds, pauses, length: w };
}

// Where playback at time t should jump to, or null to keep playing.
export function skipTo(c, clip, t) {
  return timeline(c, clip, 0).skip(t);
}

// Seconds of film the steps reach; a stand-in for the length until the film's own length is known.
export function estimateLength(c, clip) {
  return filmEnd(c, clip, 0);
}

// A step can be stamped later than the film's real end (the recorder stops when the picture stops changing).
// Such a step is treated as happening at the film's last moment. dur is the real length, 0 when not known yet.
export function effectiveAt(at, dur) {
  return isFinite(dur) && dur > 0 ? Math.min(at, Math.max(0, dur - 0.05)) : at;
}

// The moment a still of the film should show (tile thumbnails): the screenshot the case cited, else its last
// reading, else its last action. The first frames are usually a page still loading.
export function posterTime(c) {
  const clip = (c.clips || []).length - 1;
  const shot = (c.shots || []).filter((s) => s.cited && typeof s.at === "number").pop();
  if (shot) return shot.at;
  const timed = (c.steps || []).filter((s) => s.clip === clip && typeof s.at === "number");
  const reading = timed.filter((s) => s.kind === "reading").pop();
  if (reading) return reading.at;
  const action = timed.filter(isAction).pop();
  return action ? action.at : 0;
}
