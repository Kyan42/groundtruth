import { execFileSync } from "node:child_process";

// CLI auth for reading public repos: GITHUB_TOKEN, else the `gh` CLI login.
export function githubToken(): string {
  if (process.env.GITHUB_TOKEN) return process.env.GITHUB_TOKEN;
  try {
    return execFileSync("gh", ["auth", "token"], { encoding: "utf8" }).trim();
  } catch {
    throw new Error("Set GITHUB_TOKEN or log in with `gh auth login`");
  }
}
