// The list (index.html): one row per feature of a project with its current state, and its runs as history under it
// (an arrow at the row's left opens and closes them), from one GET /v1/runs. The current state is each claim's result from the newest run that tested it, worked out by
// the same code as the feature page (summary.js), so the two pages always agree. index.html?project=<name> lists one
// project's features.
import { h, clear, brandLink, signOut } from "./dom.js";
import { listRuns } from "./api.js";
import { when, plural } from "./words.js";
import { foldFeatures, byLastChecked, holdLine, openWords, featureMark, runCovered, runTally, variationCount } from "./summary.js";

const root = document.getElementById("app");
const project = new URLSearchParams(location.search).get("project") || "";
let made = 0;

const featureURL = (f) => `feature.html?project=${encodeURIComponent(f.project)}&feature=${encodeURIComponent(f.feature)}`;

// One run under its feature, as a small copy of the feature's row: one mark for how the run came out, when it ran
// (and against which commit), and how many of the claims it checked hold. Opens the run's page. A screen reader hears
// when it ran and what it covered in full.
function runRow(s) {
  const t = runTally(s);
  return h("li", null, h("a", { class: "runline", href: `run.html?run=${encodeURIComponent(s.id)}`, "aria-label": `${when(s.ran_at)}: ${runCovered(s)}` },
    h("span", { class: `glyph ${t.mark.tone}`, "aria-hidden": "true" }, t.mark.glyph),
    h("span", { class: "when" },
      h("span", null, when(s.ran_at)),
      s.commit ? h("span", { class: "commit" }, "commit ", h("code", null, s.commit)) : null,
      s.complete === false ? h("span", null, "upload not complete") : null),
    h("span", { class: "rtally" }, t.count ? h("b", null, t.count) : null, t.count ? ` ${t.words}` : t.words,
      t.left ? h("span", { class: "rleft" }, t.left) : null)));
}

function featureRow(f) {
  const id = `runs-${++made}`;
  const name = `${f.project || "Unnamed app"} · ${f.feature || "unnamed feature"}`;
  const mark = featureMark(f.results);
  const held = holdLine(f.results);
  const checked = f.lastChecked ? `last checked ${when(f.lastChecked)}` : "not checked yet";
  const tried = variationCount(f.results);
  const runsWord = plural(f.runs.length, "run", "runs");
  const facts = [...openWords(f.results), tried ? `${plural(tried, "variation", "variations")} tried` : null, runsWord].filter(Boolean);

  // The whole row opens the feature's page: the name is the link, stretched over the row, and its name for a screen
  // reader starts with the name shown and goes on with everything else the row shows. The arrow at the row's left
  // edge opens and closes the feature's runs; it sits above the link, so pressing it never also follows the link.
  const link = h("a", { class: "fname", href: featureURL(f), "aria-label": `${name}: ${[held, ...facts.slice(0, -1), checked].join(", ")}` }, name);
  const btn = h("button", { type: "button", class: "runs-btn", "aria-expanded": "false", "aria-controls": id,
    "aria-label": `${runsWord} of ${name}` }, h("span", { class: "chev", "aria-hidden": "true" }));
  const history = h("ul", { class: "history", id, hidden: true }, f.runs.map(runRow));
  const m = held.match(/^(\d+ of \d+)(.*)$/);
  const li = h("li", { class: "feature" }, h("div", { class: "fcard" },
    h("div", { class: "frow" }, btn,
      h("span", { class: `glyph ${mark.tone}`, "aria-hidden": "true" }, mark.glyph),
      h("div", { class: "what" }, link,
        h("div", { class: "facts" }, facts.map((x, i) => h("span", { class: i === facts.length - 1 ? "runs-n" : null, "aria-hidden": "true" }, x)))),
      h("div", { class: "tally", "aria-hidden": "true" }, m ? [h("b", null, m[1]), m[2]] : held, h("span", null, checked))),
    history));
  btn.addEventListener("click", () => {
    const open = btn.getAttribute("aria-expanded") !== "true";
    btn.setAttribute("aria-expanded", String(open));
    history.hidden = !open;
    li.classList.toggle("open", open);
  });
  return li;
}

async function main() {
  const page = h("div", { class: "list-page" },
    h("header", { class: "list-top" }, brandLink(), signOut()),
    h("main", null, h("h1", null, "Test runs"), h("div", { id: "runs" }, h("p", { class: "plain" }, "Loading the runs…"))));
  clear(root).append(page);
  const holder = page.querySelector("#runs");
  let runs;
  try {
    const answer = await listRuns(project);
    runs = answer && Array.isArray(answer.runs) ? answer.runs : [];
  } catch (e) {
    clear(holder).append(h("p", { class: "plain" }, `The runs could not be loaded. ${e.message}`));
    return;
  }
  if (runs.length === 0) {
    clear(holder).append(h("p", { class: "plain" }, project ? `No runs of ${project} have been uploaded yet.` : "No runs have been uploaded yet."));
    return;
  }
  clear(holder).append(h("ul", { class: "features" }, byLastChecked(foldFeatures(runs)).map(featureRow)));
}

main();
