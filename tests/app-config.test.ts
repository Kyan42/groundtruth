import { describe, expect, it } from "vitest";

import { hasTrustedRegressionImpact, loadAppConfiguration } from "@/lib/config/app-config";

const BASE_SHA = "db5c5ae6e25fdc3947738b37327a626394420365";
const HEAD_SHA = "716b9f36e35f4f1cd1944e043bfdfb13f8f97ea4";

describe("trusted app configuration", () => {
  it("loads fernway only for its pinned base and head SHAs", async () => {
    const result = await loadAppConfiguration("Kyan42", "fernway", BASE_SHA, HEAD_SHA);

    expect(result.ready).toBe(true);
    if (result.ready) {
      expect(result.profile.repository).toBe("Kyan42/fernway");
      expect(result.appMap.baseSha).toBe(BASE_SHA);
      expect(result.impactMap.headSha).toBe(HEAD_SHA);
      expect(result.mission.id).toBe("intent-checkout-application-timing");
      expect(result.missions.map((mission) => mission.kind)).toEqual(["intent", "regression"]);
      const regression = result.missions.find((mission) => mission.kind === "regression");
      expect(regression?.claimIds).toEqual([]);
      expect(JSON.stringify(regression)).not.toMatch(/Monstera|38\.00|43\.99/);
      expect(
        hasTrustedRegressionImpact(
          {
            ...regression!,
            impactEvidence: {
              ...regression!.impactEvidence!,
              routes: [...regression!.impactEvidence!.routes, "/checkout"],
            },
          },
          result.impactMap,
          result.appMap,
        ),
      ).toBe(false);
      expect(result.mission.claimIds).toEqual(["checkout-application"]);
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
