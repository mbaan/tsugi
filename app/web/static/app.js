// theme: persisted, applied pre-paint in base.html; the settings slide-over
// <select> is the only switcher (the nav toggle button is gone)
function setTheme(t) {
  localStorage.setItem("theme", t);
  document.documentElement.dataset.theme = t;
}
setTheme(localStorage.getItem("theme") || "auto");

// settings slide-over has a theme <select>; it's client-side only
document.body.addEventListener("change", (e) => {
  if (e.target.name === "theme") setTheme(e.target.value);
  if (e.target.dataset.discardDialog !== undefined && e.target.value === "discarded") {
    htmx.ajax("GET", `/works/${e.target.dataset.work}/discard`, "#overlay");
  }
});
document.body.addEventListener("htmx:afterSwap", (e) => {
  const sel = e.detail.target.querySelector?.('select[name="theme"]');
  if (sel) sel.value = localStorage.getItem("theme") || "auto";
});

// overlay: server partials land in #overlay; Esc / backdrop / X close it
function closeOverlay() {
  const overlay = document.getElementById("overlay");
  if (!overlay.firstChild) return;
  overlay.replaceChildren();
  // a work modal pushed /works/{id}; restore the list URL
  if (location.pathname.startsWith("/works/")) history.back();
}
window.closeOverlay = closeOverlay;
document.body.addEventListener("close-overlay", closeOverlay);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeOverlay(); });
document.addEventListener("click", (e) => {
  const overlay = document.getElementById("overlay");
  if (overlay.firstChild && !e.target.closest("#overlay > *") && !e.target.closest("[hx-target='#overlay']") && !e.target.closest("[hx-get]")) {
    closeOverlay();
  }
  if (!e.target.closest(".nav-search")) {
    document.getElementById("search-results").replaceChildren();
  }
});

// toasts replace alert()
function toast(message, kind = "error") {
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.textContent = message;
  document.getElementById("toasts").append(el);
  setTimeout(() => el.remove(), 4000);
}
document.body.addEventListener("htmx:responseError", (e) => {
  if (e.detail.xhr.status !== 404) toast(`Request failed (${e.detail.xhr.status})`);
});
document.body.addEventListener("htmx:sendError", () => toast("Network error"));
document.body.addEventListener("crawl-queued", () => toast("Crawl started — progress appears in the top bar", "info"));
document.body.addEventListener("marked-read", () => toast("Marked as Read", "info"));
document.body.addEventListener("refresh-started", () => toast("Catalog refresh started", "info"));

// star rows secretly act like a slider: press anywhere on the row, drag to the
// value (live preview via :checked), release commits exactly one POST.
document.body.addEventListener("click", (e) => {
  if (e.target.closest("form.stars label")) e.preventDefault(); // selection is pointer-driven
}, true);
document.body.addEventListener("pointerdown", (e) => {
  const form = e.target.closest("form.stars");
  if (!form || e.button !== 0 || e.target.closest(".clear")) return;
  e.preventDefault();
  const prior = form.querySelector("input[type=radio]:checked");
  let current = null;
  const badge = document.createElement("span"); // ★ N.N readout while dragging
  badge.className = "drag-val";
  form.append(badge);
  const preview = (x, y) => {
    const label = document.elementFromPoint(x, y)?.closest("form.stars label");
    if (!label || label.closest("form") !== form) return;
    const radio = document.getElementById(label.htmlFor);
    if (radio && radio !== current) {
      radio.checked = true; current = radio;
      badge.textContent = "★ " + (radio.value / 2).toFixed(1);
      badge.style.left = label.offsetLeft + label.offsetWidth / 2 + "px";
    }
  };
  preview(e.clientX, e.clientY);
  const move = (ev) => preview(ev.clientX, ev.clientY);
  const finish = (commit) => () => {
    badge.remove();
    document.removeEventListener("pointermove", move);
    document.removeEventListener("pointerup", up);
    document.removeEventListener("pointercancel", cancel);
    if (commit && current && current !== prior) {
      current.dispatchEvent(new Event("change", { bubbles: true }));
    } else if (!commit && current) {
      if (prior) prior.checked = true; else current.checked = false;
    }
  };
  const up = finish(true);
  const cancel = finish(false); // e.g. touch turned into a vertical scroll
  document.addEventListener("pointermove", move);
  document.addEventListener("pointerup", up);
  document.addEventListener("pointercancel", cancel);
});
