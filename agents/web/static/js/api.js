// Data access. The same front end runs in two places:
//   live   — served by `keelson web`; JSON comes from /api/*
//   static — the hosted demo; the same JSON was exported to data/*.json at build time
// Views call getJSON("bench") and never need to know which one they are in.

const mode = document.querySelector('meta[name="keelson-mode"]')?.content || "live";

export const isStatic = mode === "static";

export async function getJSON(name) {
  const url = isStatic ? `data/${name}.json` : `api/${name}`;
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`${url}: ${response.status}`);
  return response.json();
}

async function send(method, name, body) {
  if (isStatic) throw new Error("read-only demo");
  const response = await fetch(`api/${name}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  if (!response.ok) {
    let detail = String(response.status);
    try { detail = (await response.json()).detail || detail; } catch { /* not JSON */ }
    throw new Error(detail);
  }
  return response.json();
}

export const post = (name, body) => send("POST", name, body);
export const del = (name) => send("DELETE", name);
