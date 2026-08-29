import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";

import { z } from "zod";

import { getEnvironment } from "@/lib/config/env";
import { GroundtruthError } from "@/lib/domain/errors";
import { type Run, RunSchema } from "@/lib/domain/schemas";
import type { RunIndex } from "@/lib/persistence/run-index";

const RunIndexFileSchema = z.object({
  version: z.literal(1),
  runs: z.array(RunSchema),
});

type RunIndexFile = z.infer<typeof RunIndexFileSchema>;

export class JsonRunIndex implements RunIndex {
  private queue: Promise<void> = Promise.resolve();

  constructor(private readonly filePath: string) {}

  async getById(id: string): Promise<Run | undefined> {
    return this.withLock(async () => (await this.read()).runs.find((run) => run.id === id));
  }

  async getByKey(key: string): Promise<Run | undefined> {
    return this.withLock(async () => (await this.read()).runs.find((run) => run.key === key));
  }

  async getOrCreate(
    key: string,
    create: () => Run,
  ): Promise<{ run: Run; created: boolean }> {
    return this.withLock(async () => {
      const index = await this.read();
      const existing = index.runs.find((run) => run.key === key);
      if (existing) {
        return { run: existing, created: false };
      }

      const run = RunSchema.parse(create());
      if (run.key !== key) {
        throw new GroundtruthError(
          "run_key_mismatch",
          "The new run did not match the requested idempotency key.",
          500,
        );
      }
      index.runs.push(run);
      await this.write(index);
      return { run, created: true };
    });
  }

  async save(run: Run): Promise<Run> {
    return this.withLock(async () => {
      const validRun = RunSchema.parse(run);
      const index = await this.read();
      const position = index.runs.findIndex((candidate) => candidate.id === validRun.id);
      if (position === -1) {
        index.runs.push(validRun);
      } else {
        index.runs[position] = validRun;
      }
      await this.write(index);
      return validRun;
    });
  }

  async update(id: string, update: (run: Run) => Run): Promise<Run> {
    return this.withLock(async () => {
      const index = await this.read();
      const position = index.runs.findIndex((run) => run.id === id);
      if (position === -1) {
        throw new GroundtruthError("run_not_found", `Run ${id} was not found.`, 404);
      }

      const next = RunSchema.parse(update(index.runs[position]));
      if (next.id !== id) {
        throw new GroundtruthError("run_id_changed", "A run update cannot change its ID.", 500);
      }
      index.runs[position] = next;
      await this.write(index);
      return next;
    });
  }

  private async read(): Promise<RunIndexFile> {
    try {
      const contents = await readFile(this.filePath, "utf8");
      return RunIndexFileSchema.parse(JSON.parse(contents));
    } catch (error) {
      if (isNodeError(error) && error.code === "ENOENT") {
        return { version: 1, runs: [] };
      }
      if (error instanceof z.ZodError || error instanceof SyntaxError) {
        throw new GroundtruthError(
          "run_index_corrupt",
          `The run index at ${this.filePath} is invalid.`,
          500,
          false,
          error instanceof z.ZodError ? error.issues : undefined,
        );
      }
      throw error;
    }
  }

  private async write(index: RunIndexFile): Promise<void> {
    const validIndex = RunIndexFileSchema.parse(index);
    const directory = path.dirname(this.filePath);
    await mkdir(directory, { recursive: true });
    const temporaryPath = `${this.filePath}.${process.pid}.${Date.now()}.tmp`;
    await writeFile(temporaryPath, `${JSON.stringify(validIndex, null, 2)}\n`, "utf8");
    await rename(temporaryPath, this.filePath);
  }

  private async withLock<T>(operation: () => Promise<T>): Promise<T> {
    const previous = this.queue;
    let release!: () => void;
    this.queue = new Promise<void>((resolve) => {
      release = resolve;
    });
    await previous;
    try {
      return await operation();
    } finally {
      release();
    }
  }
}

function isNodeError(error: unknown): error is NodeJS.ErrnoException {
  return error instanceof Error && "code" in error;
}

let singleton: JsonRunIndex | undefined;

export function getRunIndex(): RunIndex {
  const environment = getEnvironment();
  const stateDirectory = path.resolve(
    /* turbopackIgnore: true */ process.cwd(),
    environment.GROUNDTRUTH_STATE_DIR,
  );
  singleton ??= new JsonRunIndex(path.join(stateDirectory, "run-index.json"));
  return singleton;
}
