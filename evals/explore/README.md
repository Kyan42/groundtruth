# Exploration evals

Does the exploring agent's verdict match reality? Each case gives the agent fixed, hand-written claims (not the extractor's output) and runs it on commits where the answer is known. Where a claim came from doesn't matter here: this measures the agent's judgment. Whether the extractor would write the claim is the extraction eval's job (`evals/cases/`).

## A case file

```yaml
id: mealie-7564
pr: mealie-recipes/mealie#7564
boot: evals/boot/mealie.yml     # or "repo" for the repo's own .groundtruth.yml
persona: admin                  # the test account the claims assume (from the boot config's personas)
commits:                        # full SHAs; any names, one agent run per commit
  base: ...                     # where the PR branched off: the feature doesn't exist yet
  buggy_a: ...                  # just before a fix inside the PR
  head: ...
claims:                         # inline, or a path to an extraction case whose expected.claims to use
  - { id: c1, when: ..., then: { kind: ..., what: ... } }
expect:                         # per claim, per commit
  c1: { base: breaks, buggy_a: holds, head: holds }
```

## Labels

| Label | The agent should say | Typical commit |
|---|---|---|
| `holds` | verified | the feature works |
| `breaks` | anything but verified (failed or unreachable) | base: the feature isn't there, so the action may not even exist |
| `fails` | failed: the feature is there and does the wrong thing | a buggy commit |

Scored per claim and commit. The costly mistake is a **false pass** (verified where the label is `breaks` or `fails`); a false fail (failed where it `holds`) is noise; `unreachable` where it `holds` is a missed check. For `fails`, a verdict of unreachable counts as wrong but not as a false pass.

## Cases

| Case | Commits | What it tests |
|---|---|---|
| cbay-1 | base, head | an easy control (a cart where the base has none) |
| cbay-3 | base, head | new claims break on base; a regression we planted fails on head (smoke test) |
| mealie-8363 | base, head | a real app with a login; three claims flip, one holds on both |
| mealie-7564 | base, buggy_a, fix_a, head | in-PR bugs against the stated intent, and one still in the merged code |
| mealie-8292 | base, buggy, head | an edge case (a ticked assumption) fixed inside the PR |
