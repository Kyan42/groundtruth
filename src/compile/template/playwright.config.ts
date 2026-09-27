import { defineConfig } from "playwright/test";

// Groundtruth's tests: run against GROUNDTRUTH_BASE_URL (plus GROUNDTRUTH_HEADERS as JSON, e.g. a sandbox
// tunnel's token). To reset data, set GROUNDTRUTH_RESET to the app's reset command. For example:
//   GROUNDTRUTH_BASE_URL=http://localhost:3000 GROUNDTRUTH_RESET="npm run seed -- --reset" \
//     npx playwright test -c .groundtruth/support/playwright.config.ts
const output = process.env.GROUNDTRUTH_OUTPUT ?? "../test-results";
export default defineConfig({
  testDir: "../tests",
  outputDir: output,
  workers: 1,
  retries: 0,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  reporter: [["list"], ["json", { outputFile: `${output}/results.json` }]],
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
