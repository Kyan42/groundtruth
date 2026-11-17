# The run document

One finished run, as the results store keeps it and the dashboard reads it. `toolserver/rundoc.py` builds it on the user's machine from a run folder; nothing else writes one. Real examples are in `dashboard/samples/`.

It never carries the repo's path, the process id, the process's command line, the machine's user name or any absolute path. `tests/test_rundoc.py` checks this.

## Top level

| Field | Meaning |
|---|---|
| `schema` | `1`. |
| `project`, `feature`, `goal` | The app, the feature file's name, and its goal sentence. |
| `run`, `ran_at` | The verifier's run id (`20261110-195106`) and the same moment as ISO local time. |
| `ran_against` | `url`, `commit`, `uncommitted_changes`, `platform`, `process_started`, `registered_at`, three yes/no facts: `could_measure`, `could_read_code`, `had_reset`, and `viewport`. |
| `ran_against.viewport` | `{"width", "height"}`: the size of the verifier's browser window in this run, which is also the size of every film and whole-page screenshot. Set with `register --viewport` or `verify --viewport`; `{"width": 900, "height": 700}` when neither was given. Always present in documents built now; runs made before it could be set were 900 by 700 and show that. (Documents built before this field lack it.) |
| `verifier` | `version`, `model`. |
| `error`, `aborted` | Set when the run did not finish. |
| `totals` | `properties` (count per verdict), `cases` (count per outcome), `seconds`, `cost_usd`. |
| `properties` | Every property in the feature file, in file order, run or not. |
| `not_checked` | List of plain sentences (at most 12, each at most 200 characters): what the owner was told no claim covers. Copied from the feature file's top-level `not_checked:` list when the run started. `[]` when there is none. |
| `walkthrough` | `null`, or one object shaped exactly like a case (below) with `id` `"tour"`: one film of the feature being used start to finish on data that looks like ordinary use. Its files live under `walkthrough/tour/`. It never changes any property's verdict. Its `setting` is the planner's; its `variation` is `""`; each of its compare steps has `claim`, the id of the property that stop is about. When a walkthrough was asked for (`verify --walkthrough` or `--walkthrough-only`) but could not be planned or started, it is still an object, with `outcome` `cant_run` and the reason in `observed`. Its seconds and cost are in `totals`. |

## A property

| Field | Meaning |
|---|---|
| `id`, `statement`, `example` | As the owner wrote them. |
| `title` | The owner's few-word name for the property, for scanning; `""` when there is none (then the page shows the statement). The verifier never sees it. |
| `your_words` | What the owner said when first describing the property, word for word, so they recognise it later; `""` when there is none. The verifier never sees it. |
| `checked_against` | Where the right answer comes from, as the owner wrote it; `""` when the property has none. |
| `approval` | Whether the property, as it was sent in this run, is what the owner approved with `groundtruth.py approve`: `approved` (every part the same, whitespace aside), `changed` (the file was edited after approval), `new` (added after approval), or `null` when the feature had no approval record when the run started, or the property was not in the run. Taken from the comparison the run saved when it started, never from today's file. |
| `approval_changed` | For `changed`, the parts that differ, in this order: `statement`, `title`, `your_words`, `checked_against`, `variations`. `[]` otherwise. |
| `group` | `new` or `regression`. Always `new` today. |
| `verdict` | `holds`, `fails`, `needs_decision`, `cant_tell`, `planned` (plan-only run) or `not_run` (in the file, not in this run). |
| `kind` | `behaviour`, `measurement`, `code`, or null when not run. |
| `searched` | True when the ways in came from the code search; false when a model read the code. |
| `from_claim` | `true` when the cases were planned from the owner's approved variations without reading the code. Absent or `false` otherwise. (For runs made before this field: treat a property as from a claim when `searched` is false and `checked_against` is not empty.) |
| `variations` | The approved variations as they were sent, in the owner's order: `[{"id", "what"}]`. `[]` when the property has none. A case's `id` equals its variation's `id`. |
| `read_as` | `applies_to`, `action`, `compares`: how the verifier read the sentence. |
| `how_found`, `not_covered`, `no_plan_because`, `search_notes` | Plain text about how the ways in were found and what was left. |
| `ways_in` | Each place the action can be done: `id`, `door` (`browser`, `api`, `code`), `status` (`found`, `flagged`, `to check`), `where` (plain words), `page`, `route`, `file`. A case points at one by `way_in`. |
| `left_out` | Files the search found but no page shows: `file`, `why`, `because`. |
| `counts` | Cases per outcome. |
| `seconds`, `cost_usd` | For the whole property. |
| `cases` | In the order they ran. |

