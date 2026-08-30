import { describe, expect, it } from "vitest";

import type { TestMission } from "@/lib/domain/schemas";
import { BrowserVerificationSchema } from "@/lib/domain/schemas";
import { resolveMission } from "@/lib/runloop/browser-verification";
import { buildClaimCoverage, buildResults } from "@/lib/views/build-run-view";
import { makeRun } from "./fixtures";

const claims = [
  {
    id: "checkout-application",
    statement: "Apply the discount only at checkout.",
    sourceQuote:
      "The discount is applied at checkout, so the cart total stays undiscounted until the order is placed",
    priority: "must" as const,
    acceptanceCriteria: ["The pre-checkout cart has no discount."],
  },
  {
    id: "invalid-code-error",
    statement: "Keep invalid codes visible with an error.",
    sourceQuote: "invalid codes show an inline error",
    priority: "must" as const,
    acceptanceCriteria: ["The invalid code remains in the field."],
  },
  {
    id: "discount-types",
    statement: "Support percentage and fixed discounts.",
    sourceQuote: "percentage and fixed discounts",
    priority: "must" as const,
    acceptanceCriteria: ["Both discount types work."],
  },
  {
    id: "optional-copy",
    statement: "Use concise helper copy.",
    sourceQuote: "concise helper copy",
    priority: "should" as const,
    acceptanceCriteria: ["Helper copy is concise."],
  },
];

const mission: TestMission = {
  schemaVersion: 1,
  id: "checkout-timing",
  title: "Checkout timing",
  kind: "intent",
  claimIds: ["checkout-application"],
  goal: "Verify the cart before order placement.",
  startPath: "/",
  preconditions: [],
  deferredClaims: [
    {
      claimId: "invalid-code-error",
      reason: "Invalid codes require a separate fixture.",
    },
  ],
  assertions: [
    {
      kind: "dom",
      locator: { by: "test_id", value: "discount" },
      state: "hidden",
    },
  ],
};

const run = makeRun({
  intentSpec: {
    schemaVersion: 1,
    summary: "Promo intent",
    claims,
    nonGoals: [],
    ambiguities: [],
  },
});

