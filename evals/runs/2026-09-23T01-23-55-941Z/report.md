# Eval run 2026-09-23T01:23:55.936Z

Extractor: `claude-opus-5` (prompt 97c3dd65) · Judge: `claude-opus-5-5` (prompt 9b1faa42) · 8 cases × 3 runs

Cost: extraction $0.960 + judge $0.302 · spent by this run $1.262

## Summary

Pooled across cases per run; mean over runs (min–max). Partial matches count half. Test cases are held out from prompt tuning, so their details are only in results.json.

| Metric | dev (4 cases) | test (4 cases) |
|---|---|---|
| **Precision** | 90% (69%–100%) | 78% (64%–86%) |
| Intent recall | 74% (56%–89%) | 58% (46%–65%) |
| Lures taken (count) | 0.0 | 0.0 |
| Unmatched claims (count, adjudicate) | 0.0 | 1.3 (1.0–2.0) |
| Duplicate claims (count) | 0.3 (0.0–1.0) | 1.3 (1.0–2.0) |
| Ungrounded sources (count) | 0.0 | 0.0 |
| Assumption coverage | 52% (43%–57%) | 75% |
| App-context coverage | 33% | 0% |
| Default accuracy | 87% (80%–100%) | 100% |
| Off-topic assumptions (count) | 0.7 (0.0–2.0) | 3.0 (2.0–4.0) |
| **Extraction cost per PR** | $0.039 | $0.041 |
| Judge cost per PR (eval only) | $0.014 | $0.011 |

## actual-8780 (new-feature, dev)

actualbudget/actual#8780 · 9 reference claims, 4 intent, 5 assumptions

| Run | Claims | Precision | Intent recall | Lures | Unmatched | Dupes | Ungrounded | Assumptions | Coverage | Defaults | App-context | Cost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 4 | 50% | 38% | 0 | 0 | 1 | 0 | 3 | 33% | 50% | 0% | $0.037 + $0.028 |
| 2 | 3 | 100% | 75% | 0 | 0 | 0 | 0 | 3 | 50% | 100% | 0% | $0.050 + $0.024 |
| 3 | 3 | 100% | 75% | 0 | 0 | 0 | 0 | 3 | 50% | 50% | 0% | $0.046 + $0.022 |

<details><summary>Run 1: 4 claims, 3 assumptions</summary>

| # | Claim | Ref | Label | Grounded | Judge |
|---|---|---|---|---|---|
| m1 | Open a Sankey chart report and view its options/menu → (visible) A toggle option to show transfers is available | c1 | partial | yes | It checks that a show-transfers option is in the Options menu but doesn't specify the Spent view the reference requires. |
| m2 | On a Sankey chart report, enable the show-transfers toggle → (state) The toggle appears checked/enabled | c1 | partial | yes | It only checks that the toggle becomes checked, which barely touches the option's existence and verifies no transfer behavior. |
| m3 | On a Sankey chart with the show-transfers toggle enabled, view the chart → (visible) Transfer flows between accounts are drawn in the Sankey diagram | c2 | partial | yes | It is aimed at transfer flows being drawn, but lacks the specific setup where income arrives in one account and spending comes from a transfer-funded account. |
| m4 | On a Sankey chart with the show-transfers toggle off, view the chart → (hidden) No transfer flows are shown in the Sankey diagram | c3 | partial | yes | No transfer flows with the toggle off would also hold before this PR, so it wouldn't catch a broken toggle-off. |

| # | Assumption | Ref | Label | Judge |
|---|---|---|---|---|
| s1 | ☑ Enable the show-transfers toggle, then reload the page → (persisted) The toggle remains enabled — _Other Actual report options persist with the report; evidence is silent._ | c7 | partial | It checks that the toggle persists across a reload, rather than that the saved dashboard card still shows transfer flows. |
| s2 | ☑ Enable the show-transfers toggle on a Sankey chart that includes off-budget accounts → (hidden) Transfers to off-budget accounts are not drawn — _A commit says transfers to off-budget accounts are filtered out, but it may be internal._ | c5 | match | It checks the same exclusion of on/off-budget transfers from drawn flows when the option is enabled. |
| s3 | ☐ Open a Sankey chart in a view other than Spent (e.g. income/other view) → (hidden) The show-transfers toggle is not offered — _Title mentions Spent view only, but toggle placement/scope is unclear._ | a3 | partial | It is aimed at the option only applying to the Spent view, but checks that the toggle is hidden rather than that no flows are drawn after switching to Budgeted. |

