// Reading from the results store (store/CONTRACT.md). Same origin as the pages.

export class StoreError extends Error {}

export async function getJSON(url) {
  let res;
  try {
    res = await fetch(url, { headers: { Accept: "application/json" } });
  } catch {
    throw new StoreError("The results store could not be reached.");
  }
  let body = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  if (res.status === 401 && body && typeof body.sign_in === "string" && body.sign_in.startsWith("/") && !body.sign_in.startsWith("//")) {
    // A hosted store whose sign-in has ended: to the sign-in page, which comes back here.
    location.assign(`${body.sign_in}?next=${encodeURIComponent(location.pathname + location.search + location.hash)}`);
    throw new StoreError("Sign in to see this.");
  }
  if (!res.ok) {
    const said = body && typeof body.error === "string" ? body.error : `The store answered ${res.status}.`;
    throw new StoreError(said);
  }
  return body;
}

// The newest 500 run summaries (the most the store gives at once), of one project when it is named.
export const listRuns = (project) => getJSON(`/v1/runs?limit=500${project ? `&project=${encodeURIComponent(project)}` : ""}`);
export const getRun = (id) => getJSON(`/v1/runs/${encodeURIComponent(id)}`);

export function fileURL(base, path) {
  const root = typeof base === "string" && base ? base : "";
  return root + String(path || "").split("/").map(encodeURIComponent).join("/");
}
