# saleor/saleor (+ saleor/saleor-dashboard)
Saleor is headless commerce split across two repos.
- **saleor/saleor** is the Django GraphQL API ("Saleor Core"). About 23.4k stars, default branch `main` (version `3.24.0-a.0`, `pyproject.toml` L7), last commit seen 2026-09-28. https://github.com/saleor/saleor
- **saleor/saleor-dashboard** is the React/Vite admin SPA that talks to that API. About 1.0k stars, default branch `main` (package version `3.23.35`, `package.json` L3), last commit seen 2026-09-28. https://github.com/saleor/saleor-dashboard
- Stable backend line: branch `3.23` (version 3.23.36, last commit 2026-09-24).

## Stack and services
**Core (saleor/saleor)**
- Python `>=3.12,<3.13`, so exactly 3.12, on both `main` and `3.23` (`pyproject.toml` L10). Managed with uv (`uv.lock`; `[tool.uv] exclude-newer = "3 weeks"`, L137).
- Django + Graphene GraphQL, served by `uvicorn saleor.asgi:application --reload` (`pyproject.toml` `[tool.poe.tasks] start`, L178-179), Celery 5.
- `psycopg[binary]` (no compilation), `python-magic`, which needs libmagic (`pyproject.toml` L65, L75).

**Dashboard (saleor/saleor-dashboard)**
- Node `>=24 <25` (`package.json` `engines`), pnpm 11.13.1 (`packageManager`), React + Vite; `pnpm run dev` is `vite --host` on port 9000 (`vite.config.js` L133-134).
- GraphQL types are generated and committed (`src/graphql/types.generated.ts` etc.), so no codegen is needed to boot.
- The schema generation is chosen at **build time** with `FF_USE_STAGING_SCHEMA`. `false` means the stable schema, which targets Saleor 3.23; `true` targets Saleor `main` (`e2e/README.md` "The two targets"; `e2e/config.ts` L35-36).

| Service | Required? | How dev setup provides it | Source |
|---|---|---|---|
| PostgreSQL 15 (+ `pg_trgm`/`btree_gin` via migrations) | required | devcontainer compose `db: postgres:15-alpine`; CI `services:` | `saleor/.devcontainer/docker-compose.yml`; `.github/workflows/tests.yaml` L40-42; `saleor/account/migrations/0065_...search_gin.py` (`gin_trgm_ops`) |
| Redis/Valkey (cache + Celery broker) | **optional**. With no `CELERY_BROKER_URL`, Celery runs eagerly (`settings.py` L632-643). With no `CACHE_URL`, `django_cache_url.config()` falls back to its default (locmem, guess) | compose `cache: valkey:8.1-alpine` | `.devcontainer/common.env` comments; `settings.py` L982-990 |
| Celery worker / beat | optional (eager mode by default in the devcontainer) | `uv run poe worker` / `poe scheduler` | `CONTRIBUTING.md` "Common commands" |
| Mail catcher | optional. `EMAIL_URL=smtp://localhost:1025` with Mailpit, or `console://` | compose `mailpit` | `.env.example`; dashboard `e2e/compose.yml` uses `EMAIL_URL: console://` |
| Dashboard (for browser UI) | required for any browser check | compose `dashboard: ghcr.io/saleor/saleor-dashboard:3.23` on :9000 | `saleor/.devcontainer/docker-compose.yml` |
| Saleor API (for a dashboard PR) | required | dashboard `e2e/compose.yml` runs `ghcr.io/saleor/saleor:3.23` + worker + postgres + valkey | `saleor-dashboard/e2e/compose.yml`, `e2e/README.md` |

