// How each kind of tool call is shown once expanded.
//
// A command reads best as a terminal, an edit as a diff, a file as highlighted
// code. Anything without a dedicated view falls back to input and output.

import { fmtCount, h } from "./dom.js";
import { highlight, languageOf } from "./highlight.js";
import { t } from "./i18n.js";

export function diffView(text) {
  const lines = String(text || "").split("\n").filter((line, i, all) => !(i === all.length - 1 && line === ""));
  return h("pre", { class: "diff" }, lines.map((line) => {
    let kind = "";
    if (line.startsWith("+++") || line.startsWith("---")) kind = "file";
    else if (line.startsWith("@@")) kind = "hunk";
    else if (line.startsWith("+")) kind = "add";
    else if (line.startsWith("-")) kind = "del";
    return h("span", { class: `diff-line ${kind}` }, line || " ");
  }));
}

function code(text, language) {
  return h("pre", { class: "code" }, h("code", { html: highlight(text, language) }));
}

function label(text, extra = "") {
  return h("div", { class: "tool-label" }, text, extra);
}

function terminal(item) {
  const output = item.done ? item.result : item.live;
  return h("div", { class: "terminal" },
    h("div", { class: "terminal-line prompt" }, h("span", { class: "terminal-sigil" }, "$"), item.input?.command || ""),
    output
      ? h("pre", { class: "terminal-output" + (item.status === "error" ? " failed" : "") }, output)
      : (!item.done && item.status === "running" ? h("div", { class: "terminal-line muted" }, t("tool.live")) : null),
  );
}

function fileChange(item) {
  if (!item.result) return code(JSON.stringify(item.input ?? {}, null, 2), "json");
  const [headline, ...rest] = item.result.split("\n");
  const body = rest.join("\n").trim();
  // edit_file returns a "- old / + new" diff; write_file returns numbered lines.
  const isDiff = /^(@@|[-+] )/m.test(body);
  return h("div", {},
    h("div", { class: "tool-headline" }, headline),
    body ? (isDiff ? diffView(body) : code(body, languageOf(item.input?.file_path))) : null);
}

function fileList(item) {
  const names = item.result.split("\n").filter(Boolean);
  if (names.length > 60 || names.some((name) => name.length > 80) || /^(No files|Error)/.test(item.result)) {
    return h("pre", {}, item.result);
  }
  return h("ul", { class: "file-list" }, names.map((name) => h("li", {}, name)));
}

const views = {
  run_shell: terminal,
  edit_file: fileChange,
  write_file: fileChange,
  read_file: (item) => (item.result ? code(item.result, languageOf(item.input?.file_path)) : null),
  list_files: (item) => (item.result ? fileList(item) : null),
};

export function renderToolBody(item) {
  const denied = item.gate?.action === "deny" ? h("div", { class: "tool-deny" }, item.gate.message) : null;
  const truncated = item.chars > item.result.length ? ` · ${fmtCount(item.chars)} chars` : "";
  const view = views[item.name];

  if (view) {
    return h("div", { class: "tool-body" }, denied, view(item), truncated ? h("div", { class: "muted small" }, truncated.slice(3)) : null);
  }
  return h("div", { class: "tool-body" },
    label(t("tool.input")),
    code(JSON.stringify(item.input ?? {}, null, 2), "json"),
    denied,
    item.result ? [label(t("tool.output"), truncated), h("pre", {}, item.result)] : null,
  );
}

// A one-glyph icon per tool family, drawn as an inline SVG path.
const icons = {
  read_file: "M5 3h7l3 3v11H5zM12 3v3h3",
  write_file: "M4 16l1-4 8-8 3 3-8 8zM11.500 5.500l3 3",
  edit_file: "M4 16l1-4 8-8 3 3-8 8zM11.500 5.500l3 3",
  list_files: "M3 6h5l1.500 2H17v8H3z",
  grep_search: "M9 9m-5 0a5 5 0 1 0 10 0a5 5 0 1 0-10 0M13 13l4 4",
  run_shell: "M3 5h14v10H3zM6 8.500l2.500 2L6 12.500M10.500 12.500h3.500",
  skill: "M10 3l6 3.500v7L10 17l-6-3.500v-7zM10 10l6-3.500M10 10v7M10 10L4 6.500",
  agent: "M6 8h8v7H6zM10 5v3M8 11.500h.010M12 11.500h.010M4 11h2M14 11h2",
};

export function toolIcon(name) {
  const path = icons[name] || (name.startsWith("skill") ? icons.skill : "M10 4v12M4 10h12");
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 20 20");
  svg.setAttribute("class", "tool-icon");
  svg.setAttribute("aria-hidden", "true");
  const el = document.createElementNS("http://www.w3.org/2000/svg", "path");
  el.setAttribute("d", path);
  svg.append(el);
  return svg;
}
