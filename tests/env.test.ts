import { afterEach, describe, expect, it } from "vitest";

import { getEnvironment, resetEnvironmentForTests } from "@/lib/config/env";

const names = [
  "RUNLOOP_API_KEY",
  "GITHUB_TOKEN",
  "REFLEX_API_KEY",
  "REFLEX_ORG_ID",
  "RUNLOOP_BROWSER_BLUEPRINT_ID",
  "CODEX_AUTH_JSON",
] as const;
const previous = Object.fromEntries(names.map((name) => [name, process.env[name]]));

afterEach(() => {
  for (const name of names) {
    const value = previous[name];
    if (value === undefined) {
      delete process.env[name];
    } else {
      process.env[name] = value;
    }
  }
  resetEnvironmentForTests();
});

describe("environment configuration", () => {
  it("treats blank optional variables as unset", () => {
    for (const name of names) {
      process.env[name] = "";
    }
    resetEnvironmentForTests();

    const environment = getEnvironment();

    expect(environment.RUNLOOP_API_KEY).toBeUndefined();
    expect(environment.REFLEX_API_KEY).toBeUndefined();
    expect(environment.REFLEX_ORG_ID).toBeUndefined();
    expect(environment.RUNLOOP_BROWSER_BLUEPRINT_ID).toBeUndefined();
    expect(environment.CODEX_AUTH_JSON).toBeUndefined();
  });
});
