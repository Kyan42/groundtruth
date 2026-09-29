import { readFileSync } from "node:fs";

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`Missing required env var ${name} (see .env.example)`);
  return value;
}

export const config = {
  get appId() { return required("GITHUB_APP_ID"); },
  get privateKey() { return readFileSync(required("GITHUB_PRIVATE_KEY_PATH"), "utf8"); },
  get webhookSecret() { return required("GITHUB_WEBHOOK_SECRET"); },
  // Read by the Anthropic SDK itself; required here so a missing key fails at startup, not mid-PR.
  anthropicApiKey: required("ANTHROPIC_API_KEY"),
  extractConcurrency: Number(process.env.EXTRACT_CONCURRENCY ?? 4),
  // Read by the Runloop SDK itself; required so boots don't fail mid-PR.
  runloopApiKey: required("RUNLOOP_API_KEY"),
  bootConcurrency: Number(process.env.BOOT_CONCURRENCY ?? 2),
  port: Number(process.env.PORT ?? 3000),
  // Where the dashboard is reachable; the Groundtruth check links runs here. Local-only by default.
  dashboardUrl: (process.env.DASHBOARD_URL ?? `http://localhost:${process.env.PORT ?? 3000}`).replace(/\/$/, ""),
  // Each test run (trace.json, videos) is saved in a folder here; the dashboard reads them.
  runsDir: process.env.RUNS_DIR ?? "runs",
  // Optional: when set, forward webhooks from this smee.io channel to the local server.
  smeeUrl: process.env.SMEE_URL,
};
