import { z } from "zod";

import { AssertionSchema, JourneyStepSchema } from "@/lib/domain/assertion-dsl";

const IsoDateSchema = z.iso.datetime();
const ShaSchema = z.string().regex(/^[a-f0-9]{40}$/i, "Expected a 40-character Git SHA");
const RepositoryNameSchema = z.string().regex(/^[^/\s]+\/[^/\s]+$/);

export const BlockerSchema = z.object({
  code: z.string().min(1),
  message: z.string().min(1),
  retryable: z.boolean(),
});

export type Blocker = z.infer<typeof BlockerSchema>;

export const IntentSpecSchema = z.object({
  schemaVersion: z.literal(1),
  summary: z.string().min(1),
  claims: z
    .array(
      z.object({
        id: z.string().min(1),
        statement: z.string().min(1),
        sourceQuote: z.string().min(1),
        priority: z.enum(["must", "should"]),
        acceptanceCriteria: z.array(z.string().min(1)).min(1),
      }),
    )
    .min(1),
  nonGoals: z.array(z.string().min(1)),
  ambiguities: z.array(z.string().min(1)),
});

export type IntentSpec = z.infer<typeof IntentSpecSchema>;

export const AppProfileSchema = z.object({
  schemaVersion: z.literal(1),
  repository: RepositoryNameSchema,
  compatibility: z.object({
    baseSha: ShaSchema,
    headSha: ShaSchema,
  }),
  workspace: z.object({
    workingDirectory: z
      .string()
      .min(1)
      .regex(/^(?![\\/])(?!.*(?:^|[\\/])\.\.(?:[\\/]|$)).+$/),
    installCommand: z.string().min(1),
    startCommand: z.string().min(1),
    port: z.number().int().min(1).max(65535),
    healthPath: z.string().startsWith("/"),
    preparedSnapshotId: z.string().min(1).optional(),
  }),
  fixtures: z.object({ resetCommand: z.string().min(1) }),
  auth: z.discriminatedUnion("mode", [
    z.object({ mode: z.literal("none") }),
    z.object({
      mode: z.literal("storage_state_object"),
      objectIdEnvironmentVariable: z.string().regex(/^[A-Z][A-Z0-9_]*$/),
    }),
  ]),
  safety: z.object({
    allowedHosts: z.array(z.string().min(1)).min(1),
    blockedPathPrefixes: z.array(z.string().startsWith("/")),
    allowStateChangingRequests: z.boolean(),
  }),
});

export type AppProfile = z.infer<typeof AppProfileSchema>;

export const AppMapSchema = z.object({
  schemaVersion: z.literal(1),
  repository: RepositoryNameSchema,
  baseSha: ShaSchema,
  routes: z.array(z.object({ path: z.string().min(1), sourceFiles: z.array(z.string().min(1)) })),
  components: z.array(
    z.object({
      name: z.string().min(1),
      sourceFiles: z.array(z.string().min(1)),
      routePaths: z.array(z.string().min(1)),
    }),
  ),
  apis: z.array(
    z.object({
      method: z.string().min(1),
      path: z.string().min(1),
      sourceFiles: z.array(z.string().min(1)),
    }),
  ),
  journeys: z.array(
    z.object({
      id: z.string().min(1),
      title: z.string().min(1),
      startPath: z.string().min(1),
      steps: z.array(z.object({ action: z.string().min(1), target: z.string().min(1) })),
    }),
  ),
});

export type AppMap = z.infer<typeof AppMapSchema>;

export const ImpactMapSchema = z.object({
  schemaVersion: z.literal(1),
  baseSha: ShaSchema,
  headSha: ShaSchema,
  changedFiles: z.array(z.string().min(1)),
  affectedRoutes: z.array(
    z.object({ path: z.string().min(1), evidenceFiles: z.array(z.string().min(1)).min(1) }),
  ),
  affectedComponents: z.array(
    z.object({ name: z.string().min(1), evidenceFiles: z.array(z.string().min(1)).min(1) }),
  ),
  affectedApis: z.array(
    z.object({
      method: z.string().min(1),
      path: z.string().min(1),
      evidenceFiles: z.array(z.string().min(1)).min(1),
    }),
  ),
  riskSignals: z.array(
    z.object({ label: z.string().min(1), evidenceFiles: z.array(z.string().min(1)).min(1) }),
  ),
});

