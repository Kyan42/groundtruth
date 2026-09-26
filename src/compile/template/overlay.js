// Video overlays, injected into every page before its own scripts run (exploration and replay alike):
// - a visible mouse pointer (headless browsers draw none) that follows the automated mouse, glides
//   between positions, pulses on clicks, and remembers where it was across page loads;
// - window.__gtShowCheck(title, code, passed): a banner naming the claim and the Playwright assertion
//   being checked. window.__gtHideCheck() removes it.
// Both are aria-hidden with pointer-events: none, so the page's accessibility tree and clicks ignore them.
(() => {
  if (window.__gtOverlay) return;
  window.__gtOverlay = true;
  const KEY = "__gt_cursor";
  const EASE = "transform .5s cubic-bezier(.25,.8,.3,1)";
  const root = () => document.body || document.documentElement;

  let cursor;
  const makeCursor = () => {
    cursor = document.createElement("div");
    cursor.setAttribute("aria-hidden", "true");
    cursor.setAttribute("data-gt-overlay", "");
    cursor.style.cssText = `position:fixed;left:0;top:0;z-index:2147483647;pointer-events:none;transition:${EASE};will-change:transform;`
      + "filter:drop-shadow(0 2px 3px rgba(0,0,0,.35))";
    cursor.innerHTML = '<svg width="24" height="24" viewBox="0 0 24 24"><path d="M3 2l7.5 19 2.6-7.9L21 10.5z" fill="#111" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>';
    root().appendChild(cursor);
  };
  const place = (x, y, instant) => {
    if (!cursor || !cursor.isConnected) makeCursor();
    if (instant) cursor.style.transition = "none";
    cursor.style.transform = `translate(${x - 3}px, ${y - 2}px)`;
    if (instant) requestAnimationFrame(() => { cursor.style.transition = EASE; });
    try { sessionStorage.setItem(KEY, JSON.stringify({ x, y })); } catch {}
  };
  addEventListener("mousemove", (e) => place(e.clientX, e.clientY), true);
  addEventListener("mousedown", () => cursor && cursor.animate([{ scale: "1" }, { scale: "0.75" }, { scale: "1" }], { duration: 280 }), true);
  const start = () => {
    let pos = null;
    try { pos = JSON.parse(sessionStorage.getItem(KEY) || "null"); } catch {}
    place(pos ? pos.x : innerWidth * 0.5, pos ? pos.y : innerHeight * 0.55, true);
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();

  window.__gtShowCheck = (title, code, passed) => {
    window.__gtHideCheck();
    const colour = passed ? "#0b8259" : "#c03d29";
    const box = document.createElement("div");
    box.id = "__groundtruth_check";
    box.setAttribute("aria-hidden", "true");
    box.setAttribute("data-gt-overlay", "");
    box.style.cssText = `position:fixed;top:14px;left:14px;z-index:2147483647;pointer-events:none;max-width:min(760px,80vw);`
      + `background:${colour};color:#fff;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.3);overflow:hidden;`
      + "font:600 17px/1.35 system-ui,-apple-system,'Segoe UI',sans-serif";
    const head = document.createElement("div");
    head.style.cssText = "padding:9px 14px 8px";
    head.textContent = `${passed ? "✓" : "✗"} ${title}`;
    const body = document.createElement("div");
    body.style.cssText = "padding:8px 14px 10px;background:rgba(0,0,0,.28);font:500 14px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;white-space:pre-wrap;word-break:break-word";
    body.textContent = code;
    box.append(head, body);
    root().appendChild(box);
  };
  window.__gtHideCheck = () => document.getElementById("__groundtruth_check")?.remove();
})();
