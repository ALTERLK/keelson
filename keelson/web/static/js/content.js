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
  eyebrow: { en: "Coding agent", zh: "编码 Agent" },
  title: {
    en: "Learns your ways, checks its own work, stays out of trouble, keeps your rules, takes it all back.",
    zh: "会自进化、不骗你、不闯祸、不失忆、能撤销的编码 Agent。",
  },
  lead: {
    en: "The model only writes text; Keelson reads the files, makes the edits and runs the commands for it. So Keelson can stand guard at every step: it runs your tests itself before believing \"done\", deletes only into the Recycle Bin, undoes a whole turn including what shell commands did, keeps your rules word for word across a context fold, and turns your lasting preferences into skills.",
    zh: "模型只会输出文字，读文件、改代码、跑命令都由 Keelson 代为执行。所以它能在每一步替你把关：改完代码自己跑测试核实，删除只进回收站，每一轮都能连 shell 的改动一起撤销，你定的规矩在上下文压缩后原样保留，你的长期偏好沉淀成技能。",
  },
  primary: { en: "Watch a guided replay", zh: "看一次带讲解的回放" },
  secondaryLive: { en: "Open the console", zh: "打开控制台" },
  secondaryStatic: { en: "Run it yourself", zh: "在自己电脑上运行" },
};

