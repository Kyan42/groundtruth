import { App } from "@octokit/app";
import { config } from "./config.js";
import { buildEvidence, renderEvidence, summarizeEvidence } from "./evidence.js";

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

// Build the evidence bundle and report what was gathered. Claim extraction goes here next.
app.webhooks.on(["pull_request.opened", "pull_request.reopened"], async ({ octokit, payload }) => {
  const { repository, pull_request: pr } = payload;
  console.log(`[groundtruth] PR #${pr.number} "${pr.title}" on ${repository.full_name} (head ${pr.head.sha.slice(0, 7)})`);

  const evidence = await buildEvidence(octokit, { owner: repository.owner.login, repo: repository.name, number: pr.number });
  const summary = summarizeEvidence(evidence, renderEvidence(evidence));
  console.log(`[groundtruth] evidence:`, summary);

  await octokit.request("POST /repos/{owner}/{repo}/issues/{issue_number}/comments", {
    owner: repository.owner.login,
    repo: repository.name,
    issue_number: pr.number,
    body: [
      `👋 **Groundtruth** gathered evidence for \`${pr.head.sha.slice(0, 7)}\`:`,
      "",
      `- Description: ${evidence.body ? "yes" : "**empty**"}`,
      `- Linked issues: ${summary.linkedIssues}`,
      `- Commits: ${summary.commits}`,
      `- Size: ~${summary.approxTokens.toLocaleString()} tokens`,
      "",
      "_Claim extraction isn't wired up yet._",
    ].join("\n"),
  });
  console.log(`[groundtruth] commented on PR #${pr.number}`);
});
