import type { Octokit } from "@octokit/core";
import type { PrRef } from "../evidence.js";
import { readOnlyRepoToken, revokeToken } from "../github.js";
import { type BrowserCheck, checkInBrowser } from "./browser-check.js";
import { type BootConfig, loadBootConfig } from "./config.js";
import { createSandbox, type ExposedPort, type Sandbox } from "./sandbox.js";

// Boots a PR's app in a fresh sandbox from the repo's .groundtruth.yml, and proves a browser can use it.
// A failure here is an infrastructure `error` (the app didn't come up), never a claim failing.

export type Phase = { name: string; seconds: number; ok: boolean; detail?: string };

export type BootResult = {
  pr: string;
  sha: string;
  sandboxId?: string;
  phases: Phase[];
  ok: boolean;
  error?: string;
  appLogTail?: string;       // last lines of the app's own output, for diagnosing a failed start
  url?: string;
  browser?: BrowserCheck;
};

export type BootOptions = {
  sha?: string;              // commit to boot; defaults to the PR's current head
  expectSelector?: string;   // an element the home page should show once the app is up
  screenshotPath: string;
  keep?: boolean;            // leave the sandbox running afterwards (it still shuts itself down when idle)
  // Runs once the app is up and verified, before the sandbox shuts down (e.g. exploration).
  afterBoot?: (app: {
    url: string; headers: Record<string, string>; sandbox: Sandbox;
    resetApp?: () => Promise<void>;        // runs the config's `reset` command, if it has one
  }) => Promise<void>;
  onPhase?: (p: Phase) => void;
};

const REPO_DIR = "$HOME/repo";
const APP_LOG = "$HOME/app.log";

// What to boot: a commit, where its config comes from, and how to authenticate the clone.
export type BootTarget = {
  owner: string;
  repo: string;
  sha: string;
  label: string;                                            // e.g. owner/repo#123, for names and reports
  loadConfig: () => Promise<{ config: BootConfig; source: string }>;
  cloneToken?: () => Promise<string>;                       // omit for public repos (anonymous clone)
  revokeCloneToken?: (token: string) => Promise<void>;
};

// Boots a PR of an installed repo: its config from the default branch, cloned with a scoped app token.
export async function bootPr(octokit: Octokit, ref: PrRef, opts: BootOptions): Promise<BootResult> {
  const { data: pr } = await octokit.request("GET /repos/{owner}/{repo}/pulls/{pull_number}", {
    owner: ref.owner, repo: ref.repo, pull_number: ref.number,
  });
  return bootCommit({
    owner: ref.owner, repo: ref.repo, sha: opts.sha ?? pr.head.sha, label: `${ref.owner}/${ref.repo}#${ref.number}`,
    loadConfig: async () => {
      const { config, ref: branch } = await loadBootConfig(octokit, ref.owner, ref.repo);
      return { config, source: `from ${branch}` };
    },
    cloneToken: () => readOnlyRepoToken(ref.owner, ref.repo),
    revokeCloneToken: revokeToken,
  }, opts);
}

