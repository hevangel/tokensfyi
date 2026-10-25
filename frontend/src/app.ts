// @ts-nocheck
/** Small progressive enhancements; core journeys remain usable without JavaScript. */
const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex=\"-1\"])"
].join(",");

function focusErrorSummary() {
  const summary = document.querySelector("[data-error-summary]");
  if (summary) window.requestAnimationFrame(() => summary.focus());
}

function setupDialogs() {
  document.querySelectorAll("dialog[data-accessible-dialog]").forEach((dialog) => {
    let returnTarget = null;
    document.querySelectorAll(`[data-dialog-open=\"${dialog.id}\"]`).forEach((trigger) => {
      trigger.addEventListener("click", () => {
        returnTarget = trigger;
        if (!dialog.open) dialog.showModal();
        const first = dialog.querySelector("[autofocus], button, input, select, textarea");
        first?.focus();
      });
    });
    dialog.addEventListener("keydown", (event) => {
      if (event.key !== "Tab") return;
      const focusable = Array.from(dialog.querySelectorAll(FOCUSABLE)).filter(
        (element) => element.offsetParent !== null
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });
    dialog.addEventListener("close", () => returnTarget?.focus());
  });
}

export function boot() {
  document.documentElement.dataset.enhanced = "true";
  focusErrorSummary();
  setupDialogs();
}

if (typeof document !== "undefined") boot();
