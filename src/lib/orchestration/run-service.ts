import { randomUUID } from "node:crypto";

import {
  initialAgentLiveness,
  isTurnEndEventType,
  reduceAgentLiveness,
  type AgentLivenessState,
  type ReflexStreamEvent,
} from "@runloop/reflex-client";

import { GroundtruthError } from "@/lib/domain/errors";
import { loadAppConfiguration } from "@/lib/config/app-config";
import {
  buildRunKey,
  type Blocker,
  type Run,
  RunSchema,
  type RunView,
} from "@/lib/domain/schemas";
import { fetchPublicPullRequest } from "@/lib/github/public-pr-client";
import { getRunIndex } from "@/lib/persistence/json-run-index";
import {
  createIntentAgent,
  interruptIntentAgent,
  parseIntentSpec,
  retryIntentAgent,
} from "@/lib/reflex/intent-agent";
import {
  extractAssistantText,
  isFailedTurn,
  isTurnStart,
} from "@/lib/reflex/stream-reducer";
import {
  createCoordinationAxon,
  publishRunEvent,
  saveIntentContract,
} from "@/lib/runloop/coordination-axon";
import { executeBrowserVerification } from "@/lib/runloop/browser-verification";
import { buildRunView } from "@/lib/views/build-run-view";

const livenessByRun = new Map<string, AgentLivenessState>();
const messageByRun = new Map<string, string>();
const seenEventIdsByRun = new Map<string, Set<string>>();
const eventQueuesByRun = new Map<string, Promise<RunView>>();
const provisioningByRun = new Map<string, Promise<Run>>();
const verificationByRun = new Map<string, Promise<void>>();

export class RunService {
  async create(prUrl: string): Promise<RunView> {
    const publicPr = await fetchPublicPullRequest(prUrl);
    const key = buildRunKey(
      publicPr.repository.owner,
      publicPr.repository.name,
      publicPr.pullRequest.number,
      publicPr.pullRequest.headSha,
    );
    const now = new Date().toISOString();
    const result = await getRunIndex().getOrCreate(key, () =>
      RunSchema.parse({
        id: randomUUID(),
        key,
        repository: publicPr.repository,
        pullRequest: publicPr.pullRequest,
        status: "creating",
        createdAt: now,
        updatedAt: now,
      }),
    );

    const shouldProvision =
      result.created ||
      ["creating", "setup_required"].includes(result.run.status) ||
      (result.run.status === "failed" && result.run.blocker?.code === "integration_failed");
    const run = shouldProvision ? await this.enqueueProvision(result.run) : result.run;
    return buildRunView(run);
  }

  async get(runId: string): Promise<Run> {
    const run = await getRunIndex().getById(runId);
    if (!run) {
      throw new GroundtruthError("run_not_found", `Run ${runId} was not found.`, 404);
    }
    return run;
  }

  async getView(runId: string): Promise<RunView> {
    return buildRunView(await this.get(runId));
  }

  async resume(runId: string): Promise<RunView> {
    const run = await this.get(runId);
    const resumableFailure =
      run.status === "failed" && run.blocker?.code === "integration_failed";
    if (!["creating", "setup_required"].includes(run.status) && !resumableFailure) {
      throw new GroundtruthError(
        "run_not_resumable",
        "Only a run waiting on setup can resume provisioning.",
        409,
      );
    }
    const reset = await getRunIndex().update(run.id, (current) => ({
      ...current,
      blocker: undefined,
      status: "creating",
      updatedAt: new Date().toISOString(),
    }));
    return buildRunView(await this.enqueueProvision(reset));
  }

  approveIntent(runId: string): Promise<RunView> {
    return this.enqueueRunWork(runId, () => this.approveIntentOnce(runId));
  }

