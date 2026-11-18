# Keelson 架构设计

[English](../architecture.md) | **中文**

## 1. 总体定位

Keelson 的核心是 Harness。

模型负责推理和提出工具调用意图，Runtime 负责真正的执行边界、上下文管理、会话保存、Skills 自进化和外部扩展。

普通聊天工具通常是：

```text
用户输入 -> 调模型 -> 打印回复
```

Keelson 更接近：

```text
用户输入
  -> CLI / REPL / Web 控制台 / 桌面窗口
  -> Agentic Harness
  -> 给项目拍快照（检查点）、把用户的规则记进任务账本
  -> Prompt / Skills / Memory / MCP 初始化
  -> 模型推理
  -> tool call
  -> 权限检查（按能不能撤销）+ 范围护栏
  -> 工具执行
  -> tool result 回写
  -> 多轮循环
  -> 轮末验证：真的跑过检查并通过了吗
  -> session 和任务账本保存
  -> Skills usage tracking
  -> online skill evolution
```

它的架构重点是把模型、工具、记忆、技能和外部能力统一编排到一个可控 runtime 里。

## 2. 架构总览

![系统总览](../assets/diagrams/overview.zh.svg)

本页的图都由 `scripts/build_diagrams.py` 生成；要改图请改脚本，不要直接改 SVG。

从层次上看，系统可以分成这几层：

| 层级 | 主要文件 | 职责 |
|------|----------|------|
| 入口层 | `keelson/main.py`、`ui.py`、`commands.py`、`terminal_input.py`、`keelson/web/`、`desktop.py`、`doctor.py` | 参数解析、REPL、one-shot、终端渲染、Web 控制台与多会话、Windows 桌面窗口、`doctor` / `init` |
| 事件总线 | `keelson/events.py` | 运行时只发布事件，所有前端都订阅同一条事件流；耗时 span；按会话绑定总线 |
| Harness 层 | `keelson/agent.py` | Agent Loop、协议适配、工具调度、上下文压缩、session 保存、轮末验证 |
| 安全与撤销 | `keelson/safety.py`、`checkpoints.py`、`trust.py`、`keelson/platforms/` | 按可逆性判断命令和路径、快照与 `/undo`、工作区信任；各平台的 shell、回收站和进程管理 |
| 完工保障 | `keelson/verify.py`、`ledger.py`、`scope.py` | 完工验证、任务账本、范围护栏 |
| 能力层 | `keelson/tools.py`、`textio.py`、`search.py`、`skills.py`、`memory.py`、`mcp_client.py`、`subagent.py` | 工具与权限门、保编码的文件读写、搜索、Skills、Memory、MCP、子 Agent |
| Prompt 层 | `keelson/prompt.py` | 动态构建 system prompt，前缀保持稳定以命中缓存 |
| 成本层 | `keelson/models.py`、`usage.py` | 模型登记表（上下文窗口、输出上限、价格）、用量账本与预算 |
| 演化层 | `keelson/online_skill_evolution.py`、`keelson/skill_evolution.py`、`keelson/online_skill_eval.py` | Skills 抽取、演化、审计、评测 |
| 持久化层 | `.keelson/`、`KEELSON.md`、`~/.keelson/` | 项目规则与检查命令；用户级的 `config.env`、配置档，以及每个项目的会话、轨迹、检查点、账本和用量 |

这几个层级的边界比较清楚：

- `main.py` 只管入口。
- `agent.py` 只管运行时调度。
- `tools.py` 只管工具和权限；`safety.py` 只管“这个操作能不能撤销、该不该问”。
- `checkpoints.py`、`verify.py`、`ledger.py`、`scope.py` 各管一个运行时保障，由 `agent.py` 在一轮的开头、工具调用前后和结尾调用。
- `skills.py` / `skill_evolution.py` 只管能力沉淀。
- `memory.py` 只管长期事实。
- `mcp_client.py` 只管外部工具接入。
- `subagent.py` 只管隔离上下文任务。

## 3. 一次请求的主链路

![一轮对话的完整路径](../assets/diagrams/turn.zh.svg)

主链路从 `keelson/main.py` 进入，最终落到 `Agent.chat()`。

