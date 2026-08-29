import type { JourneyStep, Locator } from "@/lib/domain/assertion-dsl";
import type { RunView, TestMission } from "@/lib/domain/schemas";

export type DashboardItemStatus =
  | "conformant"
  | "non_conformant"
  | "inconclusive"
  | "deferred"
  | "uncovered"
  | "pending"
  | "preserved"
  | "regressed";

export type DashboardItem = {
  key: string;
  id: string;
  kind: "intent" | "regression";
  label: string;
  status: DashboardItemStatus;
  detail?: string;
  missionId?: string;
  hasResult: boolean;
  executed: boolean;
};

export type DashboardItemGroups = {
  intent: DashboardItem[];
  regression: DashboardItem[];
  all: DashboardItem[];
};

export type NormalizedNetworkRow = {
  key: string;
  method: string;
  path: string;
  status: number;
  target: "base" | "head";
  relevant: boolean;
};

export type ActionRow = {
  key: string;
  at: string;
  offset: string;
  summary: string;
  status: string;
  target: "base" | "head";
  structuredDetail?: string;
};

export type VerdictNarrative = {
  title: string;
  body: string;
  missionLabel?: string;
};

const finishedRunStatuses = new Set<RunView["run"]["status"]>([
  "complete",
  "blocked",
  "failed",
]);

export function buildDashboardItems(view: RunView): DashboardItemGroups {
  const intent =
    view.contract.intentSpec?.claims.map((claim) => {
      const result = view.results.intent.find((candidate) => candidate.claimId === claim.id);
      const coverage = view.contract.claimCoverage.find(
        (candidate) => candidate.claimId === claim.id,
      );
      const mission = findMissionForClaim(view, claim.id);
      const deferred = mission?.deferredClaims?.find(
        (candidate) => candidate.claimId === claim.id,
      );
      const missionId = result?.missionId ?? coverage?.missionId ?? mission?.id;
      const executed = missionWasExecuted(view, missionId);

      if (result) {
        return {
          key: intentItemKey(claim.id),
          id: claim.id,
          kind: "intent" as const,
          label: claim.statement,
          status: result.verdict,
          detail:
            result.verdict === "non_conformant"
              ? mission
                ? `Finding in ${mission.title}.`
                : "The linked mission returned a non-conformant result."
              : undefined,
          missionId,
          hasResult: true,
          executed: true,
        };
      }

      if (coverage?.status === "deferred" || deferred) {
        return {
          key: intentItemKey(claim.id),
          id: claim.id,
          kind: "intent" as const,
          label: claim.statement,
          status: "deferred" as const,
          detail: coverage?.reason ?? deferred?.reason,
          missionId,
          hasResult: false,
          executed: false,
        };
      }

      if (coverage?.status === "uncovered") {
        return {
          key: intentItemKey(claim.id),
          id: claim.id,
          kind: "intent" as const,
          label: claim.statement,
          status: "uncovered" as const,
          detail: coverage.reason ?? "No browser mission is linked to this claim.",
          missionId,
          hasResult: false,
          executed: false,
        };
      }

      if (coverage?.status === "covered" || mission?.claimIds.includes(claim.id)) {
        return {
          key: intentItemKey(claim.id),
          id: claim.id,
          kind: "intent" as const,
          label: claim.statement,
          status:
            executed && finishedRunStatuses.has(view.run.status)
              ? ("inconclusive" as const)
              : ("pending" as const),
          detail:
            executed && finishedRunStatuses.has(view.run.status)
              ? "The mission ran, but this view has no claim-level verdict."
              : "Linked browser verification has not produced a verdict yet.",
          missionId,
          hasResult: false,
          executed,
        };
      }

      return {
        key: intentItemKey(claim.id),
        id: claim.id,
        kind: "intent" as const,
        label: claim.statement,
        status: verificationHasStarted(view)
          ? ("uncovered" as const)
          : ("pending" as const),
        detail: verificationHasStarted(view)
          ? "No browser mission is linked to this claim."
          : undefined,
        missionId,
        hasResult: false,
        executed: false,
      };
    }) ?? [];

  const regressionByMission = new Map(
    view.results.regression.map((result) => [result.missionId, result]),
  );
  const regression: DashboardItem[] = view.results.regression.map((result) => {
    const mission = view.missions.find((candidate) => candidate.id === result.missionId);
    return {
      key: regressionItemKey(result.missionId),
      id: result.missionId,
      kind: "regression",
      label: mission?.title ?? result.missionId,
      status: result.verdict,
      detail: result.reason,
      missionId: result.missionId,
      hasResult: true,
      executed: true,
    };
  });

  for (const mission of view.missions) {
    if (mission.kind !== "regression" || regressionByMission.has(mission.id)) {
      continue;
    }
    regression.push({
      key: regressionItemKey(mission.id),
      id: mission.id,
      kind: "regression",
      label: mission.title,
      status:
        missionWasExecuted(view, mission.id) && finishedRunStatuses.has(view.run.status)
          ? "inconclusive"
          : "pending",
      detail: mission.goal,
      missionId: mission.id,
      hasResult: false,
      executed: missionWasExecuted(view, mission.id),
    });
  }

  return { intent, regression, all: [...intent, ...regression] };
}