Not testable:

- Development aided by OpenCode / GPT-5 Mini. (Process note, not user-visible behavior.)
- Manual testing with a specifically crafted tested budget and the full history of my own budget. (Describes how testing was done.)
- Fix recursion error happening in otherGrouping (Internal fix to new code introduced in this PR; no stated user-facing symptom.)
- Add release note (Repository metadata, not observable in the app.)
- Relates to #1919, where it has been requested multiple times. (Reference to an issue, not a specific behavior statement.)

Regression hints:

- The Sankey chart continues to render spending flows correctly when the transfers option is off, as before.
- Existing Sankey chart views and groupings remain functional.

</details>

<details><summary>Run 2: 3 claims, 3 assumptions</summary>

| # | Claim | Ref | Label | Grounded | Judge |
|---|---|---|---|---|---|
| m1 | Open the Sankey chart report in Reports → (visible) An option/toggle to show transfers is shown in the chart's options | c1 | match | yes | Both check that the Sankey report's options include a show-transfers toggle. |
| m2 | In the Sankey chart's Spent view, turn on the show transfers option → (visible) Transfer flows between accounts appear as links/nodes in the Sankey diagram | c2 | match | yes | Turning on show transfers in Spent view makes transfer flows between accounts appear, which is the same behavior c2 tests. |
| m3 | With the show transfers option turned on, view a budget that has transfers to an off-budget account → (hidden) No flow for the transfer to the off-budget account is shown in the diagram | c5 | match | yes | Both check that a transfer involving an off-budget account is not drawn when show transfers is on. |

| # | Assumption | Ref | Label | Judge |
|---|---|---|---|---|
| s1 | ☑ Open the Sankey chart for the first time → (state) The show transfers option is off by default — _Screenshots present 'With option off' as the existing/base appearance, but default is not stated._ | c4 | match | Both check that the show-transfers option is off by default on a fresh Sankey chart. |
| s2 | ☑ Turn on the show transfers option and reload the page → (persisted) The show transfers option is still on — _Report options in Actual are usually saved, but the evidence doesn't say._ | c7 | partial | It targets persistence but omits saving the report to the dashboard, and checks the option state rather than transfer flows on the dashboard card. |
| s3 | ☑ Switch the Sankey chart away from the Spent view → (hidden) The show transfers option has no effect outside the Spent view — _Commit says 'Add transfers as option to Spent view', scope to other views unclear._ | a3 | partial | It targets the option being limited to the Spent view but does not turn transfers on first and gives a vague 'no effect' result instead of 'no transfer flows are drawn'. |

Not testable:

- Development aided by OpenCode / GPT-5 Mini. (Statement about how the work was done, not user-visible behavior.)
- Manual testing with a specifically crafted tested budget and the full history of my own budget. (Describes testing process, not intended behavior.)
- Fix recursion error happening in otherGrouping (Internal fix within the new code; no described user-facing symptom.)
- Add release note (Repository housekeeping, not observable in the app.)
- No obvious regressions in affected areas / Self-review has been performed (Checklist items about process.)

Regression hints:

- With the show transfers option off, the Sankey chart renders exactly as before (income and spending flows only).
- Other Sankey views and existing report charts continue to render and function normally.

</details>

<details><summary>Run 3: 3 claims, 3 assumptions</summary>

