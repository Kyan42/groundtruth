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

const JUDGE_PROMPT = `You compare a claim extractor's output against a hand-written reference for the same pull request. You only decide correspondences; you don't score.

Claims and assumptions both describe browser-checkable behavior as an action (when) and one observable result (then); assumptions are the extractor's guesses about decisions the evidence leaves open. Map every extracted claim (m1, m2, ...) and every extracted assumption (s1, s2, ...) the same way:
- reference: the id of the single best-corresponding reference claim (c...) or reference assumption (a...), or of a not-testable item (nt...) if the item restates that statement, or null if nothing corresponds.
- label:
  - match: same user action and same observable result, even if worded differently or more or less specific in harmless ways.
  - partial: clearly aimed at the same reference item but materially off: the action is vague or different, it bundles several results, the result is weaker or stronger than the reference, or the kind is wrong in a way that changes what would be checked.
  - none: nothing corresponds (use this with reference null, or with an nt... id).
Several extracted items may point at the same reference item. Ignore whether an assumption is checked or unchecked; only match its content.

Judge meaning, not wording. Be strict about the observable result: an item that checks something different from the reference is not a match even if the topic is the same.`;

function renderReference(c: EvalCase): string {
  const claims = c.expected.claims.map((r) =>
    `${r.id} [${r.derivable}] when: ${r.when} | then (${r.then.kind}): ${r.then.what}`);
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
    messages: [{ role: "user", content: `${renderReference(c)}\n\n---\n\n${renderExtraction(e)}` }],
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