export type ImpactMap = z.infer<typeof ImpactMapSchema>;

export const TestMissionSchema = z.object({
  schemaVersion: z.literal(1),
  id: z.string().min(1),
  title: z.string().min(1),
  kind: z.enum(["intent", "regression"]),
  claimIds: z.array(z.string().min(1)),
  goal: z.string().min(1),
  startPath: z.string().min(1),
  preconditions: z.array(z.string().min(1)),
  fixtureValues: z.record(z.string().min(1), z.string()).optional(),
  deferredClaims: z
    .array(
      z.object({
        claimId: z.string().min(1),
        reason: z.string().min(1),
      }),
    )
    .optional(),
  assertions: z.array(AssertionSchema).min(1),
});

export type TestMission = z.infer<typeof TestMissionSchema>;

export const ExecutableJourneySchema = z.object({
  schemaVersion: z.literal(1),
  missionId: z.string().min(1),
  discoveredAgainst: z.literal("head"),
  steps: z.array(JourneyStepSchema).min(1),
  producer: z.object({ kind: z.literal("codex"), agentId: z.string().min(1) }),
});

export type ExecutableJourney = z.infer<typeof ExecutableJourneySchema>;

export const ExecutionResultSchema = z.object({
  schemaVersion: z.literal(1),
  attemptId: z.uuid().optional(),
  executionId: z.uuid().optional(),
  missionId: z.string().min(1),
  target: z.enum(["base", "head"]),
  status: z.enum(["passed", "failed", "blocked", "error"]),
  startedAt: IsoDateSchema,
  endedAt: IsoDateSchema,
  steps: z.array(
    z.object({
      index: z.number().int().nonnegative(),
      status: z.enum(["passed", "failed"]),
      message: z.string().optional(),
    }),
  ),
  checks: z.array(
    z.object({
      assertionIndex: z.number().int().nonnegative(),
      passed: z.boolean(),
      actual: z.unknown(),
    }),
  ),
  evidence: z.object({
    videoArtifactId: z.string().min(1).optional(),
    traceArtifactId: z.string().min(1).optional(),
    screenshotArtifactIds: z.array(z.string().min(1)),
    actionArtifactId: z.string().min(1).optional(),
    consoleArtifactId: z.string().min(1).optional(),
    networkArtifactId: z.string().min(1).optional(),
  }),
  error: z.object({ code: z.string().min(1), message: z.string().min(1) }).optional(),
});

export type ExecutionResult = z.infer<typeof ExecutionResultSchema>;

export const BrowserEnvironmentSchema = z.object({
  role: z.enum(["base", "head", "browser"]),
  devboxId: z.string().min(1),
  status: z.enum(["provisioning", "running", "suspended", "shutdown", "failed"]),
  exactSha: ShaSchema.optional(),
  url: z.url().optional(),
  detail: z.string().min(1).optional(),
});

export type BrowserEnvironment = z.infer<typeof BrowserEnvironmentSchema>;

