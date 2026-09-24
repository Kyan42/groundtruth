// Usage: npm run boot -- owner/repo#123 [--expect '[data-testid=header-logo]'] [--keep]
// Boots the PR's app in a fresh Runloop sandbox from the repo's .groundtruth.yml (default branch),
// checks it through a tunnel with a real browser, prints a timed report, and shuts the sandbox down.
import { mkdirSync } from "node:fs";
import path from "node:path";
import { parseArgs } from "node:util";
import { bootPr } from "../boot/boot.js";
import { parsePrRef } from "../evidence.js";
import { installationOctokit } from "../github.js";

const { values, positionals } = parseArgs({
  allowPositionals: true,
  options: { expect: { type: "string" }, keep: { type: "boolean", default: false } },
});
if (!positionals[0]) {
  console.error("Usage: npm run boot -- owner/repo#123 [--expect <css selector>] [--keep]");
  process.exit(1);
}

const ref = parsePrRef(positionals[0]);
const outDir = path.join("boot-runs", new Date().toISOString().replace(/[:.]/g, "-"));
mkdirSync(outDir, { recursive: true });

console.log(`Booting ${ref.owner}/${ref.repo}#${ref.number}…`);
const result = await bootPr(await installationOctokit(ref.owner, ref.repo), ref, {
  expectSelector: values.expect,
  screenshotPath: path.join(outDir, "home.png"),
  keep: values.keep,
  onPhase: (p) => console.log(`  ${p.ok ? "✓" : "✗"} ${p.name.padEnd(22)} ${p.seconds.toFixed(1).padStart(6)}s  ${p.detail ?? ""}`),
});

const total = result.phases.reduce((s, p) => s + p.seconds, 0);
console.log(`\n${result.ok ? "BOOTED" : "ERROR"} in ${total.toFixed(1)}s · commit ${result.sha.slice(0, 7)} · sandbox ${result.sandboxId ?? "-"}` +
  `${values.keep && result.sandboxId ? " (kept running; shuts itself down after 10 idle minutes)" : ""}`);
if (result.error) console.log(`\n${result.error}`);
if (result.appLogTail) console.log(`\nLast lines of the app's output:\n${result.appLogTail}`);
if (result.browser) {
  const b = result.browser;
  console.log(`\nBrowser: HTTP ${b.status}, title "${b.title}", screenshot ${b.screenshot}`);
  // First line of each error only: React's errors run to dozens of lines.
  if (b.consoleErrors.length) {
    console.log(`  console errors:\n    ${b.consoleErrors.slice(0, 10).map((e) => e.split("\n")[0].slice(0, 200)).join("\n    ")}`);
  }
  if (b.failedRequests.length) console.log(`  failed requests:\n    ${b.failedRequests.slice(0, 10).join("\n    ")}`);
}
process.exit(result.ok ? 0 : 1);
