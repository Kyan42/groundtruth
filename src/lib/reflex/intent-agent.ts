import {
  createAgent,
  getAgentModelSupport,
  interruptAgent,
  sendAgentMessage,
} from "@runloop/reflex-client";
import { z } from "zod";

import { GroundtruthError, SetupRequiredError } from "@/lib/domain/errors";
import { type IntentSpec, IntentSpecSchema, type Run } from "@/lib/domain/schemas";
import { configureReflexServer } from "@/lib/reflex/client";

export async function createIntentAgent(
  run: Run,
): Promise<{ agentId: string; streamId: string }> {
  configureReflexServer();
  const support = (await getAgentModelSupport()).data;
  const launchable = support.launchableAgents?.find(
    (agent) =>
      agent.enabled &&
      `${agent.agentType} ${agent.displayName}`.toLowerCase().includes("codex"),
  );
  if (!launchable) {
    throw new SetupRequiredError(
      "reflex_codex_unavailable",
      "The active Reflex organization does not expose a launchable Codex agent.",
    );
  }

  const response = await createAgent({
    name: `Groundtruth intent ${run.pullRequest.number}`,
    agentType: launchable.agentType,
    prompt: buildIntentPrompt(run),
    systemPrompt:
      "Extract only explicit pull-request intent. Return JSON only. Never reveal hidden reasoning or infer behavior from repository code.",
    sandboxOptions: {
      computerUse: false,
      idleTimeMinutes: 5,
    },
  });

  return {
    agentId: response.data.id,
    streamId: response.data.streamId,
  };
}

export async function retryIntentAgent(agentId: string, validationMessage: string): Promise<void> {
  configureReflexServer();
  await sendAgentMessage(agentId, {
    message: [
      "Your previous response did not satisfy the IntentSpec contract.",
      validationMessage,
      "Return one corrected JSON object only. Use claims explicitly supported by the supplied PR prose.",
    ].join("\n"),
  });
}

export async function interruptIntentAgent(agentId: string): Promise<void> {
  configureReflexServer();
  await interruptAgent(agentId);
}

export function parseIntentSpec(text: string, run: Run): IntentSpec {
  const normalized = stripJsonFence(text);
  let value: unknown;
  try {
    value = JSON.parse(normalized);
  } catch {
    throw new GroundtruthError(
      "invalid_intent_spec",
      "Codex did not return a valid JSON IntentSpec.",
      422,
      true,
    );
  }

  const parsed = IntentSpecSchema.safeParse(value);
  if (!parsed.success) {
    throw new GroundtruthError(
      "invalid_intent_spec",
      "Codex returned JSON that does not match the IntentSpec schema.",
      422,
      true,
      parsed.error.issues,
    );
  }

  const prose = `${run.pullRequest.title}\n${run.pullRequest.body}`;
  const unsupportedQuotes = parsed.data.claims
    .map((claim) => claim.sourceQuote)
    .filter((quote) => !prose.includes(quote));
  if (unsupportedQuotes.length > 0) {
    throw new GroundtruthError(
      "intent_quote_not_in_pr",
      "Every intent claim must quote the pull request title or body exactly.",
      422,
      true,
      unsupportedQuotes,
    );
  }
  return parsed.data;
}

function buildIntentPrompt(run: Run): string {
  return [
    "Produce an IntentSpec from the pull request prose below.",
    "Use only explicit statements in the title and body.",
    "Every claim sourceQuote must be an exact substring of that prose.",
    "Do not inspect or discuss repository code, diffs, branches, or external context.",
    "Return a single JSON object with no markdown.",
    "",
    `JSON schema: ${JSON.stringify(z.toJSONSchema(IntentSpecSchema))}`,
    "",
    `PR title:\n${run.pullRequest.title}`,
    "",
    `PR body:\n${run.pullRequest.body || "(empty)"}`,
  ].join("\n");
}

function stripJsonFence(text: string): string {
  const trimmed = text.trim();
  const match = trimmed.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i);
  return match ? match[1].trim() : trimmed;
}