## Running without Docker
**Core.** Yes. CI and backend e2e run with only `apt-get install -y libpq-dev` in a `python:3.12` container plus a Postgres service (`tests.yaml` L71-82; `e2e.yml`).
- On Ubuntu: `apt install postgresql libmagic1`, uv with Python 3.12, `uv sync --locked`.
- Create a `saleor` role and database. Making the role a SUPERUSER is easiest so migrations can `CREATE EXTENSION pg_trgm/btree_gin`; CI's `POSTGRES_USER` is a superuser.
- Then `manage.py migrate` and `populatedb --createsuperuser`.
- Redis and Mailpit can be skipped.

**Dashboard.** `pnpm install` + `pnpm run dev`, with `API_URL` pointing at a running Saleor. For a dashboard PR the API must come from **a checkout of the other repo**:
- Clone saleor/saleor branch `3.23` for the default stable schema, or `main` if the build sets `FF_USE_STAGING_SCHEMA=true`.
- Boot it as above, then run the dashboard dev server against it.
- The dashboard's own e2e harness does exactly this with the Docker image instead (`e2e/README.md`, "What a run does").
- Timings from that README (M-series laptop): baseline migration **1m16s**, `populatedb` **20s**.

**The port problem.** The dashboard calls the API from the browser. `API_URL` defaults to the absolute `http://localhost:8000/graphql/` (`.env.template`; `docs/configuration.md`). `API_URL` may be relative, for example `/graphql/`, and resolves against the dashboard origin (`src/config.ts` L27-36), but the Vite dev server has no proxy configured (`vite.config.js` `server` block). So either the browser reaches two ports (9000 and 8000), or a reverse proxy (for example apt nginx) fronts both on one port. The repo's `nginx/default.conf` only serves static files and does not proxy the API.

## Env vars and third-party services
- **Core** (`.env.example`; `settings.py`):
  - `DATABASE_URL`, default `postgres://saleor:saleor@localhost:5432/saleor` (L139)
  - `SECRET_KEY`: random if unset and DEBUG (L258-273)
  - `DEBUG`, default **True** (L86)
  - `RSA_PRIVATE_KEY` for JWT: auto debug key when DEBUG (`saleor/core/jwt_manager.py` L78-86)
  - `ALLOWED_HOSTS`, default `localhost,127.0.0.1` (L516); `ALLOWED_GRAPHQL_ORIGINS` default `*` (L517)
  - `PUBLIC_URL`, used for absolute URLs such as media (L202-209)
  - `DASHBOARD_URL` (`.env.example`); `HTTP_IP_FILTER_ALLOW_LOOPBACK_IPS=True` so local webhooks and apps work (L972)
  - `DEFAULT_CHANNEL_SLUG`
- **Dashboard** (`.env.template`; `docs/configuration.md`): `API_URL` (required, trailing slash), `APP_MOUNT_URI` (default `/`), `FF_USE_STAGING_SCHEMA`, `EXTENSIONS_API_URL` (optional; Cloud only), `LOCALE_CODE`.
- **Third parties:** payments go through Saleor Apps and webhooks (Stripe/Adyen apps) and are not needed for admin flows. Also SendGrid (`SENDGRID_*`, L169), S3/GCS/Azure storage, Sentry, OTEL (dashboard e2e sets `OTEL_*_EXPORTER=none`). Admin dashboard flows work without any real key; `populatedb` configures a dummy/offline plugin ("set only our custom plugin to not call external API", `populatedb.py` `handle`).

