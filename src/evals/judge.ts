import Anthropic from "@anthropic-ai/sdk";
import { betaZodOutputFormat } from "@anthropic-ai/sdk/helpers/beta/zod";
import * as z from "zod/v4";
import type { Extraction } from "../extract.js";
import type { EvalCase } from "./cases.js";

// LLM judge: maps each extracted claim and assumption onto the reference case.
// It only matches; all scoring happens in score.ts.

export const DEFAULT_JUDGE_MODEL = "claude-opus-5-5";

const Mapping = z.object({
  item: z.string().describe("Id of the extracted item, e.g. m1 or s1"),
  reference: z.string().nullable()
    .describe("Id of the best reference claim (c…), reference assumption (a…) or not-testable item (nt…), or null"),
  label: z.enum(["match", "partial", "none"]),
  reason: z.string().describe("One sentence"),
});

const JudgmentSchema = z.object({
  claims: z.array(Mapping),
  assumptions: z.array(Mapping),
});

export type Judgment = z.infer<typeof JudgmentSchema>;

export const JUDGE_PROMPT = `You compare a claim extractor's output against a hand-written reference for the same pull request. You only decide correspondences; you don't score. The PR's evidence (what the extractor read) is included for context.

Claims and assumptions both describe browser-checkable behavior as an action (when) and one observable result (then); assumptions are the extractor's guesses about decisions the evidence leaves open. Map every extracted claim (m1, m2, ...) and every extracted assumption (s1, s2, ...) the same way:
- reference: the id of the single best-corresponding reference claim (c...) or reference assumption (a...), or of a not-testable item (nt...) if the item restates that statement, or null if nothing corresponds.
- label: match, partial or none, by one test: would a browser test built from the extracted item catch the same broken implementations as one built from the reference? A claim must say what to check; it may leave out how to get there.
  - What to check (then) must be the same observable result. When the evidence specifies how the behavior shows up (e.g. buttons are "disabled", not removed; a length limit on an input field), a different observable is not the same, because it would miss an implementation that got that wrong. When the evidence states only the goal, any observable that proves the goal is the same.
  - How to get there (when) may leave out preconditions a competent tester would supply anyway because the check can't be run without them (e.g. a transfer must exist for a transfer flow to appear), and where to find a control.
  - match: same observable result, in the same situation, with any omitted setup of the harmless kind above.
  - partial: aimed at the same reference item but would catch different failures: a different observable (per the rule above); a different situation (e.g. editing an existing item instead of creating one, another page or view); omitted setup that lets the check pass even when the feature is broken (e.g. checking a toggle's off state without ever turning it on); several results bundled into one; or a result that would also hold without the change (e.g. already true before this PR; reference notes say when). The last can't apply when the action itself needs the new feature.
  - none: nothing corresponds (use this with reference null, or with an nt... id).
Several extracted items may point at the same reference item. Ignore whether an assumption is checked or unchecked; only match its content. Judge meaning, not wording.`;

function renderReference(c: EvalCase): string {
  const claims = c.expected.claims.map((r) =>
    `${r.id} [${r.derivable}] when: ${r.when} | then (${r.then.kind}): ${r.then.what}${r.note ? ` | note: ${r.note}` : ""}`);
  // Assumptions with same_as are represented by their claim.
  const assumptions = c.expected.assumptions.filter((a) => !a.same_as).map((a) =>
    `${a.id} when: ${a.when} | then (${a.then!.kind}): ${a.then!.what}`);
  const nt = c.expected.not_testable.map((n, i) => `nt${i + 1}: ${n.text} (${n.why})`);
  return [
    `Reference claims:\n${claims.join("\n") || "(none: this PR should produce zero claims)"}`,
    `Reference assumptions:\n${assumptions.join("\n") || "(none)"}`,
    `Reference not-testable statements:\n${nt.join("\n") || "(none)"}`,
  ].join("\n\n");
}

function renderExtraction(e: Extraction): string {
  const claims = e.claims.map((m, i) => `m${i + 1} when: ${m.when} | then (${m.then.kind}): ${m.then.what}`);
  const assumptions = e.assumptions.map((a, i) => `s${i + 1} when: ${a.when} | then (${a.then.kind}): ${a.then.what}`);
  return `Extracted claims:\n${claims.join("\n") || "(none)"}\n\nExtracted assumptions:\n${assumptions.join("\n") || "(none)"}`;
}

export async function judge(
  c: EvalCase,
  e: Extraction,
  evidenceText: string,
  opts: { model?: string; client?: Anthropic } = {},
): Promise<{ judgment: Judgment; usage: { input_tokens: number; output_tokens: number } }> {
  // Nothing to match: skip the call.
  if (e.claims.length === 0 && e.assumptions.length === 0) {
    return { judgment: { claims: [], assumptions: [] }, usage: { input_tokens: 0, output_tokens: 0 } };
  }
  const client = opts.client ?? new Anthropic();
  const response = await client.beta.messages.parse({
    model: opts.model ?? DEFAULT_JUDGE_MODEL,
    max_tokens: 16000,
    output_config: { effort: "high", format: betaZodOutputFormat(JudgmentSchema) },
    system: JUDGE_PROMPT,
    messages: [{
      role: "user",
      content: `<evidence>\n${evidenceText}\n</evidence>\n\n${renderReference(c)}\n\n---\n\n${renderExtraction(e)}`,
    }],
  });
  if (response.stop_reason === "refusal") {
    throw new Error(`Judge refused (${response.stop_details?.category ?? "no category"})`);
  }
  if (!response.parsed_output) {
    throw new Error(`Judge returned no parseable output (stop_reason: ${response.stop_reason})`);
  }
  return {
    judgment: response.parsed_output,
    usage: { input_tokens: response.usage.input_tokens, output_tokens: response.usage.output_tokens },
  };
}
