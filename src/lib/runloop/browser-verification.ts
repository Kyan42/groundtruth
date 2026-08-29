import { randomUUID } from "node:crypto";

import type { Devbox } from "@runloop/api-client";
import { z } from "zod";

import type { AppConfiguration } from "@/lib/config/app-config";
import { hasTrustedRegressionImpact } from "@/lib/config/app-config";
import { GroundtruthError } from "@/lib/domain/errors";
import { compareRegressionExecutions } from "@/lib/domain/regression-comparison";
import {
  type AppMap,
  type AppProfile,
  type BrowserEnvironment,
  type BrowserVerification,
  BrowserVerificationSchema,
  type ExecutableJourney,
  ExecutableJourneySchema,
  type ExecutionResult,
  ExecutionResultSchema,
  type ExecutionTarget,
  type Run,
  type TestMission,
} from "@/lib/domain/schemas";
import {
  ensureBrowserTables,
  publishRunEvent,
  saveArtifact,
  saveEnvironment,
  saveExecution,
  saveJourney,
  saveMission,
  saveRegressionComparison,
} from "@/lib/runloop/coordination-axon";
import { validateJourneyForReplay } from "@/lib/runloop/browser-safety";
import { getRunloopClient } from "@/lib/runloop/client";

const RunnerOutputSchema = z.object({
  status: z.enum(["passed", "failed", "error"]),
  startedAt: z.iso.datetime(),
  endedAt: z.iso.datetime(),
  steps: z.array(
    z.object({
      index: z.number().int().nonnegative(),
      status: z.enum(["passed", "failed"]),
      message: z.string().optional(),
      at: z.iso.datetime(),
      summary: z.string().min(1),
    }),
  ),
  checks: z.array(
    z.object({
      assertionIndex: z.number().int().nonnegative(),
      assertionId: z.string().min(1),
      behavior: z.string().min(1),
      comparison: z.enum(["pass_only", "exact"]),
      passed: z.boolean(),
      actual: z.unknown(),
    }),
  ),
  actions: z.array(
    z.object({
      at: z.iso.datetime(),
      target: z.enum(["base", "head"]),
      summary: z.string().min(1),
      status: z.string().min(1),
    }),
  ),
  network: z.array(
    z.object({
      method: z.string().min(1),
      url: z.string().min(1),
      status: z.number().int().min(100).max(599),
      target: z.enum(["base", "head"]),
    }),
  ),
  files: z.object({
    screenshots: z.array(z.string().min(1)),
    trace: z.string().min(1).optional(),
    video: z.string().min(1).optional(),
    actions: z.string().min(1),
    console: z.string().min(1),
    errors: z.string().min(1),
    network: z.string().min(1),
  }),
  error: z.object({ code: z.string().min(1), message: z.string().min(1) }).optional(),
});
const JourneyDraftSchema = ExecutableJourneySchema.omit({ producer: true });

type ProgressCallback = (state: BrowserVerification) => Promise<void>;

