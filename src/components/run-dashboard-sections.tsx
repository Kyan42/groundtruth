"use client";

import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";

import {
  buildActionRows,
  buildVerdictNarrative,
  describeJourneyStep,
  findLatestAttemptForItem,
  findMissionForItem,
  isEvidenceLinked,
  labelRunStatus,
  normalizeNetworkRows,
  statusLabel,
  type DashboardItem,
  type DashboardItemGroups,
  type DashboardItemStatus,
} from "@/components/run-dashboard-model";
import styles from "@/components/run-dashboard.module.css";
import { RunEvidencePlayer } from "@/components/run-evidence-player";
import type { RunView } from "@/lib/domain/schemas";

type RunDashboardShellProps = {
  view: RunView;
  items: DashboardItemGroups;
  selectedItem?: DashboardItem;
  busyAction?: string;
  actionError?: string;
  onSelect: (key: string) => void;
  onAction: (action: string) => void;
};

export function RunDashboardShell({
  view,
  items,
  selectedItem,
  busyAction,
  actionError,
  onSelect,
  onAction,
}: RunDashboardShellProps) {
  const attempt = findLatestAttemptForItem(view, selectedItem);
  const selectedView = attempt
    ? { ...view, journey: attempt.journey, actions: attempt.actions, network: attempt.network }
    : view;
  const narrative = buildVerdictNarrative(selectedView, selectedItem);
  const evidenceLinked = isEvidenceLinked(view, selectedItem);
  const mission = findMissionForItem(view, selectedItem);
  const [evidenceTarget, setEvidenceTarget] = useState<"base" | "head">("head");
  const recordingTargets = availableRecordingTargets(view, attempt);
  const selectedEvidenceTarget = recordingTargets.includes(evidenceTarget)
    ? evidenceTarget
    : recordingTargets[0] ?? evidenceTarget;
  const recording = recordingForTarget(view, attempt, selectedEvidenceTarget);
  const contractMode = shouldShowContract(view);

  useEffect(() => {
    setEvidenceTarget("head");
  }, [selectedItem?.key]);

  return (
    <div className={styles.page}>
      <a className={styles.skipLink} href="#run-evidence">
        Skip to run evidence
      </a>
      <TopRail view={view} />
      <div className={styles.dashboardLayout}>
        <ClaimsRail
          view={view}
          items={items}
          selectedItem={selectedItem}
          busyAction={busyAction}
          onSelect={onSelect}
          onAction={onAction}
        />
        <main className={styles.workspace} id="run-evidence">
          <div className={styles.workspaceInner}>
            {view.setup.blockers.length > 0 ? (
              <SetupBanner
                view={view}
                busyAction={busyAction}
                onAction={onAction}
              />
            ) : null}
            {actionError ? (
              <p className={styles.actionError} role="alert">
                {actionError}
              </p>
            ) : null}

            <EvidenceHeading selectedItem={selectedItem} view={view} />

            {view.run.status === "verifying" ? (
              <RunningStage view={view} selectedItem={selectedItem} />
            ) : contractMode ? (
              <ContractStage
                view={view}
                selectedItem={selectedItem}
                busyAction={busyAction}
                onAction={onAction}
              />
            ) : evidenceLinked && recording ? (
              <>
                {selectedItem?.kind === "regression" ? (
                  <EvidenceTargetSelector
                    target={selectedEvidenceTarget}
                    availableTargets={recordingTargets}
                    onChange={setEvidenceTarget}
                  />
                ) : null}
                <RunEvidencePlayer
                  src={`/api/artifacts/${encodeURIComponent(recording.artifactId)}`}
                  title={`${selectedEvidenceTarget === "base" ? "Base" : "Head"} browser recording for ${selectedItem?.label ?? "the selected check"}`}
                  safeAddress={`Recorded ${selectedEvidenceTarget.toUpperCase()} application session`}
                  badge={`${selectedEvidenceTarget} · ${selectedItem?.id ?? mission?.id ?? "evidence"}`}
                />
              </>
            ) : (
              <EvidenceEmptyStage
                view={selectedView}
                selectedItem={selectedItem}
                evidenceLinked={evidenceLinked}
                busyAction={busyAction}
                onAction={onAction}
              />
            )}

            {!contractMode && view.run.status !== "verifying" ? (
              <VerdictSummary
                item={selectedItem}
                title={narrative.title}
                body={narrative.body}
                missionLabel={narrative.missionLabel}
              />
            ) : null}

            {selectedItem?.kind === "regression" && attempt ? (
              <RegressionEvidencePanel attempt={attempt} />
            ) : null}
            <DetailPanel view={selectedView} />
          </div>
        </main>
      </div>
    </div>
  );
}

