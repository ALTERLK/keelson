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

// Text with `code spans`, as an array of nodes.
function rich(text) {
  return String(text || "").split("`").map((part, i) => (i % 2 ? h("code", {}, part) : part));
}

function tile(label, value, detail) {
  return h("div", { class: "tile" },
    h("div", { class: "tile-label" }, label),
    h("div", { class: "tile-value" }, value),
    h("div", { class: "tile-detail" }, detail));
}

function verdict(resolved) {
  return h("span", { class: `chip ${resolved ? "ok" : "bad"}` }, t(resolved ? "cases.pass" : "cases.fail"));
}

// The same issues, round by round. Every round after the first follows a fix
// to the runtime that the round before it exposed.
function roundsCard(study) {
  const cases = study.cases.filter((c) => (c.earlier || []).length);
  if (!cases.length) return null;
  const label = (n) => t("cases.rounds.round").replace("{n}", n);
  const info = (n) => (study.rounds || []).find((r) => r.round === n);
  const numbers = [...new Set(cases.flatMap((c) => c.earlier.map((e) => e.round)))].sort((a, b) => a - b);
  const columns = [
    ...numbers.map((n) => ({ n, pick: (c) => c.earlier.find((e) => e.round === n) })),
    { n: study.round, pick: (c) => c },
  ];
  const cost = (calls, tokens) => h("span", { class: "round-cost" }, `${calls} ${t("cases.rounds.calls")} · ${fmtCount(tokens)}`);
  const cell = (run) => (run
    ? h("td", {}, verdict(run.resolved), cost(run.model_calls ?? "—", run.input_tokens || 0))
    : h("td", { class: "muted" }, "—"));
  const total = (runs) => {
    const sum = (key) => runs.reduce((acc, run) => acc + (run[key] || 0), 0);
    return h("td", {}, h("strong", {}, `${runs.filter((run) => run.resolved).length} / ${runs.length}`),
      cost(sum("model_calls"), sum("input_tokens")));
  };
  return h("section", { class: "card" },
    h("h2", {}, t("cases.rounds.title")),
    h("p", { class: "card-note" }, t("cases.rounds.lead")),
    h("div", { class: "table-wrap" },
      h("table", { class: "grid-table rounds" },
        h("thead", {}, h("tr", {}, h("th", {}, t("cases.col.issue")), columns.map((col) => h("th", {}, label(col.n))))),
        h("tbody", {},
          cases.map((item) => h("tr", {},
            h("td", {}, h("code", {}, `${item.project} #${item.issue}`)),
            columns.map((col) => cell(col.pick(item))))),
          h("tr", { class: "total" }, h("td", {}, t("cases.rounds.total")),
            columns.map((col) => total(cases.map(col.pick).filter(Boolean))))))),
    h("ol", { class: "method rounds-log" }, columns.map((col) => h("li", {},
      h("strong", {}, local(info(col.n)?.label) || label(col.n)), " — ", rich(local(info(col.n)?.change))))),
    h("p", { class: "card-note" }, t("cases.rounds.caveat")));
}

function caseCard(item, study) {
  const tests = item.fail_to_pass || { passed: 0, total: 0 };
  const regressions = (item.regressions || []).length;
  return h("section", { class: `card case ${item.resolved ? "passed" : "failed"}` },
    h("div", { class: "case-head" },
      h("div", {},
        h("div", { class: "case-project" }, `${item.project} #${item.issue}`),
        h("h2", {}, item.title)),
      h("span", { class: `chip ${item.resolved ? "ok" : "bad"}` }, t(item.resolved ? "cases.pass" : "cases.fail"))),
    h("p", { class: "card-note" }, rich(local(item.summary))),
    h("div", { class: "case-meta" },
      h("span", {}, `${t("cases.f2p")} ${tests.passed}/${tests.total}`),
      h("span", { class: regressions ? "bad" : "" }, `${t("cases.regressions")} ${item.tests_timed_out ? t("cases.timedOut") : regressions}`),
      h("span", {}, `${t("cases.col.turns")} ${item.model_calls ?? "—"}`),
      h("span", {}, `${t("cases.tools")} ${item.tool_calls ?? "—"}`),
      h("span", {}, `${t("cases.col.tokens")} ${fmtCount(item.input_tokens || 0)}`),
      h("span", {}, `${t("cases.col.time")} ${fmtDuration(item.wall_time_s || 0)}`)),
    local(item.notes) ? h("p", { class: "case-notes" }, h("strong", {}, t("cases.notes") + " — "), rich(local(item.notes))) : null,
    h("details", { class: "case-diffs" },
      h("summary", {}, t("cases.compare")),
      h("div", { class: "cards-2" },
        h("div", {}, h("h4", {}, t("cases.diff.agent")), item.agent_diff ? diffView(item.agent_diff) : h("p", { class: "muted" }, t("cases.diff.none"))),
        h("div", {}, h("h4", {}, t("cases.diff.upstream")), diffView(item.upstream_diff)))),
    h("div", { class: "case-actions" },
      item.trace ? h("button", {
        type: "button", class: "btn primary",
        onclick: async () => { await loadReplay(item.trace, { guided: false }); location.hash = "#/console"; },
      }, study.round > 1 ? t("cases.replayBefore").replace("{n}", study.round) : t("cases.replay")) : null,
      (item.earlier || []).filter((run) => run.trace).map((run) => h("button", {
        type: "button", class: "btn",
        onclick: async () => { await loadReplay(run.trace, { guided: false }); location.hash = "#/console"; },
      }, t("cases.replayBefore").replace("{n}", run.round))),
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

    const rounds = roundsCard(study);
    if (rounds) page.append(rounds);

    for (const item of cases) page.append(caseCard(item, study));
  }).catch((error) => {
    if (alive) clear(page).append(h("p", { class: "notice error" }, `${t("common.error")}: ${error.message}`));
  });

  return () => { alive = false; };
}
