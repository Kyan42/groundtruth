import type { Locator, Page } from "playwright";
import { expect } from "playwright/test";

// Checks: the fixed menu of assertions the agent can ask for. Our code runs each one as a real Playwright
// assertion (which retries until it holds or times out), so a check's pass or fail comes from the page,
// not from the agent's reading of it. Each check is also what Step 3 compiles into a line of the script.

export const ASSERTIONS = [
  "visible", "hidden",                          // element shown / not shown (or absent)
  "contains_text", "has_text",                  // element text includes / equals expected (whitespace-normalized)
  "count",                                      // number of elements the target matches
  "value",                                      // form field's current value
  "disabled", "enabled", "checked", "unchecked",
  "expanded", "collapsed", "selected",          // aria-expanded / aria-selected
  "invalid",                                    // the browser's form validation rejects the field
  "url",                                        // page path (and query) equals expected
  "request_sent", "request_not_sent",           // a request like "POST /api/cart" since the last action
] as const;
export type Assertion = (typeof ASSERTIONS)[number];

// Assertions about the page or its network traffic rather than an element.
export const PAGE_ASSERTIONS: Assertion[] = ["url", "request_sent", "request_not_sent"];
// Assertions that need an expected value.
export const NEEDS_EXPECTED: Assertion[] = ["contains_text", "has_text", "count", "value", "url", "request_sent", "request_not_sent"];

export type Check = {
  id: string;                  // k1, k2, ...
  journey: string;
  t: number;                   // seconds into the journey's video
  afterStep: number;           // last browser step before the check
  claimIds: string[];
  assert: Assertion;
  locator?: string;            // stable locator of the element(s) checked
  expected?: string;
  observed: string;            // what the page actually showed, read by our code
  passed: boolean;
};

// step: the browser step the request followed (0 = the journey's first page load).
export type RequestRecord = { journey: string; t: number; step: number; method: string; path: string; status?: number };

const TIMEOUT = 5000;          // how long an assertion retries before failing
const NOT_SENT_WAIT = 2000;    // how long to watch for a request that shouldn't happen

// Runs one assertion. Returns whether it held and what was observed; throws only for misuse
// (e.g. a target that matches several elements where one is needed), which isn't a check result.
export async function runAssertion(
  page: Page, assert: Assertion, target: Locator | undefined, expected: string | undefined,
  requestsSinceAction: () => RequestRecord[],
): Promise<{ passed: boolean; observed: string }> {
  const t = { timeout: TIMEOUT };
  const el = () => { if (!target) throw new Error(`${assert} needs a target element`); return target; };
  const attempt = async (assertion: () => Promise<unknown>) => {
    try { await assertion(); return true; }
    catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      if (/strict mode violation/i.test(message)) throw new Error(`The target matches several elements; narrow it down (${message.split("\n")[0]})`);
      if (!/expect\(|Timed out|timeout/i.test(message)) throw err;
      return false;
    }
  };

  switch (assert) {
    case "visible": return { passed: await attempt(() => expect(el()).toBeVisible(t)), observed: await visibility(el()) };
    case "hidden": return { passed: await attempt(() => expect(el()).toBeHidden(t)), observed: await visibility(el()) };
    case "contains_text": return { passed: await attempt(() => expect(el()).toContainText(expected!, t)), observed: await text(el()) };
    case "has_text": return { passed: await attempt(() => expect(el()).toHaveText(expected!, t)), observed: await text(el()) };
    case "count": {
      const n = Number(expected);
      if (!Number.isInteger(n)) throw new Error(`count needs a whole number as expected, got "${expected}"`);
      return { passed: await attempt(() => expect(el()).toHaveCount(n, t)), observed: `${await el().count()} matching` };
    }
    case "value": return { passed: await attempt(() => expect(el()).toHaveValue(expected!, t)), observed: await el().inputValue().catch(() => "(not a form field)") };
    case "disabled": return { passed: await attempt(() => expect(el()).toBeDisabled(t)), observed: await state(el(), "disabled") };
    case "enabled": return { passed: await attempt(() => expect(el()).toBeEnabled(t)), observed: await state(el(), "disabled") };
    case "checked": return { passed: await attempt(() => expect(el()).toBeChecked(t)), observed: await state(el(), "checked") };
    case "unchecked": return { passed: await attempt(() => expect(el()).toBeChecked({ ...t, checked: false })), observed: await state(el(), "checked") };
    case "expanded": return { passed: await attempt(() => expect(el()).toHaveAttribute("aria-expanded", "true", t)), observed: await attr(el(), "aria-expanded") };
    case "collapsed": return { passed: await attempt(() => expect(el()).toHaveAttribute("aria-expanded", "false", t)), observed: await attr(el(), "aria-expanded") };
    case "selected": return { passed: await attempt(() => expect(el()).toHaveAttribute("aria-selected", "true", t)), observed: await attr(el(), "aria-selected") };
    case "invalid": {
      const validity = () => el().evaluate((e) => {
        const f = e as HTMLInputElement;
        return f.validity ? { valid: f.validity.valid, message: f.validationMessage } : undefined;
      });
      const passed = await attempt(() => expect.poll(async () => (await validity())?.valid, t).toBe(false));
      const v = await validity();
      return { passed, observed: !v ? "(not a form field)" : v.valid ? "valid" : `invalid: ${v.message}` };
    }
    case "url": {
      const path = () => { const u = new URL(page.url()); return u.pathname + u.search; };
      const passed = await attempt(() => expect.poll(path, t).toBe(expected!));
      return { passed, observed: path() };
    }
    case "request_sent":
    case "request_not_sent": {
      const matches = () => requestsSinceAction().filter((r) => requestMatches(r, expected!));
      const sent = assert === "request_sent";
      const deadline = Date.now() + (sent ? TIMEOUT : NOT_SENT_WAIT);
      while (Date.now() < deadline && (matches().length > 0) !== sent) await page.waitForTimeout(100);
      if (!sent) await page.waitForTimeout(Math.max(0, deadline - Date.now()));
      const found = matches();
      const all = requestsSinceAction();
      return {
        passed: (found.length > 0) === sent,
        observed: found.length ? found.map(describeRequest).join(", ")
          : all.length ? `no match; requests since the last action: ${all.slice(0, 8).map(describeRequest).join(", ")}` : "no requests since the last action",
      };
    }
  }
}

