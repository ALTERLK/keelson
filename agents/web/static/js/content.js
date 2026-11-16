// Long-form explanatory text, in both languages: the overview page, the
// concept pop-overs and the guided-tour captions. Kept apart from i18n.js
// (short interface labels) because this is prose, edited as prose.

import { getLang } from "./i18n.js";

export function pick(entry) {
  if (entry === null || entry === undefined) return "";
  if (typeof entry === "string") return entry;
  return entry[getLang()] ?? entry.en ?? "";
}

// ─── Overview page ──────────────────────────────────────────

export const hero = {
  eyebrow: { en: "Agent harness", zh: "Agent 运行时" },
  title: {
    en: "The model proposes. Keelson decides what actually runs.",
    zh: "模型只管提议，真正执行由 Keelson 把关。",
  },
  lead: {
    en: "An AI model can only produce text. To get work done it asks for tools: read this file, run that command. Keelson is the layer in between. It checks every request, runs the ones that are allowed, records what happened, and learns your preferences along the way.",
    zh: "AI 模型本身只会输出文字。要真正干活，它得请求使用工具：读这个文件、跑那条命令。Keelson 就是夹在中间的那一层——每个请求都先检查，允许的才执行，全程留下记录，并在使用中记住你的偏好。",
  },
  primary: { en: "Watch a guided replay", zh: "看一次带讲解的回放" },
  secondaryLive: { en: "Open the console", zh: "打开控制台" },
  secondaryStatic: { en: "Run it yourself", zh: "在自己电脑上运行" },
};

export const problems = [
  {
    title: { en: "It asks before anything risky", zh: "危险操作先问你" },
    body: {
      en: "Deleting files, pushing code, running an unknown command: the permission gate stops and asks. Say no and the model is told so, and changes course.",
      zh: "删文件、推送代码、执行没见过的命令——权限门会停下来问你。你拒绝后，模型会收到“被拒绝”的结果并换一种做法。",
    },
  },
  {
    title: { en: "It does not spiral", zh: "不会原地打转" },
    body: {
      en: "Models sometimes call the same tool over and over. The loop guard notices when results stop changing and makes the model answer. A turn cap bounds the cost.",
      zh: "模型有时会反复调用同一个工具。循环保护发现结果不再变化时会叫停，让模型直接作答；轮次上限则封住了成本。",
    },
  },
  {
    title: { en: "It remembers how you like things", zh: "记得你喜欢怎么做" },
    body: {
      en: "Tell it once — “from now on, explain code as bullet points” — and that becomes a skill it applies next time. A skill only changes if the new version is no worse than the old one.",
      zh: "说一次就够——“以后解释代码都用要点列表”——它会沉淀成一条技能，下次自动套用。技能只有在新版本不比旧版本差时才会更新。",
    },
  },
];

