# netbox-community/netbox
NetBox, a network "source of truth" web app built on Django (DCIM and IPAM). About 21.6k stars. Default branch `main`, where patch releases are made; minor-release work goes to `feature`. Last commit seen on main: 2026-09-26. Current release is 4.7.1 (`netbox/release.yaml`). https://github.com/netbox-community/netbox

## Stack and services
- Python >= 3.12 (`pyproject.toml` `requires-python = ">=3.12"`; `netbox/netbox/settings.py` ~L55 raises on anything older). CI tests 3.12, 3.13 and 3.14 (`.github/workflows/ci.yml`, `test` job matrix).
- Django 6.1.1, django-rq 4.2, psycopg[c,pool] 3.3.5, strawberry-graphql (`requirements.txt`).
- Frontend: TypeScript/Sass in `netbox/project-static/src`, built with yarn v1 on Node 20 in CI (`ci.yml` `frontend` job). **The compiled bundles are committed** under `netbox/project-static/dist/` (for example `dist/netbox.js`), and `scripts/verify-bundles.sh` checks in CI that they match the source. You can boot without Node.
- There is no docker-compose or devcontainer in this repo. The official Docker setup lives in the separate netbox-docker repo, which I did not read.

| Service | Required? | How the repo's dev setup provides it | Source |
|---|---|---|---|
| PostgreSQL >= 15 with the `ltree` extension | required | manual apt install (docs); CI `services: postgres:18` | `docs/installation/1-postgresql.md` L5-6; `ci.yml` services; `netbox/dcim/migrations/0242_ltree_paths.py` L85 `CreateExtension('ltree')` |
| Redis >= 6 (task queue db 0, cache db 1) | required (a startup check requires the `REDIS` setting) | manual apt `redis-server`; CI `services: redis:8` | `docs/installation/2-redis.md` L8-14; `settings.py` L75-77 |
| RQ worker (`manage.py rqworker`) | optional; needed for background jobs such as custom scripts, data-source sync and webhooks | manual; `contrib/netbox-rq.service` | `netbox/core/management/commands/rqworker.py`; `docs/installation/3-netbox.md` |
| SMTP | optional | `EMAIL` setting, default localhost:25 | `configuration_example.py` L132-141 |
| Static docs at /static/docs | optional | `manage.py upgrade --build-docs` (zensical) | `settings.py` L629-636 |

## Running without Docker
Yes. The project's own dev docs and CI both install everything directly (`docs/development/getting-started.md`, "Setting up a Development Environment").
- PostgreSQL: `sudo apt install -y postgresql`. On Ubuntu 24.04 this gives PG 16, which meets the >= 15 requirement; the docs show `psql (16.11 (Ubuntu 16.11-0ubuntu0.24.04.1))` (`1-postgresql.md` L54). Ubuntu 22.04 ships PG 14, which is too old, so you would need the PGDG apt repo (guess, based on the stock 22.04 version). `ltree` is in core contrib, and the migrations create it when the DB user has CREATE on the database (`docs/installation/upgrading.md` L62-85).
- Redis: `sudo apt install -y redis-server`.
- Python: `python3 python3-venv python3-dev` plus `build-essential libpq-dev`, because `psycopg[c]` compiles against libpq (install list in `3-netbox.md` L13).
- No service needs Docker.
- Configuration is a **Python file**, not env vars. Copy `netbox/netbox/configuration_example.py` to `configuration.py`, or point `NETBOX_CONFIGURATION` at a module (`netbox/netbox/settings_utils.py` `load_configuration`, L192-204). In a git checkout the default module is `netbox.configuration`.
- Static files: `runserver` serves them only when `DEBUG=True`. Otherwise add `--insecure`, which the install doc uses in `3-netbox.md` L293.

## Env vars and third-party services
- Required settings: `ALLOWED_HOSTS`, `SECRET_KEY`, `REDIS`, and `DATABASES`/`DATABASE` (`settings.py` L75-81). `SECRET_KEY` must be at least 50 characters (`settings.py` L247). `netbox/generate_secret_key.py` or `netbox secret-key` (`netbox/netbox/cli.py`) will generate one.
- `API_TOKEN_PEPPERS` is optional. Without it you only get a warning, and v2 API tokens are unusable (`settings.py` L254-257).
- `configuration_testing.py` is a complete working config (DB/user/password all `netbox`, localhost Redis, fixed SECRET_KEY). It also loads the dummy test plugins (`PLUGINS = ['netbox.tests.dummy_plugin', ...]`), which would add test menu items to the UI, so it is not ideal for a demo.
- No third-party keys are needed for core flows. Optional integrations include LDAP/SAML/social auth (extras `ldap`, `saml2` in `pyproject.toml`; `SOCIAL_AUTH_*` in `settings.py` L739-767), S3/Swift storage, Sentry, and a git data source.

