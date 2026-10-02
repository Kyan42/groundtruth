# twentyhq/twenty

Open-source CRM ("The open alternative to Salesforce, designed for AI"): NestJS/GraphQL server, React front, BullMQ worker, workflows and AI chat. ~57.7k stars, default branch `main`, last commit seen 2026-09-28. https://github.com/twentyhq/twenty

**Worth knowing first:** Twenty already runs its own AI browser QA on PRs. `.claude/skills/qa-scout/SKILL.md` describes "Browser QA of a PR against a running Twenty app, post-merge on main or pre-merge via the qa-scout label". It derives scenarios from the diff, drives a Playwright MCP browser as `tim@apple.dev`, and checks the DB and worker logs. `ci-e2e-main.yaml` runs it after the e2e suite. The repo is therefore a direct comparison point for Groundtruth, and its CI recipe is a proven no-Docker boot.

## Stack and services

- Nx + Yarn 4 monorepo. `"packageManager": "yarn@4.13.0"`, `yarnPath: .yarn/releases/yarn-4.13.0.cjs`, `nodeLinker: node-modules`, `enableScripts: false`, `enableHardenedMode: true`, `npmMinimalAgeGate: 3d` (.yarnrc.yml). Node `24.16.0` (.nvmrc); `engines.node: ^24.5.0` (package.json L21-22).
- `packages/twenty-server`: NestJS, TypeORM, PostgreSQL, Redis, GraphQL (CLAUDE.md). Targets: `start` = `rimraf dist && NODE_ENV=development nest start --watch`; `start:ci` = `NODE_ENV=development nest start`; `worker` = `nest start --watch --entryFile queue-worker/queue-worker`; `database:init` = `setup-db.js` + `database:migrate --include-slow`; `database:reset` = truncate + init + `cache:flush` + `workspace:seed:dev` (packages/twenty-server/project.json L70-145, L222-289).
- `packages/twenty-front`: React 18 + Vite (CLAUDE.md), dev server on :3001, `serve` target `npx serve -s build` (packages/twenty-front/project.json).
- Root `yarn start` = `concurrently 'nx run-many -t start -p twenty-server twenty-front' 'npx wait-on tcp:3000 && npx nx run twenty-server:worker'` (package.json L69).
- Postgres extensions created at init: `uuid-ossp`, `unaccent`, `citext`, `postgres_fdw` (packages/twenty-server/src/database/scripts/setup-db.ts:18-49). All ship with Ubuntu's `postgresql` package (contrib is bundled; guess for exact packaging).

| Service | Required? | How the repo's dev setup provides it | Source |
|---|---|---|---|
| Postgres 16 (dbs `default` + `test`, user/pass `postgres`/`postgres`) | required | `setup-dev-env.sh` prefers **local `pg_ctlcluster 16`**, falls back to docker compose `postgres:16`; CI uses `postgres:18` | packages/twenty-utils/setup-dev-env.sh; packages/twenty-docker/docker-compose.dev.yml; .github/workflows/ci-e2e-main.yaml |
| Redis (7 in compose, `--maxmemory-policy noeviction`) | required (cache, BullMQ queues) | local `redis-server` or compose | setup-dev-env.sh; docker-compose.dev.yml; server `.env.example` `REDIS_URL` |
| API server :3000 | required | `npx nx start twenty-server` | project.json; local-setup.mdx:285-308 |
| Front dev server :3001 | required in dev mode (or build and serve from :3000, see below) | `npx nx start twenty-front` | setup-dev-env.sh final echo; front `.env.example` `REACT_APP_SERVER_BASE_URL=http://localhost:3000` |
| Worker (BullMQ) | required for async work (timeline, workflows, emails, imports) (guess on the exact list) | `npx nx worker twenty-server` | package.json `start`; ci-e2e-main.yaml "Start worker" |
| ClickHouse | optional (analytics; `CLICKHOUSE_URL` commented out) | not in dev compose | server `.env.example` |
| SMTP | optional; `EMAIL_DRIVER=LOGGER` logs emails | env | server `.env.example` |

