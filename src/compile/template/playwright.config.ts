import { defineConfig } from "playwright/test";

// Replays against GROUNDTRUTH_BASE_URL (plus GROUNDTRUTH_HEADERS as JSON, e.g. a sandbox tunnel's token).
export default defineConfig({
  testDir: ".",
  outputDir: "../replay",
  workers: 1,
  retries: 0,
  timeout: 90_000,
  expect: { timeout: 10_000 },
  reporter: [["list"], ["json", { outputFile: "../replay/results.json" }]],
  use: {
    baseURL: process.env.GROUNDTRUTH_BASE_URL,
    extraHTTPHeaders: process.env.GROUNDTRUTH_HEADERS ? JSON.parse(process.env.GROUNDTRUTH_HEADERS) : undefined,
    viewport: { width: 1280, height: 800 },
    actionTimeout: 15_000,
    // A little slower than full speed, so the video is easy to follow (the helper also points before acting).
    launchOptions: { slowMo: 150 },
    video: { mode: "on", size: { width: 1280, height: 800 }, show: { actions: { duration: 500, fontSize: 18, cursor: "none" } } },
    trace: "retain-on-failure",
  },
});
