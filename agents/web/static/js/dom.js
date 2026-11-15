// Tiny DOM helpers — the whole "framework" this front end uses.

/**
 * h("div", {class: "x", onclick: fn}, child, ...) -> HTMLElement
 * Strings become text nodes, so untrusted text can never be parsed as HTML.
 */
export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "html") el.innerHTML = value; // only for output of markdown.js, which escapes first
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (value === true) el.setAttribute(key, "");
    else el.setAttribute(key, value);
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    if (Array.isArray(child)) append(el, child);
    else el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

const SVG_NS = "http://www.w3.org/2000/svg";

export function svg(tag, attrs, ...children) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    el.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

export function fmtCount(n) {
  n = Number(n) || 0;
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1000) return (n / 1000).toFixed(1) + "k";
  return String(n);
}

export function fmtPct(x, digits = 1) {
  return (Number(x) * 100).toFixed(digits) + "%";
}

export function fmtDuration(seconds) {
  if (seconds === null || seconds === undefined) return "";
  if (seconds < 1) return Math.round(seconds * 1000) + "ms";
  if (seconds < 60) return seconds.toFixed(1) + "s";
  return Math.floor(seconds / 60) + "m " + Math.round(seconds % 60) + "s";
}

// One shared tooltip, driven by a `data-tip` attribute on any element.
export function installTooltips() {
  const tip = document.getElementById("tooltip");
  let current = null;
  document.addEventListener("pointermove", (event) => {
    const target = event.target.closest?.("[data-tip]");
    if (!target) {
      if (current) { tip.hidden = true; current = null; }
      return;
    }
    if (target !== current) {
      current = target;
      tip.textContent = target.dataset.tip;
      tip.hidden = false;
    }
    const pad = 14;
    const rect = tip.getBoundingClientRect();
    const x = Math.min(event.clientX + pad, window.innerWidth - rect.width - 8);
    const y = event.clientY + pad + rect.height > window.innerHeight ? event.clientY - rect.height - pad : event.clientY + pad;
    tip.style.transform = `translate(${Math.max(8, x)}px, ${Math.max(8, y)}px)`;
  });
}
