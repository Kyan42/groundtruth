import { loadAppConfiguration } from "@/lib/config/app-config";
import type { Run, RunView } from "@/lib/domain/schemas";

export async function buildRunView(run: Run): Promise<RunView> {
  const appConfiguration = await loadAppConfiguration(
    run.repository.owner,
    run.repository.name,
    run.pullRequest.baseSha,
    run.pullRequest.headSha,
  );
  const setupBlockers = appConfiguration.ready ? [] : appConfiguration.blockers;

  if (
    run.blocker?.code.endsWith("_missing") ||
    run.status === "setup_required" ||
    run.blocker?.code === "integration_failed"
  ) {
    setupBlockers.unshift({ code: run.blocker?.code ?? "setup_required", message: run.blocker?.message ?? "Setup is required." });
  }

  const intentComplete =
    run.status === "awaiting_contract_approval" ||
    run.status === "contract_approved" ||
    Boolean(run.intentSpec);
  const approved = Boolean(run.intentApproval);
  const browser = run.browserVerification;
  const journeyComplete = Boolean(browser?.journey);
  const executionComplete = Boolean(browser?.execution);
  const browserActive =
    run.status === "verifying" ||
    browser?.status === "preparing" ||
    browser?.status === "discovering" ||
    browser?.status === "executing";
  const intentFailed = !run.intentSpec && run.status === "failed";
  const intentBlocked =
    !run.intentSpec && (run.status === "blocked" || run.status === "setup_required");
  const invalidContractCodes = new Set([
    "invalid_intent_spec",
    "intent_quote_not_in_pr",
    "intent_interrupted",
    "intent_agent_failed",
  ]);
  const downstreamDetail = approved
    ? appConfiguration.ready
      ? browser
        ? undefined
        : "Ready to start the trusted browser mission."
      : "Trusted AppProfile and AppMap are required before browser verification."
    : "Waiting for intent contract approval.";

  return {
    run: {
      id: run.id,
      status: run.status,
      repository: `${run.repository.owner}/${run.repository.name}`,
      pullRequestNumber: run.pullRequest.number,
      pullRequestUrl: run.pullRequest.url,
      title: run.pullRequest.title,
      headSha: run.pullRequest.headSha,
      coordinationAxonId: run.coordinationAxonId,
    },
    setup: {
      ready: setupBlockers.length === 0,
      blockers: setupBlockers,
    },
    phases: [
      { id: "pr", label: "PR ingested", status: "complete", detail: run.pullRequest.headSha },
      {
        id: "intent",
        label: "Intent contract",
        status: intentFailed
          ? "failed"
          : intentBlocked
            ? "blocked"
            : intentComplete
              ? "complete"
              : "active",
        detail: run.blocker?.message,
      },
      {
        id: "approval",
        label: "Human approval",
        status: approved ? "complete" : intentComplete ? "active" : "pending",
      },
      {
        id: "impact",
        label: "Trusted AppMap",
        status: browser ? "complete" : approved ? "pending" : "pending",
        detail: downstreamDetail,
      },
      {
        id: "plan",
        label: "Mission planning",
        status: journeyComplete ? "complete" : browserActive ? "active" : approved ? "pending" : "pending",
        detail: browser?.blocker?.message ?? downstreamDetail,
      },
      {
        id: "execution",
        label: "Browser execution",
        status: executionComplete
          ? browser?.execution?.status === "passed"
            ? "complete"
            : "failed"
          : browser?.status === "blocked"
            ? "blocked"
            : browser?.status === "failed"
              ? "failed"
              : browserActive
                ? "active"
                : "pending",
        detail: browser?.blocker?.message ?? downstreamDetail,
      },
    ],
    contract: {
      status: approved
        ? "approved"
        : run.intentSpec
          ? "ready"
          : run.blocker && invalidContractCodes.has(run.blocker.code)
            ? "invalid"
            : "pending",
      intentSpec: run.intentSpec,
      selectedClaimId: run.intentSpec?.claims[0]?.id,
    },
    missions: browser?.mission ? [browser.mission] : [],
    journey: browser?.journey,
    environments: browser?.environments ?? [],
    results: buildResults(browser),
    recording: browser?.execution?.evidence.videoArtifactId
      ? {
          artifactId: browser.execution.evidence.videoArtifactId,
          contentType: "video/webm",
        }
      : undefined,
    actions: browser?.actions ?? [],
    network: browser?.network ?? [],
    blocker: browser?.blocker ?? run.blocker,
  };
}

function buildResults(browser: Run["browserVerification"]): RunView["results"] {
  if (!browser?.mission || !browser.execution) {
    return { intent: [], regression: [] };
  }
  if (browser.mission.kind === "intent") {
    return {
      intent: browser.mission.claimIds.map((claimId) => ({
        missionId: browser.mission!.id,
        claimId,
        verdict:
          browser.execution!.status === "passed"
            ? ("conformant" as const)
            : browser.execution!.status === "failed"
              ? ("non_conformant" as const)
              : ("inconclusive" as const),
      })),
      regression: [],
    };
  }
  return {
    intent: [],
    regression: [
      {
        missionId: browser.mission.id,
        verdict:
          browser.execution.status === "passed"
            ? ("safe" as const)
            : browser.execution.status === "failed"
              ? ("regression" as const)
              : ("inconclusive" as const),
      },
    ],
  };
}
