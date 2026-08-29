"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  buildDashboardItems,
  getDefaultDashboardItemKey,
} from "@/components/run-dashboard-model";
import { RunDashboardShell } from "@/components/run-dashboard-sections";
import { type RunView, RunViewSchema } from "@/lib/domain/schemas";

type SelectionState = {
  key?: string;
  manual: boolean;
};

export function RunDashboard({ initialView }: { initialView: RunView }) {
  const [view, setView] = useState(initialView);
  const [selection, setSelection] = useState<SelectionState>({
    key: getDefaultDashboardItemKey(initialView),
    manual: false,
  });
  const [actionError, setActionError] = useState<string>();
  const [busyAction, setBusyAction] = useState<string>();
  const items = useMemo(() => buildDashboardItems(view), [view]);
  const selectedItem = items.all.find((item) => item.key === selection.key);

  const replaceView = useCallback((next: RunView) => {
    setView(next);
    setSelection((current) => {
      const nextItems = buildDashboardItems(next);
      if (current.manual && nextItems.all.some((item) => item.key === current.key)) {
        return current;
      }
      return {
        key: getDefaultDashboardItemKey(next),
        manual: false,
      };
    });
  }, []);

  useEffect(() => {
    if (view.run.status !== "analyzing_intent") {
      return;
    }
    const source = new EventSource(`/api/runs/${view.run.id}/events`);
    const onRun = (event: MessageEvent<string>) => {
      replaceView(RunViewSchema.parse(JSON.parse(event.data)));
    };
    const onStreamError = () =>
      setActionError("The live stream disconnected. Refresh to resume.");
    source.addEventListener("run", onRun as EventListener);
    source.addEventListener("error", onStreamError);
    return () => source.close();
  }, [replaceView, view.run.id, view.run.status]);

  useEffect(() => {
    if (view.run.status !== "verifying") {
      return;
    }
    const refresh = async () => {
      try {
        const response = await fetch(`/api/runs/${view.run.id}`, { cache: "no-store" });
        const payload: unknown = await response.json();
        if (!response.ok) {
          throw new Error(readApiError(payload));
        }
        replaceView(RunViewSchema.parse(payload));
      } catch {
        setActionError("Could not refresh live browser progress.");
      }
    };
    const timer = window.setInterval(() => void refresh(), 2000);
    return () => window.clearInterval(timer);
  }, [replaceView, view.run.id, view.run.status]);

  async function runAction(action: string) {
    setBusyAction(action);
    setActionError(undefined);
    try {
      const response = await fetch(`/api/runs/${view.run.id}/actions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      });
      const payload: unknown = await response.json();
      if (!response.ok) {
        throw new Error(readApiError(payload));
      }
      const next = RunViewSchema.parse(payload);
      replaceView(next);
      if (next.run.id !== view.run.id) {
        window.history.replaceState(null, "", `/runs/${next.run.id}`);
      }
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "The action failed.");
    } finally {
      setBusyAction(undefined);
    }
  }

  return (
    <RunDashboardShell
      view={view}
      items={items}
      selectedItem={selectedItem}
      busyAction={busyAction}
      actionError={actionError}
      onSelect={(key) => setSelection({ key, manual: true })}
      onAction={(action) => void runAction(action)}
    />
  );
}

function readApiError(payload: unknown): string {
  if (
    typeof payload === "object" &&
    payload !== null &&
    "error" in payload &&
    typeof payload.error === "object" &&
    payload.error !== null &&
    "message" in payload.error &&
    typeof payload.error.message === "string"
  ) {
    return payload.error.message;
  }
  return "The action failed.";
}
