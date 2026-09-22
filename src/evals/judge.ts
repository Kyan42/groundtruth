import Anthropic from "@anthropic-ai/sdk";
import { betaZodOutputFormat } from "@anthropic-ai/sdk/helpers/beta/zod";
import * as z from "zod/v4";
import type { Extraction } from "../extract.js";
import { type EvalCase, questionText } from "./cases.js";

// LLM judge: maps each extracted claim and question onto the reference case.
// It only matches; all scoring happens in score.ts.

export const DEFAULT_JUDGE_MODEL = "claude-opus-5-5";

const JudgmentSchema = z.object({
  claims: z.array(z.object({
    model_claim: z.string().describe("Id of the extracted claim, e.g. m1"),
    reference: z.string().nullable().describe("Id of the best reference claim (c…) or not-testable item (nt…), or null"),
    label: z.enum(["match", "partial", "none"]),
    reason: z.string().describe("One sentence"),
  })),
  questions: z.array(z.object({
    model_question: z.string().describe("Id of the extracted question, e.g. q1"),
    covers: z.array(z.string()).describe("Ids of reference questions (rq…) and reference claims (c…) this question covers"),
    reason: z.string().describe("One sentence"),
  })),
});

export type Judgment = z.infer<typeof JudgmentSchema>;

const JUDGE_PROMPT = `You compare a claim extractor's output against a hand-written reference for the same pull request. You only decide correspondences; you don't score.

Claims describe browser-checkable behavior as an action (when) and one observable result (then). For each extracted claim (m1, m2, ...):
- reference: the id of the single best-corresponding reference claim (c...), or of a not-testable item (nt...) if the extractor turned that statement into a claim, or null if nothing corresponds.
- label:
  - match: same user action and same observable result, even if worded differently or more or less specific in harmless ways.
  - partial: clearly aimed at the same reference claim but materially off: the action is vague or different, it bundles several results, the result is weaker or stronger than the reference, or the kind is wrong in a way that changes what would be checked.
  - none: no reference claim corresponds (use this with reference null, or with an nt... id).
Several extracted claims may point at the same reference claim.

For each extracted question (q1, q2, ...), list what it covers: reference questions (rq...) asking substantially the same thing, and reference claims (c...) whose content the question asks about (e.g. asking "is it off by default?" covers a claim that it is off by default). Use an empty list if it covers nothing.

Judge meaning, not wording. Be strict about the observable result: a claim that checks something different from the reference is not a match even if the topic is the same.`;

function renderReference(c: EvalCase): string {
  const claims = c.expected.claims.map((r) =>
    `${r.id} [${r.derivable}] when: ${r.when} | then (${r.then.kind}): ${r.then.what}`);
  const nt = c.expected.not_testable.map((n, i) => `nt${i + 1}: ${n.text} (${n.why})`);
  const qs = c.expected.questions.map((q, i) => `rq${i + 1}: ${questionText(q)}`);
  return [
    `Reference claims:\n${claims.join("\n") || "(none: this PR should produce zero claims)"}`,
    `Reference not-testable statements:\n${nt.join("\n") || "(none)"}`,
    `Reference questions:\n${qs.join("\n") || "(none)"}`,
  ].join("\n\n");
}

function renderExtraction(e: Extraction): string {
  const claims = e.claims.map((m, i) => `m${i + 1} when: ${m.when} | then (${m.then.kind}): ${m.then.what}`);
  const qs = e.questions.map((q, i) => `q${i + 1}: ${q}`);
  return `Extracted claims:\n${claims.join("\n") || "(none)"}\n\nExtracted questions:\n${qs.join("\n") || "(none)"}`;
}

export async function judge(
  c: EvalCase,
  e: Extraction,
  opts: { model?: string; client?: Anthropic } = {},
): Promise<{ judgment: Judgment; usage: { input_tokens: number; output_tokens: number } }> {
  // Nothing to match: skip the call.
  if (e.claims.length === 0 && e.questions.length === 0) {
    return { judgment: { claims: [], questions: [] }, usage: { input_tokens: 0, output_tokens: 0 } };
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
