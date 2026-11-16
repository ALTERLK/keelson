// Console state = a fold over the event stream.
//
// `apply(state, event)` is the only place that changes state. A live SSE
// stream and a recorded trace both arrive as the same events and go through
// this one function, which is why a replay is indistinguishable from the run
// it was recorded from. Event names mirror agents/events.py.

const TIMELINE_LIMIT = 300;
const LIVE_OUTPUT_LIMIT = 60_000;

export function initialState() {
  return {
    items: [],          // conversation, in order: user | assistant | tool | notice | marker
    calls: new Map(),   // tool-call id -> its item
    usage: { input_tokens: 0, output_tokens: 0, context_tokens: 0, context_window: 0, turns: 0 },
    counts: { toolCalls: 0, allowed: 0, asked: 0, denied: 0, loopGuard: 0, folds: 0, turns: 0, errors: 0 },
    stage: "idle",      // idle | model | gate | tool | result | fold
    busy: false,
    thinking: false,
    confirm: null,      // {request_id, message} while the gate waits on the user
    timeline: [],
    spans: [],          // {name, start, end, dur, turn, tool} — timing of each step of the loop
    turn: 0,            // index of the turn in progress (or the next one)
    turnStarts: [],     // time each turn began, by index
    info: null,         // the session's own description (model, permission mode, ...)
    nextId: 1,
  };
}

function push(state, item) {
  item.id = "i" + state.nextId++;
  item.rev = 0;
  state.items.push(item);
  return item;
}

function touch(item) { item.rev++; }

function lastOpenAssistant(state) {
  const last = state.items[state.items.length - 1];
  return last && last.kind === "assistant" && last.open ? last : null;
}

function closeAssistant(state) {
  const last = lastOpenAssistant(state);
  if (last) { last.open = false; touch(last); }
}

function findCall(state, data) {
  if (data.id && state.calls.has(data.id)) return state.calls.get(data.id);
  // Events without an id (older traces): match the oldest unfinished call of that name.
  return state.items.find((it) => it.kind === "tool" && it.name === data.name && !it.done) || null;
}

function note(state, event, label, tone = "") {
  state.timeline.push({ time: event.time, type: event.type, label, tone });
  if (state.timeline.length > TIMELINE_LIMIT) state.timeline.shift();
}

