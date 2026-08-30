import { readFile } from "node:fs/promises";
import path from "node:path";

import { NextResponse } from "next/server";
import { z } from "zod";

const SNAPSHOTS = new Set(["fernway-pr4-v1"]);
const DashboardSnapshotSchema = z.object({
  schemaVersion: z.literal(1),
  recorded: z.object({
    label: z.literal("Recorded from a real Runloop execution"),
    sourceRunId: z.uuid(),
    exportedAt: z.iso.datetime(),
  }),
  status: z.literal("complete"),
  number: z.number().int().positive(),
  branch: z.string().min(1),
  duration: z.string().min(1),
  cost: z.string().min(1),
  prUrl: z.url(),
  claims: z.array(
    z.object({
      id: z.string().min(1),
      verdict: z.enum(["p", "f", "u"]),
      text: z.string().min(1),
      sub: z.string(),
      verdict_line: z.string().min(1),
      dur: z.number().positive(),
      videoUrl: z.string().regex(/^\/api\/artifacts\/obj_[A-Za-z0-9]+$/),
      steps: z.array(z.array(z.union([z.string(), z.number()]))),
      net: z.array(z.array(z.union([z.string(), z.number()]))),
    }),
  ).length(4),
});

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(
  _request: Request,
  context: { params: Promise<{ snapshotId: string }> },
): Promise<NextResponse> {
  const { snapshotId } = await context.params;
  if (!SNAPSHOTS.has(snapshotId)) {
    return NextResponse.json(
      { error: { code: "recorded_run_not_found", message: "Unknown recorded run snapshot." } },
      { status: 404 },
    );
  }

  const snapshotPath = path.join(
    process.cwd(),
    "fixtures",
    "recorded-runs",
    `${snapshotId}.json`,
  );

  try {
    const payload = DashboardSnapshotSchema.safeParse(
      JSON.parse(await readFile(snapshotPath, "utf8")),
    );
    if (!payload.success) {
      return NextResponse.json(
        { error: { code: "recorded_run_invalid", message: "The recorded run snapshot is invalid." } },
        { status: 500 },
      );
    }
    return NextResponse.json(payload.data);
  } catch (error) {
    if (isMissingFile(error)) {
      return NextResponse.json(
        {
          error: {
            code: "recorded_run_missing",
            message: "The sanitized Fernway PR #4 snapshot has not been added yet.",
          },
        },
        { status: 404 },
      );
    }
    throw error;
  }
}

function isMissingFile(error: unknown): error is NodeJS.ErrnoException {
  return error instanceof Error && "code" in error && error.code === "ENOENT";
}
