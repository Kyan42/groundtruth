# calcom/cal.com

**Heads-up: `calcom/cal.com` now redirects to `calcom/cal.diy`.** `gh api repos/calcom/cal.com` returns `full_name: calcom/cal.diy`. The README describes Cal.diy as "the community-driven, fully open-source scheduling platform — a fork of Cal.com with all enterprise/commercial code removed", MIT-licensed and self-host only (README.md:2-52). PR numbering carries on from cal.com (latest merged is #30190). Scheduling app (event types, booking pages, teams/orgs, calendar and payment integrations). ~48.7k stars, default branch `main`, last commit seen 2026-09-20 (54343aa, "fix: restore @ts-expect-error for CacheProvider type mismatch (#30171)"), last push 2026-09-26. https://github.com/calcom/cal.diy

## Stack and services

- TypeScript monorepo, Yarn 4 workspaces + turbo. `yarnPath: .yarn/releases/yarn-4.12.0.cjs`, `nodeLinker: node-modules` (.yarnrc.yml); root `engines.yarn >=4.12.0` (package.json ~L221). `turbo 2.7.1`, `typescript 5.9.3` (package.json devDependencies).
- Node: **no `.nvmrc` / `.node-version` in the tree**, even though the README says to run `nvm use` (README.md:117-127). README prerequisite is "Node.js (Version: >=18.x)" (README.md:73). CI installs `v20.x` (.github/actions/yarn-install/action.yml `node_version` default).
- Web app `apps/web`: Next.js 16.2.3, `"dev": "turbo run copy-app-store-static && next dev --turbopack"`, `"build": "next build && yarn sentry:release"`, `"start": "next start"` (apps/web/package.json L10-22, L110). Auth is NextAuth (packages/features/auth/lib/next-auth-options.ts).
- DB: Postgres via Prisma 6.16 (`packages/prisma/package.json`). Migrations: `yarn workspace @calcom/prisma db-deploy` (`prisma migrate deploy`) or `db-migrate` (`prisma migrate dev`). Seed: `yarn db-seed` → `prisma db seed` → `ts-node scripts/seed.ts` (packages/prisma/package.json `"prisma": {"seed": "yarn seed-basic"}`).
- Root `yarn dx` = turbo `dx` → `packages/prisma` `dx` = `db-setup` = `db-up` (docker compose Postgres 18 on :5450) + `db-deploy` + `db-seed` (packages/prisma/package.json scripts; packages/prisma/docker-compose.yml).
- `apps/api/v2` (NestJS API v2) is a separate optional service; it is the only thing in docker-compose.yml that uses Redis (docker-compose.yml `calcom-api`).

| Service | Required? | How the repo's dev setup provides it | Source |
|---|---|---|---|
| Postgres (CI uses 18; README says >=13) | required | `yarn dx` → docker compose `postgres:18` on :5450, trust auth, db `cal-saml`/`calendso` | packages/prisma/docker-compose.yml; .env.example:17-20; .github/workflows/e2e.yml `services.postgres` |
| Next.js web :3000 | required | `yarn dev` | package.json `dev`; apps/web/package.json |
| SMTP mail catcher (MailHog :1025 / UI :8025) | optional (booking/verification emails; E2E only when `E2E_TEST_MAILHOG_ENABLED=1`) | manual `docker run mailhog/mailhog` | README.md:248-255; .env.example:227-228; e2e.yml `services.mailhog` |
| Redis | optional (API v2 only) | docker-compose.yml `redis` | docker-compose.yml |
| API v2 (NestJS) | optional | `yarn dev:api` / compose `calcom-api` | package.json `dev:api`; docker-compose.yml |
| Prisma Studio :5555 | optional (inspecting data) | `yarn db-studio` | README.md:153, 270 |

**Preview deployments:** there is no `vercel.json` at the root or `netlify.toml`; `apps/web/vercel.json` only declares crons. `scripts/vercel.sh` is a Vercel ignore/branch script that provisions a Snaplet DB branch for previews (needs `VERCEL_TOKEN`, `SNAPLET_ACCESS_TOKEN`). `gh api repos/calcom/cal.diy/deployments` shows `vercel[bot]` Preview/Production deployments, the latest on **2026-04-15**, and none since. Recent PRs (#29648, #29593, #29282) have only a CodeRabbit status, no Vercel status. So Vercel previews existed while this was cal.com and appear to have stopped. Current PRs have no hosted preview, so Groundtruth would add something the repo lacks today.

## Running without Docker

Yes. Docker is only used to provide Postgres (and optionally MailHog/Redis); the README has a "Manual setup" path with a local Postgres (README.md:192-262).
- Postgres: Ubuntu 24.04 `apt install postgresql` gives 16 (guess, based on the stock Ubuntu version); README accepts >=13. No extensions found in the setup docs. CI uses `postgres:18` (e2e.yml) and PGDG apt can supply 18 if needed.
- Mail catcher: README only offers docker MailHog. Without Docker, use a single-binary catcher such as Mailpit (SMTP :1025, UI :8025) (guess, not in the repo). It is optional.
- Setup would be: install postgres → `createdb` → `cp .env.example .env` + fill secrets → `yarn` → `yarn workspace @calcom/prisma db-deploy` → `yarn db-seed` → `yarn dev`. CI does it the same way: `yarn install`, `yarn prisma generate`, `yarn db-seed`, then `yarn build` + `yarn e2e` (.github/actions/cache-db/action.yml L30; .github/actions/cache-build/action.yml L21-24; e2e.yml).
- Memory: README recommends `NODE_OPTIONS="--max-old-space-size=16384"` for dev (README.md:157); CI uses 4096 (e2e.yml env).

## Env vars and third-party services

From `.env.example` (483 lines) and README.md:104-110:
- Required: `DATABASE_URL`, `DATABASE_DIRECT_URL` (.env.example:17-20), `NEXTAUTH_SECRET` (`openssl rand -base64 32`), `CALENDSO_ENCRYPTION_KEY` (`openssl rand -base64 24`) (README.md:106-107; .env.example:59, 76), `NEXTAUTH_URL='http://localhost:3000'` (.env.example:56), `NEXT_PUBLIC_WEBAPP_URL='http://localhost:3000'` (.env.example:28). `.gitpod.yml` shows the whole recipe: it generates both secrets with openssl and rewrites `localhost:3000` to the public Gitpod hostname in `.env`.
- `ALLOWED_HOSTNAMES='"cal.local:3000","localhost:3000"'` (.env.example:48). Organizations need `NEXT_PUBLIC_WEBAPP_URL=http://app.cal.local:3000` plus `ORGANIZATIONS_ENABLED` (.env.example:25-27, 302).
- `.env.appStore` holds app-store integration keys. `scripts/seed-app-store.ts` loads it (L15) and seeds each integration only when its keys are present (e.g. L98-142).
- Third parties, all optional for core flows: Stripe (`STRIPE_PRIVATE_KEY`, `STRIPE_CLIENT_ID`, price IDs, .env.example:193-212), SendGrid, Google/Microsoft calendar and login (`GOOGLE_LOGIN_ENABLED=false`, `OUTLOOK_LOGIN_ENABLED=false` default, .env.example:118, 134), Daily video, Twilio, Sentry, PostHog, Intercom, Unkey rate limiting ("Not needed for testing or self-hosting", .env.example ~L366-370), Trigger.dev (`ENABLE_ASYNC_TASKER="false"`), Cloudflare Turnstile. Email goes to SMTP `localhost:1025` by default (.env.example:227-228).
- Dev works without real keys. Paid event types and calendar sync won't work without Stripe/Google keys.

## Login in dev

- Seeded accounts (scripts/seed.ts, created with `createUserAndEventType` in scripts/seed-utils.ts, which upserts the user and hashes the password, L61-73). README.md:143-151 lists the main ones:
  - `free@example.com` / `free` (seed.ts:921-926)
  - `pro@example.com` / `pro` (seed.ts:676-681; has many event types: 30min, 60min, paid, in-person, zoom, daily, yoga-class...)
  - `trial@example.com` / `trial` (seed.ts:900-905)
  - `admin@example.com` / `ADMINadmin2022!`, `role: "ADMIN"` (seed.ts:1004-1011)
  - `onboarding@example.com` / `onboarding` (onboarding incomplete, seed.ts:644-649)
  - also `delete-me`, `free-first-hidden`, `usa`, `teamfree`, `teampro`..`teampro4` (password = local part), `qa@example.com` / `qa` (seed.ts ~L1033-1034), plus an "Acme" organization whose seeded members get password = their username (seed.ts ~L264, ~L406).
- Admin caveat: an ADMIN without 2FA and a strong password becomes `INACTIVE_ADMIN` in production, but `if (isENVDev) return role;` keeps full admin in dev (next-auth-options.ts:255-272). Running a production build would downgrade the seeded admin (guess, from reading that code).
- CLI account creation: there's no user-create CLI. README suggests Prisma Studio with a bcrypt hash you enter by hand (README.md:264-276) or running the seed. For Groundtruth, the seed is the practical route.
- Roles: platform `UserPermissionRole` USER/ADMIN (next-auth-options.ts:72); team/org `MembershipRole` MEMBER/ADMIN/OWNER (seed.ts:28, 326).
- Email verification: signup calls `sendEmailVerification` (apps/web/app/api/auth/signup/handlers/selfHostedHandler.ts:187). It is gated by an `email-verification` Feature row that a migration inserts with `enabled = true` (packages/prisma/migrations/20230523101834_email_verification_feature_flag/migration.sql). Turning off that row should disable it (guess). Signup can be disabled entirely with `NEXT_PUBLIC_DISABLE_SIGNUP` (apps/web/app/api/auth/signup/route.ts:28-33). Seeded users don't hit verification.
- Not SSO-only: email/password credentials provider (next-auth-options.ts:290). Google/Azure AD/SAML are optional.

## Seed and fixture data

- `yarn db-seed` (turbo) → `scripts/seed.ts`. `runSeed()` = `mainAppStore()` + `main()` + `mainHugeEventTypesSeed()` (seed.ts:1377-1382). The data is rich: about 23 named users, teams "Seeded Team" / "Seeded Team (Marketing)" with collective and round-robin events, the Acme org with members, an API key for `owner1-acme` (seed.ts:523-527), and per-host locations.
- Re-runnable: users/passwords are upserted (seed-utils.ts:61-73); teams/orgs are skipped when they exist ("already exists, skipping", seed.ts:59, 151, 229). So re-seeding patches data back but does **not** delete rows the agent created.
- Full reset: `db-reset` = `db-nuke` (docker compose down --volumes) + `db-setup` (packages/prisma/package.json). That's Docker-only. Without Docker, use `yarn prisma migrate reset --force` then `yarn db-seed` (guess); migrations plus the seed are slow (CI caches a pg_dump of the seeded DB to avoid it: .github/actions/cache-db/action.yml L33-47).
- Cheaper reset idea: `pg_dump` right after the first seed, then `psql -f` to restore, like CI does (cache-db/action.yml).

## Draft .groundtruth.yml

```yaml
version: 1
runtime: { node: "20" }            # CI default (.github/actions/yarn-install/action.yml); no .nvmrc in repo
env:
  DATABASE_URL: postgresql://postgres:postgres@localhost:5432/calendso
  DATABASE_DIRECT_URL: postgresql://postgres:postgres@localhost:5432/calendso
  NEXTAUTH_URL: http://localhost:3000             # UNCERTAIN: must equal the origin the agent's browser uses
  NEXT_PUBLIC_WEBAPP_URL: http://localhost:3000   # UNCERTAIN: same; baked into client bundle
  NEXT_PUBLIC_WEBSITE_URL: http://localhost:3000
  NEXTAUTH_SECRET: groundtruth-dev-secret-not-for-prod-000000   # UNCERTAIN: any string should do in dev
  CALENDSO_ENCRYPTION_KEY: groundtruthgroundtruthgroundtr         # UNCERTAIN: README generates 32 chars via `openssl rand -base64 24`
  CALCOM_TELEMETRY_DISABLED: "1"
  NODE_OPTIONS: --max-old-space-size=8192         # UNCERTAIN: README suggests 16384
  HUSKY: "0"
setup:
  - sudo apt-get update -qq && sudo apt-get install -y -qq postgresql   # UNCERTAIN: Ubuntu's default major (16); CI uses 18
  - sudo service postgresql start
  - sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'postgres';"
  - sudo -u postgres createdb calendso || true
  - sudo npm install -g yarn                     # UNCERTAIN: classic yarn delegates to .yarn/releases/yarn-4.12.0.cjs via yarnPath; `corepack enable` is the alternative
  - cp .env.example .env && touch .env.appStore  # UNCERTAIN: env vars above override .env; .env.appStore may need to exist for seed-app-store
  - yarn install                                 # runs postinstall: husky + turbo post-install (prisma generate)
  - yarn workspace @calcom/prisma db-deploy
  - yarn db-seed
start: |
  sudo service postgresql start
  yarn dev
port: 3000
ready: { path: /api/version, timeout_seconds: 900 }   # UNCERTAIN: turbopack compiles routes on first hit; the login page is heavier than this route
reset: yarn db-seed    # UNCERTAIN: upserts seeded users/teams but does not remove rows the agent created
# personas:
#   pro:   { description: "Pro user with many event types", username: pro@example.com, password: pro }
#   free:  { description: "Free user", username: free@example.com, password: free }
#   admin: { description: "Instance admin (full admin only in NODE_ENV=development)", username: admin@example.com, password: "ADMINadmin2022!" }
#   team:  { description: "Member of Seeded Team", username: teampro@example.com, password: teampro }
#   onboarding: { description: "Account that has not finished onboarding", username: onboarding@example.com, password: onboarding }
```

## What the current format can't express

- [service-db] Needs a Postgres server (CI pins 18, README >=13). It's started with `service` in setup/start because there's no services key.
- [host-config] `NEXTAUTH_URL`, `NEXT_PUBLIC_WEBAPP_URL` and `ALLOWED_HOSTNAMES` must match the browser origin (.env.example:28, 48, 56). `.gitpod.yml` sed-rewrites them to the public hostname. Org features need an `app.cal.local` subdomain host.
- [slow-build] Very large monorepo install (the CI cache comment mentions "~1.2GB of cache data", yarn-install/action.yml). `next dev --turbopack` compiles each route on first request, and memory use is high (README.md:157). We'd want caching of node_modules and ideally of a seeded DB dump.
- [reset-hard] No non-Docker reset command. `db-reset` uses docker compose (packages/prisma/package.json). Re-seeding does not remove agent-created data. A pg_dump restore is the cheap path, but it isn't expressible as a first-class concept.
- [secret-gen] README/gitpod generate `NEXTAUTH_SECRET` and `CALENDSO_ENCRYPTION_KEY` with openssl. Static dev values probably work (guess), so this is minor.
- [service-other] Optional SMTP catcher for booking/verification emails. MailHog is docker-only in the README.
- [multi-port] Only if API v2 (`NEXT_PUBLIC_API_V2_URL="http://localhost:5555/api/v2"`, .env.example ~L360) or the mail UI (:8025) is needed. Core web flows use :3000 only.
- [email-verify] New signups trigger email verification (feature flag enabled by migration). Seeded personas avoid it.
- [third-party] Payments (Stripe), calendar sync (Google/Microsoft) and video (Daily) need real keys. PRs touching those can't be checked end-to-end.

## Difficulty

**medium.** It needs only Postgres, and the repo ships a rich, mostly idempotent seed with documented credentials, so login is solved. The risks are size and speed: a huge Yarn 4 install, heavy turbopack dev compiles and high memory. There is also URL/host coupling (`NEXTAUTH_URL`, `NEXT_PUBLIC_WEBAPP_URL`). Integration-heavy PRs (Stripe, calendars, video) stay unverifiable without real keys.

## Candidate PRs for evaluation

1. https://github.com/calcom/cal.diy/pull/29648, "fix(ui): render markdown headings properly in event descriptions", by GAURAV07C (author_association CONTRIBUTOR, outside). Injects Tailwind heading classes in `markdownToSafeHTML(.ts|Client.ts)` so "Large Heading" in event descriptions renders as a heading on the public booking page. Good case: fully visible (edit an event type description as `pro`, open `/pro/30min`, check heading size). It had 10 reviews, and in-PR commits reverted a scope-creep change ("revert external link changes") and then "update markdown heading styles" after review. Persona: `pro`.
2. https://github.com/calcom/cal.diy/pull/29282, "fix: persist requiresCancellationReason selection", by Maheshkumarjena (CONTRIBUTOR). The body gives exact before/after steps: Event Type → Advanced → select "Mandatory for attendee only" → save → refresh → the value must persist (previously it reverted to "Mandatory for host only"). A clean, checkable claim with a persistence check. Only one real commit plus a merge from main, so there's no in-PR bug fix. Persona: `pro`.
3. https://github.com/calcom/cal.diy/pull/29593, "fix: user table to load on scroll", by ChayanDass (CONTRIBUTOR, fixes #29590). Claims: a "Showing X of Y" counter, a loading state on first fetch, infinite scroll that loads more users, and spacing between search and table. Needs the **admin** persona (admin users list, `listPaginated.handler.ts`) and enough seeded users to scroll (the seed has ~23 plus org members, which may be too few to trigger paging (guess)). Two commits (fix + refactor into UserRepository), 9 reviews. It also tests the dev-only admin rule.

## Sources

- gh api repos/calcom/cal.com (redirect to cal.diy), repos/calcom/cal.diy/commits/main, repos/calcom/cal.diy/deployments, git tree (10,279 paths)
- README.md, package.json, .yarnrc.yml, .env.example, docker-compose.yml, .gitpod.yml, apps/web/package.json, apps/web/vercel.json, scripts/vercel.sh
- packages/prisma/package.json, packages/prisma/docker-compose.yml, packages/prisma/migrations/20230523101834_email_verification_feature_flag/migration.sql
- scripts/seed.ts, scripts/seed-utils.ts, scripts/seed-app-store.ts, scripts/seed-huge-event-types.ts
- packages/features/auth/lib/next-auth-options.ts, apps/web/app/api/auth/signup/route.ts, apps/web/app/api/auth/signup/handlers/selfHostedHandler.ts, apps/web/app/api/version/route.ts
- .github/workflows/e2e.yml, setup-db.yml; .github/actions/yarn-install, cache-db, cache-build action.yml
- gh pr view 29648, 29282, 29593, 29544, 29571 (+ statuses/check-runs for 29648, 29282, 29593, 21748); gh pr list --state merged (60 most recent)
