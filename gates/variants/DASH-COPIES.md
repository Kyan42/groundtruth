# The dashboard copies for the planted-bug check

Four copies of the dashboard page under `gates/work/` (not in git). The `claimsclean` and `claimsbugs` streams of
`gates/props_gate.py` use them. All four are `dashboard/` as committed at the pinned commit `1564323` (`PAGE_COMMIT` in the build script), without `.groundtruth/runs`,
`.groundtruth/test-store`, `.groundtruth/env.json`, `design/` and `samples/`. Each has its own test-store folder with
the `MADE-UP-DATA-ONLY` marker, and a `.groundtruth/features/claims.yml` that is byte-identical to
`dashboard/.groundtruth/features/claims.yml`.

Each patch changes `js/run.js` only. None of them adds a comment that names the bug.

| Copy | Patch | Target claim | What is wrong on the page | Able to show (one `make_run` input) |
|---|---|---|---|---|
| `dash-clean` | none | all three | nothing | n/a |
| `dash-regression-short` | `dash-regression-short.patch` | `tabs` | `props()` rebuilds the list as New tests, then rerun tests, with an off-by-one loop that skips the run's last test. Because `make_run` always puts the rerun tests last, the last rerun test is missing everywhere: from the Regression count, from its cards, from the default selection, from the `#tN` addresses and from the "This run" box. The count and the cards agree with each other. With exactly one rerun test, Regression shows 0 and "No earlier properties were re-checked in this run." New is unchanged. | `rerun_tests` >= 1 |
| `dash-single-holds` | `dash-single-holds.patch` | `result` | A test's sidebar card shows "Holds" (green, and "Holds" in its hidden description) when the recorded verdict is fails and no variation after the first broke. `make_run`'s fails test breaks its second variation, or its only one, so this is exactly a one-variation test that fails. The card's marks (the ✗ square), the main area's verdict and everything else are unchanged. Needs-your-decision and can't-tell are not affected. | `variations_each` == 1 and `verdicts` gives `fails` to a test that exists |
| `dash-other-test-film` | `dash-other-test-film.patch` | `film` | When the run is loaded, an index of films by variation id is built in run order, and the first test that has variations wins each id. The main player of every later test's variation loads that first test's film with the same id. That film is real and plays normally, but its `src` names `t1/...` and its length is the first test's. The first test's own variations are right. **The thumbnails in the tile strip stay right**, so the tile's length and the main player's length disagree. | at least two tests that have variations (see below) and `films` not false |

## The pass rule (decided before any paid run)

- Clean copy, all three claims: no variation broke on any claim. `tabs` and `result` must come out "holds". The verdict of `film` is reported but not judged, because its sentence says "with the pointer visible" and the verifier is never shown a film's picture, so that part cannot be checked.
- Each broken copy, its target claim only: at least one variation broke, on a case whose recorded starting data makes the bug able to show; and no variation broke on a case where the bug could not show.
- "Able to show" is read from each case's `result.starts_from` (the `make_run` steps that ran, without error), never from the plan.
- A question, "outside the wording" or "could not run" is never counted as a catch.

The able-to-show tests are `able_regression_short`, `able_single_holds` and `able_other_test_film` in
`gates/props_lib.py`. The hand check below uses the same functions.

- A case counts as able to show when any of its `make_run` steps in `result.starts_from` ran without error and has
  `returned`, and that step's input passes the test.
- **One change from the brief, for the film copy.** The brief's rule is "at least two tests in total". The code
  needs two tests *with variations*, because a `not_run` test has none. Take `{"new_tests": 1, "rerun_tests": 1,
  "verdicts": "not_run"}`: the only test with variations is the second one, so it is the first in the index, and the
  bug cannot show. The test therefore counts the tests whose verdict word is not `not_run`.
- For the film copy, "able to show" is about the starting data, not about what the variation then looks at. For
  example, `first-shown` and `back-again` on a two-test run count as able. They may still look only at test 1, which
  is right, so a "holds" there is not a miss. The catch is expected from `another-test`, or from any case that opens a
  later test.

