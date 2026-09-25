import { writeFileSync } from "node:fs";
import path from "node:path";
import type { ApprovedClaim } from "../comment.js";
import { explore, type ExploreEvent, type ExploreResult } from "./agent.js";
import { ExplorerBrowser } from "./browser.js";

// Explores a booted app: opens the browser, runs the agent on the claims, closes the browser (which
// finalizes each journey's video) and writes trace.json to outDir. Shared by the webhook and the CLI.

export type ExploreRun = ExploreResult & { seconds: number };

export async function exploreApp(opts: {
  url: string;
  headers: Record<string, string>;
  resetApp?: () => Promise<void>;
  claims: ApprovedClaim[];
  outDir: string;                    // videos and trace.json go here
  meta: Record<string, unknown>;     // written at the top of trace.json (PR, commit, ...)
  model?: string;
  maxTurns?: number;
  onEvent?: (e: ExploreEvent) => void;
}): Promise<ExploreRun> {
  const started = Date.now();
  const browser = await ExplorerBrowser.open(opts.url, opts.headers, opts.outDir);
  let result: ExploreResult;
  try {
    result = await explore({
      claims: opts.claims, browser, resetApp: opts.resetApp, model: opts.model, maxTurns: opts.maxTurns, onEvent: opts.onEvent,
    });
  } finally {
    await browser.close();
  }
  const run = { ...result, seconds: Math.round((Date.now() - started) / 1000) };
  writeFileSync(path.join(opts.outDir, "trace.json"), JSON.stringify({ ...opts.meta, claims: opts.claims, ...run }, null, 2));
  return run;
}