**Preview deployments:** no `vercel.json` or `netlify.toml`. GitHub deployments are only Mintlify docs staging (`gh api repos/twentyhq/twenty/deployments` → `staging - packages/twenty-docs`, `mintlify[bot]`). The app gets **its own ephemeral PR previews**: `.github/workflows/preview-env-dispatch.yaml` fires on `pull_request_target` for MEMBER/OWNER/COLLABORATOR authors, or when an outside PR is labeled `preview-app`, and dispatches to the public repo `twentyhq/ci-public`. There, `.github/workflows/preview-env.yaml` builds the server image with docker compose and exposes it through a `cloudflared` quick tunnel for up to `timeout-minutes: 310`. So Groundtruth wouldn't be the only preview here. Its value would be claim-checking on outside PRs that don't get previews by default.

## Running without Docker

Yes. It is first-class: `setup-dev-env.sh` checks `has_local_pg` (`pg_ctlcluster` + a v16 cluster) and `has_local_redis` (`redis-server` on PATH) **before** Docker, and runs `sudo pg_ctlcluster 16 main start`, `ALTER USER postgres PASSWORD 'postgres'`, and `sudo service redis-server start` (setup-dev-env.sh "start_pg"/"start_redis"). On Ubuntu 24.04, `apt install postgresql-16 redis-server` matches exactly what the script expects (guess that the Runloop image is noble).

Two ways to serve the UI:
- **Dev (two ports):** server :3000 + Vite front :3001. The browser calls the API at `REACT_APP_SERVER_BASE_URL`.
- **CI recipe (one port):** `NODE_ENV=production npx nx build twenty-front`, `npx nx build twenty-server`, then `cp -r packages/twenty-front/build packages/twenty-server/dist/front`, `npx nx run twenty-server:start:ci &`, wait for `/healthz`, then `npx nx run twenty-server:worker &`. It uses server env `.env.e2e-testing-server` with `FRONTEND_URL=http://localhost:3000` ("This server serves the front build, so both share one origin") (ci-e2e-main.yaml steps "Build frontend" through "Start worker"). This fits Groundtruth's one-port model at the cost of a production front build per boot.

Resource notes: CI runs on `ubuntu-latest-8-cores` with `NODE_OPTIONS=--max-old-space-size=10240`, and its yarn-install action deletes SDKs first to avoid "ENOSPC when restoring or linking a full Yarn node_modules" (.github/actions/yarn-install/action.yaml L10-20).

## Env vars and third-party services

- Server `.env.example`: `NODE_ENV=development`, `PG_DATABASE_URL=postgres://postgres:postgres@localhost:5432/default`, `REDIS_URL=redis://localhost:6379`, `APP_SECRET=replace_me_with_a_random_string` (a literal placeholder works in dev/CI), `SIGN_IN_PREFILLED=true`, `IS_WORKSPACE_CREATION_LIMITED_TO_SERVER_ADMINS=false`, `FRONTEND_URL=http://localhost:3001`.
- Front `.env.example`: `REACT_APP_SERVER_BASE_URL=http://localhost:3000`, `VITE_BUILD_SOURCEMAP=false`.
- `.env.e2e-testing-server` (CI): same but `IS_MULTIWORKSPACE_ENABLED=true` and `FRONTEND_URL=http://localhost:3000`.
- Optional third parties (all commented out): Google/Microsoft auth and Gmail/Outlook/calendar sync, Sentry, AI providers (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, ...), People Data Labs enrichment, AWS SES / Resend for campaigns (`EMAILING_DOMAIN_DRIVER=LOG` fakes it), Cloudflare, captcha, billing (`IS_BILLING_ENABLED=false`), `ENTERPRISE_KEY`. Core CRM works with none. AI chat/agents and email/calendar sync need real keys.

## Login in dev

