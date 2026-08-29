import type { ReflexStreamEvent } from "@runloop/reflex-client";
import { z } from "zod";

const AgentItemSchema = z.object({
  type: z.string(),
  text: z.string().optional(),
  content: z
    .array(
      z.union([
        z.string(),
        z.object({ text: z.string().optional(), content: z.string().optional() }).passthrough(),
      ]),
    )
    .optional(),
});

const DirectItemPayloadSchema = z.object({ item: AgentItemSchema }).passthrough();
const WrappedItemPayloadSchema = z
  .object({ params: z.object({ item: AgentItemSchema }).passthrough() })
  .passthrough();

const MessagePayloadSchema = z
  .object({
    role: z.string().optional(),
    text: z.string().optional(),
    message: z.string().optional(),
    content: z.union([z.string(), z.array(z.unknown())]).optional(),
    delta: z.string().optional(),
  })
  .passthrough();

export function extractAssistantText(
  event: ReflexStreamEvent,
): { mode: "append" | "replace"; text: string } | undefined {
  const payload = decodePayload(event.payload);

  if (event.type === "item/agentMessage/delta") {
    const parsed = MessagePayloadSchema.safeParse(payload);
    if (parsed.success && parsed.data.delta) {
      return { mode: "append", text: parsed.data.delta };
    }
  }

  if (event.type === "item/completed") {
    const direct = DirectItemPayloadSchema.safeParse(payload);
    const wrapped = direct.success ? undefined : WrappedItemPayloadSchema.safeParse(payload);
    const item = direct.success
      ? direct.data.item
      : wrapped?.success
        ? wrapped.data.params.item
        : undefined;
    if (item && normalizeItemType(item.type) === "agentmessage") {
      const text = itemText(item);
      if (text) {
        return { mode: "replace", text };
      }
    }
  }

  if (event.type === "agent_message_chunk") {
    const parsed = MessagePayloadSchema.safeParse(payload);
    if (parsed.success) {
      const text = parsed.data.delta ?? parsed.data.text ?? parsed.data.message;
      if (text) {
        return { mode: "append", text };
      }
    }
  }

  if (event.type === "message") {
    const parsed = MessagePayloadSchema.safeParse(payload);
    if (parsed.success && parsed.data.role?.toLowerCase() === "assistant") {
      const text = parsed.data.text ?? parsed.data.message ?? contentText(parsed.data.content);
      if (text) {
        return { mode: "replace", text };
      }
    }
  }

  return undefined;
}

export function isTurnStart(eventType: string): boolean {
  return eventType === "turn/started" || eventType === "turn.started";
}

export function isFailedTurn(eventType: string): boolean {
  return [
    "turn.failed",
    "turn.cancelled",
    "turn/failed",
    "turn/cancelled",
    "agent.stopped",
    "agent.killed",
    "agent.interrupted",
  ].includes(eventType);
}

function decodePayload(payload: unknown): unknown {
  if (typeof payload !== "string") {
    return payload;
  }
  try {
    return JSON.parse(payload);
  } catch {
    return payload;
  }
}

function normalizeItemType(value: string): string {
  return value.replaceAll("_", "").replaceAll("-", "").toLowerCase();
}

function itemText(item: z.infer<typeof AgentItemSchema>): string | undefined {
  return item.text ?? contentText(item.content);
}

function contentText(content: string | unknown[] | undefined): string | undefined {
  if (typeof content === "string") {
    return content;
  }
  if (!Array.isArray(content)) {
    return undefined;
  }
  const text = content
    .map((part) => {
      if (typeof part === "string") {
        return part;
      }
      const parsed = z
        .object({ text: z.string().optional(), content: z.string().optional() })
        .safeParse(part);
      return parsed.success ? (parsed.data.text ?? parsed.data.content ?? "") : "";
    })
    .join("");
  return text || undefined;
}
