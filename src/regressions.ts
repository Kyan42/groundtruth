import { copyFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import type { Octokit } from "@octokit/core";
import type { RegressionRow } from "./comment.js";
import type { TestEntry } from "./compile/compile.js";
import { runTests, type RanTest } from "./compile/replay.js";
import type { PrRef } from "./evidence.js";
import { fetchTests, readRegistry } from "./registry.js";

// Regression checks: replays tests that earlier PRs added to the repo (read from the base commit, so this
// PR can't edit them) against this PR's app. A test that passed when it was added and fails now means the
// PR broke something that used to work, unless the developer said the change is intended (by deleting
// the row before approving). Results go to <run>/regressions.json, videos to <run>/regression-*.webm.

export type RegressionResult = {
  id: string;              // r1, r2, ...
  file: string;
  title: string;
  summary: string;
  from: string;
  status: "passed" | "failed" | "error";
  tests: (Omit<RanTest, "file" | "video"> & { video?: string })[];   // video: file name inside the run folder
  checks: { check: string; test: string; passed: boolean; t: number; title: string; code: string }[];
  error?: string;
};

export async function runRegressions(opts: {
  octokit: Octokit; ref: PrRef; baseSha: string; rows: RegressionRow[];
  runDir: string; baseUrl: string; headers?: Record<string, string>; resetApp?: () => Promise<void>;
}): Promise<RegressionResult[]> {
  const registry = await readRegistry(opts.octokit, opts.ref, opts.baseSha);
  const entries = opts.rows.map((r) => registry.find((e) => e.file === r.file)).filter((e) => e !== undefined);
  const dir = path.join(opts.runDir, "regression");
  await fetchTests(opts.octokit, opts.ref, opts.baseSha, entries, dir);
  return replayRegressions({ ...opts, dir, entries });
}

// Replays regression tests already on disk (dir/tests + dir/support) and records the results.
export async function replayRegressions(opts: {
  dir: string; entries: TestEntry[]; rows: RegressionRow[];
  runDir: string; baseUrl: string; headers?: Record<string, string>; resetApp?: () => Promise<void>;
}): Promise<RegressionResult[]> {
  const { dir, entries } = opts;
  const ran = await runTests({
    dir, files: entries.map((e) => e.file), outputDir: path.join(opts.runDir, "regression-out"),
    baseUrl: opts.baseUrl, headers: opts.headers, resetApp: opts.resetApp,
    warmPaths: entries.flatMap((e) => e.pages),
  });

  const results: RegressionResult[] = opts.rows.map((row) => {
    const entry = entries.find((e) => e.file === row.file);
    const base = { id: row.id, file: row.file, title: row.title, summary: row.summary, from: row.from };
    if (!entry) return { ...base, status: "error", tests: [], checks: [], error: "the test is no longer in the repo" };
    const tests = ran.tests.filter((t) => t.file === row.file).map((t, i) => {
      const { file: _file, video, ...rest } = t;
      const name = video ? `regression-${row.id}${i ? `-${i + 1}` : ""}.webm` : undefined;
      if (video && name) copyFileSync(video, path.join(opts.runDir, name));
      return { ...rest, video: name };
    });
    const checks = ran.checks.filter((c) => c.file === row.file).map((c) => ({
      check: c.check, test: c.test, passed: c.passed, t: c.t,
      title: entry.checks[c.check]?.title ?? c.check, code: entry.checks[c.check]?.code ?? "",
    }));
    // A test that couldn't even reset the app's data says nothing about the PR: that's our problem, not a regression.
    const infra = tests.find((t) => t.error?.includes("Resetting the app's data failed"))?.error;
    const status = !tests.length || infra ? "error"
      : tests.every((t) => t.status === "passed") && checks.every((c) => c.passed) ? "passed" : "failed";
    return { ...base, status, tests, checks, error: !tests.length ? (ran.error ?? "the test didn't run") : infra ? "the app's data couldn't be reset" : undefined };
  });
  writeFileSync(path.join(opts.runDir, "regressions.json"), JSON.stringify(results, null, 2));
  return results;
}