```text
用户输入
  -> run_repl() / run_one_shot()
  -> Agent.chat(user_message)
  -> 给项目拍快照（_begin_checkpoint），把用户的规则记进账本
  -> 首次 chat 初始化 MCP
  -> 检索 Skills 并注入用户消息
  -> 异步预取 Memory
  -> 选择 _chat_openai() 或 _chat_anthropic()
  -> 模型返回文本或工具调用
  -> _execute_tool_call()
  -> 权限检查、范围护栏与工具执行
  -> tool result 回写模型
  -> 模型继续推理或准备结束
  -> 轮末验证（_end_of_turn_check）：没通过就把真实输出交还模型
  -> 后台执行 Skill usage tracking / online evolution
  -> 保存新的 pending window
  -> 自动保存 session
```

这条链路里最重要的三件事是：

1. 模型不直接操作环境。
2. 工具执行前有权限边界。
3. 任务结束后才进入能力沉淀链路。

## 4. 入口层

入口层主要在 `keelson/main.py`。

它负责：

- 解析 CLI 参数。
- 按优先级加载配置：环境变量 > 配置档（`~/.keelson/profiles/<名字>.env`）> 项目的 `.keelson/config.env` > `~/.keelson/config.env` > 项目里旧的 `.env`。
- 判断模型协议。
- 创建 `Agent`。
- 启动 REPL 或一次性任务。
- 处理 `/skills`、`/memory`、`/compact`、`/plan`、`/skill-*` 等命令。

关键函数：

| 函数 | 作用 |
|------|------|
| `parse_args()` | 解析模型、权限、计划模式、resume 等参数 |
| `_load_env_file()` | 按上面的优先级加载配置文件 |
| `_resolve_api_config()` | 判断 OpenAI-compatible 还是 Anthropic-compatible |
| `run_repl()` | 交互式主循环 |
| `run_one_shot()` | 一次性执行 |
| `main()` | 程序总入口 |

入口层不关心工具细节，也不关心 Skills 如何演化。它只负责把配置和输入送进 Runtime。

### 4.1 显示与运行解耦：事件总线

Runtime 不直接打印。它在 `keelson/events.py` 的进程内总线上发布带类型的事件（`tool_call`、`permission`、`tool_result`、`context_fold` 等），前端都是订阅者：

![一条事件流，三个消费者](../assets/diagrams/events.zh.svg)

`ui.py` 里的 `print_*` 只发布事件；真正往终端画的是订阅了总线的 `TerminalRenderer`。因为录制下来的轨迹和实时流是同一种事件，回放可以走与实时运行完全相同的界面代码。

**按会话隔离。** `events.emit()` 发布到当前 `contextvars` 上下文里绑定的那条总线。Web 控制台给每个会话一条自己的总线，并在绑定了这条总线的上下文里启动每一轮对话；这一轮派生出的任务（并发工具调用、后台技能提取）会继承这个绑定。于是运行时可以同时服务多个互不串台的对话，而它自己的代码完全不知道“会话”的存在。

**耗时 span。** 循环的每一步都包在 `events.span(name)` 里，发布一个带耗时的 `span` 事件：`model_stream`、`tool`、`permission`、`skill_retrieval`、`fold`、`confirm_wait`。控制台据此画出每一轮的耗时瀑布图，`scripts/perf_report.py` 汇总 P50 / P95。

细节见 [`web-console.md`](web-console.md)。

## 5. Harness 层

Harness 层集中在 `keelson/agent.py`，这是最核心的模块。

`Agent` 维护的主要状态有：

| 状态 | 含义 |
|------|------|
| `permission_mode` | 当前权限模式 |
| `model` | 当前模型名 |
| `use_openai` | 是否走 OpenAI-compatible 路径 |
| `tools` | 当前可用工具列表 |
| `_anthropic_messages` / `_openai_messages` | 双协议消息历史 |
| `_mcp_manager` | MCP 管理器 |
| `_last_retrieved_skill_reference` | 本轮检索到的 Skill 参考 |
| `_pending_skill_extraction_window` | 等待下一轮反馈的沉淀窗口 |
| `_background_skill_tasks` | 后台 skill usage / evolution 任务 |

`Agent.chat()` 是 Runtime 的主入口，它会把任务拆给内部方法：

| 方法 | 职责 |
|------|------|
| `_augment_user_message_with_skill_context()` | 检索相关 Skills 并注入消息 |
| `_chat_openai()` | OpenAI-compatible 主循环 |
| `_chat_anthropic()` | Anthropic-compatible 主循环 |
| `_execute_tool_call()` | 统一分发工具调用 |
| `_execute_skill_tool()` | 执行 Skill |
| `_execute_agent_tool()` | 启动子 Agent |
| `_run_online_skill_evolution()` | 后台在线自进化 |
| `_run_skill_usage_tracking()` | 判断 Skill 是否真的有用 |
| `_run_compression_pipeline()` | 控制长上下文和大结果 |
| `_auto_save()` | 保存 session |

