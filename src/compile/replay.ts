import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { copyFileSync, createWriteStream, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { createRequire } from "node:module";
import path from "node:path";
import { chromium } from "playwright";
import { compileRun, type TestEntry, type Trace } from "./compile.js";

// Runs compiled Groundtruth tests (tests/*.spec.ts + support/, see compile.ts) against an app with
// Playwright's own test runner: no model calls, one video per test. Used twice per approval: to replay
// the run's own compiled tests (the clean record of the run), and to replay tests from earlier PRs
// (regressions).

export type RanTest = {
  file: string; title: string;
  status: "passed" | "failed" | "timedOut" | "skipped" | "interrupted";
  seconds: number;
  video?: string;          // absolute path in the runner's output folder
  startsAt?: number;       // seconds into the video where the app appears (after the first page load)
  error?: string;
};
export type RanCheck = { file: string; test: string; check: string; passed: boolean; t: number };

const require = createRequire(import.meta.url);
const PLAYWRIGHT_CLI = path.join(path.dirname(require.resolve("playwright/package.json")), "cli.js");

export async function runTests(opts: {
  dir: string;                 // holds tests/ and support/
  files?: string[];            // test files to run (default: all)
  outputDir: string;
  baseUrl: string;
  headers?: Record<string, string>;
  resetApp?: () => Promise<void>;
  warmPaths?: string[];        // pages to request once first (dev servers compile a page on its first visit)
  timeoutSeconds?: number;
}): Promise<{ tests: RanTest[]; checks: RanCheck[]; error?: string }> {
  const checksFile = path.join(opts.outputDir, "checks.jsonl");
  rmSync(opts.outputDir, { recursive: true, force: true });
  mkdirSync(opts.outputDir, { recursive: true });

  // A dev server can take longer than a check's retry window to compile a page it hasn't served yet, and
  // clicks made before the page's JavaScript has loaded only take effect once it has. Either would read as a
  // failure. Load each page the tests visit once in a real browser (HTML and scripts), so it's all compiled.
  const warm = [...new Set(opts.warmPaths ?? [])];
  if (warm.length) {
    const browser = await chromium.launch();
    try {
      const page = await browser.newPage({ extraHTTPHeaders: opts.headers });
      for (const p of warm) {
        await page.goto(new URL(p, opts.baseUrl).toString(), { waitUntil: "load", timeout: 90_000 }).catch(() => {});
        await page.waitForLoadState("networkidle", { timeout: 30_000 }).catch(() => {});
      }
    } finally {
      await browser.close();
    }
  }

  // The tests run in a separate process; they reach resetApp through a one-off local endpoint.
  const token = randomBytes(12).toString("hex");
  const resetServer = createServer(async (req, res) => {
    if (req.method !== "POST" || req.url !== `/${token}/reset`) { res.writeHead(404).end(); return; }
    if (!opts.resetApp) { res.writeHead(501).end("This app has no reset command"); return; }
    try { await opts.resetApp(); res.writeHead(200).end("ok"); }
    catch (err) { res.writeHead(500).end(String(err)); }
  });
  await new Promise<void>((resolve) => resetServer.listen(0, "127.0.0.1", resolve));
  const resetUrl = `http://127.0.0.1:${(resetServer.address() as AddressInfo).port}/${token}/reset`;

  let error: string | undefined;
  try {
    const log = createWriteStream(path.join(opts.outputDir, "runner.log"));
    const filters = (opts.files ?? []).map((f) => f.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    const child = spawn(process.execPath, [PLAYWRIGHT_CLI, "test", "-c", path.join(opts.dir, "support", "playwright.config.ts"), ...filters], {
      cwd: process.cwd(),   // so the tests resolve playwright from Groundtruth's node_modules
      env: {
        ...process.env,
        GROUNDTRUTH_BASE_URL: opts.baseUrl,
        GROUNDTRUTH_HEADERS: JSON.stringify(opts.headers ?? {}),
        GROUNDTRUTH_RESET_URL: resetUrl,
        GROUNDTRUTH_CHECKS_FILE: checksFile,
        GROUNDTRUTH_OUTPUT: path.resolve(opts.outputDir, "results"),
        FORCE_COLOR: "0",
      },
    });
    child.stdout.pipe(log);
    child.stderr.pipe(log);
    const timer = setTimeout(() => child.kill(), (opts.timeoutSeconds ?? 600) * 1000);
    const code = await new Promise<number | null>((resolve) => child.on("close", resolve));
    clearTimeout(timer);
    if (code === null) error = "the tests timed out";
  } finally {
    resetServer.close();
  }

  const tests = readResults(path.join(opts.outputDir, "results", "results.json"));
  if (!tests.length && !error) error = "the test runner produced no results (see runner.log)";
  const checks: RanCheck[] = [];
  if (existsSync(checksFile)) {
    for (const line of readFileSync(checksFile, "utf8").split("\n").filter(Boolean)) {
      const c = JSON.parse(line) as { file: string; test: string; check?: string; event?: string; passed?: boolean; t: number };
      if (c.event === "loaded") { const t = tests.find((x) => x.file === c.file && x.title === c.test); if (t) t.startsAt = c.t; }
      else if (c.check) checks.push({ file: c.file, test: c.test, check: c.check, passed: Boolean(c.passed), t: c.t });
    }
  }
  return { tests, checks, error };
}

// Playwright's JSON report → one entry per test, in the order the files and tests appear.
function readResults(resultsFile: string): RanTest[] {
  if (!existsSync(resultsFile)) return [];
  type Spec = { title: string; file: string; tests: { results: { status: RanTest["status"]; duration: number; errors?: { message?: string }[]; attachments?: { name: string; path?: string }[] }[] }[] };
  type Suite = { suites?: Suite[]; specs?: Spec[] };
  const report = JSON.parse(readFileSync(resultsFile, "utf8")) as { suites: Suite[] };
  const out: RanTest[] = [];
  const walk = (s: Suite) => {
    for (const spec of s.specs ?? []) {
      const r = spec.tests[0]?.results.at(-1);
      const video = r?.attachments?.find((a) => a.name === "video" && a.path)?.path;
      const message = r?.errors?.[0]?.message;
      out.push({
        file: path.basename(spec.file), title: spec.title, status: r?.status ?? "skipped",
        seconds: Math.round((r?.duration ?? 0) / 100) / 10,
        video: video && existsSync(video) ? video : undefined,
        error: message ? message.replace(/\x1b\[[0-9;]*m/g, "").split("\n").slice(0, 3).join(" ").slice(0, 300) : undefined,
      });
    }
    for (const child of s.suites ?? []) walk(child);
  };
  report.suites.forEach(walk);
  return out;
}

// ---------- replaying a run's own tests ----------

export type ReplayJourney = {
  id: string; title: string; file: string;
  status: RanTest["status"];
  seconds: number;
  video?: string;          // file name inside the run folder
  startsAt?: number;
  error?: string;
};

export type ReplayResult = {
  ok: boolean;                                      // every journey passed
  seconds: number;
  journeys: ReplayJourney[];
  checks: Record<string, { journey: string; passed: boolean; t: number }>;   // checks that ran; the rest weren't reached
  entries: TestEntry[];                             // the compiled test files (tests/index.json)
  notes: string[];                                  // what the compiler kept, dropped or flagged
  error?: string;                                   // the replay itself couldn't run
};

// Compiles a run into tests (in <run>/scripts/) and replays them once against the app: results go to
// <run>/replay.json, videos to <run>/replay-<journey>.webm.
export async function replayRun(opts: {
  runDir: string;
  baseUrl: string;
  headers?: Record<string, string>;
  resetApp?: () => Promise<void>;
  approvedBy?: string;
}): Promise<ReplayResult> {
  const started = Date.now();
  const { runDir } = opts;
  const trace = JSON.parse(readFileSync(path.join(runDir, "trace.json"), "utf8")) as Trace;
  const { files, entries, notes } = compileRun(trace, { approvedBy: opts.approvedBy });
  const scripts = path.join(runDir, "scripts");
  rmSync(scripts, { recursive: true, force: true });
  for (const [name, content] of Object.entries(files)) {
    mkdirSync(path.dirname(path.join(scripts, name)), { recursive: true });
    writeFileSync(path.join(scripts, name), content);
  }

  const ran = await runTests({
    dir: scripts, outputDir: path.join(runDir, "replay"), baseUrl: opts.baseUrl, headers: opts.headers, resetApp: opts.resetApp,
    warmPaths: entries.flatMap((e) => e.pages),
  });

  // Map test titles back to the run's journeys through the manifest, and keep each journey's video.
  const journeyOf = (file: string, title: string) => entries.find((e) => e.file === file)?.journeys.find((j) => j.title === title)?.id;
  const journeys: ReplayJourney[] = [];
  for (const t of ran.tests) {
    const id = journeyOf(t.file, t.title);
    if (!id) continue;
    const j: ReplayJourney = { id, title: t.title, file: t.file, status: t.status, seconds: t.seconds, startsAt: t.startsAt, error: t.error };
    if (t.video) { j.video = `replay-${id}.webm`; copyFileSync(t.video, path.join(runDir, j.video)); }
    journeys.push(j);
  }
  const order = (id: string) => trace.journeys.findIndex((j) => j.id === id);
  journeys.sort((a, b) => order(a.id) - order(b.id));
  const checks: ReplayResult["checks"] = {};
  for (const c of ran.checks) checks[c.check] = { journey: journeyOf(c.file, c.test) ?? "?", passed: c.passed, t: c.t };

  const result: ReplayResult = {
    ok: !ran.error && journeys.length > 0 && journeys.every((j) => j.status === "passed"),
    seconds: Math.round((Date.now() - started) / 1000),
    journeys, checks, entries, notes, error: ran.error,
  };
  writeFileSync(path.join(runDir, "replay.json"), JSON.stringify(result, null, 2));
  return result;
}
