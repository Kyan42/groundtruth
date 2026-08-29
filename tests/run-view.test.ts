import { describe, expect, it } from "vitest";

import type { BrowserVerification } from "@/lib/domain/schemas";
import { buildRunView } from "@/lib/views/build-run-view";
import { BrowserVerificationSchema } from "@/lib/domain/schemas";
import { makeRun } from "./fixtures";

describe("RunView", () => {
  it("keeps intent and regression results separate before browser execution", async () => {
    const run = makeRun({
      status: "contract_approved",
      coordinationAxonId: "axn_123",
      intentSpec: {
        schemaVersion: 1,
        summary: "Separate verdicts.",
        claims: [
          {
            id: "claim-1",
            statement: "Show separate verdict groups.",
            sourceQuote: "separate intent and regression verdicts",
            priority: "must",
            acceptanceCriteria: ["Both groups are visible."],
          },
        ],
        nonGoals: [],
        ambiguities: [],
      },
      intentApproval: { approvedAt: "2026-08-29T20:10:00.000Z" },
    });

    const view = await buildRunView(run);

    expect(view.results.intent).toEqual([]);
    expect(view.results.regression).toEqual([]);
    expect(view.phases.find((phase) => phase.id === "execution")?.status).toBe("pending");
    expect(view.recording).toBeUndefined();
    expect(view.environments).toEqual([]);
  });


  it("projects a paired regression verdict without attributing it to intent claims", async () => {
    const attemptId = "62c89727-e786-4592-b886-0bc9f0f570ad";
    const mission = {
      schemaVersion: 1 as const,
      id: "regression-cart",
      title: "Cart regression",
      kind: "regression" as const,
      claimIds: [],
      goal: "Preserve the cart.",
      startPath: "/",
      preconditions: [],
      impactEvidence: {
        routes: ["/cart"],
        components: ["OrderSummary"],
        apis: [{ method: "POST", path: "/api/cart" }],
      },
      assertions: [
        {
          id: "subtotal",
          behavior: "Preserve subtotal.",
          comparison: "exact" as const,
          kind: "text" as const,
          locator: { by: "test_id" as const, value: "subtotal" },
        },
      ],
    };
    const execution = (target: "base" | "head", executionId: string) => ({
      schemaVersion: 1 as const,
      attemptId,
      executionId,
      missionId: mission.id,
      target,
      status: "passed" as const,
      startedAt: "2026-08-29T20:00:00.000Z",
      endedAt: "2026-08-29T20:01:00.000Z",
      steps: [],
      checks: [
        {
          assertionIndex: 0,
          assertionId: "subtotal",
          behavior: "Preserve subtotal.",
          comparison: "exact" as const,
          passed: true,
          actual: "$43.99",
        },
      ],
      evidence: { screenshotArtifactIds: [] },
    });
    const baseExecutionId = "a1b71fc8-24a6-4e56-98b0-6c653387cd27";
    const headExecutionId = "1b6d8628-e461-4913-8d86-f97837009566";
    const browserVerification = BrowserVerificationSchema.parse({
      attemptId,
      status: "complete",
      mission,
      environments: [],
      actions: [],
      network: [],
      executions: {
        base: execution("base", baseExecutionId),
        head: execution("head", headExecutionId),
      },
      comparison: {
        schemaVersion: 1,
        comparisonId: "4fe7cdb8-a406-475e-88d4-d16f9dc82b21",
        attemptId,
        missionId: mission.id,
        baseExecutionId,
        headExecutionId,
        verdict: "preserved",
        reason: "Head preserved the baseline.",
        observations: [
          {
            assertionId: "subtotal",
            behavior: "Preserve subtotal.",
            comparison: "exact",
            basePassed: true,
            headPassed: true,
            equal: true,
            baseNormalized: "$43.99",
            headNormalized: "$43.99",
          },
        ],
        createdAt: "2026-08-29T20:01:00.000Z",
      },
    });
    const run = makeRun({
      repository: {
        owner: "Kyan42",
        name: "fernway",
        cloneUrl: "https://github.com/Kyan42/fernway.git",
      },
      pullRequest: {
        number: 4,
        url: "https://github.com/Kyan42/fernway/pull/4",
        title: "Add promo codes at checkout",
        body: "Adds promo code support to the checkout flow.",
        baseRef: "main",
        baseSha: "db5c5ae6e25fdc3947738b37327a626394420365",
        headRef: "feature/promo-codes",
        headSha: "716b9f36e35f4f1cd1944e043bfdfb13f8f97ea4",
      },
      status: "complete",
      intentSpec: {
        schemaVersion: 1,
        summary: "Promo intent.",
        claims: [
          {
            id: "checkout-application",
            statement: "Apply discount at checkout.",
            sourceQuote: "checkout flow",
            priority: "must",
            acceptanceCriteria: ["Discount timing is verified."],
          },
        ],
        nonGoals: [],
        ambiguities: [],
      },
      intentApproval: { approvedAt: "2026-08-29T20:00:00.000Z" },
      browserVerification,
    });

    const view = await buildRunView(run);

    expect(view.results.intent).toEqual([]);
    expect(view.results.regression).toEqual([
      {
        missionId: "regression-cart",
        verdict: "preserved",
        reason: "Head preserved the baseline.",
      },
    ]);
    expect(view.verificationAttempts).toHaveLength(1);
  });

  it("marks completed replay with assertion findings as complete and non-conformant", async () => {
    const view = await buildRunView(
      makeRun({
        status: "complete",
        intentSpec: intentSpec,
        intentApproval: { approvedAt: "2026-08-29T20:10:00.000Z" },
        browserVerification: browserVerificationWith("failed"),
      }),
    );

    expect(view.phases.find((phase) => phase.id === "execution")).toMatchObject({
      status: "complete",
      detail: "Browser replay completed with assertion findings.",
    });
    expect(view.results.intent).toEqual([
      {
        missionId: "mission-claim-1",
        claimId: "claim-1",
        verdict: "non_conformant",
      },
    ]);
    expect(view.contract.selectedClaimId).toBe("claim-1");
  });

  it("reserves a failed execution phase for runner errors", async () => {
    const view = await buildRunView(
      makeRun({
        status: "complete",
        intentSpec: intentSpec,
        intentApproval: { approvedAt: "2026-08-29T20:10:00.000Z" },
        browserVerification: browserVerificationWith("error"),
      }),
    );

    expect(view.phases.find((phase) => phase.id === "execution")?.status).toBe("failed");
    expect(view.results.intent[0]?.verdict).toBe("inconclusive");
  });
});

