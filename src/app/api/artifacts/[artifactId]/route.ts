import { NextResponse } from "next/server";
import { z } from "zod";

import { toErrorResponse } from "@/lib/domain/errors";
import { getRunloopClient } from "@/lib/runloop/client";

export const runtime = "nodejs";

const ArtifactIdSchema = z.string().regex(/^obj_[A-Za-z0-9]+$/);

export async function GET(
  _request: Request,
  context: { params: Promise<{ artifactId: string }> },
): Promise<Response> {
  try {
    const { artifactId: rawArtifactId } = await context.params;
    const artifactId = ArtifactIdSchema.parse(rawArtifactId);
    const object = getRunloopClient().storageObject.fromId(artifactId);
    const [info, download] = await Promise.all([object.getInfo(), object.getDownloadUrl(300)]);
    if (typeof info.metadata?.run_id !== "string" || !info.metadata.run_id) {
      return NextResponse.json(
        { error: { code: "artifact_not_found", message: "Artifact not found." } },
        { status: 404 },
      );
    }
    const response = await fetch(download.download_url, { cache: "no-store" });
    if (!response.ok || !response.body) {
      return NextResponse.json(
        { error: { code: "artifact_unavailable", message: "Artifact download failed." } },
        { status: 502 },
      );
    }
    return new Response(response.body, {
      headers: {
        "Cache-Control": "private, no-store",
        "Content-Type": info.metadata?.mime_type ?? "application/octet-stream",
        "Content-Disposition": `inline; filename="${safeFilename(info.name)}"`,
      },
    });
  } catch (error) {
    const result = toErrorResponse(error);
    return NextResponse.json(result.body, { status: result.status });
  }
}

function safeFilename(value: string): string {
  return value.replace(/[^A-Za-z0-9._-]/g, "_");
}
