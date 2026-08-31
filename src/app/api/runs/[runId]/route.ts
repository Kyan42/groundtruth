import { NextResponse } from "next/server";

import { toErrorResponse } from "@/lib/domain/errors";
import { getRunService } from "@/lib/orchestration/run-service";
import { buildDashboardPayload } from "@/lib/views/build-dashboard-payload";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(
  _request: Request,
  context: { params: Promise<{ runId: string }> },
): Promise<NextResponse> {
  try {
    const { runId } = await context.params;
    const service = getRunService();
    const run = await service.get(runId);
    if (run.status === "analyzing_intent") {
      // Self-heal: viewing a run re-attaches the server-side intent pump after
      // a restart; Reflex replays stream history, so stranded runs catch up.
      void service.ensureIntentPump(runId);
    }
    return NextResponse.json(buildDashboardPayload(run));
  } catch (error) {
    const response = toErrorResponse(error);
    return NextResponse.json(response.body, { status: response.status });
  }
}
