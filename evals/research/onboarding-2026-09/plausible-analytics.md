# plausible/analytics

Privacy-first, cookie-free web analytics (Google Analytics alternative), self-hosted or cloud. ~29.2k stars, default branch `master`, last commit seen 2026-09-28. https://github.com/plausible/analytics

## Stack and services

- Elixir 1.20.4 on Erlang/OTP 28.5.0.5, Node.js 24.17.0 (`.tool-versions`). `mix.exs` requires `elixir: "~> 1.18"` (mix.exs:11).
- Phoenix + LiveView server; React/TypeScript dashboard in `assets/` built by the `esbuild` and `tailwind` hex packages (config/dev.exs watchers; mix.exs `assets.setup` / `assets.build` aliases ~L209-215). A separate `tracker/` npm package is built into `priv/tracker/js` (Makefile `install`; dev.exs watcher `npm run deploy` in `tracker`).
- Two build flavours: EE (default for `:dev`, compiles `lib`, `test/support`, `extra/lib`, mix.exs:62-69) and CE (`MIX_ENV=ce_dev`). Funnels, revenue goals, SSO, etc. live in `extra/lib` (EE only).
- Serves on port 8000 by default (`HTTP_PORT`/`PORT` default 8000, config/runtime.exs:63-65), bound to `LISTEN_IP` default `127.0.0.1` (runtime.exs:51).

| Service | Required? | How the repo's dev setup provides it | Source |
|---|---|---|---|
| PostgreSQL (prod uses 18) | required | `make postgres` = `docker run postgres:latest`; CI uses `postgres:18` service | Makefile `postgres`, `postgres-prod`; .github/workflows/elixir.yml `services.postgres` |
| ClickHouse (prod 25.11.5.8) | required (all stats live here; `mix ecto.create` creates it) | `make clickhouse` = `docker run clickhouse/clickhouse-server:latest-alpine` with `CLICKHOUSE_SKIP_USER_SETUP=1`, ports 8123/9000 | Makefile `clickhouse`; elixir.yml `services.clickhouse`; config/.env.dev `CLICKHOUSE_DATABASE_URL=http://127.0.0.1:8123/plausible_events_db` |
| MinIO (S3) | optional (CSV imports/exports only) | `make minio` (docker), buckets `dev-exports`/`dev-imports` | Makefile `minio`; .env.dev `S3_*` |
| Browserless Chromium | optional (installation verification) | `make browserless` (docker) | Makefile `browserless`; runtime.exs ~L1043 `BROWSERLESS_ENDPOINT` default `http://0.0.0.0:3000` |
| SAML IdP (simplesamlphp) + CoreDNS | optional (SSO testing only) | `make sso`, `make mock-dns` (docker) | Makefile `sso`, `mock-dns` |
| Mail | none needed | `MAILER_ADAPTER=Bamboo.LocalAdapter`; mail viewable at `/sent-emails` in dev | .env.dev; lib/plausible_web/router.ex:107-109 |
| Background jobs (Oban) | in-process | runs inside the Phoenix node; `DISABLE_CRON=true` in dev | .env.dev |

No docker-compose file and no `.devcontainer/` in the tree; dev setup is the Makefile + CONTRIBUTING.md.

## Running without Docker

Yes, realistically. Everything the core flows need has a non-Docker install:
- PostgreSQL: Ubuntu apt `postgresql` (24.04 ships 16; the app has no extension requirement I found). Prod uses 18 (Makefile `postgres-prod`); PGDG apt repo gives 18 if needed. Dev DATABASE_URL expects user `postgres` / password `postgres` on 127.0.0.1:5432 (.env.dev), so setup must set that password.
- ClickHouse: official apt repo `https://packages.clickhouse.com/deb stable main`, packages `clickhouse-server clickhouse-client` (clickhouse.com/docs/install/debian_ubuntu), started with `sudo service clickhouse-server start` or the binary directly. Alternative with no apt: `curl https://clickhouse.com/ | sh` then `./clickhouse server` (clickhouse.com/docs/install/quick-install-curl). HTTP port 8123 is the ClickHouse default. Dev config uses the `default` user with no password (URL has no credentials, .env.dev) -- the apt install prompts for a default-user password via debconf; non-interactive install presumably leaves it empty (guess).
- Erlang/Elixir: Ubuntu apt versions are far too old for OTP 28 / Elixir 1.20 (guess based on Ubuntu 24.04 shipping OTP 25 / Elixir 1.14). Options: `mise`/`asdf` with `.tool-versions` (compiling OTP from source is slow, ~10+ min, guess), or prebuilt OTP tarballs from builds.hex.pm as used by `erlef/setup-beam` in CI (elixir.yml) (exact URL layout is a guess).
- MinIO, browserless, SSO IdP: only via docker in the Makefile, but all optional.

