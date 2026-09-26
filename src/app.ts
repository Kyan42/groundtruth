import type { Octokit } from "@octokit/core";
import path from "node:path";
import {
  approvedClaims, approvedRegressions, COMMENT_MARKER, type CommentState, markApproved, markTestsAdded, parseReview,
  readState, type RegressionRow, renderClaimsComment, renderError, renderPlaceholder,
} from "./comment.js";
import { config } from "./config.js";
import { runUrl } from "./dashboard/server.js";
import { buildEvidence, type PrRef, renderEvidence } from "./evidence.js";
import { extractClaims } from "./extract.js";
import { githubApp } from "./github.js";
import { createLimiter } from "./limit.js";
import { editComment, upsertComment } from "./pr-comment.js";
import { commitTests, readRegistry } from "./registry.js";
import { startTesting } from "./testing.js";

export const app = githubApp;

// Caps concurrent claim extractions so a burst of PRs doesn't exceed Claude API rate limits.
const extractions = createLimiter(config.extractConcurrency);

// Log every delivery so it's obvious what GitHub is sending us.
app.webhooks.onAny(({ id, name, payload }) => {
  const action = "action" in payload ? `.${payload.action}` : "";
  console.log(`[webhook] ${name}${action} (delivery ${id})`);
});

// Respond to GitHub right away (it waits at most 10s); the work happens in the background.
// A PR is read when it's opened or reopened, or when a draft is marked ready for review. Drafts are
// skipped: their description is usually still being written.
app.webhooks.on(["pull_request.opened", "pull_request.reopened", "pull_request.ready_for_review"], ({ octokit, payload }) => {
  const { repository, pull_request: pr } = payload;
  const ref: PrRef = { owner: repository.owner.login, repo: repository.name, number: pr.number };
  const label = `${repository.full_name}#${pr.number}`;
  if (pr.draft) {
    console.log(`[groundtruth] ${label} is a draft; waiting until it's marked ready for review`);
    return;
  }
  console.log(`[groundtruth] ${label} "${pr.title}" (head ${pr.head.sha.slice(0, 7)}), ` +
    `queued (${extractions.active} running, ${extractions.queued} waiting)`);

  void extractions.run(() => processPr(octokit, ref, pr.head.sha, label)).catch((err) =>
    console.error(`[groundtruth] ${label}: unhandled error: ${err}`));
});

// The developer approves by ticking the "Approve" box in our comment, which edits it.
app.webhooks.on("issue_comment.edited", ({ octokit, payload }) => {
  const { comment, issue, repository, sender } = payload;
  // Only our comment on a PR, and only human edits: our own edits fire this event too.
  if (!issue.pull_request || sender.type === "Bot") return;
  if (Number(comment.performed_via_github_app?.id) !== Number(config.appId) || !comment.body.startsWith(COMMENT_MARKER)) return;

  const ref: PrRef = { owner: repository.owner.login, repo: repository.name, number: issue.number };
  const label = `${repository.full_name}#${issue.number}`;
  void handleReview(octokit, ref, Number(comment.id), comment.body, sender.login, label).catch((err) =>
    console.error(`[groundtruth] ${label}: approval failed: ${err}`));
});

