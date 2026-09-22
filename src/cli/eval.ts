// Usage: npm run evals -- [--runs 3] [--model claude-opus-5] [--judge-model claude-opus-5-5] [--case id ...] [--split dev|test]
// Runs claim extraction on each golden case (from its frozen evidence), has the judge map the output
// onto the reference, scores it, and writes evals/runs/<timestamp>/{results.json,report.md}.
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { parseArgs } from "node:util";
import Anthropic from "@anthropic-ai/sdk";
import { DEFAULT_EXTRACT_MODEL, type Extraction, extractClaims, isGrounded } from "../extract.js";
import { type EvalCase, evidencePath, loadCases, questionText } from "../evals/cases.js";
import { DEFAULT_JUDGE_MODEL, type Judgment, judge } from "../evals/judge.js";
import { type CaseScore, type Ratio, pct, pool, scoreRun } from "../evals/score.js";

const { values } = parseArgs({
  options: {
    runs: { type: "string", default: "3" },
    model: { type: "string", default: DEFAULT_EXTRACT_MODEL },
    "judge-model": { type: "string", default: DEFAULT_JUDGE_MODEL },
    case: { type: "string", multiple: true },
    split: { type: "string" },
    concurrency: { type: "string", default: "4" },
  },
});
const runs = Number(values.runs);
const model = values.model!;
const judgeModel = values["judge-model"]!;

// $ per million tokens (input, output), for the cost estimate only.
const PRICES: Record<string, [number, number]> = {
  "claude-opus-5-5": [4, 20], "claude-opus-5": [5, 25], "claude-sonnet-5": [2, 10], "claude-haiku-4-5": [1, 5],
};
type Usage = { input_tokens: number; output_tokens: number };
const cost = (m: string, u: Usage) => {
  const [i, o] = PRICES[m] ?? [0, 0];
  return (u.input_tokens * i + u.output_tokens * o) / 1e6;
};

type RunResult = {
  caseId: string;
  run: number;
  extraction?: Extraction;
  judgment?: Judgment;
  score?: CaseScore;
  cost: number;
  error?: string;
};

const client = new Anthropic();
const cases = loadCases(values.case).filter((c) => !values.split || c.split === values.split);
const jobs = cases.flatMap((c) => Array.from({ length: runs }, (_, i) => ({ c, run: i + 1 })));
console.log(`${cases.length} cases x ${runs} runs, extract with ${model}, judge with ${judgeModel}`);

async function runJob({ c, run }: { c: EvalCase; run: number }): Promise<RunResult> {
  const evidence = readFileSync(evidencePath(c.id), "utf8");
  let spent = 0;
  try {
    const ex = await extractClaims(evidence, { model, client });
    spent += cost(model, ex.usage);
    const jd = await judge(c, ex.extraction, { model: judgeModel, client });
    spent += cost(judgeModel, jd.usage);
    const score = scoreRun(c, ex.extraction, jd.judgment, evidence);
    console.log(`  ${c.id} #${run}: ${score.claims} claims, recall ${pct(score.intentRecall)}, precision ${pct(score.precision)}`);
    return { caseId: c.id, run, extraction: ex.extraction, judgment: jd.judgment, score, cost: spent };
  } catch (err) {
    console.error(`  ${c.id} #${run}: ERROR ${err instanceof Error ? err.message : err}`);
    return { caseId: c.id, run, cost: spent, error: String(err) };
  }
}

async function runAll(concurrency: number): Promise<RunResult[]> {
  const results: RunResult[] = [];
  let next = 0;
  await Promise.all(Array.from({ length: concurrency }, async () => {
    while (next < jobs.length) results.push(await runJob(jobs[next++]));
  }));
  return results.sort((a, b) => a.caseId.localeCompare(b.caseId) || a.run - b.run);
}

const results = await runAll(Number(values.concurrency));

// ---------- report ----------

const ok = results.filter((r) => r.score);
const totalCost = results.reduce((s, r) => s + r.cost, 0);

function spread(values: number[], fmt: (n: number) => string): string {
  if (!values.length) return "n/a";
  const mean = values.reduce((a, b) => a + b, 0) / values.length;
  const lo = Math.min(...values), hi = Math.max(...values);
  return lo === hi ? fmt(mean) : `${fmt(mean)} (${fmt(lo)}–${fmt(hi)})`;
}
const asPct = (n: number) => `${Math.round(n * 100)}%`;
const ratioValue = (r: Ratio) => (r.den === 0 ? NaN : r.num / r.den);

// Pool each metric across a split's cases within a run, then summarize across runs.
const splits = (["dev", "test"] as const).filter((s) => cases.some((c) => c.split === s));
const splitOf = new Map(cases.map((c) => [c.id, c.split]));
function summaryRow(label: string, pick: (s: CaseScore) => Ratio | number, isRatio: boolean): string {
  const cells = splits.map((split) => {
    const perRun = Array.from({ length: runs }, (_, i) => {
      const scores = ok.filter((r) => r.run === i + 1 && splitOf.get(r.caseId) === split).map((r) => pick(r.score!));
      return isRatio ? ratioValue(pool(scores as Ratio[])) : (scores as number[]).reduce((a, b) => a + b, 0);
    }).filter((v) => !Number.isNaN(v));
    return spread(perRun, isRatio ? asPct : (n) => n.toFixed(1));
  });
  return `| ${label} | ${cells.join(" | ")} |`;
}

