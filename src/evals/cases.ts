import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import YAML from "yaml";

// Shape of evals/cases/*.yaml (see evals/README.md).

export type Derivable = "intent" | "discussion" | "diff-only";

export type RefClaim = {
  id: string;
  when: string;
  then: { kind: string; what: string };
  derivable: Derivable;
  source: string;
};

// A decision the evidence leaves open, with the checkbox default a good extractor should pick.
// `same_as` points at a reference claim instead of restating it. `default: any` means the evidence
// gives no basis for a guess, so only coverage is scored, not the default.
export type RefAssumption = {
  id: string;
  same_as?: string;
  when?: string;
  then?: { kind: string; what: string };
  default: boolean | "any";
  needs?: "app-context";
};

export type EvalCase = {
  id: string;
  pr: string;
  type: string;
  split: "dev" | "test"; // test cases are held out from prompt tuning
  tags?: string[];
  snapshot: { opened_at: string; head: string; commits_visible: string[] };
  expected: {
    claims: RefClaim[];
    not_testable: { text: string; why: string }[];
    regression_hints: string[];
    assumptions: RefAssumption[];
  };
};

export const CASES_DIR = "evals/cases";
export const EVIDENCE_DIR = "evals/evidence";

export function loadCases(only?: string[]): EvalCase[] {
  return readdirSync(CASES_DIR)
    .filter((f) => f.endsWith(".yaml"))
    .map((f) => YAML.parse(readFileSync(path.join(CASES_DIR, f), "utf8")) as EvalCase)
    .filter((c) => !only?.length || only.includes(c.id))
    .sort((a, b) => a.id.localeCompare(b.id));
}

export function evidencePath(caseId: string): string {
  return path.join(EVIDENCE_DIR, `${caseId}.txt`);
}

// An assumption's when/then, resolving same_as.
export function assumptionCheck(c: EvalCase, a: RefAssumption): { when: string; then: { kind: string; what: string } } {
  const claim = a.same_as ? c.expected.claims.find((r) => r.id === a.same_as) : undefined;
  if (a.same_as && !claim) throw new Error(`${c.id} ${a.id}: same_as ${a.same_as} not found`);
  return claim ?? { when: a.when!, then: a.then! };
}
