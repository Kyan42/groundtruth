// Usage: npm run evals:spotcheck -- evals/runs/<dir> [count=15] [previous.key.json ...]
// Picks random judge verdicts from a run's dev cases and writes a checklist for a human to label
// (match / partial / none) without seeing the judge's label. The judge's answers go to a separate key
// file. Only items the judge matched to some reference item are sampled, so each has something to compare.
// Items in the given previous key files are skipped, so a fresh check never repeats labeled items.
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import type { Extraction } from "../extract.js";
import { assumptionCheck, loadCases } from "../evals/cases.js";
import type { Judgment } from "../evals/judge.js";

const [runDir, countArg, ...excludeFiles] = process.argv.slice(2);
if (!runDir) {
  console.error("Usage: npm run evals:spotcheck -- <runDir> [count] [previous.key.json ...]");
  process.exit(1);
}
const count = Number(countArg ?? 15);
const saved = JSON.parse(readFileSync(path.join(runDir, "results.json"), "utf8")) as {
  results: { caseId: string; run: number; extraction?: Extraction; judgment?: Judgment }[];
};
const cases = new Map(loadCases().map((c) => [c.id, c]));
const key = (x: { caseId: string; run: number; item: string }) => `${x.caseId}#${x.run}#${x.item}`;
const excluded = new Set(excludeFiles.flatMap((f) =>
  (JSON.parse(readFileSync(f, "utf8")) as { caseId: string; run: number; item: string }[]).map(key)));

type Item = { caseId: string; run: number; item: string; extracted: string; reference: string; judge: string; reason: string };
const pool: Item[] = [];
for (const r of saved.results) {
  const c = cases.get(r.caseId);
  if (!c || c.split !== "dev" || !r.extraction || !r.judgment) continue;
  for (const kind of ["claims", "assumptions"] as const) {
    r.judgment[kind].forEach((v) => {
      if (!v.reference || excluded.has(key({ caseId: r.caseId, run: r.run, item: v.item }))) return;
      const idx = Number(v.item.slice(1)) - 1;
      const x = r.extraction![kind][idx];
      if (!x) return;
      const ref = v.reference;
      let refText: string;
      if (ref.startsWith("nt")) {
        const nt = c.expected.not_testable[Number(ref.slice(2)) - 1];
        refText = `(not-testable statement) ${nt?.text} — ${nt?.why}`;
      } else if (ref.startsWith("a")) {
        const a = c.expected.assumptions.find((y) => y.id === ref)!;
        const chk = assumptionCheck(c, a);
        refText = `${chk.when} → (${chk.then.kind}) ${chk.then.what}`;
      } else {
        const rc = c.expected.claims.find((y) => y.id === ref)!;
        refText = `${rc.when} → (${rc.then.kind}) ${rc.then.what}${rc.note ? `\n  _Note: ${rc.note}_` : ""}`;
      }
      pool.push({
        caseId: r.caseId, run: r.run, item: v.item,
        extracted: `${kind === "assumptions" ? "(assumption) " : ""}${x.when} → (${x.then.kind}) ${x.then.what}`,
        reference: `${ref}: ${refText}`, judge: v.label, reason: v.reason,
      });
    });
  }
}

// Deterministic shuffle (seeded by the run directory name) so the sample is reproducible.
let seed = [...path.basename(runDir)].reduce((h, ch) => (h * 31 + ch.charCodeAt(0)) >>> 0, 7);
const rand = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 2 ** 32);
const sample = pool.map((x) => ({ x, k: rand() })).sort((a, b) => a.k - b.k).slice(0, count).map((y) => y.x);

const prUrl = (caseId: string) => {
  const [repo, number] = cases.get(caseId)!.pr.split("#");
  return `https://github.com/${repo}/pull/${number}`;
};

const date = new Date().toISOString().slice(0, 10);
const outDir = "evals/spotchecks";
mkdirSync(outDir, { recursive: true });
const base = path.join(outDir, `${date}-${path.basename(runDir)}`);
writeFileSync(`${base}.md`, [
  `# Judge spot check (${sample.length} items)`,
  "",
  `From \`${runDir}\`, dev cases only. For each item, decide whether the extracted item tests the same intended`,
  "behavior as the reference item:",
  "",
  "- **match**: checks the same thing (same observable result); setup a tester would fill in anyway can be left out",
  "- **partial**: aimed at it but would catch different failures: a different observable where the PR says how it should show up,",
  "  a different situation, missing setup that lets the check pass on a broken feature, two results bundled, or a check that",
  "  would pass even without the change",
  "- **none**: doesn't correspond",
  "",
  "Reply with your 15 answers (e.g. `1 match, 2 partial, ...`). The judge's answers are in the `.key.json` file; don't peek.",
  "",
  ...sample.flatMap((s, i) => [
    `### ${i + 1}. ${s.caseId} ([PR](${prUrl(s.caseId)}))`,
    "",
    `- **Extracted:** ${s.extracted}`,
    `- **Reference** ${s.reference}`,
    "",
  ]),
].join("\n"));
writeFileSync(`${base}.key.json`, JSON.stringify(sample, null, 2));
console.log(`Wrote ${base}.md (${sample.length} of ${pool.length} dev verdicts) and ${base}.key.json`);
