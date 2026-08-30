import type {
  BrowserVerification,
  ExecutionResult,
  Run,
} from "@/lib/domain/schemas";
import { buildResults } from "@/lib/views/build-run-view";

/**
 * Projection consumed by the static dashboard at public/run.html.
 *
 * The contract is fixed by the UI. Field names, tuple layouts, and array order
 * are all load-bearing:
 * - `steps` is [timeSeconds, kind, htmlText, isFailureFlag?].
 * - `net` is [method, path, status, duration, mismatchFlag?].
 * - `verdict_line` and step text are inserted with innerHTML; only <b> may pass
 *   through unescaped, so every dynamic string is escaped here first.
 * - Claims render in array order, which maps to the PR description bullets.
 */
export type DashboardStep =
  | [number, string, string]
  | [number, string, string, 1];

export type DashboardNetRow =
  | [string, string, string, string]
  | [string, string, string, string, "mismatch"];

export type DashboardClaim = {
  id: string;
  verdict: "p" | "f" | "u";
  text: string;
  sub: string;
  verdict_line: string;
  dur: number;
  videoUrl?: string;
  steps: DashboardStep[];
  net: DashboardNetRow[];
};

export type DashboardPayload = {
  status: Run["status"];
  blocker?: { code: string; message: string; retryable: boolean };
  number: number;
  branch: string;
  duration: string;
  cost: string;
  prUrl: string;
  claims: DashboardClaim[];
};

const PASSING_ACTION_STATUSES = new Set(["ok", "passed", "success", "complete", "completed"]);

export function buildDashboardPayload(run: Run): DashboardPayload {
  const attempts = [
    ...(run.browserVerificationHistory ?? []),
    ...(run.browserVerification ? [run.browserVerification] : []),
  ];

  const claimVerdict = new Map<string, DashboardClaim["verdict"]>();
  const claimAttempt = new Map<string, BrowserVerification>();
  const deferredReasons = new Map<string, string>();
  for (const attempt of attempts) {
    for (const deferred of attempt.mission?.deferredClaims ?? []) {
      deferredReasons.set(deferred.claimId, deferred.reason);
    }
    for (const result of buildResults(attempt).intent) {
      claimVerdict.set(
        result.claimId,
        result.verdict === "conformant" ? "p" : result.verdict === "non_conformant" ? "f" : "u",
      );
      claimAttempt.set(result.claimId, attempt);
    }
  }

  const claims: DashboardClaim[] = (run.intentSpec?.claims ?? []).map((claim) => {
    const attempt = claimAttempt.get(claim.id);
    const verdict = claimVerdict.get(claim.id) ?? "u";
    const execution = pickExecution(attempt);
    const deferredReason = deferredReasons.get(claim.id);
    return {
      id: claim.id,
      verdict,
      text: claim.statement,
      sub: verdict === "p" ? "" : subLine(run, verdict, attempt, execution, deferredReason),
      verdict_line: verdictLine(run, verdict, attempt, execution, deferredReason),
      dur: durationSeconds(execution),
      ...videoUrl(execution),
      steps: buildSteps(attempt, execution),
      net: buildNet(attempt),
    };
  });

  const regressionRows = new Map<string, DashboardClaim>();
  for (const attempt of attempts) {
    const mission = attempt.mission;
    if (mission?.kind !== "regression") {
      continue;
    }
    const result = buildResults(attempt).regression[0];
    if (!result) {
      continue;
    }
    const verdict =
      result.verdict === "preserved" ? "p" : result.verdict === "regressed" ? "f" : "u";
    const execution = pickExecution(attempt);
    regressionRows.set(mission.id, {
      id: `R${regressionRows.size + 1}`,
      verdict,
      text: mission.title,
      sub:
        verdict === "p"
          ? ""
          : verdict === "f"
            ? firstDivergenceSummary(attempt) ?? "diverged from base"
            : "comparison was inconclusive",
      verdict_line:
        (verdict === "p"
          ? "<b>No drift.</b> "
          : verdict === "f"
            ? "<b>Regressed against main.</b> "
            : "<b>Inconclusive.</b> ") + escapeHtml(result.reason),
      dur: durationSeconds(execution),
      ...videoUrl(execution),
      steps: buildSteps(attempt, execution),
      net: buildNet(attempt),
    });
  }
  claims.push(...regressionRows.values());

  if (claims.length === 0) {
    // Before the intent contract exists there is nothing claim-shaped to show.
    // The page cannot render an empty claim list, and letting it fall back to
    // the embedded sample would present fabricated data for a real run, so we
    // surface one honest status row instead.
    claims.push({
      id: "GT",
      verdict: "u",
      text: "Reading the PR description into claims",
      sub: pendingSub(run),
      verdict_line: `<b>No verdicts yet.</b> ${escapeHtml(pendingLine(run))}`,
      dur: 1,
      steps: [],
      net: [],
    });
  }

  return {
    status: run.status,
    ...(run.blocker ? { blocker: run.blocker } : {}),
    number: run.pullRequest.number,
    branch: run.pullRequest.headRef,
    duration: runDuration(run, attempts),
    cost: "n/a",
    prUrl: run.pullRequest.url,
    claims,
  };
}

