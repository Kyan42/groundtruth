# Groundtruth

GitHub App that turns a PR's intent into browser-verified claims. See [docs/groundtruth-prd.md](docs/groundtruth-prd.md) and [docs/architecture.md](docs/architecture.md).

## Try it

You need Node 22+, git, an [Anthropic API key](https://console.anthropic.com) and a [Runloop API key](https://runloop.ai) (Runloop runs each PR's app in a sandbox; its trial allows 3 at once).

```sh
git clone https://github.com/Kyan42/groundtruth.git && cd groundtruth
npm install
npx playwright install chromium
cp .env.example .env      # fill in ANTHROPIC_API_KEY and RUNLOOP_API_KEY
```

### On any public PR, no GitHub App

```sh
npm run local -- Kyan42/cbay#3
```

This reads the PR (title, description, commits), extracts claims, boots the PR's head commit in a sandbox, has the agent check each claim in a browser, compiles and replays the tests, and replays any regression tests the repo already has. It only reads from GitHub: no comments, checks or commits. Results open on the dashboard at `http://localhost:3000/runs`. [cbay](https://github.com/Kyan42/cbay) is a small demo shop that already has a boot config, so it works as is. To try another repo, it needs a `.groundtruth.yml` on its default branch (next section).

### As a GitHub App (the full flow)

The App adds what local mode can't: the claims comment, approval by checkbox, the check on the PR, and committing accepted tests. Set it up against a repo you own, such as a fork of cbay.

1. Create a webhook channel at [smee.io/new](https://smee.io/new). It forwards GitHub's webhooks to your machine.
2. Register a GitHub App (Settings → Developer settings → GitHub Apps → New):
   - **Webhook URL**: the smee channel. **Webhook secret**: any random string.
   - **Repository permissions**: Pull requests: Read & write · Checks: Read & write · Contents: Read & write (to commit accepted tests) · Issues: Read-only · Metadata: Read-only
   - **Events**: Pull request · Issue comment
   - Generate a private key and save the `.pem` outside the repo.
3. Install the App on your repo, and fill in the GitHub and smee lines of `.env`.
4. `npm run dev`, then open a PR. The App comments with claims; tick Approve and watch the check and the dashboard.

Each developer should use their own App and smee channel: two servers on one channel both handle every event.

## Booting an app: `.groundtruth.yml`

Each test run starts from an empty Runloop sandbox (Debian with Node) and boots the PR's commit with plain shell commands from `.groundtruth.yml`. The file is read from the default branch, never from the PR, so a PR can't change what runs. cbay's:

```yaml
version: 1
runtime:
  node: "22"
workdir: app
install: npm ci
setup:
  - npm run seed -- --reset
start: npm run dev -- --hostname 0.0.0.0 --port 3000
reset: npm run seed -- --reset
port: 3000
ready:
  path: /api/health
  timeout_seconds: 180
```

| Field | What it does |
|---|---|
| `runtime.node` | Node version the sandbox must have (checked, not installed) |
| `workdir` | Folder the commands run in |
| `env` | Environment variables for every command |
| `install`, `setup` | Run once, in order: dependencies, system packages, migrations, seed data |
| `start` | Starts the app and keeps running. Two processes: start the first with `&` |
| `port`, `ready` | The port the browser loads, and a path polled until it answers 200 |
| `reset` | Optional. Restores the starting data while the app runs, so each journey starts clean |
| `personas` | Optional. Test accounts the agent can log in as. A password is written out, or `{ secret: NAME }` to read it from Groundtruth's `.env` |
| `after_ready` | Optional. Commands run once the app answers and after every reset, such as creating persona accounts through the app's API |

The schema is in [src/boot/config.ts](src/boot/config.ts). For a harder example, [evals/boot/mealie.yml](evals/boot/mealie.yml) boots [Mealie](https://github.com/mealie-recipes/mealie) (Python backend plus Nuxt frontend, system packages, and a second account created through the admin API).

**Writing one for your app: ask your coding agent.** Run Claude Code, Cursor or similar in your app's repo, with this repo cloned next to it and set up as in Try it, and paste:

> Write a `.groundtruth.yml` for this app. The format and an example are in `../groundtruth/README.md` and `../groundtruth/src/boot/config.ts`; `../groundtruth/evals/boot/mealie.yml` is a harder example. Base it on how this repo's docs, CI and dev setup run the app. Then test it from `../groundtruth` with `npm run boot -- <owner>/<repo> --sha <latest commit on the default branch> --config <path to your file>`, read the report, fix the config and repeat until it prints BOOTED and the screenshot shows the app working.

The boot report prints each step with its timing, the app's output when something fails, and what a real browser saw (status, console errors, failed requests, a screenshot), so the agent can fix the config on its own. The usual fixes:

- The dev server must listen on `0.0.0.0`, not `localhost`, or the sandbox's tunnel can't reach it.
- Tools beyond Node and npm (pnpm, uv, Python packages, system libraries) are installed in `setup`.
- The `ready` path should only answer once everything is up, such as an API route the frontend proxies to the backend.
- Docker isn't in the sandbox. It can be installed in `setup` for databases and Redis, at about 40 seconds a boot.

For what other apps need, [evals/research/onboarding-2026-09](evals/research/onboarding-2026-09/README.md) has notes and a draft config for 15 open-source apps (Cal.com, Mastodon, Immich and others), including what the format can't express yet.

## Commands

| Command | Needs | What it does |
|---|---|---|
| `npm run local -- owner/repo#N` | Anthropic + Runloop | the whole pipeline on a public PR, read-only, with the dashboard |
| `npm run boot -- owner/repo --sha <commit> --config <file>` | Anthropic + Runloop | boot any public commit with a local config and check it in a browser |
| `npm run dev` | everything | the App server, webhooks and dashboard (restarts on code changes) |
| `npm run extract -- owner/repo#N` | App + Anthropic | extract claims for a PR without commenting |
| `npm run boot -- owner/repo#N` | App + Anthropic + Runloop | boot a PR using its repo's `.groundtruth.yml` |
| `npm run explore -- owner/repo#N` | App + Anthropic + Runloop | boot, run the agent on the PR's claims (printing every step), then compile and replay |
| `npm run compile -- runs/<run>` | nothing | compile a saved run into Playwright tests in `<run>/scripts/` |
| `npm run evals -- --runs 1` | Anthropic | claim-extraction evals on the frozen cases in `evals/` (about $0.45 a repeat) |
| `npm run typecheck` | | |

## Layout

- `src/index.ts`: HTTP server: webhooks, smee forwarding, dashboard routes
- `src/app.ts`: webhook handlers (PR opened → claims comment; comment edited → approval, or adding the tests)
- `src/extract.ts`, `src/evidence.ts`, `src/comment.ts`, `src/pr-comment.ts`: claim extraction and the PR comment
- `src/boot/`: booting a PR's app in a Runloop sandbox from `.groundtruth.yml`
- `src/explore/`: the exploring agent, its browser, and checks
- `src/personas.ts`: test accounts from `.groundtruth.yml`; real passwords never reach the model or the saved trace
- `src/compile/`: turning a run into Playwright tests, and replaying them
- `src/registry.ts`, `src/regressions.ts`: the tests committed under `.groundtruth/`, and replaying them on later PRs
- `src/testing.ts`, `src/check-report.ts`: approval → boot → agent → replay → regressions → the Groundtruth check
- `src/dashboard/`: the run list and run pages
- `src/cli/`: the commands above, plus the eval tooling
- `evals/`: extraction eval cases and runs; `evals/explore/`: exploration eval cases (drafts)
- `evals/research/`: studies on open-source repos: bugs fixed inside real PRs, and what 15 apps need to boot in a sandbox
- `docs/`: the PRD, early design notes, architecture, the exploration agent's plan, and how eval PRs were chosen
