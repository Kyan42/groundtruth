# Eval run 2026-09-22T23:41:06.392Z

Extractor: `claude-opus-5` · Judge: `claude-opus-5-5` · 1 cases × 1 runs · est. cost $0.05

## Summary

Pooled across cases per run; mean over runs (min–max). Partial matches count half. Test cases are held out from prompt tuning, so their details are only in results.json.

| Metric | dev (1 cases) |
|---|---|
| **Precision** | 50% |
| Intent recall | 50% |
| Lures taken (count) | 0.0 |
| Unmatched claims (count, adjudicate) | 0.0 |
| Ungrounded sources (count) | 0.0 |
| Assumption coverage | 100% |
| App-context coverage | 100% |
| Default accuracy | 100% |
| Off-topic assumptions (count) | 0.0 |

## lobsters-2029 (vague, dev)

lobsters/lobsters#2029 · 4 reference claims, 2 intent, 1 assumptions

| Run | Claims | Precision | Intent recall | Lures | Unmatched | Ungrounded | Assumptions | Coverage | Defaults | App-context |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 3 | 50% | 50% | 0 | 0 | 0 | 2 | 100% | 100% | 100% |

<details><summary>Run 1: 3 claims, 2 assumptions</summary>

| # | Claim | Ref | Label | Grounded | Judge |
|---|---|---|---|---|---|
| m1 | On the submit story page, type a single character in the Title field and submit the form → (state) The title field is reported invalid by the browser and the form is not submitted | c2 | partial | yes | It bundles the title field being marked invalid (c2) with the form not being submitted (c1). |
| m2 | On the submit story page, type a single character in the Title field and submit the form → (url) The browser stays on the submit story page instead of navigating to a created story | c1 | partial | yes | Staying on the page is a weaker check than confirming no request reaches the server. |
| m3 | On the submit story page, type a title long enough to meet the minimum length and submit the form with a valid URL → (state) The title field is not reported invalid and the form submission proceeds | c3 | partial | yes | It targets valid-length submission going through but does not test the 3-character boundary, and it observes state rather than the network request. |

| # | Assumption | Ref | Label | Judge |
|---|---|---|---|---|
| s1 | ☐ On the story edit page, entering a too-short title and submitting → (state) The browser blocks submission the same way — _Title mentions only the submit story page; edit page may share or not share the form_ | c4 | match | Both check that editing a story with a too-short title blocks submission. |
| s2 | ☑ On the submit story page, entering a 2-character title and submitting → (state) The title field is reported invalid — _Evidence gives no number; guessing the minimum matches the app's existing server-side title length rule (3)_ | c2 | match | A 2-character title is too short, and the result is the title field being marked invalid. |

Not testable:

- Add minlength to title input in submit story page (The presence of an HTML attribute itself is an implementation detail; only the resulting validation behavior is observable)

Regression hints:

- Submitting a story with a valid title and URL still works and creates the story
- Server-side validation errors for other fields (e.g. missing or duplicate URL) still display as before
- The title field still accepts and preserves normal-length titles, including any auto-fetched title

</details>

Reference:

- c1 [intent] on the story submit page, enter a title that is too short and submit → (network) no request is sent to the server
- c2 [intent] on the story submit page, enter a title that is too short and submit → (state) the title field is marked invalid for being too short
- c3 [diff-only] on the story submit page, enter a 3-character title (other fields valid) and submit → (network) the form is sent to the server
- c4 [diff-only] edit an existing story, shorten its title below the minimum, and save → (network) no request is sent to the server
- a1 ☐/☑ (= c4) edit an existing story, shorten its title below the minimum, and save → (network) no request is sent to the server [app-context]
