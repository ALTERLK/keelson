// Case study: real issues from real projects, with the outcome of each run.
// The numbers come from bench/casestudy/results.json, written by the runner.

import { getJSON } from "../api.js";
import { clear, fmtCount, fmtDuration, h } from "../dom.js";
import { getLang, t } from "../i18n.js";
import { loadReplay } from "../session.js";
import { diffView } from "../toolview.js";

function local(entry) {
  if (!entry || typeof entry === "string") return entry || "";
  return entry[getLang()] ?? entry.en ?? "";
}

function tile(label, value, detail) {
  return h("div", { class: "tile" },
    h("div", { class: "tile-label" }, label),
    h("div", { class: "tile-value" }, value),
    h("div", { class: "tile-detail" }, detail));
}

function caseCard(item) {
  const tests = item.fail_to_pass || { passed: 0, total: 0 };
  const regressions = (item.regressions || []).length;
  return h("section", { class: `card case ${item.resolved ? "passed" : "failed"}` },
    h("div", { class: "case-head" },
      h("div", {},
        h("div", { class: "case-project" }, `${item.project} #${item.issue}`),
        h("h2", {}, item.title)),
      h("span", { class: `chip ${item.resolved ? "ok" : "bad"}` }, t(item.resolved ? "cases.pass" : "cases.fail"))),
    h("p", { class: "card-note" }, local(item.summary)),
    h("div", { class: "case-meta" },
      h("span", {}, `${t("cases.f2p")} ${tests.passed}/${tests.total}`),
      h("span", { class: regressions ? "bad" : "" }, `${t("cases.regressions")} ${item.tests_timed_out ? t("cases.timedOut") : regressions}`),
      h("span", {}, `${t("cases.col.turns")} ${item.model_calls ?? "—"}`),
      h("span", {}, `${t("cases.tools")} ${item.tool_calls ?? "—"}`),
      h("span", {}, `${t("cases.col.tokens")} ${fmtCount(item.input_tokens || 0)}`),
      h("span", {}, `${t("cases.col.time")} ${fmtDuration(item.wall_time_s || 0)}`)),
    local(item.notes) ? h("p", { class: "case-notes" }, h("strong", {}, t("cases.notes") + " — "), local(item.notes)) : null,
    h("details", { class: "case-diffs" },
      h("summary", {}, t("cases.compare")),
      h("div", { class: "cards-2" },
        h("div", {}, h("h4", {}, t("cases.diff.agent")), item.agent_diff ? diffView(item.agent_diff) : h("p", { class: "muted" }, t("cases.diff.none"))),
        h("div", {}, h("h4", {}, t("cases.diff.upstream")), diffView(item.upstream_diff)))),
    h("div", { class: "case-actions" },
      item.trace ? h("button", {
        type: "button", class: "btn primary",
        onclick: async () => { await loadReplay(item.trace, { guided: false }); location.hash = "#/console"; },
      }, t("cases.replay")) : null,
      h("a", { class: "btn", href: item.issue_url, target: "_blank", rel: "noopener" }, t("cases.col.issue")),
      h("a", { class: "btn", href: item.fix_url, target: "_blank", rel: "noopener" }, t("cases.upstream"))));
}

export function mount(root) {
  const page = h("div", { class: "page" }, h("p", { class: "muted" }, t("common.loading")));
  root.append(page);
  let alive = true;

  getJSON("casestudy").then((study) => {
    if (!alive) return;
    clear(page).append(h("header", { class: "page-head" }, h("h1", {}, t("cases.title")), h("p", {}, t("cases.lead"))));
    if (!study.available) {
      page.append(h("p", { class: "muted" }, t("cases.unavailable")));
      return;
    }

    const cases = study.cases || [];
    const resolved = cases.filter((c) => c.resolved).length;
    const weakened = cases.filter((c) => c.test_lines_removed > 0).length;
    const average = (key) => (cases.length ? cases.reduce((sum, c) => sum + (c[key] || 0), 0) / cases.length : 0);
    page.append(h("div", { class: "tiles" },
      tile(study.model || "", `${resolved} / ${cases.length}`, t("cases.solved")),
      tile(t("cases.tamper.label"), String(weakened), t("cases.tamper")),
      tile(t("cases.avg.label"), average("model_calls").toFixed(1), `${fmtDuration(average("wall_time_s"))} ${t("cases.avg.detail")}`)));

    page.append(h("section", { class: "card" },
      h("h2", {}, t("cases.method.title")),
      h("ol", { class: "method" }, ["cases.method.1", "cases.method.2", "cases.method.3"].map((key) => h("li", {}, t(key)))),
      h("p", { class: "card-note" }, `${t("cases.method.note")} ${study.permission_mode} · max ${study.max_turns} · ${study.generated}`)));

    for (const item of cases) page.append(caseCard(item));
  }).catch((error) => {
    if (alive) clear(page).append(h("p", { class: "notice error" }, `${t("common.error")}: ${error.message}`));
  });

  return () => { alive = false; };
}
