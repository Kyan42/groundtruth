"use client";

import { useEffect, useMemo, useState } from "react";

import { type RunView, RunViewSchema } from "@/lib/domain/schemas";

export function RunDashboard({ initialView }: { initialView: RunView }) {
  const [view, setView] = useState(initialView);
  const [selectedClaimId, setSelectedClaimId] = useState(initialView.contract.selectedClaimId);
  const [actionError, setActionError] = useState<string>();
  const [busyAction, setBusyAction] = useState<string>();

  useEffect(() => {
    if (view.run.status !== "analyzing_intent") {
      return;
    }
    const source = new EventSource(`/api/runs/${view.run.id}/events`);
    const onRun = (event: MessageEvent<string>) => {
      const next = RunViewSchema.parse(JSON.parse(event.data));
      setView(next);
      setSelectedClaimId((current) => current ?? next.contract.selectedClaimId);
    };
    const onStreamError = () => setActionError("The live stream disconnected. Refresh to resume.");
    source.addEventListener("run", onRun as EventListener);
    source.addEventListener("error", onStreamError);
    return () => source.close();
  }, [view.run.id, view.run.status]);

  const selectedClaim = useMemo(
    () => view.contract.intentSpec?.claims.find((claim) => claim.id === selectedClaimId),
    [selectedClaimId, view.contract.intentSpec],
  );

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
      setView(next);
      setSelectedClaimId(next.contract.selectedClaimId);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "The action failed.");
    } finally {
      setBusyAction(undefined);
    }
  }

  return (
    <main className="dashboard-shell">
      <header className="dashboard-header">
        <a className="brand" href="/">
          <span className="brand-mark">GT</span>
          <span>Groundtruth</span>
        </a>
        <span className={`status-pill status-${view.run.status}`}>{labelStatus(view.run.status)}</span>
      </header>

      <section className="pr-overview panel">
        <div>
          <p className="eyebrow">
            {view.run.repository} / PR #{view.run.pullRequestNumber}
          </p>
          <h1>{view.run.title}</h1>
          <a href={view.run.pullRequestUrl} target="_blank" rel="noreferrer">
            Open pull request
          </a>
        </div>
        <dl className="run-meta">
          <div>
            <dt>Head SHA</dt>
            <dd>{view.run.headSha.slice(0, 12)}</dd>
          </div>
          <div>
            <dt>Coordination Axon</dt>
            <dd>{view.run.coordinationAxonId ?? "Not provisioned"}</dd>
          </div>
        </dl>
      </section>

      {view.setup.blockers.length > 0 ? (
        <section className="setup-banner">
          <div>
            <strong>Setup required</strong>
            <p>{view.setup.blockers.map((blocker) => blocker.message).join(" ")}</p>
          </div>
          {view.run.status === "setup_required" || view.blocker?.code === "integration_failed" ? (
            <ActionButton action="resume" busyAction={busyAction} onAction={runAction}>
              Retry setup
            </ActionButton>
          ) : null}
        </section>
      ) : null}

      <div className="dashboard-grid">
        <section className="panel phase-panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Live run</p>
              <h2>Verification phases</h2>
            </div>
            {view.run.status === "analyzing_intent" ? <span className="live-dot">Live</span> : null}
          </div>
          <ol className="phase-list">
            {view.phases.map((phase) => (
              <li key={phase.id} className={`phase phase-${phase.status}`}>
                <span className="phase-marker" />
                <div>
                  <strong>{phase.label}</strong>
                  {phase.detail ? <p>{phase.detail}</p> : null}
                </div>
                <span>{phase.status.replace("_", " ")}</span>
              </li>
            ))}
          </ol>
          {view.run.status === "analyzing_intent" ? (
            <ActionButton
              action="interrupt_intent"
              busyAction={busyAction}
              onAction={runAction}
              secondary
            >
              Interrupt intent agent
            </ActionButton>
          ) : null}
        </section>

        <section className="panel contract-panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Approval gate</p>
              <h2>Intent contract</h2>
            </div>
            <span className="contract-state">{view.contract.status}</span>
          </div>
          {view.contract.intentSpec ? (
            <>
              <p className="contract-summary">{view.contract.intentSpec.summary}</p>
              <div className="claim-picker" role="tablist" aria-label="Intent claims">
                {view.contract.intentSpec.claims.map((claim, index) => (
                  <button
                    type="button"
                    role="tab"
                    aria-selected={claim.id === selectedClaimId}
                    className={claim.id === selectedClaimId ? "selected" : undefined}
                    key={claim.id}
                    onClick={() => setSelectedClaimId(claim.id)}
                  >
                    Claim {index + 1}
                  </button>
                ))}
              </div>
              {selectedClaim ? (
                <article className="claim-card">
                  <span className={`priority priority-${selectedClaim.priority}`}>
                    {selectedClaim.priority}
                  </span>
                  <h3>{selectedClaim.statement}</h3>
                  <blockquote>&ldquo;{selectedClaim.sourceQuote}&rdquo;</blockquote>
                  <ul>
                    {selectedClaim.acceptanceCriteria.map((criterion) => (
                      <li key={criterion}>{criterion}</li>
                    ))}
                  </ul>
                </article>
              ) : null}
              <div className="contract-notes">
                <NoteList title="Ambiguities" items={view.contract.intentSpec.ambiguities} />
                <NoteList title="Non-goals" items={view.contract.intentSpec.nonGoals} />
              </div>
              {view.contract.status === "ready" ? (
                <ActionButton action="approve_intent" busyAction={busyAction} onAction={runAction}>
                  Approve intent contract
                </ActionButton>
              ) : (
                <p className="approved-copy">Contract approved and recorded in the run Axon.</p>
              )}
            </>
          ) : (
            <EmptyState
              title={view.contract.status === "invalid" ? "Contract needs correction" : "No contract yet"}
              detail={
                view.blocker?.message ??
                "The Reflex Codex agent is extracting explicit claims from pull-request prose."
              }
            />
          )}
          {view.contract.status === "invalid" ? (
            <ActionButton action="retry_intent" busyAction={busyAction} onAction={runAction}>
              Ask Codex to correct output
            </ActionButton>
          ) : null}
          {actionError ? <p className="form-error">{actionError}</p> : null}
        </section>
      </div>

      <section className="results-grid">
        <ResultGroup
          title="Intent conformance"
          count={view.results.intent.length}
          detail="No intent missions have been executed."
        />
        <ResultGroup
          title="Regression safety"
          count={view.results.regression.length}
          detail="No regression missions have been executed."
        />
      </section>

      <section className="evidence-grid">
        <div className="panel">
          <p className="eyebrow">Blast radius</p>
          <h2>Impact evidence</h2>
          <EmptyState
            title="Not mapped"
            detail="ImpactMap generation is outside the intent-capture foundation."
          />
        </div>
        <div className="panel recording-panel">
          <p className="eyebrow">Recording</p>
          <h2>Browser evidence</h2>
          {view.recording ? (
            <video controls src={`/api/artifacts/${view.recording.artifactId}`} />
          ) : (
            <EmptyState title="Not recorded" detail="No browser execution has run." />
          )}
        </div>
        <div className="panel">
          <p className="eyebrow">Action audit</p>
          <h2>{view.actions.length} actions</h2>
          <EmptyState title="No actions" detail="Mechanical replay has not started." />
        </div>
        <div className="panel">
          <p className="eyebrow">Network</p>
          <h2>{view.network.length} requests</h2>
          <EmptyState title="No requests" detail="Sensitive network bodies are never exposed." />
        </div>
      </section>
    </main>
  );
}

