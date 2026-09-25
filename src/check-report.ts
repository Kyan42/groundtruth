import type { ApprovedClaim } from "./comment.js";
import type { ClaimResult, ClaimStatus } from "./explore/agent.js";
import type { Check } from "./explore/checks.js";
import type { ReplayResult } from "./compile/replay.js";
import type { ExploreRun } from "./explore/run.js";

// Renders an exploration run as the Groundtruth check's conclusion, title, summary and details.
// Only a failed claim fails the check; anything short of "all verified" that isn't a failure is neutral.

export type CheckReport = {
  conclusion: "success" | "failure" | "neutral";
  title: string;
  summary: string;
  text: string;
};

const ICON: Record<ClaimStatus | "none", string> = {
  verified: "✅", failed: "❌", unreachable: "⚠️", blocked: "⏸️", error: "💥", none: "⬜",
};
const WORD: Record<ClaimStatus | "none", string> = {
  verified: "verified", failed: "failed", unreachable: "unreachable", blocked: "blocked", error: "error", none: "no status",
};
const MAX_TEXT = 60_000;   // GitHub allows 65,535 characters per output field

// The replay is a check on the script, not on the PR: it's reported, but it never changes the verdicts.
function replayLine(replay: ReplayResult | undefined): string {
  if (!replay) return "**Replay:** not run.";
  if (replay.error && !replay.journeys.length) return `**Replay:** couldn't run the compiled script (${replay.error}).`;
  const passed = replay.journeys.filter((j) => j.status === "passed").length;
  if (replay.ok) return `**Replay:** the compiled Playwright script re-ran all ${passed} journeys on the same app and passed, in ${replay.seconds}s with no model calls.`;
  const bad = replay.journeys.filter((j) => j.status !== "passed").map((j) => `${j.id} ${j.status}`).join(", ");
  return `**Replay:** the compiled script passed ${passed} of ${replay.journeys.length} journeys (${bad}). The verdicts above come from exploration; the script needs a look before it can be trusted.`;
}

export function renderReport(run: ExploreRun, claims: ApprovedClaim[], opts: { sha: string; bootSeconds: number; dashboard: string; replay?: ReplayResult; extra?: string }): CheckReport {
  const resultOf = (id: string): ClaimResult | undefined => run.results.find((r) => r.claimId === id);
  const statusOf = (id: string): ClaimStatus | "none" => resultOf(id)?.status ?? "none";
  const counts = new Map<ClaimStatus | "none", number>();
  for (const c of claims) counts.set(statusOf(c.id), (counts.get(statusOf(c.id)) ?? 0) + 1);

  const failed = counts.get("failed") ?? 0;
  const verified = counts.get("verified") ?? 0;
  const conclusion = failed ? "failure" : verified === claims.length ? "success" : "neutral";
  const others = [...counts].filter(([s]) => s !== "verified" && s !== "failed").map(([s, n]) => `${n} ${WORD[s]}`);
  const title = failed
    ? [`${failed} of ${claims.length} claims failed`, `${verified} verified`, ...others].join(" · ")
    : [`${verified} of ${claims.length} claims verified`, ...others].join(" · ");

  const checkRef = (id: string) => {
    const k = run.checks.find((x) => x.id === id);
    return k ? `${k.passed ? "✓" : "✗"} ${k.id}` : id;
  };
  const rows = claims.map((c) => {
    const r = resultOf(c.id);
    const status = statusOf(c.id);
    const basis = r?.basis === "observation" ? " (agent's judgment, no check)" : "";
    return `| ${ICON[status]} | **${c.id}** ${cell(claimText(c))} | ${WORD[status]}${basis} | ${r?.checkIds.map(checkRef).join(" ") ?? ""} |`;
  });
  const observed = run.results.filter((r) => r.basis === "observation").length;
  const summary = [
    `**${title}** on commit \`${opts.sha.slice(0, 7)}\` · ${run.journeys.length} journey${run.journeys.length === 1 ? "" : "s"} · ` +
      `${run.checks.length} checks · booted in ${opts.bootSeconds}s, tested in ${duration(run.seconds)} · $${run.costUsd.toFixed(2)}`,
    "",
    `[Open this run on the dashboard](${opts.dashboard}): a video of each journey, with every action and check marked.`,
    "",
    replayLine(opts.replay),
    "",
    "| | Claim | Result | Checks |",
    "|---|---|---|---|",
    ...rows,
    observed ? `\n${observed} verdict${observed === 1 ? " rests" : "s rest"} on the agent's own reading because no check could express the result.` : "",
    run.stoppedBecause !== "all claims have a status" ? `\nTesting stopped early: ${run.stoppedBecause}.` : "",
  ].join("\n").trimEnd();

  const journeys = run.journeys.map((j) => {
    const steps = run.steps.filter((s) => s.journey === j.id).length;
    const head = `### ${j.id} · ${j.name}\n\n${steps} step${steps === 1 ? "" : "s"}${j.resetData ? " · started from reset data" : ""}` +
      `${j.startPath !== "/" ? ` · started at \`${j.startPath}\`` : ""}`;
    const claimsHere = run.results.filter((r) => r.journey === j.id).map((r) => {
      const checks = r.checkIds.map((id) => run.checks.find((k) => k.id === id)).filter((k): k is Check => k !== undefined);
      return [
        `- ${ICON[r.status]} **${r.claimId} ${WORD[r.status]}.** ${r.evidence}`,
        ...checks.map((k) => `  - ${describeCheck(k)}`),
        r.uncheckedReason ? `  - No check: ${r.uncheckedReason}` : "",
      ].filter(Boolean).join("\n");
    });
    return `${head}\n\n${claimsHere.join("\n") || "_No claims recorded in this journey._"}`;
  });
  const unused = run.checks.filter((k) => !run.results.some((r) => r.checkIds.includes(k.id)));
  const text = [
    ...journeys,
    unused.length ? `### Checks not used for a verdict\n\nThe agent ran these and then checked again differently; they're kept so nothing is hidden.\n\n${unused.map((k) => `- ${describeCheck(k)}`).join("\n")}` : "",
    opts.replay?.notes.length ? `### Compiling the script\n\n${opts.replay.notes.map((n) => `- ${n}`).join("\n")}` : "",
    `### Run\n\n${run.turns} agent turns · ${run.steps.length} browser steps · [dashboard](${opts.dashboard})`,
    opts.extra ?? "",
  ].filter(Boolean).join("\n\n");

  return { conclusion, title, summary: clip(summary), text: clip(text) };
}

const claimText = (c: ApprovedClaim) => `${c.when ? `${c.when} → ` : ""}${c.then.what}`;

function describeCheck(k: Check): string {
  const what = `${k.assert.replace(/_/g, " ")}${k.expected ? ` "${k.expected}"` : ""}`;
  return `${k.passed ? "✓" : "✗"} ${k.id} ${k.locator ? `\`${k.locator}\` ` : ""}${what} · observed ${k.observed}`;
}

const cell = (s: string) => s.replace(/\|/g, "\\|").replace(/\n/g, " ");
const duration = (s: number) => (s >= 60 ? `${Math.floor(s / 60)}m${String(s % 60).padStart(2, "0")}s` : `${s}s`);
const clip = (s: string) => (s.length > MAX_TEXT ? `${s.slice(0, MAX_TEXT)}\n\n… (truncated)` : s);
