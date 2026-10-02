# Onboarding study (September 2026)

What do real repos need to boot in a Groundtruth sandbox? For 15 open-source web apps, each file records the stack and services, whether dev works without Docker, env vars and third-party keys, how login and accounts work in dev, seed data, a draft `.groundtruth.yml`, what the current config format can't express, and 2–3 candidate PRs for evals.

Research only: written from each repo's docs, Makefiles, compose files, CI workflows and seed scripts. Nothing was booted, so the draft configs and difficulty ratings are untested, and lines marked "guess" are guesses. Mealie is left out (already booted, see `evals/boot/mealie.yml`).

## Repos

| Repo | Stack | Difficulty |
|---|---|---|
| [BookStack](BookStackApp-BookStack.md) | Laravel, MySQL | easy–medium (new PRs now on Codeberg) |
| [Cal.com](calcom-cal.com.md) | Next.js monorepo, Postgres | medium |
| [Chatwoot](chatwoot-chatwoot.md) | Rails + Vue, Postgres + pgvector, Redis | medium |
| [Documenso](documenso-documenso.md) | Next.js, Postgres, mail catcher | medium |
| [Gitea](go-gitea-gitea.md) | Go, SQLite | easy–medium |
| [Halo](halo-dev-halo.md) | Spring Boot + Vue, H2 | medium–hard |
| [Immich](immich-app-immich.md) | NestJS + Svelte, Postgres + pgvector, Redis | hard |
| [Karakeep](karakeep-app-karakeep.md) | Next.js, SQLite, Meilisearch | easy |
| [Mastodon](mastodon-mastodon.md) | Rails + React, Postgres, Redis | medium |
| [NetBox](netbox-community-netbox.md) | Django, Postgres, Redis | medium |
| [Outline](outline-outline.md) | Node + React, Postgres, Redis; no passwords | hard |
| [Paperless-ngx](paperless-ngx-paperless-ngx.md) | Django + Angular, SQLite, Redis | medium |
| [Plausible](plausible-analytics.md) | Elixir/Phoenix, Postgres + ClickHouse | medium |
| [Saleor](saleor-saleor.md) | Django GraphQL + separate dashboard repo | hard |
| [Twenty](twentyhq-twenty.md) | NestJS + React, Postgres, Redis | medium |

## Gaps in the config format and boot pipeline

Counted from each file's "What the current format can't express" section (a repo counts once per gap).

| Gap | Repos | What it means |
|---|---|---|
| reset-hard | 14 | No cheap re-runnable reset: seeds aren't idempotent, reset means drop + migrate + seed, often with the app stopped |
| persona-create | 12 | At most one seeded account; more roles need a CLI, a `rails runner`/SQL snippet or API calls |
| host-config | 12 | An env var (`APP_URL`, `NEXTAUTH_URL`, `LOCAL_DOMAIN`...) must equal the URL the browser uses; the config can't reference it |
| service-db | 11 | A database server (Postgres, MySQL, ClickHouse), often with extensions (pgvector, ltree) |
| service-other | 11 | Redis, a mail catcher, Meilisearch, headless Chrome... |
| slow-build | 11 | Language compiles and big installs on every fresh sandbox: caching wanted |
| runtime-lang | 11 | Ruby, Python, Go, Java, PHP, Elixir: only `runtime.node` exists |
| multi-process | 10 | 2–4 processes crammed into `start` with `&` |
| multi-port | 8 | The browser needs a second port (dev asset server, WebSockets, a separate API origin) |
| mirror | 7 | Package mirror risks like Mealie's (guesses, mostly) |
| arch-native | 7 | Native build dependencies from apt |
| email-verify | 5 | Signup or login needs reading an email |
| prod-build | 5 | A single port only with a production frontend build |
| third-party | 5 | Real keys for payments, calendars, AI |
| seed-missing | 4 | No seed data at all |
| secret-gen | 4 | A generated app secret |
| post-start-hook | 2 | Accounts or data can only be created after the server is up |
| config-file | 2 | Config is a file, not env vars |
| second-repo | 2 | A second repo at a matching version |
| sso-only | 2 | No password login (Outline for all logins; Plausible only for SSO PRs) |