- Seeded users (packages/twenty-server/src/engine/workspace-manager/dev-seeder/core/utils/seed-users.util.ts:24-80) **all share one bcrypt hash, commented `// tim@apple.dev`**, so every seeded user's password is `tim@apple.dev`:
  - `tim@apple.dev` (canImpersonate, canAccessFullAdminPanel)
  - `jony.ive@apple.dev`, `phil.schiler@apple.dev`, `jane.austen@apple.dev` (same server flags)
  - `scott.forstall@apple.dev` (no impersonate/admin panel)
  - Docs confirm: "log in using the default demo account: `tim@apple.dev` (password: `tim@apple.dev`)" (packages/twenty-docs/developers/contribute/capabilities/local-setup.mdx:301-302).
- `SIGN_IN_PREFILLED=true` prefills `tim@apple.dev` / `tim@apple.dev` on the login form (packages/twenty-front/src/modules/auth/sign-in-up/hooks/useSignInUpForm.ts:67-68). CLAUDE.md: "E2E login: click 'Continue with Email' and use the prefilled credentials."
- Workspaces: `Apple` (subdomain `apple`) and `YCombinator` (`yc`) (seeder-workspaces.constant.ts:36-47). `workspace:seed:dev` seeds both unless `--light` (data-seed-dev-workspace.command.ts:29-43).
- **Workspace roles in the full seed (what `database:reset` runs):** in Apple, **Jane = admin, Tim = a "limited" role, Phil = guest, Jony + random users = member, Scott = impersonate-only**. In `--light` mode Tim is admin (dev-seeder-permissions.service.ts:89-142). PRs touching settings/data-model/views may need the `jane.austen@apple.dev` persona, not Tim.
- CLI account creation: none found beyond the seed. Signup through the UI works when `IS_WORKSPACE_CREATION_LIMITED_TO_SERVER_ADMINS=false`.
- Email verification: `IS_EMAIL_VERIFICATION_REQUIRED=false` (commented default in server `.env.example`), and `EMAIL_DRIVER=LOGGER` writes emails to the log. Seeded users have `isEmailVerified: true`.
- Not SSO-only: email/password. Google/Microsoft/SSO are optional.
- Multi-workspace mode (`IS_MULTIWORKSPACE_ENABLED=true`, as in CI) routes workspaces by subdomain (local-setup.mdx:250). Whether the browser ends up on `apple.localhost:3000` is unverified (guess). Qa-scout just uses `http://localhost:3000` + "workspace `Apple`" (SKILL.md Inputs table).

## Seed and fixture data