Setup sketch (mirrors Makefile `install` + CONTRIBUTING steps + elixir.yml e2e job): `mix deps.get`, `mix ecto.create`, `mix ecto.migrate`, `mix run priv/repo/seeds.exs`, `npm ci --prefix assets`, `npm ci --prefix tracker`, `mix assets.setup`, `npm run deploy --prefix tracker`, `mix download_country_database` (downloads the free DB-IP lite mmdb, no key: lib/mix/tasks/download_country_database.ex). Start with `mix phx.server`.

## Env vars and third-party services

- All dev env comes from the committed `config/.env.dev`, loaded by `Envy.load(["config/.env.dev"])` in config/runtime.exs:5-7. It includes `BASE_URL=http://localhost:8000`, `SECRET_KEY_BASE`, `TOTP_VAULT_KEY`, `DATABASE_URL`, `CLICKHOUSE_DATABASE_URL`, `SELFHOST=false`, `DISABLE_CRON=true`, `ADMIN_USER_IDS=1`, dev Paddle vendor id/auth code, a Google OAuth client id/secret, `S3_DISABLED=false` + minio creds, fake HelpScout keys. No secrets need generating.
- `BASE_URL` is required (runtime.exs:69-78) and is the public URL the app believes it has; a sandbox reached via another hostname may need it overridden (guess whether Envy lets process env win over the file -- UNCERTAIN).
- Paddle (billing) is mocked in dev: `paddle_api: Plausible.Billing.DevPaddleApiMock` (config/dev.exs). Installation verification is mocked: `verification_checks_mod: ...ChecksMock` (dev.exs).
- Google (Search Console / GA import) needs real OAuth; the seeds insert a fake `google_auth` row (seeds.exs ~L133) so the UI renders as connected, but real calls would fail (guess). Not needed for core dashboard flows.
- Friendly Captcha on registration only turns on when `FRIENDLY_CAPTCHA_SITEKEY` is set (lib/plausible_web/captcha.ex:10-12; runtime.exs:315); unset in .env.dev.
- Email: Bamboo LocalAdapter, readable at `/sent-emails` (router.ex:107-109). Email verification is off by default: `ENABLE_EMAIL_VERIFICATION` default false (runtime.exs:293-294) and not set in .env.dev.

## Login in dev

- Seeded accounts, all password `plausible`, from `priv/repo/seeds.exs`:
  - `user@plausible.test` (seeds.exs ~L21) -- owner of `dummy.site` (rich stats) and `another.site`; also an editor-invitee on user3's team. Documented in CONTRIBUTING.md "Seeds".
  - `user2@plausible.test` "Mary Jane" (owner of `computer.example.com`, invited as team editor) (~L82).
  - `user3@plausible.test` "Harvey Dent" (business plan subscription, `bank.example.com`) (~L88-90).
  - `user4@plausible.test` "Bruce Wayne" (`cave.example.com`, pending site transfer to user) (~L93).
  - `solo@plausible.test` (EE only, dev subscription) (~L98).
  - Guests on dummy.site: "Arnold Wallaby" (viewer) and "Lois Lane" (editor), password `plausible`, emails generated by the factory (unknown address -- UNCERTAIN) (~L68-69).
  - `ADMIN_USER_IDS=1` (.env.dev) makes the first user (user@plausible.test) a super-admin for the CRM (guess that user@ gets id 1 as it is created first).
- CLI account creation: no dedicated mix task found; the seeds use test helpers `Plausible.Teams.Test.new_user/1` which are compiled in `:dev` (mix.exs:62-63: `:dev` compiles `lib`, `test/support`, `extra/lib`). `mix run -e 'Plausible.Teams.Test.new_user(email: ..., password: ...)'` would work (guess). Alternatively register at `/register` (live view, router.ex:452).
- Roles: team roles owner/admin/editor/viewer/billing and site guest roles viewer/editor (seeds use `role: :viewer`/`:editor`; full list is a guess from the Teams module names). Plus super-admin via `ADMIN_USER_IDS`.
- Email verification: off in dev (see above). If turned on, codes land in `/sent-emails`.
- Password login is primary; Google OAuth is only for integrations; SSO (SAML) is an EE feature with its own `/sso/login` (router.ex:220) -- not needed.

## Seed and fixture data

