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

  useEffect(() => {
    if (view.run.status !== "verifying") {
      return;
    }
    const refresh = async () => {
      try {
        const response = await fetch(`/api/runs/${view.run.id}`, { cache: "no-store" });
        const payload: unknown = await response.json();
        if (response.ok) {
          setView(RunViewSchema.parse(payload));
        }
      } catch {
        setActionError("Could not refresh live browser progress.");
      }
    };
    const timer = window.setInterval(() => void refresh(), 2000);
    return () => window.clearInterval(timer);
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
                <div>
                  <strong>Must-claim coverage</strong>
                  <ul>
                    {view.contract.claimCoverage.map((coverage) => (
                      <li key={coverage.claimId}>
                        <strong>{coverage.status}</strong> {coverage.claimId}
                        {coverage.reason ? ` — ${coverage.reason}` : ""}
                      </li>
                    ))}
                  </ul>
                </div>
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
              {view.contract.status === "approved" &&
              view.run.status !== "verifying" ? (
                <ActionButton
                  action={view.run.status === "complete" ? "rerun_verification" : "start_verification"}
                  busyAction={busyAction}
                  onAction={runAction}
                >
                  {view.run.status === "complete"
                    ? "Rerun browser verification"
                    : view.blocker?.retryable
                      ? "Retry browser verification"
                      : "Start browser verification"}
                </ActionButton>
              ) : null}
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
          results={view.results.intent.map((result) => ({
            id: `${result.missionId}:${result.claimId}`,
            label: result.claimId,
            verdict: result.verdict,
          }))}
          detail="No intent missions have been executed."
        />
        <ResultGroup
          title="Regression safety"
          results={view.results.regression.map((result) => ({
            id: result.missionId,
            label: result.missionId,
            verdict: result.verdict,
          }))}
          detail="No regression missions have been executed."
        />
      </section>

      <section className="evidence-grid">
        <div className="panel">
          <p className="eyebrow">Runloop</p>
          <h2>Application environments</h2>
          {view.environments.length > 0 ? (
            <ul>
              {view.environments.map((environment) => (
                <li key={environment.devboxId}>
                  <strong>{environment.role}</strong> {environment.status} — {environment.devboxId}
                  {environment.exactSha ? ` @ ${environment.exactSha.slice(0, 12)}` : ""}
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState title="Not provisioned" detail="No Runloop application environment exists." />
          )}
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
          {view.actions.length > 0 ? (
            <ol>
              {view.actions.map((action, index) => (
                <li key={`${action.at}-${index}`}>
                  <strong>{action.status}</strong> {action.summary}
                </li>
              ))}
            </ol>
          ) : (
            <EmptyState title="No actions" detail="Mechanical replay has not started." />
          )}
        </div>
        <div className="panel">
          <p className="eyebrow">Network</p>
          <h2>{view.network.length} requests</h2>
          {view.network.length > 0 ? (
            <ul>
              {prioritizeNetwork(view.network).slice(0, 12).map((request, index) => (
                <li key={`${request.url}-${index}`}>
                  {request.method} {request.status} {new URL(request.url).pathname}
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState title="No requests" detail="Sensitive network bodies are never exposed." />
          )}
        </div>
      </section>

      <section className="panel">
        <p className="eyebrow">Codex explorer</p>
        <h2>Frozen executable journey</h2>
        {view.journey ? (
          <ol>
            {view.journey.steps.map((step, index) => (
              <li key={`${step.action}-${index}`}>
                <strong>{step.action}</strong>{" "}
                {"path" in step ? step.path : "locator" in step ? JSON.stringify(step.locator) : ""}
              </li>
            ))}
          </ol>
        ) : (
          <EmptyState title="Not discovered" detail="Codex has not frozen a live journey." />
        )}
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

function ResultGroup({
  title,
  results,
  detail,
}: {
  title: string;
  results: Array<{ id: string; label: string; verdict: string }>;
  detail: string;
}) {
  return (
    <div className="panel result-group">
      <div>
        <p className="eyebrow">Authoritative checks</p>
        <h2>{title}</h2>
      </div>
      <span className="result-count">{results.length}</span>
      {results.length > 0 ? (
        <ul>
          {results.map((result) => (
            <li key={result.id}>
              <strong>{result.verdict.replaceAll("_", " ")}</strong> {result.label}
            </li>
          ))}
        </ul>
      ) : (
        <EmptyState title="Not run" detail={detail} />
      )}
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

function prioritizeNetwork<T extends { url: string }>(requests: T[]): T[] {
  return [...requests].sort((left, right) => networkPriority(left.url) - networkPriority(right.url));
}

function networkPriority(value: string): number {
  const pathname = new URL(value).pathname;
  if (pathname.startsWith("/api/")) {
    return 0;
  }
  return pathname.startsWith("/_next/") ? 2 : 1;
}

function labelStatus(status: string): string {
  return status.replaceAll("_", " ");
}