export const problems = [
  {
    title: { en: "It learns how you like things", zh: "会自进化：记得你喜欢怎么做" },
    body: {
      en: "Tell it once — “from now on, explain code as bullet points” — and that becomes a skill it applies next time. A skill only changes if the new version is no worse than the old one.",
      zh: "说一次就够——“以后解释代码都用要点列表”——它会沉淀成一条技能，下次自动套用。技能只有在新版本不比旧版本差时才会更新。",
    },
  },
  {
    title: { en: "It does not claim work it did not check", zh: "不骗你：完成由运行时核实" },
    body: {
      en: "After a turn that changed code, Keelson runs the project's tests itself and shows a badge: verified, checks fail, or not verified. \"Tests pass\" without a passing run is flagged.",
      zh: "改过代码的一轮，Keelson 自己跑项目的测试，结论前挂上徽章：已验证、未通过、或未验证。模型说“测试通过”却没有对应的成功运行，会被标出来。",
    },
  },
  {
    title: { en: "It cannot wreck your machine", zh: "不闯祸：删除只进回收站" },
    body: {
      en: "Deleting from the shell is refused; delete_files moves things to the Recycle Bin. The home folder, drive roots and disk-erasing commands are refused in every mode.",
      zh: "shell 里的删除命令一律拒绝，删除只能经 delete_files 进回收站。删主目录、盘符根，或者擦盘，在任何模式下都拒绝。",
    },
  },
  {
    title: { en: "It keeps your rules after a fold", zh: "不失忆：压缩后不忘你的规矩" },
    body: {
      en: "Your rules are kept word for word in a task ledger outside the conversation, with the goal and the plan, and go back in unchanged after the context is folded.",
      zh: "你说过的规则按原话存进任务账本，连同目标和计划，放在对话之外；上下文折叠后原样放回。",
    },
  },
  {
    title: { en: "Undo covers shell commands", zh: "能撤销：连 shell 的改动一起撤" },
    body: {
      en: "Each turn starts with a snapshot of the project. /undo puts back everything the turn changed, including files that commands created, changed or deleted.",
      zh: "每一轮开始前给项目拍快照。/undo 还原这一轮的全部改动，包括命令新建、修改、删除的文件。",
    },
  },
  {
    title: { en: "It changes only what you asked for", zh: "不乱改：只动该动的文件" },
    body: {
      en: "Files you @-mention and files the model declares make up the scope. Changing anything else asks you first, and the review marks it.",
      zh: "你 @ 的文件加上模型声明的文件构成范围。改范围外的文件要先问你，审阅时也会单独标出。",
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
      en: "Keelson speaks both the OpenAI and the Anthropic protocol (DeepSeek is the default), keeps the prompt prefix stable so the provider's cache hits, retries transient errors with backoff, and stops at a turn or cost cap.",
      zh: "Keelson 同时支持 OpenAI 和 Anthropic 两种协议（默认 DeepSeek），保持提示词前缀不变让服务商的缓存命中，对瞬时错误做退避重试，到达轮次或成本上限时停止。",
    },
    code: "keelson/agent.py · _chat_openai / _chat_anthropic",
  },
  {
    id: "gate",
    name: { en: "Permission gate", zh: "权限门" },
    plain: {
      en: "Before a tool runs, the gate decides: allow, ask you, or refuse.",
      zh: "工具执行之前，权限门先做决定：放行、问你、还是拒绝。",
    },
    detail: {
      en: "It decides by what undo can reverse. Edits and ordinary commands inside the project run without asking, because /undo covers them. Installing packages, the network, git push and writing outside the project ask, with the reason. Deleting the home folder or erasing a disk is refused in every mode.",
      zh: "按能不能撤销来决定：项目里的编辑和普通命令能被 /undo 撤回，直接放行；装包、联网、git push、写项目外撤不回，停下来问你，并写明原因；删主目录、擦盘这类在任何模式下都拒绝。",
    },
    code: "keelson/tools.py · check_permission · keelson/safety.py",
  },
  {
    id: "tool",
    name: { en: "Tool", zh: "工具执行" },
    plain: {
      en: "The runtime — not the model — reads the file or runs the command.",
      zh: "由运行时（而不是模型）去读文件、跑命令。",
    },
    detail: {
      en: "The project was snapshotted when the turn began, in a separate git repository, so whatever a command changes can be undone. Shell commands stream their output and are killed with their whole process tree on timeout or Stop.",
      zh: "这一轮开始时已经给项目拍了快照（独立的 git 仓库），所以命令改了什么都能撤回。shell 命令边跑边显示输出；超时或点“停止”时，连同整个进程树一起杀掉。",
    },
    code: "keelson/tools.py · run_shell_async · keelson/checkpoints.py",
  },
  {
    id: "result",
    name: { en: "Result", zh: "结果回写" },
    plain: {
      en: "What the tool returned — including errors and denials — goes back to the model, which decides the next step.",
      zh: "工具返回了什么——包括报错和被拒绝——都原样告诉模型，由它决定下一步。",
    },
    detail: {
      en: "When the model says it is done and code changed, Keelson runs the project's checks itself and hands failures back with the real output. The loop guard stops a tool called again and again with results that no longer change.",
      zh: "模型说做完了、而代码改过时，Keelson 自己跑项目的检查，失败就把真实输出交还模型继续修。循环保护盯着这一步：同一个工具一再被调用而结果不再变化，就会被叫停。",
    },
    code: "keelson/verify.py · keelson/agent.py · _loop_guard_check",
  },
  {
    id: "fold",
    name: { en: "Fold", zh: "上下文折叠" },
    plain: {
      en: "When the conversation gets too long for the model, older history is condensed so the task can continue.",
      zh: "对话长到模型装不下时，较早的历史会被压缩，让任务可以继续。",
    },
    detail: {
      en: "Older history is rewritten into structured notes. The task ledger is not summarised: the goal, your rules word for word, the plan and the files changed go back in exactly as they were.",
      zh: "较早的历史被改写成结构化笔记；任务账本不交给模型总结：目标、你的规则原话、计划和改过的文件原样放回。",
    },
    code: "keelson/agent.py · _run_compression_pipeline · keelson/ledger.py",
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
    title: { en: "Install and connect a model", zh: "装上，接上模型" },
    body: {
      en: "On Windows, download Keelson.exe from Releases: a first-run wizard takes the key and the project folder. Elsewhere, pip install it and run keelson init. keelson doctor tells you if anything is off.",
      zh: "Windows 上从 Releases 下载 Keelson.exe，首次运行的向导会带你填 Key、选项目文件夹。其他系统用 pip 安装后运行 keelson init。哪里不对，keelson doctor 会告诉你。",
    },
  },
  {
    title: { en: "Give it a task, then review", zh: "派个任务，再审阅" },
    body: {
      en: "Type what you want done in the project folder. When the turn ends, the Review tab shows each file's diff and the verification badge; Undo puts it all back.",
      zh: "输入你想在项目文件夹里完成的事。一轮结束后，“审阅”页列出每个文件的 diff 和验证徽章；不满意就点“撤销”。",
    },
  },
];

