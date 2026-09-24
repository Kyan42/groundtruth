import { App } from "@octokit/app";
import { Octokit } from "@octokit/core";
import { config } from "./config.js";

// The GitHub App. Webhooks are verified with the webhook secret; API calls authenticate as an
// installation, using tokens the app mints by signing with its private key.
export const githubApp = new App({
  appId: config.appId,
  privateKey: config.privateKey,
  webhooks: { secret: config.webhookSecret },
});

export async function installationOctokit(owner: string, repo: string): Promise<Octokit> {
  const { data } = await githubApp.octokit.request("GET /repos/{owner}/{repo}/installation", { owner, repo });
  return githubApp.getInstallationOctokit(data.id);
}

// A token that can only read this one repo's contents, for cloning inside a sandbox.
// Installation tokens expire after an hour; revoke it as soon as the clone is done anyway,
// because the sandbox is about to run untrusted code.
export async function readOnlyRepoToken(owner: string, repo: string): Promise<string> {
  const { data: installation } = await githubApp.octokit.request("GET /repos/{owner}/{repo}/installation", { owner, repo });
  const { data } = await githubApp.octokit.request("POST /app/installations/{installation_id}/access_tokens", {
    installation_id: installation.id,
    repositories: [repo],
    permissions: { contents: "read" },
  });
  return data.token;
}

export async function revokeToken(token: string): Promise<void> {
  await new Octokit({ auth: token }).request("DELETE /installation/token");
}
