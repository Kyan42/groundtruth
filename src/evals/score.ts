import { type Extraction, isGrounded } from "../extract.js";
import type { EvalCase } from "./cases.js";
import type { Judgment } from "./judge.js";

// Metrics for one extraction run against one case. "partial" matches count half.
// Kept as counts (numerator/denominator) so they can be pooled across cases and runs.

export type Ratio = { num: number; den: number };

export type CaseScore = {
  claims: number;
  intentRecall: Ratio;        // intent reference claims found by claims
  precision: Ratio;           // extracted claims that correspond to any reference claim
  lures: number;              // claims made from not-testable statements, or any claim on a case expecting none
  unmatched: number;          // claims matching nothing: invented, or a gap in the reference (adjudicate)
  duplicates: number;         // claims or assumptions matching a reference item an earlier one already matched
  ungrounded: number;         // claims whose source quote isn't in the evidence
  assumptions: number;
  assumptionCoverage: Ratio;  // open decisions (reference assumptions, discussion/diff-only claims) raised
  appContextCoverage: Ratio;  // the subset marked needs: app-context
  defaultAccuracy: Ratio;     // matched assumptions whose checkbox default is right (where the reference has one)
  offTopicAssumptions: number; // assumptions matching nothing in the reference
};

type Verdict = Judgment["claims"][number];
const credit = (label: string) => (label === "match" ? 1 : label === "partial" ? 0.5 : 0);

export function scoreRun(c: EvalCase, e: Extraction, j: Judgment, evidenceText: string): CaseScore {
  const refClaims = c.expected.claims;
  const refAssumptions = c.expected.assumptions;
  const isRefClaim = (id: string | null | undefined) => !!id && refClaims.some((r) => r.id === id);
  const lookup = (list: Verdict[], prefix: string, n: number) =>
    Array.from({ length: n }, (_, i) => list.find((v) => v.item === `${prefix}${i + 1}`));
  const claimVerdicts = lookup(j.claims, "m", e.claims.length);
  const assumptionVerdicts = lookup(j.assumptions, "s", e.assumptions.length);

  // Best credit each reference claim received from any extracted claim.
  const best = new Map<string, number>();
  for (const v of claimVerdicts) {
    if (v && isRefClaim(v.reference)) best.set(v.reference!, Math.max(best.get(v.reference!) ?? 0, credit(v.label)));
  }
  const intent = refClaims.filter((r) => r.derivable === "intent");

  // Open decisions: discussion/diff-only claims plus standalone reference assumptions.
  // Raised if any extracted claim or assumption corresponds to one.
  const appContextIds = refAssumptions.filter((a) => a.needs === "app-context").map((a) => a.same_as ?? a.id);
  const openIds = [
    ...refClaims.filter((r) => r.derivable !== "intent").map((r) => r.id),
    ...refAssumptions.filter((a) => !a.same_as).map((a) => a.id),
  ];
  const raised = new Set(
    [...claimVerdicts, ...assumptionVerdicts].filter((v) => v && v.label !== "none" && v.reference).map((v) => v!.reference!),
  );
  const coverTargets = openIds.filter((id) => !appContextIds.includes(id));

  // The checkbox default a good extractor should pick for whatever an assumption matched.
  const expectedDefault = (ref: string): boolean | "any" => {
    if (ref.startsWith("nt")) return false;
    const a = refAssumptions.find((x) => x.id === ref || x.same_as === ref);
    if (a) return a.default;
    return isRefClaim(ref); // an actual reference claim is intended
  };
  const graded = e.assumptions
    .map((a, i) => ({ a, v: assumptionVerdicts[i] }))
    .filter(({ v }) => v?.reference && (v.label !== "none" || v.reference.startsWith("nt")))
    .map(({ a, v }) => ({ checked: a.checked, expected: expectedDefault(v!.reference!) }))
    .filter((x) => x.expected !== "any");

  // Any claim is a lure when the reference expects none (no-claims PRs, and vague ones with nothing to go on).
  const noClaimsCase = refClaims.length === 0;
  return {
    claims: e.claims.length,
    intentRecall: { num: intent.reduce((s, r) => s + (best.get(r.id) ?? 0), 0), den: intent.length },
    precision: {
      num: claimVerdicts.reduce((s, v) => s + (v && isRefClaim(v.reference) ? credit(v.label) : 0), 0),
      den: e.claims.length,
    },
    lures: noClaimsCase ? e.claims.length : claimVerdicts.filter((v) => v?.reference?.startsWith("nt")).length,
    unmatched: claimVerdicts.filter((v) => !v || (!isRefClaim(v.reference) && !v.reference?.startsWith("nt"))).length,
    // Claims, then assumptions, in order: any item hitting a reference claim or assumption already hit.
    duplicates: (() => {
      const seen = new Set<string>();
      let n = 0;
      for (const v of [...claimVerdicts, ...assumptionVerdicts]) {
        if (!v || v.label === "none" || !v.reference || v.reference.startsWith("nt")) continue;
        if (seen.has(v.reference)) n++;
        seen.add(v.reference);
      }
      return n;
    })(),
    ungrounded: e.claims.filter((m) => !isGrounded(m.source, evidenceText)).length,
    assumptions: e.assumptions.length,
    assumptionCoverage: { num: coverTargets.filter((id) => raised.has(id)).length, den: coverTargets.length },
    appContextCoverage: { num: appContextIds.filter((id) => raised.has(id)).length, den: appContextIds.length },
    defaultAccuracy: { num: graded.filter((x) => x.checked === x.expected).length, den: graded.length },
    offTopicAssumptions: assumptionVerdicts.filter((v) => !v?.reference).length,
  };
}

export function pool(ratios: Ratio[]): Ratio {
  return ratios.reduce((a, r) => ({ num: a.num + r.num, den: a.den + r.den }), { num: 0, den: 0 });
}

export function pct(r: Ratio): string {
  return r.den === 0 ? "n/a" : `${Math.round((100 * r.num) / r.den)}%`;
}
