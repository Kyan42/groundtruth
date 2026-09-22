import { type Extraction, isGrounded } from "../extract.js";
import type { EvalCase } from "./cases.js";
import type { Judgment } from "./judge.js";

// Metrics for one extraction run against one case. "partial" matches count half.
// Kept as counts (numerator/denominator) so they can be pooled across cases and runs.

export type Ratio = { num: number; den: number };

export type CaseScore = {
  claims: number;
  intentRecall: Ratio;      // intent reference claims found
  precision: Ratio;         // extracted claims that correspond to any reference claim
  lures: number;            // claims made from not-testable statements, or any claim on a no-claims case
  unmatched: number;        // claims matching nothing: invented, or a gap in the reference (adjudicate)
  questionCoverage: Ratio;  // reference questions + discussion/diff-only claims covered by questions
  appContextCoverage: Ratio; // the subset of reference questions marked needs: app-context
  ungrounded: number;       // claims whose source quote isn't in the evidence
};

const credit = (label: string) => (label === "match" ? 1 : label === "partial" ? 0.5 : 0);

export function scoreRun(c: EvalCase, e: Extraction, j: Judgment, evidenceText: string): CaseScore {
  const refClaims = c.expected.claims;
  const byModelClaim = new Map(j.claims.map((x) => [x.model_claim, x]));
  const verdicts = e.claims.map((_, i) => byModelClaim.get(`m${i + 1}`));
  const isRefClaim = (id: string | null | undefined) => !!id && refClaims.some((r) => r.id === id);

  // Best credit each reference claim received from any extracted claim.
  const best = new Map<string, number>();
  for (const v of verdicts) {
    if (v && isRefClaim(v.reference)) best.set(v.reference!, Math.max(best.get(v.reference!) ?? 0, credit(v.label)));
  }
  const intent = refClaims.filter((r) => r.derivable === "intent");

  const covered = new Set(j.questions.flatMap((q) => q.covers));
  const optionalClaims = refClaims.filter((r) => r.derivable !== "intent").map((r) => r.id);
  const refQuestionIds = c.expected.questions.map((_, i) => `rq${i + 1}`);
  const appContextIds = c.expected.questions
    .map((q, i) => (typeof q !== "string" && q.needs === "app-context" ? `rq${i + 1}` : null))
    .filter((x): x is string => x !== null);
  // An optional claim counts as covered if a question asks about it or a claim states it.
  const coverTargets = [...refQuestionIds.filter((id) => !appContextIds.includes(id)), ...optionalClaims];
  const isCovered = (id: string) => covered.has(id) || (best.get(id) ?? 0) > 0;

  const noClaimsCase = c.type === "no-claims";
  return {
    claims: e.claims.length,
    intentRecall: { num: intent.reduce((s, r) => s + (best.get(r.id) ?? 0), 0), den: intent.length },
    precision: {
      num: verdicts.reduce((s, v) => s + (v && isRefClaim(v.reference) ? credit(v.label) : 0), 0),
      den: e.claims.length,
    },
    lures: noClaimsCase ? e.claims.length : verdicts.filter((v) => v?.reference?.startsWith("nt")).length,
    unmatched: verdicts.filter((v) => !v || (!isRefClaim(v.reference) && !v.reference?.startsWith("nt"))).length,
    questionCoverage: { num: coverTargets.filter(isCovered).length, den: coverTargets.length },
    appContextCoverage: { num: appContextIds.filter((id) => covered.has(id)).length, den: appContextIds.length },
    ungrounded: e.claims.filter((m) => !isGrounded(m.source, evidenceText)).length,
  };
}

export function pool(ratios: Ratio[]): Ratio {
  return ratios.reduce((a, r) => ({ num: a.num + r.num, den: a.den + r.den }), { num: 0, den: 0 });
}

export function pct(r: Ratio): string {
  return r.den === 0 ? "n/a" : `${Math.round((100 * r.num) / r.den)}%`;
}