## A case

| Field | Meaning |
|---|---|
| `id`, `way_in`, `door` | `door` is `browser` (through the pages), `api` (a direct request) or `code` (reading source). |
| `what`, `stands_for` | The steps as planned, and which situation the case represents. |
| `variation` | The owner's approved sentence for the variation this case tests (the `what` of the variation with the same id). `""` when the case is not a variation. |
| `setting` | One plain sentence, written by the planner, saying what the case starts from ("Two projects: shop with 2 tests, blog with 3."). `""` when there is none. |
| `outcome` | `holds`, `breaks`, `declined` (the app refused; see `question`), `outside`, `nothing_there`, `cant_run`, `planned`. |
| `observed` | What the case saw, in its own words. |
| `question`, `draft_if_intended` | Only on a `declined` case. |
| `seconds`, `attempts` | |
| `clips` | Films, one per attempt: `file` (path under the run, e.g. `p3/c1/clip-1.webm`) and `started` (epoch seconds: the moment of the film's first frame on the receipts' clock, found by the tool server from a mark drawn at a known moment just before the film closed; the time the page was made when the mark could not be found). A step's `at` is its receipt time minus this. Empty for `api` and `code` cases and for runs made before filming. Films are the size in `ran_against.viewport` (900 by 700 unless set). |
| `shots` | Screenshots: `id`, `file`, `label`, `at`, `cited` (the case cited it as evidence). |
| `steps` | What was done, in order: `words` (plain words), `kind` (`action`, `look`, `compare`, `say`, `request`, `reading`, `code`), `ok`, `clip` (index into `clips`, or null) and `at` (seconds into that film, or null when no film was running, e.g. a measurement taken before the case). A failed step has `problem`. A request step has `status`. |
| `requests` | Direct requests: `method`, `path`, `body`, `status`, `response`, `at`. |
| `readings` | Measurements, the before-measurement first: `what`, `text`, `at`. |
| `media` | What each video or audio player had loaded, each time it was read: `text`, `at`. |
| `excerpts` | Code a code case rests on: `path`, `start`, `end`, `why`, `lines`. At most 15 lines each. |
| `checks`, `notes` | Every-file checks a code case cited, and notes the tool server added. |
| `starts_from` | What plain code set up before the case began (see `DATATOOLS.md`): each step has `tool`, `input`, `because`, and `returned` or `error`. Empty when the case started from the reset data only. |
| `data_request` | Set when the case was not run because no declared data tool could make its starting state: `state` (plain words) and `tool` (`name`, `does`, `takes`), the tool being asked for. |

### Pointing, pictures and comparisons

Every field below is optional: runs made before them lack them, and every reader keeps working without them.

- Pointing at something without clicking it is an `action` step whose words start with `point at`, for example `point at link "Cart total adds up"`.
- Looking at a picture of the page or of one element is a `look` step (`look at the page as a picture: <label>`); its picture is listed in `shots` with that label, like a screenshot.
- A comparison is a step of kind `compare`, recorded by the verifier at the moment it checked something:

```json
{"clip": 0, "at": 6.4, "kind": "compare", "ok": true,
 "words": "Tests listed for the shop project",
 "what": "Tests listed for the shop project",
 "expected": "3 tests, from the data put in: Cart total adds up, Login works, Search finds items",
 "found": "3 rows: Cart total adds up, Login works, Search finds items",
 "matches": true,
 "element": "Cart total adds up Holds 2 hours ago a1b2c3d Login works ...",
 "claim": null}
```

  - `what`, `expected`, `found`: the verifier's own plain words, each at most 200 characters. `words` is the same as `what`.
  - `matches`: `true` or `false`, as the verifier recorded it. `ok` is always `true` on a compare step (the comparison was recorded); whether it agreed is `matches`.
  - `element`: the text of the page element the verifier pointed at for this comparison, read by plain code at that moment (at most 300 characters), or `null` when it pointed at none. In the film, that element is outlined (green when it matched, red when not) for about a second and a half starting at `at`.
  - `at`: from the moment the outline was drawn (or the comparison was made, when there is no outline), not from when the call finished.
  - `claim`: only in a walkthrough: the id of the property this stop is about. `null` elsewhere.
  - A failed attempt to record a comparison (for example a stale element reference) is not a step.
  - `situation`: `true` on the step a case planned from a claim records first: whether its starting situation is the one its variation describes. It is shaped exactly like a comparison (`what` and `words` are both `The starting situation`, `element` and `claim` are `null`, `say` is `""`), so a page that shows comparisons shows it too. Absent on every other step. When its `matches` is false the case was not run (`cant_run`).
  - `say`: in a walkthrough, what the verifier said out loud with that comparison ("It says yesterday. That works."), at most 110 characters; `""` when it said nothing (every comparison outside a walkthrough, and every run made before spoken lines). Whether the comparison agreed is still `matches` and only `matches`: a reader must never take a tick or a cross from the words of `say`.

### Spoken lines (walkthrough only)

The verifier also records what it would say out loud, as short spoken sentences. In a walkthrough each stop has one line said before it is checked and one said with its comparison (the compare step's `say`).

- A step of kind `say`: said before a stop is checked: what that part of the app should do.

```json
{"clip": 0, "at": 14.2, "kind": "say", "ok": true,
 "words": "Each test should show when it last ran. Cart total ran yesterday.",
 "text": "Each test should show when it last ran. Cart total ran yesterday.",
 "element": null, "claim": "when-last-tested"}
```

  - `text`: at most 110 characters, the verifier's own spoken words. `words` is the same text.
  - `element`: the text of the page element it pointed at while saying it, or `null`. In the film that element is outlined in blue for about a second and a half starting at `at`.
  - `claim`: the id of the property this stop is about.
  - A failed attempt to say something is not a step.
- A stop of a walkthrough is every step from just after the previous stop's last comparison (or the film's start) up to and including its own last comparison. Its claim is the `claim` of its comparison.

## Learning from real use

All optional; older runs lack them.

| Field | Meaning |
|---|---|
| property `source` | Where the claim came from, as written in the claims file when the run started: `{"kind": "issue" \| "pull request" \| "interview" \| "other", "ref": "<owner/repo#123 or a URL, or empty>", "quote": "<the sentence it was taken from, or empty>"}`, or `null`. |
| `context` | What the PR and its issues said when the run started, fetched on the user's machine with their own GitHub login (`gh`), or `null` when there was none, it could not be read within ten seconds, or `verify --no-context` was used: `{"pull_request": {"number", "title", "body", "url", "state"} \| null, "issues": [{"number", "title", "body", "url"}], "fetched_at": "<ISO time>"}`. Bodies are cut at 6,000 characters; at most 5 issues (the PR's closing references, then any `Closes`/`Fixes`/`Resolves #N` in its body). Never sent to the verifier service. |
| `draft_changes` | What changed between the claims as first drafted (saved the first time `claims` showed the feature, before any approval) and as approved, from the approval record as it was when the run started: `[{"id": "<claim id>", "change": "edited" \| "added" \| "removed", "part": "<title \| statement \| your_words \| checked_against \| variations \| source, for edited; null otherwise>", "before": "<text>", "after": "<text>"}]`. Variations are compared as their sentences, one per line. `[]` when nothing changed, `null` when no draft was kept. |
| `setup` | `{"first_command_at": "<ISO>", "commands_before_first_run": <n>, "failed_commands": [{"command": "<name>", "error": "<first line, at most 200 characters>"}]}`, from `<app>/.groundtruth/log.jsonl` (one line per `groundtruth.py` command that named the app folder: never its arguments or output). `commands_before_first_run` and `failed_commands` count the commands before the app's first `verify`. `null` when there is no log. |

Feedback is not in the document. The results store keeps it (see `store/CONTRACT.md`).

Files (films and screenshots) travel beside the document, under the same paths the document names.
