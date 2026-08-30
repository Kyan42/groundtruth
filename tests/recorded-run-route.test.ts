import { describe, expect, it } from "vitest";

import { GET } from "@/app/api/recorded-runs/[snapshotId]/route";

describe("recorded run route", () => {
  it("serves the sanitized Fernway run with real artifact proxies", async () => {
    const response = await GET(new Request("http://localhost/api/recorded-runs/fernway-pr4-v1"), {
      params: Promise.resolve({ snapshotId: "fernway-pr4-v1" }),
    });
    const payload = await response.json();

    expect(response.status).toBe(200);
    expect(payload.recorded.label).toBe("Recorded from a real Runloop execution");
    expect(payload.claims.map((claim: { id: string; verdict: string }) => [claim.id, claim.verdict]))
      .toEqual([["C1", "p"], ["C2", "p"], ["C3", "f"], ["R1", "p"]]);
    for (const claim of payload.claims) {
      expect(claim.videoUrl).toMatch(/^\/api\/artifacts\/obj_[A-Za-z0-9]+$/);
    }
  });

  it("rejects unknown snapshots", async () => {
    const response = await GET(new Request("http://localhost/api/recorded-runs/unknown"), {
      params: Promise.resolve({ snapshotId: "unknown" }),
    });

    expect(response.status).toBe(404);
  });
});
