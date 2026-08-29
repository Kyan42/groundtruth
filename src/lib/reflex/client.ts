import { configureReflex } from "@runloop/reflex-client";

import { requireReflexEnvironment } from "@/lib/config/env";

let configuredFor: string | undefined;

export function configureReflexServer(): void {
  const environment = requireReflexEnvironment();
  const configurationKey = `${environment.REFLEX_BASE_URL}|${environment.REFLEX_ORG_ID}|${environment.REFLEX_API_KEY}`;
  if (configuredFor === configurationKey) {
    return;
  }

  configureReflex({
    baseUrl: environment.REFLEX_BASE_URL,
    apiKey: environment.REFLEX_API_KEY,
    organizationId: environment.REFLEX_ORG_ID,
    timeoutMs: 30_000,
  });
  configuredFor = configurationKey;
}
