// Entry point: hash router, session list, command palette, theme and language.

import { getJSON, isStatic } from "./api.js";
import { clear, h, installTooltips } from "./dom.js";
import { applyStatic, getLang, onLangChange, setLang, t } from "./i18n.js";
import { installPaletteShortcut, openPalette, setCommands } from "./palette.js";
import {
  boot, closeSession, ensureSomethingToShow, loadReplay, newSession, openLive, refreshSessions, session, subscribe,
} from "./session.js";
import { attempt } from "./toast.js";
import * as benchView from "./views/bench.js";
import * as casesView from "./views/cases.js";
import * as consoleView from "./views/console.js";
import * as homeView from "./views/home.js";
import * as skillsView from "./views/skills.js";
import * as tracesView from "./views/traces.js";

const routes = {
  home: homeView, console: consoleView, cases: casesView, skills: skillsView, bench: benchView, traces: tracesView,
};

const view = document.getElementById("view");
let unmount = null;
let currentRoute = null;

function routeName() {
  const name = location.hash.replace(/^#\/?/, "").split("?")[0];
  return routes[name] ? name : "home";
}

function show(force = false) {
  const name = routeName();
  if (name === currentRoute && !force) return;
  currentRoute = name;
  if (unmount) unmount();
  clear(view);
  view.dataset.route = name;
  view.scrollTop = 0;
  unmount = routes[name].mount(view);
  for (const link of document.querySelectorAll("#nav a")) {
    const active = link.dataset.route === name;
    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
  }
}

// -- status pill ---------------------------------------------------------------

function renderStatus() {
  const pill = document.getElementById("status");
  const text = document.getElementById("status-text");
  let kind = "offline", label = t("status.offline");
  if (session.mode === "live") {
    kind = session.connection === "open" ? "live" : "wait";
    label = session.connection === "open" ? `${t("status.live")} · ${session.state.info?.model || ""}` : t("status.reconnecting");
  } else if (session.mode === "replay") {
    kind = "replay";
    label = t("status.replay");
  } else if (isStatic || !session.server.live) {
    kind = "replay";
    label = t("status.replay");
  }
  pill.dataset.kind = kind;
  text.textContent = label;
  document.getElementById("version").textContent = session.server.version ? `v${session.server.version}` : "";
}

// -- session list (live mode only) -----------------------------------------------

const sessionsEl = document.getElementById("sessions");
let sessionsKey = "";

function renderSessions() {
  if (!session.server.live) { clear(sessionsEl); return; }
  // Titles and busy flags come from the stream of the session being viewed
  // and from the periodic listing for the others.
  const current = session.state.info || {};
  const rows = session.live.map((row) => (row.id === session.id
    ? { ...row, busy: session.state.busy, title: row.title || firstPrompt() }
    : row));
  const key = JSON.stringify([rows.map((r) => [r.id, r.title, r.busy]), session.history.map((r) => r.id), session.id, session.mode,
    session.traceId, current.model, getLang()]);
  if (key === sessionsKey) return;
  sessionsKey = key;

  const openSession = (id) => { openLive(id); location.hash = "#/console"; };
  clear(sessionsEl).append(
    h("div", { class: "sessions-head" },
      h("span", {}, t("sessions.title")),
      h("button", { type: "button", class: "ghost small", title: t("sessions.new"), onclick: () => attempt(async () => { await newSession(); location.hash = "#/console"; }) }, "+")),
    h("ul", { class: "session-list" }, rows.map((row) =>
      h("li", { class: "session-row" + (session.mode === "live" && row.id === session.id ? " active" : "") },
        h("button", { type: "button", class: "session-open", onclick: () => openSession(row.id) },
          h("span", { class: "session-dot" + (row.busy ? " busy" : "") }),
          h("span", { class: "session-title" }, row.title || t("sessions.untitled"))),
        h("button", { type: "button", class: "session-close", title: t("sessions.close"), "aria-label": t("sessions.close"),
          onclick: () => attempt(() => closeSession(row.id)) }, "×")))),
    session.history.length ? h("div", { class: "sessions-head" }, h("span", {}, t("sessions.history"))) : null,
    session.history.length ? h("ul", { class: "session-list" }, session.history.slice(0, 8).map((row) =>
      h("li", { class: "session-row past" + (session.mode === "replay" && row.id === session.traceId ? " active" : "") },
        h("button", { type: "button", class: "session-open",
          onclick: async () => { await loadReplay(row.id, { autoplay: false }); session.player.finish(); location.hash = "#/console"; } },
          h("span", { class: "session-title" }, row.title || row.id))))) : null,
  );
}

function firstPrompt() {
  return session.state.items.find((item) => item.kind === "user")?.text.slice(0, 80) || "";
}

// -- theme & language ----------------------------------------------------------

function currentTheme() {
  return document.documentElement.dataset.theme
    || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
}

function toggleTheme() {
  const next = currentTheme() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("keelson-theme", next); } catch { /* ignore */ }
}

