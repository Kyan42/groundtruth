# halo-dev/halo
Halo, an open-source website/blog builder (CMS with themes and plugins) with a Spring Boot WebFlux backend and a Vue 3 console. About 39.9k stars. Default branch `main`, version `2.27.0-SNAPSHOT` (`gradle.properties`). Last commit seen: 2026-09-28 ("Upgrade Vite+ to 1.0.0-rc.1 and Vitest 5 (#10349)"). https://github.com/halo-dev/halo

**Note:** `CONTRIBUTING.md` now says "Pull requests to this repository are restricted to project maintainers. We are not accepting pull requests from external contributors at this time." Future PRs will come from maintainers only.

## Stack and services
- **Java 21:**
  - `build.gradle` L31-32 and `application/build.gradle` sets `toolchain languageVersion = JavaLanguageVersion.of(21)`.
  - CI `setup-env` uses Temurin 21 (`.github/actions/setup-env/action.yaml`).
  - `AGENTS.md` says "Java 21 / Spring Boot WebFlux + R2DBC".
- **Gradle 9.8.0** through the wrapper (`gradle/wrapper/gradle-wrapper.properties`).
- **Frontend (`ui/`):**
  - Vue 3 + TS + Tailwind, as a pnpm workspace (`ui/AGENTS.md`).
  - Node `>=24.11.0` and `packageManager: pnpm@12.4.2` (`ui/package.json` L168-171).
  - The build runs through **Vite+ (`vp`)**, with `vite` aliased to `@voidzero-dev/vite-plus-core@1.0.0-rc.1` (`ui/pnpm-workspace.yaml` catalog).
  - `pnpm dev` = `vp run app:dev` → `vp dev . --host` after `build:packages` (`ui/vite.config.ts` L20-24).
  - The dev server runs on port 3000 (`DEV_SERVER_PORT = 3000`, `ui/vite.config.ts` L14).
- **Two UI apps:**
  - the console (`ui/console-src`, `console.html`), served at `/console`
  - the user center (`ui/uc-src`, `uc.html`), served at `/uc`
  - The public site is rendered server-side with Thymeleaf themes; `theme-earth.zip` is bundled at `application/src/main/resources/themes/`.

| Service | Required? | How the repo's dev setup provides it | Source |
|---|---|---|---|
| H2 (embedded, file DB under work-dir) | required by default, embedded | default `spring.r2dbc.url: r2dbc:h2:file:///${halo.work-dir}/db/halo-next` | `application/src/main/resources/application.yaml` |
| MySQL / MariaDB / PostgreSQL | optional alternatives | Spring profiles `mysql`, `mariadb`, `postgresql` | `application-mysql.yaml`, etc. |
| Vite dev server (:3000) | required in `dev` profile (backend proxies HTML to it) | manual `pnpm -C ui dev` | `application-dev.yaml` `halo.ui.proxy.endpoint: http://localhost:3000/` |
| Preset plugins (comment widget, search, sitemap, feed, shiki, ...) | optional; comments on the public site need `plugin-comment-widget` (guess) | `./gradlew downloadPluginPresets` downloads jars from GitHub releases | `application/build.gradle` L144-175 |
| Search engine (Lucene built-in; Meilisearch/Algolia via plugins) | built-in | in-process | `docs/full-text-search/README.md` (not read in full) |
| SMTP | optional | none | notifications/email docs (not read) |