export async function executeBrowserVerification(
  run: Run,
  configuration: Extract<AppConfiguration, { ready: true }>,
  selectedMission: TestMission,
  onProgress: ProgressCallback,
): Promise<BrowserVerification> {
  if (!run.coordinationAxonId || !run.intentSpec || !run.intentApproval) {
    throw new GroundtruthError(
      "browser_prerequisites_missing",
      "An approved intent and coordination Axon are required for browser verification.",
      409,
    );
  }
  const mission =
    selectedMission.kind === "intent"
      ? resolveMission(selectedMission, run)
      : resolveRegressionMission(selectedMission, configuration);
  const axonId = run.coordinationAxonId;
  const attemptId = randomUUID();
  let state = BrowserVerificationSchema.parse({
    attemptId,
    status: "preparing",
    mission,
    environments: [],
    actions: [],
    network: [],
  });
  let baseBox: Devbox | undefined;
  let headBox: Devbox | undefined;
  let browserBox: Devbox | undefined;

  const emit = async (next: BrowserVerification): Promise<void> => {
    state = BrowserVerificationSchema.parse(next);
    await onProgress(state);
  };
  const runTarget = async (
    box: Devbox,
    target: ExecutionTarget,
    applicationUrl: string,
    journey: ExecutableJourney,
  ): Promise<{
    result: ExecutionResult;
    replay?: z.infer<typeof RunnerOutputSchema>;
  }> => {
    const executionId = randomUUID();
    await publishRunEvent(axonId, "execution.started", "EXTERNAL_EVENT", {
      runId: run.id,
      attemptId,
      missionId: mission.id,
      executionId,
      target,
    });
    let result: ExecutionResult;
    let replay: z.infer<typeof RunnerOutputSchema> | undefined;
    try {
      await resetFixtures(box, configuration.profile, target);
      await publishRunEvent(axonId, "fixture.reset_completed", "EXTERNAL_EVENT", {
        runId: run.id,
        attemptId,
        missionId: mission.id,
        target,
      });
      replay = await replayJourney(
        browserBox!,
        mission,
        journey,
        configuration.profile,
        target,
        applicationUrl,
      );
      result = await persistReplayArtifacts(
        browserBox!,
        run,
        mission.id,
        attemptId,
        executionId,
        target,
        replay,
        axonId,
      );
    } catch (error) {
      result = failedExecution(mission.id, attemptId, executionId, target, error);
    }
    await saveExecution(axonId, executionId, result, result.endedAt);
    await publishRunEvent(axonId, "execution.completed", "EXTERNAL_EVENT", {
      runId: run.id,
      attemptId,
      executionId,
      missionId: mission.id,
      target,
      status: result.status,
      assertionCount: result.checks.length,
      evidenceCount: countEvidence(result),
    });
    return { result, replay };
  };

  try {
    if (configuration.profile.auth.mode !== "none") {
      throw new GroundtruthError(
        "browser_auth_mode_unsupported",
        "This prototype does not yet support storage-state authentication.",
        422,
      );
    }
    await ensureBrowserTables(axonId);
    await saveMission(axonId, attemptId, run.id, mission, new Date().toISOString());
    await publishRunEvent(axonId, "mission.loaded", "EXTERNAL_EVENT", {
      runId: run.id,
      attemptId,
      missionId: mission.id,
      kind: mission.kind,
    });

    baseBox = await createApplicationEnvironment(
      run,
      configuration.profile,
      "base",
      run.pullRequest.baseSha,
    );
    const baseUrl = await baseBox.getTunnelUrl(configuration.profile.workspace.port);
    const baseEnvironment: BrowserEnvironment = {
      role: "base",
      devboxId: baseBox.id,
      status: "running",
      exactSha: run.pullRequest.baseSha,
      url: baseUrl,
      detail: "Default Runloop image with the trusted install/start commands; no prepared Snapshot was configured.",
    };
    await saveEnvironment(axonId, attemptId, run.id, baseEnvironment, new Date().toISOString());
    await emit({ ...state, environments: [baseEnvironment] });
    await publishRunEvent(axonId, "environment.ready", "EXTERNAL_EVENT", {
      runId: run.id,
      role: "base",
      devboxId: baseBox.id,
      exactSha: run.pullRequest.baseSha,
    });

    headBox = await createApplicationEnvironment(
      run,
      configuration.profile,
      "head",
      run.pullRequest.headSha,
    );
    const headUrl = await headBox.getTunnelUrl(configuration.profile.workspace.port);
    const headEnvironment: BrowserEnvironment = {
      role: "head",
      devboxId: headBox.id,
      status: "running",
      exactSha: run.pullRequest.headSha,
      url: headUrl,
      detail: "Default Runloop image with the trusted install/start commands; no prepared Snapshot was configured.",
    };
    await saveEnvironment(axonId, attemptId, run.id, headEnvironment, new Date().toISOString());
    await emit({ ...state, environments: [baseEnvironment, headEnvironment] });
    await publishRunEvent(axonId, "environment.ready", "EXTERNAL_EVENT", {
      runId: run.id,
      role: "head",
      devboxId: headBox.id,
      exactSha: run.pullRequest.headSha,
    });

    const gateway = await findOpenAiGatewayBinding();
    browserBox = await createBrowserEnvironment(run, gateway);
    const codexVersion = await executeText(browserBox, "codex --version");
    const browserEnvironment: BrowserEnvironment = {
      role: "browser",
      devboxId: browserBox.id,
      status: "running",
      detail: "Default Runloop image with public Codex agent mount, OpenAI Agent Gateway, Playwright, and Chromium.",
    };
    await saveEnvironment(axonId, attemptId, run.id, browserEnvironment, new Date().toISOString());
    await emit({
      ...state,
      status: "discovering",
      environments: [baseEnvironment, headEnvironment, browserEnvironment],
      browserAgent: {
        devboxId: browserBox.id,
        agentName: "codex",
        transport: "agent_mount",
        version: codexVersion,
      },
    });
    await publishRunEvent(axonId, "journey.discovery_started", "AGENT_EVENT", {
      runId: run.id,
      attemptId,
      missionId: mission.id,
      browserDevboxId: browserBox.id,
      target: mission.kind === "regression" ? "base" : "head",
    });

    const discoveryTarget: ExecutionTarget = mission.kind === "regression" ? "base" : "head";
    const discoveryBox = discoveryTarget === "base" ? baseBox : headBox;
    const discoveryUrl = discoveryTarget === "base" ? baseUrl : headUrl;
    if (mission.kind === "regression") {
      await resetFixtures(discoveryBox, configuration.profile, discoveryTarget);
      await publishRunEvent(axonId, "fixture.reset_completed", "EXTERNAL_EVENT", {
        runId: run.id,
        attemptId,
        missionId: mission.id,
        target: discoveryTarget,
        phase: "discovery",
      });
    }
    const journey = await discoverJourney(
      browserBox,
      mission,
      configuration.profile,
      configuration.appMap,
      discoveryTarget,
      discoveryUrl,
    );
    validateJourneyForReplay(journey, mission, configuration.profile, discoveryUrl);
    await saveJourney(axonId, attemptId, run.id, journey, new Date().toISOString());
    await emit({ ...state, journey });
    await publishRunEvent(axonId, "journey.frozen", "AGENT_EVENT", {
      runId: run.id,
      attemptId,
      missionId: mission.id,
      discoveredAgainst: discoveryTarget,
      stepCount: journey.steps.length,
      producer: journey.producer,
    });

    await emit({ ...state, status: "executing" });
    if (mission.kind === "intent") {
      const head = await runTarget(headBox, "head", headUrl, journey);
      const infrastructureFailure =
        head.result.status === "error" ||
        head.result.status === "blocked" ||
        (head.result.evidenceErrors?.length ?? 0) > 0;
      await emit({
        ...state,
        status: infrastructureFailure ? "blocked" : "complete",
        execution: head.result,
        actions: head.replay?.actions ?? [],
        network: head.replay?.network ?? [],
        blocker: infrastructureFailure
          ? {
              code:
                head.result.error?.code ??
                head.result.evidenceErrors?.[0]?.code ??
                "intent_execution_incomplete",
              message:
                head.result.error?.message ??
                head.result.evidenceErrors?.[0]?.message ??
                "Intent execution did not produce complete evidence.",
              retryable: true,
            }
          : undefined,
      });
      return state;
    }

    const base = await runTarget(baseBox, "base", baseUrl, journey);
    await emit({
      ...state,
      executions: { base: base.result },
      actions: base.replay?.actions ?? [],
      network: base.replay?.network ?? [],
    });
    const head = await runTarget(headBox, "head", headUrl, journey);
    const comparison = compareRegressionExecutions(
      mission,
      base.result,
      head.result,
      { base: baseUrl, head: headUrl },
    );
    await saveRegressionComparison(axonId, comparison);
    await publishRunEvent(axonId, "regression.compared", "EXTERNAL_EVENT", {
      runId: run.id,
      attemptId,
      missionId: mission.id,
      comparisonId: comparison.comparisonId,
      baseExecutionId: comparison.baseExecutionId,
      headExecutionId: comparison.headExecutionId,
      verdict: comparison.verdict,
      firstDivergence: comparison.firstDivergence
        ? {
            stage: comparison.firstDivergence.stage,
            stepIndex: comparison.firstDivergence.stepIndex,
            assertionId: comparison.firstDivergence.assertionId,
          }
        : undefined,
    });
    const infrastructureFailure = [base.result, head.result].some(
      (result) =>
        result.status === "error" ||
        result.status === "blocked" ||
        (result.evidenceErrors?.length ?? 0) > 0,
    );
    await emit({
      ...state,
      status: infrastructureFailure ? "blocked" : "complete",
      executions: { base: base.result, head: head.result },
      comparison,
      actions: [...(base.replay?.actions ?? []), ...(head.replay?.actions ?? [])],
      network: [...(base.replay?.network ?? []), ...(head.replay?.network ?? [])],
      blocker: infrastructureFailure
        ? {
            code: "regression_execution_incomplete",
            message: "A runner, setup, or evidence persistence failure made the comparison inconclusive.",
            retryable: true,
          }
        : undefined,
    });
    return state;
  } catch (error) {
    const blocker = browserBlocker(error);
    const failed = BrowserVerificationSchema.parse({
      ...state,
      status: blocker.retryable ? "blocked" : "failed",
      blocker,
    });
    await emit(failed);
    await publishRunEvent(axonId, "browser_verification.blocked", "EXTERNAL_EVENT", {
      runId: run.id,
      code: blocker.code,
    }).catch((publishError: unknown) => {
      console.error("Failed to publish browser verification blocker.", publishError);
    });
    return failed;
  } finally {
    await suspendEnvironment(browserBox, "browser", run, attemptId, axonId, state, emit);
    await suspendEnvironment(headBox, "head", run, attemptId, axonId, state, emit);
    await suspendEnvironment(baseBox, "base", run, attemptId, axonId, state, emit);
    const failedCleanup = state.environments.filter(
      (environment) => environment.status === "failed",
    );
    if (failedCleanup.length > 0 && state.status === "complete") {
      await emit({
        ...state,
        status: "blocked",
        blocker: {
          code: "devbox_cleanup_failed",
          message: `Runloop cleanup could not be confirmed for: ${failedCleanup
            .map((environment) => environment.role)
            .join(", ")}.`,
          retryable: true,
        },
      });
    }
  }
}

