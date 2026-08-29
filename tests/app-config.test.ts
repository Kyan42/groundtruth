import { describe, expect, it } from "vitest";

import { loadAppConfiguration } from "@/lib/config/app-config";

const BASE_SHA = "db5c5ae6e25fdc3947738b37327a626394420365";
const HEAD_SHA = "716b9f36e35f4f1cd1944e043bfdfb13f8f97ea4";

describe("trusted app configuration", () => {
  it("loads fernway only for its pinned base and head SHAs", async () => {
    const result = await loadAppConfiguration("Kyan42", "fernway", BASE_SHA, HEAD_SHA);

    expect(result.ready).toBe(true);
    if (result.ready) {
      expect(result.profile.repository).toBe("Kyan42/fernway");
      expect(result.appMap.baseSha).toBe(BASE_SHA);
      expect(result.mission.id).toBe("intent-valid-promo");
    }
  });

  it("reports an explicit stale-head blocker", async () => {
    const result = await loadAppConfiguration("Kyan42", "fernway", BASE_SHA, "f".repeat(40));

    expect(result.ready).toBe(false);
    if (!result.ready) {
      expect(result.blockers.map((blocker) => blocker.code)).toContain("app_profile_head_stale");
    }
  });
});
