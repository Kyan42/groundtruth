> This is the earlier write-up, kept for the part of the code it describes: properties checked by searching the code for every way in. That path is still in the code (`service/harness.py`), but the everyday path is now claims with approved variations, described in the README.

# Groundtruth (prototype)

Groundtruth checks changes made by a coding agent. The app's owner writes a few properties: general rules about the app, one plain sentence each. Groundtruth finds every way into the app that the rule is about, plans concrete cases, carries them out against the running app, and shows a human the evidence for every verdict. A check that only uses the browser can pass an app that works but runs the wrong way, so Groundtruth also measures the running process when a property is about how the app runs.

## How it works

Groundtruth never builds or starts your app. Your coding agent starts it the way the project already does, then registers where it is. There are two halves:

- The **verifier service** decides what to check and turns the results into verdicts. It runs the model. It is meant to be hosted; in this prototype it is a separate local process (`groundtruth.py serve`).
- The **tool server** runs on your machine, inside `groundtruth.py verify`. It makes every connection itself and carries out the service's actions through a fixed list of tools (`toolserver/catalog.py`). It refuses any other tool, any address outside the registered app (redirects included) and any file outside the registered repo. It writes a receipt (one log line) for every call, and it builds the evidence in the report from what it saw on this machine, not from what the service says it saw.

```
 your machine                                          verifier service (hosted; local here)
 +-------------------------------------+               +-----------------------------------+
 | your app (started by your agent)    |  properties,  | harness: point, search, label,    |
 |        ^                            |  app URL,     | plan, run cases; checks results;  |
 |        | browser, HTTP, ps, files   |  has_pid...   | decides verdicts from the table   |
 | tool server (groundtruth.py verify) | ------------> |        |                          |
 |  fixed tool list, receipts, report  | <-- actions - |        v                          |
 |                                     | -- results -> | model (claude -p) + tool list     |
 +-------------------------------------+               +-----------------------------------+
   every connection starts on the left; the service never reaches into your machine
```

A *conversation* below is one model session with a fixed tool list. A *way in* (also called an entry point) is one place where the property's action can be triggered: a control on a page, or an API route. A *case* is a short set of steps a person could follow. Each property goes through five steps:

1. **Point at the start and search.** One conversation reads the code and points at the exact line that carries out the property's action (for a cart, the line that writes the item into the stored cart). It names the data that line writes, says what kind of property it is, and answers one yes or no question: does the property say something must never happen? Then plain code, not a model, does the searching. It looks for other lines that write the same data, so that a second way of doing the action is not missed. It follows function names up to the API routes, route addresses to the files that call them, and those files up to the pages. It also flags places that build a request address from parts.
2. **Label hits.** Each search hit gets its own short model call (no tools, one answer) that labels it, for example "calls it", "a mention" or "same name, different thing". Any file the search found that is shown on a page becomes a way in, even if its label dismissed it; the report marks those "kept to be safe". Files shown on no page are excluded and listed in the report.
3. **Plan cases for the ways in.** A second conversation opens each page, reads the code, and plans cases for the fixed list of ways in. It cannot add or drop a way in. Every way in through the pages gets one basic case, plus up to 12 more cases for branches and values (already in the cart, sold out, the largest value). Cases are meant to name no files and no expected results; nothing checks this, and in practice a plan sometimes carries a number or a state the planner read in the code (see "Known gaps").
4. **Carry out each case.** Each case runs in its own conversation that never sees the code and is not told what result to expect, beyond what the case's own text says. If a reset command is registered, the data is reset first. The conversation reports one outcome: held, broke, declined (the app openly refused the action), outside (the property's wording does not cover the case), nothing there (it was asked to look for a way to do the action in a place and found none; recorded as outside, so it does not count; accepted only for a way in the search was unsure of), or could not run. A conversation that ends without an accepted result is tried once more, from the same clean start: the reset runs again, the browser is new, and a measurement case gets a new baseline. Every case that opens the app's pages is filmed: the clip (`clip-1.webm` in the case's folder, `clip-2.webm` for a second try) shows a pointer that glides to each thing before it is clicked or typed into, with a ring at each click, and the report plays it under the case's evidence. Discovery is not filmed, and a case that never opens a page has no clip. (No case needed a second try in the latest gate runs, so this restart is covered by unit tests only.)
5. **Decide the verdict.** The service's code, not a model, decides from the table of cases (see "Read results").

Properties about the code itself, and apps whose code cannot be read, skip steps 1 to 3: one conversation reads what it can and plans the cases on its own. If the search cannot be completed (for example its starting line cannot be settled, it finds no way in, or it matches more lines than can be labelled), the run falls back to that single conversation and the report says so.

There are three kinds of property. **Behaviour**: what the app does or shows. Its proof goes through the pages, and a case through the pages has no tool for calling the API. The API is called directly only for a property that says something must never happen, because a person can skip the page; such a case has no browser. **Measurement**: how the app runs. Before each case touches the app, the service itself measures the process; the case triggers the action through a way in and must measure again after its last action. These cases run before any other property's cases, and a pass is only about this machine. **Code**: the code itself. Its cases read source, check every file in scope with `match_each` when the rule is about every file, and cite what they read.

## Quick start (bundled demo)

Prerequisites: macOS (the GPU check reads `vmmap`), Python 3.12, and Claude Code installed and signed in at `~/.local/bin/claude` (or set `GT_CLAUDE` to its path in the shell that runs `serve`). Run every command from the repo root.