function pickExecution(attempt: BrowserVerification | undefined): ExecutionResult | undefined {
  if (!attempt) {
    return undefined;
  }
  const candidates = [
    attempt.executions?.head,
    attempt.execution,
    attempt.executions?.base,
  ];
  return (
    candidates.find((execution) => execution?.evidence.videoArtifactId) ??
    candidates.find((execution) => execution !== undefined)
  );
}

function videoUrl(execution: ExecutionResult | undefined): { videoUrl?: string } {
  const artifactId = execution?.evidence.videoArtifactId;
  return artifactId ? { videoUrl: `/api/artifacts/${encodeURIComponent(artifactId)}` } : {};
}

function durationSeconds(execution: ExecutionResult | undefined): number {
  if (!execution) {
    return 1;
  }
  const span =
    (new Date(execution.endedAt).getTime() - new Date(execution.startedAt).getTime()) / 1000;
  return Number.isFinite(span) && span > 0 ? Math.max(1, Math.round(span * 10) / 10) : 1;
}

function runDuration(run: Run, attempts: BrowserVerification[]): string {
  let total = 0;
  for (const attempt of attempts) {
    const executions = attempt.executions
      ? [attempt.executions.base, attempt.executions.head]
      : [attempt.execution];
    for (const execution of executions) {
      if (execution) {
        total +=
          (new Date(execution.endedAt).getTime() - new Date(execution.startedAt).getTime()) / 1000;
      }
    }
  }
  if (total <= 0) {
    total = (new Date(run.updatedAt).getTime() - new Date(run.createdAt).getTime()) / 1000;
  }
  const seconds = Math.max(1, Math.round(total));
  return seconds >= 60 ? `${Math.floor(seconds / 60)}m ${seconds % 60}s` : `${seconds}s`;
}

function subLine(
  run: Run,
  verdict: DashboardClaim["verdict"],
  attempt: BrowserVerification | undefined,
  execution: ExecutionResult | undefined,
  deferredReason: string | undefined,
): string {
  if (verdict === "f") {
    const failedCheck = execution?.checks.find((check) => !check.passed);
    const failedStep = execution?.steps.find((step) => step.status === "failed");
    return failedCheck?.behavior ?? failedStep?.message ?? "an assertion did not hold";
  }
  if (deferredReason) {
    return deferredReason;
  }
  if (attempt?.blocker) {
    return attempt.blocker.message;
  }
  if (!attempt) {
    return pendingSub(run);
  }
  return "could not be verified";
}