| # | Claim | Ref | Label | Grounded | Judge |
|---|---|---|---|---|---|
| m1 | Open the Sankey chart report → (visible) A toggle option to show transfers is shown in the chart's options | c1 | match | yes | Both check that the Sankey report's options include a show-transfers toggle. |
| m2 | In the Sankey chart Spent view, enable the show transfers option → (visible) Transfer flows between accounts appear in the Sankey diagram | c2 | match | yes | Both check that enabling show transfers in Spent view draws transfer flows between accounts. |
| m3 | In the Sankey chart Spent view with the show transfers option enabled, in a budget that has transfers to an off-budget account → (hidden) No flow representing a transfer to an off-budget account is shown | c5 | match | yes | Both check that transfers involving an off-budget account are not drawn as flows when show transfers is on. |

| # | Assumption | Ref | Label | Judge |
|---|---|---|---|---|
| s1 | ☑ Open the Sankey chart report without changing anything → (state) The show transfers option is off by default — _Screenshots show 'With option off' as the baseline, but the default is never stated._ | c4 | match | Both check that the show-transfers option is off by default on a fresh Sankey report. |
| s2 | ☐ Switch the Sankey chart away from the Spent view (e.g. another grouping/view) → (hidden) The show transfers option is not shown — _Title scopes the feature to the Spent view, but it's unclear if the toggle is hidden elsewhere._ | a3 | partial | It targets the option being limited to Spent view, but checks that the toggle is hidden rather than that no transfer flows are drawn in Budgeted view. |
| s3 | ☐ Enable the show transfers option and reload the page → (persisted) The show transfers option is still enabled — _Evidence says nothing about saving/persisting this chart setting._ | c7 | partial | It targets persistence of show transfers, but reloads the page instead of saving to the dashboard and checking that the card shows transfer flows. |

Not testable:

- Fix recursion error happening in otherGrouping (Internal code fix with no described user-visible outcome)
- Add release note (Repository metadata, not observable in the app)
- Manual testing with a specifically crafted tested budget and the full history of my own budget. (Describes how the work was tested)
- Development aided by OpenCode / GPT-5 Mini. (Process note, not app behavior)
- Self-review has been performed - I have read every line of this diff (Checklist item, not observable behavior)

Regression hints:

- With the show transfers option off, the Sankey chart renders as before (income/spending flows without transfers)
- Other Sankey chart views and groupings continue to render without errors

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
- a3 ☑ turn on show transfers, then switch the report to the Budgeted view → (hidden) no transfer flows are drawn (the option only affects the Spent view)
- a4 ☐/☑ (= c6) turn on show transfers, with unequal transfers in both directions between the same two accounts in range → (visible) a single flow between them, sized to the net amount
- a5 ☐/☑ turn on both "Group accounts in Spent view" and show transfers → (visible) transfer flows are drawn between the account groups [app-context]

## lobsters-2029 (vague, dev)

lobsters/lobsters#2029 · 4 reference claims, 2 intent, 1 assumptions

| Run | Claims | Precision | Intent recall | Lures | Unmatched | Dupes | Ungrounded | Assumptions | Coverage | Defaults | App-context | Cost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 50% | 25% | 0 | 0 | 0 | 0 | 2 | 100% | 100% | 100% | $0.030 + $0.018 |
| 2 | 1 | 100% | 50% | 0 | 0 | 0 | 0 | 2 | 100% | 100% | 100% | $0.033 + $0.017 |
| 3 | 2 | 100% | 100% | 0 | 0 | 0 | 0 | 2 | 100% | 100% | 100% | $0.039 + $0.018 |

<details><summary>Run 1: 1 claims, 2 assumptions</summary>

| # | Claim | Ref | Label | Grounded | Judge |
|---|---|---|---|---|---|
| m1 | On the submit story page, fill in a title shorter than the minimum allowed length (plus a URL/description) and click the submit button → (state) The title field is flagged invalid by the browser and the page is not submitted | c2 | partial | yes | It checks that the browser flags the too-short title as invalid (c2), but it bundles this with a 'not submitted' result that belongs to c1. |