function TopRail({ view }: { view: RunView }) {
  return (
    <header className={styles.topRail}>
      <a className={styles.brand} href="/" aria-label="Groundtruth home">
        <GroundtruthMark />
        <span>Groundtruth</span>
      </a>
      <a
        className={styles.prLink}
        href={view.run.pullRequestUrl}
        target="_blank"
        rel="noreferrer"
      >
        <span>{view.run.repository}</span>
        <span aria-hidden="true">/</span>
        <strong>PR #{view.run.pullRequestNumber}</strong>
        <ExternalIcon />
      </a>
      <span className={styles.runStatus} aria-live="polite">
        <span className={styles.statusPulse} aria-hidden="true" />
        {labelRunStatus(view.run.status)}
      </span>
    </header>
  );
}

function ClaimsRail({
  view,
  items,
  selectedItem,
  busyAction,
  onSelect,
  onAction,
}: {
  view: RunView;
  items: DashboardItemGroups;
  selectedItem?: DashboardItem;
  busyAction?: string;
  onSelect: (key: string) => void;
  onAction: (action: string) => void;
}) {
  const activePhase = view.phases.find((phase) => phase.status === "active");

  return (
    <aside className={styles.claimsRail} aria-label="Run claims and checks">
      <div className={styles.claimsIntro}>
        <p className={styles.sectionKicker}>Run contract</p>
        <h1>Claims</h1>
        <p>Checked in a live browser against the exact pull request revision.</p>
        <p className={styles.coverageSummary} aria-label="Execution coverage">
          <strong>
            {items.coverage.intentExercised} of {items.coverage.intentTotal}
          </strong>{" "}
          intent {items.coverage.intentTotal === 1 ? "claim" : "claims"} exercised
          <span aria-hidden="true"> · </span>
          <strong>{items.coverage.regressionExecuted}</strong>{" "}
          {items.coverage.regressionExecuted === 1 ? "regression" : "regressions"} executed
        </p>
      </div>

      <div className={styles.claimsScroller}>
        <ClaimGroup
          label={items.collapseNotRun ? "Intent results" : "From the pull request"}
          count={items.intent.length}
          items={items.intent}
          selectedItem={selectedItem}
          onSelect={onSelect}
          emptyCopy={
            view.contract.status === "pending"
              ? "The intent agent is extracting claims."
              : "No intent claims are available."
          }
        />
        {items.regression.length > 0 ? (
          <ClaimGroup
            label="Not claimed, checked anyway"
            count={items.regression.length}
            items={items.regression}
            selectedItem={selectedItem}
            onSelect={onSelect}
          />
        ) : null}
        {items.collapseNotRun && items.notRun.length > 0 ? (
          <NotRunClaims items={items.notRun} />
        ) : null}
      </div>

      <div className={styles.runContext}>
        <div className={styles.runContextHeading}>
          <div>
            <p className={styles.sectionKicker}>Run context</p>
            <strong>PR #{view.run.pullRequestNumber}</strong>
          </div>
          <span>{view.run.headSha.slice(0, 8)}</span>
        </div>
        <p className={styles.runTitle}>{view.run.title}</p>
        <dl className={styles.contextFacts}>
          <div>
            <dt>Repository</dt>
            <dd>{view.run.repository}</dd>
          </div>
          <div>
            <dt>State</dt>
            <dd>{activePhase?.label ?? labelRunStatus(view.run.status)}</dd>
          </div>
        </dl>
        <SidebarActions
          view={view}
          busyAction={busyAction}
          onAction={onAction}
        />
        <a
          className={styles.backLink}
          href={view.run.pullRequestUrl}
          target="_blank"
          rel="noreferrer"
        >
          Open pull request
          <ExternalIcon />
        </a>
      </div>
    </aside>
  );
}

