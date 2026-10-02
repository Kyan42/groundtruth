# paperless-ngx/paperless-ngx
Paperless-ngx, a self-hosted document management system: scan, OCR, index and archive documents, with a Django backend and an Angular frontend. About 46.1k stars. Default branch `dev`; `main` only tracks releases (`docs/development.md`). Last commit seen on dev: 2026-09-28. Version 3.2.1 (`pyproject.toml`, `src-ui/package.json`). https://github.com/paperless-ngx/paperless-ngx

## Stack and services
- Backend: Python >= 3.11 (`pyproject.toml` `requires-python = ">=3.11"`), Django ~5.2, Celery 5.6, Channels 4.2, allauth 65.16, managed with **uv** (`[tool.uv]`, `uv.lock`). CI tests 3.11-3.14 (`.github/workflows/ci-backend.yml` L88); the Docker image uses Python 3.14 (`Dockerfile` L33).
- Heavy Python deps: `torch>=2.13,<2.15`, which comes from an explicit index `https://download.pytorch.org/whl/cpu` (`pyproject.toml` L167-173). Also `sentence-transformers`, `llama-index-*`, `ocrmypdf`, `scikit-learn`, `tantivy`, `zxing-cpp`.
- Frontend: Angular ~22.1 in `src-ui/`, Node 24 (`docs/development.md` "Node.js (version 24+)"; `ci-frontend.yml` uses 24.x), pnpm 11.15.1 (`src-ui/package.json` `packageManager`).
- Dev compose: `.devcontainer/docker-compose.devcontainer.sqlite-tika.yml` (broker redis:7, gotenberg 8.17, tika 3.3.1.0). `scripts/start_services.sh` starts postgres:15, redis, gotenberg and tika with `docker run`.

| Service | Required? | How dev setup provides it | Source |
|---|---|---|---|
| Database | required; **SQLite by default**, PostgreSQL/MariaDB optional | SQLite file in `DATA_DIR`; postgres via `start_services.sh` | `src/paperless/settings/custom.py` L222-228 (sqlite unless `PAPERLESS_DBHOST` is set) |
| Redis/Valkey (Celery broker, channels) | required for full dev (uploads and consumption go through Celery); **not** needed by the e2e backend | devcontainer `broker`, `start_services.sh`, or `docker run valkey` | `settings/__init__.py` L249-251, L270-282, L681; `docs/development.md` step 7 |
| Celery worker | required to process uploads/tasks | manual `uv run celery --app paperless worker` | `docs/development.md` "Back end development" |
| document_consumer (watches the consume dir) | optional | manual `uv run manage.py document_consumer` | same |
| OCR toolchain (tesseract, ghostscript, unpaper, qpdf, imagemagick, poppler) | required to consume real documents; not needed to browse seeded ones | apt (bare-metal docs); CI apt | `docs/setup.md` L186-210; `ci-backend.yml` L112-115 |
| libmagic | required | apt `libmagic1` | `ci-frontend.yml` e2e job |
| Tika + Gotenberg | optional (Office/email documents) | docker only (devcontainer, start_services.sh) | `.devcontainer/docker-compose...sqlite-tika.yml` L61-72 |
| AI/LLM (OpenAI-like, Ollama) | optional, off by default | env | `settings/__init__.py` L1233 `PAPERLESS_AI_ENABLED` default NO |

## Running without Docker
Yes. There are two routes, and the repo itself has the second.
1. **Full dev.** apt `redis-server libmagic1 tesseract-ocr ghostscript unpaper qpdf imagemagick poppler-utils fonts-liberation` (package list in `docs/setup.md` L190 and L208); `uv sync`; `manage.py migrate`; then runserver + celery worker (+ consumer). Tika and Gotenberg have no reasonable apt install. They are optional (Tika would be a Java jar; Gotenberg is realistically docker-only).
2. **The Playwright e2e backend, `src-ui/e2e/backend.py`.** It needs no Redis and no Postgres. It sets `PAPERLESS_CHANNELS_BACKEND=channels.layers.InMemoryChannelLayer` and `CELERY_TASK_ALWAYS_EAGER=True`, uses SQLite in a tempdir, migrates, seeds, and runs `runserver localhost:8001`. CI runs it with only `apt-get install libmagic1` + `uv sync --no-dev --frozen` (`ci-frontend.yml` e2e-tests job). Two catches: it binds **localhost:8001 only**, and it deletes all its data on exit.

