// Usage: npm run compile -- runs/<run-folder>
// Compiles a run's trace.json into Groundtruth tests in <run>/scripts/ (tests/ and support/, the same layout
// as .groundtruth/ in a repo) and prints what was kept and dropped. Replay them against an app with:
//   GROUNDTRUTH_BASE_URL=... GROUNDTRUTH_RESET="..." npx playwright test -c <run>/scripts/support/playwright.config.ts
import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { compileRun, type Trace } from "../compile/compile.js";

const runDir = process.argv[2];
if (!runDir) {
  console.error("Usage: npm run compile -- runs/<run-folder>");
  process.exit(1);
}
const trace = JSON.parse(readFileSync(path.join(runDir, "trace.json"), "utf8")) as Trace;
const { files, entries, notes } = compileRun(trace);
const out = path.join(runDir, "scripts");
rmSync(out, { recursive: true, force: true });
for (const [name, content] of Object.entries(files)) {
  mkdirSync(path.dirname(path.join(out, name)), { recursive: true });
  writeFileSync(path.join(out, name), content);
}

console.log(`Compiled ${trace.pr} into ${out}:`);
for (const e of entries) console.log(`  tests/${e.file}: ${e.title} (${e.journeys.map((j) => j.id).join(" → ")})`);
for (const n of notes) console.log(`  · ${n}`);