export function resolveMission(configured: TestMission, run: Run): TestMission {
  const approvedClaims = run.intentSpec?.claims ?? [];
  const knownClaimIds = new Set(approvedClaims.map((claim) => claim.id));
  let claimIds = configured.claimIds;
  if (configured.claimSourceQuote) {
    const matches = approvedClaims.filter(
      (claim) => claim.sourceQuote === configured.claimSourceQuote,
    );
    if (matches.length === 0) {
      throw new GroundtruthError(
        "test_mission_claim_source_quote_unmatched",
        "The trusted TestMission source quote does not exactly match an approved intent claim.",
        422,
      );
    }
    if (matches.length !== 1) {
      throw new GroundtruthError(
        "test_mission_claim_source_quote_ambiguous",
        "The trusted TestMission source quote matches more than one approved intent claim.",
        422,
      );
    }
    claimIds = [matches[0].id];
  }
  if (claimIds.length === 0) {
    throw new GroundtruthError(
      "test_mission_claim_missing",
      "The trusted TestMission must explicitly reference one approved intent claim.",
      422,
    );
  }
  if (claimIds.length !== 1) {
    throw new GroundtruthError(
      "test_mission_claim_scope_unsupported",
      "This prototype requires exactly one approved intent claim per TestMission.",
      422,
    );
  }
  if (claimIds.some((claimId) => !knownClaimIds.has(claimId))) {
    throw new GroundtruthError(
      "test_mission_claim_stale",
      "The trusted TestMission does not reference an approved intent claim.",
      422,
    );
  }
  const deferredClaimIds = configured.deferredClaims?.map((claim) => claim.claimId) ?? [];
  if (
    new Set(deferredClaimIds).size !== deferredClaimIds.length ||
    deferredClaimIds.some(
      (claimId) => claimIds.includes(claimId) || !knownClaimIds.has(claimId),
    )
  ) {
    throw new GroundtruthError(
      "test_mission_deferral_stale",
      "Trusted claim deferrals must be unique, valid, and separate from the executed claim.",
      422,
    );
  }
  if (
    configured.assertions.some(
      (assertion) =>
        (assertion.kind === "url" && (!assertion.operator || assertion.expected === undefined)) ||
        (assertion.kind === "text" &&
          (!assertion.operator || assertion.expected === undefined)) ||
        (assertion.kind === "network" && assertion.expectedStatus === undefined),
    )
  ) {
    throw new GroundtruthError(
      "test_mission_assertion_incomplete",
      "Intent assertions must include explicit expected values and operators.",
      422,
    );
  }
  return { ...configured, claimIds };
}

