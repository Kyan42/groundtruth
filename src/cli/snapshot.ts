// Usage: npm run evals:snapshot [-- case-id ...]
// Freezes each case's evidence, as it was when the PR was opened, into evals/evidence/<id>.txt.
// Eval runs read these files, so results don't drift if a PR is edited later,
// and you can read exactly what the extractor saw.
import { mkdirSync, writeFileSync } from "node:fs";
import { Octokit } from "@octokit/core";
import { EVIDENCE_DIR, evidencePath, loadCases } from "../evals/cases.js";
import { buildEvidence, parsePrRef, renderEvidence } from "../evidence.js";
import { githubToken } from "./github-token.js";

const octokit = new Octokit({ auth: githubToken() });
mkdirSync(EVIDENCE_DIR, { recursive: true });

for (const c of loadCases(process.argv.slice(2))) {
  const evidence = await buildEvidence(octokit, parsePrRef(c.pr), { asOf: c.snapshot.opened_at });
  writeFileSync(evidencePath(c.id), `${renderEvidence(evidence)}\n`);

  // Cross-check against the commits the case says were visible at open.
  const headlines = evidence.commits.map((m) => m.split("\n")[0]);
  const expected = c.snapshot.commits_visible;
  const ok = headlines.length === expected.length && headlines.every((h, i) => h === expected[i]);
  console.log(`${c.id}: ${evidence.commits.length} commits, head ${evidence.headSha.slice(0, 7)}` +
    (ok ? "" : `\n  MISMATCH with case snapshot.commits_visible:\n    got      ${JSON.stringify(headlines)}\n    expected ${JSON.stringify(expected)}`));
}
