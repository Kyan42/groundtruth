import type { Octokit } from "@octokit/core";
import YAML from "yaml";
import * as z from "zod/v4";

// .groundtruth.yml: how to boot a repo's app in a sandbox. Plain commands, run in order.
export const CONFIG_PATH = ".groundtruth.yml";

const BootConfigSchema = z.object({
  version: z.literal(1),
  runtime: z.object({ node: z.string().optional() }).optional(),
  workdir: z.string().default("."),
  install: z.string().optional(),
  setup: z.array(z.string()).default([]),
  start: z.string(),
  port: z.number().int(),
  ready: z.object({
    path: z.string().default("/"),
    timeout_seconds: z.number().default(180),
  }).default({ path: "/", timeout_seconds: 180 }),
});

export type BootConfig = z.infer<typeof BootConfigSchema>;

// Read from the repo's default branch, not the PR: a PR must not be able to change what we run.
export async function loadBootConfig(octokit: Octokit, owner: string, repo: string): Promise<{ config: BootConfig; ref: string }> {
  const { data: repoInfo } = await octokit.request("GET /repos/{owner}/{repo}", { owner, repo });
  const ref = repoInfo.default_branch;
  let text: string;
  try {
    const { data } = await octokit.request("GET /repos/{owner}/{repo}/contents/{path}", { owner, repo, path: CONFIG_PATH, ref });
    if (Array.isArray(data) || data.type !== "file") throw new Error(`${CONFIG_PATH} is not a file`);
    text = Buffer.from(data.content, "base64").toString("utf8");
  } catch (err) {
    throw new Error(`No ${CONFIG_PATH} on ${owner}/${repo}@${ref}: add one describing how to install and start the app ` +
      `(${err instanceof Error ? err.message : err})`);
  }
  const parsed = BootConfigSchema.safeParse(YAML.parse(text));
  if (!parsed.success) throw new Error(`Invalid ${CONFIG_PATH}: ${z.prettifyError(parsed.error)}`);
  return { config: parsed.data, ref };
}
