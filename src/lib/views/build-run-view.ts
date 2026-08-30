import { loadAppConfiguration } from "@/lib/config/app-config";
import type { Run, RunView, TestMission } from "@/lib/domain/schemas";

export async function buildRunView(run: Run): Promise<RunView> {
  const appConfiguration = await loadAppConfiguration(
    run.repository.owner,
    run.repository.name,
    run.pullRequest.baseSha,
    run.pullRequest.headSha,
  );
  const setupBlockers = appConfiguration.ready ? [] : appConfiguration.blockers;
  const configuredMissions = appConfiguration.ready
    ? appConfiguration.missions
        .filter((mission) => mission.kind === "intent")
        .map((mission) => resolveCoverageMission(mission, run))
    : [];
  const attempts = [
    ...(run.browserVerificationHistory ?? []),
    ...(run.browserVerification ? [run.browserVerification] : []),
  ];

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
  const completedIntentMissions = attempts.flatMap((attempt) =>
    attempt.attemptId && attempt.mission?.kind === "intent" ? [attempt.mission] : [],
  );
  const coverageMissions = [
    ...new Map(
      [...configuredMissions, ...completedIntentMissions].map((mission) => [mission.id, mission]),
    ).values(),
  ];
  const displayedMissions = [
    ...new Map(
      [
        ...configuredMissions,
        ...attempts.flatMap((attempt) => (attempt.mission ? [attempt.mission] : [])),
      ].map((mission) => [mission.id, mission]),
    ).values(),
  ];
  const journeyComplete = Boolean(browser?.journey);
  const executionComplete = Boolean(browser?.execution || browser?.comparison);
  const executionStatus = browser?.execution?.status;
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
  const executionPhaseStatus: RunView["phases"][number]["status"] =
    browser?.status === "blocked" || executionStatus === "blocked"
      ? "blocked"
      : browser?.status === "failed" || executionStatus === "error"
        ? "failed"
        : executionComplete
          ? "complete"
          : browserActive
            ? "active"
            : "pending";
  const executionPhaseDetail =
    browser?.blocker?.message ??
    (browser?.comparison
      ? browser.comparison.reason
      : browser?.status === "complete" && executionStatus === "failed"
        ? "Browser replay completed with assertion findings."
        : executionStatus === "error"
          ? "Browser replay ended with a runner error."
          : executionStatus === "blocked"
            ? "Browser replay was blocked before it could produce a verdict."
            : downstreamDetail);
  const results = buildAllResults(attempts);
  const claimCoverage = buildClaimCoverage(run, coverageMissions);
  const validClaimIds = new Set(run.intentSpec?.claims.map((claim) => claim.id) ?? []);
  const selectedClaimId =
    results.intent.find(
      (result) => result.verdict === "non_conformant" && validClaimIds.has(result.claimId),
    )?.claimId ??
    results.intent.find((result) => validClaimIds.has(result.claimId))?.claimId ??
    (executionComplete
      ? claimCoverage.find(
          (coverage) =>
            coverage.status === "covered" &&
            validClaimIds.has(coverage.claimId),
        )?.claimId
      : coverageMissions
          .flatMap((mission) => mission.claimIds)
          .find((claimId) => validClaimIds.has(claimId))) ??
    run.intentSpec?.claims[0]?.id;

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
        status: executionPhaseStatus,
        detail: executionPhaseDetail,
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
      selectedClaimId,
      claimCoverage,
    },
    missions: displayedMissions,
    journey: browser?.journey,
    environments: browser?.environments ?? [],
    results,
    blastRadius: appConfiguration.ready ? appConfiguration.impactMap : undefined,
    recording: currentRecordings(browser).find((recording) => recording.target === "head") ??
      currentRecordings(browser)[0],
    recordings: currentRecordings(browser),
    verificationAttempts: attempts,
    actions: browser?.actions ?? [],
    network: browser?.network ?? [],
    blocker: browser?.blocker ?? run.blocker,
  };
}

export function buildResults(browser: Run["browserVerification"]): RunView["results"] {
  if (!browser?.mission) {
    return { intent: [], regression: [] };
  }
  if (!browser.attemptId) {
    return { intent: [], regression: [] };
  }
  if (browser.mission.kind === "intent") {
    if (!browser.execution || browser.execution.attemptId !== browser.attemptId) {
      return { intent: [], regression: [] };
    }
    const claimId =
      browser.mission.claimIds.length === 1 ? browser.mission.claimIds[0] : undefined;
    return {
      intent: claimId
        ? [
            {
            missionId: browser.mission.id,
            claimId,
            verdict:
              browser.execution.status === "passed"
                ? ("conformant" as const)
                : browser.execution.status === "failed"
                  ? ("non_conformant" as const)
                    : ("inconclusive" as const),
            },
          ]
        : [],
      regression: [],
    };
  }
  if (!browser.comparison || browser.comparison.attemptId !== browser.attemptId) {
    return { intent: [], regression: [] };
  }
  return {
    intent: [],
    regression: [
      {
        missionId: browser.mission.id,
        verdict: browser.comparison.verdict,
        reason: browser.comparison.reason,
        firstDivergence: browser.comparison.firstDivergence,
      },
    ],
  };
}

function buildAllResults(attempts: NonNullable<Run["browserVerification"]>[]): RunView["results"] {
  const intent = new Map<string, RunView["results"]["intent"][number]>();
  const regression = new Map<string, RunView["results"]["regression"][number]>();
  for (const attempt of attempts) {
    const result = buildResults(attempt);
    for (const item of result.intent) {
      intent.set(`${item.missionId}:${item.claimId}`, item);
    }
    for (const item of result.regression) {
      regression.set(item.missionId, item);
    }
  }
  return { intent: [...intent.values()], regression: [...regression.values()] };
}

function currentRecordings(browser: Run["browserVerification"]): RunView["recordings"] {
  if (!browser) {
    return [];
  }
  const executions = browser.executions
    ? [browser.executions.base, browser.executions.head]
    : [browser.execution];
  return executions.flatMap((execution) =>
    execution?.evidence.videoArtifactId
      ? [
          {
            target: execution.target,
            artifactId: execution.evidence.videoArtifactId,
            contentType: "video/webm",
          },
        ]
      : [],
  );
}

export function buildClaimCoverage(
  run: Run,
  missionOrMissions: TestMission | TestMission[] | undefined,
): RunView["contract"]["claimCoverage"] {
  const missions = Array.isArray(missionOrMissions)
    ? missionOrMissions
    : missionOrMissions
      ? [missionOrMissions]
      : [];
  const coveredByClaim = new Map<string, string>();
  for (const mission of missions) {
    for (const claimId of mission.claimIds) {
      coveredByClaim.set(claimId, mission.id);
    }
  }
  const deferred = new Map(
    missions.flatMap(
      (mission) =>
        mission.deferredClaims?.map((claim) => [claim.claimId, claim.reason] as const) ?? [],
    ),
  );
  return (run.intentSpec?.claims ?? [])
    .filter((claim) => claim.priority === "must")
    .map((claim) => {
      const missionId = coveredByClaim.get(claim.id);
      if (missionId) {
        return {
          claimId: claim.id,
          status: "covered" as const,
          missionId,
        };
      }
      const reason = deferred.get(claim.id);
      if (reason) {
        return {
          claimId: claim.id,
          status: "deferred" as const,
          reason,
        };
      }
      return {
        claimId: claim.id,
        status: "uncovered" as const,
      };
    });
}

function resolveCoverageMission(mission: TestMission, run: Run): TestMission {
  const claims = run.intentSpec?.claims ?? [];
  const claimMatch = mission.claimSourceQuote
    ? claims.filter((claim) => claim.sourceQuote === mission.claimSourceQuote)
    : [];
  const deferredClaims = mission.deferredClaims?.map((deferred) => {
    if (!deferred.claimSourceQuote) {
      return deferred;
    }
    const matches = claims.filter((claim) => claim.sourceQuote === deferred.claimSourceQuote);
    return matches.length === 1 ? { ...deferred, claimId: matches[0].id } : deferred;
  });
  return {
    ...mission,
    claimIds: claimMatch.length === 1 ? [claimMatch[0].id] : mission.claimIds,
    deferredClaims,
  };
}
