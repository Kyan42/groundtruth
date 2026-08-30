import { readFile } from "node:fs/promises";

import { describe, expect, it } from "vitest";

describe("landing page actions", () => {
  it("links separately to live verification and the cached demo", async () => {
    const landing = await readFile(new URL("../public/landing.html", import.meta.url), "utf8");

    expect(landing).toContain('<a class="btn btn-primary" href="/new">Run live</a>');
    expect(landing).toContain(
      '<a class="btn" href="/runs/recorded-fernway-pr4?recorded=fernway-pr4-v1">View cached demo</a>',
    );
  });
});
