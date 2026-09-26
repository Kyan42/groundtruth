import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { Octokit } from "@octokit/core";
import type { TestEntry } from "./compile/compile.js";
import type { PrRef } from "./evidence.js";

// The repo's Groundtruth tests: .groundtruth/tests/*.spec.ts with a manifest (tests/index.json), added to
// PRs when developers accept a run's tests. Later PRs replay them as regression checks. The support files
// (helpers, video overlay, config) are Groundtruth's own and are always taken from this server, never
// from the repo, so a PR can't change how its checks are run.

export const REGISTRY_DIR = ".groundtruth";
const TEMPLATE = (name: string) => fileURLToPath(new URL(`./compile/template/${name}`, import.meta.url));
const SUPPORT = ["groundtruth.ts", "overlay.js", "playwright.config.ts"];

type Repo = { owner: string; repo: string };

// The tests in the repo at a branch or commit (none if it has no manifest yet).
export async function readRegistry(octokit: Octokit, repo: Repo, gitRef: string): Promise<TestEntry[]> {
  const text = await readFile(octokit, repo, `${REGISTRY_DIR}/tests/index.json`, gitRef);
  if (!text) return [];
  try { return JSON.parse(text) as TestEntry[]; } catch { return []; }
}

// Writes the given test files (and our support files) into dir/tests and dir/support, ready for runTests.
export async function fetchTests(octokit: Octokit, repo: Repo, gitRef: string, entries: TestEntry[], dir: string): Promise<void> {
  mkdirSync(path.join(dir, "tests"), { recursive: true });
  mkdirSync(path.join(dir, "support"), { recursive: true });
  for (const e of entries) {
    const text = await readFile(octokit, repo, `${REGISTRY_DIR}/tests/${e.file}`, gitRef);
    if (text === undefined) throw new Error(`${REGISTRY_DIR}/tests/${e.file} is listed in index.json but missing`);
    writeFileSync(path.join(dir, "tests", e.file), text);
  }
  writeFileSync(path.join(dir, "tests", "index.json"), JSON.stringify(entries, null, 2));
  for (const name of SUPPORT) writeFileSync(path.join(dir, "support", name), readFileSync(TEMPLATE(name)));
}

// Commits a run's compiled tests (runDir/scripts) to the PR's branch as one commit, merging its manifest
// entries into the repo's. Files from other PRs with the same name are left alone (ours get a suffix).
// Returns the new commit's sha.
export async function commitTests(octokit: Octokit, ref: PrRef, opts: { runDir: string; approvedBy: string; runUrl: string }): Promise<{ sha: string; files: string[] }> {
  const { data: pr } = await octokit.request("GET /repos/{owner}/{repo}/pulls/{pull_number}", { owner: ref.owner, repo: ref.repo, pull_number: ref.number });
  if (pr.head.repo?.full_name !== pr.base.repo.full_name) {
    throw new Error("this PR comes from a fork, and Groundtruth can't push to forks");
  }
  const branch = pr.head.ref;
  const scripts = path.join(opts.runDir, "scripts");
  const ours = JSON.parse(readFileSync(path.join(scripts, "tests", "index.json"), "utf8")) as TestEntry[];
  const existing = await readRegistry(octokit, ref, pr.head.sha);

  const files: { path: string; content: string }[] = [];
  const merged = existing.filter((e) => e.from.pr !== ours[0]?.from.pr);   // a PR's newer tests replace its older ones
  const taken = new Set(merged.map((e) => e.file));
  for (const e of ours) {
    let file = e.file;
    for (let n = 2; taken.has(file); n++) file = e.file.replace(/\.spec\.ts$/, `-${n}.spec.ts`);
    taken.add(file);
    merged.push({ ...e, file });
    files.push({ path: `${REGISTRY_DIR}/tests/${file}`, content: readFileSync(path.join(scripts, "tests", e.file), "utf8") });
  }
  files.push({ path: `${REGISTRY_DIR}/tests/index.json`, content: `${JSON.stringify(merged, null, 2)}\n` });
  for (const name of SUPPORT) files.push({ path: `${REGISTRY_DIR}/support/${name}`, content: readFileSync(TEMPLATE(name), "utf8") });
  files.push({ path: `${REGISTRY_DIR}/.gitignore`, content: "test-results/\n" });
  if (files.some((f) => !f.path.startsWith(`${REGISTRY_DIR}/`))) throw new Error("refusing to write outside .groundtruth/");

  // Git Data API: a new tree on top of the branch's head, one commit, then move the branch (fast-forward only).
  const repo = { owner: ref.owner, repo: ref.repo };
  const { data: head } = await octokit.request("GET /repos/{owner}/{repo}/git/commits/{commit_sha}", { ...repo, commit_sha: pr.head.sha });
  const { data: tree } = await octokit.request("POST /repos/{owner}/{repo}/git/trees", {
    ...repo, base_tree: head.tree.sha,
    tree: files.map((f) => ({ path: f.path, mode: "100644" as const, type: "blob" as const, content: f.content })),
  });
  const list = ours.map((e) => `- ${e.title}`).join("\n");
  const { data: commit } = await octokit.request("POST /repos/{owner}/{repo}/git/commits", {
    ...repo, tree: tree.sha, parents: [pr.head.sha],
    message: `Add Groundtruth tests for #${ref.number}\n\n${list}\n\nVerified and replayed by Groundtruth (${opts.runUrl}); added by @${opts.approvedBy}.`,
  });
  await octokit.request("PATCH /repos/{owner}/{repo}/git/refs/{ref}", { ...repo, ref: `heads/${branch}`, sha: commit.sha, force: false });
  return { sha: commit.sha, files: files.map((f) => f.path) };
}

async function readFile(octokit: Octokit, repo: Repo, filePath: string, gitRef: string): Promise<string | undefined> {
  try {
    const { data } = await octokit.request("GET /repos/{owner}/{repo}/contents/{path}", { ...repo, path: filePath, ref: gitRef });
    if (Array.isArray(data) || data.type !== "file" || !("content" in data)) return undefined;
    return Buffer.from(data.content, "base64").toString("utf8");
  } catch (err) {
    if ((err as { status?: number }).status === 404) return undefined;
    throw err;
  }
}

