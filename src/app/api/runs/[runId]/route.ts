import { NextResponse } from "next/server";

import { toErrorResponse } from "@/lib/domain/errors";
import { getRunService } from "@/lib/orchestration/run-service";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(
  _request: Request,
  context: { params: Promise<{ runId: string }> },
): Promise<NextResponse> {
  try {
    const { runId } = await context.params;
    return NextResponse.json(await getRunService().getView(runId));
  } catch (error) {
    const response = toErrorResponse(error);
    return NextResponse.json(response.body, { status: response.status });
  }
}