export const BrowserVerificationSchema = z.object({
  attemptId: z.uuid().optional(),
  status: z.enum(["preparing", "discovering", "executing", "complete", "blocked", "failed"]),
  mission: TestMissionSchema.optional(),
  journey: ExecutableJourneySchema.optional(),
  environments: z.array(BrowserEnvironmentSchema),
  execution: ExecutionResultSchema.optional(),
  actions: z.array(
    z.object({
      at: IsoDateSchema,
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
  browserAgent: z
    .object({
      devboxId: z.string().min(1),
      agentName: z.literal("codex"),
      transport: z.literal("agent_mount"),
      version: z.string().min(1),
    })
    .optional(),
  blocker: BlockerSchema.optional(),
});

export type BrowserVerification = z.infer<typeof BrowserVerificationSchema>;

export const RunStatusSchema = z.enum([
  "creating",
  "analyzing_intent",
  "awaiting_contract_approval",
  "contract_approved",
  "verifying",
  "setup_required",
  "blocked",
  "failed",
  "complete",
]);

export type RunStatus = z.infer<typeof RunStatusSchema>;

export const RunSchema = z.object({
  id: z.uuid(),
  key: z.string().min(1),
  repository: z.object({
    owner: z.string().min(1),
    name: z.string().min(1),
    cloneUrl: z.url(),
  }),
  pullRequest: z.object({
    number: z.number().int().positive(),
    url: z.url(),
    title: z.string().min(1),
    body: z.string(),
    baseRef: z.string().min(1),
    baseSha: ShaSchema,
    headRef: z.string().min(1),
    headSha: ShaSchema,
  }),
  status: RunStatusSchema,
  coordinationAxonId: z.string().min(1).optional(),
  provisioning: z
    .object({
      runCreatedPublished: z.boolean().optional(),
      prIngestedPublished: z.boolean().optional(),
      intentStartedPublished: z.boolean().optional(),
      intentValidatedPublished: z.boolean().optional(),
    })
    .optional(),
  reflexIntent: z
    .object({
      agentId: z.string().min(1),
      streamId: z.string().min(1),
      lastSequence: z.number().int().nonnegative().optional(),
      status: z.enum(["starting", "running", "needs_input", "complete", "failed"]),
    })
    .optional(),
  intentSpec: IntentSpecSchema.optional(),
  intentApprovalAttempt: z
    .object({
      approvedAt: IsoDateSchema,
      eventPublished: z.boolean(),
    })
    .optional(),
  intentApproval: z.object({ approvedAt: IsoDateSchema }).optional(),
  browserVerification: BrowserVerificationSchema.optional(),
  browserVerificationHistory: z.array(BrowserVerificationSchema).optional(),
  blocker: BlockerSchema.optional(),
  createdAt: IsoDateSchema,
  updatedAt: IsoDateSchema,
});

export type Run = z.infer<typeof RunSchema>;

export const RunViewSchema = z.object({
  run: z.object({
    id: z.uuid(),
    status: RunStatusSchema,
    repository: RepositoryNameSchema,
    pullRequestNumber: z.number().int().positive(),
    pullRequestUrl: z.url(),
    title: z.string().min(1),
    headSha: ShaSchema,
    coordinationAxonId: z.string().min(1).optional(),
  }),
  setup: z.object({
    ready: z.boolean(),
    blockers: z.array(z.object({ code: z.string().min(1), message: z.string().min(1) })),
  }),
  phases: z.array(
    z.object({
      id: z.enum(["pr", "intent", "approval", "impact", "plan", "execution"]),
      label: z.string().min(1),
      status: z.enum(["pending", "active", "complete", "blocked", "failed"]),
      detail: z.string().optional(),
    }),
  ),
  contract: z.object({
    status: z.enum(["pending", "ready", "approved", "invalid"]),
    intentSpec: IntentSpecSchema.optional(),
    selectedClaimId: z.string().optional(),
    claimCoverage: z.array(
      z.object({
        claimId: z.string().min(1),
        status: z.enum(["covered", "deferred", "uncovered"]),
        missionId: z.string().min(1).optional(),
        reason: z.string().min(1).optional(),
      }),
    ),
  }),
  missions: z.array(TestMissionSchema),
  journey: ExecutableJourneySchema.optional(),
  environments: z.array(BrowserEnvironmentSchema),
  results: z.object({
    intent: z.array(
      z.object({
        missionId: z.string().min(1),
        claimId: z.string().min(1),
        verdict: z.enum(["conformant", "non_conformant", "inconclusive"]),
      }),
    ),
    regression: z.array(
      z.object({
        missionId: z.string().min(1),
        verdict: z.enum(["safe", "regression", "inconclusive"]),
      }),
    ),
  }),
  blastRadius: ImpactMapSchema.optional(),
  recording: z
    .object({ artifactId: z.string().min(1), contentType: z.string().min(1) })
    .optional(),
  actions: z.array(
    z.object({
      at: IsoDateSchema,
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
  blocker: BlockerSchema.optional(),
});

export type RunView = z.infer<typeof RunViewSchema>;

export function buildRunKey(owner: string, repo: string, number: number, headSha: string): string {
  return `${owner.toLowerCase()}/${repo.toLowerCase()}#${number}@${headSha.toLowerCase()}`;
}
