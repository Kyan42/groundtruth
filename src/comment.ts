import type { Extraction } from "./extract.js";

// The PR comment is Groundtruth's record for a PR: the developer reviews and edits it directly.
// Hidden HTML comments carry what we need to read it back: a marker identifying the comment,
// a marker per checkbox line, and the extraction itself (base64 JSON, so it can't break the HTML comment).

export const COMMENT_MARKER = "<!-- groundtruth -->";
const STATE_PREFIX = "<!-- groundtruth-state:";

export type CommentState = {
  version: 1;
  pr: string;       // owner/repo#number
  headSha: string;
  extraction: Extraction;
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
  out.push(`${STATE_PREFIX}${Buffer.from(JSON.stringify(state)).toString("base64")} -->`);
  return out.join("\n");
}

// Reads the hidden state back out of a comment body (undefined if it has none yet, e.g. the placeholder).
export function readState(body: string): CommentState | undefined {
  const start = body.indexOf(STATE_PREFIX);
  if (start === -1) return undefined;
  const end = body.indexOf(" -->", start);
  return JSON.parse(Buffer.from(body.slice(start + STATE_PREFIX.length, end), "base64").toString("utf8"));
}