| # | Assumption | Ref | Label | Judge |
|---|---|---|---|---|
| s1 | ☑ On the submit story page, enter a title of normal length and submit → (url) The story is created and the browser navigates away from the submit form — _Evidence doesn't state the minlength value; assuming it matches the existing server-side minimum so ordinary titles still submit_ | c3 | partial | It aims at the valid-submission side, but uses a normal-length title rather than the 3-character boundary, so it would pass even without the change. |
| s2 | ☐ On the edit story page, enter a title shorter than the minimum and submit → (state) The title field is flagged invalid by the browser — _Title says 'submit story page' only; unclear whether a shared form makes it apply to edit too_ | c4 | match | The browser flagging the shortened title on edit verifies the same client-side blocking that c4 checks through the absence of a request. |

Regression hints:

- Submitting a story with a valid title still works and creates the story
- Server-side validation of title length still applies (e.g. when client-side validation is bypassed)

</details>

<details><summary>Run 2: 1 claims, 2 assumptions</summary>

| # | Claim | Ref | Label | Grounded | Judge |
|---|---|---|---|---|---|
| m1 | On the submit story page, enter a very short title (e.g. one or two characters) along with a URL and click the submit button → (state) The title field is reported as invalid (browser validation blocks submission) | c2 | match | yes | Both enter a too-short title on the submit page and check that the title field is marked invalid. |

| # | Assumption | Ref | Label | Judge |
|---|---|---|---|---|
| s1 | ☑ Open the edit page of an existing story and shorten the title to one or two characters, then save → (state) The title field is reported as invalid there too — _Submit and edit story pages typically share the same form markup, but the title only mentions the submit page_ | c4 | partial | Same edit-page situation, but it checks the field's invalid state rather than that no request is sent, and that state could also appear from a server-side rejection. |
| s2 | ☑ On the submit story page, enter a title of normal length and submit → (url) The story is created and the browser navigates away from the submit form — _Evidence implies only short titles are blocked; normal submission should be unaffected_ | c3 | partial | It checks that a valid title submits, but uses a normal-length title rather than the 3-character boundary, so it would pass without the change. |

Not testable:

- Add minlength to title input (The attribute itself is markup/internal; only the resulting validation behavior is observable)

Regression hints:

- Submitting a story with an acceptable-length title still works
- Existing server-side title length validation and its error message still apply

</details>

<details><summary>Run 3: 2 claims, 2 assumptions</summary>

| # | Claim | Ref | Label | Grounded | Judge |
|---|---|---|---|---|---|
| m1 | On the submit story page, enter a very short title (e.g. one character) along with a URL/description and click the submit button → (state) The title input is flagged invalid by the browser's form validation (minimum length not met) | c2 | match | yes | Both check that a too-short title on the submit page is marked invalid for minimum length. |
| m2 | On the submit story page, enter a very short title (e.g. one character) and click the submit button → (network) No story-creation request is sent to the server | c1 | match | yes | Both check that no request is sent when submitting a too-short title. |

| # | Assumption | Ref | Label | Judge |
|---|---|---|---|---|
| s1 | ☑ On the submit story page, enter a title that meets the server's minimum accepted title length → (network) The story-creation request is sent and the story is created — _Evidence doesn't state the minlength value; assumed it matches the server-side minimum so valid titles still submit_ | c3 | partial | It targets the accepted side of the boundary, but it doesn't pin the exact 3-character minimum and bundles story creation with the request. |
| s2 | ☐ On the edit story page for an existing story, clear the title to a very short value and save → (state) The title input is flagged invalid by the browser's form validation — _PR mentions only the submit story page, though the same form may be shared with editing_ | c4 | match | Both check that browser validation blocks saving a too-short title when editing a story; the invalid flag verifies the block equally well. |

Not testable:

- Add minlength to title input in submit story page (The attribute itself is markup/internal; only its resulting validation behavior is observable, which is captured in the claims)

Regression hints:

- Submitting a story with a normal, valid title still works and creates the story
- Other submit-form validations (e.g. required title, URL handling) continue to behave as before

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
| 1 | 3 | 100% | 100% | 0 | 0 | 0 | 0 | 2 | n/a | 100% | 0% | $0.038 + $0.011 |
| 2 | 3 | 100% | 100% | 0 | 0 | 0 | 0 | 2 | n/a | n/a | 0% | $0.042 + $0.011 |
| 3 | 3 | 100% | 100% | 0 | 0 | 0 | 0 | 2 | n/a | 100% | 0% | $0.039 + $0.014 |

<details><summary>Run 1: 3 claims, 2 assumptions</summary>

| # | Claim | Ref | Label | Grounded | Judge |
|---|---|---|---|---|---|
| m1 | On the story submission page, fill in a story URL and title and click Preview → (state) The "save" button in the previewed story's byline is disabled | c1 | match | yes | Same action and same disabled save button result. |
| m2 | On the story submission page, fill in a story URL and title and click Preview → (state) The "hide" button in the previewed story's byline is disabled | c2 | match | yes | Same action and same disabled hide button result. |
| m3 | On the story submission page, fill in a story URL and title and click Preview → (hidden) The comments text in the previewed story's byline is not a clickable link (no href) | c3 | match | yes | The comments label is not a link in the preview, which is the same check. |

| # | Assumption | Ref | Label | Judge |
|---|---|---|---|---|
| s1 | ☑ On the story submission page, click Preview → (visible) The save and hide controls are still shown in the preview byline, just non-functional — _Author says "disables" rather than removes, but hiding them entirely is an alternative fix_ | c1 | partial | Bundles save and hide, and mere visibility would hold without the disabling change. |
| s2 | ☑ Edit an existing story and click Preview → (state) The save and hide buttons in the preview byline are disabled — _Issue mentions only submit preview; edit preview likely shares the same view_ | c1 | partial | Different situation (editing an existing story) and bundles both save and hide. |

Not testable:

- Pretty straight forward, just disables the two forms (Describes how the change was implemented rather than a distinct user-observable outcome)

Regression hints:

- Previewing a story still renders the story preview (title, URL, tags) and allows submitting the story
- On real story listings and story pages, the save and hide buttons remain enabled and functional
- On real story listings, the comments link still navigates to the story's comments page

</details>

<details><summary>Run 2: 3 claims, 2 assumptions</summary>

| # | Claim | Ref | Label | Grounded | Judge |
|---|---|---|---|---|---|
| m1 | On the submit story page, fill in a title and URL and click Preview → (state) the "save" button in the previewed story's byline is disabled | c1 | match | yes | Same action and same disabled save button observable. |
| m2 | On the submit story page, fill in a title and URL and click Preview → (state) the "hide" button in the previewed story's byline is disabled | c2 | match | yes | Same action and same disabled hide button observable. |
| m3 | On the submit story page, fill in a title and URL and click Preview → (hidden) the comments entry in the previewed story's byline is not a clickable link (no href) | c3 | match | yes | Comments label not being a link verifies the same behavior. |

| # | Assumption | Ref | Label | Judge |
|---|---|---|---|---|
| s1 | ☑ On the submit story page, click Preview → (visible) the save and hide controls are still shown in the byline, just non-functional — _Evidence says "disables" rather than removes, but it also mentions removing the link/href_ | — | none | Visibility of save/hide controls is not addressed by any reference item, and it would hold without the change. |
| s2 | ☑ On the edit page of an existing story, click Preview → (state) the byline save/hide buttons are likewise disabled — _Issue only mentions submit previews; the same preview partial likely covers edit_ | — | none | The reference does not cover the edit-page preview situation. |

Not testable:

- Pretty straight forward (Comment on implementation effort, not user-observable behavior)

Regression hints:

- Save and hide buttons on real story pages (front page, story page) continue to work normally
- The story preview still renders the story title, URL and byline

</details>

<details><summary>Run 3: 3 claims, 2 assumptions</summary>