  async startVerification(runId: string): Promise<RunView> {
    const run = await this.get(runId);
    const retryableBrowserFailure =
      ["blocked", "failed"].includes(run.status) && run.browserVerification?.blocker?.retryable;
    if (run.status !== "contract_approved" && !retryableBrowserFailure) {
      throw new GroundtruthError(
        "browser_verification_not_ready",
        "Approve the intent contract before starting browser verification.",
        409,
      );
    }
    const configuration = await loadAppConfiguration(
      run.repository.owner,
      run.repository.name,
      run.pullRequest.baseSha,
      run.pullRequest.headSha,
    );
    if (!configuration.ready) {
      const blocker: Blocker = {
        code: configuration.blockers[0]?.code ?? "app_config_missing",
        message: configuration.blockers.map((candidate) => candidate.message).join(" "),
        retryable: true,
      };
      const blocked = await getRunIndex().update(run.id, (current) => ({
        ...current,
        status: "setup_required",
        blocker,
        updatedAt: new Date().toISOString(),
      }));
      return buildRunView(blocked);
    }
    if (verificationByRun.has(run.id)) {
      return this.getView(run.id);
    }

    const preparing = await getRunIndex().update(run.id, (current) => ({
      ...current,
      status: "verifying",
      blocker: undefined,
      browserVerification: {
        status: "preparing",
        mission: configuration.mission,
        environments: [],
        actions: [],
        network: [],
      },
      updatedAt: new Date().toISOString(),
    }));
    const operation = this.runBrowserVerification(preparing, configuration).finally(() => {
      verificationByRun.delete(run.id);
    });
    verificationByRun.set(run.id, operation);
    void operation.catch((error: unknown) => {
      console.error("Unhandled browser verification failure.", error);
    });
    return buildRunView(preparing);
  }

  private async approveIntentOnce(runId: string): Promise<RunView> {
    let run = await this.get(runId);
    if (
      run.status !== "awaiting_contract_approval" ||
      run.intentApproval ||
      !run.intentSpec ||
      !run.coordinationAxonId
    ) {
      throw new GroundtruthError(
        "intent_not_ready",
        "A validated IntentSpec and coordination Axon are required before approval.",
        409,
      );
    }
    const coordinationAxonId = run.coordinationAxonId;
    const intentSpec = run.intentSpec;
    if (!run.provisioning?.intentValidatedPublished) {
      await this.publishIfAvailable(run, "intent.validated", {
        runId,
        claimCount: intentSpec.claims.length,
        ambiguityCount: intentSpec.ambiguities.length,
      });
      run = await this.markProvisioned(run.id, "intentValidatedPublished");
    }
    const attempt =
      run.intentApprovalAttempt ??
      ({
        approvedAt: new Date().toISOString(),
        eventPublished: false,
      } as const);
    if (!run.intentApprovalAttempt) {
      await getRunIndex().update(run.id, (current) => ({
        ...current,
        intentApprovalAttempt: attempt,
        updatedAt: new Date().toISOString(),
      }));
    }
    await saveIntentContract(
      coordinationAxonId,
      run.id,
      intentSpec,
      attempt.approvedAt,
    );
    if (!attempt.eventPublished) {
      await publishRunEvent(coordinationAxonId, "intent.approved", "USER_EVENT", {
        runId: run.id,
        approvalId: run.id,
        approvedAt: attempt.approvedAt,
        claimCount: intentSpec.claims.length,
      });
      await getRunIndex().update(run.id, (current) => ({
        ...current,
        intentApprovalAttempt: { ...attempt, eventPublished: true },
        updatedAt: new Date().toISOString(),
      }));
    }
    const updated = await getRunIndex().update(run.id, (current) => ({
      ...current,
      status: "contract_approved",
      intentApprovalAttempt: undefined,
      intentApproval: { approvedAt: attempt.approvedAt },
      blocker: undefined,
      updatedAt: attempt.approvedAt,
    }));
    return buildRunView(updated);
  }

  retryIntent(runId: string): Promise<RunView> {
    return this.enqueueRunWork(runId, () => this.retryIntentOnce(runId));
  }

