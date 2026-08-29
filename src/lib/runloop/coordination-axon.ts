import { RunloopSDK } from "@runloop/api-client";

import { requireRunloopEnvironment } from "@/lib/config/env";
import type { IntentSpec, Run } from "@/lib/domain/schemas";

type EventOrigin = "EXTERNAL_EVENT" | "AGENT_EVENT" | "USER_EVENT";

let client: RunloopSDK | undefined;

function getClient(): RunloopSDK {
  const environment = requireRunloopEnvironment();
  client ??= new RunloopSDK({ bearerToken: environment.RUNLOOP_API_KEY });
  return client;
}

export async function createCoordinationAxon(run: Run): Promise<string> {
  const axon = await getClient().axon.create({ name: `groundtruth-${run.id}` });
  await axon.sql.batch({
    statements: [
      {
        sql: `CREATE TABLE IF NOT EXISTS contracts (
          run_id TEXT PRIMARY KEY,
          schema_version INTEGER NOT NULL,
          spec_json TEXT NOT NULL,
          approved_at TEXT NOT NULL
        )`,
      },
      {
        sql: `CREATE TABLE IF NOT EXISTS missions (
          mission_id TEXT PRIMARY KEY,
          mission_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        )`,
      },
      {
        sql: `CREATE TABLE IF NOT EXISTS executions (
          execution_id TEXT PRIMARY KEY,
          mission_id TEXT NOT NULL,
          target TEXT NOT NULL,
          result_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        )`,
      },
      {
        sql: `CREATE TABLE IF NOT EXISTS artifacts (
          artifact_id TEXT PRIMARY KEY,
          execution_id TEXT NOT NULL,
          kind TEXT NOT NULL,
          object_id TEXT NOT NULL
        )`,
      },
      {
        sql: `CREATE TABLE IF NOT EXISTS verdicts (
          verdict_id TEXT PRIMARY KEY,
          mission_id TEXT NOT NULL,
          kind TEXT NOT NULL,
          verdict_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        )`,
      },
    ],
  });
  return axon.id;
}

export async function publishRunEvent(
  axonId: string,
  eventType: string,
  origin: EventOrigin,
  payload: Record<string, unknown>,
): Promise<void> {
  const axon = getClient().axon.fromId(axonId);
  await axon.publish({
    event_type: eventType,
    origin,
    payload: JSON.stringify(payload),
    source: "groundtruth",
  });
}

export async function saveIntentContract(
  axonId: string,
  runId: string,
  spec: IntentSpec,
  approvedAt: string,
): Promise<void> {
  const axon = getClient().axon.fromId(axonId);
  await axon.sql.query({
    sql: `INSERT OR IGNORE INTO contracts (run_id, schema_version, spec_json, approved_at)
      VALUES (?, ?, ?, ?)`,
    params: [runId, spec.schemaVersion, JSON.stringify(spec), approvedAt],
  });
  const verification = await axon.sql.query({
    sql: `UPDATE contracts SET run_id = run_id
      WHERE run_id = ? AND schema_version = ? AND spec_json = ? AND approved_at = ?`,
    params: [runId, spec.schemaVersion, JSON.stringify(spec), approvedAt],
  });
  if (verification.meta.changes !== 1) {
    throw new Error("The existing intent contract does not match this approval attempt.");
  }
}
