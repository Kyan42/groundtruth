# Groundtruth — Product Requirements

## Problem

Code is cheap to write. Verifying it is not. For web apps, the most reliable check that a feature works is an end-to-end run in a real browser, and that check is still manual: a developer boots a dev server, clicks through the feature, and when it fails, goes back to the agent that wrote the code, re-prompts, reboots, and tries again.

Written end-to-end tests (Playwright) are brittle. They depend on stable selectors, have to be authored per feature, break when the UI changes, and are slow enough that running all of them on every PR is impractical. As more code is written by agents, regressions increase and the manual check does not scale.

## What Groundtruth does

Groundtruth is a GitHub App that runs on every PR. It:

1. Reads the PR's intent and turns it into testable claims.
2. Verifies those claims in a real browser against the PR.
3. Compiles the verified path into a deterministic Playwright script and stores it.
4. Selects existing scripts the PR's changes could have affected and replays them.
5. Posts results as a check on the PR and in a dashboard with video, per-step checks, and console/network logs.

Model judgment is spent once, when a claim is first verified. Every later run is plain Playwright.

## Concepts

**Claim.** One statement about the app that can be checked in a browser. Two fields: `kind` (visible, hidden, text, url, count, state, clipboard, network, persisted) and `what` (plain language). Example: `text: a toast reads "Project archived"`. A claim whose kind cannot be assigned is not browser-observable and is dropped.

**Browser journey.** One test: a starting state, an ordered list of user actions, and the claims checked after each action. Several claims per journey.

**Script.** The Playwright code for one journey. One journey → one script.

**Footprint.** The source files executed and routes visited when a script runs. Recorded during clean replay. Used to select regression journeys.

**Registry.** Journeys and scripts committed to the repo under `.e2e/`. They enter the registry when the PR merges. Footprints and run history are stored by the service, keyed by journey id and commit.

**Persona.** An account type (anonymous, member, admin) defined in the config with a way to create it and log in as it.

**Config.** A repo-level file describing how to install, start, and reach the app; the personas; and, later, data seeding functions.

## Flow per PR

### Step 0 — Detection

A webhook fires on PR opened and on new pushes. The evidence bundle is the PR body, linked issue, commit messages, and diff. The repo's agent instruction file asks the coding agent to include a "User intent" section in every PR body. Raw session transcripts are out of scope.

### Step 1 — Claim building

An agent reads the evidence and produces a list of claims. For a PR adding "archive a project":

1. `visible`: each project card has an Archive action
2. `visible`: choosing Archive opens a confirmation dialog
3. `hidden`: after confirming, the project is gone from the active list
4. `visible`: the project appears on the Archived page

**Contradiction check.** Each new claim is compared against registry claims with the same surface and persona. If the new claim describes different behavior for the same action, the old claim is superseded and is not run as a regression test on this PR. At generation time the agent is shown the action names already in use for that surface, so it reuses them instead of inventing synonyms.

### Step 1.1 — Developer review

Claims are posted as a PR comment in a fixed format. The developer edits the comment text directly. A slash command or reaction approves. Nothing runs before approval.

v1: feedback text → regenerate.

### Step 2 — Browser exploration

A sandbox checks out the PR head and starts the app from the config. The agent has three tool sets:

- Browser actions through Playwright: navigate, click, type, read the accessibility tree, screenshot.
- Setup functions: log in as a persona. v1: data factories.
- Sandbox controls: reset.

The agent finds a path that verifies each claim, checking claims as it goes. It uses role and accessible-name locators from the accessibility tree, not CSS selectors or generated ids. Setup functions can be called at any point during exploration; the starting state is not planned ahead.

Each claim ends with one status:

- **verified** — reached and held
- **failed** — reached, and the expected thing did not happen
- **blocked** — an earlier claim failed, so this one was never reached
- **unreachable** — the agent could not perform the step and cannot tell whether the feature is missing or it did not find it; reported with everything it tried
- **error** — sandbox, server, or setup failed; never reported as a claim failing

Output: per-claim status and a trace.

### Step 3 — Compile

From the trace:

- A journey file: setup calls separated from browser steps, claims attached to the step where they were verified. See *Journey file* below.
- A script assembled from the successful actions only, with an assertion per claim derived from its kind.

The script is replayed from a fresh sandbox two or three times. The footprint is recorded during these runs. If replay fails, the script is wrong, not the feature: compile retries up to a limit, then reports "verified, script not stored." On success, the journey file and script are committed to the PR branch.

