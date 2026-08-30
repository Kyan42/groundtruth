import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const runloop = vi.hoisted(() => ({
  fromId: vi.fn(),
  getDownloadUrl: vi.fn(),
  getInfo: vi.fn(),
}));

vi.mock("@/lib/runloop/client", () => ({
  getRunloopClient: () => ({
    storageObject: { fromId: runloop.fromId },
  }),
}));

import { GET } from "@/app/api/artifacts/[artifactId]/route";

describe("artifact route", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    runloop.fromId.mockReturnValue({
      getDownloadUrl: runloop.getDownloadUrl,
      getInfo: runloop.getInfo,
    });
    runloop.getInfo.mockResolvedValue({
      name: "evidence.webm",
      metadata: { mime_type: "video/webm", run_id: "run_123" },
    });
    runloop.getDownloadUrl.mockResolvedValue({
      download_url: "https://objects.example.test/signed",
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("forwards a byte range and preserves a partial response", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response("chunk", {
        status: 206,
        headers: {
          "Accept-Ranges": "bytes",
          "Content-Length": "5",
          "Content-Range": "bytes 0-4/20",
          ETag: '"artifact-etag"',
        },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const response = await GET(
      new Request("http://localhost/api/artifacts/obj_123", {
        headers: { Range: "bytes=0-4" },
      }),
      { params: Promise.resolve({ artifactId: "obj_123" }) },
    );

    expect(fetchMock).toHaveBeenCalledWith(
      "https://objects.example.test/signed",
      expect.objectContaining({ headers: { Range: "bytes=0-4" } }),
    );
    expect(response.status).toBe(206);
    expect(response.headers.get("content-range")).toBe("bytes 0-4/20");
    expect(response.headers.get("content-length")).toBe("5");
    expect(response.headers.get("accept-ranges")).toBe("bytes");
    expect(response.headers.get("etag")).toBe('"artifact-etag"');
    expect(response.headers.get("cache-control")).toBe("private, no-store");
    expect(await response.text()).toBe("chunk");
  });

  it("rejects malformed multi-range requests before creating a signed URL", async () => {
    const response = await GET(
      new Request("http://localhost/api/artifacts/obj_123", {
        headers: { Range: "bytes=0-4,10-14" },
      }),
      { params: Promise.resolve({ artifactId: "obj_123" }) },
    );

    expect(response.status).toBe(400);
    expect(runloop.fromId).not.toHaveBeenCalled();
    expect(runloop.getDownloadUrl).not.toHaveBeenCalled();
  });
});
