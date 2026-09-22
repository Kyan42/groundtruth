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

export type RefQuestion = string | { text: string; needs?: "app-context" };

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
    questions: RefQuestion[];
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

export function questionText(q: RefQuestion): string {
  return typeof q === "string" ? q : q.text;
}
