// Usage: npm run evals:compare -- evals/runs/<A> evals/runs/<B>
// Compares two judgings of the same extractions (e.g. a run and a --rejudge of it): how often the judge
// picked the same reference item and label for each extracted claim/assumption. Lists disagreements
// for dev cases only (test cases stay held out).
import { readFileSync } from "node:fs";
import path from "node:path";
import { loadCases } from "../evals/cases.js";
import type { Judgment } from "../evals/judge.js";

type Saved = { results: { caseId: string; run: number; judgment?: Judgment }[] };
const [a, b] = process.argv.slice(2).map((d) => JSON.parse(readFileSync(path.join(d, "results.json"), "utf8")) as Saved);
if (!a || !b) {
  console.error("Usage: npm run evals:compare -- <runDirA> <runDirB>");
  process.exit(1);
}
const splitOf = new Map(loadCases().map((c) => [c.id, c.split]));

let same = 0, sameRef = 0, total = 0;
const diffs: string[] = [];
for (const ra of a.results) {
  const rb = b.results.find((x) => x.caseId === ra.caseId && x.run === ra.run);
  if (!ra.judgment || !rb?.judgment) continue;
  for (const kind of ["claims", "assumptions"] as const) {
    for (const va of ra.judgment[kind]) {
      const vb = rb.judgment[kind].find((x) => x.item === va.item);
      if (!vb) continue;
      total++;
      if (va.reference === vb.reference) sameRef++;
      if (va.reference === vb.reference && va.label === vb.label) same++;
      else if (splitOf.get(ra.caseId) === "dev") {
        diffs.push(`${ra.caseId} #${ra.run} ${va.item}: ${va.reference ?? "—"}/${va.label} → ${vb.reference ?? "—"}/${vb.label}\n    A: ${va.reason}\n    B: ${vb.reason}`);
      }
    }
  }
}
const p = (n: number) => `${Math.round((100 * n) / total)}%`;
console.log(`${total} items judged in both. Same reference and label: ${same} (${p(same)}). Same reference: ${sameRef} (${p(sameRef)}).`);
if (diffs.length) console.log(`\nDisagreements (dev cases):\n${diffs.join("\n")}`);
