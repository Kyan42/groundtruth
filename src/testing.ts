import { mkdirSync } from "node:fs";
import path from "node:path";
import type { Octokit } from "@octokit/core";
import { bootPr, type BootResult } from "./boot/boot.js";
import { CONFIG_PATH } from "./boot/config.js";
import type { ApprovedClaim } from "./comment.js";
import { config } from "./config.js";
import type { PrRef } from "./evidence.js";
import { createLimiter } from "./limit.js";

// Where approved claims are handed to browser testing. Today that means: boot the PR's app in a sandbox
// and report on a GitHub Check. Exploring the app to verify the claims is the next step and isn't built.

// Each boot holds a sandbox for about 30s; the Runloop trial allows 3 at once.
const boots = createLimiter(config.bootConcurrency);

export async function startTesting(octokit: Octokit, ref: PrRef, sha: string, claims: ApprovedClaim[]): Promise<number> {
  const n = `${claims.length} check${claims.length === 1 ? "" : "s"}`;
  const { data } = await octokit.request("POST /repos/{owner}/{repo}/check-runs", {
    owner: ref.owner, repo: ref.repo, name: "Groundtruth", head_sha: sha, status: "queued",
    output: { title: `${n} approved · waiting to boot the app`, summary: summary(claims), text: claimList(claims) },
  });
  const checkId = Number(data.id);
  const label = `${ref.owner}/${ref.repo}#${ref.number}`;

  void boots.run(() => bootAndReport(octokit, ref, sha, claims, checkId, label)).catch((err) =>
    console.error(`[groundtruth] ${label}: boot reporting failed: ${err}`));
  return checkId;
}

async function bootAndReport(octokit: Octokit, ref: PrRef, sha: string, claims: ApprovedClaim[], checkId: number, label: string) {
  const update = (body: Record<string, unknown>) => octokit.request("PATCH /repos/{owner}/{repo}/check-runs/{check_run_id}", {
    owner: ref.owner, repo: ref.repo, check_run_id: checkId, ...body,
  });
  await update({ status: "in_progress", output: { title: "Booting the app…", summary: summary(claims), text: claimList(claims) } });

  const outDir = path.join("boot-runs", `${ref.repo}-${ref.number}-${sha.slice(0, 7)}-${Date.now()}`);
  mkdirSync(outDir, { recursive: true });
  console.log(`[groundtruth] ${label}: booting ${sha.slice(0, 7)}…`);
  const result = await bootPr(octokit, ref, { sha, screenshotPath: path.join(outDir, "home.png") });
  const seconds = result.phases.reduce((s, p) => s + p.seconds, 0).toFixed(0);

  if (result.ok) {
    // The app is up; the claims themselves aren't tested yet, so this is neutral, not success.
    await update({
      conclusion: "neutral",
      output: {
        title: `App booted in ${seconds}s · claim testing not built yet`,
        summary: `${summary(claims)}\n\nThe app booted from \`${CONFIG_PATH}\` and loaded in a browser.`,
        text: `${bootReport(result)}\n\n### Approved checks\n\n${claimList(claims)}`,
      },
    });
  } else {
    // A boot failure is an infrastructure error, never a claim failing: point at the config to fix.
    const { data: repo } = await octokit.request("GET /repos/{owner}/{repo}", { owner: ref.owner, repo: ref.repo });
    await update({
      conclusion: "action_required",
      details_url: `https://github.com/${ref.owner}/${ref.repo}/blob/${repo.default_branch}/${CONFIG_PATH}`,
      output: {
        title: `Couldn't boot the app (not a test failure)`,
        summary: `Booting failed at **${result.error}**. Check \`${CONFIG_PATH}\` on the default branch. ` +
          "Nothing was tested, so this says nothing about the PR's changes.",
        text: `${bootReport(result)}${result.appLogTail ? `\n\n### Last lines of the app's output\n\n\`\`\`\n${result.appLogTail}\n\`\`\`` : ""}`,
      },
    });
  }
  console.log(`[groundtruth] ${label}: ${result.ok ? `booted in ${seconds}s` : `boot error: ${result.error}`} ` +
    `(sandbox ${result.sandboxId ?? "-"}, screenshot ${outDir})`);
}

const summary = (claims: ApprovedClaim[]) =>
  `${claims.length} check${claims.length === 1 ? " was" : "s were"} approved in the Groundtruth comment.`;

const claimList = (claims: ApprovedClaim[]) =>
  claims.map((c, i) => `${i + 1}. ${c.when ? `${c.when} → ` : ""}${c.then.what}`).join("\n");

function bootReport(r: BootResult): string {
  const rows = r.phases.map((p) => `| ${p.ok ? "✓" : "✗"} ${p.name} | ${p.seconds.toFixed(1)}s | ${(p.detail ?? "").replace(/\|/g, "\\|").replace(/\n/g, " ")} |`);
  const browser = r.browser && (r.browser.consoleErrors.length || r.browser.failedRequests.length)
    ? `\n\nBrowser console errors: ${r.browser.consoleErrors.length}, failed requests: ${r.browser.failedRequests.length}`
    : "";
  return `### Boot (commit \`${r.sha.slice(0, 7)}\`)\n\n| Step | Time | Detail |\n|---|---|---|\n${rows.join("\n")}${browser}`;
}
