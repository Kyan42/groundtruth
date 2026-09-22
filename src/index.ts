import { createServer } from "node:http";
import { createNodeMiddleware } from "@octokit/webhooks";
import { SmeeClient } from "smee-client";
import { app } from "./app.js";
import { config } from "./config.js";

const WEBHOOK_PATH = "/api/github/webhooks";
const githubMiddleware = createNodeMiddleware(app.webhooks, {
  path: WEBHOOK_PATH,
  // One line per failed delivery instead of the default full AggregateError dump.
  log: {
    debug: () => {},
    info: console.info,
    warn: console.warn,
    error: (error: Error) => console.error(`[webhook] failed: ${error.message}`),
  },
});

const server = createServer(async (req, res) => {
  if (await githubMiddleware(req, res)) return;
  if (req.method === "GET" && req.url === "/") {
    res.writeHead(200, { "content-type": "text/plain" }).end("groundtruth ok\n");
    return;
  }
  res.writeHead(404).end();
});

server.listen(config.port, async () => {
  const target = `http://localhost:${config.port}${WEBHOOK_PATH}`;
  console.log(`[server] listening, webhooks at ${target}`);

  try {
    const { data } = await app.octokit.request("GET /app");
    console.log(`[server] authenticated as GitHub App "${data?.name}" (id ${data?.id})`);
  } catch (error) {
    console.error(`[server] GitHub App auth failed — check GITHUB_APP_ID and the private key: ${error}`);
  }

  if (config.smeeUrl) {
    await new SmeeClient({ source: config.smeeUrl, target, logger: console }).start();
  }
});
