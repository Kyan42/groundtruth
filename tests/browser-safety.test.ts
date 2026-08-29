import { describe, expect, it } from "vitest";

import type {
  AppProfile,
  ExecutableJourney,
  TestMission,
} from "@/lib/domain/schemas";
import {
  isAllowedHost,
  validateJourneyForReplay,
} from "@/lib/runloop/browser-safety";

const profile: AppProfile = {
  schemaVersion: 1,
  repository: "owner/repo",
  compatibility: {
    baseSha: "a".repeat(40),
    headSha: "b".repeat(40),
  },
  workspace: {
    workingDirectory: "app",
    installCommand: "npm install",
    startCommand: "npm start",
    port: 3000,
    healthPath: "/health",
  },
  fixtures: { resetCommand: "npm run reset" },
  auth: { mode: "none" },
  safety: {
    allowedHosts: ["*.tunnel.runloop.ai"],
    blockedPathPrefixes: ["/admin"],
    allowStateChangingRequests: true,
  },
};

const mission: TestMission = {
  schemaVersion: 1,
  id: "mission",
  title: "Mission",
  kind: "intent",
  claimIds: ["claim"],
  goal: "Exercise the shopper flow",
  startPath: "/",
  preconditions: [],
  fixtureValues: { promo: "SAVE10" },
  assertions: [
    {
      kind: "console",
      level: "error",
      maximumCount: 0,
    },
  ],
};

function journeyWith(step: ExecutableJourney["steps"][number]): ExecutableJourney {
  return {
    schemaVersion: 1,
    missionId: mission.id,
    discoveredAgainst: "head",
    steps: [step],
    producer: { kind: "codex", agentId: "runloop:devbox" },
  };
}

describe("browser replay safety", () => {
  it("matches only exact hosts or true wildcard subdomains", () => {
    expect(isAllowedHost("abc.tunnel.runloop.ai", profile.safety.allowedHosts)).toBe(true);
    expect(isAllowedHost("tunnel.runloop.ai", profile.safety.allowedHosts)).toBe(false);
    expect(isAllowedHost("eviltunnel.runloop.ai", profile.safety.allowedHosts)).toBe(false);
  });

  it("accepts a relative journey within the approved application", () => {
    expect(() =>
      validateJourneyForReplay(
        journeyWith({ action: "goto", path: "/" }),
        mission,
        profile,
        "https://abc.tunnel.runloop.ai",
      ),
    ).not.toThrow();
  });

  it.each(["//example.com", "/admin/users"])("rejects unsafe navigation to %s", (path) => {
    expect(() =>
      validateJourneyForReplay(
        journeyWith({ action: "goto", path }),
        mission,
        profile,
        "https://abc.tunnel.runloop.ai",
      ),
    ).toThrowError(/journey|path/i);
  });

  it("rejects fixture references outside the trusted mission", () => {
    const journey: ExecutableJourney = {
      ...journeyWith({ action: "goto", path: "/" }),
      steps: [
        { action: "goto", path: "/" },
        {
          action: "fill",
          locator: { by: "test_id", value: "promo" },
          fixtureValueKey: "unknown",
        },
      ],
    };
    expect(() =>
      validateJourneyForReplay(
        journey,
        mission,
        profile,
        "https://abc.tunnel.runloop.ai",
      ),
    ).toThrowError(/fixture/i);
  });

  it("rejects journeys that do not establish the approved start path", () => {
    expect(() =>
      validateJourneyForReplay(
        journeyWith({
          action: "click",
          locator: { by: "role", role: "button", name: "Continue" },
        }),
        mission,
        profile,
        "https://abc.tunnel.runloop.ai",
      ),
    ).toThrowError(/start path/i);
  });
});
