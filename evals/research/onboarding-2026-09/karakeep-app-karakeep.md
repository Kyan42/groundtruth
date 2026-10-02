# karakeep-app/karakeep

Self-hostable "bookmark everything" app (links, notes, images) with AI auto-tagging and full-text search; formerly Hoarder. ~29.3k stars, default branch `main`, last commit seen 2026-09-27. https://github.com/karakeep-app/karakeep

## Stack and services

- TypeScript monorepo, pnpm workspaces + turbo (`package.json`, `pnpm-workspace.yaml`, `turbo.json`). `packageManager: pnpm@11.2.1` (package.json). Node 24 (`.nvmrc` = `24`).
- `apps/web`: Next.js 16.3.6 (`apps/web/package.json`, `"dev": "next dev"`), tRPC API (`packages/trpc`), NextAuth.
- `apps/workers`: background workers (`"start": "tsx watch index.ts"`, apps/workers/package.json:72) for crawling (Playwright over CDP), OCR (tesseract.js), inference, search indexing, imports, feeds. Job queue is `liteque` on SQLite (apps/workers/package.json:33) -- no Redis.
- DB: SQLite via `better-sqlite3` + drizzle; file is `${DATA_DIR}/db.db` (packages/db/drizzle.config.ts). Migrations: `pnpm run db:migrate` (root package.json -> `packages/db` `tsx migrate.ts`).
- Root scripts: `pnpm web`, `pnpm workers`, `pnpm db:migrate`, `pnpm seed:apply`, `pnpm seed:snapshot` (package.json).

| Service | Required? | How the repo's dev setup provides it | Source |
|---|---|---|---|
| SQLite (file) | required | in-process, `DATA_DIR/db.db` | packages/db/drizzle.config.ts |
| Web (Next.js) :3000 | required | `pnpm web` | package.json; docs/docs/08-development/01-setup.md |
| Workers process | optional for browsing, required for crawling/indexing/AI/imports | `pnpm workers` | package.json; setup.md "NOTE: The web app kinda works without any dependencies..." |
| Meilisearch v1.41.0 :7700 | optional (search disabled if `MEILI_ADDR` unset) | `start-dev.sh` / compose run `getmeili/meilisearch:v1.41.0` in docker | start-dev.sh; docker/docker-compose.dev.yml; docs/docs/03-configuration/01-environment-variables.md:17-18 |
| Headless Chrome (CDP) :9222 | optional (without it the crawler runs in "browserless mode", plain HTTP fetch, no screenshots) | `start-dev.sh` runs `ghcr.io/karakeep-app/karakeep-chrome:release` in docker | start-dev.sh; apps/workers/workers/crawler/browser.ts:162-189; docs/docs/02-installation/07-minimal-install.md |
| OpenAI / Ollama | optional (AI tagging/summaries/embeddings) | env keys | packages/shared/config.ts:76-114 |
| SMTP | optional (email verification, password reset) | env | config.ts:196-202 |
| yt-dlp, monolith | optional (video download, full-page archive) | installed by karakeep-linux.sh | karakeep-linux.sh ~L193-199 |

Note: setup.md says "The worker app will automatically start headless chrome on startup", but the current code only connects to `BROWSER_WEB_URL`/`BROWSER_WEBSOCKET_URL` and otherwise logs "Running in browserless mode" (apps/workers/workers/crawler/browser.ts:162-189). The doc looks stale.

## Running without Docker

