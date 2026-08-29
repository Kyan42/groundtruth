import { z } from "zod";

import { getEnvironment } from "@/lib/config/env";
import { GroundtruthError } from "@/lib/domain/errors";

const PullRequestResponseSchema = z.object({
  number: z.number().int().positive(),
  html_url: z.url(),
  title: z.string().min(1),
  body: z.string().nullable(),
  base: z.object({
    ref: z.string().min(1),
    sha: z.string().regex(/^[a-f0-9]{40}$/i),
    repo: z.object({
      name: z.string().min(1),
      private: z.boolean(),
      owner: z.object({ login: z.string().min(1) }),
      clone_url: z.url(),
    }),
  }),
  head: z.object({
    ref: z.string().min(1),
    sha: z.string().regex(/^[a-f0-9]{40}$/i),
    repo: z
      .object({
        full_name: z.string().min(1),
      })
      .nullable(),
  }),
});

export type PublicPullRequest = {
  repository: { owner: string; name: string; cloneUrl: string };
  pullRequest: {
    number: number;
    url: string;
    title: string;
    body: string;
    baseRef: string;
    baseSha: string;
    headRef: string;
    headSha: string;
  };
};

export function parsePublicPullRequestUrl(value: string): {
  owner: string;
  repo: string;
  number: number;
} {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new GroundtruthError(
      "invalid_pr_url",
      "Enter a public GitHub pull request URL.",
      400,
    );
  }

  const match = url.pathname.match(/^\/([^/]+)\/([^/]+)\/pull\/([1-9]\d*)\/?$/);
  if (
    url.protocol !== "https:" ||
    url.hostname.toLowerCase() !== "github.com" ||
    !match ||
    url.search ||
    url.hash
  ) {
    throw new GroundtruthError(
      "invalid_pr_url",
      "Use the canonical form https://github.com/owner/repo/pull/123.",
      400,
    );
  }

  return { owner: match[1], repo: match[2], number: Number(match[3]) };
}

export async function fetchPublicPullRequest(value: string): Promise<PublicPullRequest> {
  const { owner, repo, number } = parsePublicPullRequestUrl(value);
  const headers = new Headers({
    Accept: "application/vnd.github+json",
    "User-Agent": "groundtruth-prototype",
    "X-GitHub-Api-Version": "2022-11-28",
  });
  const githubToken = getEnvironment().GITHUB_TOKEN;
  if (githubToken) {
    headers.set("Authorization", `Bearer ${githubToken}`);
  }
  let response: Response;
  try {
    response = await fetch(`https://api.github.com/repos/${owner}/${repo}/pulls/${number}`, {
      headers,
      cache: "no-store",
    });
  } catch (error) {
    throw new GroundtruthError(
      "github_unavailable",
      "GitHub could not be reached while loading the pull request.",
      503,
      true,
      error instanceof Error ? error.message : undefined,
    );
  }

  if (response.status === 403 || response.status === 429) {
    throw new GroundtruthError(
      "github_rate_limited",
      "GitHub rate-limited the public pull request lookup. Try again later.",
      503,
      true,
    );
  }
  if (response.status === 404) {
    throw new GroundtruthError(
      "pr_unavailable",
      "The pull request does not exist or is not publicly accessible.",
      404,
    );
  }
  if (!response.ok) {
    throw new GroundtruthError(
      "github_request_failed",
      `GitHub returned status ${response.status} while loading the pull request.`,
      502,
      response.status >= 500,
    );
  }

  const parsed = PullRequestResponseSchema.safeParse(await response.json());
  if (!parsed.success) {
    throw new GroundtruthError(
      "github_response_invalid",
      "GitHub returned an unexpected pull request response.",
      502,
      false,
      parsed.error.issues,
    );
  }

  const pullRequest = parsed.data;
  if (pullRequest.base.repo.private) {
    throw new GroundtruthError(
      "pr_unavailable",
      "The pull request does not exist or is not publicly accessible.",
      404,
    );
  }
  if (
    !pullRequest.head.repo ||
    pullRequest.head.repo.full_name.toLowerCase() !==
      `${pullRequest.base.repo.owner.login}/${pullRequest.base.repo.name}`.toLowerCase()
  ) {
    throw new GroundtruthError(
      "fork_pr_unsupported",
      "Fork pull requests are not supported by the browser verification prototype.",
      422,
    );
  }
  return {
    repository: {
      owner: pullRequest.base.repo.owner.login,
      name: pullRequest.base.repo.name,
      cloneUrl: pullRequest.base.repo.clone_url,
    },
    pullRequest: {
      number: pullRequest.number,
      url: pullRequest.html_url,
      title: pullRequest.title,
      body: pullRequest.body ?? "",
      baseRef: pullRequest.base.ref,
      baseSha: pullRequest.base.sha,
      headRef: pullRequest.head.ref,
      headSha: pullRequest.head.sha,
    },
  };
}
