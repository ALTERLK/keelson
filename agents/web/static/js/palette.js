// Command palette (Ctrl/Cmd+K): jump to a page, start a replay, new session.

import { clear, h } from "./dom.js";
import { t } from "./i18n.js";

let commandsProvider = () => [];
let open = false;

export function setCommands(provider) {
  commandsProvider = provider;
}

function matches(command, query) {
  if (!query) return true;
  const haystack = `${command.label} ${command.hint || ""}`.toLowerCase();
  return query.toLowerCase().split(/\s+/).every((word) => haystack.includes(word));
}

export function openPalette() {
  if (open) return;
  open = true;
  const root = document.getElementById("palette-root");
  const previousFocus = document.activeElement;
  let selected = 0;
  let visible = [];

  const list = h("ul", { class: "palette-list", role: "listbox" });
  const input = h("input", {
    class: "palette-input", type: "text", placeholder: t("palette.placeholder"),
    "aria-label": t("palette.placeholder"), autocomplete: "off", spellcheck: "false",
  });

  function close() {
    open = false;
    clear(root);
    document.removeEventListener("keydown", onKey, true);
    previousFocus?.focus?.();
  }

  function run(command) {
    close();
    command.run();
  }

  function render() {
    visible = commandsProvider().filter((command) => matches(command, input.value.trim()));
    selected = Math.min(selected, Math.max(0, visible.length - 1));
    clear(list);
    if (!visible.length) {
      list.append(h("li", { class: "palette-empty" }, t("palette.empty")));
      return;
    }
    visible.forEach((command, index) => {
      list.append(h("li", {
        class: "palette-item" + (index === selected ? " selected" : ""), role: "option",
        "aria-selected": index === selected ? "true" : "false",
        onclick: () => run(command),
        onmousemove: () => { if (selected !== index) { selected = index; render(); } },
      }, h("span", { class: "palette-label" }, command.label), command.hint ? h("span", { class: "palette-hint" }, command.hint) : null));
    });
    list.querySelector(".selected")?.scrollIntoView({ block: "nearest" });
  }

  function onKey(event) {
    if (event.key === "Escape") { event.preventDefault(); close(); }
    else if (event.key === "ArrowDown") { event.preventDefault(); selected = (selected + 1) % Math.max(1, visible.length); render(); }
    else if (event.key === "ArrowUp") { event.preventDefault(); selected = (selected - 1 + visible.length) % Math.max(1, visible.length); render(); }
    else if (event.key === "Enter" && visible[selected]) { event.preventDefault(); run(visible[selected]); }
  }

  input.addEventListener("input", () => { selected = 0; render(); });
  document.addEventListener("keydown", onKey, true);
  root.append(h("div", { class: "palette-backdrop", onclick: (event) => { if (event.target === event.currentTarget) close(); } },
    h("div", { class: "palette", role: "dialog", "aria-modal": "true" }, input, list)));
  render();
  input.focus();
}

export function installPaletteShortcut() {
  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      openPalette();
    }
  });
}