function NotRunClaims({ items }: { items: DashboardItem[] }) {
  return (
    <details className={styles.notRunClaims}>
      <summary>
        Not run <span>({items.length})</span>
      </summary>
      <div className={styles.notRunList}>
        {items.map((item) => (
          <article className={styles.notRunItem} key={item.key}>
            <div>
              <span className={styles.claimStatus}>Not run</span>
              <code>{item.id}</code>
            </div>
            <p className={styles.claimStatement}>{item.label}</p>
            <p className={styles.claimDetail}>
              {item.detail ?? "No browser mission was executed for this claim."}
            </p>
          </article>
        ))}
      </div>
    </details>
  );
}

function ClaimGroup({
  label,
  count,
  items,
  selectedItem,
  onSelect,
  emptyCopy,
}: {
  label: string;
  count: number;
  items: DashboardItem[];
  selectedItem?: DashboardItem;
  onSelect: (key: string) => void;
  emptyCopy?: string;
}) {
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const selected = listRef.current?.querySelector<HTMLElement>('[data-selected="true"]');
    selected?.scrollIntoView({ block: "nearest" });
  }, [selectedItem?.key]);

  return (
    <section className={styles.claimGroup} aria-label={label}>
      <div className={styles.claimGroupHeading}>
        <h2>{label}</h2>
        <span>{count}</span>
      </div>
      {items.length > 0 ? (
        <div className={styles.claimList} ref={listRef}>
          {items.map((item) => (
            <button
              type="button"
              className={styles.claimButton}
              data-selected={item.key === selectedItem?.key}
              data-status={item.status}
              aria-pressed={item.key === selectedItem?.key}
              key={item.key}
              onClick={() => onSelect(item.key)}
            >
              <StatusGlyph status={item.status} />
              <span className={styles.claimCopy}>
                <span className={styles.claimStatement}>{item.label}</span>
                {item.detail ? (
                  <span className={styles.claimDetail}>{item.detail}</span>
                ) : null}
                <span className={styles.claimStatus}>{statusLabel(item.status)}</span>
              </span>
              <code>{item.id}</code>
            </button>
          ))}
        </div>
      ) : (
        <p className={styles.railEmpty}>{emptyCopy ?? "No checks are available."}</p>
      )}
    </section>
  );
}

function SidebarActions({
  view,
  busyAction,
  onAction,
}: {
  view: RunView;
  busyAction?: string;
  onAction: (action: string) => void;
}) {
  if (view.run.status === "analyzing_intent") {
    return (
      <ActionButton
        action="interrupt_intent"
        busyAction={busyAction}
        onAction={onAction}
        secondary
      >
        Interrupt intent agent
      </ActionButton>
    );
  }
  if (view.run.status === "complete") {
    const hasRegressionResult = view.results.regression.length > 0;
    return (
      <ActionButton
        action={hasRegressionResult ? "start_regression" : "rerun_verification"}
        busyAction={busyAction}
        onAction={onAction}
      >
        {hasRegressionResult ? "Rerun regression" : "Rerun verification"}
      </ActionButton>
    );
  }
  if (view.run.status === "verifying") {
    return (
      <p className={styles.liveNotice}>
        <span aria-hidden="true" />
        Browser verification is live
      </p>
    );
  }
  return null;
}

function SetupBanner({
  view,
  busyAction,
  onAction,
}: {
  view: RunView;
  busyAction?: string;
  onAction: (action: string) => void;
}) {
  const canRetry =
    view.run.status === "setup_required" || view.blocker?.code === "integration_failed";
  return (
    <section className={styles.setupBanner} aria-label="Setup required">
      <WarningIcon />
      <div>
        <strong>Setup required</strong>
        <p>{view.setup.blockers.map((blocker) => blocker.message).join(" ")}</p>
      </div>
      {canRetry ? (
        <ActionButton
          action="resume"
          busyAction={busyAction}
          onAction={onAction}
          secondary
        >
          Retry setup
        </ActionButton>
      ) : null}
    </section>
  );
}