export function resolveRegressionMission(
  configured: TestMission,
  configuration: Extract<AppConfiguration, { ready: true }>,
): TestMission {
  if (
    configured.kind !== "regression" ||
    configured.claimIds.length !== 0 ||
    configured.assertions.some(
      (assertion) => !assertion.id || !assertion.behavior || !assertion.comparison,
    )
  ) {
    throw new GroundtruthError(
      "regression_mission_invalid",
      "A regression mission requires zero claim IDs and named comparison assertions.",
      422,
    );
  }
  if (!hasTrustedRegressionImpact(configured, configuration.impactMap, configuration.appMap)) {
    throw new GroundtruthError(
      "regression_mission_unrelated",
      "The regression mission is not supported by trusted impact and AppMap evidence.",
      422,
    );
  }
  return configured;
}

async function createApplicationEnvironment(
  run: Run,
  profile: AppProfile,
  target: "base" | "head",
  sha: string,
): Promise<Devbox> {
  const sdk = getRunloopClient();
  const box = await sdk.devbox.create({
    name: `groundtruth-${run.id}-${target}`,
    snapshot_id: profile.workspace.preparedSnapshotId,
    tunnel: { auth_mode: "open" },
    launch_parameters: {
      after_idle: { idle_time_seconds: 900, on_idle: "suspend" },
    },
  });
  try {
    const directory = homePath(profile.workspace.workingDirectory);
    await executeChecked(
      box,
      [
        `git clone --filter=blob:none --no-checkout ${shellQuote(run.repository.cloneUrl)} ${shellQuote(directory)}`,
        `cd ${shellQuote(directory)}`,
        `git fetch --depth=1 origin ${shellQuote(sha)}`,
        `git checkout --detach ${shellQuote(sha)}`,
        `test "$(git rev-parse HEAD)" = ${shellQuote(sha)}`,
        profile.workspace.installCommand,
      ].join(" && "),
      "exact-SHA application setup",
    );
    const startScript = `${profile.workspace.startCommand}\n`;
    await box.file.write({ file_path: "/home/user/groundtruth-start.sh", contents: startScript });
    await box.cmd.execAsync(
      `cd ${shellQuote(directory)} && exec sh /home/user/groundtruth-start.sh > /tmp/groundtruth-app.log 2>&1`,
    );
    await executeChecked(
      box,
      `for i in $(seq 1 60); do curl -fsS ${shellQuote(`http://127.0.0.1:${profile.workspace.port}${profile.workspace.healthPath}`)} >/dev/null && exit 0; sleep 2; done; tail -n 80 /tmp/groundtruth-app.log >&2; exit 1`,
      "application health check",
    );
    return box;
  } catch (error) {
    await box.shutdown().catch(() => undefined);
    throw error;
  }
}

async function resetFixtures(
  box: Devbox,
  profile: AppProfile,
  target: ExecutionTarget,
): Promise<void> {
  await executeChecked(
    box,
    `cd ${shellQuote(homePath(profile.workspace.workingDirectory))} && ${profile.fixtures.resetCommand}`,
    `${target} fixture reset`,
  );
}

type GatewayBinding = { gatewayId: string; secretName: string };

async function findOpenAiGatewayBinding(): Promise<GatewayBinding> {
  const sdk = getRunloopClient();
  const gatewayConfigs = await sdk.gatewayConfig.list();
  let gatewayId: string | undefined;
  for (const gateway of gatewayConfigs) {
    const info = await gateway.getInfo();
    const endpoint = new URL(info.endpoint);
    if (
      endpoint.protocol === "https:" &&
      endpoint.hostname.toLowerCase() === "api.openai.com" &&
      info.auth_mechanism.type === "bearer"
    ) {
      gatewayId = gateway.id;
      break;
    }
  }
  const secrets = await sdk.secret.list();
  const configuredSecretName = process.env.RUNLOOP_OPENAI_SECRET_NAME?.trim();
  const namedSecrets = secrets.filter(
    (candidate) => !/(?:^|_)(?:test|dummy|example)(?:_|$)/i.test(candidate.name),
  );
  const secret =
    configuredSecretName
      ? namedSecrets.find((candidate) => candidate.name === configuredSecretName)
      : namedSecrets.find((candidate) => /openai|codex|gpt/i.test(candidate.name));
  if (!gatewayId || !secret) {
    throw new GroundtruthError(
      "codex_gateway_missing",
      "A bearer-authenticated Runloop OpenAI GatewayConfig and an OpenAI/Codex-named account Secret are required for Codex discovery. Set RUNLOOP_OPENAI_SECRET_NAME when the compatible Secret uses another name.",
      503,
      true,
    );
  }
  return { gatewayId, secretName: secret.name };
}

async function createBrowserEnvironment(
  run: Run,
  gateway: GatewayBinding,
): Promise<Devbox> {
  const sdk = getRunloopClient();
  const box = await sdk.devbox.create({
    name: `groundtruth-${run.id}-browser`,
    mounts: [{ type: "agent_mount", agent_name: "codex", agent_id: null }],
    gateways: {
      OPENAI: { gateway: gateway.gatewayId, secret: gateway.secretName },
    },
    launch_parameters: {
      after_idle: { idle_time_seconds: 900, on_idle: "suspend" },
      launch_commands: [
        `mkdir -p "$HOME/.codex" && umask 077 && printf 'model_provider = "runloop"\\n\\n[model_providers.runloop]\\nname = "Runloop OpenAI Gateway"\\nbase_url = "%s/v1"\\nenv_key = "OPENAI"\\nwire_api = "responses"\\n' "$OPENAI_URL" > "$HOME/.codex/config.toml"`,
      ],
    },
  });
  try {
    await executeChecked(
      box,
      "mkdir -p /home/user/groundtruth-runner && cd /home/user/groundtruth-runner && test -f package.json || npm init -y >/dev/null && npm install --no-audit --no-fund playwright@1.55.0 && npx playwright install --with-deps chromium",
      "Playwright and Chromium setup",
    );
    return box;
  } catch (error) {
    await box.shutdown().catch(() => undefined);
    throw error;
  }
}

