// Usage: npm run extract -- owner/repo#123 [--model claude-opus-5]
// Runs claim extraction on a live PR's current evidence and prints the result as JSON.
import { parseArgs } from "node:util";
import { Octokit } from "@octokit/core";
import { buildEvidence, parsePrRef, renderEvidence } from "../evidence.js";
import { DEFAULT_EXTRACT_MODEL, extractClaims, isGrounded } from "../extract.js";
import { githubToken } from "./github-token.js";

const { values, positionals } = parseArgs({
  allowPositionals: true,
  options: { model: { type: "string", default: DEFAULT_EXTRACT_MODEL } },
});
if (!positionals[0]) {
  console.error("Usage: npm run extract -- owner/repo#123 [--model claude-opus-5]");
  process.exit(1);
}

const evidence = renderEvidence(await buildEvidence(new Octokit({ auth: githubToken() }), parsePrRef(positionals[0])));
const { extraction, model, usage } = await extractClaims(evidence, { model: values.model });
const ungrounded = extraction.claims.filter((c) => !isGrounded(c.source, evidence));

console.log(JSON.stringify(extraction, null, 2));
console.error(`\n${model}: ${extraction.claims.length} claims, ${extraction.assumptions.length} assumptions, ` +
  `${ungrounded.length} ungrounded · ${usage.input_tokens} in / ${usage.output_tokens} out tokens`);
