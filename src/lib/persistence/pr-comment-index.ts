import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";

import { z } from "zod";

import { getEnvironment } from "@/lib/config/env";

/**
 * Tracks the single Groundtruth result comment per PR conversation, keyed by
 * "owner/repo#number" rather than by run id. A rerun creates a brand new Run
 * record (see rerunVerification in run-service.ts), but the PR should still
 * show one comment that updates in place, not a new one per attempt.
 */
const CommentIndexFileSchema = z.object({
  version: z.literal(1),
  comments: z.record(z.string().min(1), z.number().int().positive()),
});

type CommentIndexFile = z.infer<typeof CommentIndexFileSchema>;

export function pullRequestCommentKey(owner: string, repo: string, number: number): string {
  return `${owner.toLowerCase()}/${repo.toLowerCase()}#${number}`;
}

export class PrCommentIndex {
  private queue: Promise<void> = Promise.resolve();

  constructor(private readonly filePath: string) {}

  async get(key: string): Promise<number | undefined> {
    return this.withLock(async () => (await this.read()).comments[key]);
  }

  async set(key: string, commentId: number): Promise<void> {
    await this.withLock(async () => {
      const index = await this.read();
      index.comments[key] = commentId;
      await this.write(index);
    });
  }

  private async read(): Promise<CommentIndexFile> {
    try {
      const contents = await readFile(this.filePath, "utf8");
      return CommentIndexFileSchema.parse(JSON.parse(contents));
    } catch (error) {
      if (isNodeError(error) && error.code === "ENOENT") {
        return { version: 1, comments: {} };
      }
      if (error instanceof z.ZodError || error instanceof SyntaxError) {
        return { version: 1, comments: {} };
      }
      throw error;
    }
  }

  private async write(index: CommentIndexFile): Promise<void> {
    const validIndex = CommentIndexFileSchema.parse(index);
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

let singleton: PrCommentIndex | undefined;

export function getPrCommentIndex(): PrCommentIndex {
  const environment = getEnvironment();
  const stateDirectory = path.resolve(
    /* turbopackIgnore: true */ process.cwd(),
    environment.GROUNDTRUTH_STATE_DIR,
  );
  singleton ??= new PrCommentIndex(path.join(stateDirectory, "pr-comment-index.json"));
  return singleton;
}

export function resetPrCommentIndexForTests(): void {
  singleton = undefined;
}
