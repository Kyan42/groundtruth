import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { resetEnvironmentForTests } from "@/lib/config/env";
import { BrowserVerificationSchema, type IntentSpec } from "@/lib/domain/schemas";
import { buildResultCommentBody, syncPullRequestResult } from "@/lib/github/pr-sync";
import { resetPrCommentIndexForTests } from "@/lib/persistence/pr-comment-index";
import { buildDashboardPayload } from "@/lib/views/build-dashboard-payload";
import { makeRun } from "./fixtures";

const ENV_NAMES = ["GITHUB_TOKEN", "GITHUB_REPO", "DASHBOARD_URL"] as const;
const previous = Object.fromEntries(ENV_NAMES.map((name) => [name, process.env[name]]));

let stateDir: string;

beforeEach(async () => {
  stateDir = await mkdtemp(path.join(tmpdir(), "groundtruth-pr-sync-"));
  process.env.GROUNDTRUTH_STATE_DIR = stateDir;
  process.env.GITHUB_TOKEN = "ghp_test_token";
  process.env.GITHUB_REPO = "Kyan42/fernway";
  process.env.DASHBOARD_URL = "https://dashboard.example.dev";
  resetEnvironmentForTests();
  resetPrCommentIndexForTests();
});

afterEach(async () => {
  vi.unstubAllGlobals();
  for (const name of ENV_NAMES) {
    const value = previous[name];
    if (value === undefined) {
      delete process.env[name];
    } else {
      process.env[name] = value;
    }
  }
  resetEnvironmentForTests();
  await rm(stateDir, { recursive: true, force: true });
});

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
  ],
  nonGoals: [],
  ambiguities: [],
};

function makeFailedRun() {
  const attemptId = "62c89727-e786-4592-b886-0bc9f0f570ad";
  return makeRun({
    status: "failed",
    intentSpec,
    intentApproval: { approvedAt: "2026-08-29T20:10:00.000Z" },
    browserVerification: BrowserVerificationSchema.parse({
      attemptId,
      status: "failed",
      mission: {
        schemaVersion: 1,
        id: "intent-filter",
        title: "Verify tag filtering",
        kind: "intent",
        claimIds: ["claim-filter"],
        goal: "Verify the filter claim.",
        startPath: "/",
        preconditions: [],
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
      actions: [],
      network: [],
      execution: {
        schemaVersion: 1,
        attemptId,
        executionId: "1b6d8628-e461-4913-8d86-f97837009566",
        missionId: "intent-filter",
        target: "head",
        status: "failed",
        startedAt: "2026-08-29T20:00:00.000Z",
        endedAt: "2026-08-29T20:00:05.000Z",
        steps: [{ index: 0, status: "failed", message: "List rendered 7 rows, expected 3." }],
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
        evidence: { screenshotArtifactIds: [] },
      },
    }),
  });
}

describe("buildResultCommentBody", () => {
  it("summarizes verdicts and links to the dashboard", () => {
    const payload = buildDashboardPayload(makeFailedRun());
    const body = buildResultCommentBody(payload, "https://dashboard.example.dev/runs/abc");

    expect(body).toContain("0 of 1 claims verified.");
    expect(body).toContain("1 does not match your description.");
    expect(body).toContain("✗ **claim-filter**");
    expect(body).not.toContain("<b>");
    expect(body).toContain("[Open the dashboard](https://dashboard.example.dev/runs/abc)");
  });
});

describe("syncPullRequestResult", () => {
  it("does nothing when GITHUB_TOKEN or GITHUB_REPO is not configured", async () => {
    delete process.env.GITHUB_TOKEN;
    resetEnvironmentForTests();
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await syncPullRequestResult(makeFailedRun());

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("posts a new comment and a failure status for a failed claim", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(Response.json({ id: 555 })) // create comment
      .mockResolvedValueOnce(Response.json({})); // status
    vi.stubGlobal("fetch", fetchMock);

    const run = makeFailedRun();
    await syncPullRequestResult(run);

    expect(fetchMock).toHaveBeenCalledTimes(2);

    const [commentUrl, commentInit] = fetchMock.mock.calls[0];
    expect(commentUrl).toBe(
      "https://api.github.com/repos/Kyan42/groundtruth/issues/1/comments",
    );
    expect(commentInit.method).toBe("POST");
    expect(commentInit.headers.Authorization).toBe("Bearer ghp_test_token");
    expect(JSON.parse(commentInit.body).body).toContain("does not match your description");

    const [statusUrl, statusInit] = fetchMock.mock.calls[1];
    expect(statusUrl).toBe(
      `https://api.github.com/repos/Kyan42/groundtruth/statuses/${"b".repeat(40)}`,
    );
    const statusBody = JSON.parse(statusInit.body);
    expect(statusBody.state).toBe("failure");
    expect(statusBody.context).toBe("groundtruth/verify");
    expect(statusBody.target_url).toBe(`https://dashboard.example.dev/runs/${run.id}`);
    expect(statusBody.description.length).toBeLessThanOrEqual(140);
  });

  it("edits the same comment in place on a rerun instead of creating a new one", async () => {
    const createMock = vi
      .fn()
      .mockResolvedValueOnce(Response.json({ id: 777 }))
      .mockResolvedValueOnce(Response.json({}));
    vi.stubGlobal("fetch", createMock);
    await syncPullRequestResult(makeFailedRun());

    const editMock = vi
      .fn()
      .mockResolvedValueOnce(Response.json({}))
      .mockResolvedValueOnce(Response.json({}));
    vi.stubGlobal("fetch", editMock);
    await syncPullRequestResult(makeFailedRun());

    expect(editMock).toHaveBeenCalledTimes(2);
    const [editUrl, editInit] = editMock.mock.calls[0];
    expect(editUrl).toBe("https://api.github.com/repos/Kyan42/groundtruth/issues/comments/777");
    expect(editInit.method).toBe("PATCH");
  });

  it("never throws when GitHub is unreachable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new Error("network down")),
    );

    await expect(syncPullRequestResult(makeFailedRun())).resolves.toBeUndefined();
  });
});
