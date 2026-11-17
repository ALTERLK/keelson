// Console view: the conversation, the composer / replay bar, and the inspector.

import { captions, help, pick } from "../content.js";
import { clear, fmtCount, fmtDuration, h } from "../dom.js";
import { highlight } from "../highlight.js";
import { t } from "../i18n.js";
import { renderMarkdown } from "../markdown.js";
import {
  actions, continueTour, ensureSomethingToShow, exitReplay, loadReplay, newSession, restartReplay,
  session, setGuided, subscribe,
} from "../session.js";
import { estimateCost, turnBreakdown } from "../store.js";
import { attempt } from "../toast.js";
import { renderToolBody, toolIcon } from "../toolview.js";

const STAGES = ["model", "gate", "tool", "result", "fold"];
const SPAN_KIND = { model_stream: "model", tool: "tool", confirm_wait: "wait" };

let inspectorTab = "overview"; // remembered across route changes

function helpButton(key) {
  return h("button", { type: "button", class: "help", "aria-label": "?", "data-tip": pick(help[key]) }, "?");
}

export function mount(root) {
  const feed = h("div", { class: "feed", role: "log", "aria-live": "polite" });
  const tour = h("div", { class: "tour-slot" });
  const dock = h("div", { class: "dock" });
  const inspector = h("aside", { class: "inspector" });
  const modalRoot = document.getElementById("modal-root");

  root.append(h("div", { class: "console" },
    h("section", { class: "conversation" }, feed, tour, dock),
    inspector,
  ));

  // Keyed rendering: each conversation item keeps its DOM node, and a node is
  // rebuilt only when that item's revision changed. Streaming one long answer
  // therefore re-renders one message per frame, not the whole conversation.
  const rendered = new Map(); // item.id -> {rev, el}
  let renderedState = null;
  let dockKey = "";
  let tourKey = "";
  let frame = 0;

  function schedule() {
    if (!frame) frame = requestAnimationFrame(render);
  }

  function render() {
    frame = 0;
    const { state } = session;
    const stick = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 120;

    if (state !== renderedState) { // new session or replay: start from an empty feed
      clear(feed);
      rendered.clear();
      renderedState = state;
      delete feed.dataset.emptyKey;
    }

    if (!state.items.length) {
      const key = `${session.mode}|${session.id}|${t("console.send")}`;
      if (feed.dataset.emptyKey !== key) { clear(feed).append(emptyState()); feed.dataset.emptyKey = key; }
    } else {
      delete feed.dataset.emptyKey;
      feed.querySelector(".empty")?.remove();
      for (const item of state.items) {
        const known = rendered.get(item.id);
        if (known && known.rev === item.rev) continue;
        const el = renderItem(item, known?.el);
        if (known) known.el.replaceWith(el); else feed.append(el);
        rendered.set(item.id, { rev: item.rev, el });
      }
    }

    let working = feed.querySelector(".working");
    if (state.busy && state.thinking) {
      if (!working) working = feed.appendChild(h("div", { class: "working" }, h("span", { class: "pulse" }), t("console.thinking")));
      else feed.append(working); // keep it last
    } else if (working) working.remove();

    renderTour();
    if (stick) feed.scrollTop = feed.scrollHeight;

    renderDock();
    renderInspector();
    renderConfirm();
  }

  // -- conversation items ------------------------------------------------------

  function renderItem(item, previous) {
    switch (item.kind) {
      case "user":
        return h("div", { class: "msg user" }, h("div", { class: "bubble" }, item.text));
      case "assistant": {
        const el = h("div", { class: "msg assistant" + (item.open ? " streaming" : "") },
          h("div", { class: "prose", html: renderMarkdown(item.text) }));
        for (const block of el.querySelectorAll("pre[data-lang] code")) {
          block.innerHTML = highlight(block.textContent, block.parentElement.dataset.lang);
        }
        return el;
      }
      case "tool":
        return renderTool(item, previous);
      case "notice":
        return h("div", { class: `notice ${item.level}` }, item.text);
      default:
        return h("div", { class: `marker ${item.tone || ""}`, "data-key": item.key || "" },
          h("span", { class: "marker-line" }), h("span", { class: "marker-text" }, item.text), h("span", { class: "marker-line" }));
    }
  }

  function gateChip(item) {
    const gate = item.gate;
    if (!gate) return null;
    if (gate.action === "deny") return h("span", { class: "chip bad", "data-tip": gate.message }, t("gate.deny"));
    if (gate.action === "confirm") return null; // the status chip already says "needs approval"
    if (gate.asked) return h("span", { class: "chip ok" }, t("gate.approved"));
    return h("span", { class: "chip" }, t(gate.speculative ? "gate.early" : "gate.allow"));
  }

  function renderTool(item, previous) {
    // Keep what the viewer chose; otherwise a running command opens by itself
    // so its output can be watched, and closes again when it is done.
    const userChoice = previous?.dataset.userOpen;
    const autoOpen = item.name === "run_shell" && !item.done && item.status === "running";
    const open = userChoice !== undefined ? userChoice === "1" : autoOpen;
    const el = h("details", { class: `tool ${item.status}`, open, "data-call": item.callId || "" },
      h("summary", {},
        h("span", { class: "tool-glyph" }, toolIcon(item.name)),
        h("code", { class: "tool-name" }, item.name),
        h("span", { class: "tool-summary" }, item.summary),
        h("span", { class: "tool-meta" },
          gateChip(item),
          item.status === "denied" ? null : h("span", { class: `chip status-${item.status}` },
            t(`tool.status.${item.status}`), item.duration !== null ? ` · ${fmtDuration(item.duration)}` : ""),
        ),
      ),
      renderToolBody(item),
    );
    if (userChoice !== undefined) el.dataset.userOpen = userChoice;
    el.querySelector("summary").addEventListener("click", () => { el.dataset.userOpen = el.open ? "0" : "1"; });
    const output = el.querySelector(".terminal-output");
    if (output && !item.done) requestAnimationFrame(() => { output.scrollTop = output.scrollHeight; });
    return el;
  }

  function emptyState() {
    if (session.mode !== "live") {
      return h("div", { class: "empty" }, h("p", {}, t(session.traces.length ? "common.loading" : "console.noTrace")));
    }
    return h("div", { class: "empty" },
      h("h2", {}, t("console.empty.title")),
      h("p", {}, t("console.empty.body")),
      h("div", { class: "suggestions" },
        ["console.suggest.1", "console.suggest.2", "console.suggest.3"].map((key) =>
          h("button", { type: "button", class: "suggestion", onclick: () => send(t(key)) }, t(key))),
      ),
    );
  }

  // -- guided tour ----------------------------------------------------------------

  function renderTour() {
    const caption = session.caption;
    const key = caption ? `${caption.key}|${caption.callId}|${t("console.tour.next")}` : "";
    if (key === tourKey) return;
    tourKey = key;
    for (const el of root.querySelectorAll(".spotlight")) el.classList.remove("spotlight");
    clear(tour);
    if (!caption) return;

    // Point at the part of the screen the caption is about.
    let target = null;
    if (caption.anchor === "call" && caption.callId) target = feed.querySelector(`[data-call="${CSS.escape(caption.callId)}"]`);
    else if (caption.anchor === "marker") target = [...feed.querySelectorAll(".marker")].pop();
    else if (caption.anchor === "inspector") target = inspector;
    else if (caption.anchor === "conversation") target = [...feed.querySelectorAll(".msg.user")].pop();
    target?.classList.add("spotlight");
    if (target && target !== inspector) target.scrollIntoView({ block: "nearest" });

    const next = h("button", { type: "button", class: "btn primary", onclick: continueTour }, t("console.tour.next"));
    tour.append(h("div", { class: "tour", role: "status" },
      h("p", {}, pick(captions[caption.key])),
      h("div", { class: "tour-actions" },
        h("button", { type: "button", class: "btn quiet", onclick: () => setGuided(false) }, t("console.tour.off")),
        next)));
    next.focus({ preventScroll: true });
  }

  // -- dock: composer (live) or transport controls (replay) ---------------------

  async function send(text) {
    const message = text.trim();
    if (!message || session.state.busy) return;
    await attempt(() => actions.send(message));
  }

  function renderDock() {
    const { state, mode, player, server } = session;
    const info = state.info || {};
    // The dock holds form controls, so rebuild it only when its inputs change —
    // otherwise typing would be interrupted on every streamed token.
    const key = [mode, session.id, state.busy, info.permission_mode, info.model, session.traceId, player?.playing, player?.speed,
      player?.finished, session.guided, !!session.caption, t("console.send")].join("|");
    if (key === dockKey) {
      if (player) dock.querySelector(".progress-fill")?.style.setProperty("width", `${player.progress * 100}%`);
      return;
    }
    dockKey = key;
    clear(dock);

    if (mode === "replay" && player) {
      const shipped = session.traces.some((trace) => trace.id === session.traceId);
      const past = session.history.find((row) => row.id === session.traceId);
      dock.append(h("div", { class: "replay-bar" },
        h("div", { class: "replay-head" },
          h("span", { class: "chip brand" }, t(past ? "sessions.viewing" : "console.replayOf")),
          shipped
            ? h("select", { class: "select", "aria-label": t("nav.traces"), onchange: (e) => loadReplay(e.target.value, { guided: session.guided }) },
                session.traces.map((trace) => h("option", { value: trace.id, selected: trace.id === session.traceId }, trace.title)))
            : h("span", { class: "replay-title" }, session.traceMeta.title || past?.title || session.traceId),
          session.traceMeta.model ? h("span", { class: "muted small" }, session.traceMeta.model) : null,
          past?.resumable ? h("button", { type: "button", class: "btn primary", onclick: () => attempt(() => newSession(past.id)) }, t("sessions.resume")) : null,
          server.live ? h("button", { type: "button", class: "btn", onclick: () => attempt(exitReplay) }, t("console.exitReplay")) : null,
        ),
        h("div", { class: "replay-controls" },
          h("button", {
            type: "button", class: "btn primary", disabled: !!session.caption,
            onclick: () => (player.finished ? restartReplay() : player.playing ? player.pause() : player.play()),
          }, player.finished ? t("replay.restart") : player.playing ? t("replay.pause") : t("replay.play")),
          h("button", { type: "button", class: "btn", onclick: () => { setGuided(false); player.finish(); }, disabled: player.finished }, t("replay.skip")),
          h("label", { class: "toggle" },
            h("input", { type: "checkbox", checked: session.guided, onchange: (e) => setGuided(e.target.checked) }), t("console.tour")),
          h("label", { class: "speed" }, t("replay.speed"),
            h("select", { class: "select", onchange: (e) => player.setSpeed(Number(e.target.value)) },
              [0.5, 1, 2, 4, 8].map((speed) => h("option", { value: speed, selected: speed === player.speed }, `${speed}×`)))),
          h("div", { class: "progress", role: "progressbar", "aria-valuemin": 0, "aria-valuemax": 100 },
            h("div", { class: "progress-fill", style: `width:${player.progress * 100}%` })),
        ),
      ));
      return;
    }

    if (mode !== "live") {
      dock.append(h("div", { class: "replay-bar" }, h("span", { class: "muted" }, t("console.replayMode"))));
      return;
    }

    const input = h("textarea", {
      class: "composer-input", rows: 1, placeholder: t("console.placeholder"), disabled: state.busy,
      onkeydown: (event) => {
        if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
          event.preventDefault();
          const text = input.value;
          input.value = "";
          send(text);
        }
      },
      oninput: () => { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 180) + "px"; },
    });

    dock.append(h("div", { class: "composer" },
      input,
      h("div", { class: "composer-row" },
        h("label", { class: "mode" }, t("console.mode"),
          h("select", { class: "select", disabled: state.busy, onchange: (e) => attempt(() => actions.setMode(e.target.value)) },
            (server.modes || []).map((name) => h("option", { value: name, selected: name === info.permission_mode }, name)))),
        info.model ? h("span", { class: "muted small" }, info.model) : null,
        h("span", { class: "spacer" }),
        state.busy
          ? h("button", { type: "button", class: "btn danger", onclick: () => attempt(actions.stop) }, t("console.stop"))
          : h("button", { type: "button", class: "btn primary", onclick: () => { const text = input.value; input.value = ""; send(text); } }, t("console.send")),
      ),
    ));
    if (!state.busy) input.focus({ preventScroll: true });
  }

  // -- inspector ----------------------------------------------------------------

  function stat(label, value, tone = "") {
    return h("div", { class: "stat" }, h("div", { class: "stat-label" }, label), h("div", { class: `stat-value ${tone}` }, value));
  }

  function panelTitle(label, helpKey) {
    return h("h3", {}, label, helpKey ? helpButton(helpKey) : null);
  }

  function overviewPanels(state) {
    const { usage, counts } = state;
    const windowSize = usage.context_window || 0;
    const fraction = windowSize ? Math.min(1, usage.context_tokens / windowSize) : 0;
    return [
      h("section", { class: "panel" },
        panelTitle(t("inspect.loop"), "loop"),
        h("ol", { class: "loop" }, STAGES.map((stage) =>
          h("li", { class: "loop-stage" + (state.stage === stage ? " active" : "") },
            h("span", { class: "loop-node" }), h("span", { class: "loop-label" }, t(`inspect.stage.${stage}`))))),
      ),
      h("section", { class: "panel" },
        panelTitle(t("inspect.run"), "session"),
        h("div", { class: "stats" },
          stat(t("inspect.turns"), counts.turns),
          stat(t("inspect.toolCalls"), counts.toolCalls),
          stat(t("inspect.tokens"), `${fmtCount(usage.input_tokens)} / ${fmtCount(usage.output_tokens)}`),
          stat(t("inspect.cost"), "$" + estimateCost(usage).toFixed(4)),
        ),
        h("div", { class: "meter-row" },
          h("span", {}, t("inspect.context")),
          h("span", { class: "muted" }, windowSize ? `${fmtCount(usage.context_tokens)} / ${fmtCount(windowSize)}` : "—")),
        h("div", { class: "meter" + (fraction > 0.8 ? " hot" : "") }, h("div", { class: "meter-fill", style: `width:${(fraction * 100).toFixed(1)}%` })),
      ),
      h("section", { class: "panel" },
        panelTitle(t("inspect.gate"), "gate"),
        h("div", { class: "stats three" },
          stat(t("inspect.allowed"), counts.allowed, counts.allowed ? "ok" : ""),
          stat(t("inspect.asked"), counts.asked, counts.asked ? "warn" : ""),
          stat(t("inspect.denied"), counts.denied, counts.denied ? "bad" : ""),
        ),
        h("h3", { class: "sub" }, t("inspect.guards"), helpButton("guards")),
        h("div", { class: "stats three" },
          stat(t("inspect.loopGuard"), counts.loopGuard, counts.loopGuard ? "warn" : ""),
          stat(t("inspect.folds"), counts.folds),
          stat(t("inspect.errors"), counts.errors, counts.errors ? "bad" : ""),
        ),
      ),
    ];
  }

  // One block per turn: a bar split by where the time went, then each step on a
  // shared time axis (a waterfall), so it is obvious whether a slow turn was
  // the model thinking or a tool running.
  function perfPanels(state) {
    const turns = [...new Set(state.spans.map((span) => span.turn))].sort((a, b) => a - b);
    if (!turns.length) return [h("section", { class: "panel" }, panelTitle(t("inspect.tab.perf"), "perf"), h("p", { class: "muted" }, t("perf.empty")))];

    const gateChecks = state.spans.filter((span) => span.name === "permission").map((span) => span.dur * 1000).sort((a, b) => a - b);
    const median = gateChecks.length ? gateChecks[Math.floor(gateChecks.length / 2)] : null;

    return [
      h("section", { class: "panel" },
        panelTitle(t("inspect.tab.perf"), "perf"),
        h("div", { class: "legend small" },
          ["model", "tool", "wait"].map((kind) => h("span", { class: "legend-item" }, h("span", { class: `swatch kind-${kind}` }), t(`perf.${kind}`)))),
        median !== null ? h("div", { class: "meter-row" }, h("span", {}, t("perf.gate")),
          h("span", { class: "muted" }, `${median < 1 ? median.toFixed(2) : median.toFixed(1)} ms · ${t("perf.gate.note")}`)) : null,
      ),
      ...turns.slice(-6).reverse().map((turn) => {
        const spans = state.spans.filter((span) => span.turn === turn && SPAN_KIND[span.name]);
        if (!spans.length) return null;
        const totals = turnBreakdown(state, turn);
        const begin = Math.min(...spans.map((span) => span.start), state.turnStarts[turn] ?? Infinity);
        const end = Math.max(...spans.map((span) => span.end));
        const length = Math.max(0.001, end - begin);
        return h("section", { class: "panel" },
          h("div", { class: "perf-head" },
            h("strong", {}, `${t("perf.turn")} ${turn + 1}`),
            h("span", { class: "muted" }, fmtDuration(length))),
          h("div", { class: "perf-split" }, ["model", "tool", "wait"].filter((kind) => totals[kind] > 0).map((kind) =>
            h("div", { class: `perf-part kind-${kind}`, style: `flex-grow:${totals[kind]}`, "data-tip": `${t(`perf.${kind}`)} · ${fmtDuration(totals[kind])}` }))),
          h("div", { class: "waterfall" }, spans.map((span) =>
            h("div", { class: "waterfall-row", "data-tip": `${span.tool || t(`span.${span.name}`)} · ${fmtDuration(span.dur)}` },
              h("span", { class: "waterfall-label" }, span.tool || t(`perf.${SPAN_KIND[span.name]}`)),
              h("span", { class: "waterfall-track" },
                h("span", { class: `waterfall-bar kind-${SPAN_KIND[span.name]}`,
                  style: `left:${((span.start - begin) / length) * 100}%;width:${Math.max(0.8, (span.dur / length) * 100)}%` })),
              h("span", { class: "waterfall-time" }, fmtDuration(span.dur))))));
      }),
    ];
  }

  function timelinePanel(state) {
    const start = state.timeline[0]?.time ?? 0;
    return [h("section", { class: "panel grow" },
      panelTitle(t("inspect.timeline")),
      state.timeline.length
        ? h("ol", { class: "timeline" }, state.timeline.slice(-120).map((entry) =>
            h("li", { class: entry.tone }, h("time", {}, "+" + fmtDuration(Math.max(0, entry.time - start))), h("span", {}, entry.label))))
        : h("p", { class: "muted" }, t("inspect.timeline.empty")))];
  }

  function renderInspector() {
    const { state } = session;
    const tabs = ["overview", "perf", "timeline"];
    const body = inspectorTab === "perf" ? perfPanels(state) : inspectorTab === "timeline" ? timelinePanel(state) : overviewPanels(state);
    const scroll = inspector.querySelector(".inspector-body")?.scrollTop ?? 0;
    clear(inspector).append(
      h("div", { class: "tabs", role: "tablist" }, tabs.map((tab) =>
        h("button", {
          type: "button", role: "tab", class: "tab" + (tab === inspectorTab ? " active" : ""),
          "aria-selected": tab === inspectorTab ? "true" : "false",
          onclick: () => { inspectorTab = tab; schedule(); },
        }, t(`inspect.tab.${tab}`)))),
      h("div", { class: "inspector-body" }, body),
    );
    const bodyEl = inspector.querySelector(".inspector-body");
    bodyEl.scrollTop = inspectorTab === "timeline" ? bodyEl.scrollHeight : scroll;
    if (session.caption?.anchor === "inspector") inspector.classList.add("spotlight");
  }

  // -- permission confirmation --------------------------------------------------

  let shownConfirm = null;

  function renderConfirm() {
    const pending = session.state.confirm;
    const id = pending?.request_id ?? null;
    if (id === shownConfirm) return;
    shownConfirm = id;
    clear(modalRoot);
    if (!pending) return;

    const interactive = session.mode === "live";
    const options = pending.options || ["once", "deny"];
    const reasonInput = h("input", { type: "text", class: "confirm-reason", placeholder: t("confirm.deny_reason"), disabled: !interactive });
    const answer = (allow, scope) => attempt(() => actions.confirm(pending.request_id, allow, scope, allow ? "" : reasonInput.value));
    const button = (label, onclick, cls = "btn") => h("button", { type: "button", class: cls, disabled: !interactive, onclick }, label);
    const allowButton = button(t("confirm.allow"), () => answer(true, "once"), "btn primary");
    modalRoot.append(h("div", { class: "modal-backdrop" + (interactive ? "" : " passive") },
      h("div", { class: "modal", role: "dialog", "aria-modal": "true", "aria-labelledby": "confirm-title" },
        h("h2", { id: "confirm-title" }, t("confirm.title")),
        h("p", {}, t("confirm.body")),
        h("pre", { class: "confirm-action" }, pending.message),
        pending.reason ? h("p", { class: "confirm-why" }, t("confirm.why") + pending.reason) : null,
        reasonInput,
        h("div", { class: "modal-actions" },
          button(t("confirm.deny"), () => answer(false)),
          options.includes("project") ? button(t("confirm.project"), () => answer(true, "project")) : null,
          options.includes("session") ? button(t("confirm.session"), () => answer(true, "session")) : null,
          allowButton,
        ),
      ),
    ));
    if (interactive) allowButton.focus();
  }

  const unsubscribe = subscribe(schedule);
  ensureSomethingToShow();
  render();

  return () => {
    unsubscribe();
    cancelAnimationFrame(frame);
    clear(modalRoot);
  };
}