function EvidenceHeading({
  selectedItem,
  view,
}: {
  selectedItem?: DashboardItem;
  view: RunView;
}) {
  return (
    <header className={styles.evidenceHeading}>
      <div>
        <p className={styles.sectionKicker}>
          {selectedItem?.kind === "regression" ? "Regression check" : "Intent evidence"}
        </p>
        <h2>
          {selectedItem?.label ??
            view.contract.intentSpec?.summary ??
            "Preparing the run contract"}
        </h2>
      </div>
      {selectedItem ? (
        <span className={styles.selectedStatus} data-status={selectedItem.status}>
          <StatusGlyph status={selectedItem.status} compact />
          {statusLabel(selectedItem.status)}
        </span>
      ) : (
        <span className={styles.selectedStatus}>Awaiting claims</span>
      )}
    </header>
  );
}

function ContractStage({
  view,
  selectedItem,
  busyAction,
  onAction,
}: {
  view: RunView;
  selectedItem?: DashboardItem;
  busyAction?: string;
  onAction: (action: string) => void;
}) {
  const spec = view.contract.intentSpec;
  const claim =
    selectedItem?.kind === "intent"
      ? spec?.claims.find((candidate) => candidate.id === selectedItem.id)
      : spec?.claims[0];

  return (
    <section className={styles.contractStage} aria-labelledby="contract-heading">
      <div className={styles.contractToolbar}>
        <span>Intent contract</span>
        <span>{view.contract.status}</span>
      </div>
      <div className={styles.contractBody}>
        {spec ? (
          <>
            <div className={styles.contractSummary}>
              <p className={styles.sectionKicker}>Approval gate</p>
              <h3 id="contract-heading">{spec.summary}</h3>
            </div>
            {claim ? (
              <article className={styles.contractClaim}>
                <div className={styles.contractClaimMeta}>
                  <span>{claim.priority}</span>
                  <code>{claim.id}</code>
                </div>
                <h4>{claim.statement}</h4>
                <blockquote>&ldquo;{claim.sourceQuote}&rdquo;</blockquote>
                <div>
                  <strong>Acceptance criteria</strong>
                  <ul>
                    {claim.acceptanceCriteria.map((criterion) => (
                      <li key={criterion}>{criterion}</li>
                    ))}
                  </ul>
                </div>
              </article>
            ) : null}
            <div className={styles.contractNotes}>
              <ContractNote label="Ambiguities" items={spec.ambiguities} />
              <ContractNote label="Non-goals" items={spec.nonGoals} />
            </div>
            <ContractActions
              view={view}
              busyAction={busyAction}
              onAction={onAction}
            />
          </>
        ) : (
          <div className={styles.contractPending}>
            <ActivityIcon />
            <div>
              <p className={styles.sectionKicker}>Contract in progress</p>
              <h3 id="contract-heading">
                {view.contract.status === "invalid"
                  ? "The extracted contract needs correction."
                  : "Groundtruth is extracting explicit claims."}
              </h3>
              <p>
                {view.blocker?.message ??
                  "Only claims supported by pull request prose will appear here."}
              </p>
            </div>
            <RunProgress view={view} />
            <ContractActions
              view={view}
              busyAction={busyAction}
              onAction={onAction}
            />
          </div>
        )}
      </div>
    </section>
  );
}

