import { getGithubSyncEnvironment } from "@/lib/config/env";
import type { Run } from "@/lib/domain/schemas";
import {
  buildDashboardPayload,
  type DashboardClaim,
  type DashboardPayload,
} from "@/lib/views/build-dashboard-payload";
import { getPrCommentIndex, pullRequestCommentKey } from "@/lib/persistence/pr-comment-index";

const STATUS_CONTEXT = "groundtruth/verify";
const MAX_STATUS_DESCRIPTION_LENGTH = 140;

type CommitStatusState = "success" | "failure";

const GITHUB_HEADERS = {
  Accept: "application/vnd.github+json",
  "User-Agent": "groundtruth-prototype",
  "X-GitHub-Api-Version": "2022-11-28",
};

/**
 * Posts (or edits) the PR result comment and the groundtruth/verify commit
 * status for a run that has just finished a browser verification attempt.
 *
 * Best-effort by design: GitHub being unreachable, rate-limited, or
 * unconfigured (no GITHUB_TOKEN/GITHUB_REPO) must never fail the run itself,
 * so every error is caught and logged here rather than propagated.
 */
export async function syncPullRequestResult(run: Run): Promise<void> {
  const environment = getGithubSyncEnvironment();
  if (!environment) {
    return;
  }
  try {
    const payload = buildDashboardPayload(run);
    const state = overallState(payload.claims);
    const { owner, name: repo } = run.repository;
    const { number: pullNumber, headSha } = run.pullRequest;

    const dashboardUrl = `${environment.dashboardBaseUrl}/runs/${run.id}`;
    await upsertResultComment(environment.token, owner, repo, pullNumber, payload, dashboardUrl);
    await postCommitStatus(
      environment.token,
      owner,
      repo,
      headSha,
      state,
      statusDescription(payload.claims, state),
      dashboardUrl,
    );
  } catch (error) {
    console.error("Failed to sync the run result to GitHub.", error);
  }
}

function overallState(claims: DashboardClaim[]): CommitStatusState {
  return claims.some((claim) => claim.verdict === "f") ? "failure" : "success";
}

function statusDescription(claims: DashboardClaim[], state: CommitStatusState): string {
  const failed = claims.filter((claim) => claim.verdict === "f");
  if (state === "failure") {
    const description =
      failed.length === 1
        ? `${failed[0].id} does not do what the description says`
        : `${failed.length} claims do not match the description`;
    return truncate(description, MAX_STATUS_DESCRIPTION_LENGTH);
  }
  const verified = claims.filter((claim) => claim.verdict === "p").length;
  return truncate(`${verified} of ${claims.length} claims verified`, MAX_STATUS_DESCRIPTION_LENGTH);
}

function truncate(value: string, maxLength: number): string {
  return value.length <= maxLength ? value : `${value.slice(0, maxLength - 1)}…`;
}

function verdictGlyph(verdict: DashboardClaim["verdict"]): string {
  return verdict === "p" ? "✓" : verdict === "f" ? "✗" : "?";
}

function stripHtml(value: string): string {
  return value.replace(/<[^>]+>/g, "");
}

export function buildResultCommentBody(payload: DashboardPayload, dashboardUrl: string): string {
  const verified = payload.claims.filter((claim) => claim.verdict === "p").length;
  const failed = payload.claims.filter((claim) => claim.verdict === "f").length;
  const unverified = payload.claims.length - verified - failed;

  const summaryParts = [`**${verified} of ${payload.claims.length} claims verified.**`];
  if (failed > 0) {
    summaryParts.push(`${failed} does not match your description.`);
  }
  if (unverified > 0) {
    summaryParts.push(`${unverified} could not be verified.`);
  }

  const rows = payload.claims.map((claim) => {
    const glyph = verdictGlyph(claim.verdict);
    const detail = stripHtml(claim.verdict_line);
    return `- ${glyph} **${claim.id}** ${stripHtml(claim.text)}\n  ${detail}`;
  });

  return [
    "**groundtruth**",
    "",
    summaryParts.join(" "),
    "",
    ...rows,
    "",
    `[Open the dashboard](${dashboardUrl})`,
  ].join("\n");
}

async function upsertResultComment(
  token: string,
  owner: string,
  repo: string,
  pullNumber: number,
  payload: DashboardPayload,
  dashboardUrl: string,
): Promise<void> {
  const key = pullRequestCommentKey(owner, repo, pullNumber);
  const index = getPrCommentIndex();
  const existingId = await index.get(key);
  const body = buildResultCommentBody(payload, dashboardUrl);

  if (existingId) {
    const response = await githubFetch(
      `https://api.github.com/repos/${owner}/${repo}/issues/comments/${existingId}`,
      token,
      { method: "PATCH", body: JSON.stringify({ body }) },
    );
    if (response.ok) {
      return;
    }
    // The tracked comment may have been deleted on GitHub; fall through and
    // post a fresh one rather than failing the whole sync.
    console.error(
      `Could not edit PR comment ${existingId} (status ${response.status}); posting a new one.`,
    );
  }

  const response = await githubFetch(
    `https://api.github.com/repos/${owner}/${repo}/issues/${pullNumber}/comments`,
    token,
    { method: "POST", body: JSON.stringify({ body }) },
  );
  if (!response.ok) {
    throw new Error(`GitHub comment POST failed with status ${response.status}.`);
  }
  const created = (await response.json()) as { id: number };
  await index.set(key, created.id);
}

async function postCommitStatus(
  token: string,
  owner: string,
  repo: string,
  headSha: string,
  state: CommitStatusState,
  description: string,
  targetUrl: string,
): Promise<void> {
  const response = await githubFetch(
    `https://api.github.com/repos/${owner}/${repo}/statuses/${headSha}`,
    token,
    {
      method: "POST",
      body: JSON.stringify({
        state,
        context: STATUS_CONTEXT,
        description,
        target_url: targetUrl,
      }),
    },
  );
  if (!response.ok) {
    throw new Error(`GitHub status POST failed with status ${response.status}.`);
  }
}

async function githubFetch(
  url: string,
  token: string,
  init: { method: string; body: string },
): Promise<Response> {
  return fetch(url, {
    ...init,
    headers: {
      ...GITHUB_HEADERS,
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
  });
}
