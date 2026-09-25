# Bugs fixed inside real PRs (Sept 2026)

30 merged PRs from 10 open-source web apps (3 each: Mealie, Actual, Immich, Gitea, Ghost, Paperless-ngx, Outline, Cal.com, Discourse, Saleor Dashboard) where a later commit in the same PR fixed a bug the PR itself introduced. Collected by an agent with no knowledge of Groundtruth, taking qualifying PRs in order (newest first) rather than picking; classified by the same agent. Data: `bugs.json` (one entry per PR, with commits, evidence and steps), `sampling.json` (PRs examined per repo).

Five fix commits were spot-checked against GitHub (exist, belong to the PR, messages match). The classifications themselves are one model's reading: 9 high, 17 medium, 4 low confidence.

## Base rate

30 qualifying PRs out of 205 human-authored PRs examined (262 including bots): about 15%.

## Primary category

| | Category | Count |
|---|---|---|
| A | Contradicts the PR's stated intent (at opening) | 2 |
| B | Unstated edge case of the new behavior | 14 |
| C | Broke existing behavior (regression) | 8 |
| D | Crash or error | 0 (3 as secondary) |
| E | Visual or layout | 2 (6 incl. secondary) |
| F | Not user-visible | 1 (10 incl. secondary) |
| G | Intent changed mid-PR | 3 |

Observable in a browser: 11 yes, 10 partly, 9 no.

Found by: the author (about 11), AI review bots (11: CodeRabbit 5, Copilot 6), human reviewers or maintainers (6), CI (1).

## Caveats

- These are bugs caught before merge. Bugs that slipped through review (the ones a CI check exists for) may look different: mine follow-up fix PRs for those.
- Bugs that contradict the stated intent are probably caught by authors before opening the PR (Mealie #7564's fixes all predate opening), so A is likely undercounted here relative to all bugs written.