- `mix run priv/repo/seeds.exs` (CONTRIBUTING.md; also part of `mix ecto.setup` alias, mix.exs:189). 440 lines. Creates 5+ users, several sites and teams, invitations, pending transfers, 29 IP rules, country rules, goals (pageview, custom-prop, revenue "North America Purchases" in USD, outbound link), three funnels (EE), a plugins API token, then generates ~720 days of native pageview/event data (incl. revenue events, seeds.exs ~L320-327) plus 180 days of imported GA data, written into ClickHouse via `Plausible.TestUtils.populate_stats` (~L151, ~L188, ~L237). The dashboard is rich immediately.
- Not idempotent: re-running would try to insert `user@plausible.test` again (unique email -- guess it raises). Reset is `mix ecto.reset` = `ecto.drop` + `ecto.setup` (mix.exs:190) across `Plausible.Repo` and `Plausible.IngestRepo` (config/config.exs:4), i.e. drops both Postgres and ClickHouse DBs; needs the app stopped (open connections block `DROP DATABASE` on Postgres -- guess). There are also `clean_postgres` / `clean_clickhouse` tasks used by `test.e2e` (mix.exs:205-206) which may truncate without dropping (UNCERTAIN, not read).
- Seeding generates two years of events and takes a while (guess: tens of seconds to a few minutes).

## Draft .groundtruth.yml

```yaml
version: 1
runtime: { node: "24" }                      # .tool-versions nodejs 24.17.0
env:
  MIX_ENV: dev
  LISTEN_IP: "0.0.0.0"                       # UNCERTAIN: default 127.0.0.1 (runtime.exs:51); needed if the browser isn't on the same host
  # BASE_URL: http://localhost:8000          # UNCERTAIN: .env.dev value; override if the sandbox is reached by another hostname
  S3_DISABLED: "true"                        # UNCERTAIN: avoids needing minio; unclear whether process env beats Envy-loaded .env.dev
  MIX_HOME: /home/user/.mix                  # UNCERTAIN: path of non-root user
setup:
  - sudo apt-get update -qq && sudo apt-get install -y -qq build-essential autoconf libncurses-dev libssl-dev unzip curl git postgresql apt-transport-https ca-certificates gnupg inotify-tools
  # Postgres with postgres/postgres as in config/.env.dev
  - sudo service postgresql start && sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'postgres';"
  # ClickHouse from the official apt repo (clickhouse.com/docs/install/debian_ubuntu)
  - curl -fsSL https://packages.clickhouse.com/rpm/lts/repodata/repomd.xml.key | sudo gpg --dearmor -o /usr/share/keyrings/clickhouse-keyring.gpg
  - echo "deb [signed-by=/usr/share/keyrings/clickhouse-keyring.gpg arch=$(dpkg --print-architecture)] https://packages.clickhouse.com/deb stable main" | sudo tee /etc/apt/sources.list.d/clickhouse.list
  - sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq clickhouse-server clickhouse-client   # UNCERTAIN: default user password must stay empty
  - sudo service clickhouse-server start      # UNCERTAIN: may need direct `sudo -u clickhouse clickhouse-server --daemon` if no init system
  # Erlang 28 + Elixir 1.20 via mise reading .tool-versions
  - curl https://mise.run | sh && ~/.local/bin/mise trust && ~/.local/bin/mise install erlang elixir   # UNCERTAIN: builds OTP from source, slow; prebuilt OTP from builds.hex.pm would be faster
  - ~/.local/bin/mise exec -- mix local.hex --force && ~/.local/bin/mise exec -- mix local.rebar --force
  - ~/.local/bin/mise exec -- mix deps.get
  - ~/.local/bin/mise exec -- mix ecto.create
  - ~/.local/bin/mise exec -- mix ecto.migrate
  - ~/.local/bin/mise exec -- mix run priv/repo/seeds.exs
  - npm ci --prefix assets && npm ci --prefix tracker && npm run deploy --prefix tracker
  - ~/.local/bin/mise exec -- mix assets.setup
  - ~/.local/bin/mise exec -- mix download_country_database   # downloads free DB-IP lite mmdb
start: |
  sudo service postgresql start
  sudo service clickhouse-server start
  ~/.local/bin/mise exec -- mix phx.server
port: 8000
ready: { path: /login, timeout_seconds: 900 }   # UNCERTAIN: first `phx.server` compiles app + runs esbuild/tailwind watchers
reset: ~/.local/bin/mise exec -- mix ecto.reset  # UNCERTAIN: drops DBs; likely fails while the app holds connections
# personas:
#   owner:   { description: "Owner of dummy.site with 2 years of stats, goals, funnels", username: user@plausible.test, password: plausible }
#   member:  { description: "Owner of computer.example.com; invited editor on owner's team", username: user2@plausible.test, password: plausible }
#   business:{ description: "Business-plan subscriber owning bank.example.com", username: user3@plausible.test, password: plausible }
#   transfer:{ description: "Owner of cave.example.com with a pending transfer to user@", username: user4@plausible.test, password: plausible }
```

