import type { Octokit } from "@octokit/core";
import { COMMENT_MARKER } from "./comment.js";
import { config } from "./config.js";
import { listAll, type PrRef } from "./evidence.js";

// Reading and writing Groundtruth's comment on a PR (one per PR, found by our App id and marker).

export async function findComment(octokit: Octokit, ref: PrRef): Promise<{ id: number; body: string } | undefined> {
  const comments = await listAll((page) =>
    octokit.request("GET /repos/{owner}/{repo}/issues/{issue_number}/comments", {
      owner: ref.owner, repo: ref.repo, issue_number: ref.number, per_page: 100, page,
    }).then((r) => r.data));
  const ours = comments.find((c) =>
    Number(c.performed_via_github_app?.id) === Number(config.appId) && c.body?.startsWith(COMMENT_MARKER));
  return ours ? { id: Number(ours.id), body: ours.body ?? "" } : undefined;
}

// Reuses our existing comment on the PR (e.g. on reopen) instead of posting a second one.
export async function upsertComment(octokit: Octokit, ref: PrRef, body: string): Promise<number> {
  const ours = await findComment(octokit, ref);
  if (ours) {
    await editComment(octokit, ref, ours.id, body);
    return ours.id;
  }
  const { data } = await octokit.request("POST /repos/{owner}/{repo}/issues/{issue_number}/comments", {
    owner: ref.owner, repo: ref.repo, issue_number: ref.number, body,
  });
  return Number(data.id);
}

export async function editComment(octokit: Octokit, ref: PrRef, commentId: number, body: string): Promise<void> {
  await octokit.request("PATCH /repos/{owner}/{repo}/issues/comments/{comment_id}", {
    owner: ref.owner, repo: ref.repo, comment_id: commentId, body,
  });
}
