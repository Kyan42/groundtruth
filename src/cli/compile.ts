// Usage: npm run compile -- runs/<run-folder>
// Compiles a run's trace.json into Playwright tests in <run>/scripts/ and prints what was kept and dropped.
// Replay: GROUNDTRUTH_BASE_URL=... GROUNDTRUTH_RESET="..." npx playwright test -c <run>/scripts/playwright.config.ts
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { compileRun, type Trace } from "../compile/compile.js";

const runDir = process.argv[2];
if (!runDir) {
  console.error("Usage: npm run compile -- runs/<run-folder>");
  process.exit(1);
}
const trace = JSON.parse(readFileSync(path.join(runDir, "trace.json"), "utf8")) as Trace;
const { files, notes } = compileRun(trace, path.basename(runDir));
const out = path.join(runDir, "scripts");
mkdirSync(out, { recursive: true });
for (const [name, content] of Object.entries(files)) writeFileSync(path.join(out, name), content);

console.log(`Compiled ${trace.journeys.length} journeys from ${trace.pr} into ${out}`);
for (const n of notes) console.log(`  · ${n}`);
