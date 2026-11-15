// Charts, drawn by hand in SVG/CSS — no charting library.
//
// Conventions: bars are thin with a rounded data end and a square base; text
// uses text colours and never the series colour; each series colour is always
// paired with a visible label; every chart has a "view as table" twin so no
// value is reachable only by hovering.

import { fmtPct, h, svg } from "./dom.js";
import { t } from "./i18n.js";

const SERIES = ["var(--series-1)", "var(--series-2)", "var(--series-3)"];

export function seriesColor(index) {
  return SERIES[index % SERIES.length];
}

export function dataTable(columns, rows) {
  return h("details", { class: "table-view" },
    h("summary", {}, t("bench.table")),
    h("div", { class: "table-wrap" },
      h("table", {},
        h("thead", {}, h("tr", {}, columns.map((col) => h("th", {}, col)))),
        h("tbody", {}, rows.map((row) => h("tr", {}, row.map((cell) => h("td", {}, cell))))))));
}

export function legend(labels) {
  return h("div", { class: "legend" }, labels.map((label, i) =>
    h("span", { class: "legend-item" }, h("span", { class: "swatch", style: `background:${seriesColor(i)}` }), label)));
}

/** Horizontal bars on a shared 0–100% scale. rows: [{label, value, color, note, tip}] */
export function barList(rows) {
  return h("div", { class: "barlist" }, rows.map((row) =>
    h("div", { class: "barlist-row", "data-tip": row.tip || `${row.label}: ${fmtPct(row.value)}` },
      h("div", { class: "barlist-label" }, row.label),
      h("div", { class: "barlist-track" },
        h("div", { class: "barlist-bar", style: `width:${(row.value * 100).toFixed(2)}%;background:${row.color}` })),
      h("div", { class: "barlist-value" }, fmtPct(row.value), row.note ? h("span", { class: "muted" }, " " + row.note) : null))));
}

function columnPath(x, y, w, height, r) {
  const radius = Math.min(r, height, w / 2);
  return `M${x},${y + height} V${y + radius} Q${x},${y} ${x + radius},${y} H${x + w - radius} Q${x + w},${y} ${x + w},${y + radius} V${y + height} Z`;
}

/** Grouped columns. groups: [{label, values:[...]}], series: [name, ...]; values are 0..1 */
export function groupedColumns(groups, series, { axisLabel = "" } = {}) {
  const W = 640, H = 260, left = 44, right = 12, top = 14, bottom = 46;
  const plotW = W - left - right, plotH = H - top - bottom;
  const band = plotW / groups.length;
  const barW = Math.min(22, (band - 24) / series.length - 2);
  const groupW = series.length * barW + (series.length - 1) * 2;
  const y = (v) => top + plotH * (1 - v);

  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img" });
  for (const tick of [0, 0.25, 0.5, 0.75, 1]) {
    root.append(
      svg("line", { x1: left, x2: W - right, y1: y(tick), y2: y(tick), class: tick === 0 ? "axis" : "grid" }),
      svg("text", { x: left - 8, y: y(tick) + 4, class: "tick", "text-anchor": "end" }, `${tick * 100}%`));
  }
  groups.forEach((group, gi) => {
    const x0 = left + band * gi + (band - groupW) / 2;
    group.values.forEach((value, si) => {
      const x = x0 + si * (barW + 2);
      const height = plotH * value;
      const tip = `${series[si]} · ${group.label}: ${fmtPct(value)}${group.n ? ` (n=${group.n})` : ""}`;
      if (height > 0.5) {
        root.append(svg("path", { d: columnPath(x, y(value), barW, height, 4), fill: seriesColor(si), "data-tip": tip }));
      } else { // a real zero still gets a mark, so it reads as "0", not "missing"
        root.append(svg("rect", { x, y: y(0) - 2, width: barW, height: 2, fill: seriesColor(si), "data-tip": tip }));
      }
      // hit target larger than the mark
      root.append(svg("rect", { x: x - 1, y: top, width: barW + 2, height: plotH, fill: "transparent", "data-tip": tip }));
    });
    root.append(svg("text", { x: left + band * gi + band / 2, y: H - bottom + 18, class: "tick", "text-anchor": "middle" }, group.label));
  });
  if (axisLabel) root.append(svg("text", { x: left + plotW / 2, y: H - 8, class: "axis-label", "text-anchor": "middle" }, axisLabel));
  return root;
}

/** Forest plot of differences with intervals. rows: [{label, delta, low, high, significant}] */
export function forest(rows) {
  const W = 420, rowH = 46, left = 150, right = 92, top = 10, bottom = 30;
  const H = top + rows.length * rowH + bottom;
  const lo = Math.min(0, ...rows.map((r) => r.low)), hi = Math.max(0, ...rows.map((r) => r.high));
  const pad = (hi - lo) * 0.12 || 0.01;
  const min = lo - pad, max = hi + pad;
  const x = (v) => left + ((v - min) / (max - min)) * (W - left - right);
  const pts = (v) => `${v >= 0 ? "+" : "−"}${Math.abs(v * 100).toFixed(1)}`;

  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img" });
  root.append(svg("line", { x1: x(0), x2: x(0), y1: top, y2: H - bottom, class: "axis" }));
  root.append(svg("text", { x: x(0), y: H - 10, class: "tick", "text-anchor": "middle" }, "0"));
  for (const edge of [lo, hi]) {
    if (Math.abs(edge) < 1e-9) continue;
    root.append(svg("text", { x: x(edge), y: H - 10, class: "tick", "text-anchor": "middle" }, `${pts(edge)} pts`));
  }

  rows.forEach((row, i) => {
    const cy = top + rowH * i + rowH / 2;
    const tip = `${row.label}: ${pts(row.delta)} pts, 95% CI [${pts(row.low)}, ${pts(row.high)}]`;
    root.append(
      svg("text", { x: 0, y: cy + 4, class: "row-label" }, row.label),
      svg("line", { x1: x(row.low), x2: x(row.high), y1: cy, y2: cy, class: "whisker" + (row.significant ? " sig" : "") }),
      svg("circle", { cx: x(row.delta), cy, r: 6, class: "dot" + (row.significant ? " sig" : "") }),
      svg("text", { x: W - right + 12, y: cy - 2, class: "row-value" }, `${pts(row.delta)} pts`),
      svg("text", { x: W - right + 12, y: cy + 13, class: "tick" }, t(row.significant ? "bench.pairs.sig" : "bench.pairs.ns")),
      svg("rect", { x: left - 8, y: cy - rowH / 2, width: W - left - right + 16, height: rowH, fill: "transparent", "data-tip": tip }));
  });
  return root;
}
