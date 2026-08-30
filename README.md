# Groundtruth

Intent-aware browser verification for pull requests, powered by Runloop, Reflex, Axons, and Codex.

This prototype implements a real intent-to-browser vertical slice:

1. Ingest a public GitHub pull request and key a run by its exact head SHA.
2. Create a durable coordination Axon with immutable lifecycle events and contract SQL.
3. Launch a Reflex-managed Codex agent that extracts an `IntentSpec` from PR prose only.
4. Stream sanitized progress through the Groundtruth server and require human contract approval.
5. Validate a trusted AppProfile, AppMap, ImpactMap, and mission set against the exact PR base and head SHAs.
6. Start isolated base/head applications in Runloop Devboxes from the same pinned setup contract.
7. Start a separate browser Devbox with Runloop's public Codex mount, OpenAI Agent Gateway,
   Playwright, and Chromium.
8. Require Codex to inspect the live target application and produce a schema-valid frozen journey.
9. Replay intent journeys on head. For regression missions, discover on base, reset both fixtures,
   replay the identical journey on base and head, and compare only named normalized observations.
10. Persist branch-labeled assertions, actions, network entries, screenshots, trace, video, console
    and page errors, paired comparison rows, and append-only attempt evidence.

The dashboard projects only persisted run data. It keeps intent conformance and regression safety
separate, exposes covered/deferred/uncovered must claims, and does not generate placeholder journeys,
evidence, or verdicts.

## Setup

Requires Node.js 22 or newer.

```powershell
Copy-Item .env.example .env.local
npm install
npm run dev
```

Set `RUNLOOP_API_KEY`, `REFLEX_API_KEY`, and `REFLEX_ORG_ID` in `.env.local`. The Reflex
organization must expose a launchable Codex agent. `GITHUB_TOKEN` is optional but recommended when
the shared unauthenticated GitHub API quota is exhausted. A read-only token is sufficient. Secrets
remain server-side.

Setting `GITHUB_TOKEN` and `GITHUB_REPO` (`owner/repo`, must have `repo` scope and write access,
not a fine-grained token if the repo belongs to someone else) additionally turns on GitHub sync:
when a browser verification attempt finishes, the run posts or edits a single result comment on
the PR (issues endpoint, tracked per PR so a rerun edits it in place) and a `groundtruth/verify`
commit status against the PR's exact head SHA (Commit Status API, plain token auth, no GitHub App
needed). `state` is `failure` if any claim's verdict is violated, otherwise `success`.
`target_url` points at `DASHBOARD_URL/runs/<runId>` (`DASHBOARD_URL` falls back to
`GROUNDTRUTH_PUBLIC_BASE_URL`, so set it to wherever the dashboard is reachable by someone clicking
a link on GitHub, not `localhost`, unless you are the only one ever clicking it). GitHub sync is
best-effort: it can never fail a run, and doing nothing when unconfigured is intentional.

To make the check actually block merges, `groundtruth/verify` must be posted at least once (so it
appears in GitHub's branch-protection picker) before a branch protection rule can require it, and
enabling branch protection needs *admin* on the target repo, not just write — a collaborator will
get a 404 from the branch-protection endpoint, which is GitHub's way of hiding that the endpoint
exists rather than reporting 403.

Browser discovery uses an account OpenAI GatewayConfig and compatible Runloop Secret. The browser
Devbox receives only its box-bound gateway URL/token; the upstream API credential is never mounted
into the application Devboxes. A custom browser Blueprint is optional because the prototype can use
Runloop's default image and public `codex` agent mount. Name the compatible Secret with `openai`,
`codex`, or `gpt`; otherwise set `RUNLOOP_OPENAI_SECRET_NAME` to select it explicitly.

Trusted target-app configuration belongs at:

```text
config/apps/<owner>/<repo>/app-profile.json
config/apps/<owner>/<repo>/app-map.json
config/apps/<owner>/<repo>/impact-map.json
config/apps/<owner>/<repo>/test-missions.json
```

The checked-in `Kyan42/fernway` onboarding data is pinned to PR #4 base
`db5c5ae6e25fdc3947738b37327a626394420365` and head
`716b9f36e35f4f1cd1944e043bfdfb13f8f97ea4`. Missing/stale configuration, unsupported fork PRs,
agent contract failures, and integration failures produce explicit blockers rather than simulated
success. Regression verdicts are `preserved`, `regressed`, or `inconclusive`; setup/runner failures
are never product regressions. Devboxes are suspended on terminal paths.

## Validation

```powershell
npm test
npm run typecheck
npm run build
```
