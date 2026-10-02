# outline/outline
Real-time collaborative team knowledge base (React/MobX frontend, Koa + Sequelize backend, all TypeScript). ~40.7k stars, default branch `main`, last commit seen 2026-09-28. https://github.com/outline/outline

## Stack and services
- Node **26** (`.nvmrc`); `engines.node` is `">=20.12 <21 || 22 || 24 <24.17.0 || 26 <26.3.1"` (`package.json:44-46`). Yarn 4 (`.yarnrc.yml`; the AGENTS.md dependency section says to use yarn).
- The backend is Koa with Sequelize and Postgres (`AGENTS.md:1`). The frontend is Vite + React; the dev server runs on **:3001** (`vite.config.ts:42-45`).
- Every backend service runs in one Node process, selected with `--services`: `web, websockets, collaboration, worker, cron, admin` (`server/services/index.ts`, `docs/SERVICES.md`).
- Scripts: `yarn dev` runs all services on the built server (`package.json:13`). `yarn dev:watch` runs nodemon (rebuilds the server) plus `vite` (`package.json:14-15`). The Makefile `up` target runs `docker compose up -d redis postgres`, then `yarn install-local-ssl`, `yarn install --immutable` and `yarn dev:watch` (`Makefile:1-5`).

| Service | Required? | How the repo's dev setup provides it | Source |
|---|---|---|---|
| PostgreSQL (unpinned `postgres` image) + extensions `uuid-ossp`, `pg_trgm`, `unaccent` | required | root `docker-compose.yml` (user/pass/outline); CI uses the runner's preinstalled PG | `docker-compose.yml:7-15`, `.github/workflows/ci.yml:125-134`, `server/migrations/20231120074257-*.js:7`, `20250217012609-*.js:7`, `20240912222438-*.js:6` |
| Redis | required (queues, websockets, rate limiter, verification codes) | root `docker-compose.yml` | `docker-compose.yml:2-6`, `.env.development:4` |
| Worker / collaboration / websockets | required, but in-process (same Node process in `yarn dev`) | `--services=...` flag | `package.json:13`, `Procfile` |
| Vite dev server :3001 | required in dev mode. The Koa HTML loads scripts from `URL` with the port swapped to 3001 | `yarn vite:dev` | `server/routes/app.ts:28,127-134`, `vite.config.ts:42-47` |
| File storage | required. `FILE_STORAGE` defaults to **s3**; set `local` for dev | env | `server/env.ts:724-725`, `.env.sample:95-100` |
| SMTP | optional. In dev with no `SMTP_USERNAME` it creates an ethereal.email test account and logs emails | built in | `server/emails/mailer.tsx:13,58-79,149-164` |
| Local TLS (mkcert) | optional. The default dev URL is `https://local.outline.dev:3000` and needs mkcert certs; without certs, Vite and the server fall back to HTTP | `yarn install-local-ssl` | `.env.development:1`, `server/scripts/install-local-ssl.js`, `vite.config.ts:14-26`, `server/utils/ssl.ts` |

## Running without Docker
Yes. CI does it: it starts the runner's preinstalled PostgreSQL with `systemctl`, sets a password, runs `createdb` and then `yarn sequelize db:migrate` (`ci.yml:125-134`). On Ubuntu that means `postgresql` (which includes the contrib extensions `uuid-ossp`, `pg_trgm` and `unaccent`) and `redis-server` from apt. No pgvector or other non-contrib extensions were found in migrations. The code search for `CREATE EXTENSION` returned only those three plus the search-vector index migration.

Pending migrations are **run automatically** at server start unless `--no-migrate` is passed (`server/scripts/checkMigrations.ts:20-42`, called from `server/index.ts:35`). Setup therefore only needs `createdb`.

A production build (`yarn build`, then `yarn start`) serves the frontend from the same Koa port (guess: the `app.ts` dev branch at line 66 only applies when `isDevelopment`). That avoids the second port, at the cost of a long `vite build` (guess: several minutes and several GB of RAM). In production, set `FORCE_HTTPS=false`, because HTTPS enforcement is on by default in production only (`server/env.ts:366`, `server/services/web.ts:31,50`).

