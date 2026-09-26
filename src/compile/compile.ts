import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import type { ApprovedClaim } from "../comment.js";
import type { ClaimResult } from "../explore/agent.js";
import type { Journey, Step } from "../explore/browser.js";
import { assertionCode, type Check } from "../explore/checks.js";

// Step 3, first cut: compile an exploration trace into Playwright tests, one per journey. Rules, not a
// model: keep the actions that succeeded and the checks the verdicts rest on, in the order they happened;
// drop failed actions, redone checks, screenshots, and actions after a journey's last cited check.
// Replaying the result on the same app is what tells us whether the rules were enough.

export type Trace = {
  pr: string; sha: string; title?: string;
  claims: ApprovedClaim[]; results: ClaimResult[]; journeys: Journey[]; steps: Step[]; checks: Check[];
};

export type Compiled = { files: Record<string, string>; notes: string[] };

export function compileRun(trace: Trace, runId: string): Compiled {
  const notes: string[] = [];
  const cited = new Set(trace.results.flatMap((r) => r.checkIds ?? []));
  const tests: { journey: Journey; code: string }[] = [];

  for (const j of trace.journeys) {
    type Event = { t: number; step?: Step; check?: Check };
    const events: Event[] = [
      ...trace.steps.filter((s) => s.journey === j.id).map((step) => ({ t: step.t, step })),
      ...trace.checks.filter((k) => k.journey === j.id).map((check) => ({ t: check.t, check })),
    ].sort((a, b) => a.t - b.t);
    const lastCited = events.findLastIndex((e) => e.check && cited.has(e.check.id));
    if (lastCited < 0) { notes.push(`${j.id}: no cited checks; not compiled`); continue; }

    const body: string[] = [];
    if (j.resetData) body.push("await resetApp();");
    body.push(`await page.goto(${str(j.startPath)});`);
    for (const [i, e] of events.entries()) {
      if (i > lastCited) { if (e.step && isAction(e.step)) notes.push(`${j.id}: dropped step ${e.step.n} (${e.step.action}) after the last cited check`); continue; }
      if (e.step) body.push(...action(e.step, j.id, notes));
      else if (e.check && cited.has(e.check.id)) body.push(...assertion(e.check, j.id, notes));
      else if (e.check) notes.push(`${j.id}: dropped ${e.check.id} (not cited by any verdict${e.check.passed ? "" : ", failed"})`);
    }
    const claims = [...new Set(trace.results.filter((r) => r.journey === j.id).map((r) => r.claimId))];
    tests.push({
      journey: j,
      code: [
        `// ${j.id}: ${claims.map((id) => `${id} ${trace.claims.find((c) => c.id === id)?.then.what ?? ""}`).join(" · ")}`,
        `test(${str(`${j.id} · ${j.name}`)}, async ({ page${j.resetData ? ", resetApp" : ""} }) => {`,
        ...body.map((l) => `  ${l}`),
        "});",
      ].join("\n"),
    });
  }

  // A journey that keeps the app's data sees what the journey before it left behind, so the two form a
  // chain that runs in order (and the later one is skipped if the earlier fails). A journey that resets
  // the data starts a new chain and runs regardless of what happened before.
  const chains: (typeof tests)[] = [];
  for (const t of tests) {
    if (t.journey.resetData || !chains.length) chains.push([t]);
    else chains.at(-1)!.push(t);
  }
  const indent = (s: string) => s.split("\n").map((l) => (l ? `  ${l}` : l)).join("\n");
  const blocks = chains.map((chain) => {
    if (chain.length === 1) return chain[0].code;
    const ids = chain.map((t) => t.journey.id);
    notes.push(`chain: ${ids.join(" → ")} (${ids.slice(1).join(", ")} keep the data ${ids[0]} leaves, so they run in order)`);
    return `// ${ids.slice(1).join(", ")} start from the data ${ids[0]} leaves behind, so this chain runs in order.\ntest.describe.serial(${str(ids.join(" → "))}, () => {\n${chain.map((t) => indent(t.code)).join("\n\n")}\n});`;
  });

  const spec = [
    `// Compiled by Groundtruth from run ${runId} (${trace.pr} at ${trace.sha.slice(0, 7)}${trace.title ? `, "${trace.title}"` : ""}).`,
    "// Regenerate instead of editing: npm run compile -- <run>",
    "// Checks are soft (a failed check is recorded and the journey continues); actions are hard (if one",
    "// can't be done, the rest of the journey can't be reached).",
    `import { check, expect, test } from "./groundtruth";`,
    "",
    blocks.join("\n\n"),
    "",
  ].join("\n");

  // What each check's banner in the replay video says: the claim, and the assertion as Playwright code.
  const banners: Record<string, { title: string; code: string }> = {};
  for (const k of trace.checks.filter((k) => cited.has(k.id))) {
    const claim = trace.claims.find((c) => c.id === k.claimIds[0]);
    const target = k.assert === "url" ? undefined : k.locator ? loc(k.locator, k.id) : undefined;
    banners[k.id] = { title: `${k.claimIds.map((id) => id.toUpperCase()).join(", ")} · ${claim?.then.what ?? k.assert}`, code: assertionCode(k.assert, target, k.expected) };
  }

  return {
    files: {
      "journeys.spec.ts": spec, "checks.json": JSON.stringify(banners, null, 2),
      "groundtruth.ts": template("groundtruth.ts"), "overlay.js": template("overlay.js"), "playwright.config.ts": template("playwright.config.ts"),
    },
    notes,
  };
}