## Login in dev
- **Seeded accounts:** `populatedb --createsuperuser` creates superuser **`admin@example.com` / `admin`** (`saleor/core/management/commands/populatedb.py` L48, L86; `poe populatedb` help in `pyproject.toml` L208-213).
- **Staff personas:** `create_staffs` creates one staff user per permission, e.g. `order.manager@example.com`, `product.manager@example.com`, `discount.manager@example.com`, all with password **`password`** (`saleor/core/utils/random_data.py` L1562-1582; `--staff_password` default L47). Groups "Full Access" and "Customer Support" are also created (L1541-1559). The dashboard's e2e uses these as actors (`e2e/config.ts` L40-46; `e2e/README.md` "Actors").
- **Customers:** 20 Faker users (`fake.seed(0)`, so deterministic) with password `password` (`random_data.py` L124, L910-935). They cannot log into the dashboard, which is staff-only.
- **Command line:** `manage.py createsuperuser` (overridden in `saleor/account/management/commands/createsuperuser.py`), `changepassword`, or `populatedb --createsuperuser --superuser_password X`.
- **Roles:** superuser; staff with permission groups (MANAGE_ORDERS, MANAGE_PRODUCTS, ...); customers.
- **Email:** no email is needed for seeded logins. Inviting new staff from the dashboard sends a set-password email (Mailpit or console backend).
- **Throttling:** every `tokenCreate` login is delayed per IP by design, with no setting to disable it (`saleor/account/throttling.py` per dashboard `e2e/README.md` "What cannot be tagged: signing in"). Sequential logins are fine; parallel logins fail with `LOGIN_ATTEMPT_DELAYED`.
- **Not SSO-only.** OIDC is an optional plugin.

## Seed and fixture data
- `uv run poe populatedb` runs `manage.py populatedb --createsuperuser`. The data is rich: 32 products, two channels, categories, collections, pages, menus, vouchers, promotions, gift cards, 20 orders, customers, shipping zones, warehouses, tax classes, and staff per permission (`populatedb.py` `handle`; dashboard `e2e/README.md` "Seeds are built, not written").
- `--withoutimages` skips product images ("minutes off the build").
- Re-running is not a clean reset. It adds data (get_or_create for admin).
- `manage.py cleardb [--delete-staff]` deletes orders, checkouts, products and so on while keeping shop config. It refuses unless DEBUG (`saleor/core/management/commands/cleardb.py` L27-44).
- A full reset is drop DB → migrate (~1m16s) → populatedb. The dashboard e2e instead restores a `pg_dump --data-only` (truncate + COPY, 1.5-2.5s per restore; `e2e/README.md`). That pattern could be copied with psql while the app runs (guess).

