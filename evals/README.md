# Evals

Golden cases for Groundtruth, one YAML file per PR in `cases/`. Candidate PRs are listed in [docs/eval-candidates.md](../docs/eval-candidates.md).

## Running

1. `npm run evals:snapshot` freezes each case's evidence, as it was when the PR was opened, into `evidence/<id>.txt`. That file is exactly what the extractor sees. Re-run it after adding a case.
2. `npm run evals` runs extraction on every case (3 runs each by default), has the judge map the output onto the reference, and writes `runs/<timestamp>/report.md` plus `results.json`. Needs `ANTHROPIC_API_KEY` in `.env`.
   Options: `--runs 1`, `--case lobsters-2132`, `--split test`, `--model claude-sonnet-5` (extractor), `--judge-model ...`.

Metrics, pooled across cases (partial matches count half):

- **Precision**: extracted claims that correspond to a reference claim. The headline metric.
- **Intent recall**: `intent` reference claims found.
- **Lures taken**: claims made from `not_testable` statements, plus any claim on a case whose reference has no claims (`no-claims` PRs, and vague ones where only the diff would tell what changed).
- **Duplicates**: claims or assumptions matching a reference item an earlier one already matched (e.g. an assumption repeating a claim).
- **Unmatched claims**: claims matching nothing. Either invented or a gap in the reference; review each one and either accept the penalty or add it to the case.
- **Assumption coverage**: open decisions (standalone reference assumptions, plus `discussion` and `diff-only` claims) that the extractor raised as an assumption or a claim.
- **App-context coverage**: the same, for assumptions marked `needs: app-context`.
- **Default accuracy**: of the extractor's assumptions that match something, the share whose checkbox default is right. Matching a reference claim means it should be checked; matching a `not_testable` item means unchecked; `default: any` isn't scored.
- **Off-topic assumptions**: assumptions matching nothing in the reference.
- **Ungrounded sources**: claims whose `source` quote isn't found in the evidence (checked in code, no judge).

## Judging

The judge labels each extracted item match / partial / none by one test: **would a browser test built from it catch the same broken implementations as one built from the reference?** A claim must say *what* to check; it may leave out *how* to get there.

- **What to check** must be the same observable. When the evidence specifies how the behavior shows up (buttons "disabled" rather than removed; a length limit on an input), a different observable is partial. When the evidence only states the goal, any observable that proves it matches.
- **How to get there** can omit preconditions a competent tester supplies anyway (a transfer must exist to see a transfer flow) and where controls are. It's partial when the omission lets the check pass on a broken feature (checking a toggle's off state without ever turning it on) or when it's a different situation (editing an existing story instead of creating one).

Calibrated against human spot checks in `spotchecks/` (`npm run evals:spotcheck -- <run> [count] [exclude.key.json ...]`).

## Case fields

- `type`: what kind of PR this is: `new-feature`, `behavior-change`, `bugfix`, `ui-only`, `no-claims`, `vague`. Assigned by whoever writes the case, and used to break down scores (e.g. "we do badly on behavior changes"). For `no-claims`, the expected output is zero claims.
- `split`: `dev` or `test`. Dev cases are the ones we look at when changing the extractor prompt. Test cases are held out: we only compare their scores, and the report hides their claim-level details (they're still in `results.json`). If a prompt change helps dev but not test, it's overfitting.
- `tags`: optional free-form labels for slicing results, e.g. `ai-assisted` when the PR says it was written with AI tools.
- `snapshot`: the evidence as it was when the PR was **opened**, which is when Groundtruth runs. Descriptions get edited, bots append to them, and commits land after opening; none of that should be visible to extraction.
- `setup`: what a tester needs (feature flags, data) to check the claims.
- `expected`: the reference output for claim extraction.
  - `claims`: each has `when` (the user action), `then` (`kind` + `what`), a `source`, and `derivable`:
    - `intent`: follows from the evidence at open. Extraction is expected to produce it.
    - `discussion`: only known from later review comments. Extraction should raise it as an assumption instead.
    - `diff-only`: only visible in the code. Extraction should raise it as an assumption, or miss it without penalty.
  - `not_testable`: statements in the evidence that shouldn't become claims, with the reason.
  - `regression_hints`: existing behavior that should be unchanged (used by the regression step).
  - `assumptions`: decisions the evidence leaves open, which the extractor should raise as yes/no checkboxes (at most 3 per PR) with a default: `default: true` (checked, test it), `false` (unchecked), or `any` (the evidence gives no basis for a guess; only coverage is scored). `same_as: c4` points at a reference claim instead of restating it. Only decisions the running app can't answer belong here: whether a default or scope is intended, not exact labels or values. Assumptions marked `needs: app-context` require knowing what else exists in the app; they're scored separately, and are the test for adding summaries of base-branch pages later.
- `checkpoints`: (later) expected claim statuses at specific commits, for verification.
- `regressions`: (later) behavior the PR changed that nobody asked for, for evaluating the regression step.
- `review`: draft/reviewed status and notes.

## Writing claims

- **One claim, one check.** `when` can be a sequence of actions, but it ends in exactly one observable result. Several results after the same action become several claims with the same `when`; the test builder can group them into one journey step. Status is per claim, so a claim with two results is ambiguous when one holds and the other doesn't.
- **Guard checks aren't claims.** A check like "the page still renders", which stops a `hidden` claim passing on a blank page, is added by the verifier, not written into the claim.
- **No speculative edge cases.** Claims come from intent, not from imagining what could break. An edge case becomes a claim only when the PR itself deals with it: a fix commit or review discussion during the PR. Otherwise it belongs to the future PR that fixes it, where it's that PR's intent. The same rule applies to what the extractor should produce.
