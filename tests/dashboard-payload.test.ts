import { describe, expect, it } from "vitest";

import { BrowserVerificationSchema, type IntentSpec } from "@/lib/domain/schemas";
import { buildDashboardPayload } from "@/lib/views/build-dashboard-payload";
import { makeRun } from "./fixtures";

const intentSpec: IntentSpec = {
  schemaVersion: 1,
  summary: "Tag filtering works.",
  claims: [
    {
      id: "claim-filter",
      statement: "Clicking a chip filters the list to that tag",
      sourceQuote: "clicking a chip filters the list",
      priority: "must",
      acceptanceCriteria: ["List shows only matching todos."],
    },
    {
      id: "claim-colors",
      statement: "A tag keeps the same color across sessions",
      sourceQuote: "same color across sessions",
      priority: "must",
      acceptanceCriteria: ["Colors are stable."],
    },
  ],
  nonGoals: [],
  ambiguities: [],
};

const attemptId = "62c89727-e786-4592-b886-0bc9f0f570ad";

function makeIntentAttempt() {
  return BrowserVerificationSchema.parse({
    attemptId,
    status: "complete",
    mission: {
      schemaVersion: 1,
      id: "intent-filter",
      title: "Verify tag filtering",
      kind: "intent",
      claimIds: ["claim-filter"],
      goal: "Verify the filter claim.",
      startPath: "/",
      preconditions: [],
      deferredClaims: [
        { claimId: "claim-colors", reason: "needs a signed-in session" },
      ],
      assertions: [
        {
          id: "row-count",
          behavior: "List renders only matching rows.",
          comparison: "exact",
          kind: "text",
          locator: { by: "test_id", value: "todo-list" },
        },
      ],
    },
    environments: [],
    actions: [
      {
        at: "2026-08-29T20:00:00.300Z",
        target: "head",
        summary: "opened /",
        status: "ok",
      },
      {
        at: "2026-08-29T20:00:01.400Z",
        target: "head",
        summary: 'clicked the "work" chip <on row 2>',
        status: "ok",
      },
      {
        at: "2026-08-29T20:00:04.000Z",
        target: "head",
        summary: "list still rendered 7 rows, expected 3",
        status: "failed",
      },
    ],
    network: [
      { method: "get", url: "http://localhost:3000/api/todos", status: 200, target: "head" },
      {
        method: "get",
        url: "http://localhost:3000/api/todos?tag=work",
        status: 500,
        target: "head",
      },
      { method: "get", url: "http://localhost:3000/api/todos", status: 200, target: "base" },
    ],
    execution: {
      schemaVersion: 1,
      attemptId,
      executionId: "1b6d8628-e461-4913-8d86-f97837009566",
      missionId: "intent-filter",
      target: "head",
      status: "failed",
      startedAt: "2026-08-29T20:00:00.000Z",
      endedAt: "2026-08-29T20:00:05.000Z",
      steps: [
        { index: 0, status: "passed" },
        { index: 1, status: "failed", message: "List rendered 7 rows, expected 3." },
      ],
      checks: [
        {
          assertionIndex: 0,
          assertionId: "row-count",
          behavior: "rendered 7 rows, expected 3",
          comparison: "exact",
          passed: false,
          actual: 7,
        },
      ],
      evidence: {
        videoArtifactId: "artifact-video-1",
        screenshotArtifactIds: [],
      },
    },
  });
}

