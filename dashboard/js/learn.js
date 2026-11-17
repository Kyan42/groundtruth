// What the run document says about where the claims came from (RUNDOC: a property's `source`, the run's `context`
// and `draft_changes`), in a few quiet lines. Everything taken from the document is shown as text, never as markup.
import { h } from "./dom.js";
import { titleOf } from "./words.js";

const GITHUB_REF = /\b([A-Za-z0-9][\w.-]*)\/([\w.-]+)#(\d+)\b/;
const URL_IN = /https?:\/\/[^\s<>"')\]]+/;

// A link for a source's ref: a URL (or one inside the text), or an owner/repo#n reference made a GitHub link. Its
// label is "#n" for an issue or pull request on GitHub, else the address without its scheme. Null when there is none.
export function refLink(ref, kind) {
  const text = String(ref || "").trim();
  const url = (text.match(URL_IN) || [])[0];
  if (url) {
    let u;
    try { u = new URL(url); } catch { return null; }
    if (u.protocol !== "http:" && u.protocol !== "https:") return null;
    const gh = u.hostname === "github.com" && u.pathname.match(/^\/[^/]+\/[^/]+\/(?:issues|pull)\/(\d+)/);
    const label = gh ? `#${gh[1]}` : `${u.hostname}${u.pathname.replace(/\/$/, "")}`.slice(0, 60);
    return { href: u.href, label };
  }
  const m = text.match(GITHUB_REF);
  if (m) return { href: `https://github.com/${m[1]}/${m[2]}/${kind === "pull request" ? "pull" : "issues"}/${m[3]}`, label: `#${m[3]}` };
  return null;
}

const link = (href, text) => h("a", { href, target: "_blank", rel: "noopener noreferrer" }, text);

// One quiet line under a claim's words: where it came from. Null when the claim has no source.
export function sourceLine(p) {
  const s = p && p.source;
  if (!s || typeof s !== "object") return null;
  const kind = ["issue", "pull request", "interview", "other"].includes(s.kind) ? s.kind : "other";
  const ref = String(s.ref || "").trim();
  const to = refLink(ref, kind);
  const parts = [];
  if (kind === "issue" || kind === "pull request") {
    if (to) parts.push(`From ${kind} `, link(to.href, to.label));
    else if (ref) parts.push(`From ${kind} ${ref}`);
    else parts.push(`From ${kind === "issue" ? "an issue" : "a pull request"}`);
  } else if (kind === "interview") {
    parts.push(to ? link(to.href, "From an interview") : ref ? `From an interview: ${ref}` : "From an interview");
  } else if (to) {
    parts.push("From ", link(to.href, to.label));
  } else if (ref) {
    parts.push(`From ${ref}`);
  } else {
    return null;
  }
  return h("p", { class: "source" }, parts);
}

const bodyText = (t) => (typeof t === "string" && t.trim() ? h("p", { class: "asked-body" }, t.trim()) : null);
const titled = (word, x) => {
  const title = `${word}${x.number ? ` #${x.number}` : ""}${x.title ? `: ${String(x.title).trim()}` : ""}`;
  const to = refLink(x.url);
  return h("p", { class: "asked-title" }, to ? link(to.href, title) : title);
};

// "What was asked for": the pull request and its issues as they were when the run started, closed until opened.
// Null when the run has no context.
export function askedFor(context) {
  if (!context || typeof context !== "object") return null;
  const pr = context.pull_request && typeof context.pull_request === "object" ? context.pull_request : null;
  const issues = (Array.isArray(context.issues) ? context.issues : []).filter((x) => x && typeof x === "object");
  if (!pr && !issues.length) return null;
  const items = [];
  if (pr) items.push(h("div", { class: "asked-item" }, titled("Pull request", pr), bodyText(pr.body)));
  for (const x of issues) items.push(h("div", { class: "asked-item" }, titled("Issue", x), bodyText(x.body)));
  return h("details", { class: "learn asked" }, h("summary", null, "What was asked for"), h("div", { class: "learn-body" }, items));
}

const PART = { title: ["The title", "was"], statement: ["The sentence", "was"], your_words: ["Your own words", "were"],
  checked_against: ["What it is checked against", "was"], variations: ["Variations", "were"], source: ["The source", "was"] };

// "Changed before approval (N)": each change between the claims as first drafted and as approved, in a sentence with
// what it was before and after. Null when nothing changed (or no draft was kept).
export function draftChanges(changes, props) {
  const list = (Array.isArray(changes) ? changes : []).filter((x) => x && typeof x === "object");
  if (!list.length) return null;
  const nameOf = (x) => {
    const p = (props || []).find((q) => q && q.id === x.id);
    const name = (p && (titleOf(p) || p.statement)) || (x.change === "removed" ? x.before : x.after) || x.id || "a claim";
    return `“${String(name).trim().replace(/[.\s]+$/, "")}”`;
  };
  const was = (k, t) => (typeof t === "string" && t.trim() ? h("p", { class: "was" }, h("span", { class: "k" }, `${k}: `), t.trim()) : null);
  const items = list.map((x) => {
    if (x.change === "added") return h("li", null, h("p", null, `A claim was added: ${nameOf(x)}.`));
    if (x.change === "removed") return h("li", null, h("p", null, `A claim was removed: ${nameOf(x)}.`));
    const [word, verb] = PART[x.part] || ["Something", "was"];
    return h("li", null, h("p", null, `${word} of ${nameOf(x)} ${verb} edited.`), was("Before", x.before), was("After", x.after));
  });
  return h("details", { class: "learn changes" }, h("summary", null, `Changed before approval (${list.length})`),
    h("ul", { class: "learn-body" }, items));
}
