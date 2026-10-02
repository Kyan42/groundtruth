# go-gitea/gitea
Gitea, a self-hosted Git forge (repos, issues, PRs, packages, Actions CI) written in Go with a Vite/Vue/TS frontend. About 58.2k stars. Default branch `main`. Last commit seen: 2026-09-28 ("chore(deps): update dependencies (#39462)"). https://github.com/go-gitea/gitea

## Stack and services
- Go: `go.mod` declares `module gitea.dev`, `go 1.27`, `toolchain go1.27.1` (L1-5). CI installs Go from `go-version-file: go.mod` (`.github/actions/go-setup/action.yml`).
- Node `>= 22.18.0`, pnpm `>= 11.0.0`, with `packageManager: pnpm@12.6.0` (`package.json` L3-7). CI uses Node 26 (`.github/actions/node-setup/action.yml`).
- Frontend: Vite (`vite.config.ts`), Vue, Tailwind. The build output goes to `public/assets`, via `pnpm exec vite build` (`Makefile` L603-608).
- `CGO_ENABLED ?= 0` by default (`Makefile` L42). SQLite uses the pure-Go `modernc.org/sqlite` (`go.mod`), so no C toolchain is needed. `docs/development.md` says "SQLite support is compiled in by default, which is enough for local development."
- Also needed: `make`, and the `git` CLI (Gitea shells out to git for repositories; standard). Git LFS is needed only for integration tests (`docs/build-setup.md`). Python/uv is needed only for some linters.
- Devcontainer: `mcr.microsoft.com/devcontainers/go:1.26-trixie` plus node, git-lfs, uv, python and sqlite features, with `postCreateCommand: make deps` and port 3000 (`.devcontainer/devcontainer.json`). There is no docker-compose for dev.

| Service | Required? | How the repo's dev setup provides it | Source |
|---|---|---|---|
| Database: SQLite (file) | required, embedded | built in; e2e script writes `[database] DB_TYPE = sqlite3` | `tools/test-e2e.sh` L97-103; `docs/development.md` |
| MySQL / Postgres / MSSQL | optional alternatives | CI db-tests only | `.github/workflows/pull-db-tests.yml`, `go.mod` drivers |
| Redis | optional (cache/queue/session) | not in dev setup | `go.mod` (`go-redis/v9`) |
| Vite dev server | optional (`make watch-frontend`); Gitea proxies it through its own port | manual | `Makefile` L375-376; `modules/public/vitedev.go` |
| Mail (SMTP) | optional, off unless configured | none | `routers/web/auth/auth.go` L782 (`setting.MailService == nil`) |
| Actions runner (act_runner) | optional, only for Actions features | separate binary/repo (not read) | (guess) |

There are no background worker processes. Queues, cron and indexers run in-process inside `gitea web` (standard Gitea behaviour; guess, not re-verified here).

## Running without Docker
Yes, and easily. The e2e CI job does exactly this on `ubuntu-latest` with no services (`.github/workflows/pull-e2e-tests.yml`):
1. `make deps-frontend`, `make frontend`, `make deps-backend`, `TAGS=bindata make backend`
2. `tools/test-e2e.sh` writes a temp `custom/conf/app.ini`: sqlite3, `INSTALL_LOCK = true`, `ENABLE_CAPTCHA = false`, `DISABLE_REGISTRATION = false` (L97-126)
3. It sets `GITEA_WORK_DIR` (L129) and starts `./gitea web &` (L135)
4. It polls the URL, then runs `./gitea admin user create --username e2e-admin --password password --email ... --must-change-password=false --admin` (L163-172)

On Ubuntu:
- Go 1.27.1 is not in apt at that version. Use the go.dev tarball, or set `GOTOOLCHAIN=auto` (the devcontainer does) so an older Go downloads the right toolchain.
- Node comes from `runtime`. pnpm comes from `npm i -g pnpm@12.6.0` or corepack.
- `make` and `git` come from apt.