## 6. 模型协议适配

Keelson 支持两类协议：

- OpenAI-compatible
- Anthropic-compatible

协议判断在 `keelson/main.py::_resolve_api_config()`。

运行时分支在 `Agent` 里：

```text
if self.use_openai:
    self._chat_openai(user_message)
else:
    self._chat_anthropic(user_message)
```

两条路径的区别主要在消息和 tool call 表达格式：

| 差异点 | OpenAI-compatible | Anthropic-compatible |
|--------|-------------------|----------------------|
| 客户端 | `openai.AsyncOpenAI` | `anthropic.AsyncAnthropic` |
| 消息容器 | `_openai_messages` | `_anthropic_messages` |
| 工具格式 | `tool_calls` | `tool_use` block |
| tool result 回写 | `tool` role message | `tool_result` block |

但它们共享同一套：

- 工具系统。
- 权限系统。
- Skills。
- Memory。
- session。
- 自进化链路。

## 7. 工具系统与权限架构

工具系统在 `keelson/tools.py`。

工具定义集中在 `tool_definitions`，包括：

- `read_file`
- `write_file`
- `edit_file`
- `delete_files`（移到回收站）
- `list_files`
- `grep_search`
- `run_shell`
- `skill`
- `compact_context`
- `skill_evolve`
- `skill_create`
- `enter_plan_mode` / `exit_plan_mode`
- `agent`
- `update_task`（更新任务账本）
- `declare_scope`（声明要改的文件）
- `tool_search`

执行流程：

```text
模型发起 tool call
  -> Agent._execute_tool_call()
  -> 特殊工具先由 Agent 层路由
  -> 普通工具进入 tools.execute_tool()
  -> check_permission()
  -> 具体工具函数执行
  -> 结果格式化 / 截断
  -> tool result 回写模型
```

0.4 起，权限判断的核心问题是**这个操作能不能撤销**（`keelson/safety.py`）：

| 操作 | 结论 |
|------|------|
| 项目内的修改、新建文件、普通命令 | 放行：本轮快照覆盖，`/undo` 能撤回 |
| 项目内的 `delete_files` | 放行：进回收站 |
| 装包、联网、`git push`、改写历史、写项目外的文件、MCP 和未知工具 | 询问，并用界面语言写明原因 |
| 强推、`sudo`、`curl … \| sh`、路径里有没设置的变量 | 询问，只能允许一次 |
| shell 里的删除（`rm`、`del`、`Remove-Item` 等） | 拒绝，提示模型改用 `delete_files` |
| 动主目录、盘符根、项目本身或其上级；写系统目录 | 任何模式都拒绝 |

判断路径前先展开 `~`、`$HOME`、`$env:USERPROFILE`、`%VAR%`；复合命令拆开逐段判断。

其它边界：

- 只读工具默认允许。
- Plan Mode 下阻断普通写操作和 Shell。
- `acceptEdits` 与 `default` 相同（为兼容保留）。
- `bypassPermissions` 不再询问，但“任何模式都拒绝”的那些照样拒绝。
- `dontAsk` 自动拒绝需要确认的动作。
- 编辑前要先读文件，并检查 mtime。
- 克隆来的项目，其 allow 规则和 MCP 服务要等目录被信任后才生效（`keelson/trust.py`）。
- 改范围外的文件要询问（`keelson/scope.py`）。

这一层的目标不是阻止 Agent 工作，而是让能撤销的事情不打扰你、撤不回的事情一定先问你。完整的表和边界见[安全与撤销](safety.md)。

### 7.1 Shell 的执行方式

`run_shell` 走 `run_shell_async`（`asyncio.create_subprocess_shell`），而不是阻塞式的 `subprocess.run`：

- **不阻塞事件循环。** 长命令运行期间，其他会话、事件流和“停止”按钮都保持响应。其余同步工具通过 `asyncio.to_thread` 放到工作线程。
- **输出实时流出。** 边读边增量解码，按小批量发布为 `tool_output` 事件（`_OutputRelay`：约 80ms 一批，单条命令最多 40000 字符）。输出属于哪一次调用，通过 `ContextVar` 里的“当前调用 id”得知，所以 `_execute_tool_call` 的签名没有变。
- **超时或取消时杀掉整棵进程树。** Windows 用 `taskkill /F /T`，POSIX 杀进程组。只杀 shell 的话，它启动的 `pytest` 之类会变成孤儿进程继续跑。
- **返回给模型的字符串不变**（`Command failed (exit code N)…`），所以模型行为和已有评测结果不受影响。

