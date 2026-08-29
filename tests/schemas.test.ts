import { describe, expect, it } from "vitest";

import { IntentSpecSchema } from "@/lib/domain/schemas";
import { parseIntentSpec } from "@/lib/reflex/intent-agent";
import { makeRun } from "./fixtures";

describe("IntentSpec", () => {
  it("accepts the minimum explicit contract", () => {
    const result = IntentSpecSchema.parse({
      schemaVersion: 1,
      summary: "Separate the two verdict groups.",
      claims: [
        {
          id: "claim-1",
          statement: "Show separate intent and regression verdicts.",
          sourceQuote: "separate intent and regression verdicts",
          priority: "must",
          acceptanceCriteria: ["Both result groups are independently visible."],
        },
      ],
      nonGoals: [],
      ambiguities: [],
    });

    expect(result.claims).toHaveLength(1);
  });

  it("rejects a claim whose quote is not in the PR prose", () => {
    const run = makeRun();
    const value = JSON.stringify({
      schemaVersion: 1,
      summary: "Invented behavior.",
      claims: [
        {
          id: "claim-1",
          statement: "Add production authentication.",
          sourceQuote: "production authentication",
          priority: "must",
          acceptanceCriteria: ["Authentication exists."],
        },
      ],
      nonGoals: [],
      ambiguities: [],
    });

    expect(() => parseIntentSpec(value, run)).toThrowError(/quote/i);
  });
});
