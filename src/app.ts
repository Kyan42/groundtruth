import { App } from "@octokit/app";
import type { Octokit } from "@octokit/core";
import {
  approvedClaims, COMMENT_MARKER, markApproved, parseReview, readState, renderClaimsComment, renderError,
  renderPlaceholder,
} from "./comment.js";
import { config } from "./config.js";
import { buildEvidence, listAll, type PrRef, renderEvidence } from "./evidence.js";
import { extractClaims } from "./extract.js";
import { createLimiter } from "./limit.js";
import { startTesting } from "./testing.js";

export const app = new App({
  appId: config.appId,
  privateKey: config.privateKey,
  webhooks: { secret: config.webhookSecret },
});

// Caps concurrent claim extractions so a burst of PRs doesn't exceed Claude API rate limits.
const extractions = createLimiter(config.extractConcurrency);

// Log every delivery so it's obvious what GitHub is sending us.
app.webhooks.onAny(({ id, name, payload }) => {
  const action = "action" in payload ? `.${payload.action}` : "";
  console.log(`[webhook] ${name}${action} (delivery ${id})`);
});

// Respond to GitHub right away (it waits at most 10s); the work happens in the background.
app.webhooks.on(["pull_request.opened", "pull_request.reopened"], ({ octokit, payload }) => {
  const { repository, pull_request: pr } = payload;
  const ref: PrRef = { owner: repository.owner.login, repo: repository.name, number: pr.number };
  const label = `${repository.full_name}#${pr.number}`;
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
  if (state.approval) {
    console.log(`[groundtruth] ${label}: edited by ${by} after approval; ignored`);
    return;
  }
  const review = parseReview(body);
  if (!review.approveChecked) {
    console.log(`[groundtruth] ${label}: edited by ${by}, not approved yet`);
    return;
  }

  const claims = approvedClaims(state, review);
  const { data: pr } = await octokit.request("GET /repos/{owner}/{repo}/pulls/{pull_number}", {
    owner: ref.owner, repo: ref.repo, pull_number: ref.number,
  });
  state.approval = { by, at: new Date().toISOString(), testedSha: pr.head.sha, claims };
  await editComment(octokit, ref, commentId, markApproved(body, state));
  const checkId = await startTesting(octokit, ref, pr.head.sha, claims);

  // How the developer's review differs from what we extracted: a real-world precision signal.
  const { extraction } = state;
  const keptClaims = claims.filter((c) => c.id.startsWith("c")).length;
  const flipped = extraction.assumptions.filter((a, i) => (review.lines.get(`s${i + 1}`)?.checked ?? a.checked) !== a.checked).length;
  console.log(`[groundtruth] ${label}: approved by ${by}: ${claims.length} checks ` +
    `(kept ${keptClaims}/${extraction.claims.length} claims, ${flipped}/${extraction.assumptions.length} assumptions flipped, ` +
    `${claims.filter((c) => c.edited).length} reworded), check run ${checkId} on ${pr.head.sha.slice(0, 7)}`);
}

async function processPr(octokit: Octokit, ref: PrRef, headSha: string, label: string): Promise<void> {
  const commentId = await upsertComment(octokit, ref, renderPlaceholder());
  console.log(`[groundtruth] ${label}: placeholder posted (comment ${commentId}), extracting…`);
  const started = Date.now();
  try {
    const evidence = renderEvidence(await buildEvidence(octokit, ref));
    const { extraction, usage } = await extractClaims(evidence);
    await editComment(octokit, ref, commentId, renderClaimsComment({
      version: 1, pr: label, headSha, extraction,
    }));
    console.log(`[groundtruth] ${label}: ${extraction.claims.length} claims, ${extraction.assumptions.length} assumptions ` +
      `in ${Math.round((Date.now() - started) / 1000)}s (${usage.input_tokens} in / ${usage.output_tokens} out tokens)`);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.error(`[groundtruth] ${label}: extraction failed: ${message}`);
    await editComment(octokit, ref, commentId, renderError(message));
  }
}

// Reuses our existing comment on the PR (e.g. on reopen) instead of posting a second one.
async function upsertComment(octokit: Octokit, ref: PrRef, body: string): Promise<number> {
  const comments = await listAll((page) =>
    octokit.request("GET /repos/{owner}/{repo}/issues/{issue_number}/comments", {
      owner: ref.owner, repo: ref.repo, issue_number: ref.number, per_page: 100, page,
    }).then((r) => r.data));
  const ours = comments.find((c) =>
    Number(c.performed_via_github_app?.id) === Number(config.appId) && c.body?.startsWith(COMMENT_MARKER));
  if (ours) {
    await editComment(octokit, ref, Number(ours.id), body);
    return Number(ours.id);
  }
  const { data } = await octokit.request("POST /repos/{owner}/{repo}/issues/{issue_number}/comments", {
    owner: ref.owner, repo: ref.repo, issue_number: ref.number, body,
  });
  return Number(data.id);
}

async function editComment(octokit: Octokit, ref: PrRef, commentId: number, body: string): Promise<void> {
  await octokit.request("PATCH /repos/{owner}/{repo}/issues/comments/{comment_id}", {
    owner: ref.owner, repo: ref.repo, comment_id: commentId, body,
  });
}
