import { createReadStream, existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import type { IncomingMessage, ServerResponse } from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { config } from "../config.js";

// The dashboard: a list of test runs and a page per run (journeys, videos, claims, checks), read straight
// from the run folders under config.runsDir. Pages are static HTML that fetch the run's trace.json.

const PAGES = path.dirname(fileURLToPath(import.meta.url));
const RUN_ID = /^\w[\w.-]*$/;                // folder names only: no slashes, and no "." or ".."
const VIDEO = /^\w[\w@.-]*\.webm$/;

// Handles dashboard routes; returns false for anything else.
export async function handleDashboard(req: IncomingMessage, res: ServerResponse): Promise<boolean> {
  if (req.method !== "GET" && req.method !== "HEAD") return false;
  const url = new URL(req.url ?? "/", "http://localhost");
  const parts = url.pathname.split("/").filter(Boolean).map(decodeURIComponent);

  if (parts.length === 0) { res.writeHead(302, { location: "/runs" }).end(); return true; }
  if (parts[0] !== "runs") return false;

  if (parts.length === 1) return page(res, "runs.html");
  if (parts.length === 2 && parts[1] === "index.json") return json(res, listRuns());

  const id = parts[1];
  if (!RUN_ID.test(id) || !existsSync(path.join(config.runsDir, id))) return notFound(res);
  const dir = path.join(config.runsDir, id);

  if (parts.length === 2) return page(res, "run.html");
  if (parts.length === 3 && parts[2] === "trace.json") {
    const file = path.join(dir, "trace.json");
    // No trace yet means the run is still going; the page shows that and polls.
    return existsSync(file) ? json(res, JSON.parse(readFileSync(file, "utf8"))) : json(res, { running: true }, 202);
  }
  if (parts.length === 4 && parts[2] === "videos" && VIDEO.test(parts[3])) {
    const file = path.join(dir, parts[3]);
    return existsSync(file) ? video(req, res, file) : notFound(res);
  }
  return notFound(res);
}

export const runUrl = (runDir: string) => `${config.dashboardUrl}/runs/${encodeURIComponent(path.basename(runDir))}`;

// One summary per run folder, newest first.
function listRuns() {
  if (!existsSync(config.runsDir)) return [];
  return readdirSync(config.runsDir, { withFileTypes: true })
    .filter((d) => d.isDirectory() && RUN_ID.test(d.name))
    .map((d) => {
      const dir = path.join(config.runsDir, d.name);
      const file = path.join(dir, "trace.json");
      if (!existsSync(file)) return { id: d.name, running: true, startedAt: statSync(dir).mtime.toISOString() };
      try {
        const t = JSON.parse(readFileSync(file, "utf8"));
        return {
          id: d.name, pr: t.pr, title: t.title, sha: t.sha, startedAt: t.startedAt ?? statSync(file).mtime.toISOString(),
          claims: (t.claims ?? []).length, statuses: (t.results ?? []).map((r: { status: string }) => r.status),
          journeys: (t.journeys ?? []).length, checks: (t.checks ?? []).length, seconds: t.seconds, costUsd: t.costUsd,
        };
      } catch { return { id: d.name, broken: true, startedAt: statSync(dir).mtime.toISOString() }; }
    })
    .sort((a, b) => String(b.startedAt).localeCompare(String(a.startedAt)));
}

// Pages are read on every request so edits show up on reload.
function page(res: ServerResponse, name: string): true {
  res.writeHead(200, { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" })
    .end(readFileSync(path.join(PAGES, name)));
  return true;
}

function json(res: ServerResponse, body: unknown, status = 200): true {
  res.writeHead(status, { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" }).end(JSON.stringify(body));
  return true;
}

function notFound(res: ServerResponse): true {
  res.writeHead(404, { "content-type": "text/plain" }).end("not found\n");
  return true;
}

// Browsers seek in a video with Range requests; without them the timeline can't jump.
function video(req: IncomingMessage, res: ServerResponse, file: string): true {
  const size = statSync(file).size;
  const range = /^bytes=(\d*)-(\d*)$/.exec(req.headers.range ?? "");
  const headers = { "content-type": "video/webm", "accept-ranges": "bytes" };
  if (!range) {
    res.writeHead(200, { ...headers, "content-length": size });
    if (req.method === "HEAD") res.end(); else createReadStream(file).pipe(res);
    return true;
  }
  let start = range[1] ? Number(range[1]) : size - Number(range[2]);
  let end = range[1] && range[2] ? Number(range[2]) : size - 1;
  start = Math.max(0, start); end = Math.min(end, size - 1);
  if (start > end) { res.writeHead(416, { "content-range": `bytes */${size}` }).end(); return true; }
  res.writeHead(206, { ...headers, "content-range": `bytes ${start}-${end}/${size}`, "content-length": end - start + 1 });
  if (req.method === "HEAD") res.end(); else createReadStream(file, { start, end }).pipe(res);
  return true;
}
