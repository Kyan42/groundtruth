import { type Browser, type BrowserContext, chromium, type Page } from "playwright";

// The browser the exploring agent drives. The agent sees the page as an accessibility snapshot whose
// elements carry throwaway refs (e12); every action resolves a ref and records the stable locator
// Playwright recommends for that element (test id, role + name, label), which Step 3 turns into scripts.
//
// Work is grouped into journeys: each journey is a fresh browser session (no cookies or stored login)
// with its own video, and becomes one Playwright script.

export type Step = {
  n: number;
  journey: string;               // j1, j2, ...
  t: number;                     // seconds since the journey started, i.e. its position in the journey's video
  action: string;                // click, type, select, press, navigate, back, wait_for, screenshot
  locator?: string;              // stable locator for the element acted on, e.g. getByRole('button', { name: 'Add to cart' })
  value?: string;
  url: string;                   // page URL after the step
  ok: boolean;
  note?: string;
};

export type Journey = {
  id: string;
  name: string;
  claimIds: string[];
  startPath: string;
  resetData: boolean;
  video?: string;
};

const MAX_SNAPSHOT_CHARS = 40_000;

export class ExplorerBrowser {
  readonly steps: Step[] = [];
  readonly journeys: Journey[] = [];
  readonly consoleErrors: string[] = [];
  readonly failedRequests: string[] = [];
  private context!: BrowserContext;
  private page!: Page;
  private journeyStarted = 0;

  private constructor(
    private browser: Browser, readonly baseUrl: string,
    private headers: Record<string, string>, private videoDir?: string,
  ) {}

  // Opens the browser on the app's home page, in an initial journey the agent can replace with start_journey.
  static async open(baseUrl: string, headers: Record<string, string>, videoDir?: string): Promise<ExplorerBrowser> {
    const b = new ExplorerBrowser(await chromium.launch(), baseUrl, headers, videoDir);
    await b.newSession({ id: "j1", name: "(initial page)", claimIds: [], startPath: "/", resetData: false });
    return b;
  }

  get journey(): Journey { return this.journeys.at(-1)!; }

  // Seconds into the current journey (and its video).
  get elapsed(): number { return Math.round((Date.now() - this.journeyStarted) / 100) / 10; }

  // Ends the current journey (finalizing its video) and starts a fresh browser session at startPath.
  // An initial journey with no steps is replaced rather than kept.
  async startJourney(j: { name: string; claimIds: string[]; startPath?: string; resetData: boolean }): Promise<string> {
    const current = this.journey;
    const unused = !this.steps.some((s) => s.journey === current.id);
    await this.endSession();
    if (unused) this.journeys.pop();
    await this.newSession({ id: `j${this.journeys.length + 1}`, name: j.name, claimIds: j.claimIds, startPath: j.startPath ?? "/", resetData: j.resetData });
    return `Started journey ${this.journey.id} "${j.name}" in a fresh browser session.\n\n${await this.snapshot()}`;
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
      const url = this.resolve(path);
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
    this.steps.push({ n: this.steps.length + 1, journey: this.journey.id, t: this.elapsed, action: "screenshot", url: this.relativeUrl(), ok: true });
    return (await this.page.screenshot({ caret: "initial" })).toString("base64");
  }

  async close(): Promise<Journey[]> {
    await this.endSession();
    await this.browser.close();
    return this.journeys;
  }

  private async newSession(j: Journey): Promise<void> {
    this.context = await this.browser.newContext({
      extraHTTPHeaders: this.headers,       // the tunnel's token, on every request the page makes
      viewport: { width: 1280, height: 800 },
      recordVideo: this.videoDir ? { dir: this.videoDir, size: { width: 1280, height: 800 } } : undefined,
    });
    this.page = await this.context.newPage();
    this.journeyStarted = Date.now();
    this.page.on("console", (m) => { if (m.type() === "error") this.consoleErrors.push(`[${j.id}] ${m.text().split("\n")[0]}`); });
    this.page.on("response", (r) => { if (r.status() >= 400) this.failedRequests.push(`[${j.id}] ${r.status()} ${r.url()}`); });
    this.journeys.push(j);
    await this.page.goto(this.resolve(j.startPath).toString(), { waitUntil: "load" });
    await this.settle();
  }

  // Closing the context writes the journey's video.
  private async endSession(): Promise<void> {
    const video = this.page.video();
    await this.context.close();
    if (video) this.journey.video = await video.path();
  }

  // Runs an action, waits for the page to settle, records the step, and returns what happened plus a fresh snapshot.
  private async record(action: string, fn: () => Promise<{ locator?: string; value?: string }>): Promise<string> {
    const step = { n: this.steps.length + 1, journey: this.journey.id, t: this.elapsed, action };
    try {
      const { locator, value } = await fn();
      await this.settle();
      this.steps.push({ ...step, locator, value, url: this.relativeUrl(), ok: true });
      return `${action}${locator ? ` ${locator}` : ""}${value !== undefined ? ` "${value}"` : ""}: done.\n\n${await this.snapshot()}`;
    } catch (err) {
      const note = (err instanceof Error ? err.message : String(err)).split("\n")[0];
      this.steps.push({ ...step, url: this.relativeUrl(), ok: false, note });
      throw new Error(note);
    }
  }

  // Gives navigations, client-side updates and the requests an action triggers time to finish. Dev servers
  // compile a page on its first visit, so a click can take several seconds to land.
  private async settle(): Promise<void> {
    await this.page.waitForLoadState("load").catch(() => {});
    await this.page.waitForLoadState("networkidle", { timeout: 8000 }).catch(() => {});
  }

  // Paths resolve against the app; anything outside it is refused.
  private resolve(path: string): URL {
    const url = new URL(path, this.baseUrl);
    if (url.origin !== new URL(this.baseUrl).origin) throw new Error(`Only pages of the app under test can be opened (${url.origin})`);
    return url;
  }

  private relativeUrl(): string {
    const u = new URL(this.page.url());
    return u.origin === new URL(this.baseUrl).origin ? u.pathname + u.search : u.toString();
  }
}