| # | Claim | Ref | Label | Grounded | Judge |
|---|---|---|---|---|---|
| m1 | On the story submission page, fill in a story and click Preview → (state) The "save" button in the previewed story's byline is disabled | c1 | match | yes | Same action and the same disabled save button result in the preview byline. |
| m2 | On the story submission page, fill in a story and click Preview → (state) The "hide" button in the previewed story's byline is disabled | c2 | match | yes | Same action and the same disabled hide button result in the preview byline. |
| m3 | On the story submission page, fill in a story and click Preview → (hidden) The comments item in the previewed story's byline is not a clickable link (no href) | c3 | match | yes | Checking that the comments item is not a link is the same as checking it is plain text. |

| # | Assumption | Ref | Label | Judge |
|---|---|---|---|---|
| s1 | ☑ Preview an existing story from the story edit page → (state) The byline save/hide buttons are disabled and the comments item is not a link — _Evidence says "when previewing" generally; unclear if edit-preview is in scope_ | c1 | partial | It previews from the edit page, a different situation, and bundles several byline results. |
| s2 | ☑ On the story submission page, click Preview → (visible) The save and hide buttons are still shown in the preview byline (disabled, not removed) — _Description says "disables the two forms", implying they remain visible_ | c1 | partial | It bundles save and hide, and the buttons being shown would also hold without the change. |

Not testable:

- just disables the two forms or removes the link/href (Describes the implementation approach rather than an observable outcome)
- return a error 400 when submitted (Describes the pre-PR broken behavior, not the intended new behavior)

Regression hints:

- On a real (already submitted) story listing or page, the save and hide buttons still work
- On a real story, the comments link still navigates to the story's comments page
- Previewing a story still renders the story preview with its byline

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
| 1 | 0 | n/a | n/a | 0 | 0 | 0 | 0 | 0 | n/a | n/a | n/a | $0.037 + $0.000 |
| 2 | 0 | n/a | n/a | 0 | 0 | 0 | 0 | 0 | n/a | n/a | n/a | $0.037 + $0.000 |
| 3 | 0 | n/a | n/a | 0 | 0 | 0 | 0 | 0 | n/a | n/a | n/a | $0.037 + $0.000 |

<details><summary>Run 1: 0 claims, 0 assumptions</summary>

| # | Claim | Ref | Label | Grounded | Judge |
|---|---|---|---|---|---|

Not testable:

- Remove unreferenced dead code (Pure internal code removal; no user-visible behavior change in the browser.)
- Found these leftovers using rubydex (Describes how the work was done, not a behavior.)
- `TrafficHelper::CACHE_FOR` became unused after e334ae41 stopped using time-based traffic cache keys. (Removal of an unused constant; the behavior change happened in an earlier commit.)
- `Keystore.find_or_create_key_for_update` became unused after b45d1750 switched the remaining code to `upsert`. (Internal method removal, not observable in a browser.)
- `Keystore.decrement_value_for` and `decremented_value_for` came over in the initial Rails conversion (093747b7) and don't seem to have ever had callers. (Methods never called; removing them has no user-visible effect.)
- `Story#domain_search_url` became unused after f718da18 switched domain links to `for_domain_url`. (Behavior was changed by an earlier commit; this PR only deletes unused code.)
- `Story#update_availability` was added in 9c73c87d but never used. (Unused method removal; no observable result.)
- `User#undelete!` became unused after 6fb659f8 stopped automatically reactivating deleted users on login. (The login behavior change came from an earlier commit, not this PR.)
- `User#mastodon_acct` became unused after f1a253c1 started building the Mastodon author URL directly. (Behavior was changed by an earlier commit; this PR only deletes unused code.)
- `Vote#on_comment?` was added in bb5e53e9 but never used. (Unused method removal; no observable result.)
- `Vote.comment_votes_by_user_for_story_hash` became unused after c05d00ad moved comment vote loading into `CommentVoteHydrator`. (Behavior was moved in an earlier commit; removal is internal.)
- `Github.oauth_consumer` was added in fcb24439 but the GitHub flow used `Sponge` directly, so it was never used. (Unused method removal; the GitHub flow is unchanged.)