### 7.2 检查点、完工验证、任务账本、范围护栏

这四项都由运行时强制执行，不靠提示词要求模型自觉。

- **检查点**（`keelson/checkpoints.py`）。每轮开始时，把项目写进一个独立的 git 仓库 `~/.keelson/projects/<哈希>/checkpoints/shadow.git`，用自己的 `GIT_DIR`、索引和配置（不跑钩子、不走过滤器、不转换换行），绝不碰项目自己的 `.git`。`/undo`、`/redo`、`/rewind N`、`/revert 文件` 比较的是整棵树，所以 shell 命令造成的修改、新建、删除都能还原。那一轮之后又被改过的文件只列出、不覆盖。没有 git 时退回到只给 Keelson 自己的编辑做字节快照，并明确提示。
- **完工验证**（`keelson/verify.py`）。检查命令来自 `KEELSON.md` 的 `## Verify` 一节，或自动识别（pytest、npm、cargo、go、ruff、eslint、tsc 等）。证据只来自事件流：最后一次改动之后，哪些命令真的跑过、退出码是多少。改过代码的一轮结束时如果没有通过的检查，运行时自己跑；失败就把真实输出交还模型接着修（有次数上限），最后发出 `verify` 事件，界面据此显示“已验证 / 未通过验证 / 未验证”。模型声称测试通过却找不到对应的成功运行，标为“未经证实的说法”；削弱测试的改动也会被标出。
- **任务账本**（`keelson/ledger.py`）。目标、用户的规则原话、计划、关键决定、改过的文件、最近一次验证，都放在对话之外；随会话保存，折叠时原样附在摘要后面，恢复会话时一起恢复。
- **范围护栏**（`keelson/scope.py`）。改代码前，模型用 `declare_scope` 声明要改的文件；用户提到的文件自动算进范围。改范围外的文件要询问，审阅里单独标出。

### 7.3 平台层与桌面版

平台相关的代码都在 `keelson/platforms/`：shell（Windows 上优先 PowerShell 7，否则 Windows PowerShell 5.1，输出统一为 UTF-8；其它系统用 bash）、回收站（`send2trash`）、进程管理（不弹黑框、杀整棵进程树）。`keelson/textio.py` 编辑文件时保留原编码（UTF-8、BOM、GBK）、换行和未改动的字节，无法原样往返的文件拒绝编辑。

`keelson/desktop.py` 用 pywebview（Windows 上是 WebView2）把同一个控制台装进窗口。窗口里的桥接对象 `DesktopApi` 负责首次运行向导；控制台服务作为子进程在所选项目目录里运行，切换项目时重启，关窗口时一起退出。`scripts/build_desktop.py` 用 PyInstaller 打出 `Keelson.exe` 和 `keelson-cli.exe`。使用说明见[桌面版使用手册](desktop.md)。

## 8. Skills 架构

Skills 是 Keelson 的长期方法载体。

它们以目录型 `SKILL.md` 存在：

```text
<project>/.keelson/skills/<skill_name>/SKILL.md
~/.keelson/skills/<skill_name>/SKILL.md
```

Skill 保存的是“可复用方法”，比如：

- 代码审查流程。
- 文档写作风格。
- 某类任务的输出结构。
- 某个领域的操作步骤。

相关模块：

| 文件 | 职责 |
|------|------|
| `keelson/skills.py` | 加载、解析、检索、执行 Skill |
| `keelson/skill_evolution.py` | 创建、演化、版本快照、provenance、usage stats |
| `keelson/online_skill_evolution.py` | 在线抽取候选、决策 add / merge / discard |

运行时分三段：

```text
对话前
  -> discover_skills()
  -> retrieve_relevant_skills()
  -> format_retrieved_skill_context()
  -> 注入用户消息

对话中
  -> 模型调用 skill 工具
  -> inline / fork 执行

对话后
  -> judge_retrieved_skill_usage()
  -> online_ingest()
  -> create_skill_file() 或 evolve_skill_file()
```

## 9. 自进化架构

Skills 自进化是 Keelson 最核心的差异点之一。

它的目标不是让 Agent 随机改 prompt，而是把用户明确反馈中稳定、可复用、未来仍有价值的方法沉淀为 Skill。

链路分四步：

1. 当前轮完成任务。
2. 下一轮用户反馈到来。
3. Extractor 抽取候选 Skill。
4. Maintainer 决定 add、merge 或 discard。