export const features = [
  {
    name: { en: "Undo", zh: "撤销" },
    what: { en: "Put back everything a turn changed, shell commands included.", zh: "还原一轮的全部改动，包括 shell 命令造成的。" },
    how: { en: "Console: Review → Undo, or Put back for one file. Terminal: /undo, /rewind N, /revert FILE.", zh: "控制台：审阅 → 撤销，或单个文件“放回”。终端：/undo、/rewind N、/revert 文件。" },
  },
  {
    name: { en: "Verified completion", zh: "完工验证" },
    what: { en: "The badge on each turn comes from checks Keelson ran, not from the model's word.", zh: "每轮的徽章来自 Keelson 真实运行的检查，而不是模型怎么说。" },
    how: { en: "Automatic. Write the checks in KEELSON.md's Verify section, or /verify init.", zh: "自动进行。检查命令写在 KEELSON.md 的 Verify 一节，或用 /verify init 生成。" },
  },
  {
    name: { en: "Task ledger", zh: "任务账本" },
    what: { en: "Goal, your rules word for word, plan and files changed; survives a fold and --resume.", zh: "目标、你的规则原话、计划、改过的文件；折叠和 --resume 之后都还在。" },
    how: { en: "Console: the Task tab. Terminal: /task.", zh: "控制台：“任务”页。终端：/task。" },
  },
  {
    name: { en: "Permission modes", zh: "权限模式" },
    what: { en: "How much the agent may do without asking.", zh: "Agent 可以不问你就做多少事。" },
    how: { en: "Console: the Permissions menu. Terminal: Shift+Tab, or --plan, --accept-edits, --dont-ask, --yolo.", zh: "控制台：输入框下方的“权限模式”。终端：Shift+Tab，或 --plan、--accept-edits、--dont-ask、--yolo。" },
  },
  {
    name: { en: "Plan mode", zh: "计划模式" },
    what: { en: "Read-only analysis first; you approve the plan before anything changes.", zh: "先只读分析并写出计划，你批准之后才开始改动。" },
    how: { en: "Choose Plan in the Permissions menu, keelson --plan \"…\", or Shift+Tab in the terminal.", zh: "在“权限模式”里选“计划”，或 keelson --plan \"…\"，或在终端里按 Shift+Tab。" },
  },
  {
    name: { en: "Skills and skill evolution", zh: "技能与自进化" },
    what: { en: "Your lasting feedback becomes a skill; changes must pass a no-worse-than-before check.", zh: "你给出的长期反馈会变成技能；改动必须通过“不比以前差”的检查。" },
    how: { en: "Say “from now on, …”. The Skill evolution page shows a change that was accepted and one that was blocked.", zh: "说一句“以后都……”。“技能进化”页面展示了一次被采纳和一次被拦下的改动。" },
  },
  {
    name: { en: "Desktop app", zh: "桌面版" },
    what: { en: "This console in its own window, with a first-run wizard and recent projects.", zh: "这个控制台装在独立窗口里，带首次运行向导和最近项目列表。" },
    how: { en: "Windows: Keelson.exe from Releases. From a checkout: keelson desktop.", zh: "Windows：从 Releases 下载 Keelson.exe。源码运行：keelson desktop。" },
  },
  {
    name: { en: "Sessions, traces and replay", zh: "会话、轨迹与回放" },
    what: { en: "Several conversations side by side; every run is recorded and can be replayed step by step.", zh: "多个对话并行；每次运行都录成事件日志，可以逐步回放。" },
    how: { en: "The session list on the left and the Traces page. Terminal: keelson --resume.", zh: "左侧的会话列表和“运行轨迹”页。终端：keelson --resume。" },
  },
];

// ─── Concept pop-overs in the inspector ─────────────────────

