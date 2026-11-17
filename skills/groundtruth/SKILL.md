---
name: groundtruth
description: Checks a change against what the user asked for, with a separate verifier that uses the running app in a real browser and films it. Use before building a feature or fix the user has described (to turn their words into claims they approve), and again when the work is finished (to verify the claims and report only what broke or needs them). Also use when the user says "groundtruth", "claims", "verify this", asks whether a change really works, or reacts to a verification result.
---

# Groundtruth

Groundtruth holds a change to what the user asked for. Their request becomes a few **claims** (plain sentences about what a person sees in the app), each with **variations** (the situations it must hold in). The user approves the claims. A separate **verifier** then tries every variation in the running app, on film, and plain code decides each result.

You run the commands and build the app. **You never judge a claim yourself**, and you never check one by using the app in place of the verifier. Only the verifier's result counts.

## Running the commands

Run every command from the project's folder, written exactly as `{{GROUNDTRUTH}} <command> ...` with nothing before it: no `cd`, no `&&`, no pipes. The commands print what happened and what to do next. Read that output and follow it; it is more current than this file.

The app's folder is the one that holds `.groundtruth/`. When that is a subfolder of the project and not the folder you run from, give it as the first word after the command: `{{GROUNDTRUTH}} claims <app folder> [feature]`.

```bash
{{GROUNDTRUTH}} draft --said <file> [--feature <name>]   # draft claims from the user's words
{{GROUNDTRUTH}} draft --pr <number>                      # or from a pull request and its issue
{{GROUNDTRUTH}} claims [feature]                         # the claims in brief: title, sentence, variations
{{GROUNDTRUTH}} claims [feature] --full                  # plus ids, their words, sources, what each is checked against, what is not checked
{{GROUNDTRUTH}} approve [feature]                        # record the user's approval
{{GROUNDTRUTH}} connect <address> [--reset "<command>"] [--window 1280x800]   # the app you started
{{GROUNDTRUTH}} verify [feature] --walkthrough           # check every claim, plus one film of the feature in use
{{GROUNDTRUTH}} verify [feature] --only <claim id>       # recheck one claim (ids are in claims --full)
{{GROUNDTRUTH}} answer <claim> <variation> intended|bug  # the user's answer to a question a run asked
{{GROUNDTRUTH}} feedback <claim> [variation] --kind not_what_i_meant|wrong_result|right [--note "..."]
{{GROUNDTRUTH}} results [feature]                        # each claim's latest result
```

If a command says it cannot reach the verifier, run `{{GROUNDTRUTH}} up` once and try again.

## Before you build

Do this when the user has described something to build or fix, before writing code.

1. **Save their words.** Write what the user said about this change into a file under `.groundtruth/drafts/`, copied word for word from their messages. Leave out your own replies and anything about other work. If they answered a question of yours, include the question and their answer. If the request is a pull request or an issue, skip the file and use `--pr` or `--issue`.
2. **Draft.** Run `draft`. The verifier writes the claims file (`.groundtruth/features/<feature>.yml`) and prints it. You do not write claims yourself.
3. **Show the user.** Put the printed claims in your message as they are, as plain text and not in a code block. The user sees only what you write. Never retype, shorten or tidy them. The printout is brief on purpose: do not add to it. Run `claims --full` only when the user asks for more, and then show the part they asked for.
4. **Questions come first.** If the printout has questions, ask the user, one at a time, in their words. Put each answer into the claims file (fix the claim it concerns, delete the question), then run `claims` and show the result again.
5. **Approve on their yes.** When the user says the claims are right, run `approve`. If they ask for a change, edit the file, show `claims` again, and wait. Never run `approve` without a yes from the user in this conversation about the claims as they now stand.

## Build

Build so the claims are true. The app, how it starts, and its test data are yours, not Groundtruth's.

- Do not edit anything under `.groundtruth/features/` or `.groundtruth/approved/` while building. If a claim turns out to be wrong or impossible, stop and tell the user.
- The verifier needs the app running at a local address with data that is safe to change, never data the user cares about.
- If the variations need particular data (a signed-in user, an empty list, a failed order), the verifier asks for it through **data tools** that you write. Read [references/data-tools.md](references/data-tools.md) when you need one, or when a run says it could not run for lack of one.

## When the work is finished

1. Start the app and run `connect <address>`. Add `--reset "<command>"` if the project has a command that puts test data back to a clean state.
2. Tell the user how many variations will run (`claims` shows them). Each takes about a minute.
3. Run `verify --walkthrough` with the longest timeout your shell allows, or in the background. Leave `--walkthrough` off when rechecking single claims.
4. Act on the summary it prints:

| The summary says | Who acts | What to do |
|---|---|---|
| A variation broke | you | Fix the app, then `verify --only <claim id>` |
| Could not run, and it describes a data tool | you | Write that tool, then recheck the claim |
| Could not run for another reason | you | Fix the set-up (app not answering, reset failed), then recheck |
| A question ("is this intended?") | the user | Ask them, record it with `answer`, then recheck |
| Changed since approval, or not approved | the user | Show `claims`, and approve only on their yes |

Two fix rounds at most for one claim. After that, stop and show the user what still fails and why.

## Report

Keep it short. The user wants surprises, not a tour.

- One line first: `4 of 4 claims hold`, or `1 of 4 claims fails`.
- For each claim that does not hold: its title, the variation, and the reason as the summary words it.
- What was not verified: anything listed as not checked, any variation that could not run, any open question.
- The `Feature page:` address from the summary, where the films are.

Say a claim is verified only when the verifier's result for it is "holds".

## When the user reacts to a result

If the user says a claim is not what they meant, a result is wrong, or a result is right, record it with `feedback` and the matching `--kind`, with their words as the `--note`. If the claim itself needs to change, edit the file, show `claims`, and approve again on their yes.

## Never

- Reword an approved claim, or change one so that it passes.
- Approve for the user, or answer for them what they intended.
- Say something works because you looked at it yourself.
