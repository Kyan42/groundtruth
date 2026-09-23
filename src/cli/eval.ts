// Usage: npm run evals -- [--runs 3] [--model claude-opus-5] [--judge-model claude-opus-5-5] [--case id ...] [--split dev|test]
//        npm run evals -- --rejudge evals/runs/<dir>   (re-judge that run's saved extractions with the current judge)
// Runs claim extraction on each golden case (from its frozen evidence), has the judge map the output
// onto the reference, scores it, writes evals/runs/<timestamp>/{results.json,report.md}, and appends
// a line to evals/runs/log.csv.
import { createHash } from "node:crypto";
import { appendFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { parseArgs } from "node:util";
import Anthropic from "@anthropic-ai/sdk";
import { DEFAULT_EXTRACT_MODEL, type Extraction, extractClaims, isGrounded, SYSTEM_PROMPT } from "../extract.js";
import { assumptionCheck, type EvalCase, evidencePath, loadCases } from "../evals/cases.js";
import { DEFAULT_JUDGE_MODEL, JUDGE_PROMPT, type Judgment, judge } from "../evals/judge.js";
import { type CaseScore, type Ratio, pct, pool, scoreRun } from "../evals/score.js";

const { values } = parseArgs({
  options: {
    runs: { type: "string", default: "3" },
    model: { type: "string", default: DEFAULT_EXTRACT_MODEL },
    "judge-model": { type: "string", default: DEFAULT_JUDGE_MODEL },
    case: { type: "string", multiple: true },
    split: { type: "string" },
    concurrency: { type: "string", default: "4" },
    rejudge: { type: "string" },
  },
});
const judgeModel = values["judge-model"]!;

// $ per million tokens (input, output). Output includes thinking tokens.
const PRICES: Record<string, [number, number]> = {
  "claude-opus-5-5": [4, 20], "claude-opus-5": [5, 25], "claude-sonnet-5": [2, 10], "claude-haiku-4-5": [1, 5],
};
type Usage = { input_tokens: number; output_tokens: number };
const cost = (m: string, u: Usage) => {
  const [i, o] = PRICES[m] ?? [NaN, NaN];
  return (u.input_tokens * i + u.output_tokens * o) / 1e6;
};
const hash = (s: string) => createHash("sha256").update(s).digest("hex").slice(0, 8);

type RunResult = {
  caseId: string;
  run: number;
  extraction?: Extraction;
  overCap?: number;
  judgment?: Judgment;
  score?: CaseScore;
  extractUsage?: Usage;
  judgeUsage?: Usage;
  extractCost: number;
  judgeCost: number;
  error?: string;
};

// In rejudge mode, reuse a previous run's extractions (same model, prompt and runs).
const previous = values.rejudge
  ? (JSON.parse(readFileSync(path.join(values.rejudge, "results.json"), "utf8")) as {
      model: string; runs: number; extractPrompt?: string; results: RunResult[];
    })
  : undefined;
const model = previous?.model ?? values.model!;
const runs = previous?.runs ?? Number(values.runs);
const extractPrompt = previous ? (previous.extractPrompt ?? "unknown") : hash(SYSTEM_PROMPT);

const client = new Anthropic();
const cases = loadCases(values.case).filter((c) => !values.split || c.split === values.split);
const jobs = cases.flatMap((c) => Array.from({ length: runs }, (_, i) => ({ c, run: i + 1 })));
console.log(`${cases.length} cases x ${runs} runs, ` +
  `${previous ? `re-judging extractions from ${values.rejudge}` : `extract with ${model}`}, judge with ${judgeModel}`);

async function runJob({ c, run }: { c: EvalCase; run: number }): Promise<RunResult> {
  const evidence = readFileSync(evidencePath(c.id), "utf8");
  const result: RunResult = { caseId: c.id, run, extractCost: 0, judgeCost: 0 };
  try {
    const prior = previous?.results.find((r) => r.caseId === c.id && r.run === run);
    if (previous) {
      if (!prior?.extraction) throw new Error("no saved extraction for this case/run");
      Object.assign(result, { extraction: prior.extraction, overCap: prior.overCap, extractUsage: prior.extractUsage,
        extractCost: prior.extractCost ?? NaN });
    } else {
      const ex = await extractClaims(evidence, { model, client });
      Object.assign(result, { extraction: ex.extraction, overCap: ex.overCap, extractUsage: ex.usage,
        extractCost: cost(model, ex.usage) });
    }
    const jd = await judge(c, result.extraction!, evidence, { model: judgeModel, client });
    result.judgment = jd.judgment;
    result.judgeUsage = jd.usage;
    result.judgeCost = cost(judgeModel, jd.usage);
    result.score = scoreRun(c, result.extraction!, jd.judgment, evidence);
    const s = result.score;
    console.log(`  ${c.id} #${run}: ${s.claims} claims, ${s.assumptions} assumptions, ` +
      `precision ${pct(s.precision)}, recall ${pct(s.intentRecall)}`);
  } catch (err) {
    console.error(`  ${c.id} #${run}: ERROR ${err instanceof Error ? err.message : err}`);
    result.error = String(err);
  }
  return result;
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

// ---------- metrics ----------

const ok = results.filter((r) => r.score);
const splits = (["dev", "test"] as const).filter((s) => cases.some((c) => c.split === s));
const splitOf = new Map(cases.map((c) => [c.id, c.split]));
const ratioValue = (r: Ratio) => (r.den === 0 ? NaN : r.num / r.den);
const mean = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : NaN);

// Per split: pool the metric across the split's cases within each run.
function perRun(split: string, pick: (s: CaseScore) => Ratio | number, isRatio: boolean): number[] {
  return Array.from({ length: runs }, (_, i) => {
    const scores = ok.filter((r) => r.run === i + 1 && splitOf.get(r.caseId) === split).map((r) => pick(r.score!));
    return isRatio ? ratioValue(pool(scores as Ratio[])) : (scores as number[]).reduce((a, b) => a + b, 0);
  }).filter((v) => !Number.isNaN(v));
}

function spread(xs: number[], fmt: (n: number) => string): string {
  if (!xs.length) return "n/a";
  const lo = Math.min(...xs), hi = Math.max(...xs);
  return lo === hi ? fmt(mean(xs)) : `${fmt(mean(xs))} (${fmt(lo)}–${fmt(hi)})`;
}
const asPct = (n: number) => `${Math.round(n * 100)}%`;
const usd = (n: number) => (Number.isNaN(n) ? "n/a" : `$${n.toFixed(3)}`);

function summaryRow(label: string, pick: (s: CaseScore) => Ratio | number, isRatio: boolean): string {
  return `| ${label} | ${splits.map((s) => spread(perRun(s, pick, isRatio), isRatio ? asPct : (n) => n.toFixed(1))).join(" | ")} |`;
}
// Mean cost per extraction (= per PR) and per judging call, by split.
function costRow(label: string, pick: (r: RunResult) => number): string {
  return `| ${label} | ${splits.map((s) => usd(mean(ok.filter((r) => splitOf.get(r.caseId) === s).map(pick)))).join(" | ")} |`;
}

const extractTotal = results.reduce((s, r) => s + r.extractCost, 0);
const judgeTotal = results.reduce((s, r) => s + r.judgeCost, 0);
const spentNow = previous ? judgeTotal : extractTotal + judgeTotal;

const summary = [
  `| Metric | ${splits.map((s) => `${s} (${cases.filter((c) => c.split === s).length} cases)`).join(" | ")} |`,
  `|---|${splits.map(() => "---").join("|")}|`,
  summaryRow("**Precision**", (s) => s.precision, true),
  summaryRow("Intent recall", (s) => s.intentRecall, true),
  summaryRow("Lures taken (count)", (s) => s.lures, false),
  summaryRow("Unmatched claims (count, adjudicate)", (s) => s.unmatched, false),
  summaryRow("Duplicate claims (count)", (s) => s.duplicates, false),
  summaryRow("Ungrounded sources (count)", (s) => s.ungrounded, false),
  summaryRow("Assumption coverage", (s) => s.assumptionCoverage, true),
  summaryRow("App-context coverage", (s) => s.appContextCoverage, true),
  summaryRow("Default accuracy", (s) => s.defaultAccuracy, true),
  summaryRow("Off-topic assumptions (count)", (s) => s.offTopicAssumptions, false),
  costRow("**Extraction cost per PR**", (r) => r.extractCost),
  costRow("Judge cost per PR (eval only)", (r) => r.judgeCost),
];

// ---------- report ----------

const cell = (s: string) => s.replace(/\|/g, "\\|").replace(/\n/g, " ");
const lines: string[] = [
  `# Eval run ${new Date().toISOString()}${previous ? ` (re-judge of ${values.rejudge})` : ""}`,
  "",
  `Extractor: \`${model}\` (prompt ${extractPrompt}) · Judge: \`${judgeModel}\` (prompt ${hash(JUDGE_PROMPT)}) · ` +
    `${cases.length} cases × ${runs} runs`,
  "",
  `Cost: extraction ${usd(extractTotal)}${previous ? " (from the original run)" : ""} + judge ${usd(judgeTotal)}` +
    ` · spent by this run ${usd(spentNow)}` +
    (results.length - ok.length ? ` · **${results.length - ok.length} runs failed**` : ""),
  "",
  "## Summary",
  "",
  "Pooled across cases per run; mean over runs (min–max). Partial matches count half. " +
    "Test cases are held out from prompt tuning, so their details are only in results.json.",
  "",
  ...summary,
  "",
];

for (const c of cases) {
  const rows = results.filter((r) => r.caseId === c.id);
  lines.push(`## ${c.id} (${c.type}, ${c.split})`, "", `${c.pr} · ${c.expected.claims.length} reference claims, ` +
    `${c.expected.claims.filter((r) => r.derivable === "intent").length} intent, ${c.expected.assumptions.length} assumptions`, "",
    "| Run | Claims | Precision | Intent recall | Lures | Unmatched | Dupes | Ungrounded | Assumptions | Coverage | Defaults | App-context | Cost |",
    "|---|---|---|---|---|---|---|---|---|---|---|---|---|");
  for (const r of rows) {
    const s = r.score;
    lines.push(s
      ? `| ${r.run} | ${s.claims} | ${pct(s.precision)} | ${pct(s.intentRecall)} | ${s.lures} | ${s.unmatched} | ${s.duplicates} | ${s.ungrounded} | ` +
        `${s.assumptions}${r.overCap ? ` (+${r.overCap} over cap)` : ""} | ${pct(s.assumptionCoverage)} | ${pct(s.defaultAccuracy)} | ` +
        `${pct(s.appContextCoverage)} | ${usd(r.extractCost)} + ${usd(r.judgeCost)} |`
      : `| ${r.run} | error: ${cell(r.error ?? "")} ||||||||||||`);
  }
  lines.push("");
  if (c.split === "test") continue; // held out: scores only
  const evidence = readFileSync(evidencePath(c.id), "utf8");
  for (const r of rows.filter((x) => x.extraction && x.judgment)) {
    const e = r.extraction!, j = r.judgment!;
    lines.push(`<details><summary>Run ${r.run}: ${e.claims.length} claims, ${e.assumptions.length} assumptions</summary>`, "",
      "| # | Claim | Ref | Label | Grounded | Judge |", "|---|---|---|---|---|---|");
    e.claims.forEach((m, i) => {
      const v = j.claims.find((x) => x.item === `m${i + 1}`);
      lines.push(`| m${i + 1} | ${cell(`${m.when} → (${m.then.kind}) ${m.then.what}`)} | ${v?.reference ?? "—"} | ${v?.label ?? "—"} | ` +
        `${isGrounded(m.source, evidence) ? "yes" : `**no**: "${cell(m.source)}"`} | ${cell(v?.reason ?? "")} |`);
    });
    if (e.assumptions.length) {
      lines.push("", "| # | Assumption | Ref | Label | Judge |", "|---|---|---|---|---|");
      e.assumptions.forEach((a, i) => {
        const v = j.assumptions.find((x) => x.item === `s${i + 1}`);
        lines.push(`| s${i + 1} | ${a.checked ? "☑" : "☐"} ${cell(`${a.when} → (${a.then.kind}) ${a.then.what}`)} — _${cell(a.reason)}_ | ` +
          `${v?.reference ?? "—"} | ${v?.label ?? "—"} | ${cell(v?.reason ?? "")} |`);
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
  lines.push("Reference:", "",
    ...c.expected.claims.map((r) => `- ${r.id} [${r.derivable}] ${r.when} → (${r.then.kind}) ${r.then.what}`),
    ...c.expected.assumptions.map((a) => {
      const chk = assumptionCheck(c, a);
      const box = a.default === "any" ? "☐/☑" : a.default ? "☑" : "☐";
      return `- ${a.id} ${box}${a.same_as ? ` (= ${a.same_as})` : ""} ${chk.when} → (${chk.then.kind}) ${chk.then.what}${a.needs ? ` [${a.needs}]` : ""}`;
    }), "");
}

const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const outDir = path.join("evals/runs", stamp);
mkdirSync(outDir, { recursive: true });
writeFileSync(path.join(outDir, "results.json"), JSON.stringify({
  model, judgeModel, runs, extractPrompt, judgePrompt: hash(JUDGE_PROMPT), rejudgeOf: values.rejudge, results,
}, null, 2));
writeFileSync(path.join(outDir, "report.md"), lines.join("\n"));

// One line per run, to track quality and cost over time.
const logPath = "evals/runs/log.csv";
if (!existsSync(logPath)) {
  appendFileSync(logPath, "run,mode,extract_model,extract_prompt,judge_model,judge_prompt,runs,cases," +
    "dev_precision,dev_recall,test_precision,test_recall,extract_usd_per_pr,judge_usd_per_pr,spent_usd\n");
}
const splitMean = (s: string, pick: (x: CaseScore) => Ratio) => {
  const v = mean(perRun(s, pick, true));
  return Number.isNaN(v) ? "" : v.toFixed(3);
};
appendFileSync(logPath, [
  stamp, previous ? "rejudge" : "full", model, extractPrompt, judgeModel, hash(JUDGE_PROMPT), runs, cases.length,
  splitMean("dev", (s) => s.precision), splitMean("dev", (s) => s.intentRecall),
  splitMean("test", (s) => s.precision), splitMean("test", (s) => s.intentRecall),
  mean(ok.map((r) => r.extractCost)).toFixed(4), mean(ok.map((r) => r.judgeCost)).toFixed(4), spentNow.toFixed(2),
].join(",") + "\n");

console.log(`\n${summary.join("\n")}\n\nReport: ${path.join(outDir, "report.md")} · spent ${usd(spentNow)}`);