export async function bootCommit(target: BootTarget, opts: BootOptions): Promise<BootResult> {
  const { sha } = target;
  const result: BootResult = { pr: target.label, sha, phases: [], ok: false };
  const secrets: string[] = [];
  const redact = (s: string) => secrets.reduce((acc, x) => acc.split(x).join("***"), s);

  // Runs one timed phase; on failure records it and stops the boot.
  async function phase<T>(name: string, fn: () => Promise<T>, detail?: (v: T) => string): Promise<T> {
    const t = Date.now();
    try {
      const v = await fn();
      const p = { name, seconds: (Date.now() - t) / 1000, ok: true, detail: detail?.(v) };
      result.phases.push(p);
      opts.onPhase?.(p);
      return v;
    } catch (err) {
      const message = redact(err instanceof Error ? err.message : String(err));
      const p = { name, seconds: (Date.now() - t) / 1000, ok: false, detail: message };
      result.phases.push(p);
      opts.onPhase?.(p);
      throw new BootError(name, message);
    }
  }

  let sandbox: Sandbox | undefined;
  try {
    const { config } = await phase("read .groundtruth.yml", target.loadConfig, ({ source }) => source);
    sandbox = await phase("create sandbox", () => createSandbox({
      name: `groundtruth-boot-${target.label.replace(/[^a-zA-Z0-9-]+/g, "-")}-${sha.slice(0, 7)}`,
      idleShutdownSeconds: 600,
    }), (s) => s.id);
    result.sandboxId = sandbox.id;
    const sb = sandbox;
    const exports = envExports(config);
    const inRepo = (cmd: string) => `${exports}cd ${REPO_DIR}/${config.workdir} && ${cmd}`;
    const mustRun = async (cmd: string, timeoutSeconds?: number) => {
      const r = await sb.run(cmd, { timeoutSeconds });
      if (r.exitCode !== 0) throw new Error(`exit ${r.exitCode}: ${tail(r.stderr || r.stdout, 20)}`);
      return r.stdout.trim();
    };

    await phase("check runtime", async () => {
      const node = await mustRun("node --version");
      const want = config.runtime?.node;
      if (want && !node.replace(/^v/, "").startsWith(`${want}.`) && node.replace(/^v/, "") !== want) {
        throw new Error(`config wants Node ${want}, sandbox has ${node} (custom images aren't supported yet)`);
      }
      return node;
    }, (v) => `node ${v}`);

    await phase("clone commit", async () => {
      const token = await target.cloneToken?.();
      if (token) secrets.push(token);
      try {
        // Fetch the exact commit by SHA (works for fork PRs too). A token is only in the URL
        // of this one fetch, never written to the checkout's git config.
        const auth = token ? `x-access-token:${token}@` : "";
        return await mustRun([
          `git init -q ${REPO_DIR}`, `cd ${REPO_DIR}`,
          `git fetch -q --depth 1 https://${auth}github.com/${target.owner}/${target.repo}.git ${sha}`,
          "git checkout -q FETCH_HEAD", "git log -1 --format='%h %s'",
        ].join(" && "), 300);
      } finally {
        // Revoke before any repo code runs.
        if (token) await target.revokeCloneToken?.(token);
      }
    }, (v) => v);

    if (config.install) await phase("install", () => mustRun(inRepo(config.install!), 900), () => config.install!);
    for (const cmd of config.setup) await phase("setup", () => mustRun(inRepo(cmd), 600), () => cmd);

    await phase("start app", () => mustRun([
      // Written to a script so the start command needs no quoting; setsid + nohup detach it from this command.
      `cat > $HOME/start.sh <<'GROUNDTRUTH_EOF'\n${exports}cd ${REPO_DIR}/${config.workdir}\n${config.start}\nGROUNDTRUTH_EOF`,
      `setsid nohup sh $HOME/start.sh > ${APP_LOG} 2>&1 < /dev/null &`,
      "echo started",
    ].join("\n")), () => config.start);

    await phase("wait until ready", async () => {
      const r = await sb.run(readyLoop(config), { timeoutSeconds: config.ready.timeout_seconds + 30 });
      if (r.exitCode !== 0) throw new Error(`${config.ready.path} not answering 200 after ${config.ready.timeout_seconds}s (last: ${r.stdout.trim() || "no response"})`);
      return r.stdout.trim();
    }, () => `GET ${config.ready.path} on :${config.port}`);

    const exposed: ExposedPort = await phase("open tunnel", async () => {
      const e = await sb.expose(config.port);
      secrets.push(...Object.values(e.headers));
      const res = await fetch(new URL(config.ready.path, e.url), { headers: e.headers });
      if (res.status !== 200) throw new Error(`${config.ready.path} through the tunnel returned HTTP ${res.status}`);
      return e;
    }, (e) => e.url);
    result.url = exposed.url;

    result.browser = await phase("browser check", async () => {
      const b = await checkInBrowser({ url: exposed.url, headers: exposed.headers, expectSelector: opts.expectSelector, screenshotPath: opts.screenshotPath });
      if (b.status === null || b.status >= 400) throw new Error(`home page returned HTTP ${b.status}`);
      if (!b.rendered) throw new Error(`the page stayed blank for 90s (HTTP ${b.status}, title "${b.title}")`);
      // A rendered error page is not a booted app: any server error from the app during load fails the check.
      const serverErrors = b.failedRequests.filter((r) => r.startsWith(exposed.url) && / \(HTTP 5\d\d\)$/.test(r));
      if (serverErrors.length) throw new Error(`the app returned server errors while loading: ${serverErrors.slice(0, 3).join(", ")}`);
      if (b.found === false) throw new Error(`"${opts.expectSelector}" never appeared (page title: "${b.title}")`);
      return b;
    }, (b) => `HTTP ${b.status}, "${b.title}", shows "${b.textSample.slice(0, 50)}"${b.found ? `, found ${opts.expectSelector}` : ""}`);

    result.ok = true;
    if (opts.afterBoot) {
      const reset = config.reset;
      await opts.afterBoot({
        url: exposed.url, headers: exposed.headers, sandbox: sb,
        resetApp: reset ? async () => { await mustRun(inRepo(reset), 120); } : undefined,
      });
    }
  } catch (err) {
    result.error = err instanceof BootError ? `${err.phase}: ${err.message}` : redact(String(err));
  } finally {
    if (sandbox) {
      if (!result.ok) {
        result.appLogTail = await sandbox.run(`tail -n 40 ${APP_LOG} 2>/dev/null`).then((r) => redact(r.stdout), () => undefined);
      }
      if (!opts.keep) await sandbox.shutdown().catch(() => {});
    }
  }
  return result;
}

class BootError extends Error {
  constructor(readonly phase: string, message: string) { super(message); }
}

// `export K='v'; ` for each config env var, single-quoted so values are taken literally.
function envExports(config: BootConfig): string {
  return Object.entries(config.env).map(([k, v]) => `export ${k}='${v.replace(/'/g, "'\\''")}'; `).join("");
}

// One round trip: poll the ready URL inside the sandbox until it returns 200 or time runs out.
function readyLoop(config: BootConfig): string {
  const url = `http://localhost:${config.port}${config.ready.path}`;
  const tries = Math.ceil(config.ready.timeout_seconds / 2);
  return `for i in $(seq 1 ${tries}); do code=$(curl -s -o /dev/null -w '%{http_code}' ${url}); ` +
    `if [ "$code" = 200 ]; then echo "ready after $((i*2))s"; exit 0; fi; sleep 2; done; echo "$code"; exit 1`;
}

const tail = (s: string, n: number) => s.trim().split("\n").slice(-n).join("\n");
