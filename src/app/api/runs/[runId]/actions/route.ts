import { NextResponse } from "next/server";
import { z } from "zod";

import { toErrorResponse } from "@/lib/domain/errors";
import { getRunService } from "@/lib/orchestration/run-service";

export const runtime = "nodejs";

const ActionRequestSchema = z.object({
  action: z.enum([
    "resume",
    "approve_intent",
    "retry_intent",
    "interrupt_intent",
    "start_verification",
  ]),
});

export async function POST(
  request: Request,
  context: { params: Promise<{ runId: string }> },
): Promise<NextResponse> {
  try {
    const body = ActionRequestSchema.safeParse(await request.json());
    if (!body.success) {
      return NextResponse.json(
        {
          error: {
            code: "invalid_action",
            message: "Choose a supported run action.",
            retryable: false,
          },
        },
        { status: 400 },
      );
    }

    const { runId } = await context.params;
    const service = getRunService();
    const view =
      body.data.action === "resume"
        ? await service.resume(runId)
        : body.data.action === "approve_intent"
          ? await service.approveIntent(runId)
          : body.data.action === "retry_intent"
            ? await service.retryIntent(runId)
            : body.data.action === "interrupt_intent"
              ? await service.interruptIntent(runId)
              : await service.startVerification(runId);
    return NextResponse.json(view);
  } catch (error) {
    const response = toErrorResponse(error);
    return NextResponse.json(response.body, { status: response.status });
  }
}
