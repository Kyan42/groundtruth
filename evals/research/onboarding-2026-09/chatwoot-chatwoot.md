# chatwoot/chatwoot
Open-source omnichannel customer-support desk (live chat, email and social inboxes). Rails API + Vue 3 dashboard. ~37.3k stars, default branch `develop`, last commit seen 2026-09-28. https://github.com/chatwoot/chatwoot

## Stack and services
- Ruby **3.4.4** (`.ruby-version`, `Gemfile:3` `ruby '3.4.4'`, an exact pin). Rails, Puma, Sidekiq (`Procfile.dev`).
- Node **24.13.0** (`.nvmrc`), with `engines.node: 24.x` and `pnpm: 10.x`, and `packageManager: pnpm@10.2.0` (`package.json:153-155,180`). Vue 3 is built by Vite via vite_ruby: `bin/vite`, dev server on port 3036 (`config/vite.json:6-10`).
- The dev process list is `Procfile.dev`: `backend: bin/rails s -p 3000`, `worker: dotenv bundle exec sidekiq -C config/sidekiq.yml`, `vite: bin/vite dev`. It's run with `overmind start -f Procfile.dev` (`Makefile:35-40`, `package.json` `"dev"`) or foreman (`"start:dev"`).
- The `enterprise/` directory is in-tree. CI strips it for the CE test run (`.github/workflows/run_foss_spec.yml:121-124`), so dev includes EE code by default.

| Service | Required? | How the repo's dev setup provides it | Source |
|---|---|---|---|
| PostgreSQL 16 + **pgvector** (+ pg_trgm, pgcrypto, pg_stat_statements) | required | compose `pgvector/pgvector:pg16` (devcontainer, root compose, CI) | `.devcontainer/docker-compose.yml:29-37`, `docker-compose.yaml:85-95`, `run_foss_spec.yml:81-95`, `db/schema.rb:15-19` |
| Redis | required (Sidekiq, ActionCable, `Redis::Alfred` config cache) | compose `redis` | `docker-compose.yaml:97-105`, `config/cable.yml:1-12` |
| Sidekiq worker | required for async jobs (mail, automations, reports) | Procfile.dev `worker`; compose `sidekiq` | `Procfile.dev:3`, `docker-compose.yaml:46-62`, `config/environments/development.rb:35` |
| Vite dev server :3036 | required for the dashboard in dev (vite_ruby `autoBuild: true` is the fallback) | Procfile.dev `vite` | `config/vite.json:6-10` |
| MailHog (SMTP :1025, UI :8025) | optional (agent invites and password reset need it) | compose `mailhog` | `docker-compose.yaml:107-111`, `.devcontainer/docker-compose.yml:46-49` |
| OpenSearch/Elasticsearch | optional (advanced search) | not in compose; `OPENSEARCH_URL` | `.env.example:71-79` |
| ActionCable (websocket) | required for live updates, served by Rails at the same port 3000 | built in | `config/cable.yml` |

## Running without Docker
Mostly yes. CI's test job is the closest recipe (services, `ruby/setup-ruby`, `pnpm i`, `rake db:create`, `db:schema:load`: `run_foss_spec.yml:80-130`). The devcontainer apt list is `build-essential libssl-dev zlib1g-dev libyaml-dev postgresql-client libpq-dev imagemagick git curl` plus nodesource Node (`.devcontainer/Dockerfile.base:22-40`). It installs Ruby via rbenv/ruby-build (`Dockerfile.base:44-60`; `AGENTS.md:18-19` also says to use rbenv).

On Ubuntu:
- `sudo apt-get install -y postgresql postgresql-contrib redis-server libpq-dev libyaml-dev libvips42 imagemagick build-essential`
- pgvector: `postgresql-16-pgvector` (guess: available in Ubuntu 24.04 universe and in the PGDG apt repo; package name tracks the server major version)
- Ruby 3.4.4 via ruby-build (compile) or a prebuilt binary (guess)
- `sudo npm i -g pnpm@10`
- MailHog has no apt package (guess), but it is optional. Mailpit is a single-binary alternative (guess).

