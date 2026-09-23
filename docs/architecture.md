# Architecture (as built)

```mermaid
flowchart LR
  dev([Developer])

  subgraph gh[GitHub]
    pr[Pull request]
    comment[Groundtruth comment<br/>claims + checkboxes<br/>+ hidden state]
    check[Check run<br/>queued]
  end

  smee[smee.io channel<br/>dev only]

  subgraph server[Groundtruth server · Node, one process]
    verify[Webhook middleware<br/>verify signature<br/>with webhook secret]
    onpr[PR opened / reopened<br/>respond 200 now]
    limiter[Extraction limiter<br/>max 4 at once]
    job[Background job<br/>1. post/reuse placeholder<br/>2. build evidence<br/>3. extract claims<br/>4. edit comment]
    onedit[Comment edited<br/>ignore bots · parse boxes]
    approve[On Approve:<br/>final claim list,<br/>banner + state,<br/>startTesting]
  end

  claude[Claude API<br/>Opus 5 · ~$0.04/PR]
  sandbox[Browser testing<br/>not built yet]

  dev -- opens PR --> pr
  dev -- ticks boxes, approves --> comment
  pr -- webhook --> smee
  comment -- webhook: edited --> smee
  smee --> verify
  verify --> onpr --> limiter --> job
  verify --> onedit --> approve
  job -- read PR, issues, commits --> gh
  job -- evidence --> claude
  claude -- claims + assumptions --> job
  job -- post / edit --> comment
  approve -- edit --> comment
  approve -- create --> check
  approve -. approved claims .-> sandbox
  sandbox -. per-claim results .-> check

  classDef future stroke-dasharray: 5 5
  class sandbox future
```

Calls to GitHub's API use an installation token: the app signs a token with its **private key**, and GitHub exchanges it for a short-lived token scoped to the installation. Incoming webhooks are checked against the **webhook secret**.

**Where state lives:** the PR comment is the only record for a live PR (claims, the developer's edits, approval). There's no database yet, and server logs are console output only.

## Offline: evals

```mermaid
flowchart LR
  cases[evals/cases/*.yaml<br/>reference claims<br/>dev / test split]
  snap[evals:snapshot<br/>evidence as of PR open]
  evidence[evals/evidence/*.txt]
  runner[evals runner<br/>3 runs per case]
  extract[Extractor<br/>same code as server]
  judge[Judge<br/>Opus 5.5]
  out[evals/runs/&lt;time&gt;/report.md<br/>+ log.csv: scores, cost]
  spot[spot checks<br/>human labels vs judge]

  cases --> snap --> evidence --> runner
  runner --> extract --> judge
  cases --> judge
  judge --> out
  out --> spot
```
