// Benchmarks view. Draws what /api/bench computed from the raw run artifacts.

import { getJSON } from "../api.js";
import { barList, dataTable, forest, groupedColumns, legend, seriesColor } from "../charts.js";
import { clear, fmtCount, fmtPct, h } from "../dom.js";
import { t } from "../i18n.js";

const signed = (v) => `${v >= 0 ? "+" : "−"}${Math.abs(v * 100).toFixed(1)}`;

function tile(label, value, detail, tone = "") {
  return h("div", { class: "tile" },
    h("div", { class: "tile-label" }, label),
    h("div", { class: `tile-value ${tone}` }, value),
    h("div", { class: "tile-detail" }, detail));
}

function card(title, note, ...body) {
  return h("section", { class: "card" }, h("h2", {}, title), note ? h("p", { class: "card-note" }, note) : null, ...body);
}

const SPAN_ORDER = ["model_stream", "tool", "permission", "skill_retrieval", "fold", "confirm_wait"];
const ms = (value) => (value >= 1000 ? `${(value / 1000).toFixed(1)} s` : value >= 10 ? `${value.toFixed(0)} ms` : `${value.toFixed(2)} ms`);

function perfCard(perf) {
  if (!perf?.available) return null;
  const names = SPAN_ORDER.filter((name) => perf.spans[name]);
  return card(t("bench.perf.title"), `${t("bench.perf.note")} (n = ${perf.traces})`,
    h("div", { class: "table-wrap" },
      h("table", { class: "grid-table" },
        h("thead", {}, h("tr", {}, ["bench.perf.span", "bench.perf.count", "bench.perf.p50", "bench.perf.p95", "bench.perf.share"].map((key) => h("th", {}, t(key))))),
        h("tbody", {}, names.map((name) => {
          const span = perf.spans[name];
          return h("tr", {},
            h("td", {}, t(`span.${name}`)),
            h("td", {}, span.count),
            h("td", {}, ms(span.p50_ms)),
            h("td", {}, ms(span.p95_ms)),
            h("td", {}, name === "confirm_wait" ? "—" : h("div", { class: "share" },
              h("span", { class: "share-bar", style: `width:${Math.max(1, span.share * 100)}%` }), fmtPct(span.share))));
        })))));
}