async function handleReview(
  octokit: Octokit, ref: PrRef, commentId: number, body: string, by: string, label: string,
): Promise<void> {
  const state = readState(body);
  if (!state) return; // still the placeholder or an error
  const review = parseReview(body);
  if (state.approval) {
    // After approval the only thing we act on is "Add these tests to this PR".
    if (state.tests && !state.tests.added && review.addTestsChecked) return addTests(octokit, ref, commentId, body, state, by, label);
    console.log(`[groundtruth] ${label}: edited by ${by} after approval; ignored`);
    return;
  }
  if (!review.approveChecked) {
    console.log(`[groundtruth] ${label}: edited by ${by}, not approved yet`);
    return;
  }

  const claims = approvedClaims(state, review);
  const { data: pr } = await octokit.request("GET /repos/{owner}/{repo}/pulls/{pull_number}", {
    owner: ref.owner, repo: ref.repo, pull_number: ref.number,
  });
  const regressions = approvedRegressions(state, review);
  state.approval = { by, at: new Date().toISOString(), testedSha: pr.head.sha, baseSha: pr.base.sha, claims, regressions };
  await editComment(octokit, ref, commentId, markApproved(body, state));
  const checkId = await startTesting(octokit, ref, pr.head.sha, claims, { approvedBy: by, baseSha: pr.base.sha, regressions });

  // How the developer's review differs from what we extracted: a real-world precision signal.
  const { extraction } = state;
  const keptClaims = claims.filter((c) => c.id.startsWith("c")).length;
  const flipped = extraction.assumptions.filter((a, i) => (review.lines.get(`s${i + 1}`)?.checked ?? a.checked) !== a.checked).length;
  console.log(`[groundtruth] ${label}: approved by ${by}: ${claims.length} checks ` +
    `(kept ${keptClaims}/${extraction.claims.length} claims, ${flipped}/${extraction.assumptions.length} assumptions flipped, ` +
    `${claims.filter((c) => c.edited).length} reworded, ${regressions.length}/${state.regressions?.length ?? 0} regression checks), ` +
    `check run ${checkId} on ${pr.head.sha.slice(0, 7)}`);
}

// The developer accepted a run's tests: commit them to the PR's branch under .groundtruth/.
async function addTests(octokit: Octokit, ref: PrRef, commentId: number, body: string, state: CommentState, by: string, label: string): Promise<void> {
  const runDir = path.join(config.runsDir, state.tests!.run);
  try {
    const { sha, files } = await commitTests(octokit, ref, { runDir, approvedBy: by, runUrl: runUrl(runDir) });
    state.tests!.added = { sha, by };
    const commitUrl = `https://github.com/${ref.owner}/${ref.repo}/commit/${sha}`;
    await editComment(octokit, ref, commentId, markTestsAdded(body, state, commitUrl));
    console.log(`[groundtruth] ${label}: added ${state.tests!.files.length} tests for ${by} in ${sha.slice(0, 7)} (${files.length} files)`);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.error(`[groundtruth] ${label}: couldn't add tests: ${message}`);
    await editComment(octokit, ref, commentId, body.replace(/^(- \[)[xX](\] \*\*Add these .*<!-- gt:addtests -->)\s*$/m,
      `$1 $2\n\n⚠️ Couldn't add the tests: ${message}`));
  }
}

async function processPr(octokit: Octokit, ref: PrRef, headSha: string, label: string): Promise<void> {
  const commentId = await upsertComment(octokit, ref, renderPlaceholder());
  console.log(`[groundtruth] ${label}: placeholder posted (comment ${commentId}), extracting…`);
  const started = Date.now();
  try {
    const evidence = renderEvidence(await buildEvidence(octokit, ref));
    const { extraction, usage } = await extractClaims(evidence);
    // Tests earlier PRs added to the repo, from the base branch: offered as regression checks. All of them
    // for now; picking the ones this PR could affect comes later.
    const { data: pr } = await octokit.request("GET /repos/{owner}/{repo}/pulls/{pull_number}", { owner: ref.owner, repo: ref.repo, pull_number: ref.number });
    const registry = await readRegistry(octokit, ref, pr.base.sha).catch(() => []);
    const regressions: RegressionRow[] = registry.map((e, i) => ({
      id: `r${i + 1}`, file: e.file, title: e.title, summary: e.summary,
      from: `${e.from.pr.replace(`${ref.owner}/${ref.repo}`, "")}${e.from.title ? ` "${e.from.title}"` : ""}`,
    }));
    await editComment(octokit, ref, commentId, renderClaimsComment({
      version: 1, pr: label, headSha, extraction, regressions,
    }));
    console.log(`[groundtruth] ${label}: ${extraction.claims.length} claims, ${extraction.assumptions.length} assumptions, ` +
      `${regressions.length} regression checks in ${Math.round((Date.now() - started) / 1000)}s (${usage.input_tokens} in / ${usage.output_tokens} out tokens)`);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.error(`[groundtruth] ${label}: extraction failed: ${message}`);
    await editComment(octokit, ref, commentId, renderError(message));
  }
}
