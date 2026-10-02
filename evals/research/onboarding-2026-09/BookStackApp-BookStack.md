# BookStackApp/BookStack
BookStack, a self-hosted wiki/documentation platform (shelves > books > chapters > pages) built on Laravel. About 19.1k stars. Default branch `development` (releases are cut to `release`, per `readme.md` L127). Last commit seen: 2026-09-28 ("API: Fixed attachment list showing items on deleted pages"). https://github.com/BookStackApp/BookStack

**Important:** the GitHub repo description reads "NOW MANAGED ON CODEBERG", and every link in `readme.md` points to `codeberg.org/bookstack/bookstack`. CI lives in `.forgejo/workflows/`. The GitHub repo is still pushed to (last push 2026-09-28), but the newest merged GitHub PR I found is #6109 (2026-04-22). New PRs, and the Groundtruth GitHub App, would have to target Codeberg. That is a platform problem, not a boot problem.

## Stack and services
- PHP `^8.2.0` (`composer.json` `require.php`). CI tests 8.2, 8.3, 8.4 and 8.5 (`.forgejo/workflows/test-php.yml` matrix). The dev Docker image is `php:8.3-apache` (`dev/docker/Dockerfile` L1).
- Laravel `^v12.26.4` (`composer.json`).
- Required PHP extensions: curl, dom, fileinfo, gd, mbstring, xml, zip (`composer.json`). CI also adds json, mysql, ldap and gmp (`test-php.yml` `phpextensions`). The dev image builds pdo_mysql, gd, ldap and zip (`dev/docker/Dockerfile`).
- Node v22+ for building assets (`dev/docs/development.md` L5). Assets are SASS plus esbuild (`package.json` scripts `build`, `dev`, `production`). The build output `public/dist` is gitignored (`.gitignore`), so assets must be built on every boot.
- MySQL is the only supported database: `app/Config/database.php` L57-59 lists only the `mysql` and `mysql_testing` connections. There is no SQLite.

| Service | Required? | How the repo's dev setup provides it | Source |
|---|---|---|---|
| MySQL 8.x (MariaDB also works per upstream docs; guess, not verified in repo) | required | compose `db: mysql:8.4`; CI runs `sudo systemctl start mysql` on the setup-php runner image | `docker-compose.yml`; `test-php.yml` "Start MySQL" |
| PHP web server (Apache + mod_rewrite, docroot `public/`) | required | compose `app` (built from `dev/docker/Dockerfile`, runs `apache2-foreground`) | `dev/docker/Dockerfile`, `dev/docker/entrypoint.app.sh` |
| Node asset watcher (`npm run watch`) | dev only (a one-off `npm run build` is enough) | compose `node: node:22-alpine` | `dev/docker/entrypoint.node.sh` |
| MailHog (SMTP catcher, UI on :8025) | optional | compose `mailhog` | `docker-compose.yml`; `development.md` "Development using Docker" |
| Redis | optional (cache/session driver) | not in dev setup; `predis/predis` is in `composer.json` | `app/Config/database.php` L14 (redis defaults) |
| Queue worker | optional; `QUEUE_CONNECTION=sync` by default | none | `.env.example.complete` L120 |
| wkhtmltopdf | optional (PDF export; DomPDF is the default) | none | `.env.example.complete` L347-352 |

## Running without Docker
Yes. CI boots it without Docker: MySQL from the runner image, PHP via `shivammathur/setup-php`, `composer install`, then `php artisan migrate` (`test-php.yml`).

On Ubuntu 24.04 (guess: I am assuming the image is noble, which ships PHP 8.3):
- `sudo apt-get install -y php8.3-cli php8.3-mysql php8.3-gd php8.3-mbstring php8.3-xml php8.3-curl php8.3-zip php8.3-ldap php8.3-gmp unzip mysql-server`
- Composer: install via apt `composer` or the official installer (`dev/docker/Dockerfile` uses `curl -sS https://getcomposer.org/installer | php`).
- MySQL: on 24.04 root uses auth_socket, so `sudo mysql -e "CREATE DATABASE ...; CREATE USER ...; GRANT ..."` works (the SQL is in `test-php.yml` "Create database & user"). Starting it may need `sudo service mysql start` if the devbox has no systemd (guess).
- Web server: the repo only ships an Apache setup. `php artisan serve` (Laravel's built-in server) should work, since docroot is `public/` (guess: BookStack docs don't mention it). It is single-threaded unless `PHP_CLI_SERVER_WORKERS` is set.
- No service realistically needs Docker.

