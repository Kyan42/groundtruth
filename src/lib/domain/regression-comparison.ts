import { randomUUID } from "node:crypto";

import type { Assertion } from "@/lib/domain/assertion-dsl";
import {
  type ExecutionResult,
  type RegressionComparison,
  RegressionComparisonSchema,
  type TestMission,
} from "@/lib/domain/schemas";

export function compareRegressionExecutions(
  mission: TestMission,
  base: ExecutionResult,
  head: ExecutionResult,
  origins: { base?: string; head?: string } = {},
): RegressionComparison {
  const createdAt = new Date().toISOString();
  const common = {
    schemaVersion: 1 as const,
    comparisonId: randomUUID(),
    attemptId: requiredAttemptId(base, head),
    missionId: mission.id,
    baseExecutionId: requiredExecutionId(base),
    headExecutionId: requiredExecutionId(head),
    createdAt,
  };
  const observations = mission.assertions.map((assertion, index) => {
    const baseCheck = base.checks.find((check) => check.assertionIndex === index);
    const headCheck = head.checks.find((check) => check.assertionIndex === index);
    const comparison = assertion.comparison ?? "pass_only";
    const baseNormalized = normalizeObservation(baseCheck?.actual, assertion, origins.base);
    const headNormalized = normalizeObservation(headCheck?.actual, assertion, origins.head);
    return {
      assertionId: assertion.id ?? `assertion-${index}`,
      behavior: assertion.behavior ?? `Mission assertion ${index + 1}`,
      comparison,
      basePassed: baseCheck?.passed ?? false,
      headPassed: headCheck?.passed ?? false,
      equal:
        comparison === "exact"
          ? stableJson(baseNormalized) === stableJson(headNormalized)
          : undefined,
      baseNormalized,
      headNormalized,
    };
  });

  if (isExecutionError(base) || isExecutionError(head)) {
    return RegressionComparisonSchema.parse({
      ...common,
      verdict: "inconclusive",
      reason: "A runner or setup error prevented a product-regression decision.",
      observations,
      firstDivergence: {
        stage: "execution",
        behavior: "Complete both branch executions",
        baseSummary: base.status,
        headSummary: head.status,
      },
    });
  }
  if (base.status !== "passed") {
    return RegressionComparisonSchema.parse({
      ...common,
      verdict: "inconclusive",
      reason:
        head.status === "passed"
          ? "The baseline failed while head passed; this may be an improvement, not a regression."
          : "The baseline behavior did not pass, so preservation cannot be established.",
      observations,
      firstDivergence: firstDivergence(mission, base, head, observations),
    });
  }
  const divergence = firstDivergence(mission, base, head, observations);
  if (head.status !== "passed" || divergence) {
    return RegressionComparisonSchema.parse({
      ...common,
      verdict: "regressed",
      reason:
        head.status !== "passed"
          ? "Base passed and head failed a mission assertion."
          : "A configured mission-relevant observation materially diverged from base.",
      observations,
      firstDivergence: divergence,
    });
  }
  if ((base.evidenceErrors?.length ?? 0) > 0 || (head.evidenceErrors?.length ?? 0) > 0) {
    return RegressionComparisonSchema.parse({
      ...common,
      verdict: "inconclusive",
      reason: "Product observations matched, but required evidence persistence was incomplete.",
      observations,
      firstDivergence: {
        stage: "execution",
        behavior: "Persist complete branch evidence",
        baseSummary: summarizeEvidence(base),
        headSummary: summarizeEvidence(head),
      },
    });
  }
  return RegressionComparisonSchema.parse({
    ...common,
    verdict: "preserved",
    reason: "Head preserved every configured baseline behavior and exact observation.",
    observations,
  });
}

export function normalizeObservation(
  value: unknown,
  assertion: Assertion,
  applicationOrigin?: string,
): unknown {
  let normalized = value;
  for (const normalizer of assertion.normalizers ?? []) {
    if (normalizer === "trim" && typeof normalized === "string") {
      normalized = normalized.trim();
    } else if (normalizer === "collapse_whitespace" && typeof normalized === "string") {
      normalized = normalized.replace(/\s+/g, " ");
    } else if (normalizer === "application_origin" && typeof normalized === "string") {
      normalized = stripOrigin(normalized, applicationOrigin);
    } else if (normalizer === "network_path" && Array.isArray(normalized)) {
      normalized = normalized.map((entry) => normalizeNetworkEntry(entry, applicationOrigin));
    }
  }
  return normalized;
}

function firstDivergence(
  mission: TestMission,
  base: ExecutionResult,
  head: ExecutionResult,
  observations: RegressionComparison["observations"],
): RegressionComparison["firstDivergence"] {
  const stepCount = Math.max(base.steps.length, head.steps.length);
  for (let index = 0; index < stepCount; index += 1) {
    const baseStep = base.steps.find((step) => step.index === index);
    const headStep = head.steps.find((step) => step.index === index);
    if (baseStep?.status === "passed" && headStep?.status !== "passed") {
      return {
        stage: "step",
        stepIndex: index,
        behavior: `Frozen journey step ${index + 1}`,
        baseSummary: baseStep.status,
        headSummary: headStep?.message ?? headStep?.status ?? "missing",
      };
    }
  }
  for (let index = 0; index < observations.length; index += 1) {
    const observation = observations[index];
    if (
      (observation.basePassed && !observation.headPassed) ||
      (observation.comparison === "exact" && observation.basePassed && observation.equal === false)
    ) {
      return {
        stage: "assertion",
        assertionId: observation.assertionId,
        behavior: observation.behavior,
        baseSummary: summarize(observation.baseNormalized),
        headSummary: summarize(observation.headNormalized),
      };
    }
    if (!base.checks[index] || !head.checks[index]) {
      const assertion = mission.assertions[index];
      return {
        stage: "assertion",
        assertionId: assertion.id ?? `assertion-${index}`,
        behavior: assertion.behavior ?? `Mission assertion ${index + 1}`,
        baseSummary: base.checks[index] ? "observed" : "missing",
        headSummary: head.checks[index] ? "observed" : "missing",
      };
    }
  }
  return undefined;
}

function isExecutionError(result: ExecutionResult): boolean {
  return result.status === "error" || result.status === "blocked";
}

function requiredAttemptId(base: ExecutionResult, head: ExecutionResult): string {
  if (!base.attemptId || base.attemptId !== head.attemptId) {
    throw new Error("Regression executions must share one attempt ID.");
  }
  return base.attemptId;
}

function requiredExecutionId(result: ExecutionResult): string {
  if (!result.executionId) {
    throw new Error("Regression executions require execution IDs.");
  }
  return result.executionId;
}

function stripOrigin(value: string, origin?: string): string {
  if (!origin) {
    return value;
  }
  try {
    const parsed = new URL(value);
    return parsed.origin === new URL(origin).origin
      ? `${parsed.pathname}${parsed.search}${parsed.hash}`
      : value;
  } catch {
    return value;
  }
}

function normalizeNetworkEntry(value: unknown, origin?: string): unknown {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return value;
  }
  const entry = value as Record<string, unknown>;
  return {
    method: entry.method,
    url: typeof entry.url === "string" ? stripOrigin(entry.url, origin) : entry.url,
    status: entry.status,
  };
}

function stableJson(value: unknown): string {
  return JSON.stringify(value);
}

function summarize(value: unknown): string {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return (text ?? "missing").slice(0, 200);
}

function summarizeEvidence(result: ExecutionResult): string {
  return result.evidenceErrors?.map((error) => error.code).join(", ") || "complete";
}
