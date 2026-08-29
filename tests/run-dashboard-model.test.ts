import { describe, expect, it } from "vitest";

import {
  buildDashboardItems,
  buildVerdictNarrative,
  getDefaultDashboardItemKey,
  normalizeNetworkRows,
} from "@/components/run-dashboard-model";
import type { RunView } from "@/lib/domain/schemas";

describe("run dashboard model", () => {
  it("groups executed results first, counts coverage, and selects a failure", () => {
    const view = completedView();
    view.missions.push({
      ...view.missions[0],
      id: "regression-cart",
      title: "Preserve cart behavior",
      kind: "regression",
      claimIds: [],
      deferredClaims: [],
    });
    view.results.regression.push({
      missionId: "regression-cart",
      verdict: "preserved",
      reason: "Head preserved the baseline.",
    });

    const items = buildDashboardItems(view);

    expect(items.intent.map((item) => [item.id, item.status])).toEqual([
      ["claim-failed", "non_conformant"],
    ]);
    expect(items.notRun.map((item) => [item.id, item.status, item.detail])).toEqual([
      ["claim-deferred", "deferred", "A separate fixture is required."],
      ["claim-uncovered", "uncovered", "No browser mission is linked to this claim."],
    ]);
    expect(items.coverage).toEqual({
      intentExercised: 1,
      intentTotal: 3,
      regressionExecuted: 1,
      regressionTotal: 1,
    });
    expect(items.all.map((item) => item.key)).toEqual([
      "intent:claim-failed",
      "regression:regression-cart",
    ]);
    expect(getDefaultDashboardItemKey(view)).toBe("intent:claim-failed");
  });

  it("falls back to the first covered executed result before the first claim", () => {
    const view = completedView();
    view.results.intent = [
      {
        missionId: "mission-covered",
        claimId: "claim-uncovered",
        verdict: "conformant",
      },
    ];
    view.missions[0] = {
      ...view.missions[0],
      id: "mission-covered",
      claimIds: ["claim-uncovered"],
      deferredClaims: [
        { claimId: "claim-deferred", reason: "A separate fixture is required." },
        { claimId: "claim-failed", reason: "A separate path is required." },
      ],
    };
    view.journey = {
      ...view.journey!,
      missionId: "mission-covered",
    };
    view.contract.claimCoverage = [
      {
        claimId: "claim-deferred",
        status: "deferred",
        reason: "A separate fixture is required.",
      },
      {
        claimId: "claim-uncovered",
        status: "covered",
        missionId: "mission-covered",
      },
      {
        claimId: "claim-failed",
        status: "deferred",
        reason: "A separate path is required.",
      },
    ];

    expect(getDefaultDashboardItemKey(view)).toBe("intent:claim-uncovered");
  });

  it("keeps every claim selectable while the contract is awaiting execution", () => {
    const view = completedView();
    view.run.status = "contract_approved";
    view.results.intent = [];
    view.journey = undefined;
    view.recording = undefined;
    view.recordings = [];
    view.actions = [];

    const items = buildDashboardItems(view);

    expect(items.collapseNotRun).toBe(false);
    expect(items.intent.map((item) => item.id)).toEqual([
      "claim-deferred",
      "claim-uncovered",
      "claim-failed",
    ]);
    expect(items.notRun).toEqual([]);
    expect(getDefaultDashboardItemKey(view)).toBe("intent:claim-deferred");
  });

  it("has no selectable evidence item when a terminal run executed nothing", () => {
    const view = completedView();
    view.results.intent = [];
    view.journey = undefined;
    view.recording = undefined;
    view.recordings = [];
    view.actions = [];

    const items = buildDashboardItems(view);

    expect(items.collapseNotRun).toBe(true);
    expect(items.coverage.intentExercised).toBe(0);
    expect(items.intent).toEqual([]);
    expect(items.notRun).toHaveLength(3);
    expect(items.all).toEqual([]);
    expect(getDefaultDashboardItemKey(view)).toBeUndefined();
  });

  it("prioritizes API traffic and removes origins, query values, and fragments", () => {
    const rows = normalizeNetworkRows([
      {
        method: "get",
        url: "https://credential.tunnel.runloop.ai/_next/static/app.js?token=secret",
        status: 200,
        target: "head",
      },
      {
        method: "post",
        url: "https://credential.tunnel.runloop.ai/api/cart?session=secret#result",
        status: 201,
        target: "head",
      },
      {
        method: "GET",
        url: "https://credential.tunnel.runloop.ai/cart",
        status: 200,
        target: "head",
      },
    ]);

    expect(rows.map(({ method, path, status }) => ({ method, path, status }))).toEqual([
      { method: "POST", path: "/api/cart", status: 201 },
      { method: "GET", path: "/cart", status: 200 },
      { method: "GET", path: "/_next/static/app.js", status: 200 },
    ]);
    expect(JSON.stringify(rows)).not.toContain("tunnel.runloop.ai");
    expect(JSON.stringify(rows)).not.toContain("secret");
  });

  it("uses evidence-backed narrative fallbacks without inventing an assertion cause", () => {
    const view = completedView();
    const items = buildDashboardItems(view);
    const failed = items.intent.find((item) => item.id === "claim-failed");
    const deferred = items.notRun.find((item) => item.id === "claim-deferred");

    expect(buildVerdictNarrative(view, failed)).toEqual({
      title: "Does not match the intent contract.",
      body: "All 2 recorded replay steps completed, but the linked mission produced a non-conformant assertion result.",
      missionLabel: "Verify the changed checkout behavior",
    });
    expect(buildVerdictNarrative(view, deferred).body).toBe(
      "A separate fixture is required.",
    );
    expect(buildVerdictNarrative(view, failed).body).not.toMatch(/API returned|because/i);
  });
});