## Env vars and third-party services
From `.env.example`:
- `APP_KEY`: must be generated with `php artisan key:generate` (comment in `.env.example`).
- `APP_URL`: `.env.example` says it "must be the root URL" you host on. `app/App/Providers/AppServiceProvider.php` L61-65 calls `URL::forceRootUrl($appUrl)` and `forceScheme` whenever it is non-empty, so every generated link and redirect uses `APP_URL`. If Groundtruth's browser reaches the app through a public sandbox hostname, `APP_URL` must equal that hostname. Leaving it empty skips the forcing (`app/Config/app.php` L62 defaults to `''`), so Laravel would use the request host (guess: that the app works fully with an empty `APP_URL`).
- `DB_HOST`, `DB_DATABASE`, `DB_USERNAME`, `DB_PASSWORD` (`DB_PORT` is optional; see `database.php` L43-52).
- `MAIL_DRIVER`: smtp/sendmail per `.env.example`, but `app/Config/mail.php` L51-57 also defines `log` and `array` mailers. `MAIL_DRIVER=log` avoids needing a mail catcher.
- `APP_ENV` defaults to `production` (`.env.example.complete` L12). The dev docs say set `APP_ENV=local` (`development.md` step 1).

Third parties are all optional:
- Social login (Azure, Discord, GitHub, Google, Okta, ...), SAML2, OIDC, LDAP; `AUTH_METHOD=standard` is the default (`.env.example.complete` L153-175).
- S3 storage (`STORAGE_TYPE=local` is the default).
- Gravatar avatar fetches on user creation (`UserRepo::createWithoutActivity` → `downloadAndAssignUserAvatar`, `app/Users/UserRepo.php` L98; `AVATAR_URL` at `.env.example.complete` L293).
- diagrams.net embed (`DRAWIO=true`, L299).
- Core flows need no keys.

## Login in dev
- **Seeded admin:** `admin@admin.com` / `password`. It is inserted by the very first migration, `database/migrations/2014_10_12_000000_create_users_table.php` L25-32, so it exists after any `php artisan migrate`. The dev docs also list it (`development.md` step 5).
- **Other seeded users:** `DummyContentSeeder` creates one editor and one viewer (`database/seeders/DummyContentSeeder.php` L28-38), but through `UserFactory`. That factory gives them faker emails and `Str::random(10)` passwords (`database/factories/Users/Models/UserFactory.php`), so nobody can log in as them. The seeder also creates an API token `apitoken`/`password` for the editor (L69-77).
- **CLI:**
  - `php artisan bookstack:create-admin --email=... --name=... --password=...` (`app/Console/Commands/CreateAdminCommand.php` L19-25). It creates admins only.
  - For editor/viewer personas, no CLI exists. A workaround is `php artisan tinker --execute=...` calling `app(\BookStack\Users\UserRepo::class)->createWithoutActivity([...], true)` and then `$user->attachRole(Role::getRole('viewer'))` (guess, based on `UserRepo.php` L78-100 and `CreateAdminCommand.php`).
- **Roles:**
  - `admin`, `editor` and `viewer` are created in `database/migrations/2015_08_29_105422_add_roles_and_permissions.php` L71-87.
  - A `public` system role/guest user comes from `2016_09_29_101449_remove_hidden_roles.php` L26-39.
  - Custom roles and entity-level permissions are configurable in the UI.
- **Signup:** disabled by default (`app/Config/setting-defaults.php`, `'registration-enabled' => false`). When an admin enables it, email confirmation is a separate admin setting (`app/Access/RegistrationService.php` L108-118), so it can be left off.
- **SSO:** not required. Standard email/password is the default.

## Seed and fixture data
- `php artisan db:seed --class=DummyContentSeeder` (`development.md` "Running tests"; `test-php.yml`) creates:
  - 5 books × (3 chapters × 3 pages + 3 pages)
  - a "Large book" with 200 pages and 50 chapters
  - 10 shelves
  - the editor/viewer users
  - then rebuilds permissions and the search index (`DummyContentSeeder.php`)
