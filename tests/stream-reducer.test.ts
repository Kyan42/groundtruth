import type { ReflexStreamEvent } from "@runloop/reflex-client";
import { describe, expect, it } from "vitest";

import { extractAssistantText } from "@/lib/reflex/stream-reducer";

function event(type: string, payload: unknown): ReflexStreamEvent {
  return {
    id: "event-1",
    sequence: 1,
    streamId: "stream-1",
    type,
    payload,
    timestamp: 1,
  };
}

describe("Reflex Codex stream reduction", () => {
  it("extracts only an explicit completed agent message", () => {
    expect(
      extractAssistantText(
        event("item/completed", {
          item: { type: "agentMessage", text: '{"schemaVersion":1}' },
        }),
      ),
    ).toEqual({ mode: "replace", text: '{"schemaVersion":1}' });
  });

  it("extracts the Codex app-server params envelope observed from Reflex", () => {
    expect(
      extractAssistantText(
        event("item/completed", {
          method: "item/completed",
          params: {
            item: {
              type: "agentMessage",
              text: '{"schemaVersion":1}',
              phase: "final_answer",
            },
          },
        }),
      ),
    ).toEqual({ mode: "replace", text: '{"schemaVersion":1}' });
  });

  it("does not expose reasoning events as assistant output", () => {
    expect(
      extractAssistantText(
        event("item/reasoning/textDelta", {
          delta: "private reasoning",
        }),
      ),
    ).toBeUndefined();
  });
});
