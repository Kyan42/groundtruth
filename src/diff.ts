import type { Octokit } from "@octokit/core";
import { listAll, type PrRef } from "./evidence.js";

// The PR's code diff, filtered and size-capped. Not used for claim extraction (see evidence.ts);
// kept for the regression step, which needs to know which files and surfaces a PR touched.

export type DiffFile = {
  path: string;
  status: string;
  additions: number;
  deletions: number;
  patch?: string;
  omitted?: string; // why the patch was left out
};

const MAX_PATCH_CHARS_PER_FILE = 12_000;
const MAX_PATCH_CHARS_TOTAL = 60_000;

const LOCKFILES = new Set([
  "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "bun.lock",
  "Gemfile.lock", "poetry.lock", "Pipfile.lock", "uv.lock", "Cargo.lock",
  "go.sum", "composer.lock", "mix.lock", "pubspec.lock",
]);

function omitReason(path: string): string | undefined {
  const name = path.split("/").pop()!;
  if (LOCKFILES.has(name)) return "lockfile";
  if (/\.(min\.(js|css)|map)$/.test(name)) return "minified/sourcemap";
  if (/(^|\/)(dist|build|vendor|node_modules)\//.test(path)) return "generated/vendored";
  if (/__snapshots__\/|\.snap$/.test(path)) return "test snapshot";
  return undefined;
}

export async function fetchDiff(octokit: Octokit, { owner, repo, number }: PrRef): Promise<DiffFile[]> {
  const files = await listAll((page) =>
    octokit.request("GET /repos/{owner}/{repo}/pulls/{pull_number}/files", { owner, repo, pull_number: number, per_page: 100, page })
      .then((r) => r.data));

  let patchBudget = MAX_PATCH_CHARS_TOTAL;
  return files.map((f) => {
    const base = { path: f.filename, status: f.status, additions: f.additions, deletions: f.deletions };
    const reason =
      omitReason(f.filename) ??
      (!f.patch ? "no text diff (binary or too large for GitHub)" : undefined) ??
      (f.patch!.length > MAX_PATCH_CHARS_PER_FILE ? `patch too large (${f.patch!.length} chars)` : undefined) ??
      (f.patch!.length > patchBudget ? "total diff budget exhausted" : undefined);
    if (reason) return { ...base, omitted: reason };
    patchBudget -= f.patch!.length;
    return { ...base, patch: f.patch };
  });
}

export function renderDiff(files: DiffFile[]): string {
  const rendered = files.map((f) => {
    const attrs = `path="${f.path}" status="${f.status}" additions="${f.additions}" deletions="${f.deletions}"`;
    return f.patch ? `<file ${attrs}>\n${f.patch}\n</file>` : `<file ${attrs} omitted="${f.omitted}" />`;
  });
  return `<diff>\n${rendered.join("\n")}\n</diff>`;
}
