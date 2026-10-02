# mastodon/mastodon
Self-hosted, federated microblogging server (Rails + React). ~50.3k stars, default branch `main`, last push seen 2026-09-28. https://github.com/mastodon/mastodon

## Stack and services
- Ruby **4.0.7** (`.ruby-version`); the Gemfile allows `>= 3.3.0, < 4.1.0` (`Gemfile:4`), and CI tests 3.3, 3.4 and `.ruby-version` (`.github/workflows/test-ruby.yml:119-125`). Rails app, Puma, Sidekiq.
- Node **24.21** (`.nvmrc`), `yarn@4.18.1` via corepack (`package.json` `packageManager`). The frontend is React, built by Vite (`vite.config.mts`) through Mastodon's own vite integration (`lib/vite/*`, `config/initializers/vite.rb`).
- The streaming server is a separate Node workspace, `@mastodon/streaming` (`streaming/package.json`, `"start": "node ./index.js"`).
- Processes come from `Procfile.dev`: `web` (puma :3000), `sidekiq`, `stream` (:4000), `vite` (`yarn dev`, :3036). `bin/dev` runs them with overmind or foreman.

| Service | Required? | How the repo's dev setup provides it | Source |
|---|---|---|---|
| PostgreSQL 14 | required | compose `postgres:14-alpine` (devcontainer and CI); brew `postgresql@14` on macOS | `.devcontainer/compose.yaml:37-48`, `test-ruby.yml:78-90`, `docs/DEVELOPMENT.md:40` |
| Redis 7 | required (Sidekiq, streaming, cache) | compose `redis:7-alpine` | `.devcontainer/compose.yaml:50-56` |
| Sidekiq worker | required for background jobs (e.g. mail delivery, fan-out) | Procfile.dev `sidekiq` | `Procfile.dev:2` |
| Streaming server (Node, :4000) | optional for page loads; needed for live timeline updates | Procfile.dev `stream` | `Procfile.dev:3`, `config/initializers/1_hosts.rb:22-28` |
| Vite dev server (:3036) | effectively required in dev (Rails proxies assets to it; otherwise auto-builds with `yarn build:development`) | Procfile.dev `vite` | `config/vite.yml:8-18`, `config/initializers/vite.rb:9-10` |
| Elasticsearch 8 / OpenSearch | optional (full-text search, `ES_ENABLED`) | devcontainer compose `es` | `.devcontainer/compose.yaml:21,58-73` |
| LibreTranslate | optional (translate button) | devcontainer compose `libretranslate` | `.devcontainer/compose.yaml:24,75-82` |
| Mail catcher | optional; built in as `letter_opener_web` at `/letter_opener` when `REMOTE_DEV`/`VAGRANT`/`HEROKU` is set | gem | `config/environments/development.rb:93`, `config/routes.rb:20` |
| System libs | required: libicu, libidn, libvips, ffmpeg (media), libpq | apt in devcontainer and CI | `.devcontainer/Dockerfile:10-12`, `.github/actions/setup-ruby/action.yml:16-24` |

## Running without Docker
Yes. CI's own recipe is apt plus ruby/setup-ruby: `libicu-dev libidn11-dev libvips42 libheif-plugin-aomdec libheif-plugin-libde265` plus `ffmpeg` (`.github/actions/setup-ruby/action.yml:17-24`, `test-ruby.yml:143`). Postgres 14 and Redis 7 have no special extensions or version needs, so Ubuntu's `postgresql` and `redis-server` packages should work (guess: any PG >= 14 is fine; nothing in `config/database.yml` pins a version). Setup would look like:
- `sudo apt-get install -y build-essential libssl-dev libyaml-dev libffi-dev zlib1g-dev libreadline-dev libicu-dev libidn11-dev libvips42 ffmpeg libpq-dev postgresql redis-server`
- start postgres and redis, then create a superuser role that matches `DB_USER`/`DB_PASS` (`config/database.yml:10-17` reads `DB_HOST/DB_USER/DB_PASS/DB_PORT`)
- install Ruby 4.0.7. apt has no suitable version (guess: Ubuntu 24.04 ships Ruby 3.2, below the Gemfile minimum of 3.3), so use ruby-build/rbenv (a compile of several minutes) or a prebuilt binary (guess)
- `bundle install`, `npm i -g corepack && yarn install --immutable`, `bin/rails db:setup` (this is what `bin/setup` does: `bin/setup:16-25`)

