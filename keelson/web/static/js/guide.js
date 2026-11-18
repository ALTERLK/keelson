// Guided replay: decide when a replay should stop and explain what just happened.
//
// The captions are not written per trace. Each rule looks at one event (and the
// state before it) and, the first time it matches in a replay, yields a caption
// key. So the tour works on any recording, including the viewer's own sessions.
// The caption text itself lives in content.js, in both languages.

const READ_ONLY = new Set(["read_file", "list_files", "grep_search"]);

/** Rules are tried in order; the first match wins. `seen` holds keys already shown. */
const rules = [
  { key: "task", when: (e) => e.type === "user_message" },
  { key: "gate_ask", when: (e) => e.type === "permission" && e.data.action === "confirm" },
  { key: "gate_user_deny", when: (e) => e.type === "permission" && e.data.action === "deny" && /by user/.test(e.data.message || "") },
  { key: "gate_user_allow", when: (e) => e.type === "permission" && e.data.action === "allow" && /by user/.test(e.data.message || "") },
  { key: "gate_auto_deny", when: (e) => e.type === "permission" && e.data.action === "deny" },
  { key: "gate_read", when: (e) => e.type === "permission" && e.data.action === "allow" && READ_ONLY.has(e.data.name) },
  { key: "tool_edit", when: (e) => e.type === "tool_result" && e.data.ok && (e.data.name === "edit_file" || e.data.name === "write_file") },
  { key: "tool_shell_fail", when: (e) => e.type === "tool_result" && !e.data.ok && e.data.name === "run_shell" },
  { key: "tool_shell_ok", when: (e) => e.type === "tool_result" && e.data.ok && e.data.name === "run_shell" },
  { key: "tool_delete", when: (e) => e.type === "tool_result" && e.data.ok && e.data.name === "delete_files" },
  { key: "verify", when: (e) => e.type === "verify" && e.data.phase === "done" && e.data.status !== "unverified" },
  { key: "undo", when: (e) => e.type === "undo" && e.data.ok },
  { key: "skill_used", when: (e) => e.type === "tool_call" && e.data.name === "skill" },
  { key: "skill_evolved", when: (e) => e.type === "skill_evolution" },
  { key: "context_fold", when: (e) => e.type === "context_fold" },
  { key: "loop_guard", when: (e) => e.type === "loop_guard" },
  { key: "turn_end", when: (e) => e.type === "turn_end" },
];

// Which part of the screen a caption is about, so the view can point at it.
const anchors = {
  task: "conversation",
  gate_ask: "confirm",
  gate_user_deny: "call",
  gate_user_allow: "call",
  gate_auto_deny: "call",
  gate_read: "call",
  tool_edit: "call",
  tool_shell_fail: "call",
  tool_shell_ok: "call",
  tool_delete: "call",
  verify: "last",
  undo: "last",
  skill_used: "call",
  skill_evolved: "marker",
  context_fold: "marker",
  loop_guard: "marker",
  turn_end: "inspector",
};

export function captionFor(event, seen) {
  for (const rule of rules) {
    if (seen.has(rule.key) || !rule.when(event)) continue;
    seen.add(rule.key);
    return { key: rule.key, anchor: anchors[rule.key], callId: event.data?.id || null };
  }
  return null;
}
