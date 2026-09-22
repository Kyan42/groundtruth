# Groundtruth

GitHub App that turns a PR's intent into browser-verified claims. See [docs/groundtruth-prd.md](docs/groundtruth-prd.md).

**Current slice:** receive `pull_request.opened` / `reopened` webhooks and reply with a PR comment.

## Local setup

1. `npm install`
2. Copy `.env.example` to `.env` and fill it in. Put the App's private key `.pem` in this folder (it's gitignored).
3. In the GitHub App settings:
   - **Webhook URL**: your smee.io channel URL (same as `SMEE_URL`)
   - **Webhook secret**: same as `GITHUB_WEBHOOK_SECRET`
   - **Permissions**: Pull requests → Read & write
   - **Subscribe to events**: Pull request
   - Install the App on the test repo (re-accept permissions on the installation if you changed them).
4. `npm run dev`. The server listens on `PORT` and forwards the smee channel to `/api/github/webhooks`.
5. Open a PR on the test repo. The server logs the delivery and the App comments on the PR.

## Layout

- `src/index.ts`: HTTP server, webhook middleware, smee forwarding
- `src/app.ts`: GitHub App instance and webhook handlers
- `src/config.ts`: env loading
