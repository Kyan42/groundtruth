import { ReflexSocket } from "@runloop/reflex-client";

import { toErrorResponse } from "@/lib/domain/errors";
import { getRunService } from "@/lib/orchestration/run-service";
import { configureReflexServer } from "@/lib/reflex/client";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const encoder = new TextEncoder();

export async function GET(
  request: Request,
  context: { params: Promise<{ runId: string }> },
): Promise<Response> {
  const { runId } = await context.params;
  const service = getRunService();

  try {
    const run = await service.get(runId);
    const initialView = await service.getView(runId);
    let cleanup = () => {};

    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        let closed = false;
        let queue = Promise.resolve();

        const send = (event: string, value: unknown) => {
          if (!closed) {
            controller.enqueue(
              encoder.encode(`event: ${event}\ndata: ${JSON.stringify(value)}\n\n`),
            );
          }
        };
        const close = () => {
          if (!closed) {
            closed = true;
            controller.close();
          }
        };

        send("run", initialView);
        if (!run.reflexIntent || run.status !== "analyzing_intent") {
          close();
          return;
        }

        configureReflexServer();
        const socket = new ReflexSocket();
        const unsubscribe = socket.subscribe(run.reflexIntent.streamId, (event) => {
          queue = queue
            .then(async () => {
              const view = await service.enqueueIntentEvent(runId, event);
              send("run", view);
            })
            .catch((error) => {
              const response = toErrorResponse(error);
              send("error", response.body.error);
            });
        });
        const keepAlive = setInterval(() => send("keepalive", { at: Date.now() }), 15_000);

        cleanup = () => {
          if (closed) {
            return;
          }
          clearInterval(keepAlive);
          unsubscribe();
          socket.close();
          close();
        };
        request.signal.addEventListener("abort", cleanup, { once: true });
      },
      cancel() {
        cleanup();
      },
    });

    return new Response(stream, {
      headers: {
        "Cache-Control": "no-cache, no-transform",
        Connection: "keep-alive",
        "Content-Type": "text/event-stream",
        "X-Accel-Buffering": "no",
      },
    });
  } catch (error) {
    const response = toErrorResponse(error);
    return Response.json(response.body, { status: response.status });
  }
}
