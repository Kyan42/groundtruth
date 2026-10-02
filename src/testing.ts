import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import type { Octokit } from "@octokit/core";
import { bootPr, type BootResult } from "./boot/boot.js";
import { CONFIG_PATH } from "./boot/config.js";
import { renderReport } from "./check-report.js";
import { type ApprovedClaim, offerTests, readState, type RegressionRow } from "./comment.js";
import { config } from "./config.js";
import { runUrl } from "./dashboard/server.js";
import type { PrRef } from "./evidence.js";
import { replayRun, type ReplayResult } from "./compile/replay.js";
import { exploreApp, type ExploreRun } from "./explore/run.js";
import { createLimiter } from "./limit.js";
import { editComment, findComment } from "./pr-comment.js";
import { type RegressionResult, runRegressions } from "./regressions.js";

// Where approved claims are tested: boot the PR's app in a sandbox, have the exploring agent check each
// claim in a real browser, and report per-claim results on the "Groundtruth" GitHub Check.

// Each run holds a sandbox for a few minutes (boot plus exploration); the Runloop trial allows 3 at once.
const runs = createLimiter(config.bootConcurrency);

export type TestingOptions = {
  approvedBy?: string;
  baseSha?: string;                // the base branch commit regression tests are read from
  regressions?: RegressionRow[];   // tests from earlier PRs to replay after the claims
};

export async function startTesting(octokit: Octokit, ref: PrRef, sha: string, claims: ApprovedClaim[], opts: TestingOptions = {}): Promise<number> {
  const n = `${claims.length} claim${claims.length === 1 ? "" : "s"}`;
  const { data } = await octokit.request("POST /repos/{owner}/{repo}/check-runs", {
    owner: ref.owner, repo: ref.repo, name: "Groundtruth", head_sha: sha, status: "queued",
    output: { title: `${n} approved · waiting to boot the app`, summary: summary(claims), text: claimList(claims) },
  });
  const checkId = Number(data.id);
  const label = `${ref.owner}/${ref.repo}#${ref.number}`;

  void runs.run(() => testAndReport(octokit, ref, sha, claims, checkId, label, opts)).catch((err) =>
    console.error(`[groundtruth] ${label}: testing failed to report: ${err}`));
  return checkId;
}