export function mount(root) {
  root.append(h("div", { class: "page" }, h("p", { class: "muted" }, t("common.loading"))));
  let alive = true;

  Promise.all([getJSON("bench"), getJSON("perf").catch(() => null)]).then(([bench, perf]) => {
    if (!alive) return;
    const page = clear(root.querySelector(".page"));
    page.append(h("header", { class: "page-head" }, h("h1", {}, t("bench.title")), h("p", {}, t("bench.lead"))));

    if (!bench.available) {
      page.append(h("p", { class: "muted" }, t("bench.unavailable")));
      return;
    }

    const { toolhop, sandbox } = bench;
    const { before, after, delta } = toolhop.fix;
    const armNames = Object.keys(toolhop.arms);
    const arms = armNames.map((name) => toolhop.arms[name]);
    const sandboxRuns = sandbox.reduce((sum, cell) => sum + cell.n, 0);
    const tampered = sandbox.reduce((sum, cell) => sum + Math.round(cell.test_tamper_rate * cell.n), 0);

    page.append(h("div", { class: "tiles" },
      tile(t("bench.tile.accuracy"), fmtPct(after.accuracy), `${t("bench.tile.from")} ${fmtPct(before.accuracy)} · ${signed(delta.delta)} pts`),
      tile(t("bench.tile.cap"), `${Math.round(after.hit_cap_rate * after.n_rows)} / ${after.n_rows}`,
        `${t("bench.tile.from")} ${Math.round(before.hit_cap_rate * before.n_rows)} / ${before.n_rows}`),
      tile(t("bench.tile.tamper"), String(tampered), `${t("bench.tile.of")} ${sandboxRuns} ${t("bench.tile.runs")}`),
    ));

    // 1. the fix, before vs after
    page.append(card(t("bench.fix.title"), `${toolhop.dataset} · ${toolhop.model}. ${t("bench.fix.note")}`,
      h("div", { class: "split" },
        barList([
          { label: t("bench.fix.before"), value: before.accuracy, color: "var(--border-strong)", note: `${before.n_rows} ${t("bench.rows")}` },
          { label: t("bench.fix.after"), value: after.accuracy, color: seriesColor(0), note: `${after.n_rows} ${t("bench.rows")}` },
        ]),
        h("div", { class: "delta" },
          h("div", { class: "delta-label" }, t("bench.fix.delta")),
          h("div", { class: "delta-value" }, `${signed(delta.delta)} pts`),
          h("div", { class: "muted" }, `95% CI [${signed(delta.ci_low)}, ${signed(delta.ci_high)}]`),
        ),
      ),
      dataTable(["", t("bench.accuracy"), t("bench.tile.cap"), "avg model calls"], [
        [t("bench.fix.before"), fmtPct(before.accuracy), fmtPct(before.hit_cap_rate), before.avg_model_calls.toFixed(1)],
        [t("bench.fix.after"), fmtPct(after.accuracy), fmtPct(after.hit_cap_rate), after.avg_model_calls.toFixed(1)],
      ]),
    ));

    // 2. arms + paired differences
    page.append(h("div", { class: "cards-2" },
      card(t("bench.arms.title"), t("bench.arms.note"),
        barList(arms.map((arm, i) => ({ label: arm.label, value: arm.accuracy, color: seriesColor(i), note: `n=${arm.n_rows}` }))),
        dataTable([t("bench.col.arm"), t("bench.accuracy"), t("bench.tile.cap"), "avg model calls"],
          arms.map((arm) => [arm.label, fmtPct(arm.accuracy), fmtPct(arm.hit_cap_rate), arm.avg_model_calls.toFixed(1)])),
      ),
      card(t("bench.pairs.title"), t("bench.pairs.note"),
        forest(toolhop.comparisons.map((c) => ({
          label: `${c.a} − ${c.b}`, delta: c.delta, low: c.ci_low, high: c.ci_high, significant: c.significant,
        }))),
        dataTable(["A − B", "Δ", "95% CI", ""], toolhop.comparisons.map((c) => [
          `${c.a} − ${c.b}`, `${signed(c.delta)} pts`, `[${signed(c.ci_low)}, ${signed(c.ci_high)}]`,
          t(c.significant ? "bench.pairs.sig" : "bench.pairs.ns"),
        ])),
      ),
    ));

    // 3. hop depth
    const hops = Object.keys(arms[0].by_hops);
    page.append(card(t("bench.hops.title"), t("bench.hops.note"),
      legend(armNames),
      groupedColumns(hops.map((hop) => ({
        label: hop, n: arms[0].by_hops[hop]?.n, values: arms.map((arm) => arm.by_hops[hop]?.accuracy ?? 0),
      })), armNames, { axisLabel: t("bench.hops.axis") }),
      dataTable([t("bench.hops.axis"), ...armNames], hops.map((hop) => [hop, ...arms.map((arm) => fmtPct(arm.by_hops[hop]?.accuracy ?? 0))])),
    ));

    // 4. sandbox — exact figures in a small grid read better as a table than as a chart
    page.append(card(t("bench.sandbox.title"), t("bench.sandbox.note"),
      h("div", { class: "table-wrap" },
        h("table", { class: "grid-table" },
          h("thead", {}, h("tr", {}, ["bench.col.arm", "bench.col.model", "bench.col.solve", "bench.col.tamper", "bench.col.scope", "bench.col.turns", "bench.col.tokens"]
            .map((key) => h("th", {}, t(key))))),
          h("tbody", {}, sandbox.map((cell) => h("tr", {},
            h("td", {}, h("code", {}, cell.arm)),
            h("td", {}, cell.model),
            h("td", {}, `${fmtPct(cell.solve_rate)} `, h("span", { class: "muted" }, `(${Math.round(cell.solve_rate * cell.n)}/${cell.n})`)),
            h("td", {}, fmtPct(cell.test_tamper_rate, 0)),
            h("td", {}, fmtPct(cell.out_of_scope_rate, 0)),
            h("td", {}, cell.avg_turns.toFixed(1)),
            h("td", {}, fmtCount(Math.round(cell.avg_input_tokens))),
          )))))));

    const overhead = perfCard(perf);
    if (overhead) page.append(overhead);
  }).catch((error) => {
    if (alive) clear(root.querySelector(".page")).append(h("p", { class: "notice error" }, `${t("common.error")}: ${error.message}`));
  });

  return () => { alive = false; };
}
