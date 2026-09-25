// Groundtruth test helpers, copied next to every compiled script.
//
// resetApp: restores the app's starting data. During a Groundtruth replay it asks the server that's running
//   the replay (GROUNDTRUTH_RESET_URL), which runs the app's `reset` command where the app lives. Locally,
//   set GROUNDTRUTH_RESET to a shell command instead.
// check: runs one assertion as a named step and marks it in the video (a box around the element, a ✓/✗
//   banner). Assertions are soft: a failed check is recorded and the journey continues.
import { appendFileSync } from "node:fs";
import { execSync } from "node:child_process";
import { expect, type Locator, type Page, test as base } from "playwright/test";

const pageStarted = new WeakMap<Page, number>();

export const test = base.extend<{ resetApp: () => Promise<void> }>({
  page: async ({ page }, use) => {
    pageStarted.set(page, Date.now());   // the video starts with the page; check times are measured from here
    // The video shows a blank page until the app's first page loads; the dashboard starts playback there.
    const onLoad = () => {
      if (page.url() === "about:blank") return;
      page.off("load", onLoad);
      write({ test: test.info().title, event: "loaded", t: since(page) });
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

// One line per check (and per reset), for whoever runs the replay: the dashboard places them on the video.
function record(page: Page, title: string, passed: boolean) {
  write({ test: test.info().title, check: title.split(" ·")[0], passed, t: since(page) });
}

function write(line: Record<string, unknown>) {
  const file = process.env.GROUNDTRUTH_CHECKS_FILE;
  if (file) appendFileSync(file, `${JSON.stringify(line)}\n`);
}

const since = (page: Page) => Math.round((Date.now() - (pageStarted.get(page) ?? Date.now())) / 100) / 10;

async function mark(page: Page, target: Locator | null, passed: boolean, title: string) {
  const colour = passed ? "#0b8259" : "#c03d29";
  try {
    if (target && (await target.count()) > 0) {
      await target.highlight({ style: { outline: `3px solid ${colour}`, outlineOffset: "3px", borderRadius: "6px", background: `${colour}1f` } });
    }
    await page.evaluate(([text, colour]) => {
      const el = document.createElement("div");
      el.id = "__groundtruth_check";
      el.setAttribute("aria-hidden", "true");
      el.textContent = text;
      el.style.cssText = `position:fixed;top:12px;left:12px;z-index:2147483647;pointer-events:none;background:${colour};color:#fff;`
        + "font:600 18px/1.3 system-ui,sans-serif;padding:8px 14px;border-radius:8px;box-shadow:0 4px 16px rgba(0,0,0,.25);max-width:70vw";
      document.documentElement.appendChild(el);
    }, [`${passed ? "✓" : "✗"} ${title.split(" · ").slice(1).join(" · ")}`, colour] as const);
    await page.waitForTimeout(1000);
  } catch { /* cosmetic */ }
  await page.evaluate(() => document.getElementById("__groundtruth_check")?.remove()).catch(() => {});
  await page.hideHighlight().catch(() => {});
}

export { expect };
