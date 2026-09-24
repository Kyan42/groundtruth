import { type Browser, type BrowserContext, chromium, type Locator, type Page, type Request } from "playwright";
import { type Assertion, type Check, PAGE_ASSERTIONS, type RequestRecord, runAssertion } from "./checks.js";

// The browser the exploring agent drives. The agent sees the page as an accessibility snapshot whose
// elements carry throwaway refs (e12); every action resolves a ref to the stable locator Playwright
// recommends for that element (test id, role + name, label), acts through it, and records it, so
// Step 3 can turn the trace into scripts.
//
// Work is grouped into journeys: each journey is a fresh browser session (no cookies or stored login)
// with its own video, and becomes one Playwright script. Videos show a cursor for each action and a
// box plus ✓/✗ banner for each check.

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

// What a check points at: an element from the latest snapshot, or (for things that may not be on the
// page, and for counting) a role + name or a text, optionally inside a container from the snapshot.
export type CheckTarget = { ref?: string; role?: string; name?: string; text?: string; within?: string };

const MAX_SNAPSHOT_CHARS = 40_000;
const SETTLE_QUIET_MS = 500;       // the page counts as settled after this long with no requests or changes
const SETTLE_MAX_MS = 12_000;      // give up waiting after this long
const DOM_CHURN_MS = 3_000;        // after this long, ignore DOM changes alone (animations, clocks)
const LONG_REQUEST_MS = 10_000;    // requests open longer than this (polling, streams) don't count as pending
const CHECK_HOLD_MS = 1_400;       // how long a check's box and banner stay on screen for the video