## Draft .groundtruth.yml
Written for a **dashboard PR** (the useful case). The API comes from a second checkout of saleor/saleor at branch 3.23, fronted with the dashboard by nginx on one port.
```yaml
version: 1
runtime: { node: "24" }
env:
  UV_DEFAULT_INDEX: https://pypi.org/simple
  DATABASE_URL: postgres://saleor:saleor@localhost:5432/saleor
  SECRET_KEY: groundtruth-dev-only
  ALLOWED_HOSTS: "*"                      # UNCERTAIN: browser hostname unknown; Django accepts "*"
  API_URL: /graphql/                      # relative: resolved against the page origin (src/config.ts L27-36)
  FF_USE_STAGING_SCHEMA: "false"          # stable schema -> Saleor 3.23
  EMAIL_URL: console://
setup:
  - sudo apt-get update -qq && sudo apt-get install -y -qq postgresql libmagic1 nginx git curl
  - sudo service postgresql start          # UNCERTAIN: service manager in devbox
  - sudo -u postgres psql -c "CREATE ROLE saleor LOGIN SUPERUSER PASSWORD 'saleor'" && sudo -u postgres createdb -O saleor saleor
  - curl -LsSf https://astral.sh/uv/install.sh | sh
  # Second repo: the API. UNCERTAIN: must match the dashboard's target (3.23 now; 3.24 after next release)
  - git clone --depth 1 -b 3.23 https://github.com/saleor/saleor.git ~/saleor-core
  - cd ~/saleor-core && ~/.local/bin/uv python install 3.12 && ~/.local/bin/uv sync --frozen
  - cd ~/saleor-core && ~/.local/bin/uv run --no-sync python manage.py migrate          # ~1-2 min
  - cd ~/saleor-core && ~/.local/bin/uv run --no-sync python manage.py populatedb --createsuperuser --withoutimages
  - sudo npm install -g pnpm@11
  - pnpm install --frozen-lockfile
  - |
    sudo tee /etc/nginx/sites-enabled/default >/dev/null <<'EOF'
    server {
      listen 8080;
      location /graphql/  { proxy_pass http://127.0.0.1:8000; proxy_set_header Host $host; }
      location /media/    { proxy_pass http://127.0.0.1:8000; proxy_set_header Host $host; }
      location /thumbnail/ { proxy_pass http://127.0.0.1:8000; proxy_set_header Host $host; }
      location / { proxy_pass http://127.0.0.1:9000; proxy_http_version 1.1;
                   proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection "upgrade"; }
    }
    EOF
start: |
  sudo service postgresql start || true
  (cd ~/saleor-core && ~/.local/bin/uv run --no-sync uvicorn saleor.asgi:application --host 127.0.0.1 --port 8000) &
  until curl -sf http://127.0.0.1:8000/health/ >/dev/null; do sleep 2; done   # /health/ used by dashboard e2e compose healthcheck
  sudo nginx || sudo nginx -s reload
  pnpm run dev --port 9000             # UNCERTAIN: vite dev behind proxy (HMR websocket, host check)
port: 8080
ready: { path: /, timeout_seconds: 900 }   # API is up before vite starts, so / => both up
# reset: |  # UNCERTAIN: cleardb keeps shop config; re-populate afterwards adds fresh products
#   cd ~/saleor-core && ~/.local/bin/uv run --no-sync python manage.py cleardb && ~/.local/bin/uv run --no-sync python manage.py populatedb --withoutimages
# personas:
#   - name: admin
#     description: Superuser from populatedb --createsuperuser
#     username: admin@example.com
#     password: admin
#   - name: order-manager
#     description: Staff with only MANAGE_ORDERS (populatedb create_staffs)
#     username: order.manager@example.com
#     password: password
#   - name: product-manager
#     description: Staff with only MANAGE_PRODUCTS
#     username: product.manager@example.com
#     password: password
```
For a **core PR**, drop the clone and point the setup at the PR checkout itself. The only browser surface in core is the GraphQL playground at `/graphql/` (`PLAYGROUND_ENABLED` default True, `settings.py` L514), so a meaningful UI check still needs a dashboard build (Docker image or checkout) of a compatible version.

## What the current format can't express
- [second-repo] A dashboard PR needs saleor/saleor checked out at a matching version (`3.23` for the stable schema, `main` for `FF_USE_STAGING_SCHEMA=true`). A core PR needs a dashboard to have any real UI.
- [version-pairing] (new) Which API branch to pair with depends on a build-time flag and the release line, and it moves when 3.24 ships (`e2e/config.ts` L21-36).
- [runtime-lang] Core needs exactly Python 3.12 (`<3.13`). No runtime key; installed via uv.
- [service-db] PostgreSQL 15 with `pg_trgm`/`btree_gin` (superuser or trusted-extension grants).
- [multi-process] uvicorn API + Vite dev server + nginx (+ optional Celery worker).
- [multi-port] The API (:8000) and dashboard (:9000) are separate origins. A single port needs a hand-written reverse proxy.
- [host-config] `ALLOWED_HOSTS`, `PUBLIC_URL` (absolute media URLs) and dashboard `API_URL` all depend on the public hostname.
- [persona-create] Accounts come only from `populatedb` or `createsuperuser`. Staff personas depend on `populatedb`'s naming scheme.
- [reset-hard] Full reset = drop + migrate (~1m16s) + populatedb. `cleardb` is partial.
- [slow-build] Migrate from empty ~1m16s + populatedb 20s (`e2e/README.md`), plus `pnpm install` of a large dashboard. A dashboard `vite build` needs `--max-old-space-size=8192` (`package.json` `build`), though dev mode avoids it.
- [login-throttle] (new) Every login is delayed per IP, with no setting to turn it off. Parallel agents logging in can hit `LOGIN_ATTEMPT_DELAYED`.
- [mirror] uv/PyPI mirror issue likely, as with Mealie.

