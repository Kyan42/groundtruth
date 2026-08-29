import { loadAppConfiguration } from "@/lib/config/app-config";
import type { Run, RunView } from "@/lib/domain/schemas";

export async function buildRunView(run: Run): Promise<RunView> {
  const appConfiguration = await loadAppConfiguration(run.repository.owner, run.repository.name);
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
  const intentFailed = run.status === "failed";
  const intentBlocked = run.status === "blocked" || run.status === "setup_required";
  const invalidContractCodes = new Set([
    "invalid_intent_spec",
    "intent_quote_not_in_pr",
    "intent_interrupted",
    "intent_agent_failed",
  ]);
  const downstreamDetail = approved
    ? appConfiguration.ready
      ? "Not run in the intent-capture foundation."
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
        label: "Impact mapping",
        status: approved ? "blocked" : "pending",
        detail: downstreamDetail,
      },
      {
        id: "plan",
        label: "Mission planning",
        status: approved ? "blocked" : "pending",
        detail: downstreamDetail,
      },
      {
        id: "execution",
        label: "Browser execution",
        status: approved ? "blocked" : "pending",
        detail: downstreamDetail,
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
    missions: [],
    results: { intent: [], regression: [] },
    actions: [],
    network: [],
    blocker: run.blocker,
  };
}
