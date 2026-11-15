// Entry point: hash router, theme and language toggles, status pill.

import { clear, installTooltips } from "./dom.js";
import { applyStatic, getLang, onLangChange, setLang, t } from "./i18n.js";
import { boot, session, subscribe } from "./session.js";
import * as benchView from "./views/bench.js";
import * as consoleView from "./views/console.js";
import * as skillsView from "./views/skills.js";
import * as tracesView from "./views/traces.js";

const routes = { console: consoleView, skills: skillsView, bench: benchView, traces: tracesView };

const view = document.getElementById("view");
let unmount = null;
let currentRoute = null;

function routeName() {
  const name = location.hash.replace(/^#\/?/, "").split("?")[0];
  return routes[name] ? name : "console";
}

function show(force = false) {
  const name = routeName();
  if (name === currentRoute && !force) return;
  currentRoute = name;
  if (unmount) unmount();
  clear(view);
  view.dataset.route = name;
  unmount = routes[name].mount(view);
  for (const link of document.querySelectorAll("#nav a")) {
    link.classList.toggle("active", link.dataset.route === name);
    if (link.dataset.route === name) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
  }
}

// -- status pill ---------------------------------------------------------------

function renderStatus() {
  const pill = document.getElementById("status");
  const text = document.getElementById("status-text");
  let kind = "offline", label = t("status.offline");
  if (session.mode === "live") {
    kind = session.connection === "open" ? "live" : "wait";
    label = session.connection === "open" ? `${t("status.live")} · ${session.server.model || ""}` : t("status.reconnecting");
  } else if (session.mode === "replay") {
    kind = "replay";
    label = t("status.replay");
  }
  pill.dataset.kind = kind;
  text.textContent = label;
}

// -- theme & language ----------------------------------------------------------

function currentTheme() {
  return document.documentElement.dataset.theme
    || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
}

document.getElementById("theme-toggle").addEventListener("click", () => {
  const next = currentTheme() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("keelson-theme", next); } catch { /* ignore */ }
});

const langToggle = document.getElementById("lang-toggle");
function renderLangToggle() { langToggle.textContent = getLang() === "zh" ? "EN" : "中"; }
langToggle.addEventListener("click", () => setLang(getLang() === "zh" ? "en" : "zh"));

onLangChange(() => { renderLangToggle(); renderStatus(); show(true); });

// -- start ---------------------------------------------------------------------

// ?lang=en|zh and ?theme=light|dark pin the presentation for a shared link.
const params = new URLSearchParams(location.search);
if (["en", "zh"].includes(params.get("lang"))) setLang(params.get("lang"));
if (["light", "dark"].includes(params.get("theme"))) document.documentElement.dataset.theme = params.get("theme");

document.documentElement.lang = getLang() === "zh" ? "zh-CN" : "en";
applyStatic();
renderLangToggle();
installTooltips();
subscribe(renderStatus);
window.addEventListener("hashchange", () => show());
show();
boot().then(() => {
  renderStatus();
  if (currentRoute === "traces") show(true); // the trace list arrives with boot()
});
