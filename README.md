# Groundtruth

GitHub App that turns a PR's intent into browser-verified claims. See [docs/groundtruth-prd.md](docs/groundtruth-prd.md) and [docs/architecture.md](docs/architecture.md).

**What works today:**
1. A PR is opened or reopened: the App reads its intent (title, description, linked issues, commits) and posts a comment with testable claims.
2. The developer reviews them and ticks Approve in the comment.
3. The App boots the PR's app in a Runloop sandbox (from the repo's `.groundtruth.yml`), an agent checks each claim in a real browser, and the results go on a "Groundtruth" check.
4. Each run (trace and a video per journey) is shown on a local dashboard at `http://localhost:3000/runs`.

## What you need

| | For | Where |
|---|---|---|
| Node 22+ and git | everything | |
| An Anthropic API key | claim extraction, the agent, evals | console.anthropic.com |
| A Runloop API key | booting PR apps in sandboxes | runloop.ai (the trial allows 3 sandboxes at once) |
| Your own GitHub App + a test repo | the webhook flow and `npm run explore` | see below |
| A smee.io channel | delivering webhooks to your machine | smee.io/new |

Use your own App, smee channel and keys rather than sharing someone else's. GitHub sends each webhook to one URL, so two servers on the same channel would both handle every event (duplicate comments, double the cost). And the App's private key gives full access to every repo it's installed on.

## Setup

1. `npm install`
2. `npx playwright install chromium` (the browser used for boot checks and the agent)
3. Create a smee channel at https://smee.io/new.
4. Register a GitHub App (GitHub → Settings → Developer settings → GitHub Apps → New):
   - **Webhook URL**: your smee channel URL. **Webhook secret**: any random string.
   - **Repository permissions**: Pull requests: Read & write · Issues: Read-only · Checks: Read & write · Contents: Read-only · Metadata: Read-only
   - **Subscribe to events**: Pull request · Issue comment
   - Generate a private key (downloads a `.pem`; keep it out of the repo, e.g. next to it).
   - Install the App on your test repo.
5. A test repo: fork [Kyan42/cbay](https://github.com/Kyan42/cbay) (a small Next.js shop with a `.groundtruth.yml` on main), or add a `.groundtruth.yml` to your own app (format: `src/boot/config.ts`).
6. Copy `.env.example` to `.env` and fill it in.
7. `npm run dev`. The server listens on `PORT`, forwards your smee channel to `/api/github/webhooks`, and serves the dashboard at `/runs`.
8. Open a PR on the test repo. The App comments with claims; tick Approve and watch the check and the dashboard.

## Commands

| Command | Needs | What it does |
|---|---|---|
| `npm run dev` | everything | the App server, webhooks and dashboard (restarts on code changes) |
| `npm run evals -- --runs 1` | Anthropic key only | claim-extraction evals on the frozen cases in `evals/` (about $0.45 per repeat; `--runs 3` is the usual) |
| `npm run extract -- owner/repo#N` | App + Anthropic | extract claims for a PR without commenting |
| `npm run boot -- owner/repo#N` | App + Runloop | boot a PR's app in a sandbox and check it loads |
| `npm run explore -- owner/repo#N` | App + Runloop + Anthropic | boot, then run the agent on the PR's claims, printing every step; the run shows on the dashboard |
| `npm run typecheck` | | |

## Layout

- `src/index.ts`: HTTP server: webhooks, smee forwarding, dashboard routes
- `src/app.ts`: webhook handlers (PR opened → claims comment; comment edited → approval)
- `src/extract.ts`, `src/evidence.ts`, `src/comment.ts`: claim extraction and the PR comment
- `src/boot/`: booting a PR's app in a Runloop sandbox from `.groundtruth.yml`
- `src/explore/`: the exploring agent, its browser, and checks
- `src/testing.ts`, `src/check-report.ts`: approval → boot → agent → the Groundtruth check
- `src/dashboard/`: the run list and run pages
- `evals/`: extraction eval cases and runs; `evals/explore/`: exploration eval cases (drafts)
- `docs/`: PRD, design, architecture, plans, future work
