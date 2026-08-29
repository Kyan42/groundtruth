import { readFile } from "node:fs/promises";
import path from "node:path";

import { z } from "zod";

import { type AppMap, AppMapSchema, type AppProfile, AppProfileSchema } from "@/lib/domain/schemas";

export type AppConfiguration =
  | { ready: true; profile: AppProfile; appMap: AppMap }
  | { ready: false; blockers: Array<{ code: string; message: string }> };

export async function loadAppConfiguration(owner: string, repo: string): Promise<AppConfiguration> {
  const safeSegment = /^[A-Za-z0-9_.-]+$/;
  if (!safeSegment.test(owner) || !safeSegment.test(repo)) {
    return {
      ready: false,
      blockers: [{ code: "app_config_path_invalid", message: "Repository name is not supported." }],
    };
  }

  const directory = path.join(process.cwd(), "config", "apps", owner, repo);
  const [profile, appMap] = await Promise.all([
    readJson(path.join(directory, "app-profile.json"), AppProfileSchema),
    readJson(path.join(directory, "app-map.json"), AppMapSchema),
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

  if (!profile.ok || !appMap.ok) {
    return { ready: false, blockers };
  }
  return { ready: true, profile: profile.value, appMap: appMap.value };
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