Install:

```bash
python3.12 -m venv .venv && .venv/bin/pip install playwright==1.64.1 mcp==2.3.0 pyyaml==6.0.3 && .venv/bin/playwright install chromium
```

The demo uses port 8790 for the service and 8765 for the app. Check that both are free; this prints nothing when they are:

```bash
lsof -nP -iTCP:8790 -iTCP:8765 -sTCP:LISTEN
```

If one is taken, pick other ports: `serve --port <n>` with `--service http://127.0.0.1:<n>` on `verify`, and a different number after `demo/app.py`.

You need two long-running processes. Either open three terminals, or start the two in the background from one shell as shown here.

1. Start the verifier service (default port 8790, default model `claude-sonnet-5-5`; `--port` and `--model` change them). It does not return. On start it prints `verifier service on http://127.0.0.1:8790 (model claude-sonnet-5-5, work dir <temp folder>)`.

```bash
nohup .venv/bin/python groundtruth.py serve > /tmp/gt-service.log 2>&1 &
until curl -s http://127.0.0.1:8790/health; do sleep 1; done; echo
```

The health check prints `{"ok": true, "version": "3", "model": "claude-sonnet-5-5"}`.

2. Start the demo app. `DEMO_AGENT_VERSION=1` (the default) is the coding agent's version, which opens the GPU; `0` is the fixed version that stays on the CPU. It prints `avatar demo on http://127.0.0.1:8765`.

```bash
DEMO_AGENT_VERSION=1 nohup .venv/bin/python demo/app.py 8765 > /tmp/gt-demo.log 2>&1 &
until curl -s -o /dev/null http://127.0.0.1:8765/; do sleep 1; done
```

3. Register the app and verify the `lip-sync` feature (`demo/.groundtruth/features/lip-sync.yml`, three properties: g1 is about how the app runs, g2 about what it shows, g3 about the code).

```bash
.venv/bin/python groundtruth.py register demo --url http://127.0.0.1:8765
.venv/bin/python groundtruth.py verify demo lip-sync
```

`register` finds the process id by asking which process listens on the URL's port and prints:

```
Registered http://127.0.0.1:8765 (pid <pid>) for demo
  the verifier can use: browser and API, process measurements, code reading
  no reset command: cases won't start from clean data
```

`verify` is slow: every case is a model conversation, and a full demo run took about ten minutes in one test. It can be silent for minutes; give it a long timeout or run it in the background. `--only g1` runs one property. It prints lines of this shape (an illustration, not a recorded result):

```
Run <run id>: 3 properties against http://127.0.0.1:8765
  g1/discovery: 6 cases through 2 entry point(s), kind measurement
  g1/c1 breaks: <first 150 characters of what the case observed>
  g1 fails (broke: c1, c2): Rendering speech never opens a GPU device in any app process.
  g2 holds (8 of 8 cases): Speaking a text of up to 500 characters ends with the status "Done: N frames", where N equ
Result: 2 hold, 1 fail, 0 need your decision, 0 can't tell
Details: <repo>/demo/.groundtruth/runs/<run id>/run.json
Report: <repo>/demo/.groundtruth/runs/<run id>/report.html
```

- `<property>/discovery: N cases through M entry point(s), kind K` is printed when a plan is accepted, or `no plan (<reason>)`. M counts every way in the search found, including API routes that get no case because the property is not a "never" property.
- `<property>/<case> <outcome>: ...` is printed as each case finishes. The outcome is `holds`, `breaks`, `declined`, `outside` or `cant_run`. A "nothing there" case prints as `outside`, and its text starts with "No way to do the action was found there."
- `<property> <verdict> (<note>): <first 90 characters of the sentence>` is printed per property at the end. The note lists the cases that matter: `H of P cases`, `broke: ...`, `your call on: ...`, `not run: ...` (or `not run: no plan`), or `P cases planned, none carried out`.

Open `report.html` in a browser for the ways in and how they were found, every search hit and its label, the case table, screenshots, measurements, cited code and every receipt. On the agent's version g1 is expected to fail. Stop the app, start the fixed version, register again and verify g1 again; it is expected to hold:

```bash
kill $(lsof -nP -iTCP:8765 -sTCP:LISTEN -t)
DEMO_AGENT_VERSION=0 nohup .venv/bin/python demo/app.py 8765 > /tmp/gt-demo.log 2>&1 &
until curl -s -o /dev/null http://127.0.0.1:8765/; do sleep 1; done
.venv/bin/python groundtruth.py register demo --url http://127.0.0.1:8765
.venv/bin/python groundtruth.py verify demo lip-sync --only g1
```

When you are done, stop both processes:

```bash
kill $(lsof -nP -iTCP:8765 -iTCP:8790 -sTCP:LISTEN -t)
```

What the common failures print (each stops `verify` with exit status 1):

- `Nothing is listening on port 8765. Start the app first, then register it.` (from `register`)
- `No app registered. Start it, then: groundtruth.py register <repo> --url ...`
- `couldn't reach the verifier service at http://127.0.0.1:8790 (...)` followed by `Start the service first: groundtruth.py serve`
- `the registered process (pid N) is no longer running. Register the app again after a restart.`
- `<feature>.yml is not valid YAML near line N. ...` (see the colon rule below)
- `The run did not finish: FileNotFoundError: ...` usually means `claude` is not at `~/.local/bin/claude`; set `GT_CLAUDE` before starting `serve`.