async function discoverJourney(
  box: Devbox,
  mission: TestMission,
  profile: AppProfile,
  appMap: AppMap,
  target: ExecutionTarget,
  applicationUrl: string,
): Promise<ExecutableJourney> {
  const directory = "/home/user/groundtruth-runner";
  await Promise.all([
    box.file.write({
      file_path: `${directory}/mission.json`,
      contents: `${JSON.stringify(mission, null, 2)}\n`,
    }),
    box.file.write({
      file_path: `${directory}/app-map.json`,
      contents: `${JSON.stringify(appMap, null, 2)}\n`,
    }),
  ]);
  const prompt = [
    "You are the browser explorer for a Groundtruth verification mission.",
    "Use the installed Playwright package and Chromium to inspect the live application yourself.",
    "You must execute browser inspection commands against the supplied URL before answering.",
    "Do not infer the journey from source code and do not return a canned journey.",
    `Live ${target} URL: ${applicationUrl}`,
    `Approved TestMission: ${JSON.stringify(mission)}`,
    `Candidate routes: ${JSON.stringify(appMap.routes.map((route) => route.path))}`,
    `Fixture reset reference: ${profile.fixtures.resetCommand}`,
    `Auth reference: ${JSON.stringify(profile.auth)}`,
    `Safety policy: ${JSON.stringify(profile.safety)}`,
    "Return JSON only, without Markdown fences.",
    `Exact JSON Schema: ${JSON.stringify(z.toJSONSchema(JourneyDraftSchema))}`,
    "The step discriminator property is action, not type or name.",
    'Valid examples: {"action":"goto","path":"/"}, {"action":"click","locator":{"by":"role","role":"button","name":"Add to cart"}}, {"action":"fill","locator":{"by":"test_id","value":"promo-code"},"fixtureValueKey":"validPromoCode"}, {"action":"press","locator":{"by":"test_id","value":"promo-code"},"key":"Tab"}, {"action":"wait_for","locator":{"by":"test_id","value":"promo-applied"},"state":"visible"}.',
    "Allowed locators are role {role,name?}, text {text,exact?}, test_id {value}, and css {value}.",
    "Use only paths and elements you actually observed.",
    `Set discoveredAgainst to "${target}".`,
    mission.kind === "regression"
      ? "Choose an existing behavior available in this base environment. Product identity and observed values must come from live inspection, not from assumptions or configured expected values."
      : "Discover the approved intent behavior against the pull request head.",
    "Use wait_for only for prerequisite UI readiness, never to encode the final asserted outcome. End the journey immediately after triggering the behavior under test; the mechanical runner owns verdict assertions.",
    "Never visit blocked path prefixes. State-changing requests are allowed only when the supplied safety policy explicitly allows them.",
  ].join("\n");
  await box.file.write({ file_path: `${directory}/discovery-prompt.txt`, contents: prompt });
  let correctionPrompt: string | undefined;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const promptFile = correctionPrompt ? "correction-prompt.txt" : "discovery-prompt.txt";
    if (correctionPrompt) {
      await box.file.write({
        file_path: `${directory}/${promptFile}`,
        contents: correctionPrompt,
      });
    }
    const outputFile = `journey-output-${attempt}.json`;
    await executeChecked(
      box,
      `cd ${directory} && codex exec --skip-git-repo-check --sandbox danger-full-access --output-last-message ${outputFile} "$(cat ${promptFile})"`,
      attempt === 0 ? "Codex live journey discovery" : "Codex journey contract correction",
    );
    const raw = await box.file.read({ file_path: `${directory}/${outputFile}` });
    const parsed = parseJsonObject(raw, "Codex journey output");
    const candidate = ExecutableJourneySchema.safeParse({
      ...parsed,
      producer: { kind: "codex", agentId: `runloop:${box.id}` },
    });
    if (candidate.success) {
      return candidate.data;
    }
    correctionPrompt = [
      "You previously inspected the live application and produced the JSON below.",
      "It failed the frozen ExecutableJourney contract. Correct only its representation; do not invent unobserved steps.",
      `Validation issues: ${JSON.stringify(candidate.error.issues)}`,
      `Invalid JSON: ${JSON.stringify(parsed)}`,
      `Exact JSON Schema: ${JSON.stringify(z.toJSONSchema(JourneyDraftSchema))}`,
      "Return corrected JSON only, without Markdown fences.",
    ].join("\n");
  }
  throw new GroundtruthError(
    "invalid_executable_journey",
    "Codex could not correct its live-discovered journey to the ExecutableJourney contract.",
    422,
    true,
  );
}

async function replayJourney(
  box: Devbox,
  mission: TestMission,
  journey: ExecutableJourney,
  profile: AppProfile,
  target: ExecutionTarget,
  applicationUrl: string,
): Promise<z.infer<typeof RunnerOutputSchema>> {
  const directory = "/home/user/groundtruth-runner";
  const outputDirectory = `${directory}/artifacts-${target}`;
  await Promise.all([
    box.file.write({
      file_path: `${directory}/mission.json`,
      contents: `${JSON.stringify(mission, null, 2)}\n`,
    }),
    box.file.write({
      file_path: `${directory}/journey.json`,
      contents: `${JSON.stringify(journey, null, 2)}\n`,
    }),
    box.file.write({
      file_path: `${directory}/safety.json`,
      contents: `${JSON.stringify(profile.safety, null, 2)}\n`,
    }),
    box.file.write({ file_path: `${directory}/replay.mjs`, contents: PLAYWRIGHT_RUNNER }),
  ]);
  await executeChecked(
    box,
    `cd ${directory} && APPLICATION_URL=${shellQuote(applicationUrl)} EXECUTION_TARGET=${shellQuote(target)} OUTPUT_DIRECTORY=${shellQuote(outputDirectory)} node replay.mjs`,
    `${target} mechanical Playwright replay`,
  );
  const raw = await box.file.read({ file_path: `${directory}/result-${target}.json` });
  return RunnerOutputSchema.parse(JSON.parse(raw));
}

async function persistReplayArtifacts(
  box: Devbox,
  run: Run,
  missionId: string,
  attemptId: string,
  executionId: string,
  target: ExecutionTarget,
  replay: z.infer<typeof RunnerOutputSchema>,
  axonId: string,
): Promise<ExecutionResult> {
  const sdk = getRunloopClient();
  const artifactFailures: string[] = [];
  const uploadText = async (kind: string, filePath: string, mimeType: string): Promise<string> => {
    const text = await box.file.read({ file_path: filePath });
    const object = await sdk.storageObject.uploadFromText(
      text,
      `groundtruth-${run.id}-${executionId}-${kind}.json`,
      { metadata: { run_id: run.id, execution_id: executionId, kind, mime_type: mimeType } },
    );
    await saveArtifact(axonId, object.id, executionId, kind, object.id);
    return object.id;
  };
  const uploadBinary = async (
    kind: string,
    filePath: string,
    mimeType: string,
  ): Promise<string> => {
    const response = await box.file.download({ path: filePath });
    const object = await sdk.storageObject.uploadFromBuffer(
      Buffer.from(await response.arrayBuffer()),
      `groundtruth-${run.id}-${executionId}-${kind}-${filePath.split("/").at(-1)}`,
      "binary",
      { metadata: { run_id: run.id, execution_id: executionId, kind, mime_type: mimeType } },
    );
    await saveArtifact(axonId, object.id, executionId, kind, object.id);
    return object.id;
  };
  const retainPartial = async (
    kind: string,
    upload: () => Promise<string>,
  ): Promise<string | undefined> => {
    try {
      return await upload();
    } catch (error) {
      artifactFailures.push(
        `${kind}: ${sanitize(error instanceof Error ? error.message : "artifact persistence failed")}`,
      );
      return undefined;
    }
  };

  const screenshotArtifactIds = await Promise.all(
    replay.files.screenshots.map((filePath, index) =>
      retainPartial(`screenshot-${index}`, () =>
        uploadBinary(`screenshot-${index}`, filePath, "image/png"),
      ),
    ),
  ).then((ids) => ids.filter((id): id is string => Boolean(id)));
  const [actionArtifactId, consoleArtifactId, pageErrorArtifactId, networkArtifactId] = await Promise.all([
    retainPartial("actions", () => uploadText("actions", replay.files.actions, "application/json")),
    retainPartial("console", () => uploadText("console", replay.files.console, "application/json")),
    retainPartial("page-errors", () =>
      uploadText("page-errors", replay.files.errors, "application/json"),
    ),
    retainPartial("network", () => uploadText("network", replay.files.network, "application/json")),
  ]);
  const traceArtifactId = replay.files.trace
    ? await retainPartial("trace", () =>
        uploadBinary("trace", replay.files.trace!, "application/zip"),
      )
    : undefined;
  const videoArtifactId = replay.files.video
    ? await retainPartial("video", () =>
        uploadBinary("video", replay.files.video!, "video/webm"),
      )
    : undefined;

  return ExecutionResultSchema.parse({
    schemaVersion: 1,
    attemptId,
    executionId,
    missionId,
    target,
    status: replay.status,
    startedAt: replay.startedAt,
    endedAt: replay.endedAt,
    steps: replay.steps.map(({ index, status, message }) => ({ index, status, message })),
    checks: replay.checks,
    evidence: {
      videoArtifactId,
      traceArtifactId,
      screenshotArtifactIds,
      actionArtifactId,
      consoleArtifactId,
      pageErrorArtifactId,
      networkArtifactId,
    },
    evidenceErrors:
      artifactFailures.length > 0
        ? [
            {
              code: "artifact_persistence_failed",
              message: `${artifactFailures.length} artifact(s) could not be persisted: ${artifactFailures.join("; ")}`,
            },
          ]
        : undefined,
    error: replay.error,
  });
}

function failedExecution(
  missionId: string,
  attemptId: string,
  executionId: string,
  target: ExecutionTarget,
  error: unknown,
): ExecutionResult {
  const blocker = browserBlocker(error);
  const now = new Date().toISOString();
  return ExecutionResultSchema.parse({
    schemaVersion: 1,
    missionId,
    attemptId,
    executionId,
    target,
    status: "error",
    startedAt: now,
    endedAt: now,
    steps: [],
    checks: [],
    evidence: { screenshotArtifactIds: [] },
    error: { code: blocker.code, message: blocker.message },
  });
}

async function suspendEnvironment(
  box: Devbox | undefined,
  role: BrowserEnvironment["role"],
  run: Run,
  attemptId: string,
  axonId: string,
  state: BrowserVerification,
  emit: ProgressCallback,
): Promise<void> {
  if (!box) {
    return;
  }
  let status: BrowserEnvironment["status"] = "suspended";
  let detail: string | undefined;
  try {
    await box.suspend();
  } catch (error) {
    const suspendDetail =
      error instanceof Error ? error.message : "Devbox suspension failed.";
    try {
      await box.shutdown();
      status = "shutdown";
      detail = `Suspension failed; shutdown fallback succeeded. ${suspendDetail}`;
    } catch (shutdownError) {
      status = "failed";
      const shutdownDetail =
        shutdownError instanceof Error ? shutdownError.message : "Devbox shutdown failed.";
      detail = `Suspension and shutdown failed. ${suspendDetail} ${shutdownDetail}`;
    }
  }
  const environments = state.environments.map((environment) =>
    environment.devboxId === box.id ? { ...environment, status, detail: detail ?? environment.detail } : environment,
  );
  const environment = environments.find((candidate) => candidate.devboxId === box.id);
  if (environment) {
    try {
      await saveEnvironment(axonId, attemptId, run.id, environment, new Date().toISOString());
      await emit({ ...state, environments });
    } catch (error) {
      console.error("Failed to persist final environment cleanup state.", error);
      const failedEnvironments = environments.map((candidate) =>
        candidate.devboxId === box.id
          ? {
              ...candidate,
              status: "failed" as const,
              detail: `${candidate.detail ?? ""} Cleanup completed with status ${status}, but its audit state could not be persisted.`.trim(),
            }
          : candidate,
      );
      await emit({ ...state, environments: failedEnvironments }).catch((emitError: unknown) => {
        console.error("Failed to project cleanup audit failure.", emitError);
      });
    }
  }
}

async function executeChecked(box: Devbox, command: string, label: string): Promise<void> {
  const result = await box.cmd.exec(command, { optimistic_timeout: 25 });
  if (result.exitCode !== 0) {
    const stderr = (await result.stderr(8)).trim();
    throw new GroundtruthError(
      "runloop_command_failed",
      `${label} failed in Runloop${stderr ? `: ${sanitize(stderr)}` : "."}`,
      502,
      true,
    );
  }
}

async function executeText(box: Devbox, command: string): Promise<string> {
  const result = await box.cmd.exec(command, { optimistic_timeout: 25 });
  if (result.exitCode !== 0) {
    throw new GroundtruthError("runloop_command_failed", "A Runloop command failed.", 502, true);
  }
  return (await result.stdout(10)).trim();
}

function parseJsonObject(value: string, label: string): Record<string, unknown> {
  const trimmed = value.trim().replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "");
  try {
    const parsed: unknown = JSON.parse(trimmed);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      throw new Error("Expected an object.");
    }
    return parsed as Record<string, unknown>;
  } catch {
    throw new GroundtruthError(
      "invalid_executable_journey",
      `${label} was not valid JSON matching the ExecutableJourney contract.`,
      422,
      true,
    );
  }
}

function homePath(relativePath: string): string {
  return `/home/user/${relativePath.replaceAll("\\", "/")}`;
}

function shellQuote(value: string): string {
  return `'${value.replaceAll("'", `'\"'\"'`)}'`;
}

function sanitize(value: string): string {
  return value
    .replace(/(sk-|sess-|eyJ)[A-Za-z0-9._-]+/g, "[redacted]")
    .replace(/Bearer\s+\S+/gi, "Bearer [redacted]")
    .slice(0, 2000);
}

function browserBlocker(error: unknown): {
  code: string;
  message: string;
  retryable: boolean;
} {
  if (error instanceof GroundtruthError) {
    return { code: error.code, message: error.message, retryable: error.retryable };
  }
  console.error("Browser verification failed.", error);
  return {
    code: "browser_verification_failed",
    message: "Browser verification failed in an external integration.",
    retryable: true,
  };
}

function countEvidence(result: ExecutionResult): number {
  const evidence = result.evidence;
  return (
    evidence.screenshotArtifactIds.length +
    [
      evidence.videoArtifactId,
      evidence.traceArtifactId,
      evidence.actionArtifactId,
      evidence.consoleArtifactId,
      evidence.pageErrorArtifactId,
      evidence.networkArtifactId,
    ].filter(Boolean).length
  );
}