Dev-mode notes:
- `make watch-backend` uses `air` with `GITEA_RUN_MODE=dev` (`Makefile` L379-380; `.air.toml`). `make watch` runs both watchers (`tools/watch.sh`).
- A non-bindata build reads templates and assets from the working dir. Run it from the repo root (`docs/build-source.md` "Changing default paths").
- **Single port:** `modules/public/vitedev.go` proxies Vite requests through Gitea's own HTTP port, because "only Gitea web server's port is exposed" (doc comment on `ViteDevMiddleware`). It finds the Vite port from the file `public/assets/.vite/dev-port`. A plain `make frontend` build avoids Vite entirely.

## Env vars and third-party services
- Configuration is an **ini file**, `custom/conf/app.ini` under the work path (`docs/build-source.md`), not env vars. Env overrides of the form `GITEA__section__KEY` exist for the Docker image through `environment-to-ini` (guess: I did not verify native support in the binary). Plan to write app.ini with a heredoc, as the e2e script does.
- Secrets are handled without setup work:
  - An empty `SECRET_KEY` falls back to a built-in default (`modules/setting/security.go` L120-125).
  - `INTERNAL_TOKEN` is auto-generated and saved to app.ini when `INSTALL_LOCK` is true (L173-178, `generateSaveInternalToken` L98-115).
- `ROOT_URL` defaults to `http://localhost:{HTTP_PORT}/`. `PUBLIC_URL_DETECTION = auto`, the default since 1.26, uses the request `Host` header (`custom/conf/app.example.ini` L60-74), so a proxied sandbox hostname should work without setting `ROOT_URL` (guess: not tested). Clone URLs and emails still use `ROOT_URL`.
- Third parties are all optional: OAuth2/OpenID sources, SMTP, mCaptcha/reCAPTCHA (captcha is off by default, `app.example.ini` L861), avatars (Gravatar/Libravatar), S3/MinIO storage, and the external Actions runner. No keys are needed for core flows.

## Login in dev
- **Seeded accounts:** none. `models/fixtures/*.yml` (78 files) are unit/integration test fixtures only, loaded by the test harness.
- **First-run install page:** if `INSTALL_LOCK = false` (the `app.example.ini` L442 default), every request serves the installer at `/` (`routers/install/routes.go` L30-36). It asks for DB, paths and an optional admin account. Setting `INSTALL_LOCK = true` in app.ini skips it (`tools/test-e2e.sh` L108). Do that.
- **First registered user becomes admin:** `handleUserCreated` makes the only user an active admin (`routers/web/auth/auth.go` L690-711).
- **CLI:** `gitea admin user create --username U --password P --email E [--admin] [--restricted] [--must-change-password=false] [--access-token]` (`cmd/admin_user_create.go` L24-97).
  - `--must-change-password` defaults to true for everyone except the first user (L68-69). Always pass `=false`.
  - The command calls `setting.MustInstalled()` and `db.InitEngine`, not a migration (`cmd/helper.go` L50-60). The schema must already exist, so run `gitea migrate` first (`cmd/migrate.go`), or run the command after `gitea web` has started, as the e2e script does. The migrate-first variant is a guess, not tested.
  - Related subcommands: `change-password`, `delete`, `list`, `must-change-password`, `generate-access-token` (`cmd/admin_user_*.go`).
- **Roles:**
  - site admin
  - regular user
  - restricted user (`--restricted`)
  - bot (`--user-type`)
  - per-repo collaborator permissions (read/write/admin)
  - org owner/member via teams
- **Email verification:** `REGISTER_EMAIL_CONFIRM` defaults to false (`app.example.ini` L810). Registration is open by default (`DISABLE_REGISTRATION = false`, L823).
- **SSO:** not required. Local password login is the default.

## Seed and fixture data
- There is no seed command. A fresh instance has no repos, orgs or issues.
- Options:
  - Create data through the UI.
  - Call the REST API after start, e.g. `POST /api/v1/user/repos {"name":"demo","auto_init":true}` with basic auth (guess: standard Gitea API, not re-read here).
  - Pass `--access-token` to `admin user create` and use that token.
