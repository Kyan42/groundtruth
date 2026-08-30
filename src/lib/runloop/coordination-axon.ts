import { randomUUID } from "node:crypto";

import type {
  BrowserEnvironment,
  ExecutableJourney,
  ExecutionResult,
  IntentSpec,
  RegressionComparison,
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
  // A re-provisioned run re-extracts and re-approves its contract, so a
  // fresh approval supersedes the stored row; every approval is still
  // recorded in the immutable Axon event log.
  await axon.sql.query({
    sql: `INSERT INTO contracts (run_id, schema_version, spec_json, approved_at)
      VALUES (?, ?, ?, ?)
      ON CONFLICT(run_id) DO UPDATE SET
        schema_version = excluded.schema_version,
        spec_json = excluded.spec_json,
        approved_at = excluded.approved_at`,
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
        {
          sql: `CREATE TABLE IF NOT EXISTS mission_attempts (
            attempt_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            mission_id TEXT NOT NULL,
            mission_json TEXT NOT NULL,
            created_at TEXT NOT NULL
          )`,
        },
        {
          sql: `CREATE TABLE IF NOT EXISTS journey_attempts (
            attempt_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            mission_id TEXT NOT NULL,
            journey_json TEXT NOT NULL,
            created_at TEXT NOT NULL
          )`,
        },
        {
          sql: `CREATE TABLE IF NOT EXISTS execution_attempts (
            execution_id TEXT PRIMARY KEY,
            attempt_id TEXT NOT NULL,
            mission_id TEXT NOT NULL,
            target TEXT NOT NULL,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (attempt_id, target)
          )`,
        },
        {
          sql: `CREATE TABLE IF NOT EXISTS environment_attempts (
            attempt_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            role TEXT NOT NULL,
            environment_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (attempt_id, role)
          )`,
        },
        {
          sql: `CREATE TABLE IF NOT EXISTS environment_events (
            event_id TEXT PRIMARY KEY,
            attempt_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            role TEXT NOT NULL,
            status TEXT NOT NULL,
            environment_json TEXT NOT NULL,
            created_at TEXT NOT NULL
          )`,
        },
        {
          sql: `CREATE TABLE IF NOT EXISTS regression_comparisons (
            comparison_id TEXT PRIMARY KEY,
            attempt_id TEXT NOT NULL UNIQUE,
            base_execution_id TEXT NOT NULL,
            head_execution_id TEXT NOT NULL,
            verdict TEXT NOT NULL,
            comparison_json TEXT NOT NULL,
            created_at TEXT NOT NULL
          )`,
        },
      ],
    });
  }

export async function saveMission(
    axonId: string,
    attemptId: string,
    runId: string,
    mission: TestMission,
    createdAt: string,
  ): Promise<void> {
    const axon = getRunloopClient().axon.fromId(axonId);
    await axon.sql.query({
      sql: `INSERT INTO mission_attempts
        (attempt_id, run_id, mission_id, mission_json, created_at)
        VALUES (?, ?, ?, ?, ?)`,
      params: [attemptId, runId, mission.id, JSON.stringify(mission), createdAt],
    });
  }

export async function saveEnvironment(
    axonId: string,
    attemptId: string,
    runId: string,
    environment: BrowserEnvironment,
    updatedAt: string,
  ): Promise<void> {
    const axon = getRunloopClient().axon.fromId(axonId);
    const environmentJson = JSON.stringify(environment);
    const result = await axon.sql.batch({
      statements: [
        {
          sql: `INSERT OR REPLACE INTO environment_attempts
            (attempt_id, run_id, role, environment_json, updated_at)
            VALUES (?, ?, ?, ?, ?)`,
          params: [attemptId, runId, environment.role, environmentJson, updatedAt],
        },
        {
          sql: `INSERT INTO environment_events
            (event_id, attempt_id, run_id, role, status, environment_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)`,
          params: [
            randomUUID(),
            attemptId,
            runId,
            environment.role,
            environment.status,
            environmentJson,
            updatedAt,
          ],
        },
      ],
    });
    if (result.results.some((step) => step.error || !step.success)) {
      throw new Error("Failed to persist the environment audit transaction.");
    }
  }

export async function saveJourney(
    axonId: string,
    attemptId: string,
    runId: string,
    journey: ExecutableJourney,
    createdAt: string,
  ): Promise<void> {
    const axon = getRunloopClient().axon.fromId(axonId);
    await axon.sql.query({
      sql: `INSERT INTO journey_attempts
        (attempt_id, run_id, mission_id, journey_json, created_at)
        VALUES (?, ?, ?, ?, ?)`,
      params: [attemptId, runId, journey.missionId, JSON.stringify(journey), createdAt],
    });
  }

export async function saveExecution(
    axonId: string,
    executionId: string,
    result: ExecutionResult,
    createdAt: string,
  ): Promise<void> {
    const axon = getRunloopClient().axon.fromId(axonId);
    if (!result.attemptId) {
      throw new Error("Attempt-scoped execution persistence requires an attempt ID.");
    }
    await axon.sql.query({
      sql: `INSERT INTO execution_attempts
        (execution_id, attempt_id, mission_id, target, result_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)`,
      params: [
        executionId,
        result.attemptId,
        result.missionId,
        result.target,
        JSON.stringify(result),
        createdAt,
      ],
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
      sql: `INSERT INTO artifacts (artifact_id, execution_id, kind, object_id)
        VALUES (?, ?, ?, ?)`,
      params: [artifactId, executionId, kind, objectId],
    });
}

export async function saveRegressionComparison(
  axonId: string,
  comparison: RegressionComparison,
): Promise<void> {
  const axon = getRunloopClient().axon.fromId(axonId);
  const comparisonJson = JSON.stringify(comparison);
  const result = await axon.sql.batch({
    statements: [
      {
        sql: `INSERT INTO regression_comparisons
          (comparison_id, attempt_id, base_execution_id, head_execution_id, verdict, comparison_json, created_at)
          VALUES (?, ?, ?, ?, ?, ?, ?)`,
        params: [
          comparison.comparisonId,
          comparison.attemptId,
          comparison.baseExecutionId,
          comparison.headExecutionId,
          comparison.verdict,
          comparisonJson,
          comparison.createdAt,
        ],
      },
      {
        sql: `INSERT INTO verdicts (verdict_id, mission_id, kind, verdict_json, created_at)
          VALUES (?, ?, ?, ?, ?)`,
        params: [
          comparison.comparisonId,
          comparison.missionId,
          "regression",
          comparisonJson,
          comparison.createdAt,
        ],
      },
    ],
  });
  if (result.results.some((step) => step.error || !step.success)) {
    throw new Error("Failed to persist the regression comparison transaction.");
  }
}
