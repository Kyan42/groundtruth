import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { copyFileSync, createWriteStream, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { createRequire } from "node:module";
import path from "node:path";
import { compileRun, type Trace } from "./compile.js";

// Compiles a run into Playwright tests and replays them once against the app (the same sandbox the agent
// explored), with Playwright's own test runner. The replay is the clean record of the run: fast, no model
// calls, one video per journey. Results go to <run>/replay.json; videos to <run>/replay-<journey>.webm.

export type ReplayJourney = {
  id: string;
  title: string;
  status: "passed" | "failed" | "timedOut" | "skipped" | "interrupted";
  seconds: number;
  video?: string;          // file name inside the run folder
  startsAt?: number;       // seconds into the video where the app appears (after a data reset)
  error?: string;
};

export type ReplayResult = {
  ok: boolean;                                      // every journey passed
  seconds: number;
  journeys: ReplayJourney[];
  checks: Record<string, { journey: string; passed: boolean; t: number }>;   // checks that ran; the rest weren't reached
  notes: string[];                                  // what the compiler kept, dropped or flagged
  error?: string;                                   // the replay itself couldn't run
};

const require = createRequire(import.meta.url);
const PLAYWRIGHT_CLI = path.join(path.dirname(require.resolve("playwright/package.json")), "cli.js");

export async function replayRun(opts: {
  runDir: string;
  baseUrl: string;
  headers?: Record<string, string>;
  resetApp?: () => Promise<void>;
  timeoutSeconds?: number;
}): Promise<ReplayResult> {
  const started = Date.now();
  const { runDir } = opts;
  const trace = JSON.parse(readFileSync(path.join(runDir, "trace.json"), "utf8")) as Trace;
  const { files, notes } = compileRun(trace, path.basename(runDir));
  const scripts = path.join(runDir, "scripts");
  mkdirSync(scripts, { recursive: true });
  for (const [name, content] of Object.entries(files)) writeFileSync(path.join(scripts, name), content);

  const checksFile = path.join(runDir, "replay-checks.jsonl");
  rmSync(checksFile, { force: true });
  rmSync(path.join(runDir, "replay"), { recursive: true, force: true });

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
    const log = createWriteStream(path.join(runDir, "replay.log"));
    const child = spawn(process.execPath, [PLAYWRIGHT_CLI, "test", "-c", path.join(scripts, "playwright.config.ts")], {
      cwd: process.cwd(),
      env: {
        ...process.env,
        GROUNDTRUTH_BASE_URL: opts.baseUrl,
        GROUNDTRUTH_HEADERS: JSON.stringify(opts.headers ?? {}),
        GROUNDTRUTH_RESET_URL: resetUrl,
        GROUNDTRUTH_CHECKS_FILE: checksFile,
        FORCE_COLOR: "0",
      },
    });
    child.stdout.pipe(log);
    child.stderr.pipe(log);
    const timer = setTimeout(() => child.kill(), (opts.timeoutSeconds ?? 600) * 1000);
    const code = await new Promise<number | null>((resolve) => child.on("close", resolve));
    clearTimeout(timer);
    if (code === null) error = "the replay timed out";
  } finally {
    resetServer.close();
  }

  // Playwright reports grouped (chained) tests after top-level ones; keep the trace's journey order.
  const order = (id: string) => trace.journeys.findIndex((j) => j.id === id);
  const journeys = readJourneys(path.join(runDir, "replay", "results.json"), runDir).sort((a, b) => order(a.id) - order(b.id));
  if (!journeys.length && !error) error = "the test runner produced no results (see replay.log)";
  const checks: ReplayResult["checks"] = {};
  if (existsSync(checksFile)) {
    for (const line of readFileSync(checksFile, "utf8").split("\n").filter(Boolean)) {
      const c = JSON.parse(line) as { test: string; check?: string; event?: string; passed?: boolean; t: number };
      const journey = c.test.split(" ·")[0];
      if (c.event === "loaded") { const j = journeys.find((x) => x.id === journey); if (j) j.startsAt = c.t; }
      else if (c.check) checks[c.check] = { journey, passed: Boolean(c.passed), t: c.t };
    }
  }
  const result: ReplayResult = {
    ok: !error && journeys.length > 0 && journeys.every((j) => j.status === "passed"),
    seconds: Math.round((Date.now() - started) / 1000),
    journeys, checks, notes, error,
  };
  writeFileSync(path.join(runDir, "replay.json"), JSON.stringify(result, null, 2));
  return result;
}

// Playwright's JSON report → one entry per journey, with its video copied next to the trace.
function readJourneys(resultsFile: string, runDir: string): ReplayJourney[] {
  if (!existsSync(resultsFile)) return [];
  type Suite = { suites?: Suite[]; specs?: { title: string; tests: { results: { status: ReplayJourney["status"]; duration: number; errors?: { message?: string }[]; attachments?: { name: string; path?: string }[] }[] }[] }[] };
  const report = JSON.parse(readFileSync(resultsFile, "utf8")) as { suites: Suite[] };
  const out: ReplayJourney[] = [];
  const walk = (s: Suite) => {
    for (const spec of s.specs ?? []) {
      const id = spec.title.split(" ·")[0];
      const r = spec.tests[0]?.results.at(-1);
      const j: ReplayJourney = { id, title: spec.title, status: r?.status ?? "skipped", seconds: Math.round((r?.duration ?? 0) / 100) / 10 };
      const video = r?.attachments?.find((a) => a.name === "video" && a.path)?.path;
      if (video && existsSync(video)) {
        j.video = `replay-${id}.webm`;
        copyFileSync(video, path.join(runDir, j.video));
      }
      const message = r?.errors?.[0]?.message;
      if (message) j.error = message.replace(/\x1b\[[0-9;]*m/g, "").split("\n").slice(0, 3).join(" ").slice(0, 300);
      out.push(j);
    }
    for (const child of s.suites ?? []) walk(child);
  };
  report.suites.forEach(walk);
  return out;
}
