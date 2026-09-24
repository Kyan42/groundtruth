import { chromium } from "playwright";

// Proves a real browser can use the booted app: load a page through the tunnel, optionally wait for
// an element, and record what went wrong along the way (console errors, failed requests).

export type BrowserCheck = {
  status: number | null;
  title: string;
  rendered: boolean;              // the page showed visible text
  textSample: string;             // the first visible text, to see what the page actually showed
  found: boolean | null;          // null when no selector was given
  consoleErrors: string[];
  failedRequests: string[];
  screenshot: string;
};

export async function checkInBrowser(opts: {
  url: string;
  headers: Record<string, string>;
  expectSelector?: string;
  screenshotPath: string;
}): Promise<BrowserCheck> {
  const browser = await chromium.launch();
  try {
    // The tunnel header goes on every request the page makes (scripts, styles, API calls), not just the first.
    const context = await browser.newContext({ extraHTTPHeaders: opts.headers });
    const page = await context.newPage();
    const consoleErrors: string[] = [];
    const failedRequests: string[] = [];
    page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });
    page.on("requestfailed", (r) => failedRequests.push(`${r.url()} (${r.failure()?.errorText})`));
    page.on("response", (r) => { if (r.status() >= 400) failedRequests.push(`${r.url()} (HTTP ${r.status()})`); });

    const response = await page.goto(opts.url, { waitUntil: "load", timeout: 60_000 });
    // "load" fires before a client-rendered app draws anything, so an HTTP 200 can still be a blank page.
    // Wait for visible text; dev servers compile on first request, which can take a while.
    const rendered = await page.waitForFunction(() => (document.body?.innerText ?? "").trim().length > 0, null, { timeout: 90_000 })
      .then(() => true, () => false);
    let found: boolean | null = null;
    if (opts.expectSelector) {
      found = await page.locator(opts.expectSelector).first().waitFor({ state: "visible", timeout: 30_000 })
        .then(() => true, () => false);
    }
    // caret: "initial" stops Playwright from injecting a style to hide the text cursor, which modifies
    // the page and can make React report a hydration mismatch that the app didn't cause.
    await page.screenshot({ path: opts.screenshotPath, fullPage: true, caret: "initial" });
    const textSample = (await page.evaluate(() => document.body?.innerText ?? "")).replace(/\s+/g, " ").trim().slice(0, 120);
    return { status: response?.status() ?? null, title: await page.title(), rendered, textSample, found, consoleErrors, failedRequests, screenshot: opts.screenshotPath };
  } finally {
    await browser.close();
  }
}
