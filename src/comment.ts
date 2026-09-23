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
  approval?: Approval;
};

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
  claims: ApprovedClaim[];
};

const HEADER = "### 🧪 Groundtruth: what this PR should do";

export function renderPlaceholder(): string {
  return [COMMENT_MARKER, HEADER, "", "Reading this PR… (usually under a minute)"].join("\n");
}

export function renderError(message: string): string {
  return [COMMENT_MARKER, HEADER, "", `⚠️ Couldn't read this PR: ${message}`, "",
    "Close and reopen the PR to try again."].join("\n");
}

const line = (when: string, what: string) => `${when.replace(/\.$/, "")} → ${what.replace(/\.$/, "")}`;
const sub = (text: string) => `<sub>${text.replace(/\s+/g, " ").trim()}</sub>`;

export function renderClaimsComment(state: CommentState): string {
  const { claims, assumptions, not_testable, regression_hints } = state.extraction;
  const out = [COMMENT_MARKER, HEADER, ""];

  if (claims.length === 0 && assumptions.length === 0) {
    out.push("Found no user-visible change to test in the PR description and commits.",
      "If this PR does change behavior, describe it in the PR description and reopen the PR.", "");
  } else {
    out.push("Read from the PR description, linked issues and commits. **Nothing is tested until you approve.**",
      "Uncheck anything that's wrong, or edit the wording directly.", "");
  }

  if (claims.length) {
    out.push("**Claims**: tested as written", "");
    claims.forEach((c, i) => {
      out.push(`- [x] **${i + 1}.** ${line(c.when, c.then.what)} <!-- gt:c${i + 1} -->`,
        `  ${sub(`from the evidence: "${c.source}"`)}`);
    });
    out.push("");
  }

  if (assumptions.length) {
    out.push("**Please confirm**: our guesses where the PR doesn't say (checked = intended, test it)", "");
    assumptions.forEach((a, i) => {
      out.push(`- [${a.checked ? "x" : " "}] ${line(a.when, a.then.what)} <!-- gt:s${i + 1} -->`, `  ${sub(a.reason)}`);
    });
    out.push("");
  }

  if (not_testable.length) {
    out.push(`<details><summary>Not tested (${not_testable.length})</summary>`, "",
      ...not_testable.map((n) => `- "${n.text}": ${n.why}`), "", "</details>", "");
  }
  if (regression_hints.length) {
    out.push(`<details><summary>Existing behavior to re-check (${regression_hints.length})</summary>`, "",
      ...regression_hints.map((h) => `- ${h}`), "", "</details>", "");
  }

  if (claims.length || assumptions.length) {
    out.push("- [ ] **Approve and start testing** <!-- gt:approve -->", "");
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
  lines: Map<string, { checked: boolean; text: string }>; // by marker id: c1, s2, ...
};

// Finds each checkbox line by its hidden marker, so reordering or rewording lines doesn't lose them.
export function parseReview(body: string): ReviewedComment {
  const lines = new Map<string, { checked: boolean; text: string }>();
  let approveChecked = false;
  for (const m of body.matchAll(/^- \[([ xX])\] (.*?)\s*<!-- gt:(c\d+|s\d+|approve) -->\s*$/gm)) {
    const checked = m[1] !== " ";
    if (m[3] === "approve") approveChecked = checked;
    else lines.set(m[3], { checked, text: m[2].replace(/^\*\*\d+\.\*\*\s*/, "").trim() });
  }
  return { approveChecked, lines };
}

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
  const banner = `✅ **Approved by @${state.approval!.by}: ${n} check${n === 1 ? "" : "s"} will be tested.** ` +
    "Testing isn't built yet. Edits after approval are ignored; reopen the PR to start over.";
  const start = body.indexOf(STATE_PREFIX);
  const end = body.indexOf(" -->", start) + " -->".length;
  return (body.slice(0, start) + encodeState(state) + body.slice(end)).replace(HEADER, `${HEADER}\n\n${banner}`);
}
