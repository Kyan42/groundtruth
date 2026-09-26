import type { Extraction } from "./extract.js";

// The PR comment is Groundtruth's record for a PR: the developer reviews and edits it directly.
// Hidden HTML comments carry what we need to read it back: a marker identifying the comment,
// a marker per checkbox line, and the extraction itself (base64 JSON, so it can't break the HTML comment).

export const COMMENT_MARKER = "<!-- groundtruth -->";
const STATE_PREFIX = "<!-- groundtruth-state:";

export type CommentState = {
  version: 1;
  pr: string;       // owner/repo#number
  headSha: string;  // the commit the claims were read at
  extraction: Extraction;
  // Tests earlier PRs added to the repo (.groundtruth/tests on the base branch), offered as regression checks.
  regressions?: RegressionRow[];
  approval?: Approval;
  // After a run whose compiled tests replayed cleanly: the tests offered for adding to this PR.
  tests?: { run: string; files: { file: string; title: string }[]; added?: { sha: string; by: string } };
};

export type RegressionRow = { id: string; file: string; title: string; summary: string; from: string };

// One claim the developer approved for testing: a checked claim or a checked assumption,
// with their wording if they edited it. `kind` always comes from the original extraction.
export type ApprovedClaim = {
  id: string;       // c1, c2, ... for claims; s1, s2, ... for assumptions
  when: string;
  then: { kind: string; what: string };
  edited: boolean;
};

export type Approval = {
  by: string;
  at: string;
  testedSha: string; // the PR head when approved, which testing will run against
  baseSha?: string;  // the base branch when approved, which regression tests are read from
  claims: ApprovedClaim[];
  regressions?: RegressionRow[];   // the regression rows the developer kept
};

const HEADER = "### Groundtruth · what I'll verify";

export function renderPlaceholder(): string {
  return [COMMENT_MARKER, HEADER, "", "Reading this PR… (usually under a minute)"].join("\n");
}

export function renderError(message: string): string {
  return [COMMENT_MARKER, HEADER, "", `⚠️ Couldn't read this PR: ${message}`, "",
    "Close and reopen the PR to try again."].join("\n");
}

const line = (when: string, what: string) => `${when.replace(/\.$/, "")} → ${what.replace(/\.$/, "")}`;
const sub = (text: string) => `<sub>${text.replace(/\s+/g, " ").trim()}</sub>`;
const cell = (text: string) => text.replace(/\|/g, "\\|").replace(/\s+/g, " ").trim();
const clip = (text: string, max = 90) => (text.length > max ? `${text.slice(0, max - 1).trimEnd()}…` : text);

// Claims are a table (one row per claim: the result, a quote of where it came from, and the action);
// the developer rewords or deletes rows to change them. Assumptions are yes/no, so they stay checkboxes.
export function renderClaimsComment(state: CommentState): string {
  const { claims, assumptions, not_testable, regression_hints } = state.extraction;
  const out = [COMMENT_MARKER, HEADER, ""];

  if (claims.length === 0 && assumptions.length === 0) {
    out.push("Found no user-visible change to test in the PR description and commits.",
      "If this PR does change behavior, describe it in the PR description and reopen the PR.", "");
  } else {
    out.push("I read the PR description, linked issues and commits. This is what I'll check in a live browser. " +
      "**Nothing runs until you approve.**", "");
  }

  if (claims.length) {
    out.push("| ID | Claim | When |", "|:--|:--|:--|");
    claims.forEach((c, i) => {
      out.push(`| \`C${i + 1}\` | **${cell(c.then.what)}**<br><sub>from the description: "${cell(clip(c.source))}"</sub> ` +
        `| ${cell(c.when)} <!-- gt:c${i + 1} --> |`);
    });
    out.push("");
  }

  const regressions = state.regressions ?? [];
  if (regressions.length) {
    out.push("#### Regression checks",
      "Tests that earlier PRs added to this repo. I'll replay them on this PR to catch anything it breaks. " +
      "Delete a row if this PR changes that behavior on purpose.", "",
      "| ID | Test | Added by |", "|:--|:--|:--|");
    for (const r of regressions) {
      out.push(`| \`${r.id.toUpperCase()}\` | **${cell(r.title)}**<br><sub>${cell(clip(r.summary, 120))}</sub> | ${cell(r.from)} <!-- gt:${r.id} --> |`);
    }
    out.push("");
  }

  if (assumptions.length) {
    out.push("#### Please confirm", "Where the PR doesn't say, these are my guesses. Tick the ones you intend and I'll test them too.", "");
    assumptions.forEach((a, i) => {
      out.push(`- [${a.checked ? "x" : " "}] \`A${i + 1}\` ${line(a.when, a.then.what)} <!-- gt:s${i + 1} -->`, `  ${sub(a.reason)}`);
    });
    out.push("");
  }

  if (not_testable.length) {
    out.push(`<details><summary>Not tested (${not_testable.length})</summary>`, "",
      ...not_testable.map((n) => `- "${n.text}": ${n.why}`), "", "</details>", "");
  }
  if (regression_hints.length) {
    out.push(`<details><summary>Existing behavior to re-check later (${regression_hints.length})</summary>`, "",
      ...regression_hints.map((h) => `- ${h}`), "", "</details>", "");
  }

  if (claims.length || assumptions.length || regressions.length) {
    out.push("---", "- [ ] **Approve and run** <!-- gt:approve -->", "",
      "<sub>To change a claim, edit this comment: reword its row, or delete it. Then tick Approve.</sub>", "");
  }
  out.push(encodeState(state));
  return out.join("\n");
}

const encodeState = (state: CommentState) =>
  `${STATE_PREFIX}${Buffer.from(JSON.stringify(state)).toString("base64")} -->`;

// Reads the hidden state back out of a comment body (undefined if it has none yet, e.g. the placeholder).
export function readState(body: string): CommentState | undefined {
  const start = body.indexOf(STATE_PREFIX);
  if (start === -1) return undefined;
  const end = body.indexOf(" -->", start);
  return JSON.parse(Buffer.from(body.slice(start + STATE_PREFIX.length, end), "base64").toString("utf8"));
}

// ---------- reading the developer's review ----------

export type ReviewedComment = {
  approveChecked: boolean;
  addTestsChecked: boolean;
  lines: Map<string, { checked: boolean; text: string }>; // by marker id: c1, s2, r1, ...
};

// Finds each claim row and checkbox line by its hidden marker, so reordering or rewording doesn't lose
// them. A claim row that was deleted is simply absent (not approved); one struck through (~~) counts as
// removed too. Checkbox lines are also how older comments listed claims, so those still parse.
export function parseReview(body: string): ReviewedComment {
  const lines = new Map<string, { checked: boolean; text: string }>();
  let approveChecked = false;
  let addTestsChecked = false;
  for (const m of body.matchAll(/^- \[([ xX])\] (.*?)\s*<!-- gt:(c\d+|s\d+|approve|addtests) -->\s*$/gm)) {
    const checked = m[1] !== " ";
    if (m[3] === "approve") approveChecked = checked;
    else if (m[3] === "addtests") addTestsChecked = checked;
    else lines.set(m[3], { checked, text: plain(m[2].replace(/^\*\*\d+\.\*\*\s*/, "").replace(/^`[CA]\d+`\s*/, "")) });
  }
  for (const row of body.split("\n")) {
    const id = /^\s*\|.*<!-- gt:([cr]\d+) -->/.exec(row)?.[1];
    if (!id) continue;
    const cells = row.trim().replace(/^\|/, "").replace(/\|$/, "").split(/(?<!\\)\|/).map((c) => c.replace(/\\\|/g, "|"));
    const [, claimCell = "", whenCell = ""] = cells;
    const what = plain(claimCell.split(/<br\s*\/?>/i)[0]);
    const when = plain(whenCell.replace(/<!--.*?-->/g, ""));
    lines.set(id, { checked: !/~~/.test(claimCell), text: line(when, what) });
  }
  return { approveChecked, addTestsChecked, lines };
}

