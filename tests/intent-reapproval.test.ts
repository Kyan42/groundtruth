import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import type { ReflexStreamEvent } from "@runloop/reflex-client";

import { resetEnvironmentForTests } from "@/lib/config/env";
import type { IntentSpec } from "@/lib/domain/schemas";
import { makeRun } from "./fixtures";

vi.mock("@/lib/runloop/coordination-axon", () => ({
  createCoordinationAxon: vi.fn().mockResolvedValue("axn_test"),
  publishRunEvent: vi.fn().mockResolvedValue(undefined),
  saveIntentContract: vi.fn().mockResolvedValue(undefined),
}));

let stateDir: string;

beforeAll(async () => {
  stateDir = await mkdtemp(path.join(tmpdir(), "groundtruth-reapproval-"));
  process.env.GROUNDTRUTH_STATE_DIR = stateDir;
  resetEnvironmentForTests();
});

afterAll(async () => {
  delete process.env.GROUNDTRUTH_STATE_DIR;
  resetEnvironmentForTests();
  await rm(stateDir, { recursive: true, force: true });
});

const intentSpec: IntentSpec = {
  schemaVersion: 1,
  summary: "Dashboard shows separate verdicts.",
  claims: [
    {
      id: "claim-verdicts",
      statement: "The dashboard shows separate intent and regression verdicts",
      sourceQuote: "separate intent and regression verdicts",
      priority: "must",
      acceptanceCriteria: ["Both verdicts render."],
    },
  ],
  nonGoals: [],
  ambiguities: [],
};

function event(
  id: string,
  sequence: number,
  type: string,
  payload: unknown,
): ReflexStreamEvent {
  return { id, sequence, streamId: "axn_stream", type, payload, timestamp: sequence };
}

describe("re-approving a re-extracted intent contract", () => {
  it("approves a run whose stale approval survived a re-provision", async () => {
    // Regression: re-submitting a setup_required run replays the intent
    // stream, landing back in awaiting_contract_approval with the previous
    // intentApproval still attached; approve_intent then 409'd forever.
    const { RunService } = await import("@/lib/orchestration/run-service");
    const { getRunIndex } = await import("@/lib/persistence/json-run-index");
    const run = makeRun({
      id: "11111111-1111-4111-8111-111111111111",
      key: `kyan42/groundtruth#1@${"c".repeat(40)}`,
      status: "awaiting_contract_approval",
      intentSpec,
      intentApproval: { approvedAt: "2026-08-30T01:31:09.369Z" },
      coordinationAxonId: "axn_test",
      provisioning: { intentValidatedPublished: true },
    });
    await getRunIndex().save(run);

    const view = await new RunService().approveIntent(run.id);

    expect(view.run.status).toBe("contract_approved");
    const stored = await getRunIndex().getById(run.id);
    expect(stored?.intentApproval?.approvedAt).toBeDefined();
    expect(stored?.intentApproval?.approvedAt).not.toBe("2026-08-30T01:31:09.369Z");
  });

  it("clears a stale approval when a new contract is extracted", async () => {
    const { RunService } = await import("@/lib/orchestration/run-service");
    const { getRunIndex } = await import("@/lib/persistence/json-run-index");
    const run = makeRun({
      id: "22222222-2222-4222-8222-222222222222",
      key: `kyan42/groundtruth#2@${"d".repeat(40)}`,
      status: "analyzing_intent",
      intentApproval: { approvedAt: "2026-08-30T01:31:09.369Z" },
      coordinationAxonId: "axn_test",
      reflexIntent: { agentId: "agt_test", streamId: "axn_stream", status: "running" },
    });
    await getRunIndex().save(run);

    const service = new RunService();
    await service.processIntentEvent(
      run.id,
      event("evt-1", 1, "item/completed", {
        item: { type: "agentMessage", text: JSON.stringify(intentSpec) },
      }),
    );
    const view = await service.processIntentEvent(
      run.id,
      event("evt-2", 2, "turn/completed", {}),
    );

    expect(view.run.status).toBe("awaiting_contract_approval");
    const stored = await getRunIndex().getById(run.id);
    expect(stored?.intentApproval).toBeUndefined();
  });
});