const langToggle = document.getElementById("lang-toggle");
function renderLangToggle() { langToggle.textContent = getLang() === "zh" ? "EN" : "中"; }
const toggleLang = () => setLang(getLang() === "zh" ? "en" : "zh");

document.getElementById("theme-toggle").addEventListener("click", toggleTheme);
langToggle.addEventListener("click", toggleLang);
document.getElementById("palette-trigger").addEventListener("click", openPalette);

onLangChange(() => { renderLangToggle(); renderStatus(); sessionsKey = ""; renderSessions(); show(true); });

// -- command palette -----------------------------------------------------------

setCommands(() => {
  const go = (route) => () => { location.hash = route === "home" ? "#/" : `#/${route}`; };
  const commands = Object.keys(routes).filter((route) => route !== "cases" || hasCases)
    .map((route) => ({ label: t(`nav.${route}`), hint: t("palette.go"), run: go(route) }));
  for (const trace of session.traces) {
    commands.push({
      label: trace.title, hint: t("palette.replay"),
      run: async () => { await loadReplay(trace.id, { guided: true }); location.hash = "#/console"; },
    });
  }
  if (session.server.live) {
    commands.push({ label: t("palette.newSession"), run: () => attempt(async () => { await newSession(); location.hash = "#/console"; }) });
  }
  commands.push({ label: t("palette.theme"), run: toggleTheme }, { label: t("palette.lang"), run: toggleLang });
  return commands;
});

// -- start ---------------------------------------------------------------------

// The case-study page only appears once the study has been run in this install.
let hasCases = false;
const casesLink = document.querySelector('#nav a[data-route="cases"]');
casesLink.hidden = true;
getJSON("casestudy").then((study) => { hasCases = Boolean(study.available); casesLink.hidden = !hasCases; }).catch(() => {});

// ?lang=en|zh and ?theme=light|dark pin the presentation for a shared link.
const params = new URLSearchParams(location.search);
if (["en", "zh"].includes(params.get("lang"))) setLang(params.get("lang"));
if (["light", "dark"].includes(params.get("theme"))) document.documentElement.dataset.theme = params.get("theme");

document.documentElement.lang = getLang() === "zh" ? "zh-CN" : "en";
applyStatic();
renderLangToggle();
installTooltips();
installPaletteShortcut();
subscribe(renderStatus);
subscribe(renderSessions);
window.addEventListener("hashchange", () => show());
show();
boot().then(() => {
  renderStatus();
  renderSessions();
  if (currentRoute === "console") ensureSomethingToShow();
  else if (currentRoute !== "skills" && currentRoute !== "bench") show(true); // pages that list traces or status
  // Other tabs and finished turns change the listing; keep the side bar honest.
  if (session.server.live) setInterval(refreshSessions, 5000);
});