const intentSpec = {
  schemaVersion: 1 as const,
  summary: "Separate verdicts.",
  claims: [
    {
      id: "claim-0",
      statement: "Keep the existing overview visible.",
      sourceQuote: "dashboard",
      priority: "must" as const,
      acceptanceCriteria: ["The overview is visible."],
    },
    {
      id: "claim-1",
      statement: "Show separate verdict groups.",
      sourceQuote: "separate intent and regression verdicts",
      priority: "must" as const,
      acceptanceCriteria: ["Both groups are visible."],
    },
  ],
  nonGoals: [],
  ambiguities: [],
};

function browserVerificationWith(
  executionStatus: "failed" | "error",
): BrowserVerification {
  const attemptId = "cd33af50-1572-4081-8e87-13e78d42dc89";
  return {
    attemptId,
    status: "complete",
    mission: {
      schemaVersion: 1,
      id: "mission-claim-1",
      title: "Verify separate verdict groups",
      kind: "intent",
      claimIds: ["claim-1"],
      goal: "Inspect the result groups.",
      startPath: "/",
      preconditions: [],
      assertions: [
        {
          kind: "text",
          locator: { by: "role", role: "heading", name: "Intent conformance" },
          operator: "equals",
          expected: "Intent conformance",
        },
      ],
    },
    journey: {
      schemaVersion: 1,
      missionId: "mission-claim-1",
      discoveredAgainst: "head",
      steps: [{ action: "goto", path: "/" }],
      producer: { kind: "codex", agentId: "runloop:test" },
    },
    environments: [],
    execution: {
      schemaVersion: 1,
      attemptId,
      executionId: "ea266bd4-61b5-4799-8bf0-9df36d998208",
      missionId: "mission-claim-1",
      target: "head",
      status: executionStatus,
      startedAt: "2026-08-29T20:15:00.000Z",
      endedAt: "2026-08-29T20:15:02.000Z",
      steps: [
        {
          index: 0,
          status: executionStatus === "error" ? "failed" : "passed",
        },
      ],
      checks: [
        {
          assertionIndex: 0,
          passed: false,
          actual: "Regression safety",
        },
      ],
      evidence: {
        screenshotArtifactIds: [],
      },
      error:
        executionStatus === "error"
          ? { code: "mechanical_replay_failed", message: "The browser process exited." }
          : undefined,
    },
    actions: [],
    network: [],
  };
}
