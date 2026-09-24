# Exploration agent (PRD Step 2): plan

Given a booted app and the approved claims, an agent drives a real browser to check each claim and reports a status per claim, with evidence and a trace that Step 3 compiles into Playwright scripts.

## Statuses

- **verified**: reached, and the expected result held
- **failed**: reached, and the expected result didn't happen
- **blocked**: depends on a claim that failed
- **unreachable**: couldn't find a way to perform the action; reported with what was tried
- **error**: infrastructure problem; never counted against the PR

## Harness

- **Agent loop on our server** (Claude API with tool use). The agent never touches the browser; it asks for one tool at a time and sees text results (occasionally an image).
- **Browser:** Chromium driven by Playwright over the Chrome DevTools Protocol, on our side, reaching the app through the sandbox's token-protected tunnel. Moving the browser elsewhere is a later decision.
- **Tools:** `snapshot` (accessibility tree with throwaway refs like `e12`), `click` / `type` / `select` / `press` / `navigate` / `back` (by ref), `wait_for`, `screenshot` (on demand only), `start_journey`, `check`, `record_status`; later `login_as(persona)`.
- **Settling:** after each action the browser waits until no requests are pending and the page has stopped changing for 0.5s (capped at 12s). Playwright's `networkidle` only covers the first page load, so client-side navigation and data refreshes were read too early before this.
- **Model:** Opus 5 first; measure, then try cheaper models against the eval. Cost levers: prompt caching, clearing old snapshots, snapshots over screenshots.
- **Untrusted page content:** pages are rendered by PR code, so their text is data, never instructions. Every status cites observations.

## Stable locators

Refs are never saved. For every action and check, the recorder saves a locator, choosing the first candidate that matches exactly one element:

1. a developer-provided test id (`data-testid`)
2. role + accessible name (`getByRole('button', { name: 'Add to cart' })`)
3. form label or placeholder
4. any of the above scoped by a container when duplicates exist ("the Add to cart button in the card titled X")

Never CSS classes, generated ids, positions or XPath. Actions go through the stable locator (falling back to the ref, with a note, when the locator isn't unique), so the recorded locator is known to work.

## Checks

The agent decides what to check; our code decides whether it holds. `check` takes an assertion from a fixed menu, a target and an expected value, runs it as a real Playwright assertion (retrying for 5s), records the stable locator, expected and observed values at once, and draws a box plus a ✓/✗ banner into the video. Each check is one line of the compiled script.

- **Element:** visible, hidden, contains_text, has_text, count, value, disabled, enabled, checked, unchecked, expanded, collapsed, selected (aria), invalid (browser form validation)
- **Page:** url, request_sent, request_not_sent ("POST /api/cart", since the last action)
- **Targets:** a ref, or role + name, or text, optionally within a container ref (for absent elements and counting)

`record_status` must cite checks: verified needs passing ones, failed needs the one that failed. When no assertion can express a result (colours, chart shapes), the agent gives `unchecked_reason` and the verdict is shown as its judgment (`basis: observation`).

Known gaps, from mapping the eval cases' claims onto the menu: purely visual results (Actual's Sankey flows, Memos' highlighting) may have no stable element to check; decide per app once we see the markup. Risk to watch: a check that passes but doesn't test the claim. The base-commit eval run catches it (the check also passes on the old code).

## Journeys

Several claims per journey; one journey = one script = one clean replay video. The agent groups claims that share a path and starting state, and calls `start_journey` (fresh browser session, optional app-data reset, new recording) when a claim needs a different login or clean data. Short journeys limit "blocked" cascades. The exploration video (with dead ends) is kept for debugging; the dashboard shows the replay of the compiled script when one exists.

## Evals

- **Input:** the eval cases' reference claims (not extractor output), so exploration is measured on its own.
- **Run twice:** on the PR's commit (claims should be verified) and on the base commit (claims should be failed or unreachable).
- **A claim verified on both commits tests nothing:** flags weak claims and an agent that verifies too easily.
- **Disagreements are reviewed by a human with the video**, since a "failed" on the PR can be the agent's mistake or a real bug.
- **Measured:** status accuracy, steps, time, cost per PR; the share of verdicts resting only on observation (where the menu is thin); the share of checks that also pass on the base commit (checks that test nothing).
- **Cases:** cbay#1 (fully known), mealie-8363 (needs a login).

## Slices

1. **Tools and loop, one claim:** `npm run explore -- Kyan42/cbay#1 --claim 1` boots the app, runs the agent on one approved claim, prints every step.
2. **All approved claims:** statuses, trace file, screenshots, video, time and cost.
2b. **Checks and video overlays:** the check tool, settling, stable-locator actions, cursor and check boxes in videos, request log.
3. **Eval:** PR commit vs base commit on cbay and Mealie; `personas` in the config.
4. **Wire into approval:** per-claim statuses on the Groundtruth check.

Then Step 3: compile traces into Playwright scripts.
