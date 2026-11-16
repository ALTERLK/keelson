// Brief, non-blocking messages (errors from actions, "copied", and so on).

import { h } from "./dom.js";

export function toast(message, tone = "") {
  const root = document.getElementById("toast-root");
  const el = h("div", { class: `toast ${tone}`, role: tone === "bad" ? "alert" : "status" }, message);
  root.append(el);
  const remove = () => { el.classList.add("leaving"); setTimeout(() => el.remove(), 200); };
  setTimeout(remove, tone === "bad" ? 6000 : 2600);
  el.addEventListener("click", remove);
}

/** Run an action; if it fails, say why instead of failing silently. */
export async function attempt(action) {
  try {
    return await action();
  } catch (error) {
    toast(error.message || String(error), "bad");
    return undefined;
  }
}
