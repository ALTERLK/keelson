// A yes/no question in the console's own dialog style. window.confirm would do,
// but in the desktop app it is headed "127.0.0.1:<port> says", which explains nothing.

import { h } from "./dom.js";
import { t } from "./i18n.js";

/** Resolves true when the user agrees. `details` (lines) are shown under the question. */
export function ask(question, { details = [], okLabel = t("ask.ok"), danger = false } = {}) {
  return new Promise((resolve) => {
    const host = h("div", { class: "ask-root" });
    const finish = (answer) => {
      document.removeEventListener("keydown", onKey, true);
      host.remove();
      resolve(answer);
    };
    const onKey = (event) => {
      if (event.key === "Escape") { event.preventDefault(); finish(false); }
    };
    const ok = h("button", { type: "button", class: danger ? "btn danger" : "btn primary", onclick: () => finish(true) }, okLabel);
    host.append(h("div", {
      class: "modal-backdrop",
      onclick: (event) => { if (event.target === event.currentTarget) finish(false); },
    },
      h("div", { class: "modal", role: "alertdialog", "aria-modal": "true", "aria-labelledby": "ask-title" },
        h("h2", { id: "ask-title" }, question),
        details.length ? h("pre", { class: "confirm-action" }, details.join("\n")) : null,
        h("div", { class: "modal-actions" },
          h("button", { type: "button", class: "btn", onclick: () => finish(false) }, t("ask.cancel")),
          ok,
        ),
      ),
    ));
    document.body.append(host);
    document.addEventListener("keydown", onKey, true);
    ok.focus();
  });
}
