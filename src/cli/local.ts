import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import path from "node:path";
import { Octokit } from "@octokit/core";
import { bootCommit } from "../boot/boot.js";
import { loadBootConfig } from "../boot/config.js";
import type { ApprovedClaim } from "../comment.js";
import { replayRun } from "../compile/replay.js";
import { config } from "../config.js";
import { handleDashboard } from "../dashboard/server.js";
import { listAll, parsePrRef, renderEvidence, type Evidence } from "../evidence.js";
import { exploreApp } from "../explore/run.js";
import { extractClaims, isGrounded } from "../extract.js";
import { branchTip, readRegistry } from "../registry.js";
import { runRegressions } from "../regressions.js";

const target = process.argv[2];
if (!target) throw new Error("Usage: tsx --env-file=.env src/cli/local.ts owner/repo#N");
const ref = parsePrRef(target);
const octokit = new Octokit();
const startedAt = new Date().toISOString();
const runDir = path.resolve(config.runsDir, `${ref.repo}-${ref.number}-local-${startedAt.replace(/[:.]/g, "-")}`);
mkdirSync(runDir, { recursive: true });
const save = (name: string, value: unknown) => writeFileSync(path.join(runDir, name), JSON.stringify(value, null, 2));
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
  const { data: pr } = await octokit.request("GET /repos/{owner}/{repo}/pulls/{pull_number}", {
    ...ref, pull_number: ref.number,
  });
  const commits = await listAll((page) => octokit.request("GET /repos/{owner}/{repo}/pulls/{pull_number}/commits", {
    ...ref, pull_number: ref.number, per_page: 100, page,
  }).then((response) => response.data));
  const baseSha = await branchTip(octokit, ref, pr.base.ref);
  const registry = await readRegistry(octokit, ref, baseSha);
  const evidence: Evidence = {
    repo: `${ref.owner}/${ref.repo}`, number: ref.number, url: pr.html_url,
    title: pr.title, author: pr.user?.login ?? "unknown", body: pr.body ?? "",
    baseRef: pr.base.ref, headSha: pr.head.sha, linkedIssues: [],
    commits: commits.map((commit) => commit.commit.message),
  };
  save("evidence.json", { ...evidence, baseSha, linkedIssuesFetched: false });
  const rendered = renderEvidence(evidence);
  console.log(`Extracting claims: ${pr.title}; head ${pr.head.sha}; regression source ${baseSha}`);
  const extraction = await extractClaims(rendered);
  save("extraction.json", extraction);
  if (extraction.extraction.claims.some((claim) => !isGrounded(claim.source, rendered))) {
    throw new Error("Extraction contains an ungrounded source quote; review extraction.json before proceeding");
  }
  const claims: ApprovedClaim[] = extraction.extraction.claims.map((claim, index) => ({
    id: `c${index + 1}`, when: claim.when, then: claim.then, edited: false,
  }));
  save("claims.json", claims);
  for (const claim of claims) console.log(`${claim.id}: ${claim.when} → ${claim.then.what}`);
  const rows = registry.map((entry, index) => ({
    id: `r${index + 1}`, file: entry.file, title: entry.title,
    summary: entry.summary, from: entry.from.pr,
  }));
  console.log(`${rows.length} regression files from ${pr.base.ref}; assumptions saved for review, not tested`);
  const started = Date.now();
  const boot = await bootCommit({
    owner: ref.owner, repo: ref.repo, sha: pr.head.sha, label: target,
    loadConfig: async () => {
      const loaded = await loadBootConfig(octokit, ref.owner, ref.repo);
      return { config: loaded.config, source: loaded.ref };
    },
  }, {
    screenshotPath: path.join(runDir, "boot.png"),
    onPhase: (phase) => console.log(`${phase.ok ? "OK" : "ERROR"} ${phase.name} (${Math.round(phase.seconds)}s)`),
    afterBoot: async ({ url, headers, resetApp, sha }) => {
      try {
        await exploreApp({
          url, headers, resetApp, claims, outDir: runDir, maxTurns: 60,
          meta: { pr: target, title: pr.title, url: pr.html_url, branch: pr.head.ref, sha, baseSha,
            startedAt, bootSeconds: Math.round((Date.now() - started) / 1000), source: "cli" },
          onEvent: (event) => {
            if (event.type === "status") console.log(`${event.result.claimId}: ${event.result.status} — ${event.result.evidence}`);
            else if (event.type === "check") console.log(`Check ${event.check.id}: ${event.check.passed ? "PASS" : "FAIL"} ${event.check.observed}`);
            else if (event.type === "tool") console.log(`Tool ${event.name}: ${event.summary}`.slice(0, 240));
          },
        });
        const replay = await replayRun({ runDir, baseUrl: url, headers, resetApp });
        console.log(`Claim replay: ${replay.ok ? "passed" : "had problems"}`);
      } catch (error) {
        save("exploration-error.json", { error: String(error) });
        if (!existsSync(path.join(runDir, "trace.json"))) {
          save("trace.json", { pr: target, title: pr.title, startedAt, error: String(error) });
        }
        console.error(`Exploration/replay stopped: ${String(error)}`);
      }
      const results = await runRegressions({ octokit, ref, baseSha, rows, runDir, baseUrl: url, headers, resetApp });
      for (const result of results) console.log(`${result.id}: ${result.status} — ${result.title}`);
    },
  });
  save("boot.json", boot);
  if (!boot.ok || boot.error) throw new Error(boot.error ?? "Boot failed");
  console.log(`Finished. Artifacts: ${runDir}. Dashboard remains running.`);
} catch (error) {
  save("error.json", { error: String(error) });
  if (!existsSync(path.join(runDir, "trace.json"))) {
    save("trace.json", { pr: target, startedAt, error: String(error) });
  }
  console.error(`Run stopped: ${String(error)}. Saved diagnostics: ${runDir}`);
}
