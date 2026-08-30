import { z } from "zod";

import { SetupRequiredError } from "@/lib/domain/errors";

const OptionalEnvironmentValueSchema = z.preprocess(
  (value) => (value === "" ? undefined : value),
  z.string().min(1).optional(),
);

const EnvironmentSchema = z.object({
  RUNLOOP_API_KEY: OptionalEnvironmentValueSchema,
  GITHUB_TOKEN: OptionalEnvironmentValueSchema,
  GITHUB_REPO: OptionalEnvironmentValueSchema,
  DASHBOARD_URL: OptionalEnvironmentValueSchema,
  REFLEX_BASE_URL: z.url().default("https://reflex.runloop.ai"),
  REFLEX_API_KEY: OptionalEnvironmentValueSchema,
  REFLEX_ORG_ID: OptionalEnvironmentValueSchema,
  GROUNDTRUTH_PUBLIC_BASE_URL: z.url().default("http://localhost:3000"),
  GROUNDTRUTH_STATE_DIR: z.string().min(1).default(".groundtruth"),
  RUNLOOP_BROWSER_BLUEPRINT_ID: OptionalEnvironmentValueSchema,
  CODEX_AUTH_JSON: OptionalEnvironmentValueSchema,
});

export type Environment = z.infer<typeof EnvironmentSchema>;

let cachedEnvironment: Environment | undefined;

export function getEnvironment(): Environment {
  cachedEnvironment ??= EnvironmentSchema.parse(process.env);
  return cachedEnvironment;
}

export function requireRunloopEnvironment(): Environment & { RUNLOOP_API_KEY: string } {
  const environment = getEnvironment();
  if (!environment.RUNLOOP_API_KEY) {
    throw new SetupRequiredError(
      "runloop_credentials_missing",
      "RUNLOOP_API_KEY is required to create the coordination Axon.",
    );
  }
  return environment as Environment & { RUNLOOP_API_KEY: string };
}

export function requireReflexEnvironment(): Environment & {
  REFLEX_API_KEY: string;
  REFLEX_ORG_ID: string;
} {
  const environment = getEnvironment();
  if (!environment.REFLEX_API_KEY || !environment.REFLEX_ORG_ID) {
    throw new SetupRequiredError(
      "reflex_credentials_missing",
      "REFLEX_API_KEY and REFLEX_ORG_ID are required to run intent analysis.",
    );
  }
  return environment as Environment & { REFLEX_API_KEY: string; REFLEX_ORG_ID: string };
}

export type GithubSyncEnvironment = {
  token: string;
  owner: string;
  repo: string;
  dashboardBaseUrl: string;
};

/**
 * The env vars needed to post PR comments and commit statuses. Returns
 * undefined (not a throw) when any are missing, because GitHub sync is a
 * best-effort side channel: a run must still complete locally with no
 * GITHUB_TOKEN configured, per the "for later" note in the integration brief.
 */
export function getGithubSyncEnvironment(): GithubSyncEnvironment | undefined {
  const environment = getEnvironment();
  if (!environment.GITHUB_TOKEN || !environment.GITHUB_REPO) {
    return undefined;
  }
  const [owner, repo] = environment.GITHUB_REPO.split("/");
  if (!owner || !repo) {
    return undefined;
  }
  return {
    token: environment.GITHUB_TOKEN,
    owner,
    repo,
    dashboardBaseUrl: (environment.DASHBOARD_URL ?? environment.GROUNDTRUTH_PUBLIC_BASE_URL).replace(
      /\/$/,
      "",
    ),
  };
}

export function resetEnvironmentForTests(): void {
  cachedEnvironment = undefined;
}