Yes, and the repo itself has a bare-metal recipe: `karakeep-linux.sh` (documented in docs/docs/02-installation/06-debuntu.md for Debian 12 / Ubuntu 24.04) installs everything without docker:
- apt: `g++ curl build-essential sudo unzip gnupg graphicsmagick ghostscript ca-certificates` (karakeep-linux.sh ~L180-189).
- Chromium: on Ubuntu noble it adds `ppa:xtradeb/apps` and installs `ungoogled-chromium` (the stock Ubuntu `chromium` apt package is a snap shim -- that's my reading of why; guess) (~L190-196). Runs it as `chromium --headless --no-sandbox --disable-gpu --disable-dev-shm-usage --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 --hide-scrollbars` (~L330). Alternative with no PPA: `pnpm exec playwright install --with-deps chromium` (what CI does, .github/workflows/ci.yml ~L83-85) and start that binary with `--remote-debugging-port=9222` (guess that the Playwright build is fine as a CDP target).
- Meilisearch: `.deb` from GitHub releases (`meilisearch.deb`, ~L201-202). Dev doesn't need a master key (env docs line 18).
- Node 24 via NodeSource (~L206-210); pnpm via corepack (setup.md).
- `better-sqlite3` is a native module; with no prebuilt binary for the platform/Node ABI it compiles, needing build-essential/python (guess; PR #3043 "fix: upgrade SQLite bindings for Node 24" shows it's sensitive to Node version).

Nothing is docker-only. The CI e2e suite does use docker compose (packages/e2e_tests/docker-compose.yml; ci.yml "E2E Tests"), so there is no no-docker CI recipe beyond the install script.

## Env vars and third-party services

- `.env.sample` has only `DATA_DIR=<path>` and `NEXTAUTH_SECRET=<secret>`. Setup.md: `DATA_DIR` is "the only required env variable"; `NEXTAUTH_SECRET` is needed or "Logging in will not work"; suggests one `.env` at the repo root symlinked into `apps/web`, `apps/workers`, `packages/db` (each loads its own via dotenv, e.g. packages/db/drizzle.config.ts `import "dotenv/config"`). Since web, workers and migrate run with different cwds, `DATA_DIR` must be an absolute path.
- `NEXTAUTH_URL` defaults to `http://localhost:3000` (config.ts:57-61); docs say signout redirects go wrong if it's not the real address (env docs line 15).
- `MEILI_ADDR` (unset -> search disabled), `BROWSER_WEB_URL` (unset -> browserless crawling), `OPENAI_API_KEY`/`OLLAMA_BASE_URL` (unset -> no AI tagging) are all optional (config.ts:76-126; docs 07-minimal-install.md).
- Optional third parties: OpenAI/Ollama, SMTP, OAuth/OIDC (`OAUTH_*`, config.ts:65-73), Cloudflare Turnstile (`TURNSTILE_*`, config.ts:74-75), Stripe (`STRIPE_*`, config.ts:219-223, hosted-cloud billing). None needed for core bookmark/list/tag flows.
- Crawling fetches real external websites; the sandbox needs outbound internet for new bookmarks to get content.

## Login in dev

- Seeded accounts (only after `pnpm seed:apply`): `test1@example.com`, `test2@example.com`, `test3@example.com`, all password `test1234`. Source: `tools/seed-snapshot/src/index.ts:22` (`PASSWORD = "test1234"`) and `:74-86` (USERS); also recorded in `snapshots/seed-data-2026-05-20-163735.json` (`users[].email/password`).
  - test1: 20 bookmarks (nasa.gov, theverge.com, MDN, ...), tags research/docs/product, lists "Reading Queue" > "Reference", smart lists "Research Smart", "Favorites Smart" (index.ts:24-53; manifest counts).
  - test2: 4 bookmarks, tags engineering/design/ops, lists "Workbench" > "Deep Dives", smart lists (index.ts:55-69).
  - test3: empty account (datasetKey null, index.ts:85).
  - Each seeded user also has an API key named `seed-snapshot-<email>` (index.ts:300, :463).
- Admin: the first user created becomes `admin` (packages/trpc/models/users.ts:109-115: `userCount === 0 ? "admin" : "user"`). test1 is created first so is presumably the admin (guess -- depends on creation order in the snapshot script).
- Without the snapshot: sign up at the web UI (open by default, `DISABLE_SIGNUPS` default false, config.ts:63); the first signup is admin. The e2e suite creates `admin@example.com` / `test1234` via tRPC `users.create` (packages/e2e_tests/setup/seed.ts).
- CLI: `apps/cli` (`@karakeep/cli`) has `admin users list` etc. (apps/cli/src/commands/admin.ts:63-67) but needs an API key; I found no create-user CLI command.
- Roles: `user`, `admin` (users.ts:66).
- Email verification: `EMAIL_VERIFICATION_REQUIRED` default false (config.ts:202). Turnstile only if keys set.
- Not SSO-only: password auth on by default (`DISABLE_PASSWORD_AUTH` default false, config.ts:64); OAuth optional.

## Seed and fixture data

- `pnpm seed:apply` (root package.json -> tools/seed-snapshot `tsx src/apply.ts`): finds the newest `snapshots/seed-data-*.tar.gz` (8.5 MB, committed) and extracts it into `<repo>/data` (tools/seed-snapshot/src/apply.ts: `dataDir = path.join(repoRoot, "data")`). It refuses if `data/` exists unless `--force`, which deletes it first. So `DATA_DIR` must be `<repo>/data` for the seed to be used.
- The snapshot was produced by running the real app in docker (tools/seed-snapshot/docker-compose.yml with Meilisearch + Chrome) and crawling real URLs, so bookmarks come with crawl status "success" and stored assets (manifest `crawlStatus`). Snapshot dated 2026-05-20; newer migrations are applied by `pnpm db:migrate` afterwards (guess that the migrator upgrades an older db cleanly).
- Meilisearch data is not inside `/data` (separate container in the compose file), so after seeding the search index is empty until a re-index is triggered (setup.md: "You can trigger a re-index for the entire items collection in the admin panel") (guess that it isn't auto-reindexed on boot).
- Reset: `pnpm seed:apply --force` is fast (untar 8.5 MB) but swaps the SQLite file under a running web/workers process; safest with the app stopped (guess). Deleting `DATA_DIR` + `db:migrate` gives an empty DB.
- `packages/benchmarks/src/seed.ts` exists for benchmarks (not read in detail).

## Draft .groundtruth.yml

```yaml
version: 1
runtime: { node: "24" }                          # .nvmrc
env:
  NEXTAUTH_SECRET: groundtruth-dev-secret        # any fixed string works for dev (UNCERTAIN: no length check found)
  NEXTAUTH_URL: http://localhost:3000            # UNCERTAIN: set to the URL the browser really uses
  MEILI_ADDR: http://127.0.0.1:7700
  BROWSER_WEB_URL: http://127.0.0.1:9222
  CI: "1"
setup:
  - sudo apt-get update -qq && sudo apt-get install -y -qq build-essential python3 g++ curl unzip graphicsmagick ghostscript
  - sudo npm install -g pnpm@11.2.1               # packageManager in package.json
  - curl -fsSLO https://github.com/meilisearch/meilisearch/releases/download/v1.41.0/meilisearch.deb && sudo apt-get install -y ./meilisearch.deb && rm meilisearch.deb   # UNCERTAIN: exact asset name for a pinned version (karakeep-linux.sh uses releases/latest/download/meilisearch.deb)
  - pnpm install --frozen-lockfile
  - pnpm exec playwright install --with-deps chromium   # UNCERTAIN: run from apps/workers where playwright is a dependency
  - export DATA_DIR=$PWD/data && pnpm seed:apply --force && pnpm db:migrate   # DATA_DIR must be absolute and equal <repo>/data
start: |
  export DATA_DIR=$PWD/data
  meilisearch --db-path "$PWD/.meili" --http-addr 127.0.0.1:7700 --no-analytics &
  CHROME=$(ls -d ~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome | head -1)   # UNCERTAIN: path layout of Playwright's chromium
  "$CHROME" --headless --no-sandbox --disable-gpu --disable-dev-shm-usage --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 --hide-scrollbars &
  pnpm workers &
  pnpm web
port: 3000
ready: { path: /signin, timeout_seconds: 600 }   # apps/web/app/signin/page.tsx; next dev compiles on first request
reset: export DATA_DIR=$PWD/data && pnpm seed:apply --force && pnpm db:migrate   # UNCERTAIN: replaces the SQLite file under the running app
# personas:
#   admin:   { description: "First user (admin); 20 crawled bookmarks, tags, nested + smart lists", username: test1@example.com, password: test1234 }
#   member:  { description: "Regular user with 4 bookmarks and its own lists", username: test2@example.com, password: test1234 }
#   empty:   { description: "Regular user with no data", username: test3@example.com, password: test1234 }
```

## What the current format can't express

- [multi-process] Web (`pnpm web`), workers (`pnpm workers`), Meilisearch and a headless Chrome all run side by side; only the web port is browser-facing.
- [service-other] Meilisearch (search) and headless Chrome over CDP (crawler screenshots/JS pages) as local services; both optional but needed for search- or crawl-related PRs.
- [host-config] `DATA_DIR` must be an absolute path shared by three processes with different cwds, and must equal `<repo>/data` for `seed:apply`; env values can't reference the checkout path, so it has to be exported inside `setup`/`start`. `NEXTAUTH_URL` should match the public URL.
- [reset-hard] (mild) `seed:apply --force` is cheap but replaces the SQLite DB and assets under running processes, and the Meilisearch index isn't part of the snapshot.
- [third-party] Only for AI features (auto-tagging, summaries, semantic search, "article quality"): real OpenAI/Ollama needed. Core flows need no keys.
- [arch-native] `better-sqlite3` native binding (Node 24 ABI); compiles if no prebuilt.
- (new tag suggestion) [net-egress] crawling hits real external websites, so crawl-related checks depend on outbound internet and third-party page content.

## Difficulty

easy. Everything installs without docker (the repo ships a bare-metal Ubuntu 24.04 install script), the DB is a SQLite file, dev needs only `DATA_DIR` + `NEXTAUTH_SECRET`, and there is a committed seed snapshot with three documented accounts and crawled bookmarks. Main friction: four processes, absolute `DATA_DIR` that must match the snapshot location, and an empty search index after seeding.

## Candidate PRs for evaluation

1. https://github.com/karakeep-app/karakeep/pull/2629 -- author xingzihai (outside contributor). In the "Manage Lists" modal, choosing a list immediately adds the bookmark (removes the separate "Add" button), with a loading state and duplicate-submit guard. Good case: clear click-level claims; nine commits including in-PR fixes ("add loading state and prevent duplicate submission", "add isLoading guard to onChange callback", "improve ManageListsModal accessibility and rendering") after bot reviews. Persona: test1 (has bookmarks and lists "Reading Queue"/"Reference").
2. https://github.com/karakeep-app/karakeep/pull/3110 -- author eriktews (outside contributor). Home/End keys move the caret in the search bar instead of being swallowed. Good case: tiny, precise keyboard claim that can be checked in a browser; in-PR fix "preserve caller onKeyDown in search input" and a revert of a broken bot suggestion. Persona: any seeded user. (Search bar itself works without Meilisearch; results need it -- guess.)
3. https://github.com/karakeep-app/karakeep/pull/3053 -- author BrownieCoder (outside contributor). Adds `has:notes` / `has:highlights` (and `-`/`!` negation) to the search query language, including autocomplete and the query-explainer tooltip. Good case: autocomplete and explainer are browser-visible; results need bookmarks with notes/highlights (the seed has none -- agent must add some) and probably Meilisearch. Single commit, so no in-PR fix. Persona: test1.

## Sources

- gh api repos/karakeep-app/karakeep (metadata), git tree of `main`
- .nvmrc, package.json, .env.sample, start-dev.sh, karakeep-linux.sh (~L175-460)
- docs/docs/08-development/01-setup.md, docs/docs/02-installation/06-debuntu.md, docs/docs/02-installation/07-minimal-install.md, docs/docs/03-configuration/01-environment-variables.md
- packages/shared/config.ts, packages/db/drizzle.config.ts, packages/db/package.json, apps/web/package.json, apps/workers/package.json
- apps/workers/workers/crawler/browser.ts, apps/workers/workers/crawlerWorker.ts (~L85-108)
- packages/trpc/models/users.ts, apps/cli/src/commands/admin.ts, packages/e2e_tests/setup/seed.ts
- tools/seed-snapshot/package.json, src/apply.ts, src/index.ts, docker-compose.yml; snapshots/seed-data-2026-05-20-163735.json
- .github/workflows/ci.yml
- gh pr list (merged since 2025-04-01, touching apps/web); gh pr view 3053, 2629, 3110, 2620, 2558
