import { type Run, RunSchema } from "@/lib/domain/schemas";

export function makeRun(overrides: Partial<Run> = {}): Run {
  return RunSchema.parse({
    id: "0ef64fcb-9e51-44e0-862a-918a29e80862",
    key: `kyan42/groundtruth#1@${"b".repeat(40)}`,
    repository: {
      owner: "Kyan42",
      name: "groundtruth",
      cloneUrl: "https://github.com/Kyan42/groundtruth.git",
    },
    pullRequest: {
      number: 1,
      url: "https://github.com/Kyan42/groundtruth/pull/1",
      title: "Add intent verification",
      body: "The dashboard must show separate intent and regression verdicts.",
      baseRef: "main",
      baseSha: "a".repeat(40),
      headRef: "feature",
      headSha: "b".repeat(40),
    },
    status: "creating",
    createdAt: "2026-08-29T20:00:00.000Z",
    updatedAt: "2026-08-29T20:00:00.000Z",
    ...overrides,
  });
}