// The five stages of one turn through the loop; shown on the interactive diagram.
export const stages = [
  {
    id: "model",
    name: { en: "Model", zh: "模型" },
    plain: {
      en: "The model reads the conversation and either answers or asks for a tool. It cannot touch anything itself.",
      zh: "模型读完对话后，要么直接回答，要么请求使用某个工具。它自己碰不到任何东西。",
    },
    detail: {
      en: "Keelson speaks both the OpenAI and the Anthropic protocol, retries transient errors with backoff, and stops at a turn or cost cap with one best-effort answer.",
      zh: "Keelson 同时支持 OpenAI 和 Anthropic 两种协议，对瞬时错误做退避重试，到达轮次或成本上限时给出一次尽力而为的回答后停止。",
    },
    code: "agents/agent.py · _chat_openai / _chat_anthropic",
  },
  {
    id: "gate",
    name: { en: "Permission gate", zh: "权限门" },
    plain: {
      en: "Before a tool runs, the gate decides: allow, ask you, or deny.",
      zh: "工具执行之前，权限门先做决定：放行、问你、还是拒绝。",
    },
    detail: {
      en: "Reading is allowed. Destructive shell commands are matched against a pattern list and need your OK. Five modes change the default, from read-only planning to fully automatic; rules in settings.json override them per tool.",
      zh: "读文件直接放行；危险的 shell 命令会匹配一份模式清单，需要你点头。五种模式可以调整默认行为，从只读规划到全自动；settings.json 里的规则还能按工具单独覆盖。",
    },
    code: "agents/tools.py · check_permission",
  },
  {
    id: "tool",
    name: { en: "Tool", zh: "工具执行" },
    plain: {
      en: "The runtime — not the model — reads the file or runs the command.",
      zh: "由运行时（而不是模型）去读文件、跑命令。",
    },
    detail: {
      en: "Shell commands run without blocking, stream their output as they go, and are killed with their whole process tree on timeout or when you press Stop. Read-only tools in one response run concurrently.",
      zh: "shell 命令非阻塞执行，输出边跑边显示；超时或你点“停止”时，连同整个进程树一起杀掉。同一次回复里的只读工具会并发执行。",
    },
    code: "agents/tools.py · run_shell_async, execute_tool",
  },
  {
    id: "result",
    name: { en: "Result", zh: "结果回写" },
    plain: {
      en: "What the tool returned — including errors and denials — goes back to the model, which decides the next step.",
      zh: "工具返回了什么——包括报错和被拒绝——都原样告诉模型，由它决定下一步。",
    },
    detail: {
      en: "An oversized result is written to disk and replaced by a reference. The loop guard watches this step: the same tool, again and again, with results that no longer change, gets stopped.",
      zh: "过大的结果会落盘并替换成引用。循环保护盯着这一步：同一个工具一再被调用而结果不再变化，就会被叫停。",
    },
    code: "agents/agent.py · _loop_guard_check, _persist_large_result",
  },
  {
    id: "fold",
    name: { en: "Fold", zh: "上下文折叠" },
    plain: {
      en: "When the conversation gets too long for the model, older history is condensed so the task can continue.",
      zh: "对话长到模型装不下时，较早的历史会被压缩，让任务可以继续。",
    },
    detail: {
      en: "Three tiers, cheapest first: cap each result, trim stale results at 60% of the window, then at 70% rewrite history into three structured notes — what happened, what is being done now, what the tools taught us.",
      zh: "分三层，先用便宜的：单条结果设预算；窗口用到 60% 时裁掉陈旧结果；到 70% 时把历史改写成三份结构化笔记——发生过什么、现在在做什么、工具教会了我们什么。",
    },
    code: "agents/session_memory.py · agents/agent.py · _run_compression_pipeline",
  },
];

export const steps = [
  {
    title: { en: "Watch it work", zh: "先看它怎么工作" },
    body: {
      en: "The guided replay plays a real recorded run and stops to explain each step. No setup, no API key.",
      zh: "带讲解的回放会播放一次真实录制的运行，并在每个关键步骤停下来解释。不需要任何配置，也不需要 API Key。",
    },
  },
  {
    title: { en: "Connect a model", zh: "接上一个模型" },
    body: {
      en: "Run keelson init in a terminal and paste a key from any OpenAI- or Anthropic-compatible provider. keelson doctor tells you if anything is off.",
      zh: "在终端里运行 keelson init，粘贴任意 OpenAI 或 Anthropic 兼容服务商的 Key。哪里不对，keelson doctor 会告诉你。",
    },
  },
  {
    title: { en: "Give it a task", zh: "给它一个任务" },
    body: {
      en: "Open the console and type what you want done in the folder you started it from. Start with something that only reads: “explain what this project does”.",
      zh: "打开控制台，输入你想在当前目录里完成的事。可以先从只读的任务开始：“解释一下这个项目是做什么的”。",
    },
  },
];

