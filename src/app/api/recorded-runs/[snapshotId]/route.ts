import { readFile } from "node:fs/promises";
import path from "node:path";

import { NextResponse } from "next/server";

const SNAPSHOTS = new Set(["fernway-pr4-v1"]);

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
    const payload: unknown = JSON.parse(await readFile(snapshotPath, "utf8"));
    if (
      typeof payload !== "object" ||
      payload === null ||
      !("claims" in payload) ||
      !Array.isArray(payload.claims)
    ) {
      return NextResponse.json(
        { error: { code: "recorded_run_invalid", message: "The recorded run snapshot is invalid." } },
        { status: 500 },
      );
    }
    return NextResponse.json(payload);
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
