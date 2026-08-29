"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { RunViewSchema } from "@/lib/domain/schemas";

export function CreateRunForm() {
  const router = useRouter();
  const [prUrl, setPrUrl] = useState("");
  const [error, setError] = useState<string>();
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(undefined);
    try {
      const response = await fetch("/api/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prUrl }),
      });
      const payload: unknown = await response.json();
      if (!response.ok) {
        throw new Error(readApiError(payload));
      }
      const view = RunViewSchema.parse(payload);
      router.push(`/runs/${view.run.id}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not create the run.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="create-form" onSubmit={submit}>
      <label htmlFor="pr-url">Public pull request URL</label>
      <div className="input-row">
        <input
          id="pr-url"
          name="pr-url"
          type="url"
          required
          autoComplete="url"
          placeholder="https://github.com/owner/repo/pull/123"
          value={prUrl}
          onChange={(event) => setPrUrl(event.target.value)}
        />
        <button type="submit" disabled={submitting}>
          {submitting ? "Creating run..." : "Verify PR"}
        </button>
      </div>
      {error ? <p className="form-error">{error}</p> : null}
    </form>
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
  return "Could not create the run.";
}