export function getDefaultDashboardItemKey(view: RunView): string | undefined {
  const items = buildDashboardItems(view);
  return (
    items.all.find(
      (item) => item.status === "non_conformant" || item.status === "regressed",
    )?.key ??
    items.all.find((item) => item.hasResult && item.executed)?.key ??
    items.all.find((item) => item.executed)?.key ??
    items.intent[0]?.key ??
    items.regression[0]?.key
  );
}

export function isEvidenceLinked(view: RunView, item: DashboardItem | undefined): boolean {
  return Boolean(findLatestAttemptForItem(view, item)?.journey);
}

export function findLatestAttemptForItem(
  view: RunView,
  item: DashboardItem | undefined,
): RunView["verificationAttempts"][number] | undefined {
  if (!item?.missionId) {
    return undefined;
  }
  return [...view.verificationAttempts]
    .reverse()
    .find((attempt) => attempt.mission?.id === item.missionId);
}

export function findMissionForItem(
  view: RunView,
  item: DashboardItem | undefined,
): TestMission | undefined {
  if (!item?.missionId) {
    return undefined;
  }
  return view.missions.find((mission) => mission.id === item.missionId);
}

export function normalizeNetworkRows(
  requests: RunView["network"],
): NormalizedNetworkRow[] {
  return requests
    .map((request, index) => {
      const path = safePathname(request.url);
      return {
        key: `${request.target}:${index}:${request.status}:${path}`,
        method: normalizeMethod(request.method),
        path,
        status: request.status,
        target: request.target,
        relevant: path === "/api" || path.startsWith("/api/"),
        priority: networkPriority(path),
        index,
      };
    })
    .sort((left, right) => left.priority - right.priority || left.index - right.index)
    .map(({ priority: _priority, index: _index, ...request }) => request);
}

export function buildActionRows(view: RunView): ActionRow[] {
  const firstTimestamp = Date.parse(view.actions[0]?.at ?? "");
  const journeySteps =
    view.journey &&
    view.journey.steps.length === view.actions.length &&
    view.actions.every((action, index) =>
      actionMatchesJourneyStep(action.summary, view.journey?.steps[index]),
    )
      ? view.journey.steps
      : undefined;

  return view.actions.map((action, index) => ({
    key: `${action.target}:${action.at}:${index}`,
    at: action.at,
    offset: formatActionOffset(action.at, firstTimestamp),
    summary: action.summary,
    status: action.status,
    target: action.target,
    structuredDetail: journeySteps ? describeJourneyStep(journeySteps[index]) : undefined,
  }));
}

export function describeJourneyStep(step: JourneyStep): string {
  switch (step.action) {
    case "goto":
      return `Path ${step.path}`;
    case "click":
      return describeLocator(step.locator);
    case "fill":
      return `${describeLocator(step.locator)}; fixture key ${step.fixtureValueKey}`;
    case "press":
      return `${describeLocator(step.locator)}; key ${step.key}`;
    case "wait_for":
      return `${describeLocator(step.locator)}; wait until ${step.state}`;
  }
}

export function buildVerdictNarrative(
  view: RunView,
  item: DashboardItem | undefined,
): VerdictNarrative {
  if (!item) {
    return {
      title: "No check selected.",
      body: "Select an intent claim or regression check to inspect its evidence.",
    };
  }

  const mission = findMissionForItem(view, item);
  const missionLabel = mission?.title ?? item.missionId;
  const setupMessage = view.setup.blockers[0]?.message;
  const blockerMessage = view.blocker?.message ?? setupMessage;
  const recordedActionsPassed =
    view.actions.length > 0 &&
    view.actions.every((action) => normalizeActionStatus(action.status) === "passed");

  switch (item.status) {
    case "non_conformant":
      return {
        title: "Does not match the intent contract.",
        body: recordedActionsPassed
          ? `All ${view.actions.length} recorded replay steps completed, but the linked mission produced a non-conformant assertion result.`
          : "The linked browser mission produced a non-conformant assertion result for this claim.",
        missionLabel,
      };
    case "conformant":
      return {
        title: "Matches the intent contract.",
        body: "The linked browser mission completed with a conformant result for this claim.",
        missionLabel,
      };
    case "regressed":
      return {
        title: "A regression was detected.",
        body:
          item.detail ??
          "The linked regression mission reported behavior that was not preserved.",
        missionLabel,
      };
    case "preserved":
      return {
        title: "No regression was detected.",
        body:
          item.detail ??
          "The linked regression mission reported that the checked behavior was preserved.",
        missionLabel,
      };
    case "deferred":
      return {
        title: "Not evaluated in this run.",
        body: item.detail ?? "This claim was explicitly deferred from the current mission.",
        missionLabel,
      };
    case "uncovered":
      return {
        title: "No browser evidence is linked.",
        body: item.detail ?? "The current run did not assign a browser mission to this claim.",
        missionLabel,
      };
    case "inconclusive":
      return {
        title: blockerMessage ? "Verification could not complete." : "The result is inconclusive.",
        body:
          blockerMessage ??
          item.detail ??
          "The linked mission did not produce a definitive product verdict.",
        missionLabel,
      };
    case "pending":
      if (blockerMessage) {
        return {
          title: "Verification is blocked.",
          body: blockerMessage,
          missionLabel,
        };
      }
      if (view.run.status === "verifying") {
        return {
          title: "Browser verification is in progress.",
          body: "Evidence will appear after the active mission finishes and its artifacts are recorded.",
          missionLabel,
        };
      }
      if (view.contract.status === "ready") {
        return {
          title: "The intent contract is ready for approval.",
          body: "Approve the extracted claims before starting browser verification.",
          missionLabel,
        };
      }
      return {
        title: "This check has not run yet.",
        body: "No browser verdict is available for the selected item.",
        missionLabel,
      };
  }
}

