// What a person thinks of a claim's result, or of one variation's: "Not what I meant", "This result is wrong" or
// "This is right", with an optional one-line note. Kept in the results store with the run the result came from
// (store/CONTRACT.md, feedback routes). One choice per claim or variation: choosing another replaces it, choosing the
// same again removes it. The controls are quiet: they must not compete with the result.
import { h, clear } from "./dom.js";

export const KINDS = [["not_what_i_meant", "Not what I meant"], ["wrong_result", "This result is wrong"], ["right", "This is right"]];
const SAID = { not_what_i_meant: "You said it is not what you meant.", wrong_result: "You said this result is wrong.", right: "You said this is right." };

async function answer(res) {
  if (res.ok) return res.status === 204 ? null : res.json();
  let why = `the results store answered ${res.status}`;
  try {
    const body = await res.json();
    if (body && typeof body.error === "string") why = body.error.replace(/\.$/, "");
  } catch { /* not JSON */ }
  throw new Error(why);
}

async function send(url, init) {
  let res;
  try {
    res = await fetch(url, { headers: { Accept: "application/json", "Content-Type": "application/json" }, ...init });
  } catch {
    throw new Error("the results store could not be reached");
  }
  return answer(res);
}

// A page's feedback, by run: read once per run, changed through the store, and told to whoever listens.
export function feedbackStore() {
  const runs = new Map();          // run id -> { entries, ready }
  const listeners = new Set();
  const url = (runId) => `/v1/runs/${encodeURIComponent(runId)}/feedback`;
  const of = (runId) => {
    if (!runs.has(runId)) runs.set(runId, { entries: [], ready: null });
    return runs.get(runId);
  };
  const tell = (runId) => listeners.forEach((f) => f(runId));
  const mine = (runId, prop, caseId) => of(runId).entries.filter((e) => e.property === prop && (e.case || null) === (caseId || null));
  return {
    load(runId) {
      const r = of(runId);
      if (!r.ready) {
        r.ready = send(url(runId), { method: "GET" })
          .then((body) => { r.entries = body && Array.isArray(body.feedback) ? body.feedback : []; })
          .catch(() => { /* none shown; choosing still works */ })
          .then(() => tell(runId));
      }
      return r.ready;
    },
    // The choice in force for a claim (caseId null) or a variation: the newest entry.
    current(runId, prop, caseId) {
      const all = mine(runId, prop, caseId);
      return all.length ? all[all.length - 1] : null;
    },
    // Make `kind` (with `note`) the choice, or remove the choice when kind is null. The new entry is saved before the
    // old ones are removed, so a failure part way never leaves nothing where there was something.
    async set(runId, prop, caseId, kind, note) {
      const r = of(runId);
      const old = mine(runId, prop, caseId);
      try {
        if (kind) {
          const body = { property: prop, kind };
          if (caseId) body.case = caseId;
          if (note) body.note = note;
          r.entries.push(await send(url(runId), { method: "POST", body: JSON.stringify(body) }));
        }
        for (const e of old) {
          await send(`${url(runId)}/${encodeURIComponent(e.id)}`, { method: "DELETE" });
          r.entries = r.entries.filter((x) => x !== e);
        }
      } finally {
        tell(runId);
      }
    },
    listen(f) { listeners.add(f); return () => listeners.delete(f); },
  };
}

// One sentence for what a person chose, for a closed row ("" when nothing).
export const saidWords = (entry) => (entry ? SAID[entry.kind] || "" : "");

// The three controls for a claim (caseId null) or a variation, and the note. `name` says which, for a screen reader
// ("the claim “…”", "the variation “…”").
export function feedbackControls(store, { runId, prop, caseId = null, name }) {
  const buttons = KINDS.map(([kind, word]) => {
    const b = h("button", { type: "button", class: `fb-k ${kind}`, "aria-pressed": "false", "aria-label": `${word}: ${name}` }, word);
    b.addEventListener("click", () => choose(kind));
    return b;
  });
  const note = h("input", { type: "text", class: "fb-note", maxlength: "1000", placeholder: "Add a note (optional)", "aria-label": `A note on ${name}` });
  const said = h("p", { class: "fb-said", role: "status" });
  const box = h("div", { class: "fb", role: "group", "aria-label": `What you think of ${name}` }, buttons, note, said);
  let busy = false;
  let typed = false;              // the note has words not saved yet
  let again = false;              // the note changed while a save was on its way: save it once that is done

  function sync() {
    const cur = store.current(runId, prop, caseId);
    buttons.forEach((b, i) => b.setAttribute("aria-pressed", String(!!cur && cur.kind === KINDS[i][0])));
    note.hidden = !cur;
    if (!typed && document.activeElement !== note) note.value = cur ? cur.note || "" : "";
    box.dataset.chosen = cur ? cur.kind : "";
  }
  const fail = (what, e) => { clear(said).append(`${what}: ${e.message}.`); said.dataset.tone = "bad"; };
  const fine = () => { clear(said); delete said.dataset.tone; };

  async function choose(kind) {
    if (busy) return;
    const cur = store.current(runId, prop, caseId);
    const next = cur && cur.kind === kind ? null : kind;
    busy = true;
    // Shown at once; put back as it was if the store does not take it.
    buttons.forEach((b, i) => b.setAttribute("aria-pressed", String(KINDS[i][0] === next)));
    const sent = next ? note.value.trim() : "";
    try {
      await store.set(runId, prop, caseId, next, sent);
      if (note.value.trim() === sent) typed = false;     // (words typed meanwhile are still to be saved)
      fine();
    } catch (e) {
      fail("Not saved", e);
    } finally {
      busy = false;
      sync();
      if (again) { again = false; saveNote(); }
    }
  }

  async function saveNote() {
    if (busy) { again = true; return; }
    const cur = store.current(runId, prop, caseId);
    const text = note.value.trim();
    if (!cur || text === (cur.note || "")) { typed = false; return; }
    busy = true;
    try {
      await store.set(runId, prop, caseId, cur.kind, text);
      if (note.value.trim() === text) typed = false;     // (words typed meanwhile are still to be saved)
      fine();
    } catch (e) {
      fail("The note was not saved", e);
    } finally {
      busy = false;
      sync();
      if (again) { again = false; saveNote(); }
    }
  }
  note.addEventListener("input", () => { typed = true; });
  note.addEventListener("blur", saveNote);
  note.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); saveNote(); } });

  store.listen((id) => { if (id === runId) sync(); });
  sync();
  return box;
}
