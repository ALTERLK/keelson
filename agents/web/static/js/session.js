// The console's controller: owns the state, and decides whether events come
// from the live stream or from a replay. It outlives route changes, so moving
// to another page and back does not lose the conversation.

import { getJSON, isStatic, post } from "./api.js";
import { apply, initialState } from "./store.js";
import { LiveStream, ReplayPlayer, normalizeTrace } from "./stream.js";

const listeners = new Set();

export const session = {
  state: initialState(),
  server: { live: false, model: "", permission_mode: "", modes: [] },
  mode: "idle",          // "live" | "replay" | "idle"
  connection: "closed",  // SSE status, live mode only
  player: null,
  traceId: null,
  traces: [],
};

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function notify() {
  for (const fn of listeners) fn();
}

function dispatch(event) {
  apply(session.state, event);
  if (event.type === "session") session.server = { ...session.server, ...event.data };
  notify();
}

let live = null;

function startLive() {
  stopReplay();
  session.mode = "live";
  session.state = initialState();
  live = new LiveStream(dispatch, (status) => { session.connection = status; notify(); });
  live.start();
  notify();
}

function stopLive() {
  if (live) live.stop();
  live = null;
}

function stopReplay() {
  if (session.player) session.player.destroy();
  session.player = null;
  session.traceId = null;
}

export async function loadReplay(traceId, { autoplay = true } = {}) {
  const trace = normalizeTrace(await getJSON(`traces/${traceId}`));
  stopLive();
  stopReplay();
  session.mode = "replay";
  session.traceId = traceId;
  session.state = initialState();
  session.player = new ReplayPlayer(trace, (event) => apply(session.state, event), notify);
  notify();
  if (autoplay) session.player.play();
}

export function restartReplay() {
  if (session.traceId) return loadReplay(session.traceId);
}

export function exitReplay() {
  if (!session.server.live) return;
  startLive();
}

export async function boot() {
  try {
    session.traces = await getJSON("traces");
  } catch {
    session.traces = [];
  }
  if (!isStatic) {
    try {
      session.server = await getJSON("state");
    } catch {
      session.server = { live: false };
    }
  }
  // Deep link: ?trace=<id> opens that recording, &skip=1 jumps to its end.
  const params = new URLSearchParams(location.search);
  const wanted = session.traces.find((trace) => trace.id === params.get("trace"));
  if (wanted) {
    await loadReplay(wanted.id, { autoplay: !params.has("skip") });
    if (params.has("skip")) session.player.finish();
  } else if (session.server.live) startLive();
  else if (session.traces.length) await loadReplay(session.traces[0].id);
  notify();
}

// -- user actions (live mode) ------------------------------------------------

export const actions = {
  send: (message) => post("chat", { message }),
  stop: () => post("abort"),
  reset: () => post("reset"),
  setMode: (mode) => post("mode", { mode }),
  confirm: (request_id, allow) => post("confirm", { request_id, allow }),
};
