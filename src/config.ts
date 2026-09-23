import { readFileSync } from "node:fs";

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`Missing required env var ${name} (see .env.example)`);
  return value;
}

export const config = {
  appId: required("GITHUB_APP_ID"),
  privateKey: readFileSync(required("GITHUB_PRIVATE_KEY_PATH"), "utf8"),
  webhookSecret: required("GITHUB_WEBHOOK_SECRET"),
  // Read by the Anthropic SDK itself; required here so a missing key fails at startup, not mid-PR.
  anthropicApiKey: required("ANTHROPIC_API_KEY"),
  extractConcurrency: Number(process.env.EXTRACT_CONCURRENCY ?? 4),
  port: Number(process.env.PORT ?? 3000),
  // Optional: when set, forward webhooks from this smee.io channel to the local server.
  smeeUrl: process.env.SMEE_URL,
};
