# Eval candidates: open-source PRs for claim extraction

Researched 2026-09-22. Every PR below is merged. Line counts leave out lockfiles.
Types: `new-feature`, `behavior-change`, `bugfix`, `ui-only`, `no-claims` (the correct output is zero claims), `vague` (thin or empty description).

Many 2026 PR descriptions were written with AI help. Actual tags these with `[AI]` in the title, and Uptime Kuma has a disclosure checkbox. Tag these in the eval set so they can be scored separately. Lobsters forbids code written with LLM tools, so its PRs are the most clearly human-written.

## Shortlist

| Repo | Stack | Boot | Descriptions |
|---|---|---|---|
| [actualbudget/actual](https://github.com/actualbudget/actual) | React/TS (Vite), local-first | Easy: `yarn start`, runs in the browser only, has a demo budget | Good: structured, with screenshots |
| [mealie-recipes/mealie](https://github.com/mealie-recipes/mealie) | FastAPI + Nuxt/Vue | Easy–medium: SQLite, uv + pnpm, needs recipes seeded | Good: "what / why" template, linked issues |
| [lobsters/lobsters](https://github.com/lobsters/lobsters) | Rails, server-rendered | Easy–medium: SQLite, `db:setup` + `fake_data` | Mixed: short, the intent is often in the linked issue |
| [louislam/uptime-kuma](https://github.com/louislam/uptime-kuma) | Vue + Node/Socket.io | Easy: `npm run dev`, SQLite | Good/mixed: checklist template adds noise |
| [usememos/memos](https://github.com/usememos/memos) | Go + React | Easy: SQLite | Good but long and model-sounding |
| [umami-software/umami](https://github.com/umami-software/umami) | Next.js + Prisma | Medium: needs Postgres and seeded event data | Good/mixed |

Start with Actual, Mealie and Lobsters. Use PRs from the last ~12 months, because early history is mostly dependency setup or has empty descriptions, and old dependencies are hard to boot.

## Candidate PRs

**Actual Budget**
Every Actual PR adds an `upcoming-release-notes/*.md` file, a human-written summary of the change. It lives in the diff, so claim extraction doesn't see it.
- [#8780](https://github.com/actualbudget/actual/pull/8780) `new-feature` (691 lines): toggle to show transfers in the Sankey "Spent" view
- [#8790](https://github.com/actualbudget/actual/pull/8790) `behavior-change` (192): the Sankey date picker no longer allows months before the first transaction
- [#8885](https://github.com/actualbudget/actual/pull/8885) `bugfix` (11): the Balance Forecast card now uses the report's Daily/Monthly setting
- [#8763](https://github.com/actualbudget/actual/pull/8763) `bugfix` [AI] (370, fixes #8760): category/group menus open again after a rename
- [#8759](https://github.com/actualbudget/actual/pull/8759) `new-feature` [AI] (191): hovering a long transaction note shows a tooltip
- [#8957](https://github.com/actualbudget/actual/pull/8957) `bugfix` [AI] (12, fixes #8888): CSV import accepts "Sept" (setup needs a file upload)

**Mealie**
- [#8402](https://github.com/mealie-recipes/mealie/pull/8402) `new-feature` (146): condensed shopping-list view plus a hide-section-labels option
- [#8363](https://github.com/mealie-recipes/mealie/pull/8363) `behavior-change` (7, fixes #8362): empty shopping-list names are rejected and submit is disabled
- [#8424](https://github.com/mealie-recipes/mealie/pull/8424) `bugfix` (11, fixes #8423): the household "disable comments" setting now hides comments
- [#8383](https://github.com/mealie-recipes/mealie/pull/8383) `bugfix` (109, fixes #8382): cancelling Manage Aliases throws away the rename
- [#8427](https://github.com/mealie-recipes/mealie/pull/8427) `ui-only` (38): "mealplan" becomes "meal plan" across the UI
- [#8356](https://github.com/mealie-recipes/mealie/pull/8356) `no-claims` (84): TypeScript type generation made deterministic

**Lobsters**
- [#2102](https://github.com/lobsters/lobsters/pull/2102) `new-feature` (55): RSS feed of a user's comments
- [#2143](https://github.com/lobsters/lobsters/pull/2143) `behavior-change` (14, fixes #2111): `ref=` parameter stripped from submitted URLs
- [#2132](https://github.com/lobsters/lobsters/pull/2132) `behavior-change` (16, fixes #2109): story buttons disabled in preview; the intent is in the issue
- [#2133](https://github.com/lobsters/lobsters/pull/2133) `bugfix` (35, fixes #2121): collapsing a comment hides its reply form
- [#2029](https://github.com/lobsters/lobsters/pull/2029) `vague` (8): the description is only template text; adds a minimum title length
- [#2172](https://github.com/lobsters/lobsters/pull/2172) `no-claims` (74): dead code removed from models the UI uses

**Uptime Kuma**
- [#7672](https://github.com/louislam/uptime-kuma/pull/7672) `new-feature` (16): show/hide toggle on the login password field
- [#7607](https://github.com/louislam/uptime-kuma/pull/7607) `behavior-change` (31, fixes #7264): 24-day maximum on monitor intervals removed
- [#7739](https://github.com/louislam/uptime-kuma/pull/7739) `bugfix` (14): swapped ping-timeout limits fixed
- [#7690](https://github.com/louislam/uptime-kuma/pull/7690) `bugfix` (7, fixes #7684): pressing Enter on a custom status range adds it
- [#7855](https://github.com/louislam/uptime-kuma/pull/7855) `vague` (2): the whole description is "Very stupid mistake."

**Memos**
- [#6335](https://github.com/usememos/memos/pull/6335) `new-feature` (258): search terms are highlighted in memo content
- [#6275](https://github.com/usememos/memos/pull/6275) `behavior-change` (133, fixes #6265): the editor gets focus automatically on desktop Home
- [#6269](https://github.com/usememos/memos/pull/6269) `behavior-change` (114, fixes #6157): a blurred memo's header and reactions stay clickable
- [#6346](https://github.com/usememos/memos/pull/6346) `bugfix` (31): checkboxes in numbered task lists toggle again
- [#6364](https://github.com/usememos/memos/pull/6364) `ui-only` (112, fixes #6363): styling for `<details>` sections
- [#6187](https://github.com/usememos/memos/pull/6187) `no-claims` (8): Go refactor from `Split` to `SplitSeq`

**Umami**
- [#4262](https://github.com/umami-software/umami/pull/4262) `new-feature` (89): switch between table and card view in data grids
- [#4434](https://github.com/umami-software/umami/pull/4434) `behavior-change` (11): the funnel window rejects 0 or negative values
- [#4259](https://github.com/umami-software/umami/pull/4259) `bugfix` (42): hidden event series stay hidden when the date range changes
- [#4472](https://github.com/umami-software/umami/pull/4472) `ui-only` (10): scrollbar hidden when the sidebar is collapsed
- [#4169](https://github.com/umami-software/umami/pull/4169) `vague` (19): empty description; the realtime link works again

## Rejected

Maybe (archived) · Vikunja (96% bot PRs) · Rallly (one author) · Owncast (one maintainer, needs RTMP) · Linkwarden (thin bodies) · Docmost (one maintainer, needs Postgres + Redis) · Outline (external auth, heavy infra) · Hoppscotch (monorepo) · Excalidraw (a canvas is hard to check by clicking) · Kutt (inactive) · Shlink (API only) · Wiki.js (stalled) · Paperless-ngx (Redis + OCR) · Tandoor (61% bots) · Kanboard (one maintainer) · Healthchecks (no merged PRs) · Livebook (dev tool). Reserve: Baby Buddy (Django + SQLite).

## Exploration evals: PRs with a bug fixed inside the PR

For testing the exploring agent: run it on the commit before the fix (a claim should fail) and on the fix (it should pass). The rule: the bug must break something the PR's intent states as of opening (description, linked issues), or the agent could never have been given that claim.

- **Regression-step candidate, not an exploration case:** [Mealie #7091](https://github.com/mealie-recipes/mealie/pull/7091) "Improve add shopping list item form". Buggy [`327d208`](https://github.com/mealie-recipes/mealie/commit/327d20888dd5167f1bdfb6513d31734a958546f7), fixed four minutes later in [`889ceab`](https://github.com/mealie-recipes/mealie/commit/889ceabbdc3422db741ad784bb58b85dd5b8b7c8) "fix missing emit for note field": pressing Enter in the note field stopped adding the item. The intent is layout only (field order, label icons inside the input, a bottom drawer on mobile, "desktop remains the same"), so no claim covers it; the regression step should catch it by replaying an older "add an item with a note" test.