- `npx nx database:reset twenty-server` (default configuration `seed`): `truncate-clickhouse.js`, `truncate-db.js`, `nx database:init`, `cache:flush`, `workspace:seed:dev` (project.json L261-289). Docs use this as the setup step (local-setup.mdx:282).
- Note that `setup-dev-env.sh` only runs `database:init` (schema, **no seed**), so a boot must run `database:reset` explicitly to get users.
- The data is rich: two workspaces, people/companies/opportunities, page layouts, navigation, message/calendar channels, agent chat seeds, billing customers (dev-seeder/core/constants/*, billing/utils/*).
- Re-runnable and it *is* a reset (truncate first). It depends on a server build (`dependsOn: ["build"]`) and replays migrations, so it's slow (guess: minutes). Whether it's safe while server + worker run is unknown. It flushes cache after, which suggests it's designed to work with a running app (guess).

## Draft .groundtruth.yml

```yaml
version: 1
runtime: { node: "24" }                  # .nvmrc 24.16.0
env:
  NODE_OPTIONS: --max-old-space-size=8192   # UNCERTAIN: CI uses 10240 on 8 cores
  NODE_ENV: development
  PG_DATABASE_URL: postgres://postgres:postgres@localhost:5432/default
  REDIS_URL: redis://localhost:6379
  APP_SECRET: groundtruth-dev-app-secret
  SIGN_IN_PREFILLED: "true"
  IS_WORKSPACE_CREATION_LIMITED_TO_SERVER_ADMINS: "false"
  FRONTEND_URL: http://localhost:3000     # single-origin CI recipe (.env.e2e-testing-server)
  IS_MULTIWORKSPACE_ENABLED: "false"      # UNCERTAIN: CI uses true; false avoids subdomain routing but untested with two seeded workspaces
setup:
  - sudo apt-get update -qq && sudo apt-get install -y -qq postgresql-16 redis-server   # UNCERTAIN: assumes Ubuntu 24.04 (PG16 in main archive)
  - sudo pg_ctlcluster 16 main start && sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'postgres';"
  - sudo service redis-server start
  - PGPASSWORD=postgres psql -h localhost -U postgres -c 'CREATE DATABASE "default";' || true
  - PGPASSWORD=postgres psql -h localhost -U postgres -c 'CREATE DATABASE "test";' || true
  - yarn --immutable                      # UNCERTAIN: node 24 ships corepack/yarn? may need `corepack enable` or `npm i -g yarn`; npmMinimalAgeGate 3d may fight a mirror
  - cp packages/twenty-front/.env.example packages/twenty-front/.env
  - cp packages/twenty-server/.env.e2e-testing-server packages/twenty-server/.env   # env above overrides FRONTEND_URL / multiworkspace (UNCERTAIN: precedence)
  - npx nx build twenty-shared
  - NODE_ENV=production npx nx build twenty-front      # slow; production front build
  - npx nx build twenty-server
  - npx nx run twenty-server:database:reset            # truncate + migrate + seed
  - rm -rf packages/twenty-server/dist/front && cp -r packages/twenty-front/build packages/twenty-server/dist/front
start: |
  sudo pg_ctlcluster 16 main start || true
  sudo service redis-server start || true
  npx nx run twenty-server:worker &
  npx nx run twenty-server:start:ci
port: 3000
ready: { path: /healthz, timeout_seconds: 900 }   # UNCERTAIN: /healthz is server-only; "/" would prove the front bundle is served
reset: npx nx run twenty-server:database:reset    # UNCERTAIN: runs against a live server/worker; slow
# personas:
#   tim:   { description: "Prefilled demo user; server admin (admin panel, impersonate) but LIMITED role in Apple workspace (full seed)", username: tim@apple.dev, password: tim@apple.dev }
#   jane:  { description: "Workspace admin of Apple (full seed)", username: jane.austen@apple.dev, password: tim@apple.dev }
#   jony:  { description: "Regular workspace member", username: jony.ive@apple.dev, password: tim@apple.dev }
#   phil:  { description: "Guest role", username: phil.schiler@apple.dev, password: tim@apple.dev }
```

## What the current format can't express

- [service-db] Needs Postgres 16 (the script checks for a v16 cluster) with `uuid-ossp`, `unaccent`, `citext`, `postgres_fdw`.
- [service-other] Needs Redis (with `noeviction` in compose).
- [multi-process] Server + BullMQ worker (+ front dev server in dev mode). Today everything is crammed into one `start` script with `&`.
- [multi-port] Dev mode needs :3001 (front) and :3000 (API) both reachable from the browser. The one-port alternative is the CI production-build trick.
- [prod-build] The single-origin setup requires a production `twenty-front` build copied into `server/dist/front`, so front edits don't hot-reload.
- [slow-build] Builds of twenty-shared, twenty-front (prod) and twenty-server, and large installs (CI frees disk to avoid ENOSPC). We'd want caching of node_modules, `.nx` cache and the seeded DB.
- [disk-heavy] (new) node_modules is big enough that CI deletes .NET/Android/GHC first (yarn-install/action.yaml L10-20). The sandbox disk size matters.
- [mirror] `.yarnrc.yml` `enableHardenedMode: true` + `npmMinimalAgeGate: 3d` + `yarn --immutable --check-cache` in CI. A package mirror without publish-time metadata or with different tarballs could fail installs (guess).
- [host-config] `FRONTEND_URL` / `REACT_APP_SERVER_BASE_URL` / `SERVER_URL` must match the browser origin. Multi-workspace mode uses subdomains (`apple.`, `yc.`).
- [persona-create] Accounts exist only after `workspace:seed:dev`. `setup-dev-env.sh` alone leaves an empty DB. Role nuance: the prefilled Tim is *not* workspace admin in the full seed.
- [reset-hard] Reset exists (`database:reset`) but needs a built server and replays migrations. Cost and live-app safety are unknown.
- [third-party] AI chat/agents, email/calendar sync, enrichment and campaigns need real provider keys.

## Difficulty

**medium.** The repo boots without Docker by design: its own setup script prefers apt Postgres 16 + Redis, and CI shows a clean one-port recipe with the front served from the server. Seeded users and a prefilled login remove the auth problem. The cost is heavy builds (three Nx builds, large installs, high memory and disk) and three processes. Only the full-seed role mapping (Tim limited, Jane admin) needs care when picking personas.

## Candidate PRs for evaluation

1. https://github.com/twentyhq/twenty/pull/26540, "Let Escape close the side panel from form fields", by thomtrp (GitHub shows CONTRIBUTOR, and the external-contributor auto-draft bot fired on it; likely core-adjacent (guess)). Claim: pressing Escape in a side-panel form field (record creation form, workflow steps, filters) now closes the panel, while an open dropdown still consumes the first Escape and IME composition is ignored. 13 reviews, and 6 commits that fixed issues before merge ("Skip Escape during IME composition...", "Drop the form field focus item on unmount instead of blurring...", "Test Escape on a dropdown opened from inside a form field"). Very browser-checkable. Persona: jane (or tim if the limited role allows record creation (guess)).
2. https://github.com/twentyhq/twenty/pull/26407, "fix(front): keep the record currency when a currency cell edit starts by typing", by Belkouche (outside contributor). The body gives a precise repro: record at `1 000 EUR`, field default USD, focus cell, type `5`, Enter; before it saved `5 USD`, after it keeps EUR. A crisp table-cell claim on seeded opportunities/companies (guess that a seeded currency field exists, e.g. opportunity amount). Single commit, so no in-PR fix. Persona: jane.
3. https://github.com/twentyhq/twenty/pull/26656, "Restore columns when resetting view grouping", by bosiraphael. The body has exact steps: Kanban grouped by Stage → change grouping to Company or table → back to Stage; columns must be recreated, and renaming a view must not reset column order/visibility. Server-side fix with a visible Kanban outcome and a regression claim. Single commit. Persona: jane (view editing likely needs workspace-admin/layout permissions (guess)).

## Sources

- gh api repos/twentyhq/twenty, commits/main, deployments, git tree (42,991 paths); repos/twentyhq/ci-public (+ .github/workflows/preview-env.yaml)
- CLAUDE.md, .nvmrc, .yarnrc.yml, package.json, .claude/skills/qa-scout/SKILL.md
- packages/twenty-utils/setup-dev-env.sh, packages/twenty-docker/docker-compose.dev.yml
- packages/twenty-server/.env.example, .env.e2e-testing-server, project.json, src/database/scripts/setup-db.ts, src/database/commands/data-seed-dev-workspace.command.ts
- packages/twenty-server/src/engine/workspace-manager/dev-seeder/core/utils/seed-users.util.ts, constants/seeder-workspaces.constant.ts, services/dev-seeder-permissions.service.ts
- packages/twenty-front/.env.example, project.json, src/modules/auth/sign-in-up/hooks/useSignInUpForm.ts
- packages/twenty-docs/developers/contribute/capabilities/local-setup.mdx
- .github/workflows/ci-e2e-main.yaml, preview-env-dispatch.yaml; .github/actions/yarn-install/action.yaml
- gh pr view 26540, 26407, 26401, 26656 (+ comments on 26540); gh pr list / gh search prs (merged, outside-contributor filter)
