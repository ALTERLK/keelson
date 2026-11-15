// Console view: the conversation, the composer / replay bar, and the inspector.

import { clear, fmtCount, fmtDuration, h } from "../dom.js";
import { t } from "../i18n.js";
import { renderMarkdown } from "../markdown.js";
import { actions, exitReplay, loadReplay, restartReplay, session, subscribe } from "../session.js";
import { estimateCost } from "../store.js";

const STAGES = ["model", "gate", "tool", "result", "fold"];

export function mount(root) {
  const feed = h("div", { class: "feed", role: "log", "aria-live": "polite" });
  const dock = h("div", { class: "dock" });
  const inspector = h("aside", { class: "inspector" });
  const modalRoot = document.getElementById("modal-root");

  root.append(h("div", { class: "console" },
    h("section", { class: "conversation" }, feed, dock),
    inspector,
  ));

  // Keyed rendering: each conversation item keeps its DOM node, and a node is
  // rebuilt only when that item's revision changed. Streaming one long answer
  // therefore re-renders one message per frame, not the whole conversation.
  const rendered = new Map(); // item.id -> {rev, el}
  let renderedState = null;
  let dockKey = "";
  let frame = 0;

  function schedule() {
    if (!frame) frame = requestAnimationFrame(render);
  }

  function render() {
    frame = 0;
    const { state } = session;
    const stick = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 80;

    if (state !== renderedState) { // new session or replay: start from an empty feed
      clear(feed);
      rendered.clear();
      renderedState = state;
    }

    if (!state.items.length) {
      if (!feed.querySelector(".empty")) { clear(feed); feed.append(emptyState()); }
    } else {
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
      case "assistant":
        return h("div", { class: "msg assistant" + (item.open ? " streaming" : "") },
          h("div", { class: "prose", html: renderMarkdown(item.text) }));
      case "tool":
        return renderTool(item, previous);
      case "notice":
        return h("div", { class: `notice ${item.level}` }, item.text);
      default:
        return h("div", { class: `marker ${item.tone || ""}` }, h("span", { class: "marker-line" }), item.text, h("span", { class: "marker-line" }));
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
    const open = previous?.open ?? false; // keep the user's expand/collapse across updates
    const body = h("div", { class: "tool-body" },
      h("div", { class: "tool-label" }, t("tool.input")),
      h("pre", {}, JSON.stringify(item.input ?? {}, null, 2)),
      item.gate?.action === "deny" ? h("div", { class: "tool-deny" }, item.gate.message) : null,
      item.result ? [
        h("div", { class: "tool-label" }, t("tool.output"), item.chars > item.result.length ? ` · ${fmtCount(item.chars)} chars` : ""),
        h("pre", {}, item.result),
      ] : null,
    );
    return h("details", { class: `tool ${item.status}`, open },
      h("summary", {},
        h("span", { class: "tool-dot" }),
        h("code", { class: "tool-name" }, item.name),
        h("span", { class: "tool-summary" }, item.summary),
        h("span", { class: "tool-meta" },
          gateChip(item),
          item.status === "denied" ? null : h("span", { class: `chip status-${item.status}` },
            t(`tool.status.${item.status}`), item.duration !== null ? ` · ${fmtDuration(item.duration)}` : ""),
        ),
      ),
      body,
    );
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

  // -- dock: composer (live) or transport controls (replay) ---------------------

  async function send(text) {
    const message = text.trim();
    if (!message || session.state.busy) return;
    try {
      await actions.send(message);
    } catch (error) {
      alert(error.message);
    }
  }

  function renderDock() {
    const { state, mode, player, server } = session;
    // The dock holds form controls, so rebuild it only when its inputs change —
    // otherwise typing would be interrupted on every streamed token.
    const key = [mode, state.busy, server.permission_mode, session.traceId, player?.playing, player?.speed, player?.finished, t("console.send")].join("|");
    if (key === dockKey) {
      if (player) dock.querySelector(".progress-fill")?.style.setProperty("width", `${player.progress * 100}%`);
      return;
    }
    dockKey = key;
    clear(dock);

    if (mode === "replay" && player) {
      const current = session.traces.find((trace) => trace.id === session.traceId);
      dock.append(h("div", { class: "replay-bar" },
        h("div", { class: "replay-head" },
          h("span", { class: "chip brand" }, t("console.replayOf")),
          h("select", { class: "select", "aria-label": t("nav.traces"), onchange: (e) => loadReplay(e.target.value) },
            session.traces.map((trace) => h("option", { value: trace.id, selected: trace.id === session.traceId }, trace.title))),
          current?.model ? h("span", { class: "muted" }, current.model) : null,
          server.live ? h("button", { type: "button", class: "btn", onclick: exitReplay }, t("console.exitReplay")) : null,
        ),
        h("div", { class: "replay-controls" },
          h("button", { type: "button", class: "btn primary", onclick: () => (player.finished ? restartReplay() : player.playing ? player.pause() : player.play()) },
            player.finished ? t("replay.restart") : player.playing ? t("replay.pause") : t("replay.play")),
          h("button", { type: "button", class: "btn", onclick: () => player.finish(), disabled: player.finished }, t("replay.skip")),
          h("label", { class: "speed" }, t("replay.speed"),
            h("select", { class: "select", onchange: (e) => player.setSpeed(Number(e.target.value)) },
              [0.5, 1, 2, 4, 8].map((speed) => h("option", { value: speed, selected: speed === player.speed }, `${speed}×`)))),
          h("div", { class: "progress", role: "progressbar", "aria-valuemin": 0, "aria-valuemax": 100 },
            h("div", { class: "progress-fill", style: `width:${player.progress * 100}%` })),
        ),
      ));
      return;
    }

    if (mode !== "live") return;

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
          h("select", { class: "select", disabled: state.busy, onchange: (e) => actions.setMode(e.target.value).catch((err) => alert(err.message)) },
            (server.modes || []).map((name) => h("option", { value: name, selected: name === server.permission_mode }, name)))),
        h("button", { type: "button", class: "btn", disabled: state.busy, onclick: () => actions.reset().catch((err) => alert(err.message)) }, t("console.new")),
        h("span", { class: "spacer" }),
        state.busy
          ? h("button", { type: "button", class: "btn danger", onclick: () => actions.stop() }, t("console.stop"))
          : h("button", { type: "button", class: "btn primary", onclick: () => { const text = input.value; input.value = ""; send(text); } }, t("console.send")),
      ),
    ));
    if (!state.busy) input.focus({ preventScroll: true });
  }

  // -- inspector ----------------------------------------------------------------

  function stat(label, value, tone = "") {
    return h("div", { class: "stat" }, h("div", { class: "stat-label" }, label), h("div", { class: `stat-value ${tone}` }, value));
  }

  function renderInspector() {
    const { state } = session;
    const { usage, counts } = state;
    const windowSize = usage.context_window || 0;
    const fraction = windowSize ? Math.min(1, usage.context_tokens / windowSize) : 0;
    const start = state.timeline[0]?.time ?? 0;

    clear(inspector).append(
      h("section", { class: "panel" },
        h("h3", {}, t("inspect.loop")),
        h("ol", { class: "loop" }, STAGES.map((stage) =>
          h("li", { class: "loop-stage" + (state.stage === stage ? " active" : "") },
            h("span", { class: "loop-node" }), h("span", { class: "loop-label" }, t(`inspect.stage.${stage}`))))),
      ),
      h("section", { class: "panel" },
        h("h3", {}, t("inspect.run")),
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
        h("h3", {}, t("inspect.gate")),
        h("div", { class: "stats three" },
          stat(t("inspect.allowed"), counts.allowed, counts.allowed ? "ok" : ""),
          stat(t("inspect.asked"), counts.asked, counts.asked ? "warn" : ""),
          stat(t("inspect.denied"), counts.denied, counts.denied ? "bad" : ""),
        ),
        h("h3", { class: "sub" }, t("inspect.guards")),
        h("div", { class: "stats three" },
          stat(t("inspect.loopGuard"), counts.loopGuard, counts.loopGuard ? "warn" : ""),
          stat(t("inspect.folds"), counts.folds),
          stat(t("inspect.errors"), counts.errors, counts.errors ? "bad" : ""),
        ),
      ),
      h("section", { class: "panel grow" },
        h("h3", {}, t("inspect.timeline")),
        state.timeline.length
          ? h("ol", { class: "timeline" }, state.timeline.slice(-60).map((entry) =>
              h("li", { class: entry.tone }, h("time", {}, "+" + fmtDuration(Math.max(0, entry.time - start))), h("span", {}, entry.label))))
          : h("p", { class: "muted" }, t("inspect.timeline.empty")),
      ),
    );
    const list = inspector.querySelector(".timeline");
    if (list) list.scrollTop = list.scrollHeight;
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
    const answer = (allow) => actions.confirm(pending.request_id, allow).catch(() => {});
    const allowButton = h("button", { type: "button", class: "btn primary", disabled: !interactive, onclick: () => answer(true) }, t("confirm.allow"));
    modalRoot.append(h("div", { class: "modal-backdrop" },
      h("div", { class: "modal", role: "dialog", "aria-modal": "true", "aria-labelledby": "confirm-title" },
        h("h2", { id: "confirm-title" }, t("confirm.title")),
        h("p", {}, t("confirm.body")),
        h("pre", { class: "confirm-action" }, pending.message),
        h("div", { class: "modal-actions" },
          h("button", { type: "button", class: "btn", disabled: !interactive, onclick: () => answer(false) }, t("confirm.deny")),
          allowButton,
        ),
      ),
    ));
    if (interactive) allowButton.focus();
  }

  const unsubscribe = subscribe(schedule);
  render();

  return () => {
    unsubscribe();
    cancelAnimationFrame(frame);
    clear(modalRoot);
  };
}
