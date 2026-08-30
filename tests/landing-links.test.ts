import { readFile } from "node:fs/promises";

import { describe, expect, it } from "vitest";

describe("landing page actions", () => {
  it("offers an inline live PR form next to the cached demo link", async () => {
    const landing = await readFile(new URL("../public/landing.html", import.meta.url), "utf8");

    expect(landing).toContain('<form class="run-form fade d2" id="run-form">');
    expect(landing).toContain('id="pr-url"');
    expect(landing).toContain('<button class="btn btn-primary" type="submit">Run live</button>');
    expect(landing).toContain(
      '<a class="btn" href="/runs/recorded-fernway-pr4?recorded=fernway-pr4-v1">View cached demo</a>',
    );
    expect(landing).not.toContain('href="/new"');
  });

  it("drives the live run through the real backend orchestrator", async () => {
    const landing = await readFile(new URL("../public/landing.html", import.meta.url), "utf8");

    expect(landing).toContain("fetch('/api/runs'");
    expect(landing).toContain("'approve_intent'");
    expect(landing).toContain("'start_verification'");
    expect(landing).toContain("'start_regression'");
    // Live mode is the default: no query-flag gate and no recorded-URL rejection.
    expect(landing).not.toContain("get('live')");
    expect(landing).not.toContain("This recorded demo supports only");
  });
});
