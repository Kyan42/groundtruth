import { afterEach, describe, expect, it, vi } from "vitest";

import { resetEnvironmentForTests } from "@/lib/config/env";
import { fetchPublicPullRequest } from "@/lib/github/public-pr-client";

afterEach(() => {
  vi.unstubAllGlobals();
  resetEnvironmentForTests();
});

describe("public pull request ingestion", () => {
  it("rejects a private repository even when a token can read it", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        Response.json({
          number: 9,
          html_url: "https://github.com/example/private/pull/9",
          title: "Private change",
          body: "Do not disclose this.",
          base: {
            ref: "main",
            sha: "a".repeat(40),
            repo: {
              name: "private",
              private: true,
              owner: { login: "example" },
              clone_url: "https://github.com/example/private.git",
            },
          },
          head: { ref: "feature", sha: "b".repeat(40) },
        }),
      ),
    );

    await expect(
      fetchPublicPullRequest("https://github.com/example/private/pull/9"),
    ).rejects.toMatchObject({ code: "pr_unavailable", status: 404 });
  });
});
