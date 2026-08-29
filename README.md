# Groundtruth

Intent-aware browser verification for pull requests, powered by Runloop, Reflex, Axons, and Codex.

This prototype implements a real intent-to-browser vertical slice:

1. Ingest a public GitHub pull request and key a run by its exact head SHA.
2. Create a durable coordination Axon with immutable lifecycle events and contract SQL.
3. Launch a Reflex-managed Codex agent that extracts an `IntentSpec` from PR prose only.
4. Stream sanitized progress through the Groundtruth server and require human contract approval.
5. Validate a trusted AppProfile, AppMap, and TestMission against the exact PR base and head SHAs.
6. Start isolated base/head applications in Runloop Devboxes from the same pinned setup contract.
7. Start a separate browser Devbox with Runloop's public Codex mount, OpenAI Agent Gateway,
   Playwright, and Chromium.
8. Require Codex to inspect the live head application and produce a schema-valid frozen journey for
   one explicit approved claim.
9. Replay that journey mechanically and persist real assertions, actions, network entries,
   screenshots, trace, video, console output, and lifecycle state.

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

Browser discovery uses an account OpenAI GatewayConfig and compatible Runloop Secret. The browser
Devbox receives only its box-bound gateway URL/token; the upstream API credential is never mounted
into the application Devboxes. A custom browser Blueprint is optional because the prototype can use
Runloop's default image and public `codex` agent mount. Name the compatible Secret with `openai`,
`codex`, or `gpt`; otherwise set `RUNLOOP_OPENAI_SECRET_NAME` to select it explicitly.

Trusted target-app configuration belongs at:

```text
config/apps/<owner>/<repo>/app-profile.json
config/apps/<owner>/<repo>/app-map.json
config/apps/<owner>/<repo>/test-mission.json
```

The checked-in `Kyan42/fernway` onboarding data is pinned to PR #4 base
`db5c5ae6e25fdc3947738b37327a626394420365` and head
`716b9f36e35f4f1cd1944e043bfdfb13f8f97ea4`. Missing/stale configuration, unsupported fork PRs,
agent contract failures, and integration failures produce explicit blockers rather than simulated
success. Devboxes are suspended on terminal paths.

## Validation

```powershell
npm test
npm run typecheck
npm run build
```
