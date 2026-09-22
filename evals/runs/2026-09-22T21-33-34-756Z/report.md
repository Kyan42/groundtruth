# Eval run 2026-09-22T21:33:34.708Z

Extractor: `claude-opus-5` · Judge: `claude-opus-5-5` · 4 cases × 1 runs · est. cost $0.23

## Summary

Pooled across cases per run; mean over runs (min–max). Partial matches count half.

| Metric | Value |
|---|---|
| Precision | 77% |
| Intent recall | 89% |
| Lures taken (count) | 0.0 |
| Unmatched claims (count, adjudicate) | 0.0 |
| Question coverage | 61% |
| App-context question coverage | 67% |
| Ungrounded sources (count) | 0.0 |

## actual-8780 (new-feature)

actualbudget/actual#8780 · 9 reference claims, 4 intent

| Run | Claims | Precision | Intent recall | Lures | Unmatched | Questions | App-context | Ungrounded |
|---|---|---|---|---|---|---|---|---|
| 1 | 6 | 75% | 88% | 0 | 0 | 50% | 0% | 0 |

<details><summary>Run 1: 6 claims, 6 questions</summary>

| # | Claim | Ref | Label | Grounded | Judge |
|---|---|---|---|---|---|
| m1 | Open a Sankey chart report and open its options/settings → (visible) A toggle option to show transfers is shown | c1 | partial | yes | Same options-menu check for a show-transfers option, but it doesn't specify the Spent view, which matters if the option only appears there. |
| m2 | On a Sankey chart in the Spent view, enable the show transfers toggle → (visible) Transfer flows appear as links/nodes in the Sankey diagram | c2 | partial | yes | Aimed at transfer flows appearing, but lacks the income/spending setup and the specific flow between the two accounts. |
| m3 | On a Sankey chart in the Spent view with the show transfers toggle enabled, where income lands in one account and spending happens from another (e.g. credit card) → (visible) The chart renders as a connected graph instead of disconnected parts | c2 | match | yes | Same setup with income in one account and spending from another; a connected graph amounts to a transfer flow linking the accounts. |
| m4 | On a Sankey chart with the show transfers toggle disabled → (hidden) No transfer flows are shown in the diagram | c3 | match | yes | With the toggle off, no transfer flows are shown. |
| m5 | Enable the show transfers toggle on a Sankey chart and reload the page → (persisted) The show transfers toggle is still enabled and transfers are still shown | c7 | partial | yes | Persistence across reload rather than the reference's save-to-dashboard-then-reload, a different action. |
| m6 | Enable the show transfers toggle on a Sankey chart for a budget that has transfers to off-budget accounts → (hidden) Transfers to off-budget accounts are not shown as flows in the chart | c5 | match | yes | Transfers involving off-budget accounts are not drawn as flows. |

Questions:

- q1: What is the exact label of the new toggle in the UI (e.g. 'Show transfers')? → covers c1
- q2: Where does the toggle appear - in the report's header menu, sidebar options, or chart settings? → covers c1
- q3: Is the toggle only available in the Spent view, and is it hidden/disabled in other Sankey views? → covers rq3
- q4: Is the toggle's default state off? → covers rq1, c4
- q5: Is the toggle setting saved with a saved report / persisted across reloads? → covers rq2, c7
- q6: How are transfer nodes labeled in the chart (account names?) → covers nothing

Not testable:

- Development aided by OpenCode / GPT-5 Mini. (Process note, not user-visible behavior)
- Manual testing with a specifically crafted tested budget and the full history of my own budget. (Describes how the change was tested)
- Fix recursion error happening in otherGrouping (Internal code fix; no stated user-visible symptom)
- Add release note (Repository/tooling change, not observable in the app)
- Relates to #1919, where it has been requested multiple times. (Context/reference, no specific behavior stated)
- Self-review has been performed - I have read every line of this diff and can explain what each change does and why it is needed (Checklist item, not user-visible behavior)

Regression hints:

