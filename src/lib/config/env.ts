import { z } from "zod";

import { SetupRequiredError } from "@/lib/domain/errors";

const EnvironmentSchema = z.object({
  RUNLOOP_API_KEY: z.string().min(1).optional(),
  GITHUB_TOKEN: z.string().min(1).optional(),
  REFLEX_BASE_URL: z.url().default("https://reflex.runloop.ai"),
  REFLEX_API_KEY: z.string().min(1).optional(),
  REFLEX_ORG_ID: z.string().min(1).optional(),
  GROUNDTRUTH_PUBLIC_BASE_URL: z.url().default("http://localhost:3000"),
  GROUNDTRUTH_STATE_DIR: z.string().min(1).default(".groundtruth"),
  RUNLOOP_BROWSER_BLUEPRINT_ID: z.string().min(1).optional(),
  CODEX_AUTH_JSON: z.string().min(1).optional(),
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

export function resetEnvironmentForTests(): void {
  cachedEnvironment = undefined;
}
