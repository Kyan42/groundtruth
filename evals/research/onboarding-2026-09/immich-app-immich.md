# immich-app/immich

Self-hosted photo and video management (Google Photos alternative): NestJS server, SvelteKit web app, Flutter mobile app, Python machine-learning service. ~115k stars, default branch `main`, last commit seen 2026-09-28. https://github.com/immich-app/immich

## Stack and services

- Node 24.21.0 (`.nvmrc`; root `mise.toml` `[tools] node = "24.21.0"`), pnpm 11.27.0 (`mise.toml`). pnpm workspace monorepo (`pnpm-workspace.yaml`).
- `server/`: NestJS, Kysely + `pg`, BullMQ + ioredis, `sharp`, `exiftool-vendored`, `bcrypt` (server/package.json:69-112). Dev start: `nest start --watch` (server/package.json:20; server/bin/immich-dev).
- `web/`: SvelteKit on Vite; `vite dev --host 0.0.0.0 --port 3000` (web/bin/immich-web). Vite proxies `/api`, `/.well-known/immich`, `/custom.css` to `IMMICH_SERVER_URL` (default `http://immich-server:2283/`) (web/vite.config.ts:9-21). Web needs `@immich/sdk` built first (web/mise.toml `start` task; .devcontainer/server/container-start-frontend.sh).
- `machine-learning/`: Python >=3.12 (`machine-learning/pyproject.toml:6`; `machine-learning/mise.toml` python 3.12, uv 0.8.15), onnxruntime.
- `packages/plugin-core`: WASM workflow plugin built with extism js-pdk + binaryen (`mise.toml` tools; task `plugins`).
- Root `mise.toml` also pins java 21 (openapi generator), jellyfin-ffmpeg 7.1.3-6, terragrunt, opentofu -- most are not needed to run the app.
- Server port 2283 (`IMMICH_PORT` default, server/src/repositories/config.repository.ts:257-258), web dev port 3000.

| Service | Required? | How the repo's dev setup provides it | Source |
|---|---|---|---|
| PostgreSQL >=14 with VectorChord (or pgvector) + `cube`/`earthdistance` | required | docker image `ghcr.io/immich-app/postgres:14-vectorchord0.4.3-pgvectors0.2.0` | docker/docker-compose.dev.yml `database`; server/src/constants.ts:23-37; docs/docs/administration/postgres-standalone.md |
| Redis (Valkey 9) | required (BullMQ job queues) | docker `valkey/valkey:9` | docker-compose.dev.yml `redis`; config.repository.ts:199-208 (`REDIS_HOSTNAME` default `redis`) |
| Server (API + microservices workers in one process by default) | required | `immich-server` container running `immich-dev` | docker-compose.dev.yml; config.repository.ts:182 (`IMMICH_WORKERS_INCLUDE` default api + microservices) |
| Web (Vite dev server) | required | `immich-web` container running `immich-web` | docker-compose.dev.yml; web/bin/immich-web |
| Machine learning (Python, :3003) | optional (smart search, face detection/recognition, OCR-ish features) | `immich-machine-learning` container built from machine-learning/Dockerfile | docker-compose.dev.yml |
| ffmpeg (jellyfin-ffmpeg) | needed for video thumbnails/transcoding | in the `base-server-dev` image; also a mise tool | server/Dockerfile.dev FROM `ghcr.io/immich-app/base-server-dev`; mise.toml |
| Custom libvips (+libheif, libjxl, libraw, jpegli, imagemagick) | optional-ish (sharp ships its own libvips; custom build adds formats) (guess) | built from source in immich-app/base-images `server/Dockerfile` | base-images server/Dockerfile L86-144 |
| Geodata files (`/build/geodata`) | required by the metadata service init (reverse geocoding) | downloaded in base image | base-images server/Dockerfile L75-84; server/src/repositories/map.repository.ts:58-69; metadata.service.ts:160-172 |
| Core plugin (`/build/plugins/immich-plugin-core`) | loaded at microservices bootstrap | compose mounts `../packages/plugin-core` there | docker-compose.dev.yml `immich-server.volumes`; workflow-execution.service.ts:55-63 |
| Prometheus/Grafana | optional (commented out) | compose | docker-compose.dev.yml |

