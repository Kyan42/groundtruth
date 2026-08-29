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
          head: {
            ref: "feature",
            sha: "b".repeat(40),
            repo: { full_name: "example/private" },
          },
        }),
      ),
    );

    await expect(
      fetchPublicPullRequest("https://github.com/example/private/pull/9"),
    ).rejects.toMatchObject({ code: "pr_unavailable", status: 404 });
  });

  it("blocks fork pull requests explicitly", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        Response.json({
          number: 4,
          html_url: "https://github.com/example/public/pull/4",
          title: "Forked change",
          body: "Test this fork.",
          base: {
            ref: "main",
            sha: "a".repeat(40),
            repo: {
              name: "public",
              private: false,
              owner: { login: "example" },
              clone_url: "https://github.com/example/public.git",
            },
          },
          head: {
            ref: "feature",
            sha: "b".repeat(40),
            repo: { full_name: "contributor/public" },
          },
        }),
      ),
    );

    await expect(
      fetchPublicPullRequest("https://github.com/example/public/pull/4"),
    ).rejects.toMatchObject({ code: "fork_pr_unsupported", status: 422 });
  });
});
