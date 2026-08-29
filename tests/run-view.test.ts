import { describe, expect, it } from "vitest";

import { buildRunView } from "@/lib/views/build-run-view";
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
});