function completedView(): RunView {
  return {
    run: {
      id: "55e16cce-fe72-4c04-8acf-d2fd3ef55fd0",
      status: "complete",
      repository: "example/storefront",
      pullRequestNumber: 42,
      pullRequestUrl: "https://github.com/example/storefront/pull/42",
      title: "Change checkout behavior",
      headSha: "b".repeat(40),
      coordinationAxonId: "axn_test",
    },
    setup: {
      ready: true,
      blockers: [],
    },
    phases: [
      { id: "pr", label: "PR ingested", status: "complete" },
      { id: "intent", label: "Intent contract", status: "complete" },
      { id: "approval", label: "Human approval", status: "complete" },
      { id: "impact", label: "Trusted AppMap", status: "complete" },
      { id: "plan", label: "Mission planning", status: "complete" },
      {
        id: "execution",
        label: "Browser execution",
        status: "complete",
        detail: "Browser replay completed with assertion findings.",
      },
    ],
    contract: {
      status: "approved",
      intentSpec: {
        schemaVersion: 1,
        summary: "Verify the checkout change.",
        claims: [
          {
            id: "claim-deferred",
            statement: "A deferred behavior remains supported.",
            sourceQuote: "deferred behavior",
            priority: "must",
            acceptanceCriteria: ["The behavior remains available."],
          },
          {
            id: "claim-uncovered",
            statement: "An uncovered behavior remains supported.",
            sourceQuote: "uncovered behavior",
            priority: "must",
            acceptanceCriteria: ["The behavior remains available."],
          },
          {
            id: "claim-failed",
            statement: "Checkout changes only after confirmation.",
            sourceQuote: "changes only after confirmation",
            priority: "must",
            acceptanceCriteria: ["The total does not change early."],
          },
        ],
        ambiguities: [],
        nonGoals: [],
      },
      selectedClaimId: "claim-deferred",
      claimCoverage: [
        {
          claimId: "claim-deferred",
          status: "deferred",
          reason: "A separate fixture is required.",
        },
        {
          claimId: "claim-uncovered",
          status: "uncovered",
        },
        {
          claimId: "claim-failed",
          status: "covered",
          missionId: "mission-failed",
        },
      ],
    },
    missions: [
      {
        schemaVersion: 1,
        id: "mission-failed",
        title: "Verify the changed checkout behavior",
        kind: "intent",
        claimIds: ["claim-failed"],
        goal: "Exercise checkout before confirmation.",
        startPath: "/",
        preconditions: [],
        deferredClaims: [
          { claimId: "claim-deferred", reason: "A separate fixture is required." },
        ],
        assertions: [
          {
            kind: "url",
            operator: "equals",
            expected: "/checkout",
          },
        ],
      },
    ],
    journey: {
      schemaVersion: 1,
      missionId: "mission-failed",
      discoveredAgainst: "head",
      steps: [
        { action: "goto", path: "/" },
        {
          action: "click",
          locator: { by: "role", role: "button", name: "Checkout" },
        },
      ],
      producer: { kind: "codex", agentId: "runloop:test" },
    },
    environments: [],
    results: {
      intent: [
        {
          missionId: "mission-failed",
          claimId: "claim-failed",
          verdict: "non_conformant",
        },
      ],
      regression: [],
    },
    recording: {
      artifactId: "artifact-video",
      contentType: "video/webm",
    },
    recordings: [
      {
        target: "head",
        artifactId: "artifact-video",
        contentType: "video/webm",
      },
    ],
    verificationAttempts: [],
    actions: [
      {
        at: "2026-08-29T20:00:00.000Z",
        target: "head",
        summary: "Navigate to /",
        status: "passed",
      },
      {
        at: "2026-08-29T20:00:01.000Z",
        target: "head",
        summary: "click",
        status: "passed",
      },
    ],
    network: [],
  };
}