## Difficulty
**hard.** Each piece is easy on its own: core needs only Postgres (Redis/Celery are optional, eager mode is the default), and `populatedb` gives rich data with well-known admin and staff logins. Hard comes from composing them. A dashboard PR needs a second repo at the right release branch, a build-time schema flag, two processes on two origins that need a hand-rolled reverse proxy for single-port access, and several minutes of migrate/populate on every fresh sandbox. Core PRs are mostly GraphQL-only and make poor browser cases.

## Candidate PRs for evaluation
**Use dashboard PRs.** Core PRs from the last 18 months are almost all by org members and change the GraphQL API; a search for merged non-member core PRs since 2025-06 found only #19727. Dashboard PRs are browser-visible and several come from outside contributors (146 of the last 500 merged were `CONTRIBUTOR`).
1. https://github.com/saleor/saleor-dashboard/pull/6873: "Stop lists crashing on a selection that outlived its rows". Author ebrahim2355 (CONTRIBUTOR, outside). The body gives an exact browser repro: products list, 100 rows/page, select all, switch to 20 rows/page, and the old code shows the error page. `populatedb`'s 32 products exceed 20. The maintainer (mirekm) added commits inside the PR: "Prune stale Glide selection once the list has finished loading", "Keep list selection when page size or columns change", "Show the selected count on list bulk actions". These are extra claims to check. Persona: admin or product.manager.
2. https://github.com/saleor/saleor-dashboard/pull/6860: "Keep decimals in discount rule reward values". Author ebrahim2355 (CONTRIBUTOR). Fixes #4962: entering `12.55` as a discount rule reward value saved `12`. Clear browser claims: the input keeps decimals, the spinner step allows decimals, and values below 1 are accepted. `populatedb` creates promotions (`create_catalogue_promotions(2)`). No in-PR fix (a merge commit only). Persona: admin or discount.manager.
3. https://github.com/saleor/saleor-dashboard/pull/6814: "Fix fulfillment warehouse line scope". Author offx366 (CONTRIBUTOR). On an order's fulfill page, opening the warehouse picker for one line used to hide the other lines; now all lines stay. It needs an unfulfilled multi-line order, and `populatedb` creates 20 orders (whether some are unfulfilled and multi-line is a guess). No in-PR fix. Persona: admin or order.manager. Alternative with an in-PR fix: https://github.com/saleor/saleor-dashboard/pull/6868 (datagrid row links; the maintainer fixed anchor positioning after load/scroll inside the PR), but its claims rely on right-click/middle-click behavior, which is harder for an agent.

## Sources
- saleor/saleor: `README.md` (listed), `CONTRIBUTING.md`, `.env.example`, `.devcontainer/docker-compose.yml`, `backend.env`, `common.env`, `Dockerfile`, `pyproject.toml` (main and `3.23`), `saleor/settings.py`, `saleor/core/jwt_manager.py`, `saleor/core/management/commands/populatedb.py`, `cleardb.py`, `saleor/core/utils/random_data.py`, `saleor/account/migrations/0065_address_warehouse_address_search_gin.py`, `.github/workflows/tests.yaml`, `e2e.yml`; branch and tag listing
- saleor/saleor-dashboard: `README.md`, `.env.template`, `docs/configuration.md`, `package.json`, `vite.config.js`, `src/config.ts`, `nginx/default.conf`, `.devcontainer/docker-compose.yaml`, `dashboard.env`, `e2e/README.md`, `e2e/compose.yml`, `e2e/config.ts`, `.github/workflows/e2e-local.yml`
- PRs: dashboard 6873, 6860, 6814, 6868, 6757, 6690, 6891, 6858, 6565; core search `is:pr is:merged` non-member
