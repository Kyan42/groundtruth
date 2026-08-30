import { readFile } from "node:fs/promises";
import path from "node:path";

import { z } from "zod";

import {
  type AppMap,
  AppMapSchema,
  type AppProfile,
  AppProfileSchema,
  type ImpactMap,
  ImpactMapSchema,
  type TestMission,
  TestMissionSchema,
} from "@/lib/domain/schemas";

const TestMissionsSchema = z.array(TestMissionSchema).min(1);

export type AppConfiguration =
  | {
      ready: true;
      profile: AppProfile;
      appMap: AppMap;
      impactMap: ImpactMap;
      mission: TestMission;
      missions: TestMission[];
    }
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
  const [profile, appMap, impactMap, missions] = await Promise.all([
    readJson(path.join(directory, "app-profile.json"), AppProfileSchema),
    readJson(path.join(directory, "app-map.json"), AppMapSchema),
    readJson(path.join(directory, "impact-map.json"), ImpactMapSchema),
    readJson(path.join(directory, "test-missions.json"), TestMissionsSchema),
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
  if (!impactMap.ok) {
    blockers.push({
      code: "impact_map_missing",
      message: `Add a valid config/apps/${owner}/${repo}/impact-map.json file.`,
    });
  }
  if (!missions.ok) {
    blockers.push({
      code: "test_missions_missing",
      message: `Add a valid config/apps/${owner}/${repo}/test-missions.json file.`,
    });
  }

  if (!profile.ok || !appMap.ok || !impactMap.ok || !missions.ok) {
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
    impactMap.value.baseSha.toLowerCase() !== expectedBaseSha.toLowerCase()
  ) {
    blockers.push({
      code: "impact_map_base_stale",
      message: `Trusted ImpactMap base SHA does not match ${expectedBaseSha}.`,
    });
  }
  if (
    expectedHeadSha &&
    impactMap.value.headSha.toLowerCase() !== expectedHeadSha.toLowerCase()
  ) {
    blockers.push({
      code: "impact_map_head_stale",
      message: `Trusted ImpactMap head SHA does not match ${expectedHeadSha}.`,
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
  const missionIds = missions.value.map((mission) => mission.id);
  if (new Set(missionIds).size !== missionIds.length) {
    blockers.push({
      code: "test_mission_id_duplicate",
      message: "Trusted TestMission IDs must be unique.",
    });
  }
  const intentMissions = missions.value.filter((mission) => mission.kind === "intent");
  const regressionMissions = missions.value.filter((mission) => mission.kind === "regression");
  if (intentMissions.length < 1) {
    blockers.push({
      code: "intent_mission_missing",
      message: "At least one trusted intent mission is required.",
    });
  }
  if (regressionMissions.length !== 1) {
    blockers.push({
      code: "regression_mission_ambiguous",
      message: "Exactly one trusted regression mission is required for this prototype.",
    });
  } else if (!hasTrustedRegressionImpact(regressionMissions[0], impactMap.value, appMap.value)) {
    blockers.push({
      code: "regression_mission_unrelated",
      message: "The trusted regression mission is not supported by the ImpactMap and AppMap.",
    });
  }
  if (blockers.length > 0) {
    return { ready: false, blockers };
  }
  return {
    ready: true,
    profile: profile.value,
    appMap: appMap.value,
    impactMap: impactMap.value,
    mission: intentMissions[0],
    missions: missions.value,
  };
}

export function hasTrustedRegressionImpact(
  mission: TestMission,
  impactMap: ImpactMap,
  appMap: AppMap,
): boolean {
  if (mission.kind !== "regression" || mission.claimIds.length !== 0 || !mission.impactEvidence) {
    return false;
  }
  const mappedRoutes = new Set(appMap.routes.map((route) => route.path));
  const mappedComponents = new Set(appMap.components.map((component) => component.name));
  const mappedApis = new Set(appMap.apis.map((api) => `${api.method.toUpperCase()} ${api.path}`));
  const affectedRoutes = new Set(impactMap.affectedRoutes.map((route) => route.path));
  const affectedComponents = new Set(
    impactMap.affectedComponents.map((component) => component.name),
  );
  const affectedApis = new Set(
    impactMap.affectedApis.map((api) => `${api.method.toUpperCase()} ${api.path}`),
  );
  const routesMatch = mission.impactEvidence.routes.every(
    (route) => mappedRoutes.has(route) && affectedRoutes.has(route),
  );
  const componentsMatch = mission.impactEvidence.components.every(
    (component) => mappedComponents.has(component) && affectedComponents.has(component),
  );
  const apisMatch = mission.impactEvidence.apis.every((api) => {
    const key = `${api.method.toUpperCase()} ${api.path}`;
    return mappedApis.has(key) && affectedApis.has(key);
  });
  const evidenceCount =
    mission.impactEvidence.routes.length +
    mission.impactEvidence.components.length +
    mission.impactEvidence.apis.length;
  return evidenceCount > 0 && routesMatch && componentsMatch && apisMatch;
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