describe("claim coverage", () => {
  it.each(["claim-3", "checkout-application"])(
    "binds an exact source quote to the current generated claim ID %s",
    (claimId) => {
      const sourceQuoteMission = {
        ...mission,
        claimIds: ["old-unstable-id"],
        claimSourceQuote: claims[0].sourceQuote,
      };
      const currentRun = makeRun({
        intentSpec: {
          schemaVersion: 1,
          summary: "Promo intent",
          claims: [{ ...claims[0], id: claimId }, ...claims.slice(1)],
          nonGoals: [],
          ambiguities: [],
        },
      });

      expect(resolveMission(sourceQuoteMission, currentRun).claimIds).toEqual([claimId]);
    },
  );

  it("rejects a source quote that has no exact approved claim match", () => {
    expect(() =>
      resolveMission(
        {
          ...mission,
          claimIds: ["checkout-application"],
          claimSourceQuote: `${claims[0].sourceQuote}.`,
        },
        run,
      ),
    ).toThrowError(
      expect.objectContaining({ code: "test_mission_claim_source_quote_unmatched" }),
    );
  });

  it("rejects a source quote that matches multiple approved claims", () => {
    const duplicateQuoteRun = makeRun({
      intentSpec: {
        schemaVersion: 1,
        summary: "Promo intent",
        claims: [
          ...claims,
          { ...claims[1], id: "duplicate-quote", sourceQuote: claims[0].sourceQuote },
        ],
        nonGoals: [],
        ambiguities: [],
      },
    });

    expect(() =>
      resolveMission(
        { ...mission, claimIds: [], claimSourceQuote: claims[0].sourceQuote },
        duplicateQuoteRun,
      ),
    ).toThrowError(
      expect.objectContaining({ code: "test_mission_claim_source_quote_ambiguous" }),
    );
  });

  it("rejects a deferred claim that overlaps the quote-resolved claim", () => {
    expect(() =>
      resolveMission(
        {
          ...mission,
          claimIds: [],
          claimSourceQuote: claims[0].sourceQuote,
          deferredClaims: [
            { claimId: "checkout-application", reason: "Must execute separately." },
          ],
        },
        run,
      ),
    ).toThrowError(expect.objectContaining({ code: "test_mission_deferral_stale" }));
  });

  it("rejects a mission without an explicit claim", () => {
    expect(() => resolveMission({ ...mission, claimIds: [] }, run)).toThrowError(
      expect.objectContaining({ code: "test_mission_claim_missing" }),
    );
  });

  it("rejects aggregate missions that could fan one verdict across claims", () => {
    expect(() =>
      resolveMission(
        { ...mission, claimIds: ["checkout-application", "invalid-code-error"] },
        run,
      ),
    ).toThrowError(
      expect.objectContaining({ code: "test_mission_claim_scope_unsupported" }),
    );
  });

  it("keeps intent assertions strict when regression observations use a base baseline", () => {
    expect(() =>
      resolveMission(
        {
          ...mission,
          assertions: [
            {
              kind: "text",
              locator: { by: "test_id", value: "discount" },
            },
          ],
        },
        run,
      ),
    ).toThrowError(expect.objectContaining({ code: "test_mission_assertion_incomplete" }));
  });

  it("projects every must claim as covered, deferred, or uncovered", () => {
    expect(buildClaimCoverage(run, mission)).toEqual([
      {
        claimId: "checkout-application",
        status: "covered",
        missionId: "checkout-timing",
      },
      {
        claimId: "invalid-code-error",
        status: "deferred",
        reason: "Invalid codes require a separate fixture.",
      },
      {
        claimId: "discount-types",
        status: "uncovered",
      },
    ]);
  });

  it("projects coverage across separate claim-specific missions", () => {
    const secondMission: TestMission = {
      ...mission,
      id: "invalid-promo",
      claimIds: ["invalid-code-error"],
      deferredClaims: [],
    };

    expect(buildClaimCoverage(run, [mission, secondMission])).toEqual([
      {
        claimId: "checkout-application",
        status: "covered",
        missionId: "checkout-timing",
      },
      {
        claimId: "invalid-code-error",
        status: "covered",
        missionId: "invalid-promo",
      },
      {
        claimId: "discount-types",
        status: "uncovered",
      },
    ]);
  });

  it("resolves deferred claims by exact source quote", () => {
    const resolved = resolveMission(
      {
        ...mission,
        deferredClaims: [
          {
            claimId: "unstable-placeholder",
            claimSourceQuote: claims[2].sourceQuote,
            reason: "Needs two fixtures.",
          },
        ],
      },
      run,
    );

    expect(resolved.deferredClaims).toEqual([
      {
        claimId: "discount-types",
        claimSourceQuote: claims[2].sourceQuote,
        reason: "Needs two fixtures.",
      },
    ]);
  });

  it.each([
    ["failed", "non_conformant"],
    ["error", "inconclusive"],
  ] as const)("maps %s execution only to its explicit claim", (status, verdict) => {
    const attemptId = "62c89727-e786-4592-b886-0bc9f0f570ad";
    const browser = BrowserVerificationSchema.parse({
      attemptId,
      status: "complete",
      mission,
      environments: [],
      actions: [],
      network: [],
      execution: {
        schemaVersion: 1,
        attemptId,
        executionId: "a1b71fc8-24a6-4e56-98b0-6c653387cd27",
        missionId: mission.id,
        target: "head",
        status,
        startedAt: "2026-08-29T20:00:00.000Z",
        endedAt: "2026-08-29T20:01:00.000Z",
        steps: [],
        checks: [{ assertionIndex: 0, passed: false, actual: true }],
        evidence: { screenshotArtifactIds: [] },
      },
    });

    expect(buildResults(browser).intent).toEqual([
      {
        missionId: mission.id,
        claimId: "checkout-application",
        verdict,
      },
    ]);
  });

  it("does not attribute legacy fallback evidence to a claim", () => {
    const browser = BrowserVerificationSchema.parse({
      status: "complete",
      mission,
      environments: [],
      actions: [],
      network: [],
      execution: {
        schemaVersion: 1,
        missionId: mission.id,
        target: "head",
        status: "passed",
        startedAt: "2026-08-29T20:00:00.000Z",
        endedAt: "2026-08-29T20:01:00.000Z",
        steps: [],
        checks: [{ assertionIndex: 0, passed: true, actual: false }],
        evidence: { screenshotArtifactIds: [] },
      },
    });

    expect(buildResults(browser).intent).toEqual([]);
  });
});
