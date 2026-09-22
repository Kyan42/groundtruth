// Usage: npm run evidence -- owner/repo#123 [--diff]   (or a PR URL)
// Prints the intent evidence bundle exactly as claim extraction will see it; a summary goes to stderr.
// --diff also prints the filtered code diff (not shown to claim extraction).
import { Octokit } from "@octokit/core";
import { fetchDiff, renderDiff } from "../diff.js";
import { buildEvidence, parsePrRef, renderEvidence, summarizeEvidence } from "../evidence.js";
import { githubToken } from "./github-token.js";

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
