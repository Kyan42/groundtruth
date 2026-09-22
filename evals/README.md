# Evals

Golden cases for Groundtruth, one YAML file per PR in `cases/`. Candidate PRs are listed in [docs/eval-candidates.md](../docs/eval-candidates.md).

## Case fields

- `type`: what kind of PR this is: `new-feature`, `behavior-change`, `bugfix`, `ui-only`, `no-claims`, `vague`. Assigned by whoever writes the case, and used to break down scores (e.g. "we do badly on behavior changes"). For `no-claims`, the expected output is zero claims.
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