- With the transfers option off, the Sankey chart behaves exactly as before the change
- Existing Sankey views other than Spent are unaffected
- Other report charts and their options continue to work unchanged

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
- rq1: Is the show-transfers option off by default? ("optional" suggests so, but it isn't stated.)
- rq2: Is the choice remembered (for a saved dashboard widget, or across reloads)?
- rq3: Does the option apply only to the Spent view, or to the Budgeted view as well?
- rq4: When money moves both ways between two accounts, should that be two flows or one net flow?
- rq5: Which transfers count? Off-budget ones are excluded per the commit; what about other cases?
- rq6: How does the option interact with "Group accounts in Spent view"?

## lobsters-2029 (vague)

lobsters/lobsters#2029 · 4 reference claims, 2 intent

| Run | Claims | Precision | Intent recall | Lures | Unmatched | Questions | App-context | Ungrounded |
|---|---|---|---|---|---|---|---|---|
| 1 | 3 | 83% | 100% | 0 | 0 | 80% | 100% | 0 |

<details><summary>Run 1: 3 claims, 3 questions</summary>

| # | Claim | Ref | Label | Grounded | Judge |
|---|---|---|---|---|---|
| m1 | On the submit story page, enter a title shorter than the allowed minimum length (e.g. one character), fill in the URL/text, and click the submit button → (network) No story-submission request is sent to the server; the page stays on the submit form | c1 | match | yes | Same action (submit with a too-short title) and same network result (no request sent); staying on the form is a harmless addition. |
| m2 | On the submit story page, enter a title shorter than the allowed minimum length and attempt to submit → (state) The title input is marked invalid by the browser's built-in form validation (minlength constraint) | c2 | match | yes | Same action, and the same result: the title field is marked invalid for being too short. |
| m3 | On the submit story page, enter a title that meets the minimum length along with the other required fields and submit → (state) The title input is not flagged as invalid and the form submits normally | c3 | partial | yes | Aims at valid titles being submitted, but it does not test the 3-character boundary and bundles a state check with submission. |

Questions:

- q1: What exact minimum length value was set on the title input (e.g. 3 characters)? → covers rq1, c3
- q2: Does the change also apply to the edit story page / story title field elsewhere, or only the new-story submission form? → covers rq4, c4
- q3: Should the browser-side message match the existing server-side title length error wording, or is the default browser message acceptable? → covers rq2

Not testable:

- Add minlength to title input in submit story page (The presence of the HTML minlength attribute itself is a markup/implementation detail; only the resulting browser validation behavior is observable to a user.)

Regression hints:

- Submitting a story with a valid-length title still works and creates the story as before.
- Existing server-side validation messages for title length continue to apply for cases that bypass client-side validation.
- Other fields on the submit story page (URL, description, tags) keep their current validation behavior.

</details>

Reference:

- c1 [intent] on the story submit page, enter a title that is too short and submit → (network) no request is sent to the server
- c2 [intent] on the story submit page, enter a title that is too short and submit → (state) the title field is marked invalid for being too short
- c3 [diff-only] on the story submit page, enter a 3-character title (other fields valid) and submit → (network) the form is sent to the server
- c4 [diff-only] edit an existing story, shorten its title below the minimum, and save → (network) no request is sent to the server
- rq1: What is the minimum title length?
- rq2: What should the user see for a too-short title - the browser's built-in warning or a custom message?
- rq3: Is this a new rule, or moving an existing server-side rule into the form? (It's the latter; that changes what a test must check.)
- rq4: Does this apply only to submitting a new story, or also to editing a story and suggesting a title?

## lobsters-2132 (behavior-change)

lobsters/lobsters#2132 · 4 reference claims, 3 intent

| Run | Claims | Precision | Intent recall | Lures | Unmatched | Questions | App-context | Ungrounded |
|---|---|---|---|---|---|---|---|---|
| 1 | 4 | 75% | 83% | 0 | 0 | 67% | 100% | 0 |

<details><summary>Run 1: 4 claims, 4 questions</summary>

| # | Claim | Ref | Label | Grounded | Judge |
|---|---|---|---|---|---|
| m1 | Submit a new story (fill in the form) and click Preview on the story submission page → (state) The "save" button in the previewed story's byline is disabled | c1 | match | yes | Same action and same result: the save button in the preview byline is disabled. |
| m2 | Submit a new story (fill in the form) and click Preview on the story submission page → (state) The "hide" button in the previewed story's byline is disabled | c2 | match | yes | Same action and same result: the hide button in the preview byline is disabled. |
| m3 | On the story submission preview, click the comments link in the previewed story's byline → (url) The browser stays on the story submission preview page (no navigation to an invalid URL) | c3 | partial | yes | It targets the comments label no longer linking, but it checks that clicking causes no navigation rather than that the label is plain text. |
| m4 | On the story submission preview, attempt to activate the "save" button in the byline → (network) No save request is sent (no 400 error response) | c1 | partial | yes | It relates to the disabled save button, but it checks for no network request rather than the button's disabled state. |

Questions:

- q1: Should the save/hide controls appear disabled (greyed-out button present) or be removed entirely from the preview? → covers rq1, c1, c2
- q2: Should the comments text still be displayed as plain text when the href is removed, or hidden? → covers rq3, c3
- q3: Does this also apply to the preview shown when editing an existing story, or only to new story submissions? → covers nothing
- q4: Are there other byline actions (e.g. upvote/flag) that should also be disabled in preview? → covers rq2

Not testable:

- Pretty straight forward, just disables the two forms or removes the link/href from the byline buttons (Describes the implementation approach (forms/href) rather than an observable user outcome on its own)

Regression hints:

- Previewing a story still renders the story byline with its save/hide controls and comments text visible
- Submitting the story after previewing still works normally
- On real story pages (not previews), save, hide and the comments link remain functional

</details>

Reference:

- c1 [intent] fill in the story submit form and click Preview → (state) the "save" button in the preview's byline is disabled
- c2 [intent] fill in the story submit form and click Preview → (state) the "hide" button in the preview's byline is disabled
- c3 [intent] fill in the story submit form and click Preview → (state) the comments label in the preview's byline is plain text, not a link
- c4 [discussion] as a moderator, fill in the story submit form and click Preview → (hidden) the "mod edit" link does not appear in the preview's byline
- rq1: In preview, should save and hide be shown disabled, or removed entirely? (The maintainer answered in the issue thread - "render them, disabled" - but extraction doesn't see issue comments.)
- rq2: Should other byline links in preview be handled too (e.g. moderators' "mod edit")? (This is what review ended up changing.)
- rq3: In preview, should the comments label be plain text or removed?

## lobsters-2172 (no-claims)

lobsters/lobsters#2172 · 0 reference claims, 0 intent

| Run | Claims | Precision | Intent recall | Lures | Unmatched | Questions | App-context | Ungrounded |
|---|---|---|---|---|---|---|---|---|
| 1 | 0 | n/a | n/a | 0 | 0 | n/a | n/a | 0 |

<details><summary>Run 1: 0 claims, 2 questions</summary>

| # | Claim | Ref | Label | Grounded | Judge |
|---|---|---|---|---|---|

Questions:

- q1: Is this PR intended to be purely non-functional (no user-visible change at all), or is any of the removed code expected to alter behavior on some page? → covers nothing
- q2: Are there any admin, moderation, or API endpoints that exercised any of these methods and might now behave differently? → covers nothing

Not testable:

- Remove unreferenced dead code (Pure internal cleanup; removing code that has no callers produces no user-visible change in the browser.)
- `TrafficHelper::CACHE_FOR` became unused after e334ae41 stopped using time-based traffic cache keys. (Describes an internal constant removal; the behavior change happened in an earlier commit (e334ae41), not this PR.)
- `Keystore.find_or_create_key_for_update` became unused after b45d1750 switched the remaining code to `upsert`. (Internal method removal with no callers; not observable in a browser.)
- `Keystore.decrement_value_for` and `decremented_value_for` came over in the initial Rails conversion (093747b7) and don't seem to have ever had callers. (Unreferenced internal methods; no user-facing behavior.)
- `Story#domain_search_url` became unused after f718da18 switched domain links to `for_domain_url`. (Domain link behavior was changed by an earlier commit (f718da18); this PR only deletes the dead method.)
- `Story#update_availability` was added in 9c73c87d but never used. (Method never had callers, so removing it changes nothing observable.)
- `User#undelete!` became unused after 6fb659f8 stopped automatically reactivating deleted users on login. (The login behavior change belongs to an earlier commit (6fb659f8); this PR only removes the unused method.)
- `User#mastodon_acct` became unused after f1a253c1 started building the Mastodon author URL directly. (Mastodon URL behavior was changed by an earlier commit (f1a253c1); no new user-visible change here.)
- `Vote#on_comment?` was added in bb5e53e9 but never used. (Unreferenced internal predicate; not observable.)
- `Vote.comment_votes_by_user_for_story_hash` became unused after c05d00ad moved comment vote loading into `CommentVoteHydrator`. (Comment vote loading was moved in an earlier commit (c05d00ad); this PR only deletes dead code.)
- `Github.oauth_consumer` was added in fcb24439 but the GitHub flow used `Sponge` directly, so it was never used. (Never-called method; the GitHub flow is unchanged by this PR.)
- Found these leftovers using [rubydex](https://github.com/shopify/rubydex) (Describes the tooling used to find the code, not a behavior change.)

Regression hints:

- Story pages continue to show domain links that navigate to the domain listing (via for_domain_url).
- Comment voting (upvote/downvote on comments) continues to display the current user's existing votes correctly on story pages.
- Story voting and vote counts continue to work as before.
- Logging in as a deleted user continues to behave as it did (no automatic reactivation).
- Mastodon author links on stories continue to render and point to the correct profile.
- GitHub OAuth connect/login flow continues to work.
- Traffic/cache-driven pages (e.g. front page ordering and traffic-based data) continue to render normally.
- Keystore-backed counters (e.g. user karma, comment/story counts) continue to display correct values.

</details>

Reference:

