# Groundtruth

Intent-aware browser verification for pull requests, powered by Runloop, Reflex, Axons, and Codex.

This prototype implements one real foundation slice:

1. Ingest a public GitHub pull request and key a run by its exact head SHA.
2. Create a durable coordination Axon with immutable lifecycle events and contract SQL.
3. Launch a Reflex-managed Codex agent that extracts an `IntentSpec` from PR prose only.
4. Stream sanitized progress through the Groundtruth server and require human contract approval.

Impact mapping and browser execution are intentionally shown as **not run**. The prototype does not
generate placeholder missions, evidence, or verdicts.

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

Trusted target-app configuration belongs at:

```text
config/apps/<owner>/<repo>/app-profile.json
config/apps/<owner>/<repo>/app-map.json
```

Missing credentials or app configuration produce explicit setup states rather than simulated
success.

## Validation

```powershell
npm test
npm run typecheck
npm run build
```
