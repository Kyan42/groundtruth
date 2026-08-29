import { randomUUID } from "node:crypto";

import type { Devbox } from "@runloop/api-client";
import { z } from "zod";

import type { AppConfiguration } from "@/lib/config/app-config";
import { GroundtruthError } from "@/lib/domain/errors";
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
      passed: z.boolean(),
      actual: z.unknown(),
    }),
  ),
  actions: z.array(
    z.object({
      at: z.iso.datetime(),
      target: z.literal("head"),
      summary: z.string().min(1),
      status: z.string().min(1),
    }),
  ),
  network: z.array(
    z.object({
      method: z.string().min(1),
      url: z.string().min(1),
      status: z.number().int().min(100).max(599),
      target: z.literal("head"),
    }),
  ),
  files: z.object({
    screenshots: z.array(z.string().min(1)),
    trace: z.string().min(1).optional(),
    video: z.string().min(1).optional(),
    actions: z.string().min(1),
    console: z.string().min(1),
    network: z.string().min(1),
  }),
  error: z.object({ code: z.string().min(1), message: z.string().min(1) }).optional(),
});
const JourneyDraftSchema = ExecutableJourneySchema.omit({ producer: true });

type ProgressCallback = (state: BrowserVerification) => Promise<void>;

export async function executeBrowserVerification(
  run: Run,
  configuration: Extract<AppConfiguration, { ready: true }>,
  onProgress: ProgressCallback,
): Promise<BrowserVerification> {
  if (!run.coordinationAxonId || !run.intentSpec || !run.intentApproval) {
    throw new GroundtruthError(
      "browser_prerequisites_missing",
      "An approved intent and coordination Axon are required for browser verification.",
      409,
    );
  }
  const mission = resolveMission(configuration.mission, run);
  const axonId = run.coordinationAxonId;
  let state = BrowserVerificationSchema.parse({
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

  try {
    if (configuration.profile.auth.mode !== "none") {
      throw new GroundtruthError(
        "browser_auth_mode_unsupported",
        "This prototype does not yet support storage-state authentication.",
        422,
      );
    }
    await ensureBrowserTables(axonId);
    await saveMission(axonId, mission, new Date().toISOString());
    await publishRunEvent(axonId, "mission.loaded", "EXTERNAL_EVENT", {
      runId: run.id,
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
    await saveEnvironment(axonId, run.id, baseEnvironment, new Date().toISOString());
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
    await saveEnvironment(axonId, run.id, headEnvironment, new Date().toISOString());
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
    await saveEnvironment(axonId, run.id, browserEnvironment, new Date().toISOString());
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
      missionId: mission.id,
      browserDevboxId: browserBox.id,
    });

    const journey = await discoverJourney(
      browserBox,
      mission,
      configuration.profile,
      configuration.appMap,
      headUrl,
    );
    validateJourneyForReplay(journey, mission, configuration.profile, headUrl);
    await saveJourney(axonId, journey, new Date().toISOString());
    await emit({ ...state, journey });
    await publishRunEvent(axonId, "journey.frozen", "AGENT_EVENT", {
      runId: run.id,
      missionId: mission.id,
      stepCount: journey.steps.length,
      producer: journey.producer,
    });

    await emit({ ...state, status: "executing" });
    await executeChecked(
      headBox,
      `cd ${shellQuote(homePath(configuration.profile.workspace.workingDirectory))} && ${configuration.profile.fixtures.resetCommand}`,
      "fixture reset",
    );
    const replay = await replayJourney(
      browserBox,
      mission,
      journey,
      configuration.profile,
      headUrl,
    );
    const executionId = randomUUID();
    const result = await persistReplayArtifacts(
      browserBox,
      run,
      mission.id,
      executionId,
      replay,
      axonId,
    );
    await saveExecution(axonId, executionId, result, result.endedAt);
    await emit({
      ...state,
      status: "complete",
      execution: result,
      actions: replay.actions,
      network: replay.network,
    });
    await publishRunEvent(axonId, "execution.completed", "EXTERNAL_EVENT", {
      runId: run.id,
      executionId,
      missionId: mission.id,
      status: result.status,
      assertionCount: result.checks.length,
      evidenceCount: countEvidence(result),
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
    await suspendEnvironment(browserBox, "browser", run, axonId, state, emit);
    await suspendEnvironment(headBox, "head", run, axonId, state, emit);
    await suspendEnvironment(baseBox, "base", run, axonId, state, emit);
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

function resolveMission(configured: TestMission, run: Run): TestMission {
  const claimIds =
    configured.claimIds.length > 0
      ? configured.claimIds
      : run.intentSpec?.claims[0]
        ? [run.intentSpec.claims[0].id]
        : [];
  const knownClaimIds = new Set(run.intentSpec?.claims.map((claim) => claim.id));
  if (claimIds.length === 0 || claimIds.some((claimId) => !knownClaimIds.has(claimId))) {
    throw new GroundtruthError(
      "test_mission_claim_stale",
      "The trusted TestMission does not reference an approved intent claim.",
      422,
    );
  }
  return { ...configured, claimIds };
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
  headUrl: string,
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
    `Live head URL: ${headUrl}`,
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
  headUrl: string,
): Promise<z.infer<typeof RunnerOutputSchema>> {
  const directory = "/home/user/groundtruth-runner";
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
    `cd ${directory} && HEAD_URL=${shellQuote(headUrl)} node replay.mjs`,
    "mechanical Playwright replay",
  );
  const raw = await box.file.read({ file_path: `${directory}/result.json` });
  return RunnerOutputSchema.parse(JSON.parse(raw));
}

async function persistReplayArtifacts(
  box: Devbox,
  run: Run,
  missionId: string,
  executionId: string,
  replay: z.infer<typeof RunnerOutputSchema>,
  axonId: string,
): Promise<ExecutionResult> {
  const sdk = getRunloopClient();
  const uploaded: Array<{ kind: string; id: string }> = [];
  const uploadText = async (kind: string, filePath: string, mimeType: string): Promise<string> => {
    const text = await box.file.read({ file_path: filePath });
    const object = await sdk.storageObject.uploadFromText(
      text,
      `groundtruth-${run.id}-${executionId}-${kind}.json`,
      { metadata: { run_id: run.id, execution_id: executionId, kind, mime_type: mimeType } },
    );
    uploaded.push({ kind, id: object.id });
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
    uploaded.push({ kind, id: object.id });
    return object.id;
  };

  const screenshotArtifactIds = await Promise.all(
    replay.files.screenshots.map((filePath, index) =>
      uploadBinary(`screenshot-${index}`, filePath, "image/png"),
    ),
  );
  const [actionArtifactId, consoleArtifactId, networkArtifactId] = await Promise.all([
    uploadText("actions", replay.files.actions, "application/json"),
    uploadText("console", replay.files.console, "application/json"),
    uploadText("network", replay.files.network, "application/json"),
  ]);
  const traceArtifactId = replay.files.trace
    ? await uploadBinary("trace", replay.files.trace, "application/zip")
    : undefined;
  const videoArtifactId = replay.files.video
    ? await uploadBinary("video", replay.files.video, "video/webm")
    : undefined;
  for (const artifact of uploaded) {
    await saveArtifact(axonId, artifact.id, executionId, artifact.kind, artifact.id);
  }

  return ExecutionResultSchema.parse({
    schemaVersion: 1,
    missionId,
    target: "head",
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
      networkArtifactId,
    },
    error: replay.error,
  });
}

async function suspendEnvironment(
  box: Devbox | undefined,
  role: BrowserEnvironment["role"],
  run: Run,
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
    await saveEnvironment(axonId, run.id, environment, new Date().toISOString()).catch(() => undefined);
    await emit({ ...state, environments }).catch(() => undefined);
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
const headUrl = process.env.HEAD_URL;
if (!headUrl) throw new Error("HEAD_URL is required.");
const headOrigin = new URL(headUrl);

const artifacts = path.resolve("artifacts");
await mkdir(artifacts, { recursive: true });
const startedAt = new Date().toISOString();
const actions = [];
const network = [];
const consoleEvents = [];
const steps = [];
const checks = [];
let error;
let status = "passed";
let videoPath;
const tracePath = path.join(artifacts, "trace.zip");
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  baseURL: headUrl,
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
    url.origin === headOrigin.origin &&
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
page.on("response", (response) => {
  network.push({
    method: response.request().method(),
    url: response.url(),
    status: response.status(),
    target: "head",
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
      steps.push({ index, status: "passed", at, summary });
      actions.push({ at, target: "head", summary, status: "passed" });
    } catch (cause) {
      status = "error";
      const message = cause instanceof Error ? cause.message : "Step failed.";
      steps.push({ index, status: "failed", message, at, summary });
      actions.push({ at, target: "head", summary, status: "failed" });
      throw cause;
    }
  }

  for (let index = 0; index < mission.assertions.length; index += 1) {
    const assertion = mission.assertions[index];
    let passed = false;
    let actual;
    if (assertion.kind === "url") {
      actual = page.url();
      passed = assertion.operator === "equals"
        ? actual === new URL(assertion.expected, headUrl).toString()
        : new RegExp(assertion.expected).test(actual);
    } else if (assertion.kind === "dom") {
      actual = await locator(assertion.locator).isVisible();
      passed = assertion.state === "visible" ? actual : !actual;
    } else if (assertion.kind === "text") {
      actual = await locator(assertion.locator).textContent();
      passed = assertion.operator === "equals"
        ? actual === assertion.expected
        : String(actual).includes(assertion.expected);
    } else if (assertion.kind === "network") {
      actual = network.filter((entry) =>
        entry.method === assertion.method && new RegExp(assertion.urlPattern).test(entry.url)
      );
      passed = actual.some((entry) => entry.status === assertion.expectedStatus);
    } else {
      actual = consoleEvents.filter((entry) => entry.level === "error").length;
      passed = actual <= assertion.maximumCount;
    }
    checks.push({ assertionIndex: index, passed, actual });
    if (!passed) status = "failed";
  }
} catch (cause) {
  error = { code: "mechanical_replay_failed", message: cause instanceof Error ? cause.message : "Replay failed." };
  status = "error";
} finally {
  await context.tracing.stop({ path: tracePath }).catch(() => undefined);
  await context.close();
  videoPath = await video?.path().catch(() => undefined);
  await browser.close();
}

const actionPath = path.join(artifacts, "actions.json");
const consolePath = path.join(artifacts, "console.json");
const networkPath = path.join(artifacts, "network.json");
await Promise.all([
  writeFile(actionPath, JSON.stringify(actions, null, 2)),
  writeFile(consolePath, JSON.stringify(consoleEvents, null, 2)),
  writeFile(networkPath, JSON.stringify(network, null, 2)),
]);
const screenshotFiles = steps
  .filter((step) => step.status === "passed")
  .map((step) => path.join(artifacts, "step-" + step.index + ".png"));
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
    network: networkPath,
  },
  error,
};
await writeFile("result.json", JSON.stringify(result, null, 2));
`;