export function statusLabel(status: DashboardItemStatus): string {
  switch (status) {
    case "non_conformant":
      return "Non-conformant";
    case "preserved":
      return "Preserved";
    case "regressed":
      return "Regressed";
    case "uncovered":
      return "Not covered";
    default:
      return `${status.slice(0, 1).toUpperCase()}${status.slice(1)}`;
  }
}

export function labelRunStatus(status: RunView["run"]["status"]): string {
  return status.replaceAll("_", " ");
}

export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) {
    return "0:00";
  }
  const wholeSeconds = Math.floor(seconds);
  const minutes = Math.floor(wholeSeconds / 60);
  return `${minutes}:${String(wholeSeconds % 60).padStart(2, "0")}`;
}

function intentItemKey(claimId: string): string {
  return `intent:${claimId}`;
}

function regressionItemKey(missionId: string): string {
  return `regression:${missionId}`;
}

function verificationHasStarted(view: RunView): boolean {
  return (
    view.run.status === "verifying" ||
    finishedRunStatuses.has(view.run.status) ||
    view.missions.length > 0 ||
    view.verificationAttempts.length > 0 ||
    Boolean(view.journey) ||
    view.actions.length > 0 ||
    view.results.intent.length > 0 ||
    view.results.regression.length > 0
  );
}

function missionWasExecuted(view: RunView, missionId: string | undefined): boolean {
  if (!missionId) {
    return false;
  }
  return view.verificationAttempts.some(
    (attempt) =>
      attempt.mission?.id === missionId &&
      Boolean(
        attempt.execution ||
          attempt.executions?.base ||
          attempt.executions?.head ||
          attempt.comparison,
      ),
  );
}

function findMissionForClaim(view: RunView, claimId: string): TestMission | undefined {
  return view.missions.find(
    (mission) =>
      mission.claimIds.includes(claimId) ||
      mission.deferredClaims?.some((claim) => claim.claimId === claimId),
  );
}

function normalizeMethod(method: string): string {
  const normalized = method.trim().toUpperCase();
  return /^[A-Z]{1,12}$/.test(normalized) ? normalized : "REQUEST";
}

function safePathname(value: string): string {
  try {
    return new URL(value, "https://groundtruth.invalid").pathname || "/";
  } catch {
    return "Path unavailable";
  }
}

function networkPriority(path: string): number {
  if (path === "/api" || path.startsWith("/api/")) {
    return 0;
  }
  if (
    path.startsWith("/_next/") ||
    path.startsWith("/static/") ||
    path === "/favicon.ico"
  ) {
    return 2;
  }
  return 1;
}

function formatActionOffset(value: string, firstTimestamp: number): string {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp) || !Number.isFinite(firstTimestamp)) {
    return "--:--";
  }
  const elapsed = Math.max(0, timestamp - firstTimestamp);
  const minutes = Math.floor(elapsed / 60_000);
  const seconds = Math.floor((elapsed % 60_000) / 1_000);
  const tenths = Math.floor((elapsed % 1_000) / 100);
  return `${minutes}:${String(seconds).padStart(2, "0")}.${tenths}`;
}

function actionMatchesJourneyStep(summary: string, step: JourneyStep | undefined): boolean {
  if (!step) {
    return false;
  }
  const normalized = summary.trim().toLowerCase();
  return step.action === "goto"
    ? normalized.startsWith("navigate")
    : normalized === step.action;
}

function describeLocator(locator: Locator): string {
  switch (locator.by) {
    case "role":
      return locator.name
        ? `Role ${locator.role}, name "${locator.name}"`
        : `Role ${locator.role}`;
    case "text":
      return `Text "${locator.text}"${locator.exact ? " (exact)" : ""}`;
    case "test_id":
      return `Test ID "${locator.value}"`;
    case "css":
      return `CSS "${locator.value}"`;
  }
}

function normalizeActionStatus(status: string): string {
  return status.trim().toLowerCase();
}