Elasticsearch has no reasonable apt install on stock Ubuntu (it needs Elastic's apt repo and a JVM), but it is optional. LibreTranslate is pip/docker only (guess) and optional.

## Env vars and third-party services
- `RAILS_ENV` **must** be set, or boot aborts (`config/boot.rb:3-13`).
- `.env.development` is committed and provides the three ActiveRecord encryption keys (`.env.development:1-4`). VAPID keys are generated at boot in dev (`config/environments/development.rb:43-47`).
- DB/Redis: `DB_HOST DB_USER DB_PASS DB_PORT`, `REDIS_HOST REDIS_PORT` (`.devcontainer/compose.yaml:15-20`).
- Host: `LOCAL_DOMAIN` defaults to `localhost:$PORT`. Rails host authorization only admits `LOCAL_DOMAIN`/`WEB_DOMAIN`/`ALTERNATE_DOMAINS` (plus Rails' dev defaults), so a browser reaching the app through a different hostname gets blocked unless `LOCAL_DOMAIN` is set to it (`config/initializers/1_hosts.rb:3-4,30-35`). The streaming URL defaults to `ws://<LOCAL_DOMAIN host>:4000` in dev (`1_hosts.rb:22-28`).
- Puma binds to `127.0.0.1` unless `BIND` is set (`config/puma.rb:12`).
- Third parties: none needed for core flows. S3, SMTP, OIDC/SAML/CAS and ES are all opt-in (`.env.production.sample`). Mail in dev goes to `letter_opener`, or to `letter_opener_web` if `REMOTE_DEV=true`.

## Login in dev
- **Seeded admin:** username `admin`, email `admin@<LOCAL_DOMAIN without port>` (so `admin@localhost` by default), password `mastodonadmin`, role **Owner**, confirmed and approved. It is created only when `Rails.env.development?` (`db/seeds/04_admin.rb:3-22`). The docs say the same (`docs/DEVELOPMENT.md:94`).
- **Richer data:** `bin/rails dev:populate_sample_data` creates `@showcase_account` (email `showcase_account@joinmastodon.org`) with a random password (`lib/tasks/dev.rake:5-19`). You can't log in as it without resetting the password.
- **CLI account creation:** `bin/tootctl accounts create USERNAME --email X --confirmed --approve --role Moderator` prints a **random** password (`lib/mastodon/cli/accounts.rb:43-115`, `password = SecureRandom.hex` at ~line 79). For a known password, use `bin/rails runner` with the same attributes as `db/seeds/04_admin.rb` (guess, modeled on that file).
- **Roles:** base "everyone" role, plus `Moderator` (10), `Admin` (100), `Owner` (1000) (`config/roles.yml`, `db/seeds/03_roles.rb`).
- **Signup:** `registrations_mode: 'none'` by default in all envs (`config/settings.yml:12,39-40`), so signup is closed until an admin opens it. Signup also requires email confirmation (Devise confirmable; `confirmed_at` is set explicitly in the seeds). In dev you can read the mail at `/letter_opener` when `REMOTE_DEV=true`.
- Not SSO-only. Password login works. OIDC/SAML/CAS/PAM are optional (`Gemfile:34-44`).

## Seed and fixture data
`bin/rails db:seed` (or `db:setup`) creates the web OAuth app, the instance actor, the roles, the dev admin and blocked usernames (`db/seeds/01..05`). All of these use `find_or_create_by`/`first_or_initialize`, so they're re-runnable. `dev:populate_sample_data` adds varied posts (polls, media, quotes, languages) and says it "Can be run multiple times" (`lib/tasks/dev.rake:4`). A reset is `bin/rails db:reset` (drop/create/schema/seed). The drop fails while puma/sidekiq/streaming hold connections (guess, standard Postgres behaviour), so a clean reset needs the app stopped.

## Draft .groundtruth.yml
```yaml
version: 1
runtime: { node: "24" }            # .nvmrc = 24.21
env:
  RAILS_ENV: development
  NODE_ENV: development
  DB_HOST: localhost
  DB_USER: mastodon
  DB_PASS: mastodon
  DB_PORT: "5432"
  REDIS_HOST: localhost
  REDIS_PORT: "6379"
  LOCAL_DOMAIN: localhost:3000      # UNCERTAIN: must equal the host the browser uses, or Rails host authorization blocks it
  BIND: 0.0.0.0
  REMOTE_DEV: "true"               # letter_opener_web at /letter_opener instead of launching a browser
  COREPACK_ENABLE_DOWNLOAD_PROMPT: "0"
  PATH: /home/user/.rubies/4.0.7/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin  # UNCERTAIN: home dir / whether env PATH is honoured
setup:
  - sudo apt-get update -qq && sudo apt-get install -y -qq build-essential git curl libssl-dev libyaml-dev libffi-dev zlib1g-dev libreadline-dev libgmp-dev libicu-dev libidn11-dev libvips42 ffmpeg libpq-dev postgresql redis-server
  - sudo service postgresql start && sudo service redis-server start
  - sudo -u postgres psql -c "CREATE ROLE mastodon WITH LOGIN SUPERUSER PASSWORD 'mastodon'"
  # UNCERTAIN: compiling Ruby takes several minutes; a prebuilt Ruby would be faster
  - git clone --depth 1 https://github.com/rbenv/ruby-build.git /tmp/ruby-build && /tmp/ruby-build/bin/ruby-build 4.0.7 ~/.rubies/4.0.7
  - gem install bundler --conservative && bundle install --jobs 4
  - sudo npm i -g corepack && corepack enable && yarn install --immutable
  - bin/rails db:setup              # creates DB, loads schema, seeds admin@localhost / mastodonadmin
start: |
  bundle exec sidekiq &
  PORT=4000 yarn workspace @mastodon/streaming start &
  yarn dev &
  PORT=3000 bundle exec puma -C config/puma.rb
port: 3000
ready: { path: /health, timeout_seconds: 900 }   # UNCERTAIN: /health is 200 before Vite is up; first page load may be slow
reset: DISABLE_DATABASE_ENVIRONMENT_CHECK=1 bin/rails db:schema:load db:seed   # UNCERTAIN: may conflict with open connections; db:reset needs the app stopped
# personas:
#   admin:
#     description: Instance owner (seeded by db/seeds/04_admin.rb)
#     username: admin@localhost
#     password: mastodonadmin
#   alice:
#     description: Regular confirmed user, created in setup via
#       bin/rails runner "a=Account.create!(username:'alice'); User.create!(email:'alice@localhost', password:'alicepassword1', password_confirmation:'alicepassword1', confirmed_at:Time.now.utc, agreement:true, approved:true, account:a, bypass_registration_checks:true)"
#     username: alice@localhost
#     password: alicepassword1
```

## What the current format can't express
- [runtime-lang] Needs Ruby 4.0.7 (`.ruby-version`), with at least 3.3 required by the Gemfile. There's no `runtime.ruby` key, so it's installed by hand in `setup`.
- [slow-build] Compiling Ruby with ruby-build, plus `bundle install` with native gems, likely costs 5-15 minutes per fresh sandbox (guess); a cache is wanted.
- [arch-native] Native gems need libidn, libicu, libvips, libpq and ffmpeg headers (`setup-ruby/action.yml:17-24`).
- [service-db] Needs PostgreSQL (14 in compose/CI).
- [service-other] Needs Redis. Elasticsearch and LibreTranslate are optional.
- [multi-process] Four processes: puma, sidekiq, streaming (Node), vite (`Procfile.dev`).
- [multi-port] The browser opens a WebSocket straight to `ws://<host>:4000` for streaming (`1_hosts.rb:26`), and Vite HMR connects to :3036 (`vite.config.mts:118-126`). Pages still load without them, but live updates need :4000 reachable (or `STREAMING_API_BASE_URL` routed somehow).
- [host-config] `LOCAL_DOMAIN` must match the browser-facing host, because Rails host authorization blocks other hosts (`1_hosts.rb:30-35`). The admin email is also derived from it (`04_admin.rb:4-5`).
- [persona-create] Only one seeded account. More personas need a `rails runner` snippet, because tootctl only prints a random password.
- [email-verify] UI signup is closed by default and needs email confirmation. It's readable at `/letter_opener` only with `REMOTE_DEV=true`.
- [reset-hard] `db:reset` drops the DB, which needs all four processes stopped.

## Difficulty
**medium.** Everything installs from apt except Ruby, and the seeded Owner account with a known password makes login easy. The costs are a slow Ruby compile, four processes to juggle, and a second port for streaming WebSockets. Host authorization will bite if the browser reaches the sandbox by a non-localhost name.

## Candidate PRs for evaluation
1. https://github.com/mastodon/mastodon/pull/39404, "add search field to admin ip blocks" by **arte7**. Author association is CONTRIBUTOR, but the branch lives in the upstream repo (not a fork), so this is possibly a staff contractor (guess).
   - What it changes: adds an IP search box to Admin > IP rules. Searching a single IP or a range finds blocks that contain it and blocks contained in it (PR body).
   - Why it's a good case: there are 9 commits after review, including "remove partial IP search", "scope both ways for ips" and "change scope of ips depending on search param". Review flagged `192.168.0.1/24` being treated as a single IP (ClearlyClaire's review comment).
   - Browser-checkable claims: create a few IP blocks, search an IP, a range, and a contained range.
   - Persona: seeded Owner.
2. https://github.com/mastodon/mastodon/pull/39897, "Fix selected account being lost when creating a collection" by **sharlayan** (fork, outside contributor).
   - What it changes: "Add to collection…" from a profile keeps the viewed account; adds a "New collection" button when collections exist; clears the preselected account when leaving the editor.
   - Why it's a good case: three concrete UI claims. Review by diondiondion asked for rename and removal changes, and the author offered to squash, so the in-PR history is squashed to 1 commit.
   - Persona: owner plus a second local account to add to a collection. Collections appear un-flagged at HEAD (`app/javascript/mastodon/utils/environment.ts:15` lists only `fasp`/`redesign`), but whether the feature needed a flag at merge time is unverified.
3. https://github.com/mastodon/mastodon/pull/40478, "Fix missing confirmation when leaving with an unsent post" by **crafkaz** (fork, outside contributor).
   - What it changes: in the advanced interface, reloading with text in the composer shows a leave-page confirmation (single file `features/compose/index.tsx`).
   - Why it's a good case: a simple, precise claim. It tests whether the agent can check a `beforeunload` dialog. There was no in-PR fix.
   - Persona: any logged-in user, with the advanced web UI enabled in preferences.
- Alternate: https://github.com/mastodon/mastodon/pull/39256, "Add import for custom filters" by arte7. 20 commits with review fixes; needs a JSON file upload at Settings > Import.

## Sources
- README-adjacent: `docs/DEVELOPMENT.md`, `.ruby-version`, `.nvmrc`, `.env.development`, `.env.production.sample`, `Procfile.dev`, `bin/dev`, `bin/setup`, `Gemfile`, `package.json`, `streaming/package.json`, `Aptfile`
- `.devcontainer/compose.yaml`, `.devcontainer/devcontainer.json`, `.devcontainer/Dockerfile`
- `.github/workflows/test-ruby.yml`, `.github/actions/setup-ruby/action.yml`
- `config/database.yml`, `config/boot.rb`, `config/environments/development.rb`, `config/initializers/1_hosts.rb`, `config/initializers/vite.rb`, `config/vite.yml`, `vite.config.mts`, `config/puma.rb`, `config/routes.rb`, `config/settings.yml`, `config/roles.yml`, `config/mastodon.yml`
- `db/seeds/01_web_app.rb` … `04_admin.rb`, `lib/tasks/dev.rake`, `lib/mastodon/cli/accounts.rb`, `app/javascript/mastodon/utils/environment.ts`
- PRs #39404, #39897, #40478, #39256, #40398, #40367, #40327, #39085, #39473, #39696, #39339 (gh pr view, plus review comments for #39897 and #39404)