## Running without Docker

Possible but with real work; the repo's dev setup is docker-only (`mise dev` = `docker compose -f ./docker-compose.dev.yml up`, mise.toml `[tasks.dev]`; docs/docs/developer/setup.md "All the services are packaged to run with a single Docker Compose command"). CI e2e also uses docker compose (.github/workflows/test.yml ~L444-446, ~L523-525). There is no bare-metal recipe in the repo, but the pieces exist:
- Postgres: apt `postgresql` (16 on Ubuntu 24.04) + `postgresql-16-pgvector` from the PGDG apt repo (postgres-standalone.md "Prerequisites"). VectorChord is installed "using their instructions" and needs `shared_preload_libraries = 'vchord.so'` (same doc); VectorChord publishes .deb packages on its GitHub releases (guess -- not verified). Simpler: set `DB_VECTOR_EXTENSION=pgvector` (supported, config.repository.ts:243-252; `VECTOR_VERSION_RANGE = '>=0.5 <1'`, constants.ts:25) and use only apt pgvector. `cube`/`earthdistance` come with postgresql contrib. Immich expects superuser on the DB (postgres-standalone.md "With superuser permission").
- Redis: apt `redis-server` (guess that Redis 7 works in place of Valkey 9 -- BullMQ is compatible with both).
- Server runtime deps: `perl` for exiftool-vendored.pl (made a hard dependency in .pnpmfile.cjs), ffmpeg (apt `ffmpeg`, or mise's jellyfin-ffmpeg), sharp (pnpm-workspace.yaml `allowBuilds: sharp: true` -- builds or uses prebuilt libvips; guess prebuilt works).
- `/build` folder: set `IMMICH_BUILD_DATA` (config.repository.ts:193) to a writable dir, download the geonames/natural-earth files exactly as base-images server/Dockerfile L77-84 does (cities500.zip, admin1CodesASCII.txt, admin2Codes.txt, countryInfo.txt, ne_10m_admin_0_countries.geojson, plus a `geodata-date.txt`), and link `packages/plugin-core` into `$IMMICH_BUILD_DATA/plugins/immich-plugin-core` after `mise //:plugins` builds it. Without geodata the metadata service throws "Metadata service init failed" (metadata.service.ts:169-171); whether that kills the process is a guess.
- Media: `IMMICH_MEDIA_LOCATION` defaults to `/data` (docs/docs/install/environment-variables.md:38); must point to a writable dir without root. The server also runs "mount checks" on the media folder (`IMMICH_IGNORE_MOUNT_CHECK_ERRORS`, env docs:47) -- may need that flag on first boot (guess).
- ML: Python 3.12 + uv + `uv sync` (machine-learning/mise.toml) and model downloads on first use; skip it and disable ML in admin settings / config file (guess on exact key).

## Env vars and third-party services

- Dev compose reads `docker/.env` copied from `docker/example.env`: `UPLOAD_LOCATION=./library`, `DB_DATA_LOCATION`, `DB_PASSWORD=postgres`, `DB_USERNAME=postgres`, `DB_DATABASE_NAME=immich`, `IMMICH_VERSION`. Compose adds `IMMICH_ENV`-style vars via Dockerfile.dev (`ENV IMMICH_ENV=development`) and many `IMMICH_*` build metadata vars (docker-compose.dev.yml `immich-server.environment`).
- Without docker hostnames, set `DB_URL` or `DB_HOSTNAME=127.0.0.1` (defaults `database`, config.repository.ts:230-239), `REDIS_HOSTNAME=127.0.0.1` (default `redis`, :200), `IMMICH_ENV=development` (default production, :191), `IMMICH_SERVER_URL=http://127.0.0.1:2283` for the web proxy (vite.config.ts:10).
- No secrets need generating (JWT secret for plugins is random at runtime, workflow-execution.service.ts:72; sessions are DB tokens -- guess).
- Third parties are all optional: OAuth/OIDC (docs/docs/administration/oauth.md), SMTP for notification emails (docs/docs/guides/smtp-*.md), map tiles from an external style URL (guess), ML models downloaded from Hugging Face on first use (guess).

## Login in dev

- No seeded accounts. First visit shows onboarding; the first user registers as admin via `POST /api/auth/admin-sign-up` (server/src/controllers/auth.controller.ts:53-59, "Create the first admin user in the system"; service at server/src/services/auth.service.ts:212-221 sets `isAdmin: true`). Allowed while `IMMICH_ALLOW_SETUP` is true (default, env docs:48).
- The e2e suite uses `admin@immich.cloud` / `password` (name "Immich Admin") via that endpoint, and creates users like `user1@immich.cloud` / `password1` through the admin API (e2e/src/fixtures.ts; e2e/src/utils.ts ~L312-326). These are a natural persona set but are only created by the e2e harness.
- Command-line: the persona can be created with `curl -X POST http://127.0.0.1:2283/api/auth/admin-sign-up -H 'Content-Type: application/json' -d '{"email":...,"password":...,"name":...}'` once the server is up, then more users via `POST /api/admin/users` with the admin's token (guess on exact path, based on `createUserAdmin` in e2e/src/utils.ts:324). `immich-admin` CLI (server/bin/immich-admin; server/src/commands/index.ts) can reset the admin password, grant/revoke admin, list users, enable/disable password or OAuth login, toggle maintenance mode -- but cannot create users.
- Roles: admin vs regular user (`isAdmin`), plus per-album roles (owner/editor/viewer) and shared links (guess on album role names from PR #31805 "editors can update album title & description").
- No public self-signup and no email verification; admins create users. Password login is on by default; OAuth optional (`immich-admin enable-oauth-login`/`disable-oauth-login`).
- After first login the admin gets an onboarding wizard (theme, storage template, privacy) (guess) -- an agent may need to click through it.

## Seed and fixture data

- No seed script. Photos exist as a git submodule `e2e/test-assets` -> https://github.com/immich-app/test-assets (`.gitmodules`); the e2e suite uploads from it. A setup step could `git submodule update --init e2e/test-assets` and upload a few files with the `@immich/cli` package (`packages/cli`) or the upload API (guess on CLI flags). Thumbnail/metadata jobs then run in the background (microservices worker).
- Reset: e2e `resetDatabase` truncates `asset`, `album`, `user`, `session`, ... and deletes `system_metadata` except geocoding/system-flags (e2e/src/utils.ts:178-209) -- cheap and works while the server runs. `mise //server:schema-reset` drops the public schema and re-runs migrations (server/mise.toml `schema-drop`/`schema-reset`). Neither removes files under the media location.

## Draft .groundtruth.yml

```yaml
version: 1
runtime: { node: "24" }                       # .nvmrc 24.21.0
env:
  IMMICH_ENV: development
  DB_URL: postgres://postgres:postgres@127.0.0.1:5432/immich
  DB_VECTOR_EXTENSION: pgvector                # avoids VectorChord (config.repository.ts:243-252)
  REDIS_HOSTNAME: 127.0.0.1
  IMMICH_SERVER_URL: http://127.0.0.1:2283     # web/vite.config.ts proxy target
  IMMICH_MEDIA_LOCATION: /home/user/immich-data   # UNCERTAIN: home path of the sandbox user
  IMMICH_BUILD_DATA: /home/user/immich-build      # UNCERTAIN: holds geodata + plugins
  IMMICH_IGNORE_MOUNT_CHECK_ERRORS: "true"     # UNCERTAIN: may be needed on first boot
  CI: "1"
setup:
  - sudo apt-get update -qq && sudo apt-get install -y -qq build-essential python3 perl ffmpeg redis-server unzip curl ca-certificates postgresql-common
  - sudo /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y && sudo apt-get install -y -qq postgresql-16 postgresql-16-pgvector   # UNCERTAIN: PGDG script flags
  - sudo service postgresql start && sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'postgres';" && sudo -u postgres createdb immich
  - sudo npm install -g pnpm@11.27.0
  - pnpm install --frozen-lockfile
  - pnpm --filter @immich/sdk build && pnpm --filter @immich/plugin-sdk build
  - curl https://mise.run | sh && ~/.local/bin/mise trust && ~/.local/bin/mise install "github:extism/cli" "github:webassembly/binaryen" "github:extism/js-pdk" && ~/.local/bin/mise //:plugins   # UNCERTAIN: builds packages/plugin-core
  - mkdir -p $IMMICH_MEDIA_LOCATION $IMMICH_BUILD_DATA/geodata $IMMICH_BUILD_DATA/plugins && ln -sfn $PWD/packages/plugin-core $IMMICH_BUILD_DATA/plugins/immich-plugin-core
  - cd $IMMICH_BUILD_DATA/geodata && curl -fsSLO https://download.geonames.org/export/dump/cities500.zip && unzip -o cities500.zip && rm cities500.zip && for f in admin1CodesASCII.txt admin2Codes.txt countryInfo.txt; do curl -fsSLO https://download.geonames.org/export/dump/$f; done && curl -fsSLO https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/ne_10m_admin_0_countries.geojson && date --iso-8601=seconds | tr -d "\n" > geodata-date.txt   # mirrors base-images server/Dockerfile L77-84
start: |
  sudo service postgresql start
  sudo service redis-server start
  (cd server && pnpm exec nest start --watch) &
  until curl -sf http://127.0.0.1:2283/api/server/config >/dev/null; do sleep 2; done
  curl -s -X POST http://127.0.0.1:2283/api/auth/admin-sign-up -H 'Content-Type: application/json' -d '{"email":"admin@immich.cloud","password":"password","name":"Immich Admin"}' || true   # UNCERTAIN: create persona on first boot
  cd web && pnpm exec vite dev --host 0.0.0.0 --port 3000
port: 3000
ready: { path: /api/server/config, timeout_seconds: 900 }   # through the Vite proxy, so 200 only when web + server are up
# reset: psql TRUNCATE of asset/album/user/... as in e2e/src/utils.ts:178-209, then re-run admin-sign-up  # UNCERTAIN
# personas:
#   admin: { description: "First user, server admin (created via admin-sign-up)", username: admin@immich.cloud, password: password }
#   user1: { description: "Regular user created by admin via API", username: user1@immich.cloud, password: password1 }
#   user2: { description: "Second regular user, for sharing albums", username: user2@immich.cloud, password: password12 }
```

## What the current format can't express

- [service-db] Postgres >=14 with a vector extension (pgvector via PGDG apt, or VectorChord with `shared_preload_libraries`) plus `cube`/`earthdistance`, superuser.
- [service-other] Redis/Valkey for BullMQ; optional Python ML service on :3003 (Python 3.12 + uv + model downloads).
- [runtime-lang] ML service needs Python 3.12 (machine-learning/mise.toml); plugin build needs extism/binaryen toolchain from mise.
- [multi-process] Server (`nest start --watch`) and web (`vite dev`) are separate processes; web proxies `/api` to the server, so one browser port (3000) suffices.
- [persona-create] No seed accounts; the admin must be created by calling `POST /api/auth/admin-sign-up` after the server is up (or via the UI onboarding), other users via the admin API.
- [seed-missing] No photos; most UI features (timeline, albums, archive, people) need uploaded assets -- would need the `e2e/test-assets` submodule plus an upload step, and background jobs to finish thumbnails.
- [host-config] Paths the Docker image normally provides must be recreated: `IMMICH_BUILD_DATA` (geodata + core plugin) and `IMMICH_MEDIA_LOCATION` (default `/data`).
- [arch-native] sharp/libvips (custom-built in the base image for HEIF/JXL/RAW), bcrypt, exiftool (perl), ffmpeg.
- [slow-build] Large pnpm install, SDK + plugin-sdk + plugin builds, Nest compile, first Vite compile; ML image and models are heavy.
- [docker-only] (partial) The official dev setup, CI e2e and the custom Postgres image are all docker; no maintained bare-metal recipe, so the above is reverse-engineered.
- [second-repo] (optional) `e2e/test-assets` is a git submodule (separate repo) if used for seed photos; the base image recipe lives in immich-app/base-images.
- (new tag suggestion) [boot-hook] persona creation needs an HTTP call after the server is ready, i.e. a post-start hook rather than a setup command.

## Difficulty

hard. It can probably run without docker (apt Postgres + pgvector, apt Redis, `DB_VECTOR_EXTENSION=pgvector`, ML skipped), but the dev environment is docker-only and the server silently depends on things the base image bakes in (geodata under `/build`, the core plugin, custom libvips/ffmpeg, a `/data` media mount). There is no seed data at all: the admin must be created through the API after boot and photos must be uploaded before most browser-visible features can be checked.

## Candidate PRs for evaluation

1. https://github.com/immich-app/immich/pull/27061 -- author YarosMallorca (GitHub author_association COLLABORATOR, i.e. has repo access -- not a pure outsider). "Undo" on the archive toast restores archived assets (timeline and asset viewer). Good case: very checkable (archive a photo, click Undo, it reappears at the right timeline position); many in-PR fixes after review: "fix(web): correct timeline position on undo", "fix: restore from asset viewer", "fix(web): ignore unknown assets in album timelines", CHANGES_REQUESTED round by danieldietzler. Needs: one user with a few uploaded photos.
2. https://github.com/immich-app/immich/pull/31768 -- author kojomba (outside contributor). Adds "Leave album" for non-owners: inside a shared album, on the album card context menu, and in the album table row menu; album disappears from overview and recent albums afterwards. Good case: three stated entry points and a stated after-state; follow-up commit "refactor(web): use album event when leaving" after review. Needs two personas (owner shares an album with a second user) -- a strong test of personas.
3. https://github.com/immich-app/immich/pull/30462 -- author djadji-gueye (outside contributor). "Add to album" modal search also matches album description, and fixes highlight rendering when the match is not in the name. Good case: concrete search/highlight claims; needs an album with a description and at least one asset to open the modal. Only a merge commit after the fix, so no in-PR bug fix.
(Also considered: #27909 tag renaming v2 by jorbrock, outside contributor, ~50 commits with many fixes -- rich but server + web + migration-heavy.)

## Sources

- gh api repos/immich-app/immich (metadata), git tree of `main`
- .nvmrc, mise.toml, server/mise.toml, web/mise.toml, machine-learning/mise.toml, machine-learning/pyproject.toml, .gitmodules, .pnpmfile.cjs, pnpm-workspace.yaml
- docker/docker-compose.dev.yml, docker/example.env, server/Dockerfile.dev, server/Dockerfile, server/bin/immich-dev, server/bin/immich-admin, web/bin/immich-web, web/vite.config.ts
- .devcontainer/server/container-start-backend.sh, container-start-frontend.sh, container-common.sh
- server/package.json, server/src/repositories/config.repository.ts, server/src/constants.ts, server/src/repositories/map.repository.ts, server/src/services/metadata.service.ts, server/src/services/workflow-execution.service.ts, server/src/controllers/auth.controller.ts, server/src/services/auth.service.ts, server/src/commands/index.ts, server/src/commands/grant-admin.ts
- e2e/src/fixtures.ts, e2e/src/utils.ts
- docs/docs/developer/setup.md, docs/docs/administration/postgres-standalone.md, docs/docs/install/environment-variables.md
- .github/workflows/test.yml
- immich-app/base-images: server/Dockerfile (tree + grep)
- gh pr list (merged since 2025-06-01, web label, feat); gh pr view 31768, 27909, 30462, 27061, 30485
