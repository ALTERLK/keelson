// The desktop app's own page (desktop.html): first-run wizard and project picker.
//
// Steps: 1 model (DeepSeek preset, key tested before it is saved) → 2 project
// folder (checks detected, KEELSON.md offered) → 3 a first task. Afterwards the
// page is only the project picker. Every action goes through
// window.pywebview.api — see keelson/desktop.py.

import { clear, h } from "./dom.js";
import { getLang, setLang, t } from "./i18n.js";
import { attempt, toast } from "./toast.js";

const root = document.getElementById("wizard");
let info = null;          // what api.boot() returned
let project = null;       // api.inspect() of the folder being set up
let busy = "";            // a label while a long call runs

const api = () => window.pywebview.api;

// -- frame -------------------------------------------------------------------------

function brand() {
  return h("div", { class: "wizard-brand" },
    h("span", { class: "brand-name" }, "Keelson"),
    h("span", { class: "muted small" }, info?.version ? `v${info.version}` : ""),
    h("span", { class: "spacer" }),
    h("button", { type: "button", class: "ghost", onclick: () => { setLang(getLang() === "zh" ? "en" : "zh"); render(); } },
      getLang() === "zh" ? "EN" : "中"),
  );
}

function steps(current) {
  const names = ["desktop.step.model", "desktop.step.project", "desktop.step.task"];
  return h("ol", { class: "wizard-steps" }, names.map((key, i) =>
    h("li", { class: i + 1 === current ? "active" : i + 1 < current ? "done" : "" },
      h("span", { class: "wizard-step-n" }, String(i + 1)), t(key))));
}

function page(stepNo, ...body) {
  // Element.append(null) would print "null": only append what is there.
  const parts = [brand(), stepNo ? steps(stepNo) : null, h("section", { class: "wizard-card" }, ...body)];
  clear(root).append(...parts.filter(Boolean));
}

function working(label) {
  busy = label;
  clear(root).append(brand(), h("section", { class: "wizard-card center" },
    h("span", { class: "pulse" }), h("p", {}, label)));
}

// -- step 1: the model ---------------------------------------------------------

function modelStep() {
  const presets = info.presets;
  const select = h("select", { class: "select wide", id: "preset" },
    presets.map((p, i) => h("option", { value: i }, p.name)));
  const base = h("input", { class: "field", id: "base", value: presets[0].base, spellcheck: "false" });
  const model = h("input", { class: "field", id: "model", value: presets[0].model, spellcheck: "false" });
  const key = h("input", { class: "field", id: "key", type: "password", autocomplete: "off", placeholder: "sk-…" });
  const result = h("p", { class: "wizard-result", role: "status" });
  select.addEventListener("change", () => {
    const p = presets[Number(select.value)];
    base.value = p.base; model.value = p.model;
  });
  const values = () => [Number(select.value), base.value, model.value, key.value];

  const testButton = h("button", { type: "button", class: "btn", onclick: async () => {
    result.className = "wizard-result"; result.textContent = t("desktop.model.testing");
    const answer = await attempt(() => api().test_model(...values()));
    if (!answer) { result.textContent = ""; return; }
    result.className = "wizard-result " + (answer.ok ? "ok" : "bad");
    result.textContent = (answer.ok ? "✓ " : "✗ ") + answer.detail;
  } }, t("desktop.model.test"));

  const saveButton = h("button", { type: "button", class: "btn primary", onclick: async () => {
    result.className = "wizard-result"; result.textContent = t("desktop.model.testing");
    const tested = await attempt(() => api().test_model(...values()));
    if (!tested) { result.textContent = ""; return; }
    if (!tested.ok) {
      result.className = "wizard-result bad";
      result.textContent = "✗ " + tested.detail + "  " + t("desktop.model.fix");
      return;
    }
    const saved = await attempt(() => api().save_model(...values()));
    if (!saved?.ok) { result.className = "wizard-result bad"; result.textContent = saved?.detail || ""; return; }
    key.value = "";
    info.config = saved.config;
    toast(t("desktop.model.saved") + saved.path);
    if (info.pending_folder) return chooseProject(info.pending_folder);
    projectStep();
  } }, t("desktop.model.save"));

  page(1,
    h("h1", {}, t("desktop.model.title")),
    h("p", { class: "muted" }, t("desktop.model.body")),
    h("label", { class: "wizard-field" }, h("span", {}, t("desktop.model.provider")), select),
    h("label", { class: "wizard-field" }, h("span", {}, t("desktop.model.base")), base),
    h("label", { class: "wizard-field" }, h("span", {}, t("desktop.model.name")), model),
    h("label", { class: "wizard-field" }, h("span", {}, t("desktop.model.key")), key),
    h("p", { class: "muted small" }, t("desktop.model.where") + info.config_path),
    result,
    h("div", { class: "wizard-actions" }, testButton, saveButton),
  );
  key.focus();
}

