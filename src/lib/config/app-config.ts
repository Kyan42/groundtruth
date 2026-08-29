import { readFile } from "node:fs/promises";
import path from "node:path";

import { z } from "zod";

import {
  type AppMap,
  AppMapSchema,
  type AppProfile,
  AppProfileSchema,
  type TestMission,
  TestMissionSchema,
} from "@/lib/domain/schemas";

export type AppConfiguration =
  | { ready: true; profile: AppProfile; appMap: AppMap; mission: TestMission }
  | { ready: false; blockers: Array<{ code: string; message: string }> };

export async function loadAppConfiguration(
  owner: string,
  repo: string,
  expectedBaseSha?: string,
  expectedHeadSha?: string,
): Promise<AppConfiguration> {
  const safeSegment = /^[A-Za-z0-9_.-]+$/;
  if (!safeSegment.test(owner) || !safeSegment.test(repo)) {
    return {
      ready: false,
      blockers: [{ code: "app_config_path_invalid", message: "Repository name is not supported." }],
    };
  }

  const directory = path.join(process.cwd(), "config", "apps", owner, repo);
  const [profile, appMap, mission] = await Promise.all([
    readJson(path.join(directory, "app-profile.json"), AppProfileSchema),
    readJson(path.join(directory, "app-map.json"), AppMapSchema),
    readJson(path.join(directory, "test-mission.json"), TestMissionSchema),
  ]);
  const blockers: Array<{ code: string; message: string }> = [];

  if (!profile.ok) {
    blockers.push({
      code: "app_profile_missing",
      message: `Add a valid config/apps/${owner}/${repo}/app-profile.json file.`,
    });
  }
  if (!appMap.ok) {
    blockers.push({
      code: "app_map_missing",
      message: `Add a valid config/apps/${owner}/${repo}/app-map.json file.`,
    });
  }
  if (!mission.ok) {
    blockers.push({
      code: "test_mission_missing",
      message: `Add a valid config/apps/${owner}/${repo}/test-mission.json file.`,
    });
  }

  if (!profile.ok || !appMap.ok || !mission.ok) {
    return { ready: false, blockers };
  }

  const repository = `${owner}/${repo}`.toLowerCase();
  if (
    profile.value.repository.toLowerCase() !== repository ||
    appMap.value.repository.toLowerCase() !== repository
  ) {
    blockers.push({
      code: "app_config_repository_mismatch",
      message: "Trusted app configuration does not match the target repository.",
    });
  }
  if (expectedBaseSha && appMap.value.baseSha.toLowerCase() !== expectedBaseSha.toLowerCase()) {
    blockers.push({
      code: "app_map_stale",
      message: `Trusted AppMap base SHA does not match ${expectedBaseSha}.`,
    });
  }
  if (
    expectedBaseSha &&
    profile.value.compatibility.baseSha.toLowerCase() !== expectedBaseSha.toLowerCase()
  ) {
    blockers.push({
      code: "app_profile_base_stale",
      message: `Trusted AppProfile base SHA does not match ${expectedBaseSha}.`,
    });
  }
  if (
    expectedHeadSha &&
    profile.value.compatibility.headSha.toLowerCase() !== expectedHeadSha.toLowerCase()
  ) {
    blockers.push({
      code: "app_profile_head_stale",
      message: `Trusted AppProfile head SHA does not match ${expectedHeadSha}.`,
    });
  }
  if (blockers.length > 0) {
    return { ready: false, blockers };
  }
  return { ready: true, profile: profile.value, appMap: appMap.value, mission: mission.value };
}

async function readJson<T>(
  filePath: string,
  schema: z.ZodType<T>,
): Promise<{ ok: true; value: T } | { ok: false }> {
  try {
    return { ok: true, value: schema.parse(JSON.parse(await readFile(filePath, "utf8"))) };
  } catch {
    return { ok: false };
  }
}
