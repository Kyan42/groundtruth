# Data tools: giving a case the starting state it needs

Some properties can only be tested on data the app does not have: a listing above a certain price, an order from last month, a second user's cart. The verifier never writes to an app's data on its own. Instead the project declares small **data tools**, the planner says which ones a case needs before it starts, and plain code runs them. This file is the agreed design; the code follows it.

Three rules, agreed with the owner on 2026-11-10:

1. **Setup happens before a case, never during it.** The planner gives each case a starting state. Plain code sets it up. The conversation that carries out the case gets no data tool, and the tool server itself refuses a data tool once the case has done anything else.
2. **Data tools are a last resort.** If a person could reach the state through the pages in a few steps, the case does that. Each use of a data tool carries one sentence saying why the pages could not do it.
3. **A missing ability is a request, not a stall.** If no declared tool can make the state, the case is not run: it comes back "could not run" with a request naming the tool that would be needed. It never counts as held or as outside.

## Declaring tools

`<repo>/.groundtruth/data.yml`, written by the project (usually by its coding agent):

```yaml
tools:
  - name: make_listing
    does: Creates one listing in the shop and returns its id and the address of its page.
    takes:
      title: {type: string}
      price_cents: {type: integer, does: The price in cents.}
      sold_out: {type: boolean, optional: true}
    run: node scripts/groundtruth/make-listing.mjs
```

- `name`: 1 to 40 letters, digits or underscores, starting with a letter. Unique in the file.
- `does`: one plain sentence, at most 400 characters (a tool's and each input's). The planner reads all of it.
  - Inside `{...}` a comma ends the value, so a `does` with a comma in it goes on its own line, in double quotes. A sentence cut short this way is reported as an unknown key.
  - Keep examples in a `does` ordinary. The planner reads them, and an example that happens to sit on an edge of the app's behaviour would point it there.
- `takes`: the inputs, each with `type` (`string`, `integer`, `number` or `boolean`), an optional `optional: true`, and an optional `does`. No other input is accepted.
- `run`: a shell command, run in the repo folder like the reset command. It is never shown to the verifier service.
- `works_on` (optional): the name of one service from `services.yml` (below), matched ignoring case and surrounding spaces, never by a partial match. Absent means the tool works on the app's own data.
- `changes` (optional): at most 120 characters saying what the tool changes. The single word `nothing` (any case) means the tool only reads. Absent means what it always meant: the tool may change the app's data.
- At most 20 tools. A file that breaks these rules is an error at the start of `verify`, in plain words. So is a tool whose `works_on` is not listed in `services.yml` (or there is no such file), one that works on a service marked `read` without saying `changes: nothing`, and one that works on a service marked `stand-in only` or `off`.

## Outside services

A real app keeps part of its state in services it calls: menus from a menu service it does not control, a feedback form that posts to a real chat channel. The project lists them once, in `<repo>/.groundtruth/services.yml` (optional), and says what may be done with each:

```yaml
services:
  - name: menu service
    what: Where menus, stations and events come from. Calling it only reads.
    allowed: read
  - name: team feedback channel
    what: The feedback form posts to the team's chat. Calling it sends a real message.
    allowed: stand-in only
```

- `name`: 1 to 40 characters, unique ignoring case and surrounding spaces. `what`: one sentence, at most 200 characters. `allowed`: exactly `read`, `stand-in only` or `off` (a bare `off` is read as off). At most 12 services. A file that breaks these rules is an error in plain words.
- Nothing may change a real outside service. A data tool may read a service marked `read`, and must then say `changes: nothing`. No tool may call a service marked `stand-in only` or `off`.
- The services are sent to the verifier service with the run (`outside_services`), and each declared tool is described with `reads_only` and `works_on` (the service's name as the file spells it, or null). Without the file nothing about services is sent, said or checked.

## Tools that only read

A tool is read-only exactly when it says `changes: nothing`, whatever it works on. With a reset command registered nothing differs. Without one, read-only tools stay usable and only the others are blocked: the planner is told only the read-only ones, with "Only reads." after each, and is told the others cannot be used. In `set_up`, a read-only tool does not need the unit's own reset to have run; it still has to come before any other call and still counts toward the six. A tool that is not read-only keeps every rule above.

## Running a tool (the tool server)

- The inputs arrive as one JSON object on the command's standard input. Nothing from the service is ever placed in a command line.
- The environment gets `GROUNDTRUTH_APP_URL` (the registered address).
- The command must exit 0 within 60 seconds and print one JSON object on standard output, at most 2,000 characters: what the case needs to know about what was made (ids, addresses). Anything else is an error, reported with the last 300 characters of the command's output.

Two catalog tools:

- `data_tools()`: the declared tools, without their commands: `[{"name", "does", "takes"}]`. Empty when the file does not exist.
- `set_up(tool, input)`: runs one declared tool. Refused when:
  - no repo is registered, or the tool is not declared;
  - an input is missing, unknown, or of the wrong type; a string is over 500 characters;
  - no reset command is registered (a case that changes data must start from clean data, or one case's setup leaks into the next), unless the tool only reads;
  - the unit is a discovery unit;
  - the unit was opened without running the reset (a retry or a reopened case must reset again first), unless the tool only reads;
  - the unit has already made any call other than `reset_data` or `set_up` (rule 1);
  - the unit has already made 6 `set_up` calls.
  Every call, refused or not, leaves a receipt.

## Planning (the verifier service)

The planner is told the declared tools. In either planning path, a case may carry one of:

- `starts_from`: up to 6 steps, each `{"tool", "input", "because"}`. `because` is the sentence rule 2 asks for. Plain code checks each step against the declared tools before the plan is accepted.
- `needs`: `{"state": "<the starting state in plain words>", "tool": {"name", "does", "takes"}}` when no declared tool can make it. This is the request of rule 3. When the project lists outside services, the tool may also carry `works_on` (exactly one of the service names the planner was given, or `the app's own data`) and `changes` (true or false); any other `works_on` sends the plan back. Absent means the app's own data and `changes` true.

A case with neither starts from reset data only, as today.

## Carrying out a case (the harness)

For every attempt of a case, in this order: reset, then each `starts_from` step through `set_up`, then the case conversation.

- A step that fails makes the case "could not run", with the step's error. The conversation is not started.
- A case with `needs` is not started either: "could not run", with the request.
- A case whose `needs` would change a service marked `read`, or would use a service marked `stand-in only` or `off`, is "could not run" too, decided by plain code before anything else, with a plain reason and **no** request: nobody is asked to write a tool that would touch a real outside service. A `needs` that would only read a `read` service, or works on the app's own data, is the usual request, with `works_on` and `changes` kept in it.
- The result's `starts_from` lists the steps asked for on this machine, each with what it returned or how it failed (a step the tool server refused counts as failed). It is built by the tool server from its own calls, never from what the service reported; only each step's `because` is the planner's.
- The conversation is told what was set up and what each step returned, as facts: "Before you began, this was set up: ...". It gets no data tool.
- A second attempt repeats the reset and the setup.

## In the results

Each case's result gains:

- `starts_from`: the steps as carried out, each with `tool`, `input`, `because` and `returned` (or `error`).
- `data_request`: the `needs` object, when the case was not run for that reason.

A property's verdict follows the usual table: a case that could not run makes it "can't tell".

## What this does not solve

- A tool that writes rows directly can build a state the app could never reach; a break found there may not be a real bug. Tools should go through the app's own code. The recorded starting state is there so a person can judge.
- The tools are written by the same agent whose work is being checked. Small tools for one kind of thing are harder to rig than a preset named for one property.