const isAction = (s: Step) => ["click", "type", "select", "press", "navigate", "back", "wait_for"].includes(s.action);

function action(s: Step, journey: string, notes: string[]): string[] {
  if (!s.ok) { notes.push(`${journey}: dropped step ${s.n} (${s.action} failed during exploration: ${s.note ?? ""})`); return []; }
  if (s.note?.includes("several elements")) notes.push(`${journey}: step ${s.n} uses a locator that matched several elements during exploration; replay may fail`);
  const el = () => loc(s.locator, `${journey} step ${s.n}`);
  switch (s.action) {
    case "click": return [`await ${el()}.click();`];
    case "type": return [`await ${el()}.fill(${str(s.value ?? "")});`];
    case "select": return [`await ${el()}.selectOption(${str(s.value ?? "")});`];
    case "press": return [`await page.keyboard.press(${str(s.value ?? "")});`];
    case "navigate": return [`await page.goto(${str(s.value ?? "/")});`];
    case "back": return ["await page.goBack();"];
    case "wait_for": return [`await expect(page.getByText(${str(s.value ?? "")}).first()).toBeVisible();`];
    default: return [];   // screenshots and other observation-only steps
  }
}

// One cited check → one check() call: a named step holding the matching (soft) assertion, marked in the
// replay video with a box around the element and a ✓/✗ banner.
function assertion(k: Check, journey: string, notes: string[]): string[] {
  if (k.assert === "request_sent" || k.assert === "request_not_sent") {
    notes.push(`${journey}: ${k.id} (${k.assert}) needs request tracking in the helpers; not compiled yet`);
    return [`// ${k.id}: ${k.assert} ${k.expected ?? ""} (not compiled yet)`];
  }
  const target = k.assert === "url" ? "null" : loc(k.locator, `${journey} ${k.id}`);
  const x = str(k.expected ?? "");
  const why = str(`${k.claimIds.join(", ")}: ${k.assert.replace(/_/g, " ")}${k.expected ? ` "${k.expected}"` : ""}`);
  const soft = (matcher: string) => `expect.soft(${target}, ${why}).${matcher}`;
  const expr = (() => {
    switch (k.assert) {
      case "visible": return soft("toBeVisible()");
      case "hidden": return soft("toBeHidden()");
      case "contains_text": return soft(`toContainText(${x})`);
      case "has_text": return soft(`toHaveText(${x})`);
      case "count": return soft(`toHaveCount(${Number(k.expected)})`);
      case "value": return soft(`toHaveValue(${x})`);
      case "disabled": return soft("toBeDisabled()");
      case "enabled": return soft("toBeEnabled()");
      case "checked": return soft("toBeChecked()");
      case "unchecked": return soft("toBeChecked({ checked: false })");
      case "expanded": return soft(`toHaveAttribute("aria-expanded", "true")`);
      case "collapsed": return soft(`toHaveAttribute("aria-expanded", "false")`);
      case "selected": return soft(`toHaveAttribute("aria-selected", "true")`);
      case "invalid": return `expect.poll(() => ${target}.evaluate((e) => (e as HTMLInputElement).validity.valid), ${why}).toBe(false)`;
      case "url": return `expect.soft(page, ${why}).toHaveURL((u) => u.pathname + u.search === ${x})`;
    }
  })();
  if (/\(not unique\)/.test(k.locator ?? "")) notes.push(`${journey}: ${k.id} locator wasn't unique during exploration`);
  if (k.locator && /getByText\('.{60,}'\)/.test(k.locator)) notes.push(`${journey}: ${k.id} locator pins a long text (${k.locator.slice(0, 60)}…); brittle if the content changes`);
  const title = str(`${k.id} · ${k.claimIds.join(", ")} · ${k.assert.replace(/_/g, " ")}${k.expected ? ` "${k.expected}"` : ""}`);
  return [`await check(page, ${title}, ${target},`, `  () => ${expr});`];
}

// Recorded locators come from pages the PR controls, so they're parsed, never pasted. The parser accepts
// only chains of Playwright locator methods with literal arguments, decodes every string, and re-emits
// the code from what it parsed: text that tries to break out of a string can't reach the script.
const LOCATOR_METHODS = new Set(["getByTestId", "getByRole", "getByText", "getByLabel", "getByPlaceholder", "getByAltText", "getByTitle", "locator", "first", "last", "nth", "filter", "and", "or"]);

