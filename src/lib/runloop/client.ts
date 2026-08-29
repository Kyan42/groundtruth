import { RunloopSDK } from "@runloop/api-client";

import { requireRunloopEnvironment } from "@/lib/config/env";

let client: RunloopSDK | undefined;

export function getRunloopClient(): RunloopSDK {
  const environment = requireRunloopEnvironment();
  client ??= new RunloopSDK({ bearerToken: environment.RUNLOOP_API_KEY });
  return client;
}