## How the regression copy is kept consistent with itself

The patch also makes the sidebar's "This run" box count from the same shortened list. "Properties: H of N hold" and
"Cases: X of Y held" therefore agree with the tabs and with the marks on the cards. Without this, the box used the run
document's totals and could read "4 of 3 hold". These tells remain, and are left as they are:

- The list of runs (`index.html`, `js/runs.js`) still shows each run's totals from the document: properties holding
  out of the total, and the case count. Comparing that page with the run page would show the gap.
- With one rerun test and no New tests, the main area says "This run has no properties." This agrees with both tabs
  showing 0 and with the box's "0 of 0".

## Rebuild

```bash
.venv/bin/python gates/variants/build_dash_copies.py
```

The script uses `git archive <PAGE_COMMIT> dashboard`, so all four copies come from one commit, and the page under test does not change when the real results page does. It deletes and rebuilds only
these four folders, applies each patch with `patch -p1` (and stops if a patch does not apply cleanly), and then runs
the copy's own `python3 .groundtruth/tools/init_store.py`. It stops if a copy's `claims.yml` differs from the
dashboard's. Rebuilding empties each copy's test store, including its database and token. The store writes a new
token the next time it starts.

**Rebuilding also deletes each copy's `.groundtruth/runs/`, including the paid runs the streams wrote there.** The
results files and `--check-only` point at those runs, so move them somewhere else before rebuilding.

## Checking the copies by hand (no model)

```bash
.venv/bin/python gates/variants/check_dash_copies.py
```

For each copy, the script:

1. starts a store in this process on a free port, serving the copy's page folder and the copy's test store;
2. runs the copy's `make_run` through `toolserver.datatools.run_tool`, the tool server's own code (from the copy's
   folder, JSON on standard input, `GROUNDTRUTH_APP_URL` set), for six shapes S1–S6;
3. opens each run page in headless Chromium and compares the page with the tool's record:
   - the New and Regression counts and the cards (or the empty message);
   - the word on every card;
   - the "Properties" and "Cases" lines of the "This run" box;
   - for every variation, opened by `#tN/cJ`, the main player's `src` and the film's length.

Lines whose expected screen is the planted wrong one are marked `[the bug, visible]`. At the end, every store is
stopped and every copy's test store is reset.

On 2026-11-12 it passed 210/210:

- **dash-clean:** every shape matched the record.
- **dash-regression-short:**
  - S1 (2 New, 2 rerun) showed Regression 1 with only T3, and the box said "2 of 3 hold" and "2 of 3 held".
  - S3 (1 + 1), S5 (0 + 1) and S6 (1 + 2, T1 not run) showed Regression 0 or 1, with the empty message where 0.
  - S2 and S4 (no reruns) were right.
- **dash-single-holds:**
  - S1 (one variation each, `fails,holds,holds,fails`) showed "Holds" on the T1 and T4 cards.
  - S2 (three variations, fails), S4 (one variation, needs decision and can't tell) and S5 were right.
- **dash-other-test-film:**
  - In S1, T2, T3 and T4 C1 played `t1/c1/film-a.webm`, 1.96 s and "0:01 / 0:01". The record says film-b 2.48 s,
    film-c 2.96 s and film-d 3.48 s.
  - In S4, T2 C1 played `t1/c1/film-a.webm`.
  - In S6, T1 is not run, so T3 C1 and C2 played T2's films (`t2/c1/film-b.webm`, `t2/c2/film-c.webm`).
  - S2 (one test), S3 (no films) and S5 (one test) were right.

## The streams

```bash
.venv/bin/python gates/props_gate.py claimsclean    # service 8798, store 8921: verify dash-clean claims
.venv/bin/python gates/props_gate.py claimsbugs     # service 8799, stores 8922-8924: each broken copy, --only its claim
```

Each copy is served by a store that the stream starts in its own process. It is registered with
`--reset "python3 .groundtruth/tools/reset_store.py" --no-pid` and verified through the stream's own verifier
service. Its test store is reset afterwards. Results go to `gates/results/props_claimsclean.json` and
`props_claimsbugs.json`.