const cell = (s: string) => s.replace(/\|/g, "\\|").replace(/\n/g, " ");
const lines: string[] = [
  `# Eval run ${new Date().toISOString()}`,
  "",
  `Extractor: \`${model}\` · Judge: \`${judgeModel}\` · ${cases.length} cases × ${runs} runs · est. cost $${totalCost.toFixed(2)}` +
    (results.length - ok.length ? ` · **${results.length - ok.length} runs failed**` : ""),
  "",
  "## Summary",
  "",
  "Pooled across cases per run; mean over runs (min–max). Partial matches count half. " +
    "Test cases are held out from prompt tuning, so their claim-level details are only in results.json.",
  "",
  `| Metric | ${splits.map((s) => `${s} (${cases.filter((c) => c.split === s).length} cases)`).join(" | ")} |`,
  `|---|${splits.map(() => "---").join("|")}|`,
  summaryRow("Precision", (s) => s.precision, true),
  summaryRow("Intent recall", (s) => s.intentRecall, true),
  summaryRow("Lures taken (count)", (s) => s.lures, false),
  summaryRow("Unmatched claims (count, adjudicate)", (s) => s.unmatched, false),
  summaryRow("Question coverage", (s) => s.questionCoverage, true),
  summaryRow("App-context question coverage", (s) => s.appContextCoverage, true),
  summaryRow("Ungrounded sources (count)", (s) => s.ungrounded, false),
  "",
];

for (const c of cases) {
  const rows = results.filter((r) => r.caseId === c.id);
  lines.push(`## ${c.id} (${c.type}, ${c.split})`, "", `${c.pr} · ${c.expected.claims.length} reference claims, ` +
    `${c.expected.claims.filter((r) => r.derivable === "intent").length} intent`, "",
    "| Run | Claims | Precision | Intent recall | Lures | Unmatched | Questions | App-context | Ungrounded |",
    "|---|---|---|---|---|---|---|---|---|");
  for (const r of rows) {
    const s = r.score;
    lines.push(s
      ? `| ${r.run} | ${s.claims} | ${pct(s.precision)} | ${pct(s.intentRecall)} | ${s.lures} | ${s.unmatched} | ${pct(s.questionCoverage)} | ${pct(s.appContextCoverage)} | ${s.ungrounded} |`
      : `| ${r.run} | error: ${cell(r.error ?? "")} ||||||||`);
  }
  lines.push("");
  if (c.split === "test") continue; // held out: scores only
  const evidence = readFileSync(evidencePath(c.id), "utf8");
  for (const r of rows.filter((x) => x.extraction)) {
    const e = r.extraction!, j = r.judgment!;
    lines.push(`<details><summary>Run ${r.run}: ${e.claims.length} claims, ${e.questions.length} questions</summary>`, "",
      "| # | Claim | Ref | Label | Grounded | Judge |", "|---|---|---|---|---|---|");
    e.claims.forEach((m, i) => {
      const v = j.claims.find((x) => x.model_claim === `m${i + 1}`);
      lines.push(`| m${i + 1} | ${cell(`${m.when} → (${m.then.kind}) ${m.then.what}`)} | ${v?.reference ?? "—"} | ${v?.label ?? "—"} | ` +
        `${isGrounded(m.source, evidence) ? "yes" : `**no**: "${cell(m.source)}"`} | ${cell(v?.reason ?? "")} |`);
    });
    if (e.questions.length) {
      lines.push("", "Questions:", "");
      e.questions.forEach((q, i) => {
        const v = j.questions.find((x) => x.model_question === `q${i + 1}`);
        lines.push(`- q${i + 1}: ${q} → covers ${v?.covers.length ? v.covers.join(", ") : "nothing"}`);
      });
    }
    if (e.not_testable.length) {
      lines.push("", "Not testable:", "", ...e.not_testable.map((n) => `- ${n.text} (${n.why})`));
    }
    if (e.regression_hints.length) {
      lines.push("", "Regression hints:", "", ...e.regression_hints.map((h) => `- ${h}`));
    }
    lines.push("", "</details>", "");
  }
  lines.push("Reference:", "", ...c.expected.claims.map((r) => `- ${r.id} [${r.derivable}] ${r.when} → (${r.then.kind}) ${r.then.what}`),
    ...c.expected.questions.map((q, i) => `- rq${i + 1}: ${questionText(q)}`), "");
}

const outDir = path.join("evals/runs", new Date().toISOString().replace(/[:.]/g, "-"));
mkdirSync(outDir, { recursive: true });
writeFileSync(path.join(outDir, "results.json"), JSON.stringify({ model, judgeModel, runs, results }, null, 2));
writeFileSync(path.join(outDir, "report.md"), lines.join("\n"));
console.log(`\n${lines.slice(6, 17).join("\n")}\n\nReport: ${path.join(outDir, "report.md")} · est. cost $${totalCost.toFixed(2)}`);