## What the current format can't express

- [runtime-lang] Needs Erlang/OTP 28.5 + Elixir 1.20.4 (`.tool-versions`); no `runtime.erlang/elixir` key, and apt versions are too old.
- [service-db] Needs PostgreSQL (prod 18) with password `postgres` and ClickHouse (prod 25.11) running before `ecto.create`.
- [service-other] MinIO (S3) for CSV import/export and Browserless for installation verification; both optional and docker-only in the Makefile.
- [slow-build] Installing OTP from source plus compiling all hex deps and the app is slow (guess: 10-20 min cold); CI caches `deps`, `_build`, tracker output (elixir.yml `actions/cache`).
- [host-config] `BASE_URL` must be set and `LISTEN_IP` defaults to 127.0.0.1 (runtime.exs:51, 69-78).
- [reset-hard] Seeds aren't re-runnable; reset is `mix ecto.reset` which drops Postgres and ClickHouse DBs (mix.exs:190) and likely needs the server stopped.
- [persona-create] Accounts only exist after the seed script runs (seeds.exs); no standalone create-user CLI.
- [multi-process] (mild) `mix phx.server` spawns esbuild, tailwind, tsc and the tracker build as watchers (config/dev.exs), plus two DB servers that must be started in `start`.
- [sso-only] Not for core login; SSO flows need a docker SAML IdP + mock DNS (Makefile `sso`, `mock-dns`) -- only relevant to SSO PRs.
- [third-party] Only for Google Search Console / GA import PRs (real Google OAuth); core flows work without keys.

## Difficulty

medium. No docker-only core service: Postgres and ClickHouse both install from apt, and all dev env is committed in `config/.env.dev` with mocked billing and local mail. The friction is the BEAM toolchain (OTP 28 / Elixir 1.20 is not in apt; compiling OTP is slow), a long first compile, and a reset that means dropping two databases. The seeded data is excellent (two years of stats, goals, funnels, multiple role personas).

## Candidate PRs for evaluation

Plausible's merged PRs in the last 18 months are almost all from the core team (of 200 recent merged PRs, only 4 were from outsiders and none changed UI), so these are core-team PRs (GitHub reports author_association CONTRIBUTOR for them, since org membership is private; "core team" is inferred from their PR volume).

1. https://github.com/plausible/analytics/pull/6644 -- author sanne-san (core team). Adds revenue and revenue-per-visitor to funnel steps whose goal is a revenue goal, shows revenue next to visitors in the step label. Good case: very concrete browser claims ("each step in its own goal's currency", "shortened visitor count in the step label"), and the seed already has the "From logged in homepage to Purchase" funnel with a USD revenue goal and revenue events (seeds.exs ~L148, ~L188, ~L325). In-PR commits: "Refactor funnel logic very slightly", "Revise one test slightly" (minor). Persona: user@plausible.test.
2. https://github.com/plausible/analytics/pull/6665 -- author zoldar (core team). New "flexible" funnel type (only first and last step required) with a three-way type selector in the funnel settings modal. Good case: claims about the settings UI (sequential / flexible / strict) and the dashboard funnel numbers; in-PR fix commit "Fix computing revenue for flexible funnels" plus several review rounds (apata, zoldar, sanne-san). Needs a migration (funnel_type column) and a backfill task. Persona: user@plausible.test (site owner, can edit funnels).
3. https://github.com/plausible/analytics/pull/6632 -- author sanne-san (core team). Funnel comparison mode: two bars per step, conversion-rate change, arrow + tooltip on the header rate. Good case: checkable by enabling "compare to previous period" on dummy.site's funnels; follow-up commit "Simplify date range render" after review comments. Persona: user@plausible.test.

## Sources

- gh api repos/plausible/analytics (metadata), commits?per_page=1
- .tool-versions, Makefile, CONTRIBUTING.md, mix.exs (aliases ~L186-223, elixirc_paths L62-69)
- config/.env.dev, config/.env.e2e_test, config/dev.exs, config/config.exs, config/runtime.exs (L1-70, L286-316, L1040-1070)
- priv/repo/seeds.exs; test/support/teams/test.ex (new_user signature)
- .github/workflows/elixir.yml
- lib/plausible_web/router.ex (grep), lib/plausible_web/live/register_form.ex (grep), lib/plausible_web/captcha.ex, lib/mix/tasks/download_country_database.ex
- https://clickhouse.com/docs/install/debian_ubuntu, https://clickhouse.com/docs/install/quick-install-curl
- gh pr list (merged since 2025-04-01), gh pr view 6644, 6632, 6604, 6665