- Seeding needs the server running, and git repo init needs the `git` binary.
- Reset: stop the server and delete the data dir / sqlite file (`[database] PATH`), then re-run `gitea migrate` and re-create users. There is no reset command. The SQLite migrate on an empty DB should take seconds (guess).

## Draft .groundtruth.yml
```yaml
version: 1
runtime: { node: "24" }                      # package.json engines: node >= 22.18.0
env:
  GOTOOLCHAIN: auto                          # as in .devcontainer/devcontainer.json
  GITEA_RUN_MODE: dev                         # UNCERTAIN: used by `make watch-backend`; may just enable template reload
setup:
  - sudo apt-get update -qq && sudo apt-get install -y -qq make git curl
  - curl -fsSL https://go.dev/dl/go1.27.1.linux-amd64.tar.gz | sudo tar -C /usr/local -xz   # UNCERTAIN: arch; version from go.mod toolchain line
  - sudo npm install -g pnpm@12.6.0          # package.json packageManager
  - make frontend                             # pnpm install --frozen-lockfile + vite build -> public/assets
  - PATH=/usr/local/go/bin:$PATH make backend # go generate + go build -> ./gitea (no bindata: reads templates from repo)
  - |
    mkdir -p custom/conf data
    cat > custom/conf/app.ini <<'EOF'
    [database]
    DB_TYPE = sqlite3
    PATH = data/gitea.db
    [server]
    HTTP_ADDR = 0.0.0.0
    HTTP_PORT = 3000
    [security]
    INSTALL_LOCK = true
    [service]
    ENABLE_CAPTCHA = false
    DISABLE_REGISTRATION = false
    [log]
    MODE = console
    LEVEL = Info
    EOF
  - ./gitea migrate                            # UNCERTAIN: needed so `admin user create` finds tables (cmd/helper.go initDB does not migrate)
  - ./gitea admin user create --username gtadmin --password 'gt-password-1' --email gtadmin@example.com --admin --must-change-password=false
  - ./gitea admin user create --username alice --password 'gt-password-1' --email alice@example.com --must-change-password=false
  - ./gitea admin user create --username bob --password 'gt-password-1' --email bob@example.com --must-change-password=false
start: ./gitea web                             # single process, port from app.ini
reset: pkill -f 'gitea web'; rm -f data/gitea.db && ./gitea migrate && ...   # UNCERTAIN: needs app stopped and restarted; not expressible cleanly
port: 3000
ready: { path: /user/login, timeout_seconds: 900 }   # /api/healthz also exists (routers/web/web.go L309) but is served by the installer too
# personas:
#   admin: { description: "Site admin (can see /-/admin)", username: gtadmin, password: gt-password-1 }
#   alice: { description: "Regular user, repo owner", username: alice, password: gt-password-1 }
#   bob:   { description: "Second regular user, for collaborator/review flows", username: bob, password: gt-password-1 }
```

## What the current format can't express
- [runtime-lang] Needs Go 1.27.1 (`go.mod`) plus pnpm 12. There is no `runtime.go` key, so Go comes from a tarball in `setup`.
- [slow-build] The first `make backend` compiles all of Gitea: `go generate` plus a full `go build` of a large module (several minutes; guess). `pnpm install` plus `vite build` adds more. A Go module and pnpm store cache would help.
- [seed-missing] No seed data. Repos, issues, milestones and PRs must be created per run through UI or API, and API seeding needs the server up.
- [post-start-hook] (new tag) The upstream recipe creates users *after* `gitea web` starts (`tools/test-e2e.sh` L163-172). Any API-based seeding of repos/issues also needs a running server. There is no hook for "run after ready".
- [persona-create] Accounts come from `gitea admin user create`. This works well, but only as setup commands, and ordering against migration matters.
- [reset-hard] No reset command. It requires stop, delete the sqlite file, migrate, re-create users and restart. `reset` runs while the app is running.
- [config-file] (new tag) Configuration is an ini file (`custom/conf/app.ini`), not env vars. It has to be written by a heredoc in `setup`. A `files:` key would be cleaner.
- [mirror] Go module proxy and npm registry access. The Mealie run hit Runloop mirror issues, so `GOPROXY`/npm registry overrides may be needed (guess).