export const features = [
  {
    name: { en: "Permission modes", zh: "权限模式" },
    what: { en: "How much the agent may do without asking.", zh: "Agent 可以不问你就做多少事。" },
    how: { en: "Console: the Permissions menu. Terminal: --plan, --accept-edits, --dont-ask, --yolo.", zh: "控制台：输入框下方的“权限模式”。终端：--plan、--accept-edits、--dont-ask、--yolo。" },
  },
  {
    name: { en: "Plan mode", zh: "计划模式" },
    what: { en: "Read-only analysis first; you approve the plan before anything changes.", zh: "先只读分析并写出计划，你批准之后才开始改动。" },
    how: { en: "keelson --plan \"…\", or /plan inside the terminal session.", zh: "keelson --plan \"…\"，或在终端会话里输入 /plan。" },
  },
  {
    name: { en: "Skills", zh: "技能" },
    what: { en: "Reusable instructions the agent pulls in when a task matches.", zh: "可复用的做事规则，任务匹配时会被自动取用。" },
    how: { en: "Put a SKILL.md under .keelson/skills/<name>/, or let one be extracted from your feedback. /skills lists them.", zh: "把 SKILL.md 放到 .keelson/skills/<名称>/ 下，或者让它从你的反馈中自动提取。/skills 可以列出全部。" },
  },
  {
    name: { en: "Skill evolution", zh: "技能自进化" },
    what: { en: "Your lasting feedback becomes a skill; changes must pass a no-worse-than-before check.", zh: "你给出的长期反馈会变成技能；改动必须通过“不比以前差”的检查。" },
    how: { en: "Say “from now on, …”. See the Skill evolution page for a change that was accepted and one that was blocked.", zh: "说一句“以后都……”。“技能进化”页面展示了一次被采纳和一次被拦下的改动。" },
  },
  {
    name: { en: "Long-term memory", zh: "长期记忆" },
    what: { en: "Facts about you and the project, kept per project across sessions.", zh: "关于你和项目的事实，按项目保存，跨会话有效。" },
    how: { en: "The agent writes them itself. /memory lists what it has saved.", zh: "由 Agent 自己写入。/memory 可以查看它记了什么。" },
  },
  {
    name: { en: "Sessions", zh: "会话" },
    what: { en: "Several conversations side by side, each with its own history; finished ones can be reopened.", zh: "多个对话并行，各有各的历史；结束的会话可以重新打开继续。" },
    how: { en: "Console: the session list on the left. Terminal: keelson --resume.", zh: "控制台：左侧的会话列表。终端：keelson --resume。" },
  },
  {
    name: { en: "Traces and replay", zh: "轨迹与回放" },
    what: { en: "Every run is recorded as an event log that can be replayed step by step.", zh: "每次运行都会录成事件日志，可以逐步回放。" },
    how: { en: "The Traces page, or keelson web --replay with no model at all.", zh: "“运行轨迹”页面；或者 keelson web --replay，完全不需要模型。" },
  },
  {
    name: { en: "MCP tools and sub-agents", zh: "MCP 工具与子 Agent" },
    what: { en: "External tool servers, and helper agents that work in their own context.", zh: "外部工具服务，以及在独立上下文里工作的辅助 Agent。" },
    how: { en: "Copy .mcp.example.json to .mcp.json. Sub-agents are started by the model when a task calls for one.", zh: "把 .mcp.example.json 复制为 .mcp.json。子 Agent 由模型在需要时自行启动。" },
  },
];

// ─── Concept pop-overs in the inspector ─────────────────────

export const help = {
  loop: {
    en: "One turn is a loop: the model asks for a tool, the gate checks it, the runtime runs it, the result goes back to the model, and so on until the model answers. The highlighted dot is the step happening now.",
    zh: "一轮对话是一个循环：模型请求工具 → 权限门检查 → 运行时执行 → 结果回给模型，如此往复直到模型作答。亮起的圆点是当前正在进行的步骤。",
  },
  session: {
    en: "Tokens are the units a model reads and writes; cost is estimated from them. The context window is how much the model can hold at once — when it fills up, older history gets folded.",
    zh: "Token 是模型读写的计量单位，成本据此估算。上下文窗口是模型一次能装下的量——快满时，较早的历史会被折叠。",
  },
  gate: {
    en: "Every tool call passes the permission gate first. Allowed: ran without asking. Asked you: stopped for your decision. Denied: never ran, and the model was told why.",
    zh: "每次工具调用都先过权限门。放行：没问你就执行了。询问用户：停下来等你决定。拒绝：没有执行，并且把原因告诉了模型。",
  },
  guards: {
    en: "Loop guard: times a tool was stopped for repeating without new results. Context folds: times history was condensed. Tool errors: calls that failed — the model sees those failures as they are.",
    zh: "循环保护：某个工具因反复调用且没有新结果而被叫停的次数。上下文折叠：历史被压缩的次数。工具报错：失败的调用数——这些失败会原样告诉模型。",
  },
  perf: {
    en: "Where this turn's time went. Model time is waiting for the model to respond; tool time is the runtime executing. Time spent waiting for your decision is shown separately and not counted against either.",
    zh: "这一轮的时间花在了哪里。模型耗时是等模型回复；工具耗时是运行时在执行。等你做决定的时间单独列出，不算在两者头上。",
  },
};

