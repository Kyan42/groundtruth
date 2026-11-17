# Data tools

A data tool is a small command in the project that the verifier can ask for before a variation, to put the app in the situation the variation needs or to read the right answer from somewhere other than the page. You write them. The verifier never sees their code, only what each one says it does and what it prints.

Write one when a variation needs data that ordinary use of the app cannot produce quickly, or when a run's summary says a variation could not run and describes the tool it needed.

## Declaring a tool

Tools are listed in `.groundtruth/data.yml`:

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
    run: node scripts/groundtruth/make-listing.mjs
```

- **One tool makes or reads one kind of thing**, through the app's own code where it can. A tool that writes rows directly can build a state the app could never reach.
- **Inputs** arrive as one JSON object on standard input. Types are `string`, `integer`, `number` or `boolean`. Add `optional: true` to an input that may be left out.
- **Output** is one JSON object, at most 2,000 characters, printed within 60 seconds, with exit code 0. Say what was made or found: ids, page addresses, and the facts a checker would compare the page with.
- **What the tool prints is the right answer the verifier checks against**, so it must be true, and it must not come from the page being checked.
- **`does`** is one plain sentence of at most 400 characters. Keep example values ordinary.
- A tool only ever writes to test data.

## Tools that only read

A tool that changes nothing says so, and can then be used even when the project has no reset command:

```yaml
    changes: nothing
```

## Outside services

If the app calls services it does not own (a payment provider, a menu feed, a chat channel), list them in `.groundtruth/services.yml` so the verifier knows what it may touch:

```yaml
services:
  - name: menu service
    what: Where menus and stations come from. Calling it only reads.
    allowed: read            # read | stand-in only | off
```

A tool that works on one names it with `works_on: menu service`. A tool on a service marked `read` must say `changes: nothing`. Ask the user before listing a service as anything more open than `read`.

## Reset

A reset command puts the test data back to a known clean state. It runs before every variation, so it must be quick and safe to repeat. Give it to `connect --reset "<command>"`. Without one, only tools that change nothing can be used.

## After adding or changing a tool

Run `connect` again if the reset changed, then recheck the claim that needed the tool with `verify --only <claim id>`.