Frontend serving:
- `pnpm ng serve` (:4200) calls an **absolute** `http://localhost:8000/api/` (`src-ui/src/environments/environment.ts`), or `:8001` for the e2e config (`environment.e2e.ts`). That route needs two ports, and the session has to be established on the backend origin (`docs/development.md`: "You will need a running back end (including an active session)").
- Single-port option: `pnpm ng build --configuration production` writes to `src/documents/static/frontend/` (`src-ui/angular.json` L123-125). Django then serves the SPA at `/` through `IndexView` (`src/documents/views.py` L366; template `src/documents/templates/index.html` loads `main_js`). Static files are served by WhiteNoise (`settings/__init__.py` L138, L193). I assume WhiteNoise serves app static dirs without `collectstatic` when DEBUG is on (guess; from WhiteNoise's documented `USE_FINDERS` default).

## Env vars and third-party services
- `PAPERLESS_SECRET_KEY` is **required**. It raises if unset or `change-me` (`settings/__init__.py` L494-500).
- `PAPERLESS_DEBUG=true` for dev (`docs/development.md` step 2). DEBUG switches the cache to LocMem instead of Redis (L763-770) and adds `channels` (L169-170).
- `PAPERLESS_REDIS` (default localhost), `PAPERLESS_CHANNELS_BACKEND`, `PAPERLESS_DATA_DIR`/`MEDIA_ROOT`/`CONSUMPTION_DIR` (default to `<repo>/data`, `<repo>/media`, `<repo>/consume`; L67-75, L120-123).
- `paperless.conf.example` has one uncommented line: `PAPERLESS_SECRET_KEY=change-me`. Every other variable is documented but commented out.
- Third parties, all optional: email IMAP/SMTP (`PAPERLESS_EMAIL_*` L293-299), OAuth via allauth socialaccount (`PAPERLESS_SOCIALACCOUNT_PROVIDERS` L334), Azure Document Intelligence and OpenAI/Ollama for AI (`azure-ai-documentintelligence`, `openai` deps; AI off by default). No keys are needed for core flows.

## Login in dev
- **Seeded accounts:** the only ones in the repo come from `src-ui/e2e/backend.py` `seed_database()`: superuser `playwright` / `playwright` and plain user `viewer` / `viewer` with no permissions.
- **Docker:** creates `admin` (username from `PAPERLESS_ADMIN_USER`, default admin) with password `PAPERLESS_ADMIN_PASSWORD`, via `manage.py manage_superuser` (`src/documents/management/commands/manage_superuser.py`; `docker/rootfs/.../init-superuser/run`). It is idempotent: it skips when the user or any superuser already exists.
- **Command line:** `uv run manage.py createsuperuser` (`docs/development.md` step 6), or `PAPERLESS_ADMIN_PASSWORD=... uv run manage.py manage_superuser`.
- **First-run signup:** with zero users and zero documents, signup is open automatically so the first account can be created in the UI (`src/paperless/adapter.py` L23-37). After that, `PAPERLESS_ACCOUNT_ALLOW_SIGNUPS` controls it (default false, L324).
- **Roles:** superuser; users/groups with Django model permissions; per-object owner and view/change permissions (django-guardian in `INSTALLED_APPS`). The e2e spec `src-ui/e2e/permissions/global-permissions.spec.ts` covers them.
- **Email verification:** off unless email is configured. `ACCOUNT_EMAIL_VERIFICATION = "none" if not EMAIL_ENABLED` (L362-369).
- **Not SSO-only.** Optional allauth social login and `PAPERLESS_DISABLE_REGULAR_LOGIN` (L357). `PAPERLESS_AUTO_LOGIN_USERNAME` (L360) auto-logs-in as a user, which is useful but bypasses the login UI.

## Seed and fixture data
- **No seed command.** The only seed is `seed_database()` in `src-ui/e2e/backend.py`. It is moderately rich:
  - 61 documents: `test document 1-9` and `document 10-61`, sharing one sample PDF and a webp thumbnail
  - tags (Inbox, "Another Sample Tag", TagWithPartial), document type "Invoice Test", 2 correspondents, storage path "Testing 12"
  - a select custom field, 4 notes, and an "Inbox" saved view
  - UI settings with the tour disabled, then `document_index reindex`
- It is not re-runnable (`create_superuser` would collide; `mkdir(parents=True)` without `exist_ok`).
- **Reset:** the e2e backend resets by restarting (a fresh tempdir each run). With a persistent SQLite file, reset means stopping the app, deleting `data/` and `media/`, then migrate + seed again. There is no `flush`-style reset command in `documents/management/commands/`. Real documents can be created by dropping PDFs into `consume/` (needs the worker, consumer and OCR toolchain).

## Draft .groundtruth.yml
```yaml
version: 1
runtime: { node: "24" }
env:
  UV_DEFAULT_INDEX: https://pypi.org/simple        # as in Mealie; torch still comes from download.pytorch.org (explicit index)
  PAPERLESS_DEBUG: "true"
  PAPERLESS_SECRET_KEY: groundtruth-dev-only-not-a-real-secret
  PAPERLESS_REDIS: redis://localhost:6379
  PAPERLESS_AI_ENABLED: "false"
setup:
  - sudo apt-get update -qq && sudo apt-get install -y -qq libmagic1 redis-server build-essential
  # OCR toolchain: only needed if a PR consumes/uploads real documents
  - sudo apt-get install -y -qq tesseract-ocr ghostscript unpaper qpdf imagemagick poppler-utils fonts-liberation  # UNCERTAIN: needed for upload claims only
  - sudo npm install -g pnpm@11
  - curl -LsSf https://astral.sh/uv/install.sh | sh
  - ~/.local/bin/uv sync --frozen --no-dev          # UNCERTAIN: torch CPU wheel (~hundreds of MB) makes this slow
  - cd src-ui && pnpm install --frozen-lockfile && pnpm ng build --configuration production   # UNCERTAIN: build time/memory
  - mkdir -p consume media data
  - cd src && ~/.local/bin/uv run --no-sync manage.py migrate --no-input
  # Reuse the Playwright seed (users playwright/playwright + viewer/viewer, 61 docs). UNCERTAIN: seed_database reads
  # PAPERLESS_MEDIA_ROOT from os.environ, so set it; it is not re-runnable.
  - |
    cd src && PAPERLESS_MEDIA_ROOT="$PWD/../media" ~/.local/bin/uv run --no-sync python -c "
    import os, sys, django; os.environ.setdefault('DJANGO_SETTINGS_MODULE','paperless.settings')
    sys.path.insert(0, '../src-ui/e2e'); django.setup()
    from django.conf import settings; settings.CELERY_TASK_ALWAYS_EAGER = True
    import backend; backend.seed_database()"
start: |
  redis-server --daemonize yes
  cd src
  ~/.local/bin/uv run --no-sync celery --app paperless worker -l INFO &
  ~/.local/bin/uv run --no-sync manage.py document_consumer &     # UNCERTAIN: only for consume-dir claims
  ~/.local/bin/uv run --no-sync manage.py runserver 0.0.0.0:8000
port: 8000
ready: { path: /accounts/login/, timeout_seconds: 900 }   # same URL Playwright waits on (src-ui/playwright.config.ts)
# reset: not expressible cheaply; needs app stopped, rm -rf data media, migrate, re-seed
# personas:
#   - name: admin
#     description: Superuser seeded by src-ui/e2e/backend.py
#     username: playwright
#     password: playwright
#   - name: viewer
#     description: Plain user with no permissions (tests permission-denied UI)
#     username: viewer
#     password: viewer
```

## What the current format can't express
- [runtime-lang] Needs Python 3.11+ with uv. There is no `runtime.python` key.
- [service-other] Full dev needs Redis for the Celery broker and channels. The e2e backend avoids it only by setting eager mode in code, which no env var can do.
- [multi-process] runserver, celery worker, and an optional document_consumer.
- [multi-port] Dev mode (`ng serve` :4200) calls the API at an absolute `localhost:8000` (or `:8001` for e2e), so the browser must reach two origins.
- [prod-build] The single-port route needs `ng build --configuration production` into `src/documents/static/frontend/`, so frontend PRs need a full Angular build on every boot.
- [slow-build] `uv sync` pulls torch (CPU), sentence-transformers and llama-index. The Angular prod build adds more time (both guesses; not measured).
- [mirror] Uses PyPI plus the explicit `download.pytorch.org` index (`pyproject.toml` L170-173). A mirror override of the default index does not cover it.
- [secret-gen] `PAPERLESS_SECRET_KEY` is mandatory (a fixed dev value works).
- [persona-create] Accounts exist only through the e2e seed function, `createsuperuser`, or `manage_superuser`. There is no seed command.
- [seed-missing] No first-class seed command. The seed is embedded in a test harness script and is not idempotent.
- [reset-hard] Reset means deleting the SQLite DB and media while the app is stopped, then migrating and re-seeding.
- [docker-only] Tika and Gotenberg (Office/email parsing) are realistically docker-only. They are optional.
- [arch-native] OCR toolchain via apt, plus native wheels (zxing-cpp, tantivy, torch).

## Difficulty
**medium.** The repo already has a Docker-free, Redis-free, SQLite boot path with seeded users and documents (`src-ui/e2e/backend.py`), and CI proves it needs only libmagic. Two things keep it from easy: that path is localhost-only, two-port and ephemeral; and the single-port path needs an Angular production build plus a very heavy `uv sync` (torch). Upload/OCR claims add Redis, a worker and the apt OCR stack.

## Candidate PRs for evaluation
1. https://github.com/paperless-ngx/paperless-ngx/pull/12095: "Enhancment: Formatted filename for single document downloads". Author JanKleine (CONTRIBUTOR, outside; maintainers are shamoon/stumpylog). Adds a "use formatted filename" option to the single-document download menu, backed by `documents/views.py`. The downloaded filename follows the storage path, and the seed puts 8 docs in storage path "Testing 12". It has 8 commits. The maintainer's review suggestions (drop a redundant param, simplify `download()`, disable the button while downloading) were applied in the PR, and there was a test merge-conflict fix. Persona: admin (playwright).
2. https://github.com/paperless-ngx/paperless-ngx/pull/12271: "Enhancement: Show more document details in merge dialog". Author svenstaro (first-time contributor). The merge dialog shows correspondent and other details next to titles. Before/after screenshots are in the body. Easy to check: select 2+ seeded docs with correspondents, then Merge. Four review suggestions from shamoon were applied in the PR ("Apply suggestions from code review"). Persona: admin.
3. https://github.com/paperless-ngx/paperless-ngx/pull/13501: "Fix: parse unpadded yyyy-mm-dd date input regardless of locale". Author Se1foo (CONTRIBUTOR). Typing `2023-5-4` into a date field now yields May 4 2023 instead of a garbage date. The body gives a precise before/after table per locale format. Checkable in the document list date filter or a document's created date. The persona can change the date locale in Settings. Single commit, no in-PR fix.

## Sources
- `README.md` (listed), `docs/development.md`, `docs/setup.md` (bare-metal section), `paperless.conf.example`, `.env`
- `pyproject.toml`, `Dockerfile`, `.github/workflows/ci-backend.yml`, `ci-frontend.yml`
- `.devcontainer/docker-compose.devcontainer.sqlite-tika.yml`, `scripts/start_services.sh`, `docker/rootfs/etc/s6-overlay/s6-rc.d/init-superuser/run`
- `src/paperless/settings/__init__.py`, `src/paperless/settings/custom.py`, `src/paperless/adapter.py`, `src/paperless/urls.py`, `src/documents/views.py`, `src/documents/templates/index.html`, `src/documents/management/commands/manage_superuser.py`
- `src-ui/package.json`, `src-ui/angular.json`, `src-ui/playwright.config.ts`, `src-ui/e2e/backend.py`, `src-ui/src/environments/*.ts`
- PRs 13501, 12271, 11899, 12095 (bodies, commits, review comments)
