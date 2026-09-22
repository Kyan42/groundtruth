import { App } from "@octokit/app";
import { config } from "./config.js";

export const app = new App({
  appId: config.appId,
  privateKey: config.privateKey,
  webhooks: { secret: config.webhookSecret },
});

// Log every delivery so it's obvious what GitHub is sending us.
app.webhooks.onAny(({ id, name, payload }) => {
  const action = "action" in payload ? `.${payload.action}` : "";
  console.log(`[webhook] ${name}${action} (delivery ${id})`);
});

// First slice: acknowledge new PRs with a comment. Claim extraction goes here later.
app.webhooks.on(["pull_request.opened", "pull_request.reopened"], async ({ octokit, payload }) => {
  const { repository, pull_request: pr } = payload;
  console.log(`[groundtruth] PR #${pr.number} "${pr.title}" on ${repository.full_name} (head ${pr.head.sha.slice(0, 7)})`);

  await octokit.request("POST /repos/{owner}/{repo}/issues/{issue_number}/comments", {
    owner: repository.owner.login,
    repo: repository.name,
    issue_number: pr.number,
    body: [
      "👋 **Groundtruth** received this PR.",
      "",
      `- Title: ${pr.title}`,
      `- Head: \`${pr.head.sha.slice(0, 7)}\``,
      `- Changed files: ${pr.changed_files}`,
      "",
      "_Claim extraction isn't wired up yet — this is a connectivity check._",
    ].join("\n"),
  });
  console.log(`[groundtruth] commented on PR #${pr.number}`);
});
