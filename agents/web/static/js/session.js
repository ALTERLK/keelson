// The console's controller: owns the state, and decides whether events come
// from a live session's stream or from a replay. It outlives route changes, so
// moving to another page and back does not lose the conversation.

import { del, getJSON, isStatic, post } from "./api.js";
import { captionFor } from "./guide.js";
import { apply, initialState } from "./store.js";
import { LiveStream, ReplayPlayer, normalizeTrace } from "./stream.js";

const listeners = new Set();
const STORAGE_KEY = "keelson-session"; // per tab, so two tabs can hold two sessions

export const session = {
  server: { live: false, version: "", modes: [], workspace: "" },
  mode: "idle",          // "live" | "replay" | "idle"
  id: null,              // the live session this tab is attached to
  connection: "closed",  // SSE status, live mode only
  state: initialState(),
  player: null,
  traceId: null,
  traceMeta: {},
  guided: false,         // pause and explain at key events during a replay
  caption: null,         // {key, anchor, callId} while the tour is paused
  traces: [],            // recordings shipped with the repo
  live: [],              // sessions running on this server
  history: [],           // finished sessions of this workspace
};

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function notify() {
  for (const fn of listeners) fn();
}

function remember(id) {
  try {
    if (id) sessionStorage.setItem(STORAGE_KEY, id); else sessionStorage.removeItem(STORAGE_KEY);
  } catch { /* storage unavailable */ }
}

function remembered() {
  try { return sessionStorage.getItem(STORAGE_KEY); } catch { return null; }
}

// -- live ----------------------------------------------------------------------

let stream = null;

function detach() {
  if (stream) stream.stop();
  stream = null;
  if (session.player) session.player.destroy();
  session.player = null;
  session.traceId = null;
  session.traceMeta = {};
  session.caption = null;
}

export function openLive(id) {
  detach();
  session.mode = "live";
  session.id = id;
  session.state = initialState();
  remember(id);
  stream = new LiveStream(
    `api/sessions/${id}/events`,
    (event) => { apply(session.state, event); notify(); },
    (status) => { session.connection = status; notify(); },
  );
  stream.start();
  notify();
}

export async function refreshSessions() {
  if (isStatic) return;
  try {
    const listing = await getJSON("sessions");
    session.live = listing.live;
    session.history = listing.history;
    notify();
  } catch { /* server went away; the status pill already says so */ }
}

export async function newSession(resume = null) {
  const created = await post("sessions", resume ? { resume } : {});
  openLive(created.id);
  await refreshSessions();
  return created.id;
}

export async function closeSession(id) {
  await del(`sessions/${id}`);
  await refreshSessions();
  if (id === session.id) {
    if (session.live.length) openLive(session.live[0].id);
    else await newSession();
  }
}

// -- replay --------------------------------------------------------------------

const seenCaptions = new Set();

export async function loadReplay(traceId, { autoplay = true, guided = false } = {}) {
  const trace = normalizeTrace(await getJSON(`traces/${traceId}`));
  detach();
  seenCaptions.clear();
  session.mode = "replay";
  session.traceId = traceId;
  session.traceMeta = trace.meta;
  session.guided = guided;
  session.state = initialState();
  session.player = new ReplayPlayer(trace, (event, { skipping }) => {
    apply(session.state, event);
    if (!session.guided || skipping) return false;
    const caption = captionFor(event, seenCaptions);
    if (!caption) return false;
    session.caption = caption;   // the player pauses here until the viewer continues
    return true;
  }, notify);
  notify();
  if (autoplay) session.player.play();
}

export function continueTour() {
  session.caption = null;
  session.player?.play();
  notify();
}

export function setGuided(on) {
  session.guided = on;
  if (!on && session.caption) continueTour();
  notify();
}

export function restartReplay() {
  if (session.traceId) return loadReplay(session.traceId, { guided: session.guided });
}

export async function exitReplay() {
  if (!session.server.live) return;
  const id = session.id && session.live.some((s) => s.id === session.id) ? session.id : session.live[0]?.id;
  if (id) openLive(id); else await newSession();
}

// -- start ---------------------------------------------------------------------

export async function boot() {
  try { session.traces = await getJSON("traces"); } catch { session.traces = []; }
  if (!isStatic) {
    try { session.server = await getJSON("state"); } catch { session.server = { live: false, modes: [] }; }
    await refreshSessions();
  }

  // Deep link: ?trace=<id> opens that recording; &skip=1 jumps to its end,
  // &at=0.5 stops half way, &tour=1 turns the guided tour on.
  const params = new URLSearchParams(location.search);
  const wanted = params.get("trace");
  if (wanted) {
    try {
      await loadReplay(wanted, { autoplay: !params.has("skip") && !params.has("at"), guided: params.has("tour") });
      if (params.has("skip")) session.player.finish();
      else if (params.has("at")) session.player.seek(Number(params.get("at")));
      notify();
      return;
    } catch { /* unknown trace: fall through to the default */ }
  }

  if (session.server.live) {
    const stored = remembered();
    const existing = session.live.find((s) => s.id === stored) || session.live[0];
    if (existing) openLive(existing.id); else await newSession();
  }
  notify();
}

/** The console needs something to show: in replay-only mode, load the first recording. */
export async function ensureSomethingToShow() {
  if (session.mode !== "idle" || !session.traces.length) return;
  await loadReplay(session.traces[0].id, { guided: true });
}

// -- user actions (live mode) ----------------------------------------------------

const scoped = (path) => `sessions/${session.id}/${path}`;

export const actions = {
  send: (message) => post(scoped("chat"), { message }),
  stop: () => post(scoped("abort")),
  setMode: (mode) => post(scoped("mode"), { mode }),
  confirm: (request_id, allow) => post(scoped("confirm"), { request_id, allow }),
};