落盘和审计都在 `keelson/skill_evolution.py`：

| 能力 | 说明 |
|------|------|
| `create_skill_file()` | 创建新的 `SKILL.md` |
| `evolve_skill_file()` | 演化已有 Skill，并保存 history 快照 |
| `record_online_skill_provenance()` | 记录来源证据 |
| `record_skill_usage_judgments()` | 记录 retrieved / relevant / used 统计 |
| `format_skill_stats()` | 输出 Skill 使用效果 |

这种设计让自进化具备：

- 用户反馈证据。
- add / merge / discard 决策。
- 版本快照。
- provenance。
- usage stats。
- stale 归档。

## 10. Memory 架构

Memory 和 Skills 的职责不同：

| 类型 | 保存内容 | 示例 |
|------|----------|------|
| Memory | 事实、偏好、背景、历史决策 | 用户喜欢中文回答；项目部署在 Docker；某接口已废弃 |
| Skills | 方法、流程、风格、可复用操作步骤 | review 输出结构；政府报告写法；重构检查流程 |

Memory 在 `keelson/memory.py` 里实现：

```text
保存 Memory
  -> save_memory()
  -> 写入 ~/.keelson/projects/<project_hash>/memory/
  -> 更新 index

召回 Memory
  -> scan_memory_headers()
  -> start_memory_prefetch()
  -> select_relevant_memories()
  -> format_memories_for_injection()
```

Memory 使用项目路径 hash 隔离，避免不同项目互相污染。

## 11. MCP 架构

MCP 用来把外部工具统一接入 Agent。

Keelson 没有依赖 MCP SDK，而是自己实现了 stdio JSON-RPC Client。

相关模块是 `keelson/mcp_client.py`。

核心对象：

| 对象 | 职责 |
|------|------|
| `McpConnection` | 管理一个 MCP Server 子进程和 JSON-RPC 通信 |
| `McpManager` | 读取配置、启动多个 Server、合并工具定义、路由调用 |

启动流程：

```text
Agent.chat() 首次执行
  -> McpManager.load_and_connect()
  -> 读取全局 / 用户 / 项目配置
  -> 启动 MCP Server
  -> initialize
  -> tools/list
  -> 包装为 mcp__server__tool
  -> 合并进 Agent.tools
```

调用流程：

```text
模型调用 mcp__server__tool
  -> Agent._execute_tool_call()
  -> McpManager.is_mcp_tool()
  -> McpManager.call_tool()
  -> JSON-RPC tools/call
```

## 12. 子 Agent 架构

子 Agent 用来处理探索、规划和局部任务，减少主上下文压力。

相关模块：

| 文件 | 职责 |
|------|------|
| `keelson/subagent.py` | 定义内置子 Agent，发现自定义子 Agent |
| `keelson/agent.py` | 通过 `_execute_agent_tool()` 创建并运行子 Agent |

执行流程：

```text
模型调用 agent 工具
  -> Agent._execute_agent_tool()
  -> get_sub_agent_config(agent_type)
  -> 创建子 Agent
  -> 子 Agent 独立 run_once()
  -> 结果返回主 Agent
```

内置类型：

| 类型 | 工具范围 | 适用场景 |
|------|----------|----------|
| `explore` | 只读工具 | 快速定位和搜索 |
| `plan` | 只读工具 | 输出结构化计划 |
| `general` | 除 `agent` 外的工具 | 独立处理局部任务 |

## 13. Prompt 架构

Prompt 在 `keelson/prompt.py` 中动态构建。

`build_system_prompt()` 会拼接：

- 基础身份和工作规则。
- 当前目录。
- Git 上下文。
- 项目规则。
- Memory 说明和索引。
- Available Skills。
- Available Sub Agents。
- 延迟工具提示。

这意味着 system prompt 不是固定文本，而是 runtime 状态的一部分。

## 14. 会话、压缩和大结果持久化

长任务会产生大量消息和工具结果，必须控制上下文体积。

相关模块：

| 文件 | 职责 |
|------|------|
| `keelson/session.py` | 保存、加载、列出 session |
| `keelson/agent.py` | 自动保存、恢复、压缩消息、大结果持久化 |

主要机制：

| 机制 | 说明 |
|------|------|
| `save_session()` | 保存 session |
| `load_session()` | 恢复 session |
| `_run_compression_pipeline()` | 压缩消息和工具结果 |
| `compact_context` | 主动触发上下文压缩 |
| `_persist_large_result()` | 大工具结果写文件，只把引用留在上下文中 |
| 任务账本 | 折叠时原样附在摘要后面，用户的规则不经过模型总结 |