export const help = {
  loop: {
    en: "One turn is a loop: the model asks for a tool, the gate checks it, the runtime runs it, the result goes back to the model, and so on until the model answers. The highlighted dot is the step happening now.",
    zh: "一轮对话是一个循环：模型请求工具 → 权限门检查 → 运行时执行 → 结果回给模型，如此往复直到模型作答。亮起的圆点是当前正在进行的步骤。",
  },
  session: {
    en: "Tokens are the units a model reads and writes; cost is estimated from them. The context window is how much the model can hold at once — when it fills up, older history gets folded. Cache hits are input the provider had already seen, billed at a fraction of the price.",
    zh: "Token 是模型读写的计量单位，成本据此估算。上下文窗口是模型一次能装下的量——快满时，较早的历史会被折叠。缓存命中是服务商已经见过的输入，只按一小部分价格计费。",
  },
  gate: {
    en: "Every tool call passes the permission gate first. Allowed: ran without asking (undo covers it). Asked you: stopped for your decision, because undo cannot reverse it. Denied: never ran, and the model was told why.",
    zh: "每次工具调用都先过权限门。放行：没问你就执行了（撤销管得到）。询问用户：撤销管不到，停下来等你决定。拒绝：没有执行，并且把原因告诉了模型。",
  },
  guards: {
    en: "Loop guard: times a tool was stopped for repeating without new results. Context folds: times history was condensed. Tool errors: calls that failed — the model sees those failures as they are.",
    zh: "循环保护：某个工具因反复调用且没有新结果而被叫停的次数。上下文折叠：历史被压缩的次数。工具报错：失败的调用数——这些失败会原样告诉模型。",
  },
  perf: {
    en: "Where this turn's time went. Model time is waiting for the model to respond; tool time is the runtime executing. Time spent waiting for your decision is shown separately and not counted against either.",
    zh: "这一轮的时间花在了哪里。模型耗时是等模型回复；工具耗时是运行时在执行。等你做决定的时间单独列出，不算在两者头上。",
  },
  verify: {
    en: "After a turn that changed code, Keelson runs the project's checks itself (KEELSON.md's Verify section, or the detected tests). The badge comes from those runs, not from what the model says.",
    zh: "改过代码的一轮结束时，Keelson 自己运行项目的检查命令（KEELSON.md 的 Verify 一节，或识别出的测试）。徽章来自这些真实运行，而不是模型怎么说。",
  },
  undo: {
    en: "Every turn starts with a snapshot of the project, so undo puts back everything the turn changed — including what shell commands changed, created or deleted. Your project's own .git is never touched. A file changed again after the turn is listed instead of overwritten.",
    zh: "每一轮开始前都会给项目拍快照，所以撤销能还原这一轮的全部改动——包括 shell 命令修改、新建、删除的文件。项目自己的 .git 不会被碰。如果某个文件在这一轮之后又被改过，会先列出来，不会直接覆盖。",
  },
  ledger: {
    en: "Kept outside the conversation and saved every turn: the goal, your rules word for word, the plan, the files changed. When the context is folded it goes back in unchanged, so a long task does not forget what you said.",
    zh: "保存在对话之外、每轮落盘：目标、你的规则（原话）、计划、改过的文件。上下文折叠后原样放回，所以长任务不会忘记你说过的话。",
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
    en: "Undo cannot reverse this one, so the gate has stopped the loop and says why. Nothing runs until someone decides.",
    zh: "这一步撤销管不到，所以权限门把循环停住了，并写明了原因。在有人做出决定之前，什么都不会执行。",
  },
  gate_user_deny: {
    en: "Denied. The command never ran. The refusal is handed back to the model as the tool's result, so it knows what happened and can choose another way.",
    zh: "已拒绝，命令没有执行。这个“被拒绝”会作为工具结果交还给模型，所以它知道发生了什么，可以换一种做法。",
  },
  gate_user_allow: {
    en: "Approved once, for this call only. The next call like it asks again, unless you chose this session or this project.",
    zh: "只批准了这一次。下次同样的操作还会再问，除非你选的是“本会话”或“本项目”。",
  },
  gate_auto_deny: {
    en: "The gate refused this by itself: the current permission mode does not allow it, or it is on the list refused in every mode. The model is told why.",
    zh: "权限门自己拒绝了这次调用：当前权限模式不允许，或者它属于任何模式下都拒绝的操作。原因会告诉模型。",
  },
  tool_edit: {
    en: "A file was changed. The runtime only lets the model edit a file it has read in this session, so it cannot overwrite something it never looked at.",
    zh: "文件被修改了。运行时只允许模型修改它在本次会话里读过的文件，所以它不会覆盖自己没看过的内容。",
  },
  tool_delete: {
    en: "Deleted, but only into the Recycle Bin: delete_files is the one way to delete, and /undo or the Recycle Bin brings the file back.",
    zh: "删了，但只是进了回收站：delete_files 是唯一的删除方式，/undo 或回收站都能把文件找回来。",
  },
  tool_shell_fail: {
    en: "The command failed, and the model receives that failure as it is — exit code and output. Often a failing test is exactly the information it needs.",
    zh: "命令失败了，模型收到的是原样的失败信息——退出码和输出。很多时候，一个失败的测试正是它需要的线索。",
  },
  tool_shell_ok: {
    en: "The command ran and its output went back to the model. Whatever it changed in the project can be undone: the turn began with a snapshot.",
    zh: "命令执行完毕，输出回给了模型。它在项目里改了什么都能撤回：这一轮开始时已经拍了快照。",
  },
  verify: {
    en: "The model says it is done. Keelson does not take its word for it: it ran the project's checks itself, and the badge shows what really happened.",
    zh: "模型说做完了。Keelson 不信口头说法：它自己跑了项目的检查，徽章显示的是真实结果。",
  },
  undo: {
    en: "Undo: every file the turn touched is back as it was, including the ones shell commands created or changed. /redo puts the turn back.",
    zh: "撤销：这一轮碰过的文件全部恢复原样，包括 shell 命令新建或修改的。/redo 可以再放回来。",
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
    en: "The conversation was getting too long, so older history was condensed into structured notes, and the task ledger went back in unchanged.",
    zh: "对话太长了，较早的历史被压缩成结构化笔记，任务账本原样放回。",
  },
  loop_guard: {
    en: "The same tool kept being called with results that no longer changed. The loop guard skipped the call and told the model to answer with what it has.",
    zh: "同一个工具被一再调用，结果却不再变化。循环保护跳过了这次调用，并让模型用已有信息作答。",
  },
  turn_end: {
    en: "The turn is over. The panel on the right shows what it cost, the cache hit rate, and how many calls the gate allowed, asked about, or denied.",
    zh: "这一轮结束了。右侧面板显示了这一轮的开销、缓存命中率，以及权限门放行、询问、拒绝各多少次。",
  },
};
