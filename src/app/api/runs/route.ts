import { NextResponse } from "next/server";
import { z } from "zod";

import { toErrorResponse } from "@/lib/domain/errors";
import { getRunService } from "@/lib/orchestration/run-service";

export const runtime = "nodejs";

const CreateRunRequestSchema = z.object({
  prUrl: z.string().min(1),
});

export async function POST(request: Request): Promise<NextResponse> {
  try {
    const body = CreateRunRequestSchema.safeParse(await request.json());
    if (!body.success) {
      return NextResponse.json(
        {
          error: {
            code: "invalid_request",
            message: "A public GitHub pull request URL is required.",
            retryable: false,
            details: body.error.issues,
          },
        },
        { status: 400 },
      );
    }

    const view = await getRunService().create(body.data.prUrl);
    return NextResponse.json(view, { status: 201 });
  } catch (error) {
    const response = toErrorResponse(error);
    return NextResponse.json(response.body, { status: response.status });
  }
}