## Running without Docker
Yes. There are no external services by default: H2 is embedded, and caching is Caffeine (`application.yaml` `spring.cache.type: caffeine`). The documented source run (https://docs.halo.run/developer-guide/core/run) is:
1. `cd ui && pnpm install && pnpm build:packages && pnpm dev`, which serves on :3000 but "should be accessed through Halo's proxy"
2. `./gradlew bootRun --args="--spring.profiles.active=dev"` (backend on :8090)
3. Open `http://localhost:8090/console` or `/uc`. The docs warn "Do not directly use the UI's running port 3000", because of CORS and login issues.

On Ubuntu:
- `openjdk-21-jdk-headless` is available from apt (guess: standard on 22.04/24.04).
- Gradle comes from the wrapper.
- Node 24 comes from `runtime`; pnpm from `npm i -g pnpm@12.4.2`.

**Dev-mode catch (important):** `ui/src/vite/plugin-dev.ts` rewrites every `src`/`href` in the served HTML to an absolute `http://localhost:3000/...`. `ProxyFilter` (`application/src/main/java/run/halo/app/infra/ui/ProxyFilter.java`) only proxies the HTML document itself. So in dev mode **the browser must reach `localhost:3000` directly**, in addition to :8090. If Groundtruth's browser is not on the sandbox's loopback, the console will not load.

Workaround: skip the dev server.
1. Run a production UI build with `pnpm -C ui build`, which outputs `ui/build/dist/ui` (`ui/vite.config.ts` `build.outDir`).
2. The `copyUiDist` Gradle task copies it into `application/build/resources/main`. `classes` depends on it (`application/build.gradle` L116-125).
3. Run the backend with `halo.ui.proxy.enabled=false`. That the UI is then served from the classpath is a guess.

## Env vars and third-party services
- There is no `.env`. Configuration is Spring properties (`application*.yaml`), overridable with `--args` or `SPRING_*`/relaxed env vars.
- `halo.work-dir`:
  - defaults to `${user.home}/.halo2` (`application.yaml`)
  - the `dev` profile uses `${user.home}/halo2-dev` (`application-dev.yaml`)
  - It holds the H2 DB, attachments, plugins, themes and logs.
- `application.yaml` holds a hard-coded H2 username/password (`admin`/`123456`). These are DB credentials, not a login.
- The dev profile enables HTTP basic auth (`halo.security.basic-auth.disabled: false`, `application-dev.yaml`). It is disabled by default (`SecurityProperties.BasicAuthOptions.disabled = true`). This makes API seeding with `curl -u` possible in dev.
- **External URL:** the setup form requires `externalUrl` (`SystemSetupEndpoint.SetupRequest`, `@URL`), which is stored in basic settings and used for absolute links (e.g. attachment permalinks, PR #9885). It must match the browser-facing host.
- **Login rate limit:** the `authentication` rate limiter allows **3 login attempts per minute per IP** (`application.yaml` `resilience4j.ratelimiter.configs.authentication.limitForPeriod: 3`; applied per client IP in `LoginAuthenticationConverter.java` L56-64). An agent that fumbles a login twice, or logs in as several personas, will hit this. It can be raised with a Spring property override (guess: `--resilience4j.ratelimiter.configs.authentication.limit-for-period=100`).
- The login form sends an RSA-encrypted password (`LoginAuthenticationConverter` decrypts credentials, L49-54). That is fine in a browser, but scripted form login with curl is impractical. Use basic auth for scripts.
- Third parties are all optional: OAuth/social login via plugins, SMTP, Algolia/Meilisearch via plugins. `downloadPluginPresets` fetches from github.com. Core flows need no keys.

## Login in dev
- **Seeded accounts:** none. On first boot every HTML request to `/`, `/console/**`, `/uc/**`, `/login` and `/signup` redirects to **`/system/setup`** (`application/src/main/java/run/halo/app/security/InitializeRedirectionWebFilter.java`).
  - The setup form (`templates/setup.html`) takes language, externalUrl, siteTitle, username (4-63 chars), email and password (≥5).
  - Submitting it creates the super admin (`SuperAdminInitializer`) and loads `initial-data.yaml` (`SystemSetupEndpoint.java` L99-135).
- **Scripted setup:** `POST /system/setup` is **exempt from CSRF** (`CsrfConfigurer.java`, `pathMatchers(... "/system/setup" ...)`) and binds form fields (`request.bind(SetupRequest.class)`). So after the server is up, this should work (guess: not run):
  `curl -X POST localhost:8090/system/setup --data-urlencode username=gtadmin --data-urlencode password=... --data-urlencode email=... --data-urlencode siteTitle=Halo --data-urlencode language=en --data-urlencode externalUrl=http://localhost:8090`
  It can only run once the server is up, because setup is an HTTP endpoint and there is no CLI.
- **More users:** there is no CLI. Use the console API, `POST /users` in `core/endpoint/console/UserEndpoint.java` L158-165. The body is `CreateUserRequest {name, email, displayName, password, roles}`, L497-515. The full path `/apis/api.console.halo.run/v1alpha1/users`, with basic auth in the dev profile, is a guess from the `UserV1alpha1Console` tag and Halo's API naming.
- **Roles:** `super-role` and `guest` are defined in `extensions/system-default-role.yaml`. Finer roles are built from role templates (`extensions/role-template-*.yaml`) in the console's Roles page.
- **Signup:** `allowRegistration: false`, `mustVerifyEmailOnRegistration: false`, `defaultRole: guest` (`extensions/system-configurable-configmap.yaml` L6-11). Email verification is off unless enabled.
- **SSO:** not required. OAuth2 login is available through plugins/auth providers (`extensions/authproviders.yaml`).

## Seed and fixture data
- There is no seed command. `initial-data.yaml` is applied once at setup, and substitutes the admin's username. It creates:
  - one category and one tag
  - a sample post with a snapshot
  - a single page
  - 4 menu items
  - a user-agreement snapshot
- The bundled theme is `theme-earth.zip`. The data is thin: for richer data, create posts in the console (the agent can) or through the API.
- Reset: stop the app, `rm -rf ~/halo2-dev` (the dev work-dir, which holds the H2 DB, plugins and attachments), restart, then re-run setup. There is no reset command, and the embedded H2 file is locked while running (guess).

## Draft .groundtruth.yml
```yaml
version: 1
runtime: { node: "24" }                        # ui/package.json engines node >=24.11.0
env:
  JAVA_HOME: /usr/lib/jvm/java-21-openjdk-amd64   # UNCERTAIN: path/arch
setup:
  - sudo apt-get update -qq && sudo apt-get install -y -qq openjdk-21-jdk-headless curl
  - sudo npm install -g pnpm@12.4.2             # ui/package.json packageManager
  - cd ui && pnpm install --frozen-lockfile && pnpm build   # production UI build (see dev-mode catch); runs build:packages first
  - ./gradlew :application:classes --no-daemon  # compiles + copyUiDist; first run downloads Gradle 9.8 + deps (slow)
start: |
  ./gradlew :application:bootRun --no-daemon --args="--spring.profiles.active=dev --halo.ui.proxy.enabled=false --resilience4j.ratelimiter.configs.authentication.limit-for-period=100" &
  # UNCERTAIN: proxy.enabled=false + classpath UI; rate-limit override property name
  until curl -s -o /dev/null localhost:8090/system/setup; do sleep 3; done
  curl -s -X POST localhost:8090/system/setup \
    --data-urlencode username=gtadmin --data-urlencode 'password=gt-password-1' \
    --data-urlencode email=gtadmin@example.com --data-urlencode siteTitle=Groundtruth \
    --data-urlencode language=en --data-urlencode externalUrl=http://localhost:8090   # UNCERTAIN: must be the browser-facing URL
  curl -s -u gtadmin:gt-password-1 -H 'Content-Type: application/json' \
    -X POST localhost:8090/apis/api.console.halo.run/v1alpha1/users \
    -d '{"name":"guestuser","email":"guest@example.com","password":"gt-password-1","roles":["guest"]}'   # UNCERTAIN: path + basic auth
  wait
port: 8090
ready: { path: /console, timeout_seconds: 1200 }   # UNCERTAIN: redirect filter only fires for Accept: text/html; poller may get 200 before setup ran
# reset: not expressible -- needs app stopped, work-dir deleted, restart, setup again
# personas:
#   admin: { description: "Super admin created via /system/setup", username: gtadmin, password: gt-password-1 }
#   guest: { description: "Registered user with default 'guest' role (user center only)", username: guestuser, password: gt-password-1 }
```
Dev-mode alternative, if the browser can reach the sandbox loopback: `start` runs `pnpm -C ui dev &` and `bootRun` with the plain `dev` profile. Both :8090 and :3000 must then be reachable.

## What the current format can't express
- [runtime-lang] Needs JDK 21 (toolchain in `build.gradle`) plus a Gradle wrapper download. There is no `runtime.java`.
- [multi-port] In dev mode the console HTML hard-codes `http://localhost:3000` asset URLs (`ui/src/vite/plugin-dev.ts`). The browser must reach both :8090 and :3000, on the sandbox's own `localhost`.
- [prod-build] The workaround for the above is a production UI build (`pnpm build`) served by the backend, which loses HMR and adds build time per boot.
- [multi-process] Dev mode is two processes: the Vite dev server and the Gradle `bootRun`.
- [post-start-hook] (new tag) Creating the admin and other personas needs HTTP calls **after** the server is up (`POST /system/setup`, then the console users API). No CLI exists, so this has to be crammed into `start` with a curl loop.
- [persona-create] No seeded accounts. Everything goes through the setup endpoint and the API.
- [host-config] `externalUrl` is required at setup and used for absolute links. It must equal the browser-facing URL.
- [rate-limit] (new tag) Login is limited to 3 attempts/min per IP (`application.yaml`, `LoginAuthenticationConverter`). Multi-persona runs need a property override.
- [slow-build] A cold Gradle build (Gradle 9.8 download, Spring Boot deps, compile) plus a pnpm install and full Vite+ build of all workspace packages takes many minutes (guess: 5-10). It would benefit from caching `~/.gradle` and the pnpm store.
- [reset-hard] Reset means stopping the app, deleting the work-dir (embedded H2 file) and repeating setup. It is not possible while running.
- [mirror] Maven Central, the Gradle distribution, the npm registry and GitHub release jars (plugin presets) are all fetched at build time. Mirror problems are likely on Runloop (guess, based on the Mealie experience).

## Difficulty
**medium-hard.** There are no external services (H2 is embedded), which is good. But:
- Dev mode is split across two ports, with hard-coded `localhost:3000` asset URLs, so a production UI build is the realistic path.
- The first admin can only be created through an HTTP setup endpoint after boot.
- Persona creation needs API calls.
- Login is rate-limited to 3/min.
- Cold builds are heavy (Gradle and Vite+).
- New PRs will only come from maintainers.

## Candidate PRs for evaluation
1. https://github.com/halo-dev/halo/pull/8310 by AR-26710 (CONTRIBUTOR, outside, from before the PR restriction), merged 2026-02-06.
   - **Change:** the "protected/reserved names" setting now applies to both username and display name, at registration and in the user center profile editor, but not in console user management. It also trims leading/trailing spaces.
   - **In-PR fixes:** JohnNiang requested changes, followed by "Make revisions based on the review", "Remove leading and trailing spaces before checking...", "Auto remove the leading and trailing spaces of input fields in user-r…", and two test/checkstyle fixes.
   - **Checkable claims:** a reserved display name is rejected in UC profile edit and in signup, and allowed in console user management.
   - **Setup:** admin persona (to set reserved names and enable registration) plus a normal user persona.
2. https://github.com/halo-dev/halo/pull/10302 by ruibaby (MEMBER), merged 2026-09-10. "Support editing comments and replies in Console."
   - **PR body claims:** the editor loads the latest body, edits preserve moderation state, author and creation time, "stale writes are rejected, and failed saves retain the draft".
   - **In-PR fix:** "fix: address comment editing review findings" (after a Codex review).
   - **Setup:** admin persona plus existing comments. Creating those likely needs the comment-widget preset plugin, via `downloadPluginPresets` (guess), or API seeding.
3. https://github.com/halo-dev/halo/pull/8272 by ruibaby (MEMBER), merged 2026-01-29. "Enhance post snapshot comparison feature by adding diff mode support."
   - **Checkable claims:** a diff mode for post versions, an "only show diff" toggle, synchronized scrolling, and i18n.
   - **In-PR iteration:** 9 commits across several iterations. The body asks for "comprehensive testing".
   - **Setup:** admin persona only. The agent edits and publishes a post twice (the seeded post already has a snapshot) and compares versions.

## Sources
- `README` tree, `CONTRIBUTING.md`, `AGENTS.md`, `application/AGENTS.md`, `ui/AGENTS.md`, `CLAUDE.md`
- `build.gradle`, `application/build.gradle`, `ui/build.gradle`, `gradle.properties`, `gradle/wrapper/gradle-wrapper.properties`
- `ui/package.json`, `ui/pnpm-workspace.yaml`, `ui/vite.config.ts`, `ui/src/vite/plugin-dev.ts`, `ui/src/vite/library-external.ts`, `ui/Makefile`, `ui/.env.development` (empty)
- `application/src/main/resources/application.yaml`, `application-dev.yaml`, `initial-data.yaml`, `templates/setup.html`
- `extensions/system-default-role.yaml`, `extensions/user.yaml`, `extensions/system-configurable-configmap.yaml`
- `security/InitializeRedirectionWebFilter.java`, `security/preauth/SystemSetupEndpoint.java`, `security/CsrfConfigurer.java`, `infra/ui/ProxyFilter.java`, `infra/properties/SecurityProperties.java`, `security/authentication/login/LoginAuthenticationConverter.java`, `core/endpoint/console/UserEndpoint.java`
- `.github/workflows/halo.yaml`, `.github/actions/setup-env/action.yaml`
- https://docs.halo.run/developer-guide/core/run
- `gh pr list` (label area/ui, multi-commit PRs since 2025-04); `gh pr view` #10004, #9800, #9842, #9867, #8409, #8347, #10302, #8272, #8310, #7679; reviews on #8310 and #10302
