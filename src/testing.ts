import type { Octokit } from "@octokit/core";
import type { ApprovedClaim } from "./comment.js";
import type { PrRef } from "./evidence.js";

// Where approved claims are handed to browser testing. Testing isn't built yet: for now this creates
// a queued GitHub Check on the PR's commit, which the sandbox will later move to running and completed
// with per-claim results.
export async function startTesting(octokit: Octokit, ref: PrRef, sha: string, claims: ApprovedClaim[]): Promise<number> {
  const list = claims.map((c, i) => `${i + 1}. ${c.when ? `${c.when} → ` : ""}${c.then.what}`).join("\n");
  const { data } = await octokit.request("POST /repos/{owner}/{repo}/check-runs", {
    owner: ref.owner,
    repo: ref.repo,
    name: "Groundtruth",
    head_sha: sha,
    status: "queued",
    output: {
      title: `${claims.length} check${claims.length === 1 ? "" : "s"} approved (testing not built yet)`,
      summary: "These checks were approved in the Groundtruth comment and will be tested in a browser once testing exists.",
      text: list,
    },
  });
  return Number(data.id);
}