function pendingSub(run: Run): string {
  switch (run.status) {
    case "creating":
    case "analyzing_intent":
      return "intent analysis in progress";
    case "awaiting_contract_approval":
      return "awaiting contract approval";
    case "contract_approved":
    case "verifying":
      return "verification in progress";
    case "setup_required":
      return "setup required";
    case "blocked":
    case "failed":
      return run.blocker?.message ?? "run did not complete";
    default:
      return "not exercised in this run";
  }
}

function verdictLine(
  run: Run,
  verdict: DashboardClaim["verdict"],
  attempt: BrowserVerification | undefined,
  execution: ExecutionResult | undefined,
  deferredReason: string | undefined,
): string {
  if (verdict === "p") {
    return "<b>Verified.</b> Replayed against the head build and every recorded assertion held.";
  }
  if (verdict === "f") {
    const failedCheck = execution?.checks.find((check) => !check.passed);
    const failedStep = execution?.steps.find((step) => step.status === "failed");
    const detail =
      failedStep?.message ??
      failedCheck?.behavior ??
      execution?.error?.message ??
      "The replay completed, but an assertion did not hold.";
    return `<b>Does not match your description.</b> ${escapeHtml(detail)}`;
  }
  const reason =
    deferredReason ??
    attempt?.blocker?.message ??
    execution?.error?.message ??
    (attempt ? "The attempt ended before this claim produced a verdict." : pendingLine(run));
  return `<b>Could not verify.</b> ${escapeHtml(reason)}`;
}

function pendingLine(run: Run): string {
  const sub = pendingSub(run);
  return `This claim has no completed browser attempt yet: ${sub}.`;
}

function buildSteps(
  attempt: BrowserVerification | undefined,
  execution: ExecutionResult | undefined,
): DashboardStep[] {
  if (!attempt) {
    return [];
  }
  const actions = attempt.actions.filter((action) => action.target === "head");
  const startedAt = execution ? new Date(execution.startedAt).getTime() : undefined;
  return actions.map((action, index) => {
    const at = new Date(action.at).getTime();
    const t =
      startedAt !== undefined && Number.isFinite(at) && at >= startedAt
        ? Math.round(((at - startedAt) / 1000) * 10) / 10
        : index;
    const failed = !PASSING_ACTION_STATUSES.has(action.status.toLowerCase());
    const step: DashboardStep = [t, stepKind(action.summary), escapeHtml(action.summary)];
    return failed ? ([...step, 1] as DashboardStep) : step;
  });
}

function stepKind(summary: string): string {
  const lowered = summary.toLowerCase();
  if (/\b(goto|open|opened|navigat|visit)/.test(lowered)) {
    return "goto";
  }
  if (/\b(click|press|tap)/.test(lowered)) {
    return "click";
  }
  if (/\b(type|typed|fill|enter)/.test(lowered)) {
    return "type";
  }
  if (/\b(url|route)/.test(lowered)) {
    return "route";
  }
  if (/\b(get|post|put|patch|delete|request|response|network)\b/.test(lowered)) {
    return "net";
  }
  if (/\bblock/.test(lowered)) {
    return "blocked";
  }
  if (/\b(compare|diff|identical|diverg)/.test(lowered)) {
    return "diff";
  }
  return "assert";
}

function buildNet(attempt: BrowserVerification | undefined): DashboardNetRow[] {
  if (!attempt) {
    return [];
  }
  return attempt.network
    .filter((entry) => entry.target === "head")
    .map((entry) => {
      const row: DashboardNetRow = [
        entry.method.toUpperCase(),
        pathOf(entry.url),
        String(entry.status),
        "",
      ];
      return entry.status >= 400 ? ([...row, "mismatch"] as DashboardNetRow) : row;
    });
}

function pathOf(url: string): string {
  try {
    const parsed = new URL(url);
    return `${parsed.pathname}${parsed.search}`;
  } catch {
    return url;
  }
}

function firstDivergenceSummary(attempt: BrowserVerification): string | undefined {
  const divergence = attempt.comparison?.firstDivergence;
  return divergence ? `${divergence.behavior}: ${divergence.headSummary}` : undefined;
}

function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}