- The content is faker lorem text, which is plausible for browsing, sorting and search. `LargeContentSeeder` also exists. `DatabaseSeeder` is empty.
- Reset: `php artisan migrate:fresh --force && php artisan db:seed --class=DummyContentSeeder --force`. This drops every table, then re-runs migrations, which re-creates `admin@admin.com`. It should run while the server is up, since there are no long-lived connections. It takes a few tens of seconds (guess: ~150 migrations plus about 300 pages of seed).
- Uploaded files in `public/uploads` / `storage/uploads` are not cleared by this.

## Draft .groundtruth.yml
```yaml
version: 1
runtime: { node: "22" }                       # development.md: Node v22+
env:
  APP_ENV: local
  APP_DEBUG: "true"
  APP_URL: ""                                 # UNCERTAIN: must equal the browser-facing URL if set (forceRootUrl); empty = use request host
  DB_CONNECTION: mysql
  DB_HOST: 127.0.0.1
  DB_PORT: "3306"
  DB_DATABASE: bookstack_dev
  DB_USERNAME: bookstack
  DB_PASSWORD: bookstack
  MAIL_DRIVER: log                            # mail.php defines a 'log' mailer
  AVATAR_URL: "false"                         # UNCERTAIN: intended to stop Gravatar fetches on user create
setup:
  # UNCERTAIN: assumes Ubuntu 24.04 (php8.3 packages); 22.04 would need the ondrej/php PPA
  - sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq php8.3-cli php8.3-mysql php8.3-gd php8.3-mbstring php8.3-xml php8.3-curl php8.3-zip php8.3-ldap php8.3-gmp unzip mysql-server
  - sudo service mysql start || sudo mysqld_safe --daemonize   # UNCERTAIN: no systemd in devbox?
  - sudo mysql -e "CREATE DATABASE IF NOT EXISTS bookstack_dev; CREATE USER IF NOT EXISTS 'bookstack'@'localhost' IDENTIFIED BY 'bookstack'; GRANT ALL ON bookstack_dev.* TO 'bookstack'@'localhost'; FLUSH PRIVILEGES;"
  - mkdir -p ~/.local/bin && curl -sS https://getcomposer.org/installer | php -- --install-dir=$HOME/.local/bin --filename=composer
  - ~/.local/bin/composer install --no-interaction --prefer-dist
  - cp .env.example .env && php artisan key:generate --force   # real env vars above override .env values (phpdotenv immutable) -- UNCERTAIN
  - npm ci && npm run build                   # public/dist is gitignored
  - php artisan migrate --force               # creates admin@admin.com / password
  - php artisan db:seed --class=DummyContentSeeder --force
  # personas (UNCERTAIN: tinker one-liner shape)
  - php artisan tinker --execute="\$u=app(BookStack\Users\UserRepo::class)->createWithoutActivity(['name'=>'Eddie Editor','email'=>'editor@example.com','password'=>'password'], true); \$u->attachRole(BookStack\Users\Models\Role::getRole('editor'));"
  - php artisan tinker --execute="\$u=app(BookStack\Users\UserRepo::class)->createWithoutActivity(['name'=>'Vera Viewer','email'=>'viewer@example.com','password'=>'password'], true); \$u->attachRole(BookStack\Users\Models\Role::getRole('viewer'));"
start: PHP_CLI_SERVER_WORKERS=4 php artisan serve --host 0.0.0.0 --port 8080   # UNCERTAIN: repo only documents Apache
reset: php artisan migrate:fresh --force && php artisan db:seed --class=DummyContentSeeder --force   # NOTE: wipes tinker-created personas; re-run those lines too
port: 8080
ready: { path: /login, timeout_seconds: 600 }
# personas:
#   admin:  { description: "Built-in admin (first migration)", username: admin@admin.com, password: password }
#   editor: { description: "Editor role: create/edit content", username: editor@example.com, password: password }
#   viewer: { description: "Viewer role: read-only", username: viewer@example.com, password: password }
```