  private async retryIntentOnce(runId: string): Promise<RunView> {
    const run = await this.get(runId);
    const retryableStates = new Set(["blocked", "failed"]);
    const retryableCodes = new Set([
      "invalid_intent_spec",
      "intent_quote_not_in_pr",
      "intent_interrupted",
      "intent_agent_failed",
    ]);
    if (
      !run.reflexIntent ||
      run.intentApproval ||
      !retryableStates.has(run.status) ||
      !run.blocker ||
      !retryableCodes.has(run.blocker.code)
    ) {
      throw new GroundtruthError(
        "intent_not_retryable",
        "Only a failed, interrupted, or invalid unapproved intent contract can be retried.",
        409,
      );
    }
    await retryIntentAgent(run.reflexIntent.agentId, run.blocker?.message ?? "Validation failed.");
    messageByRun.delete(run.id);
    const updated = await getRunIndex().update(run.id, (current) => ({
      ...current,
      status: "analyzing_intent",
      intentSpec: undefined,
      intentApproval: undefined,
      blocker: undefined,
      reflexIntent: current.reflexIntent
        ? { ...current.reflexIntent, status: "running" }
        : current.reflexIntent,
      updatedAt: new Date().toISOString(),
    }));
    if (updated.coordinationAxonId) {
      await publishRunEvent(updated.coordinationAxonId, "intent.retry_requested", "USER_EVENT", {
        runId: updated.id,
      });
    }
    return buildRunView(updated);
  }

  interruptIntent(runId: string): Promise<RunView> {
    return this.enqueueRunWork(runId, () => this.interruptIntentOnce(runId));
  }

  private async interruptIntentOnce(runId: string): Promise<RunView> {
    const run = await this.get(runId);
    if (!run.reflexIntent || run.status !== "analyzing_intent") {
      throw new GroundtruthError(
        "intent_not_running",
        "Intent analysis is not currently running.",
        409,
      );
    }
    await interruptIntentAgent(run.reflexIntent.agentId);
    const now = new Date().toISOString();
    const blocker: Blocker = {
      code: "intent_interrupted",
      message: "Intent analysis was interrupted by the user.",
      retryable: true,
    };
    const updated = await getRunIndex().update(run.id, (current) => ({
      ...current,
      status: "blocked",
      blocker,
      reflexIntent: current.reflexIntent
        ? { ...current.reflexIntent, status: "needs_input" }
        : current.reflexIntent,
      updatedAt: now,
    }));
    if (updated.coordinationAxonId) {
      await publishRunEvent(updated.coordinationAxonId, "intent.interrupted", "USER_EVENT", {
        runId: updated.id,
      });
    }
    return buildRunView(updated);
  }