这部分保证 Keelson 不只是一轮任务工具，而是能支撑长程任务的 runtime。

![上下文管理三层](../assets/diagrams/context.zh.svg)

Web 控制台在此之上提供了会话的浏览与恢复：每个会话的事件流录在 `.keelson/traces/`，消息列表由 `save_session()` 保存；“继续这个会话”就是把旧事件重放到新会话的总线上（界面可见），再用 `Agent.restore_session()` 恢复消息列表（模型可见）。

## 15. 存储架构

重要路径如下：

| 数据 | 路径 | 说明 |
|------|------|------|
| 项目级 Skills | `.keelson/skills/<skill_name>/SKILL.md` | 当前项目专用能力 |
| 用户级 Skills | `~/.keelson/skills/<skill_name>/SKILL.md` | 跨项目复用能力 |
| Skill 演化审计 | `.keelson/skill-evolution/` | provenance、usage stats、history |
| Memory | `~/.keelson/projects/<project_hash>/memory/` | 项目隔离的长期记忆 |
| Session | `~/.keelson/sessions/` | 会话恢复数据 |
| 模型配置 | `~/.keelson/config.env`、`~/.keelson/profiles/<名字>.env`、项目的 `.keelson/config.env` | 接口地址、模型、Key |
| 检查点 | `~/.keelson/projects/<project_hash>/checkpoints/` | 每轮快照，供 `/undo` 使用 |
| 任务账本、用量、轨迹 | `~/.keelson/projects/<project_hash>/` | 每个项目的运行数据 |
| 检查命令 | 项目根目录 `KEELSON.md` 的 `## Verify` | 完工验证用 |
| 桌面版 | `~/.keelson/desktop.json`、`~/.keelson/logs/` | 最近项目、日志 |
| 大工具结果 | `~/.keelson/tool-results/` | 大输出持久化引用 |
| Plan 文件 | `~/.keelson/plans/` | Plan Mode 计划 |
| MCP 配置 | `.mcp.json`、`~/.keelson/settings.json` | 外部工具配置 |

存储设计体现两个原则：

- 项目相关能力放项目目录。
- 用户级偏好和跨项目能力放 home 目录。

## 16. 架构边界总结

| 问题 | 应该看哪里 |
|------|------------|
| 用户命令怎么进入系统？ | `keelson/main.py` |
| 一次请求怎么跑完整 Agent Loop？ | `keelson/agent.py` |
| 模型怎么调用工具？ | `keelson/agent.py::_execute_tool_call()` |
| 工具具体怎么执行？ | `keelson/tools.py` |
| 权限怎么判断？ | `keelson/tools.py::check_permission()`、`keelson/safety.py` |
| 怎么撤销？ | `keelson/checkpoints.py` |
| “做完了”怎么核实？ | `keelson/verify.py`、`keelson/agent.py::_end_of_turn_check()` |
| 压缩后怎么不丢规则？ | `keelson/ledger.py` |
| 桌面窗口怎么起服务？ | `keelson/desktop.py` |
| system prompt 怎么生成？ | `keelson/prompt.py` |
| Skill 怎么加载和检索？ | `keelson/skills.py` |
| Skill 怎么新增或合并？ | `keelson/online_skill_evolution.py`、`keelson/skill_evolution.py` |
| Memory 怎么保存和召回？ | `keelson/memory.py` |
| MCP 怎么接入？ | `keelson/mcp_client.py` |
| 子 Agent 怎么创建？ | `keelson/subagent.py`、`keelson/agent.py::_execute_agent_tool()` |
| session 怎么保存和恢复？ | `keelson/session.py`、`keelson/agent.py` |

## 17. 通用 Agent Runtime

Keelson 不是单纯的聊天 CLI，也不是只提供几个文件工具。

它的核心是一个通用 Runtime：

- 管模型协议。
- 管工具调用。
- 管权限（按能不能撤销）。
- 管撤销和完工验证。
- 管上下文。
- 管长期记忆。
- 管 Skills。
- 管 MCP。
- 管子 Agent。
- 管 session 和压缩。
- 管用户反馈到 Skill 的沉淀。

“通用”体现在它不依赖某个垂直场景。
“Runtime”体现在它真正控制执行边界，而不是让模型直接碰环境。
“可演化”体现在 Skills、provenance、usage stats 和评测链路。