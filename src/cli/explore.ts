// Usage: npm run explore -- owner/repo#123 [--claim 1] [--model claude-opus-5] [--max-turns 40]
// Boots the PR's app, then has the exploring agent check the approved claims from the PR's Groundtruth
// comment (or just one with --claim N), printing every step. Saves trace.json and a video under explore-runs/.
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { parseArgs } from "node:util";
import type { Octokit } from "@octokit/core";
import { bootPr } from "../boot/boot.js";
import { type ApprovedClaim, COMMENT_MARKER, readState } from "../comment.js";
import { config } from "../config.js";
import { listAll, parsePrRef, type PrRef } from "../evidence.js";
import { DEFAULT_EXPLORE_MODEL, explore, type ExploreResult } from "../explore/agent.js";
import { ExplorerBrowser } from "../explore/browser.js";
import { installationOctokit } from "../github.js";

const { values, positionals } = parseArgs({
  allowPositionals: true,
  options: {
    claim: { type: "string" },
    model: { type: "string", default: DEFAULT_EXPLORE_MODEL },
    "max-turns": { type: "string", default: "40" },
  },
});
if (!positionals[0]) {
  console.error("Usage: npm run explore -- owner/repo#123 [--claim N] [--model ...] [--max-turns 40]");
  process.exit(1);
}
const ref = parsePrRef(positionals[0]);
const octokit = await installationOctokit(ref.owner, ref.repo);

// The claims a developer approved in our PR comment; before approval, every claim and checked assumption.
async function approvedClaims(octokit: Octokit, ref: PrRef): Promise<ApprovedClaim[]> {
  const comments = await listAll((page) => octokit.request("GET /repos/{owner}/{repo}/issues/{issue_number}/comments", {
    owner: ref.owner, repo: ref.repo, issue_number: ref.number, per_page: 100, page,
  }).then((r) => r.data));
  const ours = comments.find((c) => Number(c.performed_via_github_app?.id) === Number(config.appId) && c.body?.startsWith(COMMENT_MARKER));
  const state = ours?.body ? readState(ours.body) : undefined;
  if (!state) throw new Error("No Groundtruth claims comment on this PR yet");
  if (state.approval) return state.approval.claims;
  return [
    ...state.extraction.claims.map((c, i) => ({ id: `c${i + 1}`, when: c.when, then: c.then, edited: false })),
    ...state.extraction.assumptions.flatMap((a, i) => (a.checked ? [{ id: `s${i + 1}`, when: a.when, then: a.then, edited: false }] : [])),
  ];
}

let claims = await approvedClaims(octokit, ref);
if (values.claim) {
  const i = Number(values.claim) - 1;
  if (!claims[i]) throw new Error(`There are ${claims.length} claims; --claim must be 1-${claims.length}`);
  claims = [claims[i]];
}
console.log(`Claims to check (${claims.length}):`);
for (const c of claims) console.log(`  ${c.id}: ${c.when ? `${c.when} → ` : ""}${c.then.what}`);

const outDir = path.join("explore-runs", `${ref.repo}-${ref.number}-${new Date().toISOString().replace(/[:.]/g, "-")}`);
mkdirSync(outDir, { recursive: true });
const started = Date.now();
let explored: ExploreResult | undefined;
let video: string | undefined;

console.log(`\nBooting ${ref.owner}/${ref.repo}#${ref.number}…`);
const boot = await bootPr(octokit, ref, {
  screenshotPath: path.join(outDir, "boot.png"),
  onPhase: (p) => { if (!p.ok || p.name === "browser check") console.log(`  ${p.ok ? "✓" : "✗"} ${p.name} (${p.seconds.toFixed(0)}s)`); },
  afterBoot: async ({ url, headers }) => {
    console.log(`\nExploring with ${values.model}…`);
    const browser = await ExplorerBrowser.open(url, headers, outDir);
    try {
      explored = await explore({
        claims, browser, model: values.model, maxTurns: Number(values["max-turns"]),
        onEvent: (e) => {
          if (e.type === "thinking") console.log(`  💭 ${e.text.replace(/\s+/g, " ").slice(0, 220)}`);
          else if (e.type === "tool") console.log(`  ${e.ok ? "→" : "✗"} ${e.name} ${e.summary}`.slice(0, 220));
          else console.log(`  ■ ${e.result.claimId} ${e.result.status.toUpperCase()}: ${e.result.evidence}` +
            (e.result.check ? `\n      check: ${e.result.check.locator} expected "${e.result.check.expected}", observed "${e.result.check.observed}"` : ""));
        },
      });
    } finally {
      video = await browser.close();
    }
  },
});

if (!boot.ok) {
  console.log(`\nBoot error: ${boot.error}${boot.appLogTail ? `\n${boot.appLogTail}` : ""}`);
  process.exit(1);
}
const r = explored!;
writeFileSync(path.join(outDir, "trace.json"), JSON.stringify({ pr: `${ref.owner}/${ref.repo}#${ref.number}`, sha: boot.sha, claims, ...r }, null, 2));
console.log(`\nDone in ${Math.round((Date.now() - started) / 1000)}s (${r.turns} turns, ${r.steps.length} browser steps, stopped: ${r.stoppedBecause})`);
for (const c of claims) {
  const res = r.results.find((x) => x.claimId === c.id);
  console.log(`  ${c.id}: ${res ? res.status : "no status"}`);
}
console.log(`Cost: $${r.costUsd.toFixed(3)} (${r.usage.input} in, ${r.usage.cacheWrite} cache write, ${r.usage.cacheRead} cache read, ${r.usage.output} out)`);
console.log(`Trace: ${path.join(outDir, "trace.json")}${video ? ` · video: ${video}` : ""}`);
