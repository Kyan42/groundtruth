// Groundtruth test helpers, copied next to every compiled script.
//
// resetApp: restores the app's starting data. During a Groundtruth replay it asks the server that's running
//   the replay (GROUNDTRUTH_RESET_URL), which runs the app's `reset` command where the app lives. Locally,
//   set GROUNDTRUTH_RESET to a shell command instead.
// check: runs one assertion as a named step and marks it in the video: a box around the element and a
//   banner with the claim and the assertion's code (from tests/index.json). Assertions are soft: a failed check
//   is recorded and the journey continues.
// Every page also gets overlay.js: a visible cursor that glides between the automated mouse's positions.
import { appendFileSync, existsSync, readFileSync } from "node:fs";
import { execSync } from "node:child_process";
import path from "node:path";
import { expect, type Locator, type Page, test as base } from "playwright/test";

const HOLD_MS = 2500;   // how long a check's banner stays on screen, so it can be read in the video
const POINT_MS = 500;   // how long the cursor takes to glide to an element before acting on it
const pageStarted = new WeakMap<Page, number>();

// For the video only: before a click (or typing, selecting, ticking), move the mouse onto the element and
// pause, so the cursor visibly arrives before the action. The script itself stays plain Playwright.
function pointBeforeActing(page: Page) {
  type Proto = Record<string, unknown> & { __gtPoint?: boolean };
  const proto = Object.getPrototypeOf(page.locator("body")) as Proto;
  if (proto.__gtPoint) return;
  proto.__gtPoint = true;
  for (const name of ["click", "dblclick", "fill", "selectOption", "check", "uncheck"]) {
    const original = proto[name] as (...args: unknown[]) => Promise<unknown>;
    proto[name] = async function (this: Locator, ...args: unknown[]) {
      await this.hover({ timeout: 10_000 }).catch(() => {});
      await this.page().waitForTimeout(POINT_MS);
      return original.apply(this, args);
    };
  }
}

export const test = base.extend<{ resetApp: () => Promise<void> }>({
  page: async ({ page }, use, testInfo) => {
    const overlay = path.join(path.dirname(testInfo.file), "..", "support", "overlay.js");
    if (existsSync(overlay)) {
      await page.addInitScript({ path: overlay });
      pointBeforeActing(page);
    }
    pageStarted.set(page, Date.now());   // the video starts with the page; check times are measured from here
    // The video shows a blank page until the app's first page loads; the dashboard starts playback there.
    const onLoad = () => {
      if (page.url() === "about:blank") return;
      page.off("load", onLoad);
      write({ file: path.basename(test.info().file), test: test.info().title, event: "loaded", t: since(page) });
    };
    page.on("load", onLoad);
    await use(page);
  },
  resetApp: async ({}, use) => {
    await use(async () => {
      const url = process.env.GROUNDTRUTH_RESET_URL;
      if (url) {
        const res = await fetch(url, { method: "POST" });
        if (!res.ok) throw new Error(`Resetting the app's data failed: ${res.status} ${await res.text()}`);
      } else {
        const command = process.env.GROUNDTRUTH_RESET;
        if (!command) throw new Error("Set GROUNDTRUTH_RESET_URL or GROUNDTRUTH_RESET to reset the app's data");
        execSync(command, { stdio: "inherit" });
      }
    });
  },
});

export async function check(page: Page, title: string, target: Locator | null, assertion: () => Promise<unknown>): Promise<void> {
  await test.step(title, async () => {
    const errorsBefore = test.info().errors.length;
    await assertion();
    const passed = test.info().errors.length === errorsBefore;
    record(page, title, passed);
    await mark(page, target, passed, title);
  });
}

// One line per check (and per first page load), for whoever runs the replay: the dashboard places them on the video.
function record(page: Page, title: string, passed: boolean) {
  write({ file: path.basename(test.info().file), test: test.info().title, check: title.split(" ·")[0], passed, t: since(page) });
}

function write(line: Record<string, unknown>) {
  const file = process.env.GROUNDTRUTH_CHECKS_FILE;
  if (file) appendFileSync(file, `${JSON.stringify(line)}\n`);
}

const since = (page: Page) => Math.round((Date.now() - (pageStarted.get(page) ?? Date.now())) / 100) / 10;

// The banner text per check id, from this test file's entry in tests/index.json (written by the compiler).
const banners = new Map<string, Record<string, { title: string; code: string }>>();
function bannerFor(id: string, fallback: string) {
  const spec = test.info().file;
  if (!banners.has(spec)) {
    let checks = {};
    try {
      const index = path.join(path.dirname(spec), "index.json");
      const entries = existsSync(index) ? (JSON.parse(readFileSync(index, "utf8")) as { file: string; checks?: typeof checks }[]) : [];
      checks = entries.find((e) => e.file === path.basename(spec))?.checks ?? {};
    } catch { /* no banners: fall back to the step title */ }
    banners.set(spec, checks);
  }
  return banners.get(spec)![id] ?? { title: fallback, code: "" };
}

async function mark(page: Page, target: Locator | null, passed: boolean, title: string) {
  const colour = passed ? "#0b8259" : "#c03d29";
  const { title: text, code } = bannerFor(title.split(" ·")[0], title.split(" · ").slice(1).join(" · "));
  try {
    if (target && (await target.count()) > 0) {
      await target.highlight({ style: { outline: `3px solid ${colour}`, outlineOffset: "3px", borderRadius: "6px", background: `${colour}1f` } });
    }
    await page.evaluate(([t, c, p]) => (window as unknown as { __gtShowCheck?: (t: string, c: string, p: boolean) => void }).__gtShowCheck?.(t, c, p),
      [text, code, passed] as const);
    await page.waitForTimeout(HOLD_MS);
  } catch { /* cosmetic */ }
  await page.evaluate(() => (window as unknown as { __gtHideCheck?: () => void }).__gtHideCheck?.()).catch(() => {});
  await page.hideHighlight().catch(() => {});
}

export { expect };