## Env vars and third-party services
Variables:
- `URL` (required). Vite's `allowedHosts` is restricted to its hostname (`vite.config.ts:15,46`).
- `SECRET_KEY`: exactly 64 hex chars, validated (`server/env.ts:70-74`).
- `UTILS_SECRET`: non-empty (`env.ts:80-81`).
- `DATABASE_URL`, `REDIS_URL` (`.env.development:3-4`).
- `FILE_STORAGE=local` plus `FILE_STORAGE_LOCAL_ROOT_DIR` (`.env.sample:95-100`).

The committed `.env.development` sets only `URL`, `DATABASE_URL`, `REDIS_URL`, `SMTP_FROM_EMAIL` and log/CSP flags, **not** `SECRET_KEY`. The env loader merges `.env`, then `.env.<NODE_ENV>`, then `.env.local`, and `process.env` wins (`server/utils/environment.ts:7-37`). So the Groundtruth `env:` block can override everything. CI uses a fixed `SECRET_KEY` (`ci.yml:14-15`), so a fixed value is fine for dev.

Third parties: every auth provider is OAuth/OIDC (Slack, Google, Azure, Discord, OIDC, GitHub, GitLab in `plugins/` and `.env.sample:169-207`), and all are optional. Dev email goes to ethereal.email, which needs internet (`mailer.tsx:327-344`); the plain text is also logged at debug level (`mailer.tsx:149-164`). S3 is optional.

## Login in dev
- **There are no passwords anywhere.** The sign-in methods are the SSO plugins, email magic link/OTP (`plugins/email/server/auth/email.ts`) and passkeys (`plugins/passkeys`). Email sign-in is enabled when SMTP is configured **or** in development (`plugins/email/server/index.ts:6`). This confirms the expected SSO/magic-link-only case.
- **First-run workspace setup (the only no-email path):** on a self-hosted instance with no teams and no SSO env configured, `auth.config` returns zero providers. Email is excluded because there's no team yet, and passkeys because none are registered (`server/models/helpers/AuthenticationHelper.ts:40-80`). The Login page then renders `WorkspaceSetup` (`app/scenes/Login/Login.tsx:256-271`), which posts to `/api/installation.create`. That creates the team plus an **Admin** user from name and email and **signs the user in immediately** (`server/routes/api/installation/installation.ts:17-52`). It only works while `Team.count() == 0` (`installation.ts:26-29`). Since every Groundtruth sandbox is fresh, one admin session can be created through the UI (or by a setup-time POST) per run.
- **Dev-only routes:** `developer.create_test_users` and `developer.create_test_notifications`. They're mounted only when `env.isDevelopment` (`server/routes/api/index.ts:132-134`) and guarded by `ENVIRONMENT === "development"` (`server/routes/api/developer/developer.ts:20-28`). Both **require an authenticated user** (`auth()`). `create_test_users` makes up to 100 active `member` users with random `@example.com` emails (`developer.ts:30-65`). These users still have no way to log in except email or passkey.
- **There is no dev login bypass, seed user or CLI account command.** `server/scripts/bootstrap.ts` only loads dotenv and the DB/Redis connections for scripts. `server/scripts/` otherwise holds data migrations and release tooling. There's no seed script in the tree.
- **Roles:** `admin`, `member`, `viewer`, `guest` (`shared/types.ts:2-7`), plus per-collection and per-document permissions (read / edit / manage).
- **Magic link:** `POST /auth/email` emails a link token or a 6-digit code (`email.ts:26-101`). The callback verifies it (`email.ts:103-215`). To log in as a second persona, something must read that email: the dev log line, the ethereal preview URL (`mailer.tsx:213-217`), or a local SMTP catcher.

## Seed and fixture data
None. There's no seed script or fixtures outside tests (`server/test/factories.ts` is test-only). `yarn db:reset` drops, creates and migrates (`package.json:29`), which needs the server stopped. First run gives an empty workspace. On first sign-in, content is whatever `teamCreator` provisions (guess: Outline historically creates a "Welcome" collection with onboarding docs; not verified here).