## Login in dev
- **Seeded account:** none in the repo. The public demo data from netbox-community/netbox-demo-data (`sql/netbox-demo-v4.7.sql`, ~3.3 MB) includes user `admin` / password `admin` (that repo's README: "accessed using the username `admin` and password `admin`").
- **Command line:** `python netbox/manage.py createsuperuser` (`3-netbox.md` L278-282). This is Django's standard command, so a non-interactive run with `--noinput` plus `DJANGO_SUPERUSER_USERNAME/PASSWORD/EMAIL` should work (guess: I did not check for a NetBox override; `users` has no `createsuperuser` command in the tree listing).
- **Roles:** superuser/staff flags, groups, and constraint-based ObjectPermissions (`EXEMPT_VIEW_PERMISSIONS`, `DEFAULT_PERMISSIONS` in `configuration_example.py` L150; `configuration_testing.py` sets `DEFAULT_PERMISSIONS = {}`).
- **Signup:** there is no self-signup. `LOGIN_REQUIRED` defaults to True (`settings.py` L170), so every page redirects to `/login/`. No email verification.
- **SSO:** optional. `REMOTE_AUTH_ENABLED` (`configuration_example.py` L200) and social-auth. The local login form stays on unless `LOGIN_FORM_HIDDEN` is set (L175).

## Seed and fixture data
- **In the repo:** nothing. `docs/development/getting-started.md` ("Populating Demo Data") points to netbox-demo-data.
- **netbox-demo-data:** one PostgreSQL dump per minor version, up to `netbox-demo-v4.7.sql`. It has rich data (sites, racks, devices, cables, IPAM; it is the data behind demo.netbox.dev). Load it into an empty DB with `psql netbox < dump`.
- **Reset:** re-run drop → create → load. `scripts/load_database.sh` (repo) does DROP/CREATE/GRANT/load via `sudo -u postgres`. A plain `DROP DATABASE` fails while the app holds connections (`CONN_MAX_AGE=300`), so a reset while the app runs would need `DROP DATABASE ... WITH (FORCE)` (PG 13+; guess that Django reconnects cleanly afterwards). A ~3 MB load should take seconds (guess). If the PR adds migrations, run `manage.py migrate` after loading.
- **Migrating an empty DB** from scratch runs every app's migrations (there are hundreds). Probably 1-3 minutes (guess).

## Draft .groundtruth.yml
```yaml
version: 1
workdir: "."
env:
  PIP_INDEX_URL: https://pypi.org/simple   # UNCERTAIN: mirror workaround analogous to Mealie's UV_DEFAULT_INDEX
setup:
  - sudo apt-get update -qq && sudo apt-get install -y -qq postgresql redis-server python3 python3-venv python3-dev build-essential libpq-dev curl
  # UNCERTAIN: service manager in the devbox; may need `sudo pg_ctlcluster 16 main start` instead
  - sudo service postgresql start && (redis-server --daemonize yes || true)
  - sudo -u postgres psql -c "CREATE ROLE netbox LOGIN SUPERUSER PASSWORD 'netbox'"
  - sudo -u postgres createdb -O netbox netbox
  - python3 -m venv ~/.venv/netbox && ~/.venv/netbox/bin/pip install -q -r requirements.txt
  - |
    cat > netbox/netbox/configuration.py <<'EOF'
    ALLOWED_HOSTS = ['*']
    DATABASES = {'default': {'ENGINE': 'django.db.backends.postgresql', 'NAME': 'netbox', 'USER': 'netbox',
                 'PASSWORD': 'netbox', 'HOST': 'localhost', 'PORT': '', 'CONN_MAX_AGE': 300}}
    REDIS = {'tasks': {'HOST': 'localhost', 'PORT': 6379, 'USERNAME': '', 'PASSWORD': '', 'DATABASE': 0, 'SSL': False},
             'caching': {'HOST': 'localhost', 'PORT': 6379, 'USERNAME': '', 'PASSWORD': '', 'DATABASE': 1, 'SSL': False}}
    SECRET_KEY = 'groundtruth-dev-only-secret-key-0123456789abcdefghijklmnopqrstuvwxyz'
    DEBUG = True
    EOF
  # Demo data (contains admin/admin). UNCERTAIN: pick dump matching the PR's base minor version
  - curl -fsSL -o /tmp/netbox-demo.sql https://raw.githubusercontent.com/netbox-community/netbox-demo-data/master/sql/netbox-demo-v4.7.sql  # default branch is master (checked)
  - psql "postgresql://netbox:netbox@localhost/netbox" -q < /tmp/netbox-demo.sql
  - ~/.venv/netbox/bin/python netbox/manage.py migrate --no-input   # applies PR migrations on top of the dump
start: |
  sudo service postgresql start || true      # UNCERTAIN: whether daemons from setup survive into start
  redis-server --daemonize yes || true
  cd netbox
  ~/.venv/netbox/bin/python manage.py rqworker &   # listens on high/default/low by default (core/management/commands/rqworker.py); optional for most UI PRs
  ~/.venv/netbox/bin/python manage.py runserver 0.0.0.0:8000
reset: |
  sudo -u postgres psql -c "DROP DATABASE netbox WITH (FORCE)" && sudo -u postgres createdb -O netbox netbox \
    && psql "postgresql://netbox:netbox@localhost/netbox" -q < /tmp/netbox-demo.sql \
    && ~/.venv/netbox/bin/python netbox/manage.py migrate --no-input   # UNCERTAIN: safe while runserver holds connections
port: 8000
ready: { path: /login/, timeout_seconds: 900 }   # / redirects to /login/ (LOGIN_REQUIRED); UNCERTAIN whether ready follows redirects
# personas:
#   - name: admin
#     description: Superuser from netbox-demo-data dump
#     username: admin
#     password: admin
#   - name: readonly
#     description: Non-superuser with only view permissions (would need an ObjectPermission created via nbshell/SQL)
#     username: viewer
#     password: viewer
```

## What the current format can't express
- [runtime-lang] Needs Python >= 3.12 and a venv. There is no `runtime.python` key, so it is installed via apt in setup.
- [service-db] Needs PostgreSQL >= 15 with `ltree`. Ubuntu 22.04's apt PG 14 is too old.
- [service-other] Needs Redis. The config requires it (`settings.py` L75), even for UI-only PRs.
- [multi-process] runserver plus an optional `rqworker`. Postgres and Redis daemons must also be running in `start`.
- [config-file] (new) Configuration is a Python module (`netbox/netbox/configuration.py`), not env vars, so setup has to write a file with a heredoc.
- [seed-external] (new) Demo data lives in another repo (netbox-demo-data) as a SQL dump per minor version, so setup downloads it. The dump version must match the PR's base.
- [persona-create] The repo seeds no accounts. They come from the external dump (admin/admin) or `createsuperuser`. Non-admin personas need ObjectPermissions created through code or SQL.
- [reset-hard] Reset is drop-and-reload, which needs `WITH (FORCE)` while the app holds pooled connections.
- [arch-native] `psycopg[c]` compiles against libpq, so `build-essential libpq-dev` are needed.
- [mirror] pip installs from PyPI and may hit the same mirror issue Mealie had with uv.

## Difficulty
**medium.** Everything installs from apt with no Docker, and the frontend bundle is committed, so no Node build is needed. The work is Postgres + Redis + a venv + writing a Python config file. Demo data is one `curl | psql` away and includes admin/admin. The remaining risks are the PG version on older Ubuntu images and daemon management inside the devbox.

## Candidate PRs for evaluation
1. https://github.com/netbox-community/netbox/pull/22764: "Fixes #22694: Clear a Device's Rack assignment when changing its Site". Author ascjreddy, outside contributor (`author_association: CONTRIBUTOR`, not in the core set pheus/jeremystretch/jnovinger/arthanson/bctiemann). Changes `dynamicTomSelect.ts` and the committed `dist/netbox.js`. Why it is a good case: a clean browser claim ("device with Location and Rack set, changing Site now clears Rack"). It has 7 commits and 19 review comments. In-PR fixes: after review the author moved the sequence increment before an early return, fixed a loading-spinner leak on stale responses, and removed a duplicate change dispatch. The demo data has devices with site/location/rack. Persona: admin.
2. https://github.com/netbox-community/netbox/pull/22784: "Fixes #22683: Prevent server errors when bulk import validation references an omitted field". Author ascjreddy (CONTRIBUTOR). A bulk import that used to 500 now shows a readable validation error. Checkable in the browser via the Interfaces → Import form with a CSV that omits `rf_channel_width`. It has 13 commits, including in-PR fixes: moving the fix to `NetBoxModelImportForm`, keeping the field name in remapped errors, and fixing double interpolation of error params. Persona: admin.
3. https://github.com/netbox-community/netbox/pull/22735: "Closes #22685 - Add 'has any of these tags' filter mode". Author bctiemann (core team, so not an outside contributor). Adds a new "has any of these tags" option to the list filter UI, with a screenshot in the body. Claims can be checked on any tagged list (demo data has tags). There are 3 commits, one fixing a failing test mixin after the first push. Persona: admin.

## Sources
- `README.md`, `CONTRIBUTING.md` (listed), `docs/development/getting-started.md`, `docs/development/web-ui.md`, `docs/installation/1-postgresql.md`, `2-redis.md`, `3-netbox.md`, `upgrading.md`
- `.github/workflows/ci.yml`, `requirements.txt`, `pyproject.toml`, `netbox/release.yaml`
- `netbox/netbox/settings.py`, `settings_utils.py`, `configuration_example.py`, `configuration_testing.py`, `cli.py`, `scaffold.py`, `__main__.py`, `urls.py`
- `netbox/core/management/commands/upgrade.py`, `netbox/dcim/migrations/0242_ltree_paths.py`, `scripts/load_database.sh`
- https://github.com/netbox-community/netbox-demo-data (README, `sql/` listing)
- PRs 22764, 22784, 22794, 22735, 23221, 22803, 22885, 23192 (`gh api repos/.../pulls/N`, `/commits`, `/comments`)
