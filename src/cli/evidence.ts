// Usage: npm run evidence -- owner/repo#123 [--diff]   (or a PR URL)
// Prints the intent evidence bundle exactly as claim extraction will see it; a summary goes to stderr.
// --diff also prints the filtered code diff (not shown to claim extraction).
import { execFileSync } from "node:child_process";
import { Octokit } from "@octokit/core";
import { fetchDiff, renderDiff } from "../diff.js";
import { buildEvidence, parsePrRef, renderEvidence, summarizeEvidence } from "../evidence.js";

function githubToken(): string {
  if (process.env.GITHUB_TOKEN) return process.env.GITHUB_TOKEN;
  try {
    return execFileSync("gh", ["auth", "token"], { encoding: "utf8" }).trim();
  } catch {
    throw new Error("Set GITHUB_TOKEN or log in with `gh auth login`");
  }
}

const args = process.argv.slice(2);
const ref = args.find((a) => !a.startsWith("--"));
if (!ref) {
  console.error("Usage: npm run evidence -- owner/repo#123 [--diff]");
  process.exit(1);
}

const octokit = new Octokit({ auth: githubToken() });
const prRef = parsePrRef(ref);
const evidence = await buildEvidence(octokit, prRef);
const rendered = renderEvidence(evidence);
console.log(rendered);
if (args.includes("--diff")) console.log(`\n${renderDiff(await fetchDiff(octokit, prRef))}`);
console.error("\n--- summary ---");
console.error(summarizeEvidence(evidence, rendered));