// The Playwright assertion a check stands for, as shown on screen in videos (the compiled script adds
// soft mode and a message). `target` is locator code such as page.getByTestId("cart-link").
export function assertionCode(assert: Assertion, target: string | undefined, expected: string | undefined): string {
  const t = target ?? "page";
  const x = JSON.stringify(expected ?? "");
  const matcher: Record<Assertion, string> = {
    visible: "toBeVisible()", hidden: "toBeHidden()", contains_text: `toContainText(${x})`, has_text: `toHaveText(${x})`,
    count: `toHaveCount(${Number(expected)})`, value: `toHaveValue(${x})`, disabled: "toBeDisabled()", enabled: "toBeEnabled()",
    checked: "toBeChecked()", unchecked: "toBeChecked({ checked: false })",
    expanded: `toHaveAttribute("aria-expanded", "true")`, collapsed: `toHaveAttribute("aria-expanded", "false")`,
    selected: `toHaveAttribute("aria-selected", "true")`, invalid: "", url: `toHaveURL(${x})`, request_sent: "", request_not_sent: "",
  };
  // Not single matchers in Playwright: shown as what the compiled script does, or as a plain description.
  if (assert === "invalid") return `expect.poll(() => ${t}.evaluate((e) => e.validity.valid)).toBe(false)`;
  if (assert === "request_sent") return `// a request ${x} was sent`;
  if (assert === "request_not_sent") return `// no request ${x} was sent`;
  return `expect(${assert === "url" ? "page" : t}).${matcher[assert]}`;
}

// "POST /api/cart" matches a POST whose path starts with /api/cart; "/api/cart" matches any method.
export function requestMatches(r: RequestRecord, spec: string): boolean {
  const m = spec.trim().match(/^(?:([A-Za-z]+)\s+)?(\S+)$/);
  if (!m) return false;
  const [, method, path] = m;
  return (!method || method.toUpperCase() === r.method) && r.path.startsWith(path);
}

const describeRequest = (r: RequestRecord) => `${r.method} ${r.path}${r.status ? ` → ${r.status}` : ""}`;

async function visibility(l: Locator): Promise<string> {
  const n = await l.count();
  if (n === 0) return "not on the page";
  const shown = await l.first().isVisible();
  return `${shown ? "visible" : "present but not visible"}${n > 1 ? ` (${n} matching)` : ""}`;
}

// The text the assertion compares: textContent with whitespace collapsed, like Playwright. Text in
// neighbouring elements runs together ("HelmetQty 2"), unlike what the page shows.
async function text(l: Locator): Promise<string> {
  const n = await l.count();
  if (n === 0) return "not on the page";
  const s = ((await l.first().textContent().catch(() => "")) ?? "").replace(/\s+/g, " ").trim();
  return `"${s.length > 200 ? `${s.slice(0, 200)}…` : s}"${n > 1 ? ` (first of ${n})` : ""}`;
}

async function state(l: Locator, which: "disabled" | "checked"): Promise<string> {
  if ((await l.count()) === 0) return "not on the page";
  if (which === "disabled") return (await l.first().isDisabled()) ? "disabled" : "enabled";
  return (await l.first().isChecked().catch(() => undefined)) ? "checked" : "not checked";
}

async function attr(l: Locator, name: string): Promise<string> {
  if ((await l.count()) === 0) return "not on the page";
  return `${name}=${(await l.first().getAttribute(name)) ?? "(not set)"}`;
}