## Draft .groundtruth.yml
```yaml
version: 1
runtime: { node: "24" }       # UNCERTAIN: .nvmrc says 26; engines also allow 22 or 24 (<24.17.0)
env:
  NODE_ENV: production        # UNCERTAIN: production build chosen so one port serves everything (dev mode needs :3001 too)
  URL: http://localhost:3000  # UNCERTAIN: must be the browser-facing origin (cookies, redirects, Vite allowedHosts in dev)
  PORT: "3000"
  FORCE_HTTPS: "false"
  SECRET_KEY: F0E5AD933D7F6FD8F4DBB3E038C501C052DC0593C686D21ACB30AE205D2F634B   # value from ci.yml; any 64-hex works
  UTILS_SECRET: devutils123
  DATABASE_URL: postgres://outline:outline@127.0.0.1:5432/outline
  PGSSLMODE: disable          # production defaults to SSL for PG unless disabled (server/storage/database.ts:56,156-157; env.ts:180-187)
  REDIS_URL: redis://127.0.0.1:6379
  FILE_STORAGE: local
  FILE_STORAGE_LOCAL_ROOT_DIR: /tmp/outline-data
  RATE_LIMITER_ENABLED: "false"   # UNCERTAIN: avoids TenPerHour limit on /auth/email during repeated runs
setup:
  - sudo apt-get update -qq && sudo apt-get install -y -qq postgresql postgresql-contrib redis-server
  - sudo service postgresql start && sudo service redis-server start
  - sudo -u postgres psql -c "CREATE ROLE outline WITH LOGIN SUPERUSER PASSWORD 'outline'" && sudo -u postgres createdb -O outline outline
  - corepack enable || sudo npm i -g corepack && corepack enable     # UNCERTAIN: yarn 4 via corepack
  - yarn install --immutable
  - yarn build                                  # UNCERTAIN: slow (vite build + server build + i18n)
start: |
  mkdir -p /tmp/outline-data
  yarn start --services=web,websockets,collaboration,worker,cron   # migrations auto-run on boot
port: 3000
ready: { path: /_health, timeout_seconds: 900 }   # server/main.ts:78 (checks DB with SELECT 1)
# reset: none. No seed; `yarn db:reset` needs the server stopped
# personas:
#   admin:
#     description: First-run workspace admin. NOT a password login; the agent fills the
#       "Setup your workspace" form (Workspace name / Admin name / Admin email) on first visit,
#       which calls installation.create and signs in. Only possible once per fresh DB.
#     username: admin@example.com
#     password: null   # Outline has no passwords; would need a "first-run form" or "magic link via mail catcher" persona type
#   member:
#     description: Second user for permission claims. Must be invited by admin and sign in via
#       emailed magic link / 6-digit code; needs a readable mail catcher (e.g. SMTP_HOST=localhost + mailpit).
```
In dev-mode, the alternative `start` would be `yarn build:server && NODE_ENV=development yarn dev & yarn vite:dev` with `port: 3000`. The browser would then also have to load `http://localhost:3001/static/...` (`server/routes/app.ts:28,127-134`).

## What the current format can't express
- [sso-only] There's no password login anywhere. Sign-in is SSO, email magic link/OTP or passkey (`plugins/*`, `AuthenticationHelper.ts`). The `personas: {username, password}` shape doesn't fit.
- [no-password] (new tag) Personas would need a different login kind: "complete first-run workspace setup" (`installation.create`), "magic link read from a mail catcher", or a session cookie minted by a setup script (guess: `signIn` sets an `accessToken` cookie from a JWT; no CLI exists for this).
- [email-verify] Every login after the first, and every second persona, requires reading an email (link or 6-digit code) from logs, ethereal.email or a mail catcher.
- [persona-create] There's no seed or CLI for users. `developer.create_test_users` needs an authenticated admin and produces users who still can't log in without email.
- [seed-missing] No seed data; the workspace starts empty (aside from any default onboarding docs, unverified).
- [multi-port] In dev mode the page loads JS from the Vite server on :3001 (`server/routes/app.ts:28`). Only a production build avoids it.
- [prod-build] Using one port means `yarn build` and running with `NODE_ENV=production` + `FORCE_HTTPS=false`, which loses hot reload.
- [slow-build] A full `yarn build` (vite + server + i18n) on every fresh sandbox; caching is wanted (guess: many minutes).
- [host-config] `URL` must match the browser origin. It drives cookies and redirects, and in dev Vite's `allowedHosts` (`vite.config.ts:15,46`). The default `https://local.outline.dev:3000` also needs mkcert certs.
- [secret-gen] `SECRET_KEY` (64 hex) and `UTILS_SECRET` are required and absent from `.env.development`. A fixed value works.
- [service-db] Needs PostgreSQL with the contrib extensions (uuid-ossp, pg_trgm, unaccent).
- [service-other] Needs Redis.
- [runtime-lang] Not applicable (Node only), but `.nvmrc` is Node 26. Whether `runtime.node: "26"` is supported is unknown; engines excludes 24.17+.
- [reset-hard] There's no reset command that works with the server running.