function ActionButton({
  action,
  busyAction,
  onAction,
  children,
  secondary = false,
}: {
  action: string;
  busyAction?: string;
  onAction: (action: string) => void;
  children: React.ReactNode;
  secondary?: boolean;
}) {
  return (
    <button
      type="button"
      className={secondary ? "secondary-button" : "primary-button"}
      disabled={Boolean(busyAction)}
      onClick={() => onAction(action)}
    >
      {busyAction === action ? "Working..." : children}
    </button>
  );
}

function NoteList({ title, items }: { title: string; items: string[] }) {
  return (
    <div>
      <strong>{title}</strong>
      {items.length > 0 ? (
        <ul>
          {items.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      ) : (
        <p>None declared.</p>
      )}
    </div>
  );
}

function ResultGroup({ title, count, detail }: { title: string; count: number; detail: string }) {
  return (
    <div className="panel result-group">
      <div>
        <p className="eyebrow">Authoritative checks</p>
        <h2>{title}</h2>
      </div>
      <span className="result-count">{count}</span>
      <EmptyState title="Not run" detail={detail} />
    </div>
  );
}

function EmptyState({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="empty-state">
      <strong>{title}</strong>
      <p>{detail}</p>
    </div>
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

function labelStatus(status: string): string {
  return status.replaceAll("_", " ");
}
