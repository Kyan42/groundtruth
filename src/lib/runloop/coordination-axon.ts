import type {
  BrowserEnvironment,
  ExecutableJourney,
  ExecutionResult,
  IntentSpec,
  Run,
  TestMission,
} from "@/lib/domain/schemas";
import { getRunloopClient } from "@/lib/runloop/client";

type EventOrigin = "EXTERNAL_EVENT" | "AGENT_EVENT" | "USER_EVENT";

export async function createCoordinationAxon(run: Run): Promise<string> {
  const axon = await getRunloopClient().axon.create({ name: `groundtruth-${run.id}` });
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
      {
        sql: `CREATE TABLE IF NOT EXISTS environments (
          run_id TEXT NOT NULL,
          role TEXT NOT NULL,
          environment_json TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          PRIMARY KEY (run_id, role)
        )`,
      },
      {
        sql: `CREATE TABLE IF NOT EXISTS journeys (
          mission_id TEXT PRIMARY KEY,
          journey_json TEXT NOT NULL,
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
  const axon = getRunloopClient().axon.fromId(axonId);
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
  const axon = getRunloopClient().axon.fromId(axonId);
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

export async function ensureBrowserTables(axonId: string): Promise<void> {
    const axon = getRunloopClient().axon.fromId(axonId);
    await axon.sql.batch({
      statements: [
        {
          sql: `CREATE TABLE IF NOT EXISTS environments (
            run_id TEXT NOT NULL,
            role TEXT NOT NULL,
            environment_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (run_id, role)
          )`,
        },
        {
          sql: `CREATE TABLE IF NOT EXISTS journeys (
            mission_id TEXT PRIMARY KEY,
            journey_json TEXT NOT NULL,
            created_at TEXT NOT NULL
          )`,
        },
      ],
    });
  }

export async function saveMission(
    axonId: string,
    mission: TestMission,
    createdAt: string,
  ): Promise<void> {
    const axon = getRunloopClient().axon.fromId(axonId);
    await axon.sql.query({
      sql: `INSERT OR REPLACE INTO missions (mission_id, mission_json, created_at)
        VALUES (?, ?, ?)`,
      params: [mission.id, JSON.stringify(mission), createdAt],
    });
  }

export async function saveEnvironment(
    axonId: string,
    runId: string,
    environment: BrowserEnvironment,
    updatedAt: string,
  ): Promise<void> {
    const axon = getRunloopClient().axon.fromId(axonId);
    await axon.sql.query({
      sql: `INSERT OR REPLACE INTO environments (run_id, role, environment_json, updated_at)
        VALUES (?, ?, ?, ?)`,
      params: [runId, environment.role, JSON.stringify(environment), updatedAt],
    });
  }

export async function saveJourney(
    axonId: string,
    journey: ExecutableJourney,
    createdAt: string,
  ): Promise<void> {
    const axon = getRunloopClient().axon.fromId(axonId);
    await axon.sql.query({
      sql: `INSERT OR REPLACE INTO journeys (mission_id, journey_json, created_at)
        VALUES (?, ?, ?)`,
      params: [journey.missionId, JSON.stringify(journey), createdAt],
    });
  }

export async function saveExecution(
    axonId: string,
    executionId: string,
    result: ExecutionResult,
    createdAt: string,
  ): Promise<void> {
    const axon = getRunloopClient().axon.fromId(axonId);
    await axon.sql.query({
      sql: `INSERT OR REPLACE INTO executions
        (execution_id, mission_id, target, result_json, created_at)
        VALUES (?, ?, ?, ?, ?)`,
      params: [executionId, result.missionId, result.target, JSON.stringify(result), createdAt],
    });
  }

export async function saveArtifact(
    axonId: string,
    artifactId: string,
    executionId: string,
    kind: string,
    objectId: string,
  ): Promise<void> {
    const axon = getRunloopClient().axon.fromId(axonId);
    await axon.sql.query({
      sql: `INSERT OR REPLACE INTO artifacts (artifact_id, execution_id, kind, object_id)
        VALUES (?, ?, ?, ?)`,
      params: [artifactId, executionId, kind, objectId],
    });
}
