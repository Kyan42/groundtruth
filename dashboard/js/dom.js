// Building elements without ever parsing a string as HTML: every string becomes a text node
// or an attribute value, so text from a run document can never become markup.

export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  setAttrs(el, attrs);
  append(el, children);
  return el;
}

export function setAttrs(el, attrs) {
  if (!attrs) return el;
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "text") el.textContent = value;
    else if (key === "style" && typeof value === "object") Object.assign(el.style, value);
    else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2), value);
    else if (key in el && typeof value !== "string" && key !== "list") el[key] = value;
    else el.setAttribute(key, value === true ? "" : String(value));
  }
  return el;
}

export function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false || child === "") continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

const SVG = "http://www.w3.org/2000/svg";

function svg(width, height, d) {
  const s = document.createElementNS(SVG, "svg");
  s.setAttribute("width", width);
  s.setAttribute("height", height);
  s.setAttribute("viewBox", `0 0 ${width} ${height}`);
  s.setAttribute("aria-hidden", "true");
  s.setAttribute("focusable", "false");
  const p = document.createElementNS(SVG, "path");
  p.setAttribute("d", d);
  p.setAttribute("fill", "currentColor");
  s.append(p);
  return s;
}

export const playIcon = () => svg(12, 14, "M1 1l10 6-10 6z");
export const pauseIcon = () => svg(12, 14, "M1 1h3.5v12H1zM7.5 1H11v12H7.5z");

export function logo(size = 31) {
  return h("img", { src: "gt-mark.svg", width: size, height: size, alt: "" });
}

export function brandLink() {
  return h("a", { class: "brandlink", href: "index.html", "aria-label": "Groundtruth, all runs" },
    logo(), h("strong", { "aria-hidden": "true" }, "Groundtruth"));
}

// "Sign out", only on a hosted store's pages signed in with the cookie. The store sets gt_signed_in=1 beside its
// HttpOnly sign-in cookie for exactly this; a local store never does, so local pages show nothing new.
export function signOut() {
  if (!/(?:^|;\s*)gt_signed_in=1(?:;|$)/.test(document.cookie)) return null;
  return h("button", {
    class: "signout", type: "button",
    onclick: async (event) => {
      event.currentTarget.disabled = true;
      try {
        await fetch("/v1/session", { method: "DELETE", credentials: "same-origin" });
      } catch { /* signed out here either way */ }
      location.assign("/signin.html");
    },
  }, "Sign out");
}
