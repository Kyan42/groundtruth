import { type Browser, type BrowserContext, chromium, type Page } from "playwright";

// The browser the exploring agent drives. The agent sees the page as an accessibility snapshot whose
// elements carry throwaway refs (e12); every action resolves a ref and records the stable locator
// Playwright recommends for that element (test id, role + name, label), which Step 3 turns into scripts.

export type Step = {
  n: number;
  action: string;                // click, type, select, press, navigate, back, wait_for, screenshot, snapshot
  locator?: string;              // stable locator for the element acted on, e.g. getByRole('button', { name: 'Add to cart' })
  value?: string;
  url: string;                   // page URL after the step
  ok: boolean;
  note?: string;
};

const MAX_SNAPSHOT_CHARS = 40_000;

export class ExplorerBrowser {
  readonly steps: Step[] = [];
  readonly consoleErrors: string[] = [];
  readonly failedRequests: string[] = [];
  private constructor(
    private browser: Browser, private context: BrowserContext, private page: Page, readonly baseUrl: string,
  ) {}

  static async open(baseUrl: string, headers: Record<string, string>, videoDir?: string): Promise<ExplorerBrowser> {
    const browser = await chromium.launch();
    const context = await browser.newContext({
      extraHTTPHeaders: headers,       // the tunnel's token, on every request the page makes
      viewport: { width: 1280, height: 800 },
      recordVideo: videoDir ? { dir: videoDir, size: { width: 1280, height: 800 } } : undefined,
    });
    const page = await context.newPage();
    const b = new ExplorerBrowser(browser, context, page, baseUrl);
    page.on("console", (m) => { if (m.type() === "error") b.consoleErrors.push(m.text().split("\n")[0]); });
    page.on("response", (r) => { if (r.status() >= 400) b.failedRequests.push(`${r.status()} ${r.url()}`); });
    await page.goto(baseUrl, { waitUntil: "load" });
    return b;
  }

  // The page as the agent sees it: where it is, and the accessibility tree with refs.
  async snapshot(): Promise<string> {
    let tree = await this.page.ariaSnapshot({ mode: "ai" });
    if (tree.length > MAX_SNAPSHOT_CHARS) tree = `${tree.slice(0, MAX_SNAPSHOT_CHARS)}\n… (snapshot truncated)`;
    return `URL: ${this.relativeUrl()}\nTitle: ${await this.page.title()}\n\n${tree}`;
  }

  // Resolves a ref from the latest snapshot to its stable, user-facing locator.
  async describe(ref: string): Promise<string> {
    const loc = this.page.locator(`aria-ref=${ref}`);
    if ((await loc.count()) !== 1) throw new Error(`No element ${ref} on the current page; take a new snapshot`);
    return (await loc.normalize()).toString();
  }

  async act(action: "click" | "type" | "select", ref: string, value?: string): Promise<string> {
    return this.record(action, async () => {
      const locator = await this.describe(ref);
      const el = this.page.locator(`aria-ref=${ref}`);
      // Playwright waits until the element is visible, enabled and stable, then acts like a user.
      if (action === "click") await el.click({ timeout: 10_000 });
      else if (action === "type") await el.fill(value ?? "", { timeout: 10_000 });
      else await el.selectOption(value ?? "", { timeout: 10_000 });
      return { locator, value };
    });
  }

  async press(key: string): Promise<string> {
    return this.record("press", async () => { await this.page.keyboard.press(key); return { value: key }; });
  }

  async navigate(path: string): Promise<string> {
    return this.record("navigate", async () => {
      const url = new URL(path, this.baseUrl);
      // Stay inside the app under test.
      if (url.origin !== new URL(this.baseUrl).origin) throw new Error(`Only pages of the app under test can be opened (${url.origin})`);
      await this.page.goto(url.toString(), { waitUntil: "load" });
      return { value: url.pathname + url.search };
    });
  }

  async back(): Promise<string> {
    return this.record("back", async () => { await this.page.goBack({ waitUntil: "load" }); return {}; });
  }

  async waitFor(text: string, seconds = 10): Promise<string> {
    return this.record("wait_for", async () => {
      await this.page.getByText(text).first().waitFor({ state: "visible", timeout: seconds * 1000 });
      return { value: text };
    });
  }

  async screenshot(): Promise<string> {
    this.steps.push({ n: this.steps.length + 1, action: "screenshot", url: this.relativeUrl(), ok: true });
    return (await this.page.screenshot({ caret: "initial" })).toString("base64");
  }

  // Closes the browser; the video (if recording) is written on close.
  async close(): Promise<string | undefined> {
    const video = this.page.video();
    await this.context.close();
    await this.browser.close();
    return video ? await video.path() : undefined;
  }

  // Runs an action, waits for the page to settle, records the step, and returns what happened plus a fresh snapshot.
  private async record(action: string, fn: () => Promise<{ locator?: string; value?: string }>): Promise<string> {
    try {
      const { locator, value } = await fn();
      await this.settle();
      this.steps.push({ n: this.steps.length + 1, action, locator, value, url: this.relativeUrl(), ok: true });
      return `${action}${locator ? ` ${locator}` : ""}${value !== undefined ? ` "${value}"` : ""}: done.\n\n${await this.snapshot()}`;
    } catch (err) {
      const note = (err instanceof Error ? err.message : String(err)).split("\n")[0];
      this.steps.push({ n: this.steps.length + 1, action, url: this.relativeUrl(), ok: false, note });
      throw new Error(note);
    }
  }

  // Gives client-side updates and requests triggered by an action a moment to finish.
  private async settle(): Promise<void> {
    await this.page.waitForLoadState("load").catch(() => {});
    await this.page.waitForLoadState("networkidle", { timeout: 3000 }).catch(() => {});
  }

  private relativeUrl(): string {
    const u = new URL(this.page.url());
    return u.origin === new URL(this.baseUrl).origin ? u.pathname + u.search : u.toString();
  }
}
