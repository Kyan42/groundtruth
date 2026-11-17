// The sign-in page of a hosted store: the token is sent once to POST /v1/session, which sets the sign-in cookie
// (store/CONTRACT.md). The token itself is never kept in the browser. Afterwards the page goes back to where it came from.

function backTo() {
  const asked = new URLSearchParams(location.search).get("next");
  if (asked) {
    try {
      const url = new URL(asked, location.origin);       // only somewhere on this site, never another one
      if (url.origin === location.origin && url.pathname !== "/signin.html") return url.pathname + url.search + url.hash;
    } catch { /* not an address */ }
  }
  return "/index.html";
}

const form = document.getElementById("signin");
const input = document.getElementById("token");
const said = document.getElementById("said");
const button = form.querySelector("button");

function say(text) {
  said.textContent = text;
  said.hidden = !text;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const token = input.value.trim();
  if (!token) {
    say("Paste your token first.");
    input.focus();
    return;
  }
  button.disabled = true;
  say("");
  try {
    const res = await fetch("/v1/session", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify({ token }),
    });
    if (res.ok) {
      input.value = "";
      location.replace(backTo());
      return;
    }
    let why = `The store answered ${res.status}.`;
    try {
      const body = await res.json();
      if (body && typeof body.error === "string") why = body.error;
    } catch { /* not JSON */ }
    say(why);
  } catch {
    say("The store could not be reached. Check your connection and try again.");
  }
  button.disabled = false;
  input.focus();
});