  async processIntentEvent(runId: string, event: ReflexStreamEvent): Promise<RunView> {
    const current = await this.get(runId);
    if (current.status !== "analyzing_intent") {
      return buildRunView(current);
    }
    const previousLiveness = livenessByRun.get(runId) ?? initialAgentLiveness();
    const nextLiveness = reduceAgentLiveness(previousLiveness, event);
    livenessByRun.set(runId, nextLiveness);

    if (isTurnStart(event.type)) {
      messageByRun.set(runId, "");
    }
    const extracted = extractAssistantText(event);
    if (extracted) {
      const previous = messageByRun.get(runId) ?? "";
      messageByRun.set(runId, extracted.mode === "append" ? previous + extracted.text : extracted.text);
    }

    const isNew =
      event.sequence === undefined ||
      current.reflexIntent?.lastSequence === undefined ||
      event.sequence > current.reflexIntent.lastSequence;
    if (!isNew) {
      return buildRunView(current);
    }

    const sequenceUpdate =
      event.sequence === undefined
        ? {}
        : {
            lastSequence: event.sequence,
          };
    const now = new Date().toISOString();

    if (isFailedTurn(event.type)) {
      const blocker: Blocker = {
        code: "intent_agent_failed",
        message: "The Reflex Codex intent turn did not complete.",
        retryable: true,
      };
      const failed = await getRunIndex().update(runId, (run) => ({
        ...run,
        status: "failed",
        blocker,
        reflexIntent: run.reflexIntent
          ? { ...run.reflexIntent, ...sequenceUpdate, status: "failed" }
          : run.reflexIntent,
        updatedAt: now,
      }));
      await this.publishIfAvailable(failed, "intent.failed", { runId, eventType: event.type });
      return buildRunView(failed);
    }

    if (isTurnEndEventType(event.type)) {
      const message = messageByRun.get(runId)?.trim() ?? "";
      let intentSpec;
      try {
        intentSpec = parseIntentSpec(message, current);
      } catch (error) {
        const groundtruthError =
          error instanceof GroundtruthError
            ? error
            : new GroundtruthError(
                "invalid_intent_spec",
                "Codex returned an invalid IntentSpec.",
                422,
                true,
              );
        const blocker: Blocker = {
          code: groundtruthError.code,
          message: groundtruthError.message,
          retryable: true,
        };
        await this.publishIfAvailable(current, "intent.invalid", {
          runId,
          code: blocker.code,
        });
        const blocked = await getRunIndex().update(runId, (run) => ({
          ...run,
          status: "blocked",
          intentSpec: undefined,
          blocker,
          reflexIntent: run.reflexIntent
            ? { ...run.reflexIntent, ...sequenceUpdate, status: "needs_input" }
            : run.reflexIntent,
          updatedAt: now,
        }));
        messageByRun.delete(runId);
        return buildRunView(blocked);
      }

      let completed = await getRunIndex().update(runId, (run) => ({
        ...run,
        status: "awaiting_contract_approval",
        intentSpec,
        blocker: undefined,
        reflexIntent: run.reflexIntent
          ? { ...run.reflexIntent, ...sequenceUpdate, status: "complete" }
          : run.reflexIntent,
        updatedAt: now,
      }));
      try {
        await this.publishIfAvailable(completed, "intent.validated", {
          runId,
          claimCount: intentSpec.claims.length,
          ambiguityCount: intentSpec.ambiguities.length,
        });
        completed = await this.markProvisioned(runId, "intentValidatedPublished");
      } catch (error) {
        console.error("Failed to publish validated intent to the coordination Axon.", error);
        completed = await getRunIndex().update(runId, (run) => ({
          ...run,
          blocker: {
            code: "axon_event_pending",
            message: "The intent is valid, but its Axon audit event still needs to be published.",
            retryable: true,
          },
          updatedAt: new Date().toISOString(),
        }));
      }
      messageByRun.delete(runId);
      return buildRunView(completed);
    }

    const running = await getRunIndex().update(runId, (run) => ({
      ...run,
      status: "analyzing_intent",
      reflexIntent: run.reflexIntent
        ? { ...run.reflexIntent, ...sequenceUpdate, status: "running" }
        : run.reflexIntent,
      updatedAt: now,
    }));
    return buildRunView(running);
  }

  enqueueIntentEvent(runId: string, event: ReflexStreamEvent): Promise<RunView> {
    return this.enqueueRunWork(runId, async () => {
        const seen = seenEventIdsByRun.get(runId) ?? new Set<string>();
        seenEventIdsByRun.set(runId, seen);
        if (seen.has(event.id)) {
          return this.getView(runId);
        }
        const view = await this.processIntentEvent(runId, event);
        seen.add(event.id);
        return view;
      });
  }

  private enqueueProvision(run: Run): Promise<Run> {
    const existing = provisioningByRun.get(run.id);
    if (existing) {
      return existing;
    }
    const provisioning = this.provision(run).finally(() => {
      if (provisioningByRun.get(run.id) === provisioning) {
        provisioningByRun.delete(run.id);
      }
    });
    provisioningByRun.set(run.id, provisioning);
    return provisioning;
  }

  private async runBrowserVerification(
    run: Run,
    configuration: Extract<Awaited<ReturnType<typeof loadAppConfiguration>>, { ready: true }>,
  ): Promise<void> {
    await executeBrowserVerification(run, configuration, async (browserVerification) => {
      await getRunIndex().update(run.id, (current) => ({
        ...current,
        status:
          browserVerification.status === "complete"
            ? "complete"
            : browserVerification.status === "blocked"
              ? "blocked"
              : browserVerification.status === "failed"
                ? "failed"
                : "verifying",
        browserVerification,
        blocker: browserVerification.blocker,
        updatedAt: new Date().toISOString(),
      }));
    });
  }

