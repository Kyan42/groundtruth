# documenso/documenso

Open-source DocuSign alternative: upload a PDF, place fields, send to recipients, sign, then seal with a PKCS#12 certificate. ~15.2k stars, default branch `main`, last commit seen 2026-09-26 (push 2026-09-28). https://github.com/documenso/documenso

## Stack and services

- TypeScript monorepo, **npm** workspaces + turbo (`package.json` `workspaces: apps/*, packages/*`). `"packageManager": "npm@11.19.1"`, `engines: { npm: ">=11.17.0", node: ">=24.0.0" }` (package.json). README says "Node.js (v24 or above)" (README.md:110). CI uses `v24.x` + `corepack enable npm` (.github/actions/node-install/action.yml). No `.nvmrc`.
- `.npmrc`: `legacy-peer-deps = true`, `prefer-dedupe = true`, `min-release-age = 7`.
- Main app `apps/remix`: **React Router v7 (Remix) + Hono server, not Next.js**. `"dev": "npm run with:env -- react-router dev"`, `"start": "... cross-env NODE_ENV=production node build/server/main.js"`, `"build": "./.bin/build.sh"` (apps/remix/package.json). Env loads from root `.env` / `.env.local` via `dotenv -e ../../.env -e ../../.env.local`.
- Other apps: `apps/docs` (docs site), `apps/openpage-api` (not needed to use the product).
- DB: Postgres via Prisma (`packages/prisma`). Scripts: `prisma:migrate-dev` (`prisma migrate dev --skip-seed`), `prisma:migrate-deploy`, `prisma:migrate-reset`, `prisma:seed` (`tsx ./seed-database.ts`) (packages/prisma/package.json L8-21).
- `npm run dx` = `npm ci && dx:up (docker compose -f docker/development/compose.yml up -d) && prisma:migrate-dev && prisma:seed`. `npm run d` = dx + translate:compile + dev (package.json scripts). `npm run dev` also runs `lingui compile` first.
- PDF: `@libpdf/core` signatures, `@cantoo/pdf-lib`, and `@documenso/skia-canvas` (native, allowed in `allowScripts`, package.json) for certificate/audit-log rendering (PR #3214 "use documenso fork of skia-canvas for rendering"). A Playwright/Chromium HTML-to-PDF path exists but is opt-in via `NEXT_PRIVATE_USE_PLAYWRIGHT_PDF` (packages/lib/jobs/definitions/internal/seal-document.handler.ts:16-19, ~L257-265; packages/lib/server-only/htmltopdf/get-certificate-pdf.ts:23-46).

| Service | Required? | How the repo's dev setup provides it | Source |
|---|---|---|---|
| Postgres 15 (:54320 → 5432, user/pass/db `documenso`/`password`/`documenso`) | required | docker compose | docker/development/compose.yml; .env.example `NEXT_PRIVATE_DATABASE_URL` |
| Inbucket mail catcher (SMTP :2500, UI :9000) | effectively required (signup verification, sending documents to recipients) | docker compose | compose.yml; README.md:229-233; .env.example `NEXT_PRIVATE_SMTP_*` |
| Redis 8 (:63790) | optional (only for `NEXT_PRIVATE_JOBS_PROVIDER=bullmq`; default `local`) | docker compose | compose.yml; .env.example `[[BACKGROUND JOBS]]` |
| MinIO (S3 :9002, console :9001) | optional (`NEXT_PUBLIC_UPLOAD_TRANSPORT="database"` is the default) | docker compose | compose.yml; .env.example `[[STORAGE]]` |
| Gotenberg (:3005) | optional (DOCX → PDF; "When unset, DOCX uploads are disabled and only PDF is accepted") | docker compose (custom Dockerfile.gotenberg) | compose.yml; .env.example `[[DOCUMENT CONVERSION]]` |
| Background jobs | in-process with `local` provider; Inngest/BullMQ optional | env | .env.example; apps/docs/.../local-development/manual.mdx ("default local job provider does not support scheduled jobs required for document reminders") |
| Signing certificate (.p12) | required to seal/complete documents | committed dev cert `apps/remix/example/cert.p12`, loaded when `NODE_ENV !== 'production'` | packages/signing/transports/local.ts:18-19 |

**Preview deployments:** no `vercel.json` or `netlify.toml` in the tree. The Vercel GitHub integration is active but only for the **docs** site and `openpage-api`: PR #3396's statuses are `Vercel – prd-docs`, `Vercel – prd-openpage-api`, `Vercel – stg-docs`, and `gh api repos/documenso/documenso/deployments` lists only `Preview – stg-docs` from `vercel[bot]`. The app ships through a `release` branch on tag push (.github/workflows/deploy.yml). `railway.toml`/`render.yaml` are self-host one-click templates (guess). So PRs get **no running preview of the signing app**, which leaves room for Groundtruth.

## Running without Docker

Yes. The docs have a "Manual Setup" (apps/docs/content/docs/developers/local-development/manual.mdx): `npm i`, set env, `npm run prisma:migrate-dev`, optionally `npm run prisma:seed -w @documenso/prisma`, `npm run dev`. Only Postgres is truly needed. The rest has defaults that avoid services (database upload transport, local jobs).
- Postgres: apt `postgresql` (Ubuntu 24.04 ships 16 (guess)); compose uses 15. No extensions seen. Create role `documenso`/`password` and db `documenso`, or point the URL at any role.
- Mail: Inbucket and Mailpit are single Go binaries, not apt packages. Mailpit has an install script (guess, not in repo). Point `NEXT_PRIVATE_SMTP_HOST/PORT` at it. Without a catcher, verification emails and "document sent" emails fail to send. Seeded users are pre-verified.
- Redis/MinIO/Gotenberg: skip.
- CI (.github/workflows/e2e-tests.yml) still boots services with `npm run dx:up` (docker), then `prisma:migrate-dev`, `prisma:seed`, `npm run ci` (build + e2e), with `NEXT_PRIVATE_SIGNING_LOCAL_FILE_PATH: './example/cert.p12'` because `next start` sets NODE_ENV=production.

## Env vars and third-party services

`.env.example` (251 lines) has working dev defaults. README: "just run `cp .env.example .env` to get started with our handpicked defaults".
- Required: `NEXTAUTH_SECRET="secret"`, `NEXT_PRIVATE_ENCRYPTION_KEY="CAFEBABE"`, `NEXT_PRIVATE_ENCRYPTION_SECONDARY_KEY="DEADBEEF"` (the comments say "at least 32 characters", but the shipped dev values are shorter and are what CI uses), `NEXT_PUBLIC_WEBAPP_URL`, `NEXT_PRIVATE_INTERNAL_WEBAPP_URL` ("URL used by the web app to request itself (e.g. local background jobs)"), `NEXT_PRIVATE_DATABASE_URL`, `NEXT_PRIVATE_DIRECT_DATABASE_URL`, `NEXT_PRIVATE_SMTP_FROM_NAME/ADDRESS` (manual.mdx env list).
- Signing: `NEXT_PRIVATE_SIGNING_TRANSPORT="local"`. Passphrase empty. Cert path optional in dev (see table). GCloud HSM / CSC are optional.
- Third parties, all optional: Stripe (`NEXT_PRIVATE_STRIPE_*`, billing off unless `NEXT_PUBLIC_FEATURE_BILLING_ENABLED`), Google/Microsoft/OIDC login, Resend/MailChannels, AWS SES (EE), PostHog, Google Vertex AI, Cloudflare Turnstile, Browserless, Plain support. Dev works with none of them.
- `DANGEROUS_BYPASS_RATE_LIMITS` ("Only use for E2E tests"). An agent that logs in repeatedly may need it (guess).
- EE license key (`NEXT_PRIVATE_DOCUMENSO_LICENSE_KEY`) gates enterprise features. PRs in `packages/ee` may not be checkable (guess).

## Login in dev

- Seeded accounts (packages/prisma/seed/initial-seed.ts:56-64 via `seedUser` in seed/users.ts:24-46, default `password = 'password'`, `verified = true`):
  - `example@documenso.com` / `password`, role USER
  - `admin@documenso.com` / `password`, roles USER + ADMIN (`isAdmin: true` → `roles: [Role.USER, Role.ADMIN]`, users.ts:46)
  - `medium-account@documenso.com` / `password` (seed/medium-account-seed.ts:8, 26-28)
  - `.env.example` also names `E2E_TEST_AUTHENTICATE_USER_EMAIL="testuser@mail.com"` / `test_Password123` for e2e. That user is created by tests, not by the seed (guess).
- Each seeded user gets its own personal organisation + team (users.ts ~L56-95).
- CLI account creation: none found. Accounts come from the seed, or from signup + email verification.
- Roles: global `Role { ADMIN, USER }`; `OrganisationMemberRole` and `TeamMemberRole` `{ ADMIN, MANAGER, MEMBER }` (packages/prisma/schema.prisma:34-37, 938-948).
- Email verification is **mandatory for sign-in**. `if (!user.emailVerified) { ... triggerJob('send.signup.confirmation.email') ... throw new AppError('UNVERIFIED_EMAIL') }` (packages/auth/server/routes/email-password.ts:160-180). There's no env flag to skip it. Signup can be turned off (`NEXT_PUBLIC_DISABLE_SIGNUP`, `NEXT_PUBLIC_DISABLE_EMAIL_PASSWORD_SIGNUP`). Seeded users are verified.
- Not SSO-only: email/password is the default. Passkeys, 2FA, Google/Microsoft/OIDC are optional.
- Recipients sign via tokenized links, and the sender can copy a pending recipient's signing link from the recipient avatar popover (PR #3072 description: "copy signing link on pending documents"). An agent can therefore play the signer without reading email (guess that no login is needed on the link).

## Seed and fixture data

- `npm run prisma:seed` → `seed-database.ts` runs every `seed/*.ts` exporting `seedDatabase` (L4-25): `initial-seed.ts` and `medium-account-seed.ts` (analytics and large-team seeds don't export it).
- Contents: 2 users. `Example Document 1..4` owned by example. `Document 1..4` owned by admin with example as recipient, so example's **inbox** has items. A "Pending Document", `Template 1` + `Direct Template 1` (token `example`) per user, plus overflow/alignment test documents (initial-seed.ts:66-230). It's decent but small.
- **Not a reset:** `initial-seed.ts:39-53` returns early if `example@documenso.com` or `admin@documenso.com` exists, so re-seeding is a no-op.
- Reset: `npm run prisma:migrate-reset` (`prisma migrate reset`, which drops and re-applies the schema, then runs the seed; add `--force` for non-interactive (guess)). It's destructive and has to replay all migrations. Whether the running app tolerates it is untested (guess).

## Draft .groundtruth.yml

```yaml
version: 1
runtime: { node: "24" }                 # package.json engines node >=24
env:
  NEXT_PRIVATE_DATABASE_URL: postgres://documenso:password@127.0.0.1:5432/documenso
  NEXT_PRIVATE_DIRECT_DATABASE_URL: postgres://documenso:password@127.0.0.1:5432/documenso
  NEXT_PUBLIC_WEBAPP_URL: http://localhost:3000            # UNCERTAIN: must match browser origin; used in email/signing links
  NEXT_PRIVATE_INTERNAL_WEBAPP_URL: http://localhost:3000  # jobs call the app itself
  NEXT_PRIVATE_SMTP_HOST: 127.0.0.1
  NEXT_PRIVATE_SMTP_PORT: "1025"                           # Mailpit default instead of Inbucket's 2500
  NEXT_PRIVATE_SMTP_USERNAME: ""                           # UNCERTAIN: blank to avoid SMTP AUTH against Mailpit
  NEXT_PRIVATE_SMTP_PASSWORD: ""
  DOCUMENSO_DISABLE_TELEMETRY: "true"
  DANGEROUS_BYPASS_RATE_LIMITS: "true"                     # UNCERTAIN: only if repeated logins trip rate limits
setup:
  - sudo apt-get update -qq && sudo apt-get install -y -qq postgresql   # UNCERTAIN: compose uses 15; Ubuntu default is newer
  - sudo service postgresql start
  - sudo -u postgres psql -c "CREATE ROLE documenso LOGIN SUPERUSER PASSWORD 'password';" || true
  - sudo -u postgres createdb -O documenso documenso || true
  - curl -sL https://raw.githubusercontent.com/axllent/mailpit/develop/install.sh | sudo bash   # UNCERTAIN: third-party installer, not in repo
  - sudo npm install -g npm@11.19.1      # UNCERTAIN: engines needs npm >=11.17; CI uses `corepack enable npm`
  - cp .env.example .env                 # dotenv-cli should not override the env above (UNCERTAIN)
  - npm ci                               # postinstall: patch-package; downloads prisma engines + skia-canvas prebuilds
  - npm run prisma:generate
  - npm run prisma:migrate-deploy        # non-interactive; docs/CI use prisma:migrate-dev
  - npm run prisma:seed
start: |
  sudo service postgresql start
  (mailpit --smtp 127.0.0.1:1025 --listen 127.0.0.1:8025 &)   # UNCERTAIN: flags
  npm run dev                            # lingui compile + react-router dev on :3000
port: 3000
ready: { path: /api/health, timeout_seconds: 600 }   # 200 when DB ok (cert missing only gives "warning"), 500 on DB error (apps/remix/app/routes/api+/health.ts)
reset: npm run prisma:migrate-reset -- --force        # UNCERTAIN: destructive, re-seeds; prisma:seed alone is a no-op once users exist
# personas:
#   sender: { description: "Regular user; owns Example Documents; recipient of admin's Document 1-4 (has inbox items)", username: example@documenso.com, password: password }
#   admin:  { description: "Instance admin (admin panel) and owner of Document 1-4", username: admin@documenso.com, password: password }
#   medium: { description: "Account with a medium-sized document set", username: medium-account@documenso.com, password: password }
```

## What the current format can't express

- [service-db] Needs a Postgres server with a specific role/db. That's done with `service`/`psql` in setup because there's no services key.
- [service-other] Needs an SMTP catcher for signup verification and document-sent emails. Inbucket/Mailpit aren't apt packages, so we need a binary installer.
- [multi-port] If the agent must read emails (verification link, recipient invite), the mail UI (:8025/:9000) is a second origin the browser must reach. Copying signing links from the UI avoids it for signing flows.
- [email-verify] Sign-in hard-requires `emailVerified` (email-password.ts:160-180). Any PR about signup/onboarding needs email reading.
- [reset-hard] The seed is guarded "if users exist, return", so reset means `prisma migrate reset` (drop + all migrations + seed) and may need the app stopped (guess).
- [arch-native] Native `@documenso/skia-canvas` prebuilds (package.json `allowScripts`), Prisma engines, optional Playwright Chromium for PDF.
- [mirror] `.npmrc` `min-release-age = 7` plus a hard npm >=11.17 requirement. A package mirror that lags or lacks publish-time metadata could break `npm ci` (guess).
- [host-config] `NEXT_PUBLIC_WEBAPP_URL` goes into email and signing links. `NEXT_PRIVATE_INTERNAL_WEBAPP_URL` must be reachable from the server for local jobs (sealing).
- [prod-build] Only if a PR touches the production signing path: prod `start` needs `NEXT_PRIVATE_SIGNING_LOCAL_FILE_PATH` (CI sets it, e2e-tests.yml). Dev mode is otherwise fine.
- [third-party] Billing (Stripe), EE features (license key), AI (Vertex) and HSM/CSC signing are not checkable without keys.

## Difficulty

**medium.** Postgres plus a mail catcher is all that's needed. The committed dev cert makes signing work out of the box, and seeded users are verified with a known password. The friction is email: new-account flows need a readable inbox, and signing flows need either email or the copy-link UI. Reset is heavy because the seed is not re-runnable. The npm 11 / Node 24 requirement and native skia-canvas add install risk.

## Candidate PRs for evaluation

1. https://github.com/documenso/documenso/pull/3372, "feat: add inbox filters", by dguyen (COLLABORATOR, core team). "Allow users to search documents and filter by statuses in the inbox page". Commits include "fix: adjust pagination limits for inbox query" and "fix: test" after the feature commit. Good fit: `example@documenso.com` has seeded inbox items (admin's Document 1-4), so search and status filters can be checked immediately. Persona: sender (example).
2. https://github.com/documenso/documenso/pull/3086, "feat: add document naming options when using templates", by catalinpit (MEMBER). When using a template, the user can name the document after the template, the uploaded file, or a custom name. Many in-PR commits, including "chore: display errors for each custom document upload" and a final "fix: simplify state". Seeded `Template 1` exists per user. Checkable: open Use Template dialog, choose each option, confirm the resulting document title. Persona: sender.
3. https://github.com/documenso/documenso/pull/3256, "fix(ui): add type=button to multiselect remove and clear buttons", by misinierijon4-debug (CONTRIBUTOR, outside; fixes #3201). Claim: removing a badge or clearing a MultiSelect inside a form (webhook creation, team member creation, auth selection dialogs) no longer submits the form. Small but crisp browser claim. Commits are the fix plus a merge from main, so no in-PR fix. Persona: sender (team webhooks settings).

## Sources

- gh api repos/documenso/documenso, commits/main, deployments, git tree (3,484 paths); gh api commits/{sha}/statuses and check-runs for PR #3396
- README.md, package.json, .npmrc, .env.example, docker/development/compose.yml, .devcontainer/devcontainer.json, on-create.sh, post-start.sh
- apps/remix/package.json, apps/remix/app/routes/api+/health.ts
- apps/docs/content/docs/developers/local-development/manual.mdx, signing-certificate.mdx
- packages/prisma/package.json, schema.prisma, seed-database.ts, seed/initial-seed.ts, seed/users.ts, seed/medium-account-seed.ts, seed/analytics-seed.ts, seed/large-team-seed.ts, seed/templates.ts
- packages/signing/transports/local.ts, packages/auth/server/routes/email-password.ts, packages/lib/jobs/definitions/internal/seal-document.handler.ts, packages/lib/server-only/htmltopdf/get-certificate-pdf.ts
- .github/workflows/e2e-tests.yml, ci.yml, deploy.yml; .github/actions/node-install/action.yml
- gh pr view 3372, 3086, 3173, 3256, 3153, 3072; gh pr list --state merged (100 most recent)
