import type { Octokit } from "@octokit/core";

// The intent evidence bundle: everything claim extraction is allowed to read about a PR.
// Deliberately excludes the code diff. Claims describe what the author *wanted*, not what
// the code does; if the prose doesn't say enough, extraction should ask rather than infer.
// Built deterministically so we can inspect exactly what the model sees.

export type Evidence = {
  repo: string;
  number: number;
  url: string;
  title: string;
  author: string;
  body: string;
  baseRef: string;
  headSha: string;
  linkedIssues: { number: number; title: string; body: string }[];
  commits: string[];
};

export type PrRef = { owner: string; repo: string; number: number };

const MAX_ISSUE_BODY_CHARS = 4_000;

export function parsePrRef(input: string): PrRef {
  const match =
    input.match(/^([\w.-]+)\/([\w.-]+)#(\d+)$/) ??
    input.match(/github\.com\/([\w.-]+)\/([\w.-]+)\/pull\/(\d+)/);
  if (!match) throw new Error(`Expected owner/repo#123 or a PR URL, got "${input}"`);
  return { owner: match[1], repo: match[2], number: Number(match[3]) };
}

export async function listAll<T>(fetchPage: (page: number) => Promise<T[]>): Promise<T[]> {
  const all: T[] = [];
  for (let page = 1; ; page++) {
    const items = await fetchPage(page);
    all.push(...items);
    if (items.length < 100) return all;
  }
}

type PrGraph = {
  repository: {
    pullRequest: {
      closingIssuesReferences: { nodes: { number: number; title: string; body: string }[] };
      userContentEdits: { nodes: { editedAt: string; diff: string | null }[] };
    };
  };
};

// `asOf` rebuilds the evidence as it was at that moment (e.g. when the PR was opened):
// the description version current then, and only commits made by then.
// Linked issues and the title are taken as they are now.
export async function buildEvidence(
  octokit: Octokit,
  { owner, repo, number }: PrRef,
  { asOf }: { asOf?: string } = {},
): Promise<Evidence> {
  const params = { owner, repo, pull_number: number };
  const [{ data: pr }, allCommits, graph] = await Promise.all([
    octokit.request("GET /repos/{owner}/{repo}/pulls/{pull_number}", params),
    listAll((page) =>
      octokit.request("GET /repos/{owner}/{repo}/pulls/{pull_number}/commits", { ...params, per_page: 100, page })
        .then((r) => r.data)),
    octokit.graphql<PrGraph>(
      `query($owner: String!, $repo: String!, $number: Int!) {
        repository(owner: $owner, name: $repo) {
          pullRequest(number: $number) {
            closingIssuesReferences(first: 10) { nodes { number title body } }
            userContentEdits(first: 100) { nodes { editedAt diff } }
          }
        }
      }`,
      { owner, repo, number },
    ),
  ]);
  const { closingIssuesReferences, userContentEdits } = graph.repository.pullRequest;

  let body = pr.body ?? "";
  let commits = allCommits;
  if (asOf) {
    // Each edit's `diff` holds the full description as of that edit.
    const version = userContentEdits.nodes
      .filter((e) => e.editedAt <= asOf && e.diff !== null)
      .sort((a, b) => a.editedAt.localeCompare(b.editedAt))
      .at(-1);
    if (version) body = version.diff!;
    // Commit dates can postdate the opening if the author force-pushed a replacement
    // (lobsters#2029), and a PR always has at least one commit, so keep the first regardless.
    commits = allCommits.filter((c, i) => i === 0 || (c.commit.committer?.date ?? "") <= asOf);
  }

  return {
    repo: `${owner}/${repo}`,
    number,
    url: pr.html_url,
    title: pr.title,
    author: pr.user?.login ?? "unknown",
    body: stripComments(body),
    baseRef: pr.base.ref,
    headSha: asOf ? (commits.at(-1)?.sha ?? pr.head.sha) : pr.head.sha,
    linkedIssues: closingIssuesReferences.nodes.map((i) => ({
      number: i.number,
      title: i.title,
      body: truncate(stripComments(i.body ?? ""), MAX_ISSUE_BODY_CHARS),
    })),
    commits: commits.map((c) => c.commit.message.trim()),
  };
}

// PR/issue templates are usually HTML comments the author never filled in.
function stripComments(text: string): string {
  return text.replace(/<!--[\s\S]*?-->/g, "").trim();
}

function truncate(text: string, max: number): string {
  return text.length <= max ? text : `${text.slice(0, max)}\n[... truncated ${text.length - max} chars]`;
}

// The exact text handed to the model.
export function renderEvidence(e: Evidence): string {
  const issues = e.linkedIssues.length
    ? e.linkedIssues.map((i) => `<issue number="${i.number}">\n<title>${i.title}</title>\n${i.body || "(empty)"}\n</issue>`).join("\n")
    : "(none)";

  return [
    `<pull_request repo="${e.repo}" number="${e.number}" author="${e.author}" base="${e.baseRef}">`,
    `<title>${e.title}</title>`,
    `<description>\n${e.body || "(empty)"}\n</description>`,
    `</pull_request>`,
    `<linked_issues>\n${issues}\n</linked_issues>`,
    `<commits>\n${e.commits.map((m) => `- ${m.replace(/\n/g, "\n  ")}`).join("\n")}\n</commits>`,
  ].join("\n\n");
}

export function summarizeEvidence(e: Evidence, rendered: string) {
  return {
    description: e.body ? `${e.body.length} chars` : "empty",
    linkedIssues: e.linkedIssues.length,
    commits: e.commits.length,
    chars: rendered.length,
    approxTokens: Math.round(rendered.length / 4),
  };
}