const handlers = {
  session(state, { data }) {
    state.info = { ...(state.info || {}), ...data };
  },

  user_message(state, event) {
    closeAssistant(state);
    push(state, { kind: "user", text: event.data.text || "" });
    state.busy = true;
    state.stage = "model";
    state.turnStarts[state.turn] = event.time;
    note(state, event, "user message");
  },

  thinking(state, { data }) {
    state.thinking = !!data.active;
    if (data.active) state.stage = "model";
  },

  assistant_delta(state, { data }) {
    let item = lastOpenAssistant(state);
    if (!item) item = push(state, { kind: "assistant", text: "", open: true });
    item.text += data.text || "";
    touch(item);
    state.stage = "model";
    state.thinking = false;
  },

  tool_call(state, event) {
    const { data } = event;
    closeAssistant(state);
    const item = push(state, {
      kind: "tool", callId: data.id, name: data.name, input: data.input, summary: data.summary || "",
      status: "pending", gate: null, startedAt: event.time, duration: null,
      live: "", result: "", chars: 0, done: false,
    });
    if (data.id) state.calls.set(data.id, item);
    state.counts.toolCalls++;
    state.stage = "gate";
    note(state, event, `${data.name} ${data.summary || ""}`.trim());
  },

  permission(state, event) {
    const { data } = event;
    const item = findCall(state, data);
    const wasAsked = item?.gate?.action === "confirm";
    if (item) {
      item.gate = { action: data.action, message: data.message || "", speculative: !!data.speculative, asked: wasAsked };
      if (data.action === "deny") { item.status = "denied"; item.done = true; }
      else if (data.action === "confirm") item.status = "awaiting";
      else item.status = "running";
      touch(item);
    }
    if (data.action === "deny") { state.counts.denied++; state.stage = "result"; note(state, event, `gate denied ${data.name}`, "bad"); }
    else if (data.action === "confirm") { state.counts.asked++; note(state, event, `gate asks about ${data.name}`, "warn"); }
    else { if (!wasAsked) state.counts.allowed++; state.stage = "tool"; }
  },

  confirm_request(state, { data }) { state.confirm = { request_id: data.request_id, message: data.message }; },
  confirm_resolved(state) { state.confirm = null; },

  // Live output of a command that is still running.
  tool_output(state, { data }) {
    const item = findCall(state, { id: data.id, name: "run_shell" });
    if (!item || item.done) return;
    item.live = (item.live + (data.text || "")).slice(-LIVE_OUTPUT_LIMIT);
    touch(item);
  },

  tool_result(state, event) {
    const { data } = event;
    const item = findCall(state, data);
    if (item) {
      item.status = data.ok ? "ok" : "error";
      item.result = data.preview || "";
      item.chars = data.chars || 0;
      item.duration = Math.max(0, event.time - item.startedAt);
      item.done = true;
      touch(item);
    }
    if (!data.ok) state.counts.errors++;
    state.stage = "result";
  },

  span(state, event) {
    const { data } = event;
    const dur = (data.dur_ms || 0) / 1000;
    state.spans.push({ name: data.name, tool: data.tool || "", end: event.time, start: event.time - dur, dur, turn: state.turn });
  },

  loop_guard(state, event) {
    state.counts.loopGuard = event.data.hits || state.counts.loopGuard + 1;
    push(state, { kind: "marker", tone: "warn", key: "loop_guard", text: `loop guard stopped repeated ${event.data.name} calls` });
    note(state, event, `loop guard: ${event.data.name}`, "warn");
  },

  context_fold(state, event) {
    state.counts.folds++;
    state.stage = "fold";
    push(state, { kind: "marker", tone: "info", key: "context_fold", text: `context folded (${event.data.trigger})` });
    note(state, event, "context folded", "info");
  },

  usage(state, { data }) {
    for (const key of Object.keys(state.usage)) {
      if (data[key] !== undefined && data[key] !== null) state.usage[key] = data[key];
    }
  },

  skill_evolution(state, event) {
    const { data } = event;
    const rejected = String(data.action || "").includes("quarantin");
    const verdict = data.gate?.action ? ` · gate: ${data.gate.action}` : "";
    push(state, {
      kind: "marker", tone: rejected ? "bad" : "ok", key: "skill_evolution",
      text: `skill ${rejected ? "quarantined" : data.action}: ${data.skill}${data.version ? " @ " + data.version : ""}${verdict}`,
    });
    note(state, event, `skill ${data.action}: ${data.skill}`, rejected ? "bad" : "ok");
  },

  subagent(state, event) {
    const { data } = event;
    if (data.phase === "start") {
      push(state, { kind: "marker", tone: "", key: "subagent", text: `sub-agent [${data.agent_type}] ${data.description || ""}` });
    }
    note(state, event, `sub-agent ${data.phase}: ${data.agent_type}`);
  },

  retry(state, event) {
    const { data } = event;
    push(state, { kind: "marker", tone: "warn", key: "retry", text: `retry ${data.attempt}/${data.max_retries}: ${data.reason}` });
    note(state, event, "model call retried", "warn");
  },

  notice(state, event) {
    const { data } = event;
    closeAssistant(state);
    push(state, { kind: "notice", level: data.level || "info", text: data.message || "" });
    if (data.level === "error") note(state, event, "error", "bad");
  },

  turn_end(state, event) {
    closeAssistant(state);
    state.busy = false;
    state.thinking = false;
    state.stage = "idle";
    state.confirm = null;
    state.counts.turns++;
    state.turn++;
    for (const item of state.items) {
      if (item.kind === "tool" && !item.done) { item.done = true; item.status = "stopped"; touch(item); }
    }
    note(state, event, "turn complete");
  },
};

export function apply(state, event) {
  const handler = handlers[event.type];
  if (handler) handler(state, event);
  return state;
}

// Same rough list price the terminal uses for its on-screen estimate.
export function estimateCost(usage) {
  return (usage.input_tokens / 1e6) * 3 + (usage.output_tokens / 1e6) * 15;
}

/** Where one turn's time went, by kind of step. Waiting on a person is kept apart. */
export function turnBreakdown(state, turn) {
  const totals = { model: 0, tool: 0, wait: 0, other: 0 };
  for (const span of state.spans) {
    if (span.turn !== turn) continue;
    if (span.name === "model_stream") totals.model += span.dur;
    else if (span.name === "tool") totals.tool += span.dur;
    else if (span.name === "confirm_wait") totals.wait += span.dur;
    else if (span.name !== "permission") totals.other += span.dur;
  }
  return totals;
}