// ─── Guided-tour captions (keys come from guide.js) ─────────

export const captions = {
  task: {
    en: "A task arrives. From here on the model can only propose actions; whether each one runs is up to the runtime.",
    zh: "任务来了。从这里开始，模型只能“提议”动作；每个动作能不能执行，由运行时说了算。",
  },
  gate_read: {
    en: "The model asked to read. The permission gate checked the request and let it through without asking: reading changes nothing.",
    zh: "模型请求读取内容。权限门检查后直接放行，没有问你——因为读取不会改变任何东西。",
  },
  gate_ask: {
    en: "This command matches a destructive pattern, so the gate has stopped the loop. Nothing runs until someone decides.",
    zh: "这条命令匹配了危险模式，权限门把循环停住了。在有人做出决定之前，什么都不会执行。",
  },
  gate_user_deny: {
    en: "Denied. The command never ran. The refusal is handed back to the model as the tool's result, so it knows what happened and can choose another way.",
    zh: "已拒绝，命令没有执行。这个“被拒绝”会作为工具结果交还给模型，所以它知道发生了什么，可以换一种做法。",
  },
  gate_user_allow: {
    en: "Approved once, for this command only. The same command will not ask again in this session; a different one will.",
    zh: "仅对这条命令批准了一次。同一条命令在本次会话里不会再问；换一条命令还会再问。",
  },
  gate_auto_deny: {
    en: "The gate refused this by itself: the current permission mode does not allow it. The model is told why.",
    zh: "权限门自己拒绝了这次调用：当前权限模式不允许。原因会告诉模型。",
  },
  tool_edit: {
    en: "A file was changed. The runtime only lets the model edit a file it has read in this session, so it cannot overwrite something it never looked at.",
    zh: "文件被修改了。运行时只允许模型修改它在本次会话里读过的文件，所以它不会覆盖自己没看过的内容。",
  },
  tool_shell_fail: {
    en: "The command failed, and the model receives that failure as it is — exit code and output. Often a failing test is exactly the information it needs.",
    zh: "命令失败了，模型收到的是原样的失败信息——退出码和输出。很多时候，一个失败的测试正是它需要的线索。",
  },
  tool_shell_ok: {
    en: "The command ran and its output went back to the model. Output appears while the command is running, and stopping the turn kills the command.",
    zh: "命令执行完毕，输出回给了模型。输出是边跑边显示的；中途停止这一轮，命令也会被一并终止。",
  },
  skill_used: {
    en: "A stored skill matched this task, so the agent loaded it. This is how an earlier preference carries over to new work.",
    zh: "有一条已保存的技能匹配上了这个任务，于是被调用。之前说过的偏好，就是这样延续到新任务里的。",
  },
  skill_evolved: {
    en: "In the background, the previous user message was recognised as a lasting rule and turned into a skill. It was taken from the user's words, not from the model judging itself.",
    zh: "在后台，上一句用户输入被识别为一条长期规则，并沉淀成了技能。它取自用户的原话，而不是模型的自我评价。",
  },
  context_fold: {
    en: "The conversation was getting too long, so older history was condensed into structured notes. The task continues with the notes in place of the raw history.",
    zh: "对话太长了，较早的历史被压缩成结构化笔记。任务带着这些笔记继续，不再携带原始历史。",
  },
  loop_guard: {
    en: "The same tool kept being called with results that no longer changed. The loop guard skipped the call and told the model to answer with what it has.",
    zh: "同一个工具被一再调用，结果却不再变化。循环保护跳过了这次调用，并让模型用已有信息作答。",
  },
  turn_end: {
    en: "The turn is over. The panel on the right now shows what it cost and how many calls the gate allowed, asked about, or denied.",
    zh: "这一轮结束了。右侧面板显示了这一轮的开销，以及权限门放行、询问、拒绝各多少次。",
  },
};