## Difficulty
**easy-medium.** It is one self-contained binary with embedded SQLite, no external services, auto-generated secrets, a documented `INSTALL_LOCK` switch to skip the installer, and a first-class CLI for users. CI's e2e job is effectively a ready-made Docker-free boot script. The costs are build time (Go plus Vite on every fresh sandbox) and the lack of seed data: most PR claims need a repo, issue or PR to exist first, which the agent must create in the UI or which needs a post-start seeding hook.

## Candidate PRs for evaluation
1. https://github.com/go-gitea/gitea/pull/36741 by WinterCabbage (CONTRIBUTOR, outside), merged 2026-02-26. "Fix milestone/project text overflow in issue sidebar."
   - **Checkable claims:** long unbroken milestone/project names no longer overflow the issue sidebar or its dropdown, and full names are visible.
   - **In-PR iteration:** reviewer lunny asked for full-text tips. The contributor added tooltips, then switched to wrapping ("Use wrapping for issue sidebar long unbroken milestone/project names"), for 3 non-merge commits.
   - **Setup:** one persona (repo owner). The agent must create a repo, a milestone with a long name, and an issue, all through the UI.
2. https://github.com/go-gitea/gitea/pull/35156 by bartvdbraak (CONTRIBUTOR, outside), merged 2025-07-26. "Only hide dropzone when no files have been uploaded."
   - **Checkable claims:** the attachment dropzone in the issue/comment editor stays visible once a file is uploaded, and hides again when inactive and empty (Fixes #35125).
   - **In-PR fix:** wxiaoguang showed the first version was broken ("`null?.foo()` is always `undefined`, then `hasUploadedFiles` becomes `true`"). Follow-up commits "Replace fragile selectors..." plus 4 applied suggestions fixed it.
   - **Setup:** one persona, a repo and an issue, plus a file to upload (the agent needs file-upload capability).
3. https://github.com/go-gitea/gitea/pull/34730 by bytedream (CONTRIBUTOR, outside), merged 2025-06-19. "Add repo file tree item link behavior."
   - **Checkable claims:** file tree items are real `<a>` links, middle-click and ctrl/meta-click open a new tab, and a plain click still loads in-page without a reload.
   - **In-PR fixes:** 23 commits, including "prevent page reload on subtree load", "check meta key in addition to ctrl", and wxiaoguang refactors.
   - **Setup:** a repo with nested directories. `auto_init` only gives a README, so this needs git pushes or the web editor.
   - Alternative: #38689 (brechtvl, commit avatar stacks; needs commits with multiple author emails, which is harder to seed).

## Sources
- `README.md` (tree), `docs/development.md`, `docs/build-setup.md`, `docs/build-source.md`
- `go.mod`, `package.json`, `Makefile` (build/frontend/backend/watch/test-e2e/deps targets), `.air.toml`, `tools/watch.sh`, `tools/test-e2e.sh`
- `.devcontainer/devcontainer.json`, `.github/workflows/pull-e2e-tests.yml`, `.github/actions/node-setup/action.yml`, `.github/actions/go-setup/action.yml`
- `custom/conf/app.example.ini`, `modules/setting/security.go`, `modules/public/vitedev.go`
- `cmd/admin_user_create.go`, `cmd/helper.go`, `cmd/migrate.go`, `routers/web/auth/auth.go`, `routers/install/routes.go`, `routers/web/web.go`
- `gh pr list` (label topic/ui, merged since 2025-06); `gh pr view` #38689, #39285, #36384, #34730, #35156, #36741; review and issue comments on #36741 and #35156
