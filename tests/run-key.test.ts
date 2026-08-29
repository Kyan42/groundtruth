import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { buildRunKey } from "@/lib/domain/schemas";
import { JsonRunIndex } from "@/lib/persistence/json-run-index";
import { makeRun } from "./fixtures";

const temporaryDirectories: string[] = [];

afterEach(async () => {
  await Promise.all(
    temporaryDirectories.splice(0).map((directory) => rm(directory, { recursive: true })),
  );
});

describe("run idempotency", () => {
  it("normalizes repository and SHA casing", () => {
    expect(buildRunKey("Kyan42", "GroundTruth", 7, "A".repeat(40))).toBe(
      `kyan42/groundtruth#7@${"a".repeat(40)}`,
    );
  });

  it("returns one record for the same exact head SHA", async () => {
    const directory = await mkdtemp(path.join(tmpdir(), "groundtruth-index-"));
    temporaryDirectories.push(directory);
    const index = new JsonRunIndex(path.join(directory, "runs.json"));
    const run = makeRun();

    const first = await index.getOrCreate(run.key, () => run);
    const second = await index.getOrCreate(run.key, () => ({
      ...run,
      id: "aa03e049-0662-40eb-8161-cb26b126e3fa",
    }));

    expect(first.created).toBe(true);
    expect(second.created).toBe(false);
    expect(second.run.id).toBe(run.id);
  });
});
