// Traces view: pick a recorded run and replay it in the console.

import { fmtDuration, h } from "../dom.js";
import { t } from "../i18n.js";
import { loadReplay, session } from "../session.js";

async function open(traceId, guided) {
  await loadReplay(traceId, { guided });
  location.hash = "#/console";
}

function card(trace) {
  return h("article", { class: "card trace" },
    h("div", { class: "trace-main" },
      h("h2", {}, trace.title),
      trace.description ? h("p", { class: "card-note" }, trace.description) : null,
      h("div", { class: "trace-meta" },
        trace.model ? h("span", { class: "chip" }, trace.model) : null,
        h("span", {}, `${trace.events} ${t("traces.events")}`),
        h("span", {}, `${trace.tool_calls} ${t("traces.tools")}`),
        h("span", {}, fmtDuration(trace.duration_s)),
        trace.recorded_at ? h("span", {}, trace.recorded_at.slice(0, 10)) : null)),
    h("div", { class: "trace-actions" },
      h("button", { type: "button", class: "btn primary", onclick: () => open(trace.id, true) }, t("traces.tour")),
      h("button", { type: "button", class: "btn", onclick: () => open(trace.id, false) }, t("traces.play"))));
}

export function mount(root) {
  const traces = session.traces;
  root.append(h("div", { class: "page" },
    h("header", { class: "page-head" }, h("h1", {}, t("traces.title")), h("p", {}, t("traces.lead"))),
    traces.length ? h("div", { class: "trace-list" }, traces.map(card)) : h("p", { class: "muted" }, t("traces.empty")),
    session.history.length
      ? [h("h2", { class: "section-title" }, t("sessions.history")), h("div", { class: "trace-list" }, session.history.map(card))]
      : null,
  ));
  return () => {};
}