export class ExplorerBrowser {
  readonly steps: Step[] = [];
  readonly journeys: Journey[] = [];
  readonly checks: Check[] = [];
  readonly requests: RequestRecord[] = [];
  readonly consoleErrors: string[] = [];
  readonly failedRequests: string[] = [];
  private context!: BrowserContext;
  private page!: Page;
  private journeyStarted = 0;
  private pending = new Map<Request, number>();   // requests in flight → when they started
  private actionStep = 0;                         // the step the latest action was recorded as

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
    const unused = !this.steps.some((s) => s.journey === current.id) && !this.checks.some((c) => c.journey === current.id);
    await this.endSession();
    if (unused) {
      this.journeys.pop();
      for (let i = this.requests.length - 1; i >= 0; i--) if (this.requests[i].journey === current.id) this.requests.splice(i, 1);
    }
    await this.newSession({ id: `j${this.journeys.length + 1}`, name: j.name, claimIds: j.claimIds, startPath: j.startPath ?? "/", resetData: j.resetData });
    return `Started journey ${this.journey.id} "${j.name}" in a fresh browser session.\n\n${await this.snapshot()}`;
  }

  // The page as the agent sees it: where it is, and the accessibility tree with refs.
  async snapshot(): Promise<string> {
    let tree = await this.page.ariaSnapshot({ mode: "ai" });
    if (tree.length > MAX_SNAPSHOT_CHARS) tree = `${tree.slice(0, MAX_SNAPSHOT_CHARS)}\n… (snapshot truncated)`;
    return `URL: ${this.relativeUrl()}\nTitle: ${await this.page.title()}\n\n${tree}`;
  }

  // Resolves a ref from the latest snapshot to its stable, user-facing locator. Falls back to the ref
  // itself (with a note) when the stable locator doesn't pin down exactly that one element.
  async resolve(ref: string): Promise<{ locator: Locator; description: string; stable: boolean }> {
    const byRef = this.page.locator(`aria-ref=${ref}`);
    if ((await byRef.count()) !== 1) throw new Error(`No element ${ref} on the current page; take a new snapshot`);
    const stable = await byRef.normalize();
    const description = stable.toString();
    if ((await stable.count()) === 1) return { locator: stable, description, stable: true };
    return { locator: byRef, description, stable: false };
  }

  async act(action: "click" | "type" | "select", ref: string, value?: string): Promise<string> {
    return this.record(action, async () => {
      // Act through the stable locator: it's what the script will use, and what the video's action label shows.
      const { locator: el, description, stable } = await this.resolve(ref);
      // Playwright waits until the element is visible, enabled and stable, then acts like a user.
      if (action === "click") await el.click({ timeout: 10_000 });
      else if (action === "type") await el.fill(value ?? "", { timeout: 10_000 });
      else await el.selectOption(value ?? "", { timeout: 10_000 });
      return { locator: description, value, note: stable ? undefined : "locator matches several elements; acted on the snapshot's element" };
    });
  }

  async press(key: string): Promise<string> {
    return this.record("press", async () => { await this.page.keyboard.press(key); return { value: key }; });
  }

  async navigate(path: string): Promise<string> {
    return this.record("navigate", async () => {
      const url = this.appUrl(path);
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

  // Runs one assertion from the menu against the page as it is now, records it, and marks it in the video.
  async check(c: { assert: Assertion; target?: CheckTarget; expected?: string; claimIds: string[] }): Promise<Check> {
    const onPage = PAGE_ASSERTIONS.includes(c.assert);
    if (!onPage && !c.target) throw new Error(`${c.assert} needs a target`);
    const target = onPage ? undefined : await this.checkTarget(c.target!);
    const since = this.actionStep;
    const { passed, observed } = await runAssertion(this.page, c.assert, target?.locator, c.expected,
      () => this.requests.filter((r) => r.journey === this.journey.id && r.step >= since));
    // t is when the result is known and its overlay appears (a failing assertion retries first).
    const check: Check = {
      id: `k${this.checks.length + 1}`, journey: this.journey.id, t: this.elapsed, afterStep: this.steps.length, claimIds: c.claimIds,
      assert: c.assert, locator: target?.description, expected: c.expected, observed, passed,
    };
    this.checks.push(check);
    await this.showCheck(check, target?.locator);
    return check;
  }

  async close(): Promise<Journey[]> {
    await this.endSession();
    await this.browser.close();
    return this.journeys;
  }

  private async checkTarget(t: CheckTarget): Promise<{ locator: Locator; description: string }> {
    if (t.ref) {
      const { locator, description, stable } = await this.resolve(t.ref);
      return { locator, description: stable ? description : `${description} (not unique)` };
    }
    const scope: Page | Locator = t.within ? (await this.resolve(t.within)).locator : this.page;
    let locator: Locator;
    if (t.role) locator = scope.getByRole(t.role as Parameters<Page["getByRole"]>[0], t.name ? { name: t.name } : undefined);
    else if (t.text) locator = scope.getByText(t.text);
    else throw new Error("A target needs a ref, a role (with an optional name), or a text");
    return { locator, description: locator.toString() };
  }

  // Draws the check into the page for the video: a box around the element(s) and a ✓/✗ banner, briefly.
  private async showCheck(check: Check, target?: Locator): Promise<void> {
    const colour = check.passed ? "#0b8259" : "#c03d29";
    const label = `${check.passed ? "✓" : "✗"} ${check.claimIds.join(", ")} · ${check.assert.replace(/_/g, " ")}${check.expected ? ` "${check.expected}"` : ""}`;
    try {
      if (target && (await target.count()) > 0) {
        await target.highlight({ style: { outline: `3px solid ${colour}`, outlineOffset: "3px", borderRadius: "6px", background: `${colour}1f` } });
      }
      await this.page.evaluate(([text, colour]) => {
        const el = document.createElement("div");
        el.id = "__groundtruth_check";
        el.setAttribute("aria-hidden", "true");
        el.textContent = text;
        el.style.cssText = `position:fixed;top:12px;left:12px;z-index:2147483647;pointer-events:none;background:${colour};color:#fff;`
          + "font:600 18px/1.3 system-ui,sans-serif;padding:8px 14px;border-radius:8px;box-shadow:0 4px 16px rgba(0,0,0,.25);max-width:70vw";
        document.documentElement.appendChild(el);
      }, [label, colour] as const);
      await this.page.waitForTimeout(CHECK_HOLD_MS);
    } catch { /* the overlay is cosmetic; a page that navigated away mid-check just loses it */ }
    await this.page.evaluate(() => document.getElementById("__groundtruth_check")?.remove()).catch(() => {});
    await this.page.hideHighlight().catch(() => {});
  }

  private async newSession(j: Journey): Promise<void> {
    this.context = await this.browser.newContext({
      extraHTTPHeaders: this.headers,       // the tunnel's token, on every request the page makes
      viewport: { width: 1280, height: 800 },
      recordVideo: this.videoDir
        ? { dir: this.videoDir, size: { width: 1280, height: 800 }, showActions: { duration: 600, position: "top-right", fontSize: 18, cursor: "pointer" } }
        : undefined,
    });
    // Count DOM changes so settle() can tell when the page has stopped updating.
    await this.context.addInitScript(() => {
      const w = window as unknown as { __gtChanges: number };
      w.__gtChanges = 0;
      new MutationObserver(() => { w.__gtChanges++; }).observe(document, { subtree: true, childList: true, characterData: true, attributes: true });
    });
    this.page = await this.context.newPage();
    this.journeyStarted = Date.now();
    this.actionStep = 0;
    this.pending.clear();
    this.page.on("console", (m) => { if (m.type() === "error") this.consoleErrors.push(`[${j.id}] ${m.text().split("\n")[0]}`); });
    this.page.on("request", (r) => this.onRequest(j.id, r));
    this.page.on("requestfinished", (r) => this.pending.delete(r));
    this.page.on("requestfailed", (r) => this.pending.delete(r));
    this.page.on("response", (r) => {
      if (r.status() >= 400) this.failedRequests.push(`[${j.id}] ${r.status()} ${r.url()}`);
      const rec = this.requests.findLast((x) => x.journey === j.id && x.path === this.relativePath(r.url()) && x.method === r.request().method() && x.status === undefined);
      if (rec) rec.status = r.status();
    });
    this.journeys.push(j);
    await this.page.goto(this.appUrl(j.startPath).toString(), { waitUntil: "load" });
    await this.settle();
  }

  private onRequest(journey: string, r: Request): void {
    if (["websocket", "eventsource"].includes(r.resourceType())) return;
    this.pending.set(r, Date.now());
    // Keep the requests a check might ask about: page loads and form posts, fetch and XHR. Not assets.
    if (["document", "fetch", "xhr"].includes(r.resourceType())) {
      this.requests.push({ journey, t: this.elapsed, step: this.actionStep, method: r.method(), path: this.relativePath(r.url()) });
    }
  }

  // Closing the context writes the journey's video.
  private async endSession(): Promise<void> {
    const video = this.page.video();
    await this.context.close();
    if (video) this.journey.video = await video.path();
  }

  // Runs an action, waits for the page to settle, records the step, and returns what happened plus a fresh snapshot.
  private async record(action: string, fn: () => Promise<{ locator?: string; value?: string; note?: string }>): Promise<string> {
    const step = { n: this.steps.length + 1, journey: this.journey.id, t: this.elapsed, action };
    this.actionStep = step.n;        // requests from here on follow this action
    try {
      const { locator, value, note } = await fn();
      const settled = await this.settle();
      const notes = [note, settled ? undefined : `page still changing after ${SETTLE_MAX_MS / 1000}s`].filter(Boolean).join("; ") || undefined;
      this.steps.push({ ...step, locator, value, url: this.relativeUrl(), ok: true, note: notes });
      return `${action}${locator ? ` ${locator}` : ""}${value !== undefined ? ` "${value}"` : ""}: done.${notes ? ` (${notes})` : ""}\n\n${await this.snapshot()}`;
    } catch (err) {
      const note = (err instanceof Error ? err.message : String(err)).split("\n")[0];
      this.steps.push({ ...step, url: this.relativeUrl(), ok: false, note });
      throw new Error(note);
    }
  }

  // Waits until the page has finished reacting to an action: no requests in flight, no URL change and no
  // DOM changes for SETTLE_QUIET_MS. Client-side navigation and data refreshes (e.g. a cart badge updating
  // after a POST) finish well after the click itself returns. Returns false if it gave up.
  private async settle(): Promise<boolean> {
    const start = Date.now();
    await this.page.waitForLoadState("load", { timeout: SETTLE_MAX_MS }).catch(() => {});
    let quietSince = Date.now();
    let lastChanges = -1;
    let lastUrl = this.page.url();
    while (Date.now() - start < SETTLE_MAX_MS) {
      await new Promise((r) => setTimeout(r, 100));
      const now = Date.now();
      const busy = [...this.pending.values()].some((began) => now - began < LONG_REQUEST_MS);
      const changes = await this.page.evaluate(() => (window as unknown as { __gtChanges?: number }).__gtChanges ?? 0).catch(() => -2);
      const url = this.page.url();
      const domChanged = changes !== lastChanges && now - start < DOM_CHURN_MS;
      if (busy || domChanged || url !== lastUrl || changes === -2) quietSince = now;
      lastChanges = changes;
      lastUrl = url;
      if (now - quietSince >= SETTLE_QUIET_MS) return true;
    }
    return false;
  }

  // Paths resolve against the app; anything outside it is refused.
  private appUrl(path: string): URL {
    const url = new URL(path, this.baseUrl);
    if (url.origin !== new URL(this.baseUrl).origin) throw new Error(`Only pages of the app under test can be opened (${url.origin})`);
    return url;
  }

  private relativePath(raw: string): string {
    const u = new URL(raw);
    return u.origin === new URL(this.baseUrl).origin ? u.pathname + u.search : u.toString();
  }

  private relativeUrl(): string { return this.relativePath(this.page.url()); }
}