## Using it on your own app

The verifier service has to be running (step 1 of the quick start; the quick start's cleanup stops it). Run every command from the Groundtruth repo root and pass your app's folder as `<repo>`. Start your app the way the project already does before you register it.

### Register

```bash
.venv/bin/python groundtruth.py register <repo> --url http://127.0.0.1:<port> [--pid PID] [--reset "CMD" [--skip-reset-check]] [--routes SOURCE] [--no-repo] [--no-pid]
```

This writes `<repo>/.groundtruth/env.json`. What you register decides what the verifier can do:

| What | Flag | Unlocks | Without it |
|---|---|---|---|
| App URL | `--url` (required, a full address such as `http://127.0.0.1:3000`) | pages and API | nothing works |
| Process id | found from the port, or `--pid`; `--no-pid` leaves it out | process measurements | measurement cases can't run, so those properties end in "can't tell" |
| Repo | the `<repo>` argument; `--no-repo` leaves it out | the code search, and code reading when planning and in code properties' cases | ways in are found by one conversation exploring the app only; code properties can't be checked |
| Reset command | `--reset "<shell command>"`, run in the repo | every case starts from clean data | each case sees whatever earlier cases left |
| Route source | `--routes next`, `openapi:/openapi.json`, `file:routes.json` or `command:<shell command printing "METHOD /path" lines>` | where the `list_routes` tool gets the full list | built-in readers are tried: Next.js build output and route files (needs a repo), then `/openapi.json` |

The process id must belong to the process that does the work. `register` picks the process listening on the URL's port; for an app started through `npm`, that is the server, not `npm`. Registering also records the commit, whether there are uncommitted changes, and the process's command line and start time; the report shows them.

The reset command runs in the repo, with the environment and `PATH` of the shell that runs `verify`. `register` runs it once, the same way (in the repo, at most 120 seconds), before it writes anything. If it fails or takes too long, `register` prints the last 300 characters of its output, writes nothing and exits 1. `--skip-reset-check` stores the command without trying it; a reset that fails then only shows up later, as cases that could not run. Each `register` replaces the earlier registration for that repo completely, including its reset command and route source.

### Give cases their own data (optional)

Some properties can only be tested on data the reset data does not contain, for example a listing priced above anything seeded. For these, the project can declare **data tools**: small commands that make one kind of thing. They go in `<repo>/.groundtruth/data.yml` (the full rules are in `DATATOOLS.md`):

```yaml
tools:
  - name: make_listing
    does: Creates one listing in the shop and returns its id and the address of its page.
    takes:
      title:
        type: string
      price_cents:
        type: integer
        does: The price in cents.
      sold_out:
        type: boolean
        optional: true
    run: node scripts/groundtruth/make-listing.mjs
```

Each tool has a `name`, one sentence in `does` saying what it makes, the inputs it `takes` (each `string`, `integer`, `number` or `boolean`, optionally `optional: true`) and a shell command in `run`, run in the repo like the reset command. The command receives the inputs as one JSON object on standard input (never on its command line) and the registered app address in `GROUNDTRUTH_APP_URL`. It must exit 0 within 60 seconds and print one JSON object of at most 2,000 characters, saying what it made (ids, addresses). A file that breaks the rules stops `verify` at the start with a message saying what is wrong.

The planner is told each tool's name, sentence and inputs, never its command. A planned case may then start from up to 6 steps, each with one sentence saying why the pages could not make that state. For each case, plain code resets the data, runs the steps, and then starts the case's conversation, which is told what was made; the conversation itself never gets a data tool. Data tools are only used when a reset command is registered. When no declared tool can make the state a case needs, or a step fails, the case is reported as could not run; in the first situation the result carries a request naming the tool the case wants, so your agent can add it.

### Write properties

Properties live in `<repo>/.groundtruth/features/<feature>.yml`. The file name without `.yml` is the feature name you pass to `verify`. Set the `feature:` field to the same name, because `decide` looks runs up by it. A complete example:

```yaml
feature: cart
goal: Shoppers collect items in a cart before buying.
properties:
  - id: p1
    statement: Adding an in-stock item to the cart raises the cart subtotal by the price shown on its listing.
    example: Adding the diving helmet to an empty cart makes the subtotal $249.99.
  - id: p2
    statement: >-
      A sold-out item can't be added to the cart: trying to add it, from a page or by calling the API,
      leaves the cart unchanged.
```

Format: `feature` and `properties` are required, `goal` is optional. Each property needs a unique `id` (1 to 32 letters, digits, `_` or `-`) and a `statement`. An optional `example` becomes one case in the plan, never the whole proof. At most 15 properties per run. Keep `properties:` as the last key with items indented two spaces, because `decide` appends to the end of the file as text.

A good property is a general rule, not one example: "Adding an in-stock item to the cart raises the cart subtotal by the price shown on its listing", not "adding the helmet shows $249.99". Say what it applies to and what is compared with what, in ordinary words. A property checks only what it says, so say everything you care about. Write what should be true, never how to check it: no tool names, selectors or commands. Use "never", "cannot" or "must not" only when you mean it: that is what makes the verifier also call the API directly.

The colon rule: a sentence that contains a colon followed by a space must be in double quotes, or written on the lines after `statement: >-` as in p2 above. Otherwise the file is not valid YAML and `verify` stops with a message saying so.

### Run verify

```bash
.venv/bin/python groundtruth.py verify <repo> <feature> [--only p1,p2] [--plan-only] [--exit-code] [--service http://127.0.0.1:8790]
```

- `--only` takes a comma-separated list of property ids, with no spaces.
- `--plan-only` finds the ways in and plans the cases without carrying out any case. Every property with a plan gets the verdict `planned`.
- `--exit-code` makes the exit status carry the result: 0 only when every property holds, 1 when any property fails, 2 otherwise (something needs your decision, can't tell, is only planned, or the run did not finish). Without it, a completed run exits 0 whatever the verdicts. A run that cannot start prints why and exits 1 either way.
- `--service` is the service address. The default is `GT_SERVICE` if set, else `http://127.0.0.1:8790`.
- If the feature's claims were approved with `approve` (see "Using it from your coding agent"), `verify` first prints one line for each property that was changed or added since: `changed since it was approved: tabs (the sentence, the variations)` or `not approved yet: film`. The run goes ahead either way. The comparison is saved in `run.json` under `approval`, and the dashboard's run page says the same at the top.
- `GT_HEADED=1` (set in the shell that runs `verify`) shows the browser while it works.

### Read results

Each property gets one verdict, decided by the service's code from the table of cases, in this order:

- **fails** (`fails`): at least one case broke. Nothing softens this.
- **needs your decision** (`needs_decision`): nothing broke, but in at least one case the app openly declined the action (a disabled control, an error message, a refusal), so the property could not be observed there. The report shows that case's question, what happened, and a drafted sentence stating what the app does there. Questions never pause a run.
- **can't tell** (`cant_tell`): a planned case was not run, there was no plan, or no case held (for example every case was outside).
- **holds** (`holds`): every planned case was run, none broke or was declined, and at least one held. Outside cases do not count.
- **planned** (`planned`): only with `--plan-only`.

In the report, case outcomes read held, broke, declined by the app, outside the property, and could not run. Each way in is marked "found by the search", "flagged" (it builds its request address from parts) or "kept to be safe" (a label dismissed it, but its file is on that page).

Files, under `<repo>/.groundtruth/runs/<run id>/`:

- `run.json`: the whole run (registration, service, case order, every property with its plan, search record, cases, results and verdict).
- `sent.json`: exactly what was sent to the service at the start.
- `report.html`: the human report, one self-contained file.
- `<property id>/discovery/` and `<property id>/<case id>/`: each holds `report.json` (the step's result, including the search record) and `receipts.jsonl` (every tool call with its result), plus screenshots (`shot-N.png`) and every-file checks (`check-N.json`) when there are any.
- `runner.jsonl`: only when the tool server refused an action before any step was open.

A case that started from made data has `starts_from` in its result (in `run.json`, its `report.json` and the run document): the steps asked for on this machine, each with its reason and what it returned or how it failed (a step the tool server refused is listed as failed). A case not run for lack of a data tool has `data_request` instead: the starting state it needed and the tool it asks for (`name`, `does`, `takes`). `report.html` shows only the case's one-line reason it was not run, not the steps or the requested tool.

The service keeps each conversation's raw output in the work folder it printed at start. `report` rebuilds the HTML (the latest run if no id is given):

```bash
.venv/bin/python groundtruth.py report <repo> [run id]
```

### Answer a question

The report shows two ready-to-copy commands under each question. The last argument is `intended` or `bug`. For example, for case c4 of property p0 in the `cart` feature of an app at `../shop`:

```bash
.venv/bin/python groundtruth.py decide ../shop cart p0 c4 intended
```

`decide` finds the most recent run of the feature that asked about that case, records your answer in `<repo>/.groundtruth/decisions/<feature>.json`, and prints `Recorded: p0/c4 is intended.` For `intended` it also prints the property it added and a reminder that you can edit the sentence.

- `intended`: the situation is outside the property. The drafted sentence is appended to the feature file as a property of its own, with id `<property id>-1` (or the next free number) and a `came_from` line, so the accepted behaviour is checked too. Edit the sentence if it isn't what you meant.
- `bug`: a refusal in that situation counts as breaking the property from now on.

The answer changes nothing in the run that asked; run `verify` again to see it applied. If you verify with `--only`, include the new property's id (for example `--only p0,p0-1`), or it is not checked. Answers are sent to the service with the property on every later run.

### Re-run after a fix

Restart the app the usual way, register again, then verify again, optionally only the properties that did not hold. A restart gives the app a new process id, and `verify` refuses a registration whose process is gone or is a different process with the same id.

```bash
.venv/bin/python groundtruth.py register <repo> --url http://127.0.0.1:<port>
.venv/bin/python groundtruth.py verify <repo> <feature> --only p2
```

## The results store and the dashboard

The results store keeps your finished runs so a dashboard can show them, with every case's film and screenshots. Here it runs on your own machine: a SQLite file stands in for the hosted database and a folder of files stands in for hosted file storage, behind the same web address scheme the hosted version will have (`store/CONTRACT.md`). The dashboard's pages are served from the same address.

```bash
.venv/bin/python groundtruth.py store [--port 8900] [--data DIR]          # run the store; prints its address and where its token is
.venv/bin/python groundtruth.py push <repo> [run id] [--project NAME]     # upload one finished run (the newest if no id)
.venv/bin/python groundtruth.py verify <repo> <feature> --push            # upload right after the run, then print the dashboard address
```

- The store keeps its data in `.store/` beside `groundtruth.py` unless you give `--data`. On first start it makes one user, `local`, and writes that user's upload token to `<data folder>/token`, readable only by you. Only a hash of the token is kept in the database.
- `push` and `--push` find the store at `--store`, else `GT_STORE`, else `http://127.0.0.1:8900`, and use the token in `GT_STORE_TOKEN`, else `.store/token`.
- An upload contains the run document (`RUNDOC.md`: the properties, verdicts, cases, steps, requests, readings and code excerpts, but never the repo path, the process id or a command line) and every film and screenshot the run made. Films and screenshots show your app as it was during the run.
- A failed upload with `--push` prints one line and changes neither the verdicts nor the exit code; the run stays saved, and `push` can send it later. Uploading the same run again replaces the earlier copy.
- After an upload, `push` and `--push` save what the store answered in the run folder as `pushed.json` (the store's address, the run's id there and its page address); `summary` reads the dashboard address from it.
- Uploading is only ever started by the command line, after the run is saved. No tool the verifier service can ask for uploads anything; the lock-down gate checks this.

## Using it from your coding agent

A coding agent can drive Groundtruth with a few short commands. `bin/groundtruth` runs `groundtruth.py` with the repo's own `.venv`, from any folder, so the agent never needs to change directory. Each command prints plain text the agent can pass on as it is.

```bash
bin/groundtruth install-skills <project> [--remove]   # copy skills/<name>/SKILL.md into <project>/.claude/skills/
bin/groundtruth up [--service-port 8790] [--store-port 8900]
bin/groundtruth status [--service-port 8790] [--store-port 8900]
bin/groundtruth claims <app folder> <feature>
bin/groundtruth approve <app folder> <feature>
bin/groundtruth summary <app folder> [run id] [--json]
```

- `install-skills` writes each skill with `{{GROUNDTRUTH}}` replaced by the full path of `bin/groundtruth`, and prints each file it wrote. It also prints the one line you can add under `"permissions"` > `"allow"` in `<project>/.claude/settings.local.json` so the agent may run the launcher without asking; it never edits a settings file. `--remove` deletes only the skill files it would have written.
- `up` starts the verifier service and the results store in the background if nothing answers `/health` on their ports, waits up to 20 seconds for each, and prints one line each: already running, or started (with its log file). Logs and process ids are kept in `.run/`. It never stops or restarts anything; if a port is taken by something else it says so and exits 1. `status` prints one line each, running or not, with the service's version; it exits 1 unless both are running. The ports match what `verify` and `push` use by default.
- `claims` prints the feature file as it is on disk, for the agent to show its owner word for word: the goal, then for each property its id, title, the owner's own words, the full sentence, what it is checked against and each variation, and whether it is approved, changed since it was approved, or not approved yet. A file `verify` would refuse is refused here too, with the same message.
- `approve` records the claims as they now stand in `<app>/.groundtruth/approved/<feature>.json` and prints one line per property. Approving again replaces the record. A part counts as changed only if its words changed; spacing and line breaks do not count.
- `summary` describes a finished run (the newest if no id is given): one line for the run, one per property (its verdict and how many variations or cases held), one per case that did not hold (with its question, what it saw, or the data tool it asks for), any approval lines, and the dashboard address if it was uploaded. If the newest run has no `run.json` yet (it is still running, or it broke), it says so and exits 1. `--json` gives the same as one JSON object.

## The verifier's tools

Which conversation gets which tools is decided by the service (`discovery_tools` and `case_tools` in `service/harness.py`):

| Group | Tools | Pointing at the start, planning, single-conversation discovery | Case through the pages | Direct API case | Measurement case | Case of a code property |
|---|---|---|---|---|---|---|
| Pages | `observe`, `navigate`, `click`, `fill`, `read_attribute`, `click_and_watch`, `wait`, `screenshot`, `media` | `observe` and `navigate` only | all | none | all, if its way in is the pages | none |
| API | `http_request` (one request; redirects not followed) | no | no | yes | yes, if its way in is the API | no |
| Process measurements | `process_info`, `gpu_check`, `cpu_sample`, `net_connections` | no | no | no | yes, when a process id is registered | no |
| Routes | `list_routes` | yes | no | no | no | no |
| Source | `list_files` (names), `find` (every `path:line`, no text), `grep` (at most 15 matching lines), `read_lines` (at most 15 lines per read) | when a repo is registered | no | no | no | yes |
| Every-file check | `match_each` (every file matching a glob must or must not contain a pattern) | no | no | no | no | yes |

`observe` lists the page as an accessibility tree and puts a reference such as `[ref=e12]` on every element. `click`, `fill` and `read_attribute` take that reference, so a control with no name of its own, or with the same name as another, can still be used; a role and exact name, a label or a test id also still work. A reference is a fixed-form token checked by the tool server, never a selector, and one that no longer points at an element is an error, never a guess. The receipt of a click says what the element was, read from the page at that moment. `fill` reports how many characters it typed. `media` reads which file each video or audio element has loaded, its length and position, and whether it is playing; it says what is loaded, not what the picture looks like. A case through the pages is filmed: before each `click`, `click_and_watch` and `fill`, a pointer drawn in the page glides to the centre of the target, and the real action then lands where it stopped. The pointer never makes an action fail, and the accessibility tree `observe` returns is the same with filming on or off. A plan that carries a page reference is sent back.

Steps that use no model at all: the code search itself (the `trace` tool, which only the service's own code calls: searches, the lines around a hit, which function a line is in, who uses a file, the pages), the measurement taken before each measurement case, the data reset (the tool server runs the registered command itself), a case's starting steps (the `set_up` tool, which only the service's own code calls, after the reset and before the case's conversation starts), and the verdict. The labelling calls get no tools. The tool server also has `probe_each` (one request to every listed route), which no conversation is given. It also has `data_tools` (the declared data tools, without their commands), which nothing calls during a run: the list goes to the service at the start. The tool server refuses `set_up` while finding cases, after any other call in the case, past 6 calls, and when no reset command is registered. Hidden files and folders (including `.groundtruth/`), `node_modules` and `__pycache__` are never listed or read.

## What leaves your machine

Sent to the service at the start (saved as `sent.json`): the feature name and goal; each property's id, statement, example and your past answers to its questions (with what was observed); the app's URL; yes or no flags for whether a process id, repo, reset command and route source are registered; and each declared data tool's name, sentence and inputs (never its command).

After that, the text and data of every tool result: what each data tool returned (the case is told it), page accessibility trees, HTTP responses, measurement output, route and file lists, code locations, check results and source lines. With a repo registered, the code search sends the text of every line each search matches (up to 200 characters per line, up to 500 lines per search), the file names on the paths it follows, the list of pages, and, for labelling, up to 15 lines of code around each hit it labels. Conversations read up to 15 lines per read. `--no-repo` is the only way to stop source leaving the machine.

Nothing is uploaded to the results store unless you run `push` or `verify --push`. If you do, the hosted version would then hold the run document and the films and screenshots of your app (see "The results store and the dashboard").

Not sent, by design: the repo path, the process id value (only "has a pid"), screenshot images (only ids such as `shot-1`), the case films (they stay in the case folders like screenshots; only their file names and start times go back in the case's finished result, which no model reads), the receipts and `report.html`. Measurement output does contain process ids and full command lines, which can include absolute paths.

## How this prototype is checked

`gates/props_gate.py` checks the design end to end in seven streams. Each starts its own verifier service and apps on its own ports (it stops whatever already listens there), spends model usage, prints one PASS or FAIL line per check, writes `gates/results/props_<stream>.json`, and exits 0 only if every check passed.

- `cbay` (service 8790, app 3100): cbay, a Next.js shop, with the cart properties in `../cbay/app/.groundtruth/features/cart.yml`. Plans are checked against `gates/expected/cbay_cart.md`; verdicts must be p0 needs your decision (declined only on sold-out cases), p1 holds, p2 holds. It also runs the answer loop ("intended", the new property appears, both then hold) and two plan-only runs.
- `qtycap` (8792, 3103): a cbay copy where adding an item already in the cart does not raise its quantity. p1 must fail, and only on cases that add an item a second time.
- `lipsync` (8793, 8765 and 8767): the demo's agent version (g1 fails, g2 and g3 hold), the fixed version (all hold), g1's cases run first, and a control copy with a planted second letter-to-shape table where g3 must fail.
- `twodoors` (8794, 3104 and 3105): cbay copies with a second way into the cart (a quick-add button on the home page). Both ways in must be found and each tested; the working copy holds, and in the broken copy p1 fails only on the quick-add cases.
- `unseen` (8795, 3100, 3106, 3107): a clean reference run on cbay, then copies the stream has not seen, compared with a sealed key only after both runs finish. It shares port 3100 with `cbay`: never run the two at once.
- `ways` (8796, 3321 to 3324): p1 on four sealed copies; after all four finish, scores how their ways in were found against a sealed key (found, missed, invented).
- `data` (8797, 3331 to 3333): p1 on three cbay copies with data tools, each registered with the shop's reset command (`gates/variants/DATA-COPIES.md`). In `cbay-data-clean` p1 must hold. In `cbay-data-bug` a listing priced at $1,000.00 or more enters the cart at the wrong amount, and no seeded listing costs that much; p1 must fail, and only on cases that started from a made listing at that price. In `cbay-data-bug-no-tool` the tool that could make such a listing is not declared; p1 must neither hold nor fail, and a case must carry a request for that tool. In all three, no conversation may be given a data tool, and no `set_up` may come after a case's first other call.

Every stream also checks each run's shape (no scope mark on planned cases, the case counts add up, every declined case has a question and a drafted sentence, behaviour and measurement plans came from the search, code locations are listed) and how each case ran, from its receipts: a page case had no API tool, an API case had no browser, behaviour and measurement cases had no source tools, and a measurement case has a baseline and a GPU measurement before its first action.

```bash
.venv/bin/python gates/props_gate.py cbay
.venv/bin/python gates/props_gate.py qtycap
.venv/bin/python gates/props_gate.py lipsync
.venv/bin/python gates/props_gate.py twodoors
.venv/bin/python gates/props_gate.py unseen
.venv/bin/python gates/props_gate.py ways
.venv/bin/python gates/props_gate.py data
.venv/bin/python gates/props_gate.py --check-only <run dir> <checkset>
```

`--check-only` re-applies one set of checks to an existing run folder with no apps, service or model; run the script with no arguments to list the checksets. The `cbay`, `qtycap`, `twodoors`, `unseen` and `ways` streams need cbay at `../cbay/app` with a production build and Node 22 at `/opt/homebrew/opt/node@22`. `qtycap` and `twodoors` need the copies `gates/work/cbay-qty-cap`, `cbay-two-doors` and `cbay-two-doors-broken` (made from the patches in `gates/variants/`; no script builds them). `unseen` and `ways` need sealed copies under `gates/work/unseen/` and `gates/work/ways/`, built by another agent. `data` needs the same Node 22, the copies `gates/work/cbay-data-clean`, `cbay-data-bug` and `cbay-data-bug-no-tool` (built as `gates/variants/DATA-COPIES.md` says), and a `node` of 22.18 or later first on the `PATH` of the shell that runs the gate, because the data tools run with that shell's environment. None of `gates/work/` is in the repo.

The lock-down gate plays a misbehaving service that asks for a shell command, other hosts, files outside the repo, over-long reads, bad unit names and uploads to the results store (tools named `push`, `upload`, `store` and the like, or a request to the store's address), and runs a failing reset. It checks that the tool server refuses each one, leaves receipts, sends no repo path or process id, and that the store receives nothing during the run. For data tools it checks that inputs reach a tool's command only on standard input, even values that look like shell; that `set_up` is refused while finding cases, after any other call, past 6 calls and without a reset command; and that the saved result lists the steps asked for on this machine and what came of each, not what the service claimed. It has 88 checks. It spends no model usage and binds its own servers to free ports.

```bash
.venv/bin/python gates/gate2_lockdown.py
```

Unit tests of the tool server, the code search, routes, every-file checks, the harness rules, data tools, the report, the results store and uploading (no model):

```bash
.venv/bin/python -m unittest discover -s tests
```

### Latest results (2026-11-10)

The six streams other than `data` ran on the code as it was before data tools: element references, the media tool and filming all on.

| Stream | Checks passed | What failed |
|---|---|---|
| `lipsync` | 127 of 127 | |
| `cbay` | 182 of 183 | p1 came back needs your decision: a sold-out item under the "in-stock" property was judged a question instead of outside. |
| `twodoors` | 42 of 42 | |
| `qtycap` | 19 of 19 | |
| `ways` | 109 of 112 | All 10 real ways in on the four sealed copies were found, and both planted bugs were caught on the right way in. Two checks failed inside the gate itself: its keyword check does not recognise a way in worded "add-to-cart" with hyphens (the same page and file as before). One clean copy came back needs your decision (a false question). |
| `unseen` | 172 of 173 | One false question. |

651 of 656 in all. Every planted bug is caught. The lock-down gate passed 47 of 47 and the unit tests passed. (Since then, with the results store: the lock-down gate passes 57 of 57 and 203 unit tests pass, 13 skipped in a worktree. With data tools, the lock-down gate passes 88 of 88 and 315 unit tests pass, 13 skipped in a worktree.)

The `data` stream has been built and its checks unit-tested, but it has not been run yet, so it has no results here.

The five failures are three false questions (listed under "Known gaps", not fixed) and the two inside the ways gate's own check, which was left as it is. The round took 238 conversations and 239 labelling calls, $9.49. One property costs about $0.25 to $0.45 and takes two to four minutes.

The round before this one (element references and the media tool, without filming) came to 650 of 661. In both rounds the lip-sync false break (a case miscounting the characters it typed) did not appear, and the cases' own notes show them using the character count `fill` now reports. That is two rounds, not proof it is gone.

No case needed a second try in either round, so the clean restart of a second try is still covered by unit tests only.

### First real project (a trial, 2026-11-10)

Two draft properties were run against the run review page of groundtruth-cli (a Node server that serves two HTML pages). This cost $0.80.

- "Every claim in a feature appears on that feature's run page, whatever its result." came back **fails**. The runs list counts 3 claims for the demo feature, and the run page shows 2. The third claim has no recorded result and appears nowhere on the run page.
- "Choosing one of the Claims, Actions, Network or Script tabs shows that tab's content and hides the others." came back **holds**, 6 of 6 cases.

The owner then gave three properties of his own for the same page, in these words (with "property" written as "claim", the page's word): every claim the user decided on for the current feature shows in the Intent section with its own card; every claim whose regression was run appears in the Regression section; clicking a claim's card shows the video for that claim.

- The first **fails** for the same reason as above.
- The second **can't be told**: the demo data has one feature, so there are no regressions to look for. In the first run it came back holds on a single case that looked at the empty section, which is a pass about nothing.
- The third could not be checked at first, because no click on a card ever went through. After element references were added it **holds**, 5 of 5: cases clicked the second card and the player's file changed from `c1.webm` to `c2.webm` and back. With filming on, every one of its cases has a film showing the pointer on the card.

The first verdict matches what the page shows by hand, but the evidence behind it is weaker than it looks. Three of its five cases could not select the second card in the page's list. Two of the three cases that reported a break said "1 rendered against 3", which is a miscount: the page has two cards. Only one case stated the real finding, that nothing on the run page lists a third claim. This is the careless-case problem from "Known gaps" landing on a verdict that happens to be right. The trial also showed four limits of the verifier that the test apps had not. They are the first four entries under "Known gaps".

### Cold run (2026-11-10)

A fresh agent that had only this README did the quick start as written and then used the tool on the shop app as if it were its own project: it started the app, found and registered a reset command, wrote two properties of its own, verified them, answered one question with `decide` and verified again. It never needed `--help` or the source. Each part took about 12 minutes. The verdicts were the expected ones on the demo, and on its own properties one held and one came back as a false question of the known kind. The gaps it found in this README are fixed above; what it found in the tool is under "Known gaps" (fields with no label, measurement cases after the first, expected values in case text, no next step after a fail).

## Known gaps

- Found on the first real project. The search takes an HTML page's address from where its file sits: `review/run.html` is assumed to be at `/review/run.html`. That server serves it at `/runs/<feature>`, so the way in carried an address the app answers with "not found", and the cases had to find the real page themselves. A server that picks its handler by hand, not through a framework's route files, is not followed either: a search that starts in such a server finds no way in and falls back to a model reading the code.
- Found on the first real project. The search is built around an action that changes data. For a property about what a page shows there is often nothing to follow beyond the page itself, and the starting line is less stable: in two runs of the same property the search started once in the server (no way in, fell back) and once in the page.
- A video's picture is not checked. `media` says which file a player has loaded and whether it is playing; whether the right frames are on screen is for the human looking at the screenshot.
- Found on the first real project. A case can only compare what the app shows in one place with what it shows in another. Data that never reaches a page (a file on disk) is out of its reach. And the conversation that plans the cases reads the code, so facts it read can end up in a case's text (for example "it has 3 claims; only 2 have a recorded result").
- The service runs locally, not deployed, and binds to 127.0.0.1 only. The model is reached through `claude -p` on your Claude Code login; a hosted version would use its own key.
- Web apps only: no simulators, mobile or desktop apps.
- Every case is a model conversation, and a break is not re-run, so one careless case can fail a property. Each case's outcome is the model's reading of what it observed; the service checks that the case used its way in and measured after acting, not that the reading is right.
- The start of the search is one model's judgement. If it points at the wrong line or names the data wrongly, everything after it follows that mistake.
- The search supports Next.js projects (the `app/` folder, and pages under `pages/`) and plain projects with HTML pages. Anything else finds no pages, so the run falls back to a model reading the code, with no check that it found every way in. Only a Next.js `route.*` file under `app/` counts as a certain API route; elsewhere routes are inferred from the addresses a file mentions.
- Limits the search states itself (`BLIND_SPOTS` in `service/trace.py`): a request address built from parts is only found if it matches a known request form; callers outside this repo are not searched; a way in that should exist but was never written cannot be found; code reached only through a framework convention or a name built at run time has no name to follow; a second write of the same data is only noticed when it is on one line under the data's own name.
- Finer limits (`toolserver/trace.py`): the search is line by line, so a pattern never spans lines; files over 1 MB, binary files, hidden folders and `node_modules`, `__pycache__`, `.next`, `dist` and `build` are skipped; import aliases other than `@/` and `~/` are not resolved; a request made through a wrapped client (an axios instance, a helper such as `api(path)`) or an address built in one place and used in another is not seen.
- Some search limits only leave a note in the report: a name used on more than 60 lines is not followed, following stops 6 calls deep, and cases are planned on at most 3 pages per file. Others make the whole search fall back: more than 80 lines that look like writes, or more than 120 hits to label.
- Following your answers is in the instructions only. "intended" and "bug" reach the planning and case conversations as text; no code checks that they were applied.
- The rule "call the API directly only for never-properties" is enforced by the service only when the search ran. In the single-conversation path it is an instruction.
- A property with a scope word ("in-stock item") sometimes comes back as a question about the excluded situation, because the case conversation judged it declined instead of outside.
- If the reset command fails, the case is not run and is reported as could not run.
- Data tools need a reset command. Without one the planner is told the declared tools cannot be used, so every case that needs made data comes back could not run. Its reason says that no reset command is registered, but its request may still name a tool the project already has. The fix there is to register a reset command, not to add a tool.
- Only what a declared tool's command accepts can be set up. The planner has no other way to write to the app's data, so a state no tool can make is a request, never a workaround. A tool that writes rows directly can also make a state the app could never reach; tools should go through the app's own code.
- Whether the planner asks for made data only when the pages cannot reach the state has not been measured yet.
- Measurement cases share one running process. Once one case has made the process open the GPU, every later case in that run starts with it already open and is still counted as broke, though only the first shows a clean before and after.
- Measurement is macOS only for the GPU check, and detects a GPU driver mapped into the process (a GPU device was opened), not GPU time. Ways in for every property are found before the first measurement case, and that step can load pages.
- While a case is filmed, the verifier adds one element to the app's page: `<groundtruth-cursor>`, the drawn pointer and its click rings. It is appended to `<html>`, outside `<body>`, ignores the pointer (`pointer-events: none`) and is hidden from accessibility (`aria-hidden="true"`), so clicks and `observe` are unaffected. It does show in a filmed case's screenshots: the arrow stays where it last stopped, and a screenshot taken just after a click can catch the fading ring. A page script that walks the whole document, or styles every element, could see it. The film is 900 by 700, the same as the browser window.
- The verifier does not see screenshot pixels, only the accessibility tree and attributes. Screenshots are for the human.
- Which tools each conversation gets is enforced by the service only. The tool server checks addresses, paths and read length, but would serve any catalog tool a misbehaving service asked for, including the code search, which returns many matching lines at once.
- Nothing stops the coding agent from registering a rigged app. The report only records what it ran against.
- Properties are written by hand; there is no step that drafts them from the owner's intent.
- Rough edges: `decide`'s append assumes `properties:` is the last block in the feature file; a feature file missing `feature:` or a `statement` gives a raw error; the report gives ready-to-copy commands for a question but says nothing about what to do after a property fails; the data is not reset after the last case, so the app is left as that case wrote it.