function ContractNote({ label, items }: { label: string; items: string[] }) {
  return (
    <details>
      <summary>
        {label}
        <span>{items.length}</span>
      </summary>
      {items.length > 0 ? (
        <ul>
          {items.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      ) : (
        <p>None declared.</p>
      )}
    </details>
  );
}

function ContractActions({
  view,
  busyAction,
  onAction,
}: {
  view: RunView;
  busyAction?: string;
  onAction: (action: string) => void;
}) {
  if (view.contract.status === "ready") {
    return (
      <ActionButton action="approve_intent" busyAction={busyAction} onAction={onAction}>
        Approve intent contract
      </ActionButton>
    );
  }
  if (view.contract.status === "invalid") {
    return (
      <ActionButton action="retry_intent" busyAction={busyAction} onAction={onAction}>
        Ask Codex to correct output
      </ActionButton>
    );
  }
  if (view.contract.status === "approved" && view.run.status !== "verifying") {
    return (
      <div className={styles.contractApproval}>
        <p>
          <CheckIcon />
          Contract approved and recorded.
        </p>
        <ActionButton
          action={view.run.status === "complete" ? "rerun_verification" : "start_intent_suite"}
          busyAction={busyAction}
          onAction={onAction}
        >
          {view.run.status === "complete"
            ? "Rerun browser verification"
            : view.blocker?.retryable
              ? "Retry browser verification"
              : "Start intent verification suite"}
        </ActionButton>
      </div>
    );
  }
  return null;
}

function RunningStage({
  view,
  selectedItem,
}: {
  view: RunView;
  selectedItem?: DashboardItem;
}) {
  return (
    <section className={styles.runningStage} aria-label="Browser verification progress">
      <div className={styles.browserToolbar}>
        <span className={styles.windowDots} aria-hidden="true">
          <i />
          <i />
          <i />
        </span>
        <span className={styles.safeAddress}>
          Live application session at {view.run.headSha.slice(0, 8)}
        </span>
        <span className={styles.recordingBadge}>
          <span aria-hidden="true" />
          {selectedItem?.id ?? "verification"}
        </span>
      </div>
      <div className={styles.runningBody}>
        <div className={styles.runningLead}>
          <ActivityIcon />
          <p className={styles.sectionKicker}>Live browser run</p>
          <h3>Collecting mechanical evidence</h3>
          <p>
            The dashboard is polling real phase and environment state. A recording will
            appear only after the execution artifact is persisted.
          </p>
        </div>
        <RunProgress view={view} />
        <EnvironmentStrip view={view} />
      </div>
    </section>
  );
}

function EvidenceEmptyStage({
  view,
  selectedItem,
  evidenceLinked,
  busyAction,
  onAction,
}: {
  view: RunView;
  selectedItem?: DashboardItem;
  evidenceLinked: boolean;
  busyAction?: string;
  onAction: (action: string) => void;
}) {
  const narrative = buildVerdictNarrative(view, selectedItem);
  const canRetry =
    view.contract.status === "approved" &&
    view.run.status !== "complete" &&
    view.run.status !== "verifying";
  const detail = view.blocker?.message
    ? view.blocker.message
    : selectedItem?.status === "deferred"
      ? "This claim was explicitly deferred from the recorded mission."
      : selectedItem?.status === "uncovered"
        ? "The current run has no mission for this claim."
        : evidenceLinked
          ? "The linked mission did not publish a playable video artifact."
          : "The selected item is not linked to the current recording.";

  return (
    <section className={styles.emptyEvidence} aria-label="Browser evidence unavailable">
      <div className={styles.browserToolbar}>
        <span className={styles.windowDots} aria-hidden="true">
          <i />
          <i />
          <i />
        </span>
        <span className={styles.safeAddress}>Groundtruth browser evidence</span>
        <span className={styles.recordingBadge}>No video</span>
      </div>
      <div className={styles.emptyEvidenceBody}>
        <StatusGlyph status={selectedItem?.status ?? "pending"} />
        <p className={styles.sectionKicker}>{statusLabel(selectedItem?.status ?? "pending")}</p>
        <h3>{narrative.title}</h3>
        <p>{detail}</p>
        {canRetry ? (
          <ActionButton
            action="start_intent_suite"
            busyAction={busyAction}
            onAction={onAction}
          >
            {view.blocker?.retryable ? "Retry intent verification suite" : "Start intent verification suite"}
          </ActionButton>
        ) : null}
      </div>
    </section>
  );
}

function VerdictSummary({
  item,
  title,
  body,
  missionLabel,
}: {
  item?: DashboardItem;
  title: string;
  body: string;
  missionLabel?: string;
}) {
  return (
    <section className={styles.verdict} aria-label="Selected check verdict">
      <StatusGlyph status={item?.status ?? "pending"} />
      <div>
        <p>
          <strong>{title}</strong> {body}
        </p>
        {missionLabel ? <span>Linked mission: {missionLabel}</span> : null}
      </div>
    </section>
  );
}

function EvidenceTargetSelector({
  target,
  availableTargets,
  onChange,
}: {
  target: "base" | "head";
  availableTargets: Array<"base" | "head">;
  onChange: (target: "base" | "head") => void;
}) {
  return (
    <div className={styles.evidenceTargetSelector} aria-label="Regression recording target">
      <span>Recording</span>
      {(["base", "head"] as const).map((candidate) => (
        <button
          key={candidate}
          type="button"
          aria-pressed={target === candidate}
          disabled={!availableTargets.includes(candidate)}
          onClick={() => onChange(candidate)}
        >
          {candidate === "base" ? "Base" : "Head"}
        </button>
      ))}
    </div>
  );
}

function RegressionEvidencePanel({
  attempt,
}: {
  attempt: RunView["verificationAttempts"][number];
}) {
  const comparison = attempt.comparison;
  if (!comparison) {
    return null;
  }
  return (
    <section className={styles.regressionEvidence} aria-label="Base and head comparison">
      <div>
        <p className={styles.sectionKicker}>Paired regression evidence</p>
        <h3>Base vs. head named observations</h3>
        <p>{comparison.reason}</p>
      </div>
      <div className={styles.observationTableWrap}>
        <table className={styles.observationTable}>
          <thead>
            <tr>
              <th>Behavior</th>
              <th>Base</th>
              <th>Head</th>
              <th>Result</th>
            </tr>
          </thead>
          <tbody>
            {comparison.observations.map((observation) => (
              <tr key={observation.assertionId}>
                <td>
                  <strong>{observation.behavior}</strong>
                  <code>{observation.assertionId}</code>
                </td>
                <td>{formatObservation(observation.baseNormalized, observation.basePassed)}</td>
                <td>{formatObservation(observation.headNormalized, observation.headPassed)}</td>
                <td>{observation.equal === false || !observation.headPassed ? "Diverged" : "Matched"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {comparison.firstDivergence ? (
        <p className={styles.firstDivergence}>
          <strong>First divergence:</strong> {comparison.firstDivergence.behavior}. Base:{" "}
          {comparison.firstDivergence.baseSummary}; Head:{" "}
          {comparison.firstDivergence.headSummary}.
        </p>
      ) : (
        <p className={styles.firstDivergence}>
          <strong>First divergence:</strong> none.
        </p>
      )}
    </section>
  );
}

function formatObservation(value: unknown, passed: boolean): string {
  if (value === undefined) {
    return passed ? "Passed" : "Failed";
  }
  return typeof value === "string" ? value : JSON.stringify(value) ?? String(value);
}

function availableRecordingTargets(
  view: RunView,
  attempt: RunView["verificationAttempts"][number] | undefined,
): Array<"base" | "head"> {
  return (["base", "head"] as const).filter((target) =>
    Boolean(recordingForTarget(view, attempt, target)),
  );
}

function recordingForTarget(
  view: RunView,
  attempt: RunView["verificationAttempts"][number] | undefined,
  target: "base" | "head",
): { artifactId: string; contentType: string } | undefined {
  const artifactId =
    attempt?.executions?.[target]?.evidence.videoArtifactId ??
    (attempt?.execution?.target === target
      ? attempt.execution.evidence.videoArtifactId
      : undefined);
  if (artifactId) {
    return { artifactId, contentType: "video/webm" };
  }
  const latestAttempt = view.verificationAttempts[view.verificationAttempts.length - 1];
  return attempt?.attemptId === latestAttempt?.attemptId
    ? view.recordings.find((candidate) => candidate.target === target)
    : undefined;
}

function DetailPanel({ view }: { view: RunView }) {
  const [tab, setTab] = useState<"actions" | "network">("actions");
  const actionsTabRef = useRef<HTMLButtonElement>(null);
  const networkTabRef = useRef<HTMLButtonElement>(null);
  const actions = buildActionRows(view);
  const network = normalizeNetworkRows(view.network);

  function handleTabKey(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") {
      return;
    }
    event.preventDefault();
    const nextTab = tab === "actions" ? "network" : "actions";
    setTab(nextTab);
    (nextTab === "actions" ? actionsTabRef : networkTabRef).current?.focus();
  }

  return (
    <section className={styles.detailsPanel} aria-label="Run evidence details">
      <div className={styles.tabs} role="tablist" aria-label="Evidence details">
        <button
          ref={actionsTabRef}
          type="button"
          role="tab"
          id="actions-tab"
          aria-controls="actions-panel"
          aria-selected={tab === "actions"}
          tabIndex={tab === "actions" ? 0 : -1}
          onClick={() => setTab("actions")}
          onKeyDown={handleTabKey}
        >
          What the agent did
          <span>{actions.length}</span>
        </button>
        <button
          ref={networkTabRef}
          type="button"
          role="tab"
          id="network-tab"
          aria-controls="network-panel"
          aria-selected={tab === "network"}
          tabIndex={tab === "network" ? 0 : -1}
          onClick={() => setTab("network")}
          onKeyDown={handleTabKey}
        >
          Network
          <span>{network.length}</span>
        </button>
      </div>

      {tab === "actions" ? (
        <div
          className={styles.tabPanel}
          id="actions-panel"
          role="tabpanel"
          aria-labelledby="actions-tab"
        >
          {actions.length > 0 ? (
            <ol className={styles.actionList}>
              {actions.map((action) => (
                <li key={action.key}>
                  <time dateTime={action.at}>{action.offset}</time>
                  <span className={styles.actionState} data-state={action.status.toLowerCase()}>
                    {action.status}
                  </span>
                  <div>
                    <strong>{action.summary}</strong>
                    {action.structuredDetail ? <p>{action.structuredDetail}</p> : null}
                  </div>
                  <span className={styles.targetLabel}>{action.target}</span>
                </li>
              ))}
            </ol>
          ) : (
            <PanelEmpty
              title="No recorded actions"
              detail="Mechanical replay has not published an action audit for this run."
            />
          )}
        </div>
      ) : (
        <div
          className={styles.tabPanel}
          id="network-panel"
          role="tabpanel"
          aria-labelledby="network-tab"
        >
          {network.length > 0 ? (
            <div className={styles.networkTableWrap}>
              <table className={styles.networkTable}>
                <thead>
                  <tr>
                    <th scope="col">Method</th>
                    <th scope="col">Path</th>
                    <th scope="col">Status</th>
                    <th scope="col">Target</th>
                  </tr>
                </thead>
                <tbody>
                  {network.map((request) => (
                    <tr key={request.key} data-relevant={request.relevant}>
                      <td>
                        <span>{request.method}</span>
                      </td>
                      <td>
                        <code>{request.path}</code>
                      </td>
                      <td>{request.status}</td>
                      <td>{request.target}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <PanelEmpty
              title="No network records"
              detail="Request bodies, query values, and application origins are never rendered."
            />
          )}
        </div>
      )}

      <SecondaryRunDetails view={view} />
    </section>
  );
}

function SecondaryRunDetails({ view }: { view: RunView }) {
  return (
    <div className={styles.secondaryDetails}>
      <details>
        <summary>
          <span>Application environments</span>
          <span>{view.environments.length}</span>
        </summary>
        {view.environments.length > 0 ? (
          <ul className={styles.environmentList}>
            {view.environments.map((environment) => (
              <li key={`${environment.role}:${environment.devboxId}`}>
                <strong>{environment.role}</strong>
                <span>{environment.status}</span>
                {environment.exactSha ? <code>{environment.exactSha.slice(0, 8)}</code> : null}
              </li>
            ))}
          </ul>
        ) : (
          <p>No application environment has been provisioned.</p>
        )}
      </details>
      <details>
        <summary>
          <span>Frozen executable journey</span>
          <span>{view.journey?.steps.length ?? 0}</span>
        </summary>
        {view.journey ? (
          <ol className={styles.journeyList}>
            {view.journey.steps.map((step, index) => (
              <li key={`${step.action}:${index}`}>
                <span>{index + 1}</span>
                <strong>{step.action.replaceAll("_", " ")}</strong>
                <p>{describeJourneyStep(step)}</p>
              </li>
            ))}
          </ol>
        ) : (
          <p>Codex has not frozen a replayable journey.</p>
        )}
      </details>
    </div>
  );
}

function RunProgress({ view }: { view: RunView }) {
  return (
    <ol className={styles.phaseList} aria-label="Verification phases">
      {view.phases.map((phase) => (
        <li key={phase.id} data-state={phase.status}>
          <span className={styles.phaseMarker} aria-hidden="true" />
          <div>
            <strong>{phase.label}</strong>
            {phase.detail ? <p>{phase.detail}</p> : null}
          </div>
          <span>{phase.status}</span>
        </li>
      ))}
    </ol>
  );
}

function EnvironmentStrip({ view }: { view: RunView }) {
  if (view.environments.length === 0) {
    return null;
  }
  return (
    <div className={styles.environmentStrip} aria-label="Environment status">
      {view.environments.map((environment) => (
        <span key={`${environment.role}:${environment.devboxId}`}>
          <i data-state={environment.status} aria-hidden="true" />
          {environment.role}: {environment.status}
        </span>
      ))}
    </div>
  );
}

function PanelEmpty({ title, detail }: { title: string; detail: string }) {
  return (
    <div className={styles.panelEmpty}>
      <strong>{title}</strong>
      <p>{detail}</p>
    </div>
  );
}

function ActionButton({
  action,
  busyAction,
  onAction,
  secondary = false,
  children,
}: {
  action: string;
  busyAction?: string;
  onAction: (action: string) => void;
  secondary?: boolean;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      className={secondary ? styles.secondaryButton : styles.primaryButton}
      disabled={Boolean(busyAction)}
      onClick={() => onAction(action)}
    >
      {busyAction === action ? "Working..." : children}
    </button>
  );
}

function StatusGlyph({
  status,
  compact = false,
}: {
  status: DashboardItemStatus;
  compact?: boolean;
}) {
  const failed = status === "non_conformant" || status === "regressed";
  const passed = status === "conformant" || status === "preserved";
  return (
    <span
      className={styles.statusGlyph}
      data-status={status}
      data-compact={compact}
      aria-hidden="true"
    >
      <svg viewBox="0 0 20 20">
        {passed ? (
          <path d="m5.2 10.2 3.1 3.1 6.7-7" />
        ) : failed ? (
          <path d="m6.1 6.1 7.8 7.8m0-7.8-7.8 7.8" />
        ) : status === "pending" ? (
          <>
            <circle cx="10" cy="10" r="5.7" />
            <path d="M10 6.8v3.6l2.3 1.5" />
          </>
        ) : status === "uncovered" ? (
          <>
            <circle cx="10" cy="10" r="5.7" />
            <path d="m6 14 8-8" />
          </>
        ) : (
          <>
            <path d="M8.2 7.5A2 2 0 0 1 10.1 6c1.3 0 2.2.8 2.2 1.9 0 1.5-1.5 1.8-2.1 2.9" />
            <path d="M10.2 13.7h.01" />
          </>
        )}
      </svg>
    </span>
  );
}

function GroundtruthMark() {
  return (
    <svg className={styles.brandMark} viewBox="0 0 26 26" aria-hidden="true">
      <circle cx="6" cy="8" r="2.2" />
      <circle cx="19.5" cy="6" r="2.2" />
      <circle cx="20" cy="19" r="2.2" />
      <path d="M7.8 8.5 17.4 6.6m1.2 1.5 1 8.7M7.4 9.8 5.7 17a2.5 2.5 0 0 0 2.5 3h9.5" />
    </svg>
  );
}

function ExternalIcon() {
  return (
    <svg className={styles.externalIcon} viewBox="0 0 16 16" aria-hidden="true">
      <path d="M6 4h6v6M12 4 5 11" />
    </svg>
  );
}

function WarningIcon() {
  return (
    <svg className={styles.bannerIcon} viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 4 3.7 19h16.6zM12 9v4.5m0 2.8h.01" />
    </svg>
  );
}

function ActivityIcon() {
  return (
    <span className={styles.activityIcon} aria-hidden="true">
      <svg viewBox="0 0 24 24">
        <path d="M3 12h4l2-5 4 10 2-5h6" />
      </svg>
    </span>
  );
}

function CheckIcon() {
  return (
    <svg className={styles.checkIcon} viewBox="0 0 16 16" aria-hidden="true">
      <path d="m3.5 8.3 2.8 2.8 6.2-6.2" />
    </svg>
  );
}

function shouldShowContract(view: RunView): boolean {
  if (view.run.status === "verifying") {
    return false;
  }
  if (view.contract.status !== "approved") {
    return true;
  }
  const hasExecutionSurface =
    Boolean(view.journey) ||
    Boolean(view.recording) ||
    view.results.intent.length > 0 ||
    view.results.regression.length > 0;
  return view.run.status === "contract_approved" && !hasExecutionSurface;
}
