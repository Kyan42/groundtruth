import { describe, expect, it } from "vitest";

import { compareRegressionExecutions } from "@/lib/domain/regression-comparison";
import type { ExecutionResult, TestMission } from "@/lib/domain/schemas";

const attemptId = "62c89727-e786-4592-b886-0bc9f0f570ad";

const mission: TestMission = {
  schemaVersion: 1,
  id: "existing-cart",
  title: "Existing cart",
  kind: "regression",
  claimIds: [],
  goal: "Preserve a base-observed cart total.",
  startPath: "/",
  preconditions: [],
  impactEvidence: {
    routes: ["/cart"],
    components: ["OrderSummary"],
    apis: [{ method: "POST", path: "/api/cart" }],
  },
  assertions: [
    {
      id: "total",
      behavior: "The total is preserved.",
      comparison: "exact",
      normalizers: ["trim"],
      kind: "text",
      locator: { by: "test_id", value: "order-total" },
    },
  ],
};

function execution(
  target: "base" | "head",
  status: ExecutionResult["status"],
  actual: unknown,
  passed = status === "passed",
): ExecutionResult {
  return {
    schemaVersion: 1,
    attemptId,
    executionId:
      target === "base"
        ? "a1b71fc8-24a6-4e56-98b0-6c653387cd27"
        : "1b6d8628-e461-4913-8d86-f97837009566",
    missionId: mission.id,
    target,
    status,
    startedAt: "2026-08-29T20:00:00.000Z",
    endedAt: "2026-08-29T20:01:00.000Z",
    steps: [{ index: 0, status: "passed" }],
    checks: [
      {
        assertionIndex: 0,
        assertionId: "total",
        behavior: "The total is preserved.",
        comparison: "exact",
        passed,
        actual,
      },
    ],
    evidence: { screenshotArtifactIds: [] },
  };
}

describe("regression comparison", () => {
  it("preserves matching base and head observations after configured normalization", () => {
    const comparison = compareRegressionExecutions(
      mission,
      execution("base", "passed", " $43.99 "),
      execution("head", "passed", "$43.99"),
    );

    expect(comparison.verdict).toBe("preserved");
    expect(comparison.firstDivergence).toBeUndefined();
  });

  it("marks a material head divergence as a regression and identifies it", () => {
    const comparison = compareRegressionExecutions(
      mission,
      execution("base", "passed", "$43.99"),
      execution("head", "passed", "$38.00"),
    );

    expect(comparison.verdict).toBe("regressed");
    expect(comparison.firstDivergence).toMatchObject({
      stage: "assertion",
      assertionId: "total",
      baseSummary: "$43.99",
      headSummary: "$38.00",
    });
  });

  it.each([
    ["error", "passed"],
    ["passed", "error"],
    ["failed", "failed"],
    ["failed", "passed"],
  ] as const)("classifies base %s and head %s as inconclusive", (baseStatus, headStatus) => {
    const comparison = compareRegressionExecutions(
      mission,
      execution("base", baseStatus, "base", false),
      execution("head", headStatus, "head", headStatus === "passed"),
    );

    expect(comparison.verdict).toBe("inconclusive");
  });

  it("marks base pass and head assertion failure as regressed", () => {
    const comparison = compareRegressionExecutions(
      mission,
      execution("base", "passed", "$43.99"),
      execution("head", "failed", "$43.99", false),
    );

    expect(comparison.verdict).toBe("regressed");
  });

  it("does not hide a product regression behind an artifact failure", () => {
    const head = execution("head", "failed", "$38.00", false);
    head.evidenceErrors = [{ code: "artifact_persistence_failed", message: "Video upload failed." }];
    const comparison = compareRegressionExecutions(
      mission,
      execution("base", "passed", "$43.99"),
      head,
    );

    expect(comparison.verdict).toBe("regressed");
  });

  it("is inconclusive when observations match but evidence persistence is incomplete", () => {
    const head = execution("head", "passed", "$43.99");
    head.evidenceErrors = [{ code: "artifact_persistence_failed", message: "Trace upload failed." }];
    const comparison = compareRegressionExecutions(
      mission,
      execution("base", "passed", "$43.99"),
      head,
    );

    expect(comparison.verdict).toBe("inconclusive");
  });
});