The official Ubuntu guide (https://developers.chatwoot.com/contributing-guide/environment-setup/ubuntu) is stale: it says Ruby 3.3.3 via RVM and Node 20, and doesn't cover starting the app.

## Env vars and third-party services
From `.env.example`:
- `SECRET_KEY_BASE` (`:7`)
- `FRONTEND_URL=http://0.0.0.0:3000`, used for route and mailer URLs (`:17`; `config/environments/development.rb:37`; `config/initializers/mailer.rb:7`)
- `REDIS_URL` (`:50`)
- `POSTGRES_HOST/USERNAME/PASSWORD` (`:85-87`). `config/database.yml:4-19` defaults to `localhost`, `postgres`, empty password and DB `chatwoot_dev`.
- `ENABLE_ACCOUNT_SIGNUP=false` (`:44`)
- `ACTIVE_STORAGE_SERVICE=local` (`:159`)

Other relevant config:
- AR encryption keys are only needed for MFA (`.env.example:9-14`).
- `config.hosts = nil` in development, so any hostname is accepted (`development.rb:64`). CSRF origin checking is only relaxed under `CODESPACES` (`development.rb:67-73`).
- Mail: if `SMTP_ADDRESS` is blank, Rails uses `sendmail` (`mailer.rb:34`). `LETTER_OPENER=true` switches to letter_opener in dev (`mailer.rb:37`). That launches a browser; there is no `letter_opener_web` gem in the Gemfile (only `letter_opener`, `Gemfile:232`).
- Third parties (Facebook, Instagram, Twitter, Slack, Google/Microsoft OAuth, Stripe, S3, Captain/OpenAI via Super Admin config) are all optional for the core agent inbox. The seeded web-widget inbox works without keys (`db/seeds.rb:44-46`). Captain AI features need an OpenAI key set in Super Admin (`.env.example:329-331`).

## Login in dev
- **Seeded user:** `john@acme.inc` / `Password1!`, `type: 'SuperAdmin'`, confirmation skipped, **administrator** of two accounts, "Acme Inc" and "Acme Org" (`db/seeds.rb:20-42`). The seeds only run when `!Rails.env.production?` (`db/seeds.rb:12`).
- **Login URLs:** dashboard login at `/app/login`. Super Admin console at `/super_admin` (`config/routes.rb:746-749`).
- **CLI account creation:** there is no dedicated task. Use `bin/rails runner` mirroring the seeds: `User.new(...).skip_confirmation!` then `AccountUser.create!(role: :agent)` (guess, modeled on `db/seeds.rb:28-36`). Adding an agent through the UI sends an invite email with a set-password link, which needs a mail catcher.
- **Roles:** `AccountUser.role` is `agent` or `administrator` (`app/models/account_user.rb:34`). There is also a `custom_role_id` (EE custom roles, `account_user.rb:14`), and `User.type` `SuperAdmin` for the installation-wide console.
- **Signup:** disabled by default (`ENABLE_ACCOUNT_SIGNUP` value `false`, `config/installation_config.yml:61-66`). When enabled, Devise `:confirmable` requires email confirmation (`app/models/user.rb:59-66`). Seeded users bypass it with `skip_confirmation!`.
- Not SSO-only. Email/password login is the default. Google/Microsoft OAuth are optional.

## Seed and fixture data
- `bundle exec rails db:seed` creates the two accounts, John, a web-widget inbox, a contact "jane" and one open conversation. The conversation contains sample messages of every rich type (email collect, location, cards, input select, form, articles, CSAT) plus a canned response (`db/seeds.rb:44-96`).
- **Not re-runnable:** it uses `Account.create!` and `User.new(...).save!`, so a second run fails on the duplicate email (guess, uniqueness validation).
- `db:chatwoot_prepare` loads the schema and seeds only when the DB is empty, then migrates (`lib/tasks/db_enhancements.rake:15-30`). `make db_reset` is `rails db:reset` (`Makefile:20-21`), which needs the processes stopped to drop the DB (guess).
- Richer data: `Seeders::AccountSeeder` via `bin/rails runner "Internal::SeedAccountJob.perform_now(Account.find(1))"` or Super Admin > Accounts > Seed. There's also `rails search:setup_test_data` (`AGENTS.md:7-11`).

## Draft .groundtruth.yml
```yaml
version: 1
runtime: { node: "24" }                 # .nvmrc = 24.13.0
env:
  RAILS_ENV: development
  NODE_ENV: development
  POSTGRES_HOST: localhost
  POSTGRES_USERNAME: postgres
  POSTGRES_PASSWORD: postgres
  REDIS_URL: redis://localhost:6379
  FRONTEND_URL: http://localhost:3000     # UNCERTAIN: should be the browser-facing URL (used in emails/links)
  SECRET_KEY_BASE: 0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0  # UNCERTAIN: dev may generate its own; any fixed hex works
  ACTIVE_STORAGE_SERVICE: local
  ENABLE_ACCOUNT_SIGNUP: "false"
  SMTP_ADDRESS: localhost                 # UNCERTAIN: nothing listens unless a mail catcher is added; jobs just fail
  SMTP_PORT: "1025"
  PATH: /home/user/.rubies/3.4.4/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin  # UNCERTAIN
setup:
  - sudo apt-get update -qq && sudo apt-get install -y -qq build-essential git curl libssl-dev libyaml-dev libffi-dev zlib1g-dev libreadline-dev libgmp-dev libpq-dev imagemagick libvips42 postgresql postgresql-contrib postgresql-16-pgvector redis-server  # UNCERTAIN: pgvector package name must match the installed PG major
  - sudo service postgresql start && sudo service redis-server start
  - sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'postgres'"
  - git clone --depth 1 https://github.com/rbenv/ruby-build.git /tmp/ruby-build && /tmp/ruby-build/bin/ruby-build 3.4.4 ~/.rubies/3.4.4   # UNCERTAIN: slow compile
  - gem install bundler -N && bundle install --jobs 4
  - sudo npm install -g pnpm@10 && pnpm install --frozen-lockfile
  - bundle exec rails db:chatwoot_prepare   # schema load + seeds on an empty DB (john@acme.inc / Password1!)
start: |
  bundle exec sidekiq -C config/sidekiq.yml &
  bin/vite dev &
  bin/rails s -p 3000 -b 0.0.0.0
port: 3000
ready: { path: /app/login, timeout_seconds: 900 }   # UNCERTAIN: /health returns 200 before Vite is ready; /app/login renders the SPA shell
# reset: omitted. There's no cheap reset: seeds aren't idempotent, and `rails db:reset` needs the app stopped (UNCERTAIN)
# personas:
#   admin:
#     description: SuperAdmin + administrator of "Acme Inc" and "Acme Org" (db/seeds.rb)
#     username: john@acme.inc
#     password: Password1!
#   agent:
#     description: Agent-role member of Acme Inc, created in setup via
#       bin/rails runner "u=User.new(name:'Aria Agent',email:'agent@acme.inc',password:'Password1!');u.skip_confirmation!;u.save!;AccountUser.create!(account:Account.first,user:u,role: :agent);InboxMember.create!(user:u,inbox:Inbox.first)"
#     username: agent@acme.inc
#     password: Password1!
```

## What the current format can't express
- [runtime-lang] Ruby 3.4.4, exact-pinned in the Gemfile (`Gemfile:3`), so any other patch version fails `bundle install`. There's no `runtime.ruby` key.
- [slow-build] ruby-build compile plus `bundle install` (a large Gemfile with native gems) plus `pnpm install` per fresh sandbox; a cache is wanted (guess: 10+ minutes).
- [service-db] Needs PostgreSQL 16 **with pgvector** (`enable_extension "vector"`, `db/schema.rb:19`). The pgvector apt package must match the PG major.
- [service-other] Redis is required (Sidekiq, ActionCable, config cache). A mail catcher is optional but needed for invite/reset flows.
- [multi-process] Three processes: rails, sidekiq, vite (`Procfile.dev`).
- [persona-create] Only one seeded user (a SuperAdmin/administrator). An agent-role persona needs a `rails runner` snippet; there's no rake task.
- [email-verify] UI-created agents get an invite/confirmation email to set a password, and signup (disabled by default) needs confirmation.
- [reset-hard] Seeds aren't idempotent and `db:reset` needs the app stopped.
- [host-config] `FRONTEND_URL` should equal the browser-facing URL for links and emails (`development.rb:37`). Host authorization itself is off (`config.hosts = nil`).
- [arch-native] Native gems need libpq, libyaml and imagemagick/vips.

## Difficulty
**medium.** The services are ordinary apt installs; pgvector is the one wrinkle. The seeds give a SuperAdmin with a known password and a populated conversation, so login and the first screens work immediately. The friction is the exact Ruby pin (a compile), three processes, and needing extra personas (agent vs admin) created by script, since the UI path goes through email.

## Candidate PRs for evaluation
1. https://github.com/chatwoot/chatwoot/pull/15768, "fix(dashboard): resolve type mismatch in conversation filter matching" by **vaibhavmashal** (fork, outside contributor).
   - What it changes: number custom-attribute filters use numeric input, and zero/false values are preserved when matching conversations.
   - Why it's a good case: the body gives explicit repro steps. The in-PR fixes were pushed by maintainer sojan-official: "preserve numeric and boolean custom attribute values" and "retain zero values in validation and payloads". The body says "Fresh UI verification is still pending", which suits a browser check.
   - Persona: administrator, to create number and checkbox custom attributes and set them on the seeded conversation.
2. https://github.com/chatwoot/chatwoot/pull/15084, "feat(conversation): add label search to right-click context menu" by **NickThePigeon** (fork, outside contributor).
   - What it changes: right-click a conversation > Assign label now has a fuzzy search box.
   - Why it's a good case: the in-PR fix by the author was "keep label search focused when selecting a result", followed by maintainer "Code clean up improve UI" and "Review fix" commits. The body has step-by-step test instructions (10+ labels).
   - Persona: administrator (to create labels) or agent.
3. https://github.com/chatwoot/chatwoot/pull/11222, "feat: agent language settings" by **micahmills** (fork, outside contributor; merged 2025-09-09).
   - What it changes: a per-user UI language selector in Profile Settings that falls back to the account locale, persists across refresh, and flips text direction for RTL locales.
   - Why it's a good case: long review history (13 comments) and maintainer refactors ("simplify logic, move from composable", "Minor fix"). The claims are easy to check in a browser: change the language, see the UI switch, refresh.
   - Persona: an agent with an account locale different from the user locale (two personas would be ideal).
- Alternate (staff-authored, clean in-PR fix): https://github.com/chatwoot/chatwoot/pull/15429, "fix: allow multi-digit values in duration inputs" (aakashb95). The commit "normalize duration before form submit" followed the first fix. Claim: typing `45` stays `45` in the delayed-automation form.

## Sources
- `README.md` (listing), `AGENTS.md`, `.ruby-version`, `.nvmrc`, `.env.example`, `Procfile.dev`, `Makefile`, `package.json`, `Gemfile`, `bin/setup`
- `.devcontainer/docker-compose.yml`, `.devcontainer/Dockerfile.base`, `.devcontainer/scripts/setup.sh`, `docker-compose.yaml`
- `.github/workflows/run_foss_spec.yml`
- `config/database.yml`, `config/vite.json`, `config/cable.yml`, `config/environments/development.rb`, `config/initializers/mailer.rb`, `config/installation_config.yml`, `config/routes.rb`
- `db/seeds.rb`, `db/schema.rb` (extensions), `lib/tasks/db_enhancements.rake`, `app/models/user.rb`, `app/models/account_user.rb`, `app/controllers/health_controller.rb`
- https://developers.chatwoot.com/contributing-guide/environment-setup/ubuntu (WebFetch)
- PRs #15768, #15084, #11222, #15429, #15418, #15827, #15703, #15119, #12641 (gh pr view)