Regression hints:

- Story domain links continue to work and point to the domain listing page
- Comment voting on story pages continues to work and shows the user's existing votes
- Login/authentication continues to work, including deleted users not being silently reactivated
- Mastodon author links on stories still render correctly
- GitHub OAuth connect/login flow still works
- Traffic-based caching/homepage rendering still works

</details>

<details><summary>Run 2: 0 claims, 0 assumptions</summary>

| # | Claim | Ref | Label | Grounded | Judge |
|---|---|---|---|---|---|

Not testable:

- Remove unreferenced dead code (Internal code removal with no user-visible change in the browser)
- Found these leftovers using rubydex (Describes how the work was done, not observable behavior)
- `TrafficHelper::CACHE_FOR` became unused after e334ae41 stopped using time-based traffic cache keys. (Refers to behavior changed by an earlier commit; the removal itself is internal)
- `Keystore.find_or_create_key_for_update` became unused after b45d1750 switched the remaining code to `upsert`. (Internal method removal, not observable in a browser)
- `Keystore.decrement_value_for` and `decremented_value_for` came over in the initial Rails conversion (093747b7) and don't seem to have ever had callers. (Unreferenced internal methods; no user-facing effect)
- `Story#domain_search_url` became unused after f718da18 switched domain links to `for_domain_url`. (Domain link behavior was changed by an earlier commit, not this PR)
- `Story#update_availability` was added in 9c73c87d but never used. (Unreferenced internal method; no user-facing effect)
- `User#undelete!` became unused after 6fb659f8 stopped automatically reactivating deleted users on login. (Login reactivation behavior was changed by an earlier commit, not this PR)
- `User#mastodon_acct` became unused after f1a253c1 started building the Mastodon author URL directly. (Mastodon URL behavior was changed by an earlier commit, not this PR)
- `Vote#on_comment?` was added in bb5e53e9 but never used. (Unreferenced internal method; no user-facing effect)
- `Vote.comment_votes_by_user_for_story_hash` became unused after c05d00ad moved comment vote loading into `CommentVoteHydrator`. (Internal refactor completed in an earlier commit)
- `Github.oauth_consumer` was added in fcb24439 but the GitHub flow used `Sponge` directly, so it was never used. (Unused internal constant; the GitHub flow is unchanged)

Regression hints:

- Story domain links continue to work and lead to the domain listing page
- Login for deleted/active users continues to behave as before (no automatic reactivation)
- Comment and story voting continue to work, with existing votes shown correctly on story pages
- GitHub account connect/authentication flow continues to work
- Mastodon author links on stories continue to render correctly
- Traffic/frontpage caching pages continue to load normally

</details>

<details><summary>Run 3: 0 claims, 0 assumptions</summary>

| # | Claim | Ref | Label | Grounded | Judge |
|---|---|---|---|---|---|

Not testable:

- Remove unreferenced dead code (Pure code removal of unreferenced methods/constants; no user-visible behavior change in a browser.)
- Found these leftovers using rubydex (Describes the tooling used to find the code, not app behavior.)
- `TrafficHelper::CACHE_FOR` became unused after e334ae41 stopped using time-based traffic cache keys. (Behavior was changed by an earlier commit, not this PR; the constant is unreferenced.)
- `Keystore.find_or_create_key_for_update` became unused after b45d1750 switched the remaining code to `upsert`. (Internal method removal; behavior changed by an earlier commit.)
- `Keystore.decrement_value_for` and `decremented_value_for` ... don't seem to have ever had callers. (Never-called internal methods; no observable effect.)
- `Story#domain_search_url` became unused after f718da18 switched domain links to `for_domain_url`. (Behavior changed by an earlier commit; removal is internal.)
- `Story#update_availability` was added in 9c73c87d but never used. (Never-called internal method.)
- `User#undelete!` became unused after 6fb659f8 stopped automatically reactivating deleted users on login. (Login reactivation was already removed by an earlier commit.)
- `User#mastodon_acct` became unused after f1a253c1 started building the Mastodon author URL directly. (Behavior changed by an earlier commit; removal is internal.)
- `Vote#on_comment?` was added in bb5e53e9 but never used. (Never-called internal method.)
- `Vote.comment_votes_by_user_for_story_hash` became unused after c05d00ad moved comment vote loading into `CommentVoteHydrator`. (Behavior changed by an earlier commit; removal is internal.)
- `Github.oauth_consumer` was added in fcb24439 but the GitHub flow used `Sponge` directly, so it was never used. (Never-called internal method; GitHub flow unaffected.)