// -- step 2: the project ---------------------------------------------------------

async function chooseProject(path) {
  const picked = path || await attempt(() => api().choose_folder());
  if (!picked) return;
  const inspected = await attempt(() => api().inspect(picked));
  if (!inspected) return;
  if (!inspected.ok) { toast(inspected.error, "bad"); return; }
  project = inspected;
  taskStep();
}

function recentList() {
  const rows = info.recent || [];
  if (!rows.length) return null;
  return h("div", { class: "wizard-recent" },
    h("h3", {}, t("desktop.project.recent")),
    h("ul", {}, rows.map((row) => h("li", { class: row.exists ? "" : "missing" },
      h("button", { type: "button", class: "recent-open", disabled: !row.exists, title: row.path,
        onclick: () => open(row.path) },
        h("strong", {}, row.name), h("span", { class: "muted small" }, row.path),
        row.path === info.current ? h("span", { class: "chip brand" }, t("desktop.project.current")) : null),
      h("button", { type: "button", class: "ghost", title: t("desktop.project.forget"), "aria-label": t("desktop.project.forget"),
        onclick: async () => { info.recent = await attempt(() => api().forget(row.path)) || info.recent; projectStep(); } }, "×")))));
}

function projectStep() {
  const firstRun = !(info.recent || []).length;
  page(firstRun ? 2 : 0,
    h("h1", {}, t(firstRun ? "desktop.project.title" : "desktop.project.pick")),
    h("p", { class: "muted" }, t("desktop.project.body")),
    h("div", { class: "wizard-actions start" },
      h("button", { type: "button", class: "btn primary large", onclick: () => chooseProject() }, t("desktop.project.choose")),
      info.current ? h("button", { type: "button", class: "btn large", onclick: () => open(info.current) }, t("desktop.project.back")) : null),
    recentList(),
    h("p", { class: "muted small" }, t("desktop.model.using") + (info.config.model || "—"), " · ",
      h("a", { href: "#", onclick: (e) => { e.preventDefault(); modelStep(); } }, t("desktop.model.change"))),
  );
}

// -- step 3: checks and a first task ------------------------------------------------

function taskStep() {
  const p = project;
  const checks = p.checks.length
    ? h("div", { class: "wizard-checks" },
        h("p", {}, h("strong", {}, t("desktop.checks.found")), " ", p.checks.map((c) => h("code", {}, c))),
        p.checks_source === "KEELSON.md"
          ? h("p", { class: "muted small" }, t("desktop.checks.inmd"))
          : h("button", { type: "button", class: "btn", onclick: async () => {
              const saved = await attempt(() => api().save_checks(p.path));
              if (saved?.ok) { toast(t("desktop.checks.saved") + saved.path); p.checks_source = "KEELSON.md"; taskStep(); }
              else if (saved) toast(saved.detail, "bad");
            } }, t("desktop.checks.save")))
    : h("p", { class: "wizard-result warn" }, t("desktop.checks.none"));
  page((info.recent || []).length ? 0 : 3,
    h("h1", {}, p.name),
    h("p", { class: "muted small" }, p.path),
    checks,
    h("p", { class: "muted small" }, t("desktop.checks.why")),
    h("h3", {}, t("desktop.task.title")),
    h("div", { class: "suggestions" }, p.suggestions.map((text) =>
      h("button", { type: "button", class: "suggestion", onclick: () => open(p.path, text) }, text))),
    h("div", { class: "wizard-actions" },
      h("button", { type: "button", class: "btn quiet", onclick: projectStep }, t("desktop.back")),
      h("button", { type: "button", class: "btn primary", onclick: () => open(p.path) }, t("desktop.task.open"))),
  );
}

// -- opening a project --------------------------------------------------------------

async function open(path, draft = "") {
  if (busy) return;
  working(t("desktop.opening") + path);
  const result = await attempt(() => api().open_project(path, draft));
  busy = "";
  if (result?.ok) { location.replace(result.url); return; }   // on to the console
  if (result) {
    page(0, h("h1", {}, t("desktop.failed")), h("p", {}, result.error || ""),
      result.log ? h("pre", { class: "wizard-log" }, result.log) : null,
      h("div", { class: "wizard-actions" }, h("button", { type: "button", class: "btn", onclick: projectStep }, t("desktop.back"))));
  } else projectStep();
}

// -- start ----------------------------------------------------------------------------

async function start() {
  info = await attempt(() => api().boot());
  if (!info) return;
  if (!info.config.configured) return modelStep();
  if (info.autostart && location.hash !== "#projects") return open(info.autostart);
  projectStep();
}

function render() {
  if (!info) return;
  if (!info.config.configured) modelStep();
  else if (project) taskStep();
  else projectStep();
}

document.documentElement.lang = getLang() === "zh" ? "zh-CN" : "en";
if (window.pywebview?.api) start();
else window.addEventListener("pywebviewready", start, { once: true });