v1: replay the script on main. New-feature claims should fail there. If one passes, report it: either it is not new or the assertion is too weak. `hidden` and `count` claims are always paired with a `visible` check on the page to avoid passing on a blank page.

### Step 4 — Regression

Input: the diff. Select registry journeys on the base branch whose footprint overlaps the changed files, minus any superseded in Step 1. Replay each on the PR with one retry.

1. Passes — done.
2. Fails — replay on main.
   - Fails on main too: pre-existing. Report, do not blame the PR. Mark broken-on-main and skip until main changes.
   - Passes on main: possible regression. v0: ask the developer, with the dashboard video, to choose one of three: regression (fail the check), intended change (retire the journey), or test is stale (re-explore from the journey file on the PR branch — Steps 2 and 3 again — and replace the script). v1: automate the decision.

v0: this step creates no new tests. v1: for touched pages with no journeys, generate existing-behavior claims from the page as it is on main — same Steps 1 through 3, authored and verified on main, then replayed on the PR.

### Subsequent pushes to an open PR

The PR's own journeys are treated like Step 4: replay, and re-explore on failure. If the developer edits the claims comment, run from Step 2 again.

### Reporting

A check on the PR with per-claim status. The dashboard shows a sidebar of journeys; for each, a video (the clean replay when one exists, the exploration otherwise), the steps with claims checked at each, and console and network events. Regression failures show PR and main side by side at the failing step. A failed claim can be exported — step, expected, observed, screenshot, console errors — in a format a coding agent can act on.

## Journey file

```yaml
id: projects.member.archive-project     # surface + persona + action
surface: /projects                      # URL path where the journey starts
persona: member                         # from config
setup:                                  # called before step 1
  - login_as: member
steps:
  - do: open the menu on a project card
    claims:
      - { kind: visible, what: the menu contains Archive }
  - do: choose Archive
    claims:
      - { kind: visible, what: a dialog asks to confirm }
  - do: confirm
    claims:
      - { kind: hidden, what: the project is gone from the active list }
      - { kind: text, what: a toast reads "Project archived" }
  - do: go to the Archived page
    claims:
      - { kind: visible, what: the project is listed }
script: .e2e/projects.member.archive-project.spec.ts
footprint: recorded by the service, keyed by commit
origin: { pr: 412 }
```

Journeys with a shared setup that could be created directly (v1 factories) are split into separate journeys at that point; otherwise steps stay together.

## Onboarding and config

On install, an onboarding agent scans the repo and drafts the config: install command, start command, a URL that means the app is ready, how to bring up the database, environment variables and secrets by name, and personas. The developer reviews it. The config is plain commands, run mechanically in every sandbox; no agent runs at boot. A boot failure is an `error` status and a prompt to fix the config.

Test accounts: preferred, a seed script that creates them fresh in each sandbox. Otherwise, secrets stored in the service's encrypted store, referenced by name, injected as environment variables.

v0: a minimal onboarding flow or a hand-written config.

## Sandbox

One container per run, from a prebuilt per-repo image with dependencies cached. Exploration uses one sandbox per agent. Replay uses N sandboxes in parallel, one dev server each.

v1: hermetic runs — third-party calls recorded to HAR during authoring and replayed, fixed clock, animations disabled.

## Scope

**v0** — Steps 0 through 3 with the status vocabulary; the check and dashboard; commit to branch and registry on merge; Step 4 selection and outcomes with the developer making the call; config by hand or minimal onboarding.

**v1** — Negative control on main; per-PR existing-behavior claims; data factories (one per entity type, parameterized, drafted from the data model); hermetic sandboxes; feedback-driven claim editing; automatic reconciliation in Step 4.

**Later** — Page objects (thin: locators and small actions per surface, promoted only when a second journey needs them); locator-only healing without re-exploration; transcript ingestion; drift detection beyond pass/fail.

## Risks

- **Booting arbitrary apps.** Databases, environment variables, and external services on boot. This is the first thing to validate with customers; nothing downstream runs without it.
- **Assertion quality.** A script can pass without checking anything. The negative control on main is the mitigation.
- **Unreachable vs. missing.** The agent cannot always tell; the report must carry enough evidence for a human to.
- **Regression breadth on day one is zero.** Coverage grows where the code changes. Say this plainly to customers.
- **Cost.** Exploration is expensive; replay is cheap. The registry is what makes the economics work, and it only pays off with history.
- **Concurrent PRs editing the same journey file** produce an ordinary git conflict the developer resolves.