export function loc(recorded: string | undefined, where: string): string {
  if (!recorded) throw new Error(`${where}: no locator recorded`);
  try {
    const p = new LocatorParser(recorded.replace(/ \(not unique\)$/, ""));
    const chain = p.chain();
    p.end();
    return `page.${chain}`;
  } catch (err) {
    throw new Error(`${where}: refusing a locator that isn't a plain Playwright locator (${(err as Error).message}): ${recorded}`);
  }
}

class LocatorParser {
  private i = 0;
  constructor(private s: string) {}

  // call ("." call)*  →  re-emitted
  chain(): string {
    const calls = [this.call()];
    while (this.peek() === ".") { this.i++; calls.push(this.call()); }
    return calls.join(".");
  }

  end() { this.ws(); if (this.i !== this.s.length) throw new Error(`unexpected "${this.s.slice(this.i, this.i + 10)}"`); }

  private call(): string {
    this.ws();
    const name = /^[A-Za-z]+/.exec(this.s.slice(this.i))?.[0];
    if (!name || !LOCATOR_METHODS.has(name)) throw new Error(`method "${name ?? this.s[this.i]}" not allowed`);
    this.i += name.length;
    this.expect("(");
    const args: string[] = [];
    while (this.peek() !== ")") {
      args.push(this.value());
      if (this.peek() === ",") this.i++;
      else break;
    }
    this.expect(")");
    return `${name}(${args.join(", ")})`;
  }

  private value(): string {
    const c = this.peek();
    if (c === "'" || c === '"') return JSON.stringify(this.string());
    if (c === "/") return this.regex();
    if (c === "{") return this.object();
    const word = /^(true|false|-?\d+(\.\d+)?)/.exec(this.s.slice(this.i))?.[0];
    if (word) { this.i += word.length; return word; }
    if (/^[A-Za-z]/.test(this.s.slice(this.i))) return `page.${this.chain()}`;   // e.g. filter({ has: getByRole('img') })
    throw new Error(`unexpected "${c}"`);
  }

  private string(): string {
    const quote = this.s[this.i++];
    let out = "";
    for (;;) {
      const c = this.s[this.i++];
      if (c === undefined || c === "\n") throw new Error("unterminated string");
      if (c === quote) return out;
      if (c !== "\\") { out += c; continue; }
      const e = this.s[this.i++];
      const simple: Record<string, string> = { "\\": "\\", "'": "'", '"': '"', n: "\n", t: "\t", r: "\r", "/": "/" };
      if (e in simple) out += simple[e];
      else if (e === "u" && /^[0-9a-fA-F]{4}/.test(this.s.slice(this.i))) { out += String.fromCharCode(parseInt(this.s.slice(this.i, this.i + 4), 16)); this.i += 4; }
      else if (e === "x" && /^[0-9a-fA-F]{2}/.test(this.s.slice(this.i))) { out += String.fromCharCode(parseInt(this.s.slice(this.i, this.i + 2), 16)); this.i += 2; }
      else throw new Error(`escape \\${e} not allowed`);
    }
  }

  // /source/flags → new RegExp("source", "flags"), so the source is a plain string in the output.
  private regex(): string {
    this.i++;
    let source = "";
    let inClass = false;
    for (;;) {
      const c = this.s[this.i++];
      if (c === undefined || c === "\n") throw new Error("unterminated regex");
      if (c === "\\") { source += c + (this.s[this.i++] ?? ""); continue; }
      if (c === "[") inClass = true;
      if (c === "]") inClass = false;
      if (c === "/" && !inClass) break;
      source += c;
    }
    const flags = /^[dgimsuvy]*/.exec(this.s.slice(this.i))![0];
    this.i += flags.length;
    new RegExp(source, flags);   // throws if invalid
    return `new RegExp(${JSON.stringify(source)}, ${JSON.stringify(flags)})`;
  }

  private object(): string {
    this.expect("{");
    const props: string[] = [];
    while (this.peek() !== "}") {
      const key = /^[A-Za-z]+/.exec(this.s.slice(this.i))?.[0];
      if (!key) throw new Error("object key expected");
      this.i += key.length;
      this.expect(":");
      props.push(`${key}: ${this.value()}`);
      if (this.peek() === ",") this.i++;
      else break;
    }
    this.expect("}");
    return `{ ${props.join(", ")} }`;
  }

  private ws() { while (this.s[this.i] === " ") this.i++; }
  private peek() { this.ws(); return this.s[this.i]; }
  private expect(c: string) { if (this.peek() !== c) throw new Error(`expected "${c}"`); this.i++; }
}


const str = (s: string) => JSON.stringify(s);

// The helpers and config the compiled tests run with, copied next to them (see src/compile/template/).
const template = (name: string) => readFileSync(fileURLToPath(new URL(`./template/${name}`, import.meta.url)), "utf8");
