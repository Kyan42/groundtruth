// Usage:
//   npm run boot -- owner/repo#123 [--expect <css selector>] [--keep]
//     Boots a PR of a repo where the app is installed, using .groundtruth.yml from its default branch.
//   npm run boot -- owner/repo --sha <commit> --config <file> [--expect ...] [--keep]
//     Boots any commit of a public repo with a local config (no installation needed), e.g. to work out
//     how to boot an app before it has a .groundtruth.yml of its own.
// Checks the app through a tunnel with a real browser, prints a timed report, and shuts the sandbox down.
import { mkdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { parseArgs } from "node:util";
import { bootCommit, bootPr, type BootOptions } from "../boot/boot.js";
import { parseBootConfig } from "../boot/config.js";
import { parsePrRef } from "../evidence.js";
import { installationOctokit } from "../github.js";

const { values, positionals } = parseArgs({
  allowPositionals: true,
  options: {
    expect: { type: "string" },
    keep: { type: "boolean", default: false },
    sha: { type: "string" },
    config: { type: "string" },
  },
});
const target = positionals[0];
if (!target || (values.config && !values.sha) || (!target.includes("#") && !(values.sha && values.config))) {
  console.error("Usage: npm run boot -- owner/repo#123 [--expect <selector>] [--keep]\n" +
    "       npm run boot -- owner/repo --sha <commit> --config <file> [--expect <selector>] [--keep]");
  process.exit(1);
}

const outDir = path.join("boot-runs", new Date().toISOString().replace(/[:.]/g, "-"));
mkdirSync(outDir, { recursive: true });
const opts: BootOptions = {
  expectSelector: values.expect,
  screenshotPath: path.join(outDir, "home.png"),
  keep: values.keep,
  // One line per step; a failed step's full output is printed once at the end.
  onPhase: (p) => console.log(`  ${p.ok ? "✓" : "✗"} ${p.name.padEnd(22)} ${p.seconds.toFixed(1).padStart(6)}s  ` +
    `${(p.detail ?? "").split("\n")[0].slice(0, 110)}`),
};

let result;
if (values.config) {
  const [owner, repo] = target.split("#")[0].split("/");
  const configFile = values.config;
  console.log(`Booting ${owner}/${repo}@${values.sha!.slice(0, 7)} with ${configFile}…`);
  result = await bootCommit({
    owner, repo, sha: values.sha!, label: `${owner}/${repo}@${values.sha!.slice(0, 7)}`,
    loadConfig: async () => ({ config: parseBootConfig(readFileSync(configFile, "utf8")), source: configFile }),
  }, opts);
} else {
  const ref = parsePrRef(target);
  console.log(`Booting ${ref.owner}/${ref.repo}#${ref.number}…`);
  result = await bootPr(await installationOctokit(ref.owner, ref.repo), ref, { ...opts, sha: values.sha });
}

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
