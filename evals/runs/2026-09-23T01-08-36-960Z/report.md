# Eval run 2026-09-23T01:08:36.951Z (re-judge of evals/runs/2026-09-23T00-24-04-205Z)

Extractor: `claude-opus-5` (prompt unknown) · Judge: `claude-opus-5-5` (prompt 9b1faa42) · 8 cases × 3 runs

Cost: extraction $0.000 (from the original run) + judge $0.351 · spent by this run $0.351

## Summary

Pooled across cases per run; mean over runs (min–max). Partial matches count half. Test cases are held out from prompt tuning, so their details are only in results.json.

| Metric | dev (4 cases) | test (4 cases) |
|---|---|---|
| **Precision** | 90% (86%–95%) | 63% (61%–66%) |
| Intent recall | 83% (78%–89%) | 64% (54%–77%) |
| Lures taken (count) | 0.0 | 2.7 (2.0–3.0) |
| Unmatched claims (count, adjudicate) | 0.0 | 3.3 (2.0–4.0) |
| Duplicate claims (count) | 2.3 (1.0–3.0) | 1.0 |
| Ungrounded sources (count) | 0.0 | 0.3 (0.0–1.0) |
| Assumption coverage | 52% (43%–57%) | 58% (50%–75%) |
| App-context coverage | 33% | 0% |
| Default accuracy | 93% (80%–100%) | 83% (50%–100%) |
| Off-topic assumptions (count) | 0.7 (0.0–1.0) | 5.3 (5.0–6.0) |
| **Extraction cost per PR** | $0.000 | $0.000 |
| Judge cost per PR (eval only) | $0.015 | $0.014 |

## actual-8780 (new-feature, dev)

actualbudget/actual#8780 · 9 reference claims, 4 intent, 5 assumptions

| Run | Claims | Precision | Intent recall | Lures | Unmatched | Dupes | Ungrounded | Assumptions | Coverage | Defaults | App-context | Cost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 4 | 75% | 63% | 0 | 0 | 1 | 0 | 3 | 50% | 100% | 0% | $0.000 + $0.025 |
| 2 | 4 | 100% | 75% | 0 | 0 | 1 | 0 | 3 | 50% | 100% | 0% | $0.000 + $0.029 |
| 3 | 3 | 83% | 63% | 0 | 0 | 0 | 0 | 3 | 33% | 100% | 0% | $0.000 + $0.017 |

<details><summary>Run 1: 4 claims, 3 assumptions</summary>

| # | Claim | Ref | Label | Grounded | Judge |
|---|---|---|---|---|---|
| m1 | Open the Reports page and view the Sankey chart → (visible) An option/toggle to show transfers is shown in the Sankey chart's controls | c1 | partial | yes | Checks that a show-transfers option exists but doesn't specify the Spent view or the Options menu, so the situation is vague. |
| m2 | In the Sankey chart's Spent view, turn on the show-transfers option → (visible) Transfer flows between accounts appear as additional links/nodes in the Sankey diagram | c2 | partial | yes | Turning on show transfers yields transfer flows, but without the specific income/funded-spending account setup that c2 requires. |
| m3 | In the Sankey chart Spent view with show-transfers turned on, for a budget whose income lands in one account and spending happens from a credit card → (visible) The diagram is connected (income flows through to the spending accounts) instead of showing disconnected parts | c2 | match | yes | Same setup (income in one account, spending from another funded account), checking that transfer flows connect them. |
| m4 | In the Sankey chart Spent view, turn on the show-transfers option in a budget that has transfers to off-budget accounts → (hidden) Transfers to off-budget accounts are not shown in the diagram | c5 | match | yes | Transfers to off-budget accounts are not drawn when show transfers is on. |

| # | Assumption | Ref | Label | Judge |
|---|---|---|---|---|
| s1 | ☑ Open the Sankey chart for the first time without changing any options → (state) The show-transfers option is off, and the chart looks as it did before this PR — _Screenshots present 'option off' as the baseline, implying off by default_ | c4 | match | The show-transfers option is off by default on a fresh Sankey chart. |
| s2 | ☐ Turn on the show-transfers option and reload the Reports/Sankey page → (persisted) The show-transfers option is still on — _Evidence says nothing about saving the toggle with the report_ | c7 | partial | Checks persistence after a reload, but not via saving to the dashboard and checking that the card still shows transfer flows. |
| s3 | ☐ Switch the Sankey chart away from the Spent view → (hidden) The show-transfers option is not offered — _Commit says 'Spent view' but it is unclear whether the toggle is hidden elsewhere_ | a3 | partial | Addresses the same open decision about show transfers outside the Spent view but guesses the option is hidden rather than applied in Budgeted. |

Not testable:

- Fix recursion error happening in otherGrouping (Internal fix introduced during this PR's development; no stated user-facing symptom)
- Development aided by OpenCode / GPT-5 Mini. (Process note, not user-visible behavior)
- Manual testing with a specifically crafted tested budget and the full history of my own budget. (Describes how the change was tested)
- Add release note (Repository housekeeping, not observable in the app)
- Self-review has been performed (Checklist item about process)

Regression hints:

- With the show-transfers option off, the Sankey chart renders exactly as before (no transfer links)
- Other report types and the rest of the Reports page continue to work unchanged
- Sankey totals/amounts for income and spending categories remain correct when the option is off

</details>

<details><summary>Run 2: 4 claims, 3 assumptions</summary>

| # | Claim | Ref | Label | Grounded | Judge |
|---|---|---|---|---|---|
| m1 | Open a dashboard/report with the Sankey chart and open its options/menu → (visible) An option to show transfers is offered for the Sankey chart | c1 | match | yes | Both check that the Sankey options menu offers a show-transfers option, though m1 does not name the Spent view. |
| m2 | Enable the show-transfers option on the Sankey chart in the Spent view → (visible) The chart renders transfer flows connecting the accounts (e.g. income account to credit card) that were absent before | c2 | match | yes | Enabling show transfers in Spent view draws transfer flows between accounts, the same behavior as c2. |
| m3 | Enable the show-transfers option on the Sankey chart when the budget has income in one account and spending from a credit card → (visible) The Sankey graph is connected rather than showing disconnected groups | c2 | match | yes | Same scenario (income in one account, spending from a credit card) and a connected graph verifies the transfer flow is drawn. |
| m4 | Enable the show-transfers option on the Sankey chart in a budget containing transfers to off-budget accounts → (hidden) Flows representing transfers to off-budget accounts are not shown | c5 | match | yes | Both check that transfers involving off-budget accounts are not drawn when show transfers is on. |

| # | Assumption | Ref | Label | Judge |
|---|---|---|---|---|
| s1 | ☑ Open the Sankey chart for the first time without changing any option → (state) The show-transfers option is off and no transfer flows are displayed — _Evidence calls it an 'optional toggle' and shows an 'option off' screenshot first, implying off by default_ | c4 | match | Both check that the show-transfers option is off by default on a fresh Sankey chart. |
| s2 | ☑ Enable the show-transfers option and reload the page → (persisted) The Sankey chart still shows transfers after reload — _Report widget options normally persist, but the evidence never states it_ | c7 | partial | It checks persistence after reload but omits saving the report to the dashboard, so it tests a different situation. |
| s3 | ☐ Switch the Sankey chart to a view other than Spent and open its options → (hidden) The show-transfers option is not offered — _Commit says 'Add transfers as option to Spent view' but scope in other views is unstated_ | a3 | partial | It addresses the same open question of transfers outside the Spent view, but guesses the opposite outcome (option hidden rather than flows drawn). |

Not testable:

- Development aided by OpenCode / GPT-5 Mini. (How the work was done; not observable in the browser)
- Manual testing with a specifically crafted tested budget and the full history of my own budget. (Describes testing process, not intended app behavior)
- Fix recursion error happening in otherGrouping (Internal fix within the PR's own development; no stated user-facing symptom)
- Add release note (Repository tooling, not visible in the app)
- No obvious regressions in affected areas / Self-review has been performed (Checklist statements about process)

Regression hints:

- With the show-transfers option off, the Sankey chart renders exactly as before (no transfer flows)
- Existing Sankey chart views and totals remain unchanged when transfers are not shown
- Other report charts are unaffected

</details>

<details><summary>Run 3: 3 claims, 3 assumptions</summary>

| # | Claim | Ref | Label | Grounded | Judge |
|---|---|---|---|---|---|
| m1 | Open the Reports section and view the Sankey chart in the Spent view → (visible) A toggle/option to show transfers is displayed in the Sankey chart controls | c1 | match | yes | Both check that a show-transfers option is available when viewing the Sankey in Spent view. |
| m2 | Enable the show-transfers option on the Sankey chart → (visible) The chart renders transfer flows/nodes connecting accounts that were previously unconnected | c2 | partial | yes | Same behavior but the setup is vague, lacking the specific income/transfer-funded-spending scenario. |
| m3 | Enable the show-transfers option on the Sankey chart with a budget that has transfers to off-budget accounts → (hidden) Transfers to off-budget accounts are not shown in the chart | c5 | match | yes | Both check that on-budget to off-budget transfers are not drawn when show transfers is enabled. |

| # | Assumption | Ref | Label | Judge |
|---|---|---|---|---|
| s1 | ☑ Open the Sankey chart for the first time without changing any option → (state) The show-transfers option is off, and the chart looks as it did before this PR — _Described as an 'optional toggle', implying transfers are off by default_ | c4 | match | Both check that the show-transfers option is off by default on a fresh Sankey report. |
| s2 | ☑ Toggle show-transfers on, then reload the page (or reopen the saved report) → (persisted) The show-transfers option is still enabled — _Report options in this app are typically saved, but the evidence does not state it_ | c7 | partial | Both concern persistence, but s2 checks only that the option stays enabled after reload, not that the saved dashboard card still shows transfer flows. |
| s3 | ☑ Switch the Sankey chart to a view other than Spent → (hidden) The show-transfers option is not offered outside the Spent view — _Title and commit scope it to the Spent view, but the UI may still expose it_ | a3 | none | a3 guesses transfer flows appear in the Budgeted view, whereas s3 guesses the option is absent outside Spent; these contradict each other rather than correspond. |

Not testable:

- Development aided by OpenCode / GPT-5 Mini. (Process note, not user-visible behavior)
- Manual testing with a specifically crafted tested budget and the full history of my own budget. (Describes how the change was tested)
- Fix recursion error happening in otherGrouping (Internal code fix within the same PR, not stated as a user-facing symptom)
- Add release note (Repository metadata, not observable in the app)
- Release notes added (see link above) (Checklist item about the repo, not app behavior)

Regression hints:

- With the transfers option off, the Sankey chart renders exactly as before this PR
- Other Sankey views and other reports remain unchanged
- No obvious regressions in affected areas

</details>

Reference:

- c1 [intent] open the Sankey report in Spent view and open its Options menu → (visible) there is an option to show transfers (shipped label: "Show transfers in Spent view")
- c2 [intent] turn on the show-transfers option, with income arriving in one account and spending made from another account that was funded by a transfer → (visible) a transfer flow is drawn between the two accounts
- c3 [intent] turn the show-transfers option back off → (hidden) no transfer flows are drawn between accounts
- c4 [discussion] open a fresh Sankey report in Spent view → (state) the show-transfers option is off by default
- c5 [intent] turn on show transfers with a transfer between an on-budget and an off-budget account in range → (hidden) that transfer is not drawn as a flow
- c6 [diff-only] turn on show transfers, with unequal transfers in both directions between the same two accounts in range → (visible) a single flow between them, sized to the net amount
- c6b [diff-only] turn on show transfers, with equal transfers in both directions between the same two accounts in range → (hidden) no flow is drawn between them
- c7 [diff-only] turn on show transfers, save the report to the dashboard, and reload → (persisted) the dashboard Sankey card still shows transfer flows
- c8 [discussion] turn on show transfers with transfers forming a cycle (A → B → C → A) in range → (visible) the chart renders (before the fix: "This widget has failed to load")
- a1 ☑ (= c4) open a fresh Sankey report in Spent view → (state) the show-transfers option is off by default
- a2 ☐/☑ (= c7) turn on show transfers, save the report to the dashboard, and reload → (persisted) the dashboard Sankey card still shows transfer flows
- a3 ☐ turn on show transfers, then switch the report to the Budgeted view → (visible) transfer flows are drawn in the Budgeted view
- a4 ☐/☑ (= c6) turn on show transfers, with unequal transfers in both directions between the same two accounts in range → (visible) a single flow between them, sized to the net amount
- a5 ☐/☑ turn on both "Group accounts in Spent view" and show transfers → (visible) transfer flows are drawn between the account groups [app-context]

## lobsters-2029 (vague, dev)

lobsters/lobsters#2029 · 4 reference claims, 2 intent, 1 assumptions

| Run | Claims | Precision | Intent recall | Lures | Unmatched | Dupes | Ungrounded | Assumptions | Coverage | Defaults | App-context | Cost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 3 | 83% | 100% | 0 | 0 | 1 | 0 | 2 | 100% | 100% | 100% | $0.000 + $0.019 |
| 2 | 3 | 83% | 100% | 0 | 0 | 1 | 0 | 2 | 100% | 100% | 100% | $0.000 + $0.021 |
| 3 | 2 | 75% | 75% | 0 | 0 | 0 | 0 | 2 | 100% | 100% | 100% | $0.000 + $0.018 |

<details><summary>Run 1: 3 claims, 2 assumptions</summary>

| # | Claim | Ref | Label | Grounded | Judge |
|---|---|---|---|---|---|
| m1 | On the submit story page, enter a title shorter than the allowed minimum length, fill the other required fields, and click the submit button → (state) The title input is reported as invalid (browser's minlength validation blocks it) | c2 | match | yes | Both check that the title field is marked invalid after submitting a too-short title. |
| m2 | On the submit story page, enter a title shorter than the allowed minimum length and click the submit button → (network) No story-creation request is sent to the server | c1 | match | yes | Both check that no request is sent when submitting a too-short title. |
| m3 | On the submit story page, enter a title shorter than the allowed minimum length and click the submit button → (url) The browser stays on the submit story page instead of navigating to a server-rendered error or story page | c1 | partial | yes | Staying on the submit page could also hold on main, where the server rejects the title and re-renders the form, so it wouldn't reliably catch the missing client-side block. |

| # | Assumption | Ref | Label | Judge |
|---|---|---|---|---|
| s1 | ☑ On the submit story page, enter a title with the minimum allowed number of characters and submit → (network) The submission is sent and accepted (minlength matches the server-side minimum title length) — _Evidence doesn't state the value; assuming it mirrors the existing server-side title length rule_ | c3 | match | Both check that a title of exactly the minimum length is sent to the server. |
| s2 | ☐ On the edit story page for an existing story, shorten the title below the minimum and save → (state) The title input is reported as invalid — _Title mentions only the submit story page, so edit may be out of scope_ | c4 | match | Both check that the edit form blocks a shortened title client-side; the invalid-field state verifies this as well as the absence of a request. |

Not testable:

- Add minlength to title input in submit story page (The presence of an HTML attribute itself is a code-level detail; only the resulting validation behavior is observable)

Regression hints:

- Submitting a story with a title of acceptable length still works and creates the story
- Other submit-form validation (URL, tags, required fields) and server-side error messages remain unchanged

</details>

<details><summary>Run 2: 3 claims, 2 assumptions</summary>

| # | Claim | Ref | Label | Grounded | Judge |
|---|---|---|---|---|---|
| m1 | Open the submit story page, enter a very short title (e.g. one character) along with a URL/description, and click the submit button → (state) The title field is reported as invalid by the browser's built-in validation (minimum length not met) | c2 | match | yes | Both check that a too-short title on the submit page leaves the title field marked invalid for minimum length. |
| m2 | Open the submit story page, enter a very short title (e.g. one character), and click the submit button → (network) No story-creation request is sent to the server; the form submission is blocked client-side | c1 | match | yes | Both check that no request is sent when submitting a too-short title. |
| m3 | Open the submit story page, enter a very short title and click the submit button → (url) The browser stays on the submit story page instead of navigating to a server-rendered error page or the new story | c1 | partial | yes | Staying on the submit page could also happen after the old server-side rejection, so it wouldn't reliably catch a missing client-side block. |

| # | Assumption | Ref | Label | Judge |
|---|---|---|---|---|
| s1 | ☑ On the submit story page, enter a title long enough to meet the server's existing minimum (e.g. 3 or more characters) and submit → (network) The form submits normally and the story-creation request is sent — _Evidence gives no number; assuming the added minlength mirrors the existing server-side minimum so valid titles still submit_ | c3 | partial | It checks that a valid title submits, but it allows any length of 3 or more instead of testing the exact 3-character boundary. |
| s2 | ☐ Open the edit page of an existing story and enter a very short title → (state) The title field is reported as invalid by the browser's built-in validation — _Title mentions only the submit story page, so the edit form may be out of scope_ | c4 | match | It checks that the minimum-length validation also applies on the edit form, which verifies the same side effect as the blocked save. |

Not testable:

- Add minlength to title input in submit story page (The attribute itself is markup/internal; only its resulting browser validation behavior is observable)

Regression hints:

- Submitting a story with a valid-length title still works and creates the story
- Existing server-side title validation and its error messages remain unchanged
- Other fields on the submit story page (URL, tags, description) keep their current validation behavior

</details>

<details><summary>Run 3: 2 claims, 2 assumptions</summary>

| # | Claim | Ref | Label | Grounded | Judge |
|---|---|---|---|---|---|
| m1 | Log in, open the submit new story page, enter a URL/title with a very short title (e.g. one character) and try to submit the form → (state) The title input is reported as invalid (browser validation blocks submission because of the minimum length) | c2 | match | yes | Both check that a too-short title on the submit page leaves the title field marked invalid for its minimum length. |
| m2 | Log in, open the submit new story page, enter a very short title and try to submit the form → (url) The browser stays on the submit story page; the story is not created | c1 | partial | yes | Staying on the page with no story created was already true before this PR because the server rejected short titles, so it doesn't verify the submit is blocked client-side. |

| # | Assumption | Ref | Label | Judge |
|---|---|---|---|---|
| s1 | ☐ Open the edit page of an existing story and shorten the title below the minimum, then save → (state) The title input is also reported as invalid — _Title mentions only the submit story page; unclear whether the edit form shares the same input_ | c4 | match | It targets the same edit-page case, and the field being reported invalid would fail if the minimum-length validation were missing there. |
| s2 | ☑ Submit a story whose title meets the minimum length but is still short → (state) The title input is accepted and the form submits — _Evidence doesn't state the exact minlength value, assumed to match the server-side minimum_ | c3 | partial | It aims at the accepted side of the boundary but is vague about the length and doesn't test exactly 3 characters. |

Not testable:

- Add minlength to title input in submit story page (The attribute itself is a markup/internal detail; only the resulting validation behavior is observable)

Regression hints:

- Submitting a story with a normal, sufficiently long title still works and creates the story
- Other fields of the submit story form (URL, tags, description) keep their existing validation behavior

</details>

Reference:

- c1 [intent] on the story submit page, enter a title that is too short and submit → (network) no request is sent to the server
- c2 [intent] on the story submit page, enter a title that is too short and submit → (state) the title field is marked invalid for being too short
- c3 [diff-only] on the story submit page, enter a 3-character title (other fields valid) and submit → (network) the form is sent to the server
- c4 [diff-only] edit an existing story, shorten its title below the minimum, and save → (network) no request is sent to the server
- a1 ☐/☑ (= c4) edit an existing story, shorten its title below the minimum, and save → (network) no request is sent to the server [app-context]

## lobsters-2132 (behavior-change, dev)

lobsters/lobsters#2132 · 4 reference claims, 3 intent, 1 assumptions

| Run | Claims | Precision | Intent recall | Lures | Unmatched | Dupes | Ungrounded | Assumptions | Coverage | Defaults | App-context | Cost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 4 | 100% | 100% | 0 | 0 | 1 | 0 | 2 | n/a | 100% | 0% | $0.000 + $0.020 |
| 2 | 4 | 100% | 100% | 0 | 0 | 1 | 0 | 2 | n/a | 100% | 0% | $0.000 + $0.013 |
| 3 | 4 | 100% | 100% | 0 | 0 | 1 | 0 | 2 | n/a | 100% | 0% | $0.000 + $0.013 |

<details><summary>Run 1: 4 claims, 2 assumptions</summary>

| # | Claim | Ref | Label | Grounded | Judge |
|---|---|---|---|---|---|
| m1 | On the story submission page (/stories/new), enter a title and URL and click Preview → (state) The "save" button in the previewed story's byline is disabled | c1 | match | yes | Same action and the same observable: the save button in the preview byline is disabled. |
| m2 | On the story submission page (/stories/new), enter a title and URL and click Preview → (state) The "hide" button in the previewed story's byline is disabled | c2 | match | yes | Same action and the same observable: the hide button in the preview byline is disabled. |
| m3 | On the story submission page (/stories/new), enter a title and URL and click Preview, then inspect the comments text in the previewed story's byline → (hidden) The comments text is not a clickable link (no link/href to a story comments page) | c3 | match | yes | Verifies the comments label in the preview byline is not a link, the same as the reference. |
| m4 | On the story submission page (/stories/new), click Preview and then click the "save" button in the previewed story byline → (network) No save request is sent (no 400 error response) | c1 | match | yes | Checking that clicking save sends no request verifies the save button doesn't work in preview just as well. |

| # | Assumption | Ref | Label | Judge |
|---|---|---|---|---|
| s1 | ☑ Previewing a story edit (editing an existing story and clicking Preview) → (state) The save/hide byline buttons are also disabled there — _Evidence only mentions submit preview; the same preview partial likely covers edit._ | — | none | The reference only covers the new-story submit preview; editing an existing story is a separate situation it doesn't address. |
| s2 | ☑ Previewing a story on the submission page → (visible) The save and hide buttons are still shown (disabled) rather than removed entirely — _Author says "disables the two forms", implying they remain visible._ | c1 | partial | Bundles save and hide, and mainly checks that the buttons are present rather than that they are disabled. |

Not testable:

- just disables the two forms (Describes the implementation approach (form-level change), not a user-observable result on its own)

Regression hints:

- Clicking Preview still renders the story preview along with the submission form
- On real story pages (front page, story page), the save and hide buttons still work and submit successfully
- On real story pages, the comments link still navigates to the story's comments page
- Submitting a story from the preview page still works

</details>

<details><summary>Run 2: 4 claims, 2 assumptions</summary>

| # | Claim | Ref | Label | Grounded | Judge |
|---|---|---|---|---|---|
| m1 | On the story submission page (/stories/new), fill in a title and URL and click Preview → (state) The "save" button shown in the previewed story's byline is disabled | c1 | match | yes | Checks that the save button in the preview byline is disabled after clicking Preview. |
| m2 | On the story submission page (/stories/new), fill in a title and URL and click Preview → (state) The "hide" button shown in the previewed story's byline is disabled | c2 | match | yes | Checks that the hide button in the preview byline is disabled after clicking Preview. |
| m3 | On the story submission page, click Preview and inspect the previewed story's byline comments text → (hidden) The comments text is not a clickable link (no href pointing to a story URL) | c3 | match | yes | Checks that the comments text in the preview byline is not a link. |
| m4 | On the story submission page, click Preview and then attempt to click the byline "save" button → (network) No save request is sent (no 400 error response occurs) | c1 | match | yes | No save request being sent verifies that save is disabled in the preview, just through a different observable. |

| # | Assumption | Ref | Label | Judge |
|---|---|---|---|---|
| s1 | ☑ On the story submission page, click Preview → (visible) The comments text is still displayed in the byline, just not as a link — _Author says the link/href is removed, not the text, but removal entirely is possible_ | c3 | partial | Targets the comments label behavior, but its result (the text is still displayed) would hold even if it remained a link. |
| s2 | ☑ Editing an existing story and clicking Preview → (state) The byline save and hide buttons are disabled there too — _Preview partial is likely shared, but evidence only mentions story submission_ | — | none | The reference covers no edit-story preview situation. |

Not testable:

- just disables the two forms (Describes the implementation approach (form-level change) rather than an observable user-facing result on its own)

Regression hints:

- The story preview still renders the story title, URL, tags and byline as before
- Submitting the story after previewing still works and creates the story
- Save and hide buttons on real story listings (home page, story pages) remain functional and enabled

</details>

<details><summary>Run 3: 4 claims, 2 assumptions</summary>

| # | Claim | Ref | Label | Grounded | Judge |
|---|---|---|---|---|---|
| m1 | On the submit story page, enter a title and URL and click Preview → (state) The "save" button in the previewed story's byline is disabled | c1 | match | yes | Same action and the same disabled-save observable. |
| m2 | On the submit story page, enter a title and URL and click Preview → (state) The "hide" button in the previewed story's byline is disabled | c2 | match | yes | Same action and the same disabled-hide observable. |
| m3 | On the submit story page, enter a title and URL and click Preview, then look at the previewed story's byline comments element → (hidden) The comments element is not a clickable link (no href pointing to a story URL) | c3 | match | yes | Checks that the comments element is not a link in the preview byline. |
| m4 | On the submit story page, click Preview and then click the "save" button in the previewed byline → (network) No save request is sent and no 400 error response occurs | c1 | match | yes | Clicking save sends no request, which verifies that save is non-functional in the preview. |

| # | Assumption | Ref | Label | Judge |
|---|---|---|---|---|
| s1 | ☑ On the submit story page, click Preview → (visible) The comments count text still appears in the byline, just without a link — _Evidence says the href is removed, not the whole element, but removal is possible_ | c3 | partial | Checks that the comments text still appears, which would hold even if it were still a link, so it wouldn't catch a broken change. |
| s2 | ☑ On the submit story page, click Preview and click the comments element → (url) The page address stays on the submit story page — _Implied by removing the invalid link, but not stated explicitly_ | c3 | match | Clicking comments not navigating away verifies that the label isn't a link. |

Not testable:

- just disables the two forms (Describes the implementation (form markup) rather than an observable browser outcome)

Regression hints:

- Previewing a story still renders the story preview with its byline and metadata
- Submitting the story after preview still works and the save/hide buttons and comments link function normally on real story listings/pages

</details>

Reference:

- c1 [intent] fill in the story submit form and click Preview → (state) the "save" button in the preview's byline is disabled
- c2 [intent] fill in the story submit form and click Preview → (state) the "hide" button in the preview's byline is disabled
- c3 [intent] fill in the story submit form and click Preview → (state) the comments label in the preview's byline is plain text, not a link
- c4 [discussion] as a moderator, fill in the story submit form and click Preview → (hidden) the "mod edit" link does not appear in the preview's byline
- a1 ☐/☑ (= c4) as a moderator, fill in the story submit form and click Preview → (hidden) the "mod edit" link does not appear in the preview's byline [app-context]

## lobsters-2172 (no-claims, dev)

lobsters/lobsters#2172 · 0 reference claims, 0 intent, 0 assumptions

| Run | Claims | Precision | Intent recall | Lures | Unmatched | Dupes | Ungrounded | Assumptions | Coverage | Defaults | App-context | Cost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 0 | n/a | n/a | 0 | 0 | 0 | 0 | 1 | n/a | 0% | n/a | $0.000 + $0.008 |
| 2 | 0 | n/a | n/a | 0 | 0 | 0 | 0 | 0 | n/a | n/a | n/a | $0.000 + $0.000 |
| 3 | 0 | n/a | n/a | 0 | 0 | 0 | 0 | 0 | n/a | n/a | n/a | $0.000 + $0.000 |

<details><summary>Run 1: 0 claims, 1 assumptions</summary>

| # | Claim | Ref | Label | Grounded | Judge |
|---|---|---|---|---|---|

| # | Assumption | Ref | Label | Judge |
|---|---|---|---|---|
| s1 | ☑ Browse the site after the dead-code removal (story domain links, login, Mastodon author links, GitHub auth, comment voting, traffic graph) → (visible) All existing pages and flows continue to work unchanged — _Pure removal of unreferenced code should have no user-visible effect; only regression checks apply._ | nt1 | none | It restates the not-testable point that removing unreferenced dead code changes no behavior. |

Not testable:

- Remove unreferenced dead code (Internal code cleanup with no user-observable behavior change.)
- Found these leftovers using rubydex (Describes the tooling/method used to find the code, not app behavior.)
- `TrafficHelper::CACHE_FOR` became unused after e334ae41 stopped using time-based traffic cache keys. (Internal constant; the behavior change happened in an earlier commit.)
- `Keystore.find_or_create_key_for_update` became unused after b45d1750 switched the remaining code to `upsert`. (Internal method removal; behavior changed by an earlier commit.)
- `Keystore.decrement_value_for` and `decremented_value_for` ... don't seem to have ever had callers. (Never-called internal methods; no observable effect.)
- `Story#domain_search_url` became unused after f718da18 switched domain links to `for_domain_url`. (Internal method removal; link behavior changed by an earlier commit.)
- `Story#update_availability` was added in 9c73c87d but never used. (Never-called internal method; no observable effect.)
- `User#undelete!` became unused after 6fb659f8 stopped automatically reactivating deleted users on login. (Login behavior changed by an earlier commit, not this PR.)
- `User#mastodon_acct` became unused after f1a253c1 started building the Mastodon author URL directly. (Internal method removal; URL building changed by an earlier commit.)
- `Vote#on_comment?` was added in bb5e53e9 but never used. (Never-called internal method; no observable effect.)
- `Vote.comment_votes_by_user_for_story_hash` became unused after c05d00ad moved comment vote loading into `CommentVoteHydrator`. (Internal method removal; behavior changed by an earlier commit.)
- `Github.oauth_consumer` was added in fcb24439 but the GitHub flow used `Sponge` directly, so it was never used. (Never-called internal method; the GitHub flow is unchanged.)

Regression hints:

- Story domain links still navigate to the domain listing page
- Login still works and deleted users are not silently reactivated
- Mastodon author links on stories still render and point to the correct profile
- GitHub authentication/connection flow still completes
- Comment votes still load and display correctly on story pages
- Traffic/graph pages that used the traffic helper still render

</details>

<details><summary>Run 2: 0 claims, 0 assumptions</summary>

| # | Claim | Ref | Label | Grounded | Judge |
|---|---|---|---|---|---|

Not testable:

- Remove unreferenced dead code (Internal code cleanup with no user-visible behavior change)
- Found these leftovers using rubydex (Describes how the work was done, not observable behavior)
- `TrafficHelper::CACHE_FOR` became unused after e334ae41 stopped using time-based traffic cache keys. (Removal of an unreferenced constant; behavior changed by an earlier commit)
- `Keystore.find_or_create_key_for_update` became unused after b45d1750 switched the remaining code to `upsert`. (Unreferenced internal method; no browser-observable effect)
- `Keystore.decrement_value_for` and `decremented_value_for` ... don't seem to have ever had callers. (Never-called internal methods; no user-visible change)
- `Story#domain_search_url` became unused after f718da18 switched domain links to `for_domain_url`. (Domain link behavior was changed by an earlier commit, not this PR)
- `Story#update_availability` was added in 9c73c87d but never used. (Unreferenced internal method removal)
- `User#undelete!` became unused after 6fb659f8 stopped automatically reactivating deleted users on login. (Login reactivation behavior was changed by an earlier commit)
- `User#mastodon_acct` became unused after f1a253c1 started building the Mastodon author URL directly. (Mastodon URL behavior was changed by an earlier commit)
- `Vote#on_comment?` was added in bb5e53e9 but never used. (Unreferenced internal method removal)
- `Vote.comment_votes_by_user_for_story_hash` became unused after c05d00ad moved comment vote loading into `CommentVoteHydrator`. (Comment vote loading already moved by an earlier commit)
- `Github.oauth_consumer` was added in fcb24439 but the GitHub flow used `Sponge` directly, so it was never used. (Never-called internal method; GitHub flow unchanged)

Regression hints:

- Story pages and domain links (via for_domain_url) continue to work
- Comment voting and vote display on story pages still work
- Login for deleted/active users behaves as before
- Mastodon author links on stories still render correctly
- GitHub OAuth connect/login flow still works
- Traffic/cache-driven pages (e.g. homepage) still render

</details>

<details><summary>Run 3: 0 claims, 0 assumptions</summary>

| # | Claim | Ref | Label | Grounded | Judge |
|---|---|---|---|---|---|

Not testable:

- Remove unreferenced dead code (Internal code cleanup with no user-visible behavior change)
- Found these leftovers using rubydex (Describes the method of discovery, not app behavior)
- `TrafficHelper::CACHE_FOR` became unused after e334ae41 stopped using time-based traffic cache keys. (Removal of an unreferenced constant; not observable in a browser)
- `Keystore.find_or_create_key_for_update` became unused after b45d1750 switched the remaining code to `upsert`. (Internal method removal; behavior already changed by an earlier commit)
- `Keystore.decrement_value_for` and `decremented_value_for` came over in the initial Rails conversion (093747b7) and don't seem to have ever had callers. (Unreferenced methods; no observable effect)
- `Story#domain_search_url` became unused after f718da18 switched domain links to `for_domain_url`. (Internal method removal; domain link behavior changed in an earlier commit)
- `Story#update_availability` was added in 9c73c87d but never used. (Unreferenced method; no observable effect)
- `User#undelete!` became unused after 6fb659f8 stopped automatically reactivating deleted users on login. (Login reactivation behavior was changed by a prior commit, not this PR)
- `User#mastodon_acct` became unused after f1a253c1 started building the Mastodon author URL directly. (Mastodon URL behavior changed in an earlier commit; this is only method removal)
- `Vote#on_comment?` was added in bb5e53e9 but never used. (Unreferenced method; no observable effect)
- `Vote.comment_votes_by_user_for_story_hash` became unused after c05d00ad moved comment vote loading into `CommentVoteHydrator`. (Internal method removal; loading path already moved earlier)
- `Github.oauth_consumer` was added in fcb24439 but the GitHub flow used `Sponge` directly, so it was never used. (Unreferenced method; GitHub flow unchanged)

Regression hints:

- Story pages and domain links (for_domain_url) continue to work and navigate correctly
- Comment voting on story pages still loads and displays the current user's votes
- User login, including for deleted users, behaves as before (no automatic reactivation)
- Mastodon author links on stories still render correctly
- GitHub authentication/connection flow still works
- Traffic/caching-dependent pages still render normally

</details>

Reference:


## mealie-8363 (behavior-change, test)

mealie-recipes/mealie#8363 · 4 reference claims, 4 intent, 3 assumptions

| Run | Claims | Precision | Intent recall | Lures | Unmatched | Dupes | Ungrounded | Assumptions | Coverage | Defaults | App-context | Cost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 6 | 75% | 100% | 0 | 1 | 1 | 0 | 2 | 100% | 100% | 0% | $0.000 + $0.024 |
| 2 | 5 | 100% | 100% | 0 | 0 | 1 | 0 | 2 | 100% | 100% | 0% | $0.000 + $0.015 |
| 3 | 6 | 75% | 100% | 0 | 1 | 1 | 0 | 2 | 100% | 100% | 0% | $0.000 + $0.026 |

## memos-6187 (no-claims, test)

usememos/memos#6187 · 0 reference claims, 0 intent, 0 assumptions

| Run | Claims | Precision | Intent recall | Lures | Unmatched | Dupes | Ungrounded | Assumptions | Coverage | Defaults | App-context | Cost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 0 | n/a | n/a | 0 | 0 | 0 | 0 | 0 | n/a | n/a | n/a | $0.000 + $0.000 |
| 2 | 0 | n/a | n/a | 0 | 0 | 0 | 0 | 0 | n/a | n/a | n/a | $0.000 + $0.000 |
| 3 | 0 | n/a | n/a | 0 | 0 | 0 | 0 | 0 | n/a | n/a | n/a | $0.000 + $0.000 |

## memos-6335 (new-feature, test)

usememos/memos#6335 · 9 reference claims, 9 intent, 2 assumptions

| Run | Claims | Precision | Intent recall | Lures | Unmatched | Dupes | Ungrounded | Assumptions | Coverage | Defaults | App-context | Cost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 6 | 67% | 44% | 0 | 1 | 0 | 0 | 3 | 0% | 0% | n/a | $0.000 + $0.019 |
| 2 | 7 | 43% | 33% | 2 | 1 | 0 | 0 | 3 | 0% | 100% | n/a | $0.000 + $0.030 |
| 3 | 8 | 75% | 67% | 1 | 1 | 0 | 1 | 3 | 50% | 100% | n/a | $0.000 + $0.021 |

## uptime-kuma-7855 (vague, test)

louislam/uptime-kuma#7855 · 0 reference claims, 0 intent, 0 assumptions

| Run | Claims | Precision | Intent recall | Lures | Unmatched | Dupes | Ungrounded | Assumptions | Coverage | Defaults | App-context | Cost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 2 | 0% | n/a | 2 | 2 | 0 | 0 | 3 | n/a | n/a | n/a | $0.000 + $0.011 |
| 2 | 1 | 0% | n/a | 1 | 1 | 0 | 0 | 3 | n/a | n/a | n/a | $0.000 + $0.010 |
| 3 | 2 | 0% | n/a | 2 | 2 | 0 | 0 | 3 | n/a | n/a | n/a | $0.000 + $0.012 |