  private async provision(run: Run): Promise<Run> {
    let current = run;
    try {
      if (!current.coordinationAxonId) {
        const axonId = await createCoordinationAxon(current);
        current = await getRunIndex().update(current.id, (stored) => ({
          ...stored,
          coordinationAxonId: axonId,
          blocker: undefined,
          updatedAt: new Date().toISOString(),
        }));
      }

      if (!current.provisioning?.runCreatedPublished) {
        await publishRunEvent(current.coordinationAxonId!, "run.created", "EXTERNAL_EVENT", {
          runId: current.id,
          runKey: current.key,
        });
        current = await this.markProvisioned(current.id, "runCreatedPublished");
      }

      if (!current.provisioning?.prIngestedPublished) {
        await publishRunEvent(current.coordinationAxonId!, "pr.ingested", "EXTERNAL_EVENT", {
          runId: current.id,
          repository: `${current.repository.owner}/${current.repository.name}`,
          pullRequestNumber: current.pullRequest.number,
          baseSha: current.pullRequest.baseSha,
          headSha: current.pullRequest.headSha,
        });
        current = await this.markProvisioned(current.id, "prIngestedPublished");
      }

      if (!current.reflexIntent) {
        const agent = await createIntentAgent(current);
        current = await getRunIndex().update(current.id, (stored) => ({
          ...stored,
          status: "analyzing_intent",
          reflexIntent: { ...agent, status: "starting" },
          blocker: undefined,
          updatedAt: new Date().toISOString(),
        }));
      }

      if (!current.provisioning?.intentStartedPublished && current.reflexIntent) {
        await this.publishIfAvailable(current, "intent.agent_started", {
          runId: current.id,
          agentId: current.reflexIntent.agentId,
          streamId: current.reflexIntent.streamId,
        });
        current = await this.markProvisioned(current.id, "intentStartedPublished");
      }

      if (current.status !== "analyzing_intent") {
        current = await getRunIndex().update(current.id, (stored) => ({
          ...stored,
          status: "analyzing_intent",
          blocker: undefined,
          updatedAt: new Date().toISOString(),
        }));
      }
      return current;
    } catch (error) {
      const blocker = blockerFromError(error);
      const status =
        error instanceof GroundtruthError && error.status === 503 ? "setup_required" : "failed";
      const failed = await getRunIndex().update(current.id, (stored) => ({
        ...stored,
        status,
        blocker,
        updatedAt: new Date().toISOString(),
      }));
      if (failed.coordinationAxonId) {
        try {
          await publishRunEvent(
            failed.coordinationAxonId,
            status === "setup_required" ? "setup.required" : "run.failed",
            "EXTERNAL_EVENT",
            {
              runId: failed.id,
              code: blocker.code,
            },
          );
        } catch (publishError) {
          console.error("Failed to publish run failure to the coordination Axon.", publishError);
        }
      }
      return failed;
    }
  }

  private async publishIfAvailable(
    run: Run,
    eventType: string,
    payload: Record<string, unknown>,
  ): Promise<void> {
    if (run.coordinationAxonId) {
      await publishRunEvent(run.coordinationAxonId, eventType, "AGENT_EVENT", payload);
    }
  }

  private async markProvisioned(
    runId: string,
    step:
      | "runCreatedPublished"
      | "prIngestedPublished"
      | "intentStartedPublished"
      | "intentValidatedPublished",
  ): Promise<Run> {
    return getRunIndex().update(runId, (run) => ({
      ...run,
      provisioning: { ...run.provisioning, [step]: true },
      updatedAt: new Date().toISOString(),
    }));
  }

  private enqueueRunWork(runId: string, operation: () => Promise<RunView>): Promise<RunView> {
    const previous = eventQueuesByRun.get(runId) ?? Promise.resolve(undefined);
    const next = previous.catch(() => undefined).then(operation);
    eventQueuesByRun.set(runId, next);
    return next;
  }
}

function blockerFromError(error: unknown): Blocker {
  if (error instanceof GroundtruthError) {
    return { code: error.code, message: error.message, retryable: error.retryable };
  }
  console.error(error);
  return {
    code: "integration_failed",
    message: "An external integration failed while provisioning the run.",
    retryable: true,
  };
}

let singleton: RunService | undefined;

export function getRunService(): RunService {
  singleton ??= new RunService();
  return singleton;
}