Regression hints:

- Story domain links still work and lead to the domain page (for_domain_url)
- Comment vote state still loads and displays correctly on story pages
- Mastodon author links on stories still render correctly
- GitHub account connect/authentication flow still works
- Traffic-based caching / homepage rendering still works
- Keystore-backed counters (e.g. karma, counts) still update correctly
- Deleted users are still not automatically reactivated on login

</details>

Reference:


## mealie-8363 (behavior-change, test)

mealie-recipes/mealie#8363 · 4 reference claims, 4 intent, 3 assumptions

| Run | Claims | Precision | Intent recall | Lures | Unmatched | Dupes | Ungrounded | Assumptions | Coverage | Defaults | App-context | Cost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 4 | 100% | 100% | 0 | 0 | 0 | 0 | 2 | 100% | 100% | 0% | $0.048 + $0.014 |
| 2 | 6 | 67% | 88% | 0 | 1 | 1 | 0 | 2 | 100% | 100% | 0% | $0.044 + $0.024 |
| 3 | 5 | 100% | 100% | 0 | 0 | 1 | 0 | 2 | 100% | 100% | 0% | $0.052 + $0.019 |

## memos-6187 (no-claims, test)

usememos/memos#6187 · 0 reference claims, 0 intent, 0 assumptions

| Run | Claims | Precision | Intent recall | Lures | Unmatched | Dupes | Ungrounded | Assumptions | Coverage | Defaults | App-context | Cost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 0 | n/a | n/a | 0 | 0 | 0 | 0 | 0 | n/a | n/a | n/a | $0.017 + $0.000 |
| 2 | 0 | n/a | n/a | 0 | 0 | 0 | 0 | 0 | n/a | n/a | n/a | $0.017 + $0.000 |
| 3 | 0 | n/a | n/a | 0 | 0 | 0 | 0 | 0 | n/a | n/a | n/a | $0.019 + $0.000 |

## memos-6335 (new-feature, test)

usememos/memos#6335 · 9 reference claims, 9 intent, 2 assumptions

| Run | Claims | Precision | Intent recall | Lures | Unmatched | Dupes | Ungrounded | Assumptions | Coverage | Defaults | App-context | Cost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 6 | 75% | 44% | 0 | 1 | 1 | 0 | 3 | 50% | 100% | n/a | $0.069 + $0.018 |
| 2 | 5 | 60% | 28% | 0 | 1 | 1 | 0 | 3 | 50% | 100% | n/a | $0.083 + $0.018 |
| 3 | 6 | 75% | 50% | 0 | 1 | 0 | 0 | 3 | 50% | 100% | n/a | $0.077 + $0.019 |

## uptime-kuma-7855 (vague, test)

louislam/uptime-kuma#7855 · 0 reference claims, 0 intent, 0 assumptions

| Run | Claims | Precision | Intent recall | Lures | Unmatched | Dupes | Ungrounded | Assumptions | Coverage | Defaults | App-context | Cost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 0 | n/a | n/a | 0 | 0 | 0 | 0 | 2 | n/a | n/a | n/a | $0.023 + $0.008 |
| 2 | 0 | n/a | n/a | 0 | 0 | 0 | 0 | 2 | n/a | n/a | n/a | $0.024 + $0.008 |
| 3 | 0 | n/a | n/a | 0 | 0 | 0 | 0 | 2 | n/a | n/a | n/a | $0.023 + $0.008 |