const PLAYWRIGHT_RUNNER = String.raw`
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { chromium } from "playwright";

const mission = JSON.parse(await readFile("mission.json", "utf8"));
const journey = JSON.parse(await readFile("journey.json", "utf8"));
const safety = JSON.parse(await readFile("safety.json", "utf8"));
const applicationUrl = process.env.APPLICATION_URL;
const target = process.env.EXECUTION_TARGET;
const outputDirectory = process.env.OUTPUT_DIRECTORY;
if (!applicationUrl) throw new Error("APPLICATION_URL is required.");
if (!["base", "head"].includes(target)) throw new Error("EXECUTION_TARGET must be base or head.");
if (!outputDirectory) throw new Error("OUTPUT_DIRECTORY is required.");
const applicationOrigin = new URL(applicationUrl);

const artifacts = path.resolve(outputDirectory);
await mkdir(artifacts, { recursive: true });
const startedAt = new Date().toISOString();
const actions = [];
const network = [];
const consoleEvents = [];
const pageErrors = [];
const steps = [];
const checks = [];
const screenshotFiles = [];
let error;
let status = "passed";
let productFailure = false;
let videoPath;
const tracePath = path.join(artifacts, "trace.zip");
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  baseURL: applicationUrl,
  recordVideo: { dir: path.join(artifacts, "video") },
});
await context.tracing.start({ screenshots: true, snapshots: true, sources: false });
function isAllowedHost(hostname, patterns) {
  const normalizedHostname = hostname.toLowerCase();
  return patterns.some((pattern) => {
    const normalizedPattern = pattern.toLowerCase();
    if (normalizedPattern.startsWith("*.")) {
      const suffix = normalizedPattern.slice(1);
      return normalizedHostname.endsWith(suffix) && normalizedHostname.length > suffix.length;
    }
    return normalizedHostname === normalizedPattern;
  });
}
await context.route("**/*", async (route) => {
  const request = route.request();
  const url = new URL(request.url());
  const method = request.method().toUpperCase();
  const allowed =
    ["http:", "https:"].includes(url.protocol) &&
    url.origin === applicationOrigin.origin &&
    isAllowedHost(url.hostname, safety.allowedHosts) &&
    !safety.blockedPathPrefixes.some((prefix) => url.pathname.startsWith(prefix)) &&
    (safety.allowStateChangingRequests || ["GET", "HEAD", "OPTIONS"].includes(method));
  if (!allowed) {
    await route.abort("blockedbyclient");
    return;
  }
  await route.continue();
});
const page = await context.newPage();
const video = page.video();
page.on("console", (message) => {
  consoleEvents.push({ at: new Date().toISOString(), level: message.type(), text: message.text() });
});
page.on("pageerror", (cause) => {
  pageErrors.push({ at: new Date().toISOString(), message: cause.message });
});
page.on("response", (response) => {
  network.push({
    method: response.request().method(),
    url: response.url(),
    status: response.status(),
    target,
  });
});

function locator(spec) {
  if (spec.by === "role") return page.getByRole(spec.role, spec.name ? { name: spec.name } : undefined);
  if (spec.by === "text") return page.getByText(spec.text, { exact: spec.exact });
  if (spec.by === "test_id") return page.getByTestId(spec.value);
  return page.locator(spec.value);
}

try {
  for (let index = 0; index < journey.steps.length; index += 1) {
    const step = journey.steps[index];
    const at = new Date().toISOString();
    const summary = step.action === "goto" ? "Navigate to " + step.path : step.action;
    try {
      if (step.action === "goto") await page.goto(step.path, { waitUntil: "networkidle" });
      else if (step.action === "click") await locator(step.locator).click();
      else if (step.action === "fill") {
        const fixture = mission.fixtureValues?.[step.fixtureValueKey];
        if (typeof fixture !== "string") throw new Error("Unknown fixture key: " + step.fixtureValueKey);
        await locator(step.locator).fill(fixture);
      } else if (step.action === "press") await locator(step.locator).press(step.key);
      else if (step.action === "wait_for") await locator(step.locator).waitFor({ state: step.state });
      const screenshot = path.join(artifacts, "step-" + index + ".png");
      await page.screenshot({ path: screenshot, fullPage: true });
      screenshotFiles.push(screenshot);
      steps.push({ index, status: "passed", at, summary });
      actions.push({ at, target, summary, status: "passed" });
    } catch (cause) {
      status = "failed";
      productFailure = true;
      const message = cause instanceof Error ? cause.message : "Step failed.";
      const screenshot = path.join(artifacts, "step-" + index + "-failed.png");
      await page.screenshot({ path: screenshot, fullPage: true }).then(
        () => screenshotFiles.push(screenshot),
        () => undefined,
      );
      steps.push({ index, status: "failed", message, at, summary });
      actions.push({ at, target, summary, status: "failed" });
      throw cause;
    }
  }

  for (let index = 0; index < mission.assertions.length; index += 1) {
    const assertion = mission.assertions[index];
    let passed = false;
    let actual;
    try {
      if (assertion.kind === "url") {
        actual = page.url();
        passed = assertion.expected === undefined
          ? true
          : assertion.operator === "equals"
            ? actual === new URL(assertion.expected, applicationUrl).toString()
            : new RegExp(assertion.expected).test(actual);
      } else if (assertion.kind === "dom") {
        actual = await locator(assertion.locator).isVisible();
        passed = assertion.state === "visible" ? actual : !actual;
      } else if (assertion.kind === "text") {
        actual = await locator(assertion.locator).textContent();
        passed = assertion.expected === undefined
          ? actual !== null
          : assertion.operator === "equals"
            ? actual === assertion.expected
            : String(actual).includes(assertion.expected);
      } else if (assertion.kind === "network") {
        actual = network.filter((entry) =>
          entry.method === assertion.method && new RegExp(assertion.urlPattern).test(entry.url)
        );
        passed = assertion.expectedStatus === undefined
          ? actual.length > 0
          : actual.some((entry) => entry.status === assertion.expectedStatus);
      } else if (assertion.kind === "console") {
        actual = consoleEvents.filter((entry) => entry.level === "error").length;
        passed = actual <= assertion.maximumCount;
      } else {
        actual = pageErrors.length;
        passed = actual <= assertion.maximumCount;
      }
    } catch (cause) {
      actual = {
        error: cause instanceof Error ? cause.message : "Assertion observation failed.",
      };
      passed = false;
    }
    checks.push({
      assertionIndex: index,
      assertionId: assertion.id ?? "assertion-" + index,
      behavior: assertion.behavior ?? "Mission assertion " + (index + 1),
      comparison: assertion.comparison ?? "pass_only",
      passed,
      actual,
    });
    if (!passed) status = "failed";
  }
} catch (cause) {
  error = {
    code: productFailure ? "journey_step_failed" : "mechanical_replay_failed",
    message: cause instanceof Error ? cause.message : "Replay failed.",
  };
  if (!productFailure) status = "error";
} finally {
  await context.tracing.stop({ path: tracePath }).catch(() => undefined);
  await context.close();
  videoPath = await video?.path().catch(() => undefined);
  await browser.close();
}

const actionPath = path.join(artifacts, "actions.json");
const consolePath = path.join(artifacts, "console.json");
const errorPath = path.join(artifacts, "page-errors.json");
const networkPath = path.join(artifacts, "network.json");
await Promise.all([
  writeFile(actionPath, JSON.stringify(actions, null, 2)),
  writeFile(consolePath, JSON.stringify(consoleEvents, null, 2)),
  writeFile(errorPath, JSON.stringify(pageErrors, null, 2)),
  writeFile(networkPath, JSON.stringify(network, null, 2)),
]);
const result = {
  status,
  startedAt,
  endedAt: new Date().toISOString(),
  steps,
  checks,
  actions,
  network,
  files: {
    screenshots: screenshotFiles,
    trace: tracePath,
    video: videoPath,
    actions: actionPath,
    console: consolePath,
    errors: errorPath,
    network: networkPath,
  },
  error,
};
await writeFile("result-" + target + ".json", JSON.stringify(result, null, 2));
`;
