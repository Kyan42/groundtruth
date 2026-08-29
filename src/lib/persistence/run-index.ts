import type { Run } from "@/lib/domain/schemas";

export interface RunIndex {
  getById(id: string): Promise<Run | undefined>;
  getByKey(key: string): Promise<Run | undefined>;
  getOrCreate(key: string, create: () => Run): Promise<{ run: Run; created: boolean }>;
  save(run: Run): Promise<Run>;
  update(id: string, update: (run: Run) => Run): Promise<Run>;
}
