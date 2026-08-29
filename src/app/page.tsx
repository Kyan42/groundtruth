import { CreateRunForm } from "@/components/create-run-form";

export default function HomePage() {
  return (
    <main className="landing-shell">
      <div className="brand">
        <span className="brand-mark">GT</span>
        <span>Groundtruth</span>
      </div>
      <section className="hero">
        <div className="eyebrow">Intent-aware PR verification</div>
        <h1>Did the pull request do what it said?</h1>
        <p className="hero-copy">
          Groundtruth turns real pull-request prose into an independently validated intent contract,
          then prepares browser missions without inventing claims or results.
        </p>
        <CreateRunForm />
        <p className="fine-print">
          Public GitHub pull requests only. Missing Runloop or Reflex configuration is reported
          explicitly.
        </p>
      </section>
      <section className="trust-row" aria-label="Prototype guarantees">
        <span>Exact head SHA</span>
        <span>Immutable Axon audit</span>
        <span>Human contract approval</span>
        <span>No canned verdicts</span>
      </section>
    </main>
  );
}