// Markdown emphasis and whitespace removed: what the developer's wording says.
const plain = (text: string) => text.replace(/\*\*|~~/g, "").replace(/\s+/g, " ").trim();

// The approved claims: checked lines, using the developer's wording where it differs from ours.
// A line without "→" is taken as the result, with no setup.
export function approvedClaims(state: CommentState, review: ReviewedComment): ApprovedClaim[] {
  const out: ApprovedClaim[] = [];
  const items = [
    ...state.extraction.claims.map((c, i) => ({ id: `c${i + 1}`, c })),
    ...state.extraction.assumptions.map((c, i) => ({ id: `s${i + 1}`, c })),
  ];
  for (const { id, c } of items) {
    const reviewed = review.lines.get(id);
    if (!reviewed?.checked) continue;
    const edited = reviewed.text !== line(c.when, c.then.what);
    const arrow = reviewed.text.indexOf(" → ");
    const [when, what] = !edited ? [c.when, c.then.what]
      : arrow === -1 ? ["", reviewed.text]
      : [reviewed.text.slice(0, arrow).trim(), reviewed.text.slice(arrow + 3).trim()];
    out.push({ id, when, then: { kind: c.then.kind, what }, edited });
  }
  return out;
}

// Marks the comment approved while keeping the developer's edits: adds a banner under the header
// and updates the hidden state. Everything else in the body is left as they left it.
export function markApproved(body: string, state: CommentState): string {
  const n = state.approval!.claims.length;
  const r = state.approval!.regressions?.length ?? 0;
  const what = `${n} claim${n === 1 ? "" : "s"}${r ? ` and ${r} regression check${r === 1 ? "" : "s"}` : ""}`;
  const banner = `✅ **Approved by @${state.approval!.by}: ${what} will be tested.** ` +
    "Follow along on the Groundtruth check. Edits after approval are ignored; reopen the PR to start over.";
  return withState(body, state).replace(HEADER, `${HEADER}\n\n${banner}`);
}

// The regression rows the developer kept: rows still present and not struck through.
export function approvedRegressions(state: CommentState, review: ReviewedComment): RegressionRow[] {
  return (state.regressions ?? []).filter((r) => review.lines.get(r.id)?.checked);
}

// After a run whose compiled tests replayed cleanly: offers to commit them to the PR.
export function offerTests(body: string, state: CommentState, runUrl: string): string {
  const files = state.tests!.files;
  const n = files.length;
  const section = [
    "---",
    "#### Tests from this run",
    `Every claim was verified, and the compiled Playwright tests replayed cleanly ([see the run](${runUrl})). ` +
      "Add them to this PR and later PRs are checked against them.",
    "",
    ...files.map((f) => `- ${"`"}.groundtruth/tests/${f.file}${"`"}: ${f.title}`),
    "",
    `- [ ] **Add these ${n} test${n === 1 ? "" : "s"} to this PR** <!-- gt:addtests -->`,
    "",
  ].join("\n");
  const start = body.indexOf(STATE_PREFIX);
  return body.slice(0, start) + section + "\n" + withState(body.slice(start), state);
}

// After the tests were committed: replaces the offer with where they went, and updates the state.
export function markTestsAdded(body: string, state: CommentState, commitUrl: string): string {
  const { added, files } = state.tests!;
  const note = `✅ **Added ${files.length} test${files.length === 1 ? "" : "s"} to this PR** in [${added!.sha.slice(0, 7)}](${commitUrl}), for @${added!.by}.`;
  return withState(body, state).replace(/^- \[[ xX]\] \*\*Add these .*<!-- gt:addtests -->\s*$/m, note);
}

// Replaces the hidden state in a comment body.
function withState(body: string, state: CommentState): string {
  const start = body.indexOf(STATE_PREFIX);
  const end = body.indexOf(" -->", start) + " -->".length;
  return body.slice(0, start) + encodeState(state) + body.slice(end);
}