## Difficulty
**hard.** Booting is straightforward: Postgres and Redis from apt, and migrations run themselves. Login is the problem. Outline has no passwords, so the only no-email path is the one-shot first-run workspace setup. Any second persona, and any re-login, needs a mail catcher the agent can read, and dev mode additionally needs a second port for Vite. Not blocked, because the first-run form gives an admin session on every fresh sandbox.

## Candidate PRs for evaluation
1. https://github.com/outline/outline/pull/12001, "feat: Allow comparing any two revisions in document history" by **RHawkins-Fisher** (fork, outside contributor).
   - What it changes: with "Highlight changes" on, a "Compare to" dropdown picks any revision to diff against. It's stored in a `?compareTo=` query param and defaults to "Previous revision".
   - Why it's a good case: in-PR fixes after review include "Force editor remount when comparison target changes" and "Don't show wrong diff while compareTo revision is loading" (14 reviews).
   - Persona: the single first-run admin. The agent must create a doc and edit it several times to get revisions.
2. https://github.com/outline/outline/pull/12690, "feat(editor): support image corner drag resizing with aspect ratio lock" by **UmbraCi** (fork, outside contributor).
   - What it changes: four corner handles on images, with proportional resizing locked to the aspect ratio.
   - Why it's a good case: in-PR fixes include "fix corner resize speed and sync inputs in real-time" and "improve corner resize cursor tracking" (7 reviews, 11 comments).
   - Browser-checkable claims need drag interactions and an uploaded image (local storage). Persona: admin.
3. https://github.com/outline/outline/pull/12597, "only users with 'manage' permissions can edit page permissions" by **orikad** (fork, outside contributor).
   - What it changes: the permissions submenu is hidden for editors and shown only for managers.
   - Why it's a good case: in-PR follow-ups include "Fix document permission test authentication" and "allow manage perms on archived docs".
   - It is the persona stress case: it needs **two users** (manager vs editor), so the second must log in via magic link. That makes it a good test of the [no-password]/[email-verify] gap.
- Alternate: https://github.com/outline/outline/pull/13032, "fix: Allow deleting failed and expired exports" by 47star. It's a clear expected/actual body, but producing an Error or Expired export in a browser is hard.

## Sources
- `.nvmrc`, `.env.development`, `.env.sample`, `Makefile`, `docker-compose.yml`, `Procfile`, `package.json`, `AGENTS.md`, `docs/SERVICES.md`, `.sequelizerc`
- `.github/workflows/ci.yml`
- `vite.config.ts`, `server/env.ts`, `server/utils/environment.ts`, `server/utils/ssl.ts`, `server/scripts/install-local-ssl.js`, `server/scripts/checkMigrations.ts`, `server/utils/startup.ts`, `server/index.ts`, `server/main.ts`, `server/services/index.ts`, `server/services/web.ts`, `server/routes/app.ts`, `server/routes/index.ts`, `server/routes/api/index.ts`
- Auth: `server/routes/api/installation/installation.ts`, `server/routes/api/developer/developer.ts`, `server/routes/api/auth/auth.ts`, `server/models/helpers/AuthenticationHelper.ts`, `plugins/email/server/index.ts`, `plugins/email/server/auth/email.ts`, `plugins/passkeys/server/index.ts`, `app/scenes/Login/Login.tsx`, `app/scenes/Login/components/WorkspaceSetup.tsx`, `server/emails/mailer.tsx`, `shared/types.ts`
- Migrations with `CREATE EXTENSION` (code search)
- https://docs.getoutline.com/s/hosting/doc/local-development-5hEhFRXow7 (WebFetch)
- PRs #12001, #12690, #12597, #13032, #13571, #13717 (gh pr view)
