import { NextResponse } from "next/server";
import { z } from "zod";

import { toErrorResponse } from "@/lib/domain/errors";
import { InvalidByteRangeError, parseByteRange } from "@/lib/http/byte-range";
import { getRunloopClient } from "@/lib/runloop/client";

export const runtime = "nodejs";

const ArtifactIdSchema = z.string().regex(/^obj_[A-Za-z0-9]+$/);

export async function GET(
  request: Request,
  context: { params: Promise<{ artifactId: string }> },
): Promise<Response> {
  try {
    const range = parseByteRange(request.headers.get("range"));
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
    const response = await fetch(download.download_url, {
      cache: "no-store",
      headers: range ? { Range: range } : undefined,
      signal: request.signal,
    });
    if (response.status === 416) {
      return new Response(null, {
        status: 416,
        headers: artifactHeaders(response.headers),
      });
    }
    if (![200, 206].includes(response.status) || !response.body) {
      return NextResponse.json(
        { error: { code: "artifact_unavailable", message: "Artifact download failed." } },
        { status: 502 },
      );
    }

    const headers = artifactHeaders(response.headers);
    headers.set(
      "Content-Type",
      info.metadata?.mime_type ?? response.headers.get("content-type") ?? "application/octet-stream",
    );
    headers.set("Content-Disposition", `inline; filename="${safeFilename(info.name)}"`);
    return new Response(response.body, {
      status: response.status,
      headers,
    });
  } catch (error) {
    if (error instanceof InvalidByteRangeError) {
      return NextResponse.json(
        { error: { code: "invalid_range", message: error.message } },
        {
          status: 400,
          headers: {
            "Accept-Ranges": "bytes",
            "Cache-Control": "private, no-store",
          },
        },
      );
    }
    const result = toErrorResponse(error);
    return NextResponse.json(result.body, { status: result.status });
  }
}

function artifactHeaders(upstream: Headers): Headers {
  const headers = new Headers({
    "Accept-Ranges": upstream.get("accept-ranges") ?? "bytes",
    "Cache-Control": "private, no-store",
  });
  for (const name of ["content-length", "content-range", "etag", "last-modified"]) {
    const value = upstream.get(name);
    if (value !== null) {
      headers.set(name, value);
    }
  }
  return headers;
}

function safeFilename(value: string): string {
  return value.replace(/[^A-Za-z0-9._-]/g, "_");
}
