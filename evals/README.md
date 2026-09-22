# Evals

Golden cases for Groundtruth, one YAML file per PR in `cases/`. Candidate PRs are listed in [docs/eval-candidates.md](../docs/eval-candidates.md).

## Running

1. `npm run evals:snapshot` freezes each case's evidence, as it was when the PR was opened, into `evidence/<id>.txt`. That file is exactly what the extractor sees. Re-run it after adding a case.
2. `npm run evals` runs extraction on every case (3 runs each by default), has the judge map the output onto the reference, and writes `runs/<timestamp>/report.md` plus `results.json`. Needs `ANTHROPIC_API_KEY` in `.env`.
   Options: `--runs 1`, `--case lobsters-2132`, `--split test`, `--model claude-sonnet-5` (extractor), `--judge-model ...`.

Metrics, pooled across cases (partial matches count half):

- **Precision**: extracted claims that correspond to a reference claim. The headline metric.
- **Intent recall**: `intent` reference claims found.
- **Lures taken**: claims made from `not_testable` statements, plus any claim on a `no-claims` case.
- **Unmatched claims**: claims matching nothing. Either invented or a gap in the reference; review each one and either accept the penalty or add it to the case.
- **Question coverage**: reference questions, plus `discussion` and `diff-only` claims, covered by the extractor's questions (or claims).
- **App-context question coverage**: the same, for questions marked `needs: app-context`.
- **Ungrounded sources**: claims whose `source` quote isn't found in the evidence (checked in code, no judge).

## Case fields

- `type`: what kind of PR this is: `new-feature`, `behavior-change`, `bugfix`, `ui-only`, `no-claims`, `vague`. Assigned by whoever writes the case, and used to break down scores (e.g. "we do badly on behavior changes"). For `no-claims`, the expected output is zero claims.
- `split`: `dev` or `test`. Dev cases are the ones we look at when changing the extractor prompt. Test cases are held out: we only compare their scores, and the report hides their claim-level details (they're still in `results.json`). If a prompt change helps dev but not test, it's overfitting.
- `tags`: optional free-form labels for slicing results, e.g. `ai-assisted` when the PR says it was written with AI tools.
- `snapshot`: the evidence as it was when the PR was **opened**, which is when Groundtruth runs. Descriptions get edited, bots append to them, and commits land after opening; none of that should be visible to extraction.
- `setup`: what a tester needs (feature flags, data) to check the claims.
- `expected`: the reference output for claim extraction.
  - `claims`: each has `when` (the user action), `then` (`kind` + `what`), a `source`, and `derivable`:
    - `intent`: follows from the evidence at open. Extraction is expected to produce it.
    - `discussion`: only known from later review comments. Extraction should raise it as a question instead.
    - `diff-only`: only visible in the code. Extraction should raise it as a question, or miss it without penalty.
  - `not_testable`: statements in the evidence that shouldn't become claims, with the reason.
  - `regression_hints`: existing behavior that should be unchanged (used by the regression step).
  - `questions`: what a good extractor should ask the developer. Questions marked `needs: app-context` require knowing what else exists in the app (not in the PR's evidence). They're scored separately, and are the test for adding summaries of base-branch pages later.
- `checkpoints`: (later) expected claim statuses at specific commits, for verification.
- `regressions`: (later) behavior the PR changed that nobody asked for, for evaluating the regression step.

## Writing claims

- **One claim, one check.** `when` can be a sequence of actions, but it ends in exactly one observable result. Several results after the same action become several claims with the same `when`; the test builder can group them into one journey step. Status is per claim, so a claim with two results is ambiguous when one holds and the other doesn't.
- **Guard checks aren't claims.** A check like "the page still renders", which stops a `hidden` claim passing on a blank page, is added by the verifier, not written into the claim.
- **No speculative edge cases.** Claims come from intent, not from imagining what could break. An edge case becomes a claim only when the PR itself deals with it: a fix commit or review discussion during the PR. Otherwise it belongs to the future PR that fixes it, where it's that PR's intent. The same rule applies to what the extractor should produce.
- `review`: draft/reviewed status and notes.
