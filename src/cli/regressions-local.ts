import { readFileSync, renameSync, existsSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import path from "node:path";
import { Octokit } from "@octokit/core";
import { bootCommit } from "../boot/boot.js";
import { parseBootConfig } from "../boot/config.js";
import { config } from "../config.js";
import { handleDashboard } from "../dashboard/server.js";
import { parsePrRef } from "../evidence.js";
import { readRegistry } from "../registry.js";
import { runRegressions } from "../regressions.js";

const runDir = path.resolve(process.argv[2] ?? "");
const trace = JSON.parse(readFileSync(path.join(runDir, "trace.json"), "utf8"));
const ref = parsePrRef(trace.pr);
if (!trace.sha || !trace.baseSha) throw new Error("Saved run must have pinned head and base SHAs");
const octokit = new Octokit();
const server = createServer(async (request, response) => {
  try {
    if (!await handleDashboard(request, response)) response.writeHead(404).end();
  } catch {
    if (!response.headersSent) response.writeHead(500);
    response.end();
  }
});
await new Promise<void>((resolve, reject) => {
  server.once("error", reject);
  server.listen(config.port, "127.0.0.1", resolve);
});
console.log(`Dashboard: http://localhost:${config.port}/runs/${path.basename(runDir)}`);
try {
  const entries = await readRegistry(octokit, ref, trace.baseSha);
  const rows = entries.map((entry, index) => ({
    id: `r${index + 1}`, file: entry.file, title: entry.title,
    summary: entry.summary, from: entry.from.pr,
  }));
  const previous = path.join(runDir, "regression-out");
  if (existsSync(previous)) renameSync(previous, `${previous}-interrupted-${Date.now()}`);
  const boot = await bootCommit({
    owner: ref.owner, repo: ref.repo, sha: trace.sha, label: trace.pr,
    loadConfig: async () => {
      const { data } = await octokit.request("GET /repos/{owner}/{repo}/contents/{path}", {
        owner: ref.owner, repo: ref.repo, path: ".groundtruth.yml", ref: trace.baseSha,
      });
      if (Array.isArray(data) || data.type !== "file") throw new Error("Missing boot configuration");
      return { config: parseBootConfig(Buffer.from(data.content, "base64").toString("utf8")), source: trace.baseSha };
    },
  }, {
    screenshotPath: path.join(runDir, "regression-boot.png"),
    onPhase: (phase) => console.log(`${phase.ok ? "OK" : "ERROR"} ${phase.name} (${Math.round(phase.seconds)}s)`),
    afterBoot: async ({ url, headers, resetApp }) => {
      const results = await runRegressions({ octokit, ref, baseSha: trace.baseSha, rows, runDir, baseUrl: url, headers, resetApp });
      for (const result of results) console.log(`${result.id}: ${result.status} — ${result.title}`);
    },
  });
  writeFileSync(path.join(runDir, "regression-boot.json"), JSON.stringify(boot, null, 2));
  if (!boot.ok || boot.error) throw new Error(boot.error ?? "Boot failed");
  console.log("Regression run finished; sandbox shutdown requested. Dashboard remains running.");
} catch (error) {
  writeFileSync(path.join(runDir, "regression-error.json"), JSON.stringify({ error: String(error) }, null, 2));
  console.error(String(error));
}