## What the current format can't express
- [runtime-lang] Needs PHP 8.2+ with about 9 extensions plus Composer. There is no `runtime.php`, so everything goes through apt in `setup`.
- [service-db] Needs a MySQL 8 server with a pre-created database and user. MySQL is the only supported driver (`app/Config/database.php`).
- [host-config] `APP_URL` is forced as the root URL for every link and redirect (`AppServiceProvider.php` L61-65). The config has no variable for the sandbox's public URL to inject into env.
- [persona-create] Only admins can be created from the CLI (`bookstack:create-admin`). Editor/viewer personas need a tinker/SQL workaround, and the seeded editor/viewer have random, unknown credentials.
- [secret-gen] `APP_KEY` must be generated (`php artisan key:generate`). This can be done in `setup`, so it is low friction.
- [reset-hard] Reset (`migrate:fresh`) wipes users, so persona creation must be repeated inside `reset`. With `setup` and `reset` as separate strings, that means duplicating commands.
- [platform-codeberg] (new tag) Development moved to Codeberg (`readme.md`; repo description). New PRs will not arrive on GitHub, so the GitHub App cannot watch them.

## Difficulty
**easy-medium.** Everything installs from apt, CI shows a Docker-free recipe, the admin login is hard-coded in a migration, and there is a one-line dummy-content seeder. The rough edges are: `APP_URL` forcing absolute URLs, non-admin personas having no CLI, and PHP having no runtime key. The practical blocker for ongoing use is that new PRs now live on Codeberg, not GitHub.

## Candidate PRs for evaluation
All are merged on GitHub, before the Codeberg move.

1. https://github.com/BookStackApp/BookStack/pull/5663 by shresthkapoor7 (CONTRIBUTOR, outside), merged 2025-07-19.
   - **Change:** the page-editor changelog input becomes a 2-row textarea with `maxlength=250` and a live "0 / 250" character counter.
   - **Checkable claims (PR body "Tested:"):** the counter updates live, the summary preview still truncates after 16 chars, and long changelogs save and show in revision history.
   - **Setup:** admin or editor persona, plus any seeded page.
   - **Caveat:** single commit, so no in-PR fix.
2. https://github.com/BookStackApp/BookStack/pull/5550 by bernardo-campos (CONTRIBUTOR, outside), merged 2025-04-02.
   - **Change:** sort rules "by name" now order accented names correctly (é no longer sorts after z).
   - **In-PR revision:** the first approach (`Collator` + user locale) was rejected in review. The contributor switched to `ASCII::to_transliterate`, and the maintainer followed up in 1ba0d26 (PR comments).
   - **Browser check:** create books named with accents, apply a name sort rule (Settings > Sorting), and check the order.
   - **Setup:** admin persona.
3. https://github.com/BookStackApp/BookStack/pull/6108 by ssddanbrown (maintainer), merged 2026-04-19.
   - **Change:** adds a `revision-view-all` role permission, so revisions are hidden from roles without it.
   - **In-PR fix:** 5 commits, including "Tweaks/fixed during review" and "Prevent export revision metadata view without permission".
   - **Checkable claims:** a viewer without the permission can't open the revisions list or see revision metadata in exports, and an admin can toggle it in role settings.
   - **Setup:** needs **two personas** (admin plus a restricted viewer/editor), which is a good test of personas.
   - Alternative: #5944 "Comment mentions" (12 commits, fixes during review; needs 2+ users with comment permissions).

## Sources
- `readme.md`, `dev/docs/development.md`, `.env.example`, `.env.example.complete`, `docker-compose.yml`
- `dev/docker/Dockerfile`, `dev/docker/entrypoint.app.sh`, `dev/docker/entrypoint.node.sh`, `dev/docker/init.db/01.sql`
- `.forgejo/workflows/test-php.yml`, `package.json`, `composer.json`, `.gitignore`
- `app/Config/database.php`, `app/Config/app.php`, `app/Config/mail.php`, `app/Config/setting-defaults.php`, `app/App/Providers/AppServiceProvider.php`
- `app/Console/Commands/CreateAdminCommand.php`, `app/Access/RegistrationService.php`, `app/Users/UserRepo.php`
- `database/migrations/2014_10_12_000000_create_users_table.php`, `2015_08_29_105422_add_roles_and_permissions.php`, `2016_09_29_101449_remove_hidden_roles.php`
- `database/seeders/DummyContentSeeder.php`, `database/seeders/DatabaseSeeder.php`, `database/factories/Users/Models/UserFactory.php`
- `gh pr list` / `gh pr view` for #5663, #5606, #5550, #5944, #6100, #6108, #5864, #5923