describe("buildDashboardPayload", () => {
  it("projects a failed intent claim into the dashboard contract", () => {
    const run = makeRun({
      status: "complete",
      intentSpec,
      intentApproval: { approvedAt: "2026-08-29T20:10:00.000Z" },
      browserVerification: makeIntentAttempt(),
    });

    const payload = buildDashboardPayload(run);

    expect(payload.number).toBe(1);
    expect(payload.branch).toBe("feature");
    expect(payload.prUrl).toBe("https://github.com/Kyan42/groundtruth/pull/1");
    expect(payload.duration).toBe("5s");

    expect(payload.claims.map((claim) => claim.id)).toEqual(["claim-filter", "claim-colors"]);

    const failed = payload.claims[0];
    expect(failed.verdict).toBe("f");
    expect(failed.text).toBe("Clicking a chip filters the list to that tag");
    expect(failed.sub).toBe("rendered 7 rows, expected 3");
    expect(failed.verdict_line).toContain("<b>Does not match your description.</b>");
    expect(failed.verdict_line).toContain("List rendered 7 rows, expected 3.");
    expect(failed.dur).toBe(5);
    expect(failed.videoUrl).toBe("/api/artifacts/artifact-video-1");

    expect(failed.steps[0]).toEqual([0.3, "goto", "opened /"]);
    expect(failed.steps[1][1]).toBe("click");
    expect(failed.steps[1][2]).toBe("clicked the &quot;work&quot; chip &lt;on row 2&gt;");
    expect(failed.steps[2][3]).toBe(1);

    expect(failed.net).toEqual([
      ["GET", "/api/todos", "200", ""],
      ["GET", "/api/todos?tag=work", "500", "", "mismatch"],
    ]);
  });

  it("marks deferred claims unverified with the deferral reason", () => {
    const run = makeRun({
      status: "complete",
      intentSpec,
      browserVerification: makeIntentAttempt(),
    });

    const deferred = buildDashboardPayload(run).claims[1];
    expect(deferred.verdict).toBe("u");
    expect(deferred.sub).toBe("needs a signed-in session");
    expect(deferred.verdict_line).toContain("<b>Could not verify.</b>");
    expect(deferred.steps).toEqual([]);
    expect(deferred.net).toEqual([]);
    expect(deferred.dur).toBe(1);
  });

  it("keeps every claim unverified while the run is still pending", () => {
    const run = makeRun({ status: "awaiting_contract_approval", intentSpec });

    const payload = buildDashboardPayload(run);

    expect(payload.cost).toBe("n/a");
    expect(payload.claims).toHaveLength(2);
    for (const claim of payload.claims) {
      expect(claim.verdict).toBe("u");
      expect(claim.sub).toBe("awaiting contract approval");
      expect(claim.dur).toBe(1);
    }
  });

  it("surfaces one honest status row before the intent contract exists", () => {
    const run = makeRun({ status: "analyzing_intent" });

    const payload = buildDashboardPayload(run);

    expect(payload.claims).toHaveLength(1);
    expect(payload.claims[0].id).toBe("GT");
    expect(payload.claims[0].verdict).toBe("u");
    expect(payload.claims[0].sub).toBe("intent analysis in progress");
  });

  it("appends regression rows after intent claims without sorting claims", () => {
    const regressionAttempt = BrowserVerificationSchema.parse({
      attemptId: "7b1f4f9e-63f4-4d43-b355-8c2edbd47ae5",
      status: "complete",
      mission: {
        schemaVersion: 1,
        id: "regression-done",
        title: "Completing a todo still moves it to Done",
        kind: "regression",
        claimIds: [],
        goal: "Preserve completion.",
        startPath: "/",
        preconditions: [],
        assertions: [
          {
            id: "done-row",
            behavior: "Done list keeps the row.",
            comparison: "pass_only",
            kind: "text",
            locator: { by: "test_id", value: "done-list" },
          },
        ],
      },
      environments: [],
      actions: [],
      network: [],
      executions: {
        base: {
          schemaVersion: 1,
          attemptId: "7b1f4f9e-63f4-4d43-b355-8c2edbd47ae5",
          executionId: "a1b71fc8-24a6-4e56-98b0-6c653387cd27",
          missionId: "regression-done",
          target: "base",
          status: "passed",
          startedAt: "2026-08-29T20:02:00.000Z",
          endedAt: "2026-08-29T20:02:04.000Z",
          steps: [],
          checks: [],
          evidence: { screenshotArtifactIds: [] },
        },
        head: {
          schemaVersion: 1,
          attemptId: "7b1f4f9e-63f4-4d43-b355-8c2edbd47ae5",
          executionId: "e3d1c9aa-90cf-4c60-9d8c-3a2f1c1de111",
          missionId: "regression-done",
          target: "head",
          status: "passed",
          startedAt: "2026-08-29T20:02:05.000Z",
          endedAt: "2026-08-29T20:02:09.000Z",
          steps: [],
          checks: [],
          evidence: { screenshotArtifactIds: [] },
        },
      },
      comparison: {
        schemaVersion: 1,
        comparisonId: "4fe7cdb8-a406-475e-88d4-d16f9dc82b21",
        attemptId: "7b1f4f9e-63f4-4d43-b355-8c2edbd47ae5",
        missionId: "regression-done",
        baseExecutionId: "a1b71fc8-24a6-4e56-98b0-6c653387cd27",
        headExecutionId: "e3d1c9aa-90cf-4c60-9d8c-3a2f1c1de111",
        verdict: "preserved",
        reason: "No drift against main across the replayed journey.",
        observations: [],
        createdAt: "2026-08-29T20:02:10.000Z",
      },
    });

    const run = makeRun({
      status: "complete",
      intentSpec,
      browserVerificationHistory: [makeIntentAttempt()],
      browserVerification: regressionAttempt,
    });

    const payload = buildDashboardPayload(run);

    expect(payload.claims.map((claim) => claim.id)).toEqual([
      "claim-filter",
      "claim-colors",
      "R1",
    ]);
    const regression = payload.claims[2];
    expect(regression.verdict).toBe("p");
    expect(regression.sub).toBe("");
    expect(regression.verdict_line).toContain("<b>No drift.</b>");
    expect(regression.text).toBe("Completing a todo still moves it to Done");
  });
});