async function testAndReport(octokit: Octokit, ref: PrRef, sha: string, claims: ApprovedClaim[], checkId: number, label: string, opts: TestingOptions) {
  const update = (body: Record<string, unknown>) => octokit.request("PATCH /repos/{owner}/{repo}/check-runs/{check_run_id}", {
    owner: ref.owner, repo: ref.repo, check_run_id: checkId, ...body,
  });
  // Progress updates go out in order and never break the run.
  let progress: Promise<unknown> = Promise.resolve();
  const report = (title: string) => {
    progress = progress.then(() => update({ output: { title, summary: summary(claims), text: claimList(claims) } })).catch(() => {});
  };
  const startedAt = new Date().toISOString();
  const runDir = path.join(config.runsDir, `${ref.repo}-${ref.number}-${sha.slice(0, 7)}-${startedAt.replace(/[:.]/g, "-")}`);
  mkdirSync(runDir, { recursive: true });
  // The check's Details link opens this run on the dashboard (local-only unless DASHBOARD_URL says otherwise).
  const inProgress = { status: "in_progress", output: { title: "Booting the app…", summary: summary(claims), text: claimList(claims) } };
  await update({ ...inProgress, details_url: runUrl(runDir) }).catch(async (err) => {
    console.warn(`[groundtruth] ${label}: GitHub refused the dashboard link (${err}); continuing without it`);
    await update(inProgress);
  });
  const { data: pr } = await octokit.request("GET /repos/{owner}/{repo}/pulls/{pull_number}", { owner: ref.owner, repo: ref.repo, pull_number: ref.number });
  console.log(`[groundtruth] ${label}: booting ${sha.slice(0, 7)}…`);

  let explored: ExploreRun | undefined;
  let exploreError: string | undefined;
  let replay: ReplayResult | undefined;
  let regressions: RegressionResult[] | undefined;
  let regressionError: string | undefined;
  let bootSeconds = 0;
  const result = await bootPr(octokit, ref, {
    sha, screenshotPath: path.join(runDir, "home.png"),
    onPhase: (p) => { bootSeconds += p.seconds; },
    afterBoot: async ({ url, headers, resetApp, personas }) => {
      bootSeconds = Math.round(bootSeconds);
      console.log(`[groundtruth] ${label}: booted in ${bootSeconds}s; testing ${claims.length} claims`);
      report(`App booted in ${bootSeconds}s · testing ${claims.length} claims…`);
      let done = 0;
      try {
        explored = await exploreApp({
          url, headers, resetApp, personas, claims, outDir: runDir,
          meta: { pr: label, title: pr.title, url: pr.html_url, branch: pr.head.ref, sha, startedAt, bootSeconds, source: "approval", checkRun: checkId },
          onEvent: (e) => {
            if (e.type === "status") report(`Testing · ${++done} of ${claims.length} claims done`);
            if (e.type === "status" || (e.type === "check" && !e.check.passed)) {
              console.log(`[groundtruth] ${label}: ${e.type === "status" ? `${e.result.claimId} ${e.result.status}` : `check ${e.check.id} failed`}`);
            }
          },
        });
      } catch (err) {
        // The agent or the browser broke: our problem, reported as such, never as a claim failing.
        exploreError = err instanceof Error ? err.message : String(err);
      }
      if (!explored) return;
      // Compile the run into Playwright tests and replay them once on the same app: the clean record of the
      // run, and a first sign of whether the script holds up. A replay problem never changes the verdicts.
      report(`Claims tested · replaying the compiled script…`);
      try {
        replay = await replayRun({ runDir, baseUrl: url, headers, resetApp, personas, approvedBy: opts.approvedBy });
        console.log(`[groundtruth] ${label}: replay ${replay.ok ? "passed" : "had problems"}: ${replay.journeys.map((j) => `${j.id} ${j.status}`).join(", ")}${replay.error ? ` (${replay.error})` : ""}`);
      } catch (err) {
        console.log(`[groundtruth] ${label}: replay error: ${err}`);
      }
      // Tests earlier PRs added: replayed on this PR's app to catch what it breaks.
      const rows = opts.regressions ?? [];
      if (rows.length && opts.baseSha) {
        report(`Claims tested · replaying ${rows.length} regression check${rows.length === 1 ? "" : "s"} from earlier PRs…`);
        try {
          regressions = await runRegressions({ octokit, ref, baseSha: opts.baseSha, rows, runDir, baseUrl: url, headers, resetApp, personas });
          console.log(`[groundtruth] ${label}: regressions: ${regressions.map((r) => `${r.id} ${r.status}`).join(", ")}`);
        } catch (err) {
          regressionError = err instanceof Error ? err.message : String(err);
          console.log(`[groundtruth] ${label}: regression checks couldn't run: ${regressionError}`);
        }
      }
    },
  });
  await progress;
  bootSeconds = Math.round(bootSeconds);

  // Runs that end without exploring still get a trace, so the dashboard shows what happened.
  if (!explored) {
    const meta = { pr: label, title: pr.title, url: pr.html_url, branch: pr.head.ref, sha, startedAt, bootSeconds, source: "approval", checkRun: checkId, claims };
    const error = result.ok ? `Testing stopped: ${exploreError}` : `Couldn't boot the app: ${result.error}`;
    writeFileSync(path.join(runDir, "trace.json"), JSON.stringify({ ...meta, error }, null, 2));
  }

  if (!result.ok) {
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
    console.log(`[groundtruth] ${label}: boot error: ${result.error}`);
    return;
  }

  if (!explored) {
    await update({
      conclusion: "neutral",
      output: {
        title: "Testing stopped (not a test failure)",
        summary: `The app booted in ${bootSeconds}s, but testing broke before it finished: ${exploreError ?? "unknown error"}. ` +
          "This is a Groundtruth problem, so it says nothing about the PR's changes.",
        text: `${bootReport(result)}\n\n### Approved claims\n\n${claimList(claims)}`,
      },
    });
    console.log(`[groundtruth] ${label}: testing error: ${exploreError}`);
    return;
  }

  const r = renderReport(explored, claims, { sha, bootSeconds, dashboard: runUrl(runDir), replay, regressions, regressionError, extra: bootReport(result) });
  await update({ conclusion: r.conclusion, output: { title: r.title, summary: r.summary, text: r.text } });
  console.log(`[groundtruth] ${label}: ${r.conclusion}: ${r.title} ($${explored.costUsd.toFixed(2)}, ${explored.seconds}s, ${runDir})`);

  // Every claim verified and the compiled tests replayed cleanly: offer to add them to the PR.
  const allVerified = claims.length > 0 && claims.every((c) => explored!.results.some((x) => x.claimId === c.id && x.status === "verified"));
  if (allVerified && replay?.ok && replay.entries.length) {
    try {
      const comment = await findComment(octokit, ref);
      const state = comment && readState(comment.body);
      if (comment && state) {
        state.tests = { run: path.basename(runDir), files: replay.entries.map((e) => ({ file: e.file, title: e.title })) };
        await editComment(octokit, ref, comment.id, offerTests(comment.body, state, runUrl(runDir)));
        console.log(`[groundtruth] ${label}: offered ${replay.entries.length} tests for the PR`);
      }
    } catch (err) {
      console.log(`[groundtruth] ${label}: couldn't offer the tests: ${err}`);
    }
  }
}

const summary = (claims: ApprovedClaim[]) =>
  `${claims.length} claim${claims.length === 1 ? " was" : "s were"} approved in the Groundtruth comment.`;

const claimList = (claims: ApprovedClaim[]) =>
  claims.map((c, i) => `${i + 1}. ${c.when ? `${c.when} → ` : ""}${c.then.what}`).join("\n");

function bootReport(r: BootResult): string {
  const rows = r.phases.map((p) => `| ${p.ok ? "✓" : "✗"} ${p.name} | ${p.seconds.toFixed(1)}s | ${(p.detail ?? "").replace(/\|/g, "\\|").replace(/\n/g, " ")} |`);
  const browser = r.browser && (r.browser.consoleErrors.length || r.browser.failedRequests.length)
    ? `\n\nBrowser console errors: ${r.browser.consoleErrors.length}, failed requests: ${r.browser.failedRequests.length}`
    : "";
  return `### Boot (commit \`${r.sha.slice(0, 7)}\`)\n\n| Step | Time | Detail |\n|---|---|---|\n${rows.join("\n")}${browser}`;
}
