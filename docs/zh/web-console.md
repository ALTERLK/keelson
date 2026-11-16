# Web 控制台

[English](../web-console.md) | **中文**

`keelson web` 为 CLI 所用的同一个运行时打开一个浏览器控制台。它不增加任何 Agent 行为，只是另一种**观察**运行过程、并回答权限询问的方式。

```bash
pip install -e ".[web]"
keelson web              # 配置了 API Key 时为实时模式
keelson web --replay     # 只回放录制的轨迹，不调用模型
```

第一次接触这个项目？先看[上手指南](getting-started.md)。这一页讲的是控制台是怎么做出来的。

## 核心思路：一条事件流，多个消费者

![一条事件流，三个消费者](../assets/diagrams/events.zh.svg)

改造之前，循环里直接调用 `print_*` 函数往终端画，“发生了什么”和“怎么显示”是同一段代码。现在 `ui.py` 里的 `print_*` 只负责发布事件，终端渲染器只是订阅者之一。

事件总线（`agents/events.py`）的几个性质：

- **同步、有序分发。** 订阅者看到的顺序与循环产生的顺序完全一致，终端的流式输出依赖这一点。
- **订阅者隔离。** 某个订阅者抛异常只会被记录并吞掉。关掉一个浏览器标签页，不应该让 Agent 停下来。
- **有界历史。** 保留最近 5000 条事件及其序号，晚连接或断线重连的消费者可以补齐。
- **“当前总线”是一个上下文变量。** `events.emit()` 发布到当前 `contextvars` 上下文里绑定的那条总线，没有绑定时落回进程级总线。多会话就建立在这一层间接之上。

## 会话

一个服务进程里可以有多个互相独立的对话（`agents/web/sessions.py`）。每个 `Session` 拥有自己的 `EventBus`、`Agent`、轨迹录制器和待回答的权限询问。

运行时本身完全不知道“会话”这个概念。一轮对话开始时，会话复制当前上下文，在副本里绑定自己的总线，然后在这个上下文里创建这一轮的任务：

```python
context = contextvars.copy_context()
context.run(events.bind_bus, self.bus)
loop.create_task(self._run_turn(message), context=context)
```

这一轮内部再创建的任务——并发的工具调用、后台的技能提取——会再次复制上下文，所以这个绑定会跟着整轮对话走，不需要改任何函数签名来传参。给命令的实时输出标上“属于哪一次工具调用”用的也是同一个机制（`events.tool_call(id)`）。

被隔离的：对话历史、事件流、权限模式、权限询问、录制文件。**没有**被隔离的：所有会话共用服务进程的工作目录。所以它们是“关于同一个工作区的几场对话”，而不是几个沙箱。

同时最多 8 个活跃会话。请求第 9 个时，最久没用过的空闲会话会被收起；如果 8 个都在忙，请求以 409 拒绝。

**历史与恢复。** 被收起或关闭的会话会留下两个文件：轨迹（`.keelson/traces/<时间>-<id>.jsonl`，记录每一个事件）和保存下来的消息列表。侧边栏会列出它们。点开是只读回放；“继续这个会话”会新建一个实时会话，把旧事件重放到新总线上（于是界面上能看到之前的对话），再把消息列表恢复进 Agent（于是模型记得之前说过什么）。服务重启之后依然有效。

## 服务端 → 浏览器：Server-Sent Events

`GET /api/sessions/{id}/events` 是一个长连接响应，每个事件一帧：

```text
id: 42
data: {"seq": 42, "ts": 1731640000.1, "type": "tool_call", "data": {...}}
```

为什么用 SSE 而不是 WebSocket：数据是单向的（浏览器自己的操作很少，用普通 POST 就够），SSE 就是普通 HTTP、不需要协议升级，而且 `EventSource` 会自动重连并带上 `Last-Event-ID`，服务端可以精确地从断点续传。

续传要做对，有一个细节：处理函数**先订阅，再读积压**。两步之间发布的事件会同时出现在两边，再用序号去重即可。如果反过来先读积压，中间就有一个会丢事件的窗口。

## 浏览器 → 服务端

| 接口 | 作用 |
|---|---|
| `GET /api/state` | 是否配置了模型、版本号、可切换的模式 |
| `GET /api/sessions` · `POST /api/sessions` | 列出活跃和已保存的会话 · 新建会话（带 `{"resume": id}` 则继续一个已保存的） |
| `DELETE /api/sessions/{id}` | 关闭会话 |
| `POST /api/sessions/{id}/chat` | 开始一轮（已有一轮在跑时返回 409） |
| `POST /api/sessions/{id}/confirm` | 回答权限询问 |
| `POST /api/sessions/{id}/abort` | 停止当前这一轮 |
| `POST /api/sessions/{id}/mode` | 在 `default`、`acceptEdits`、`dontAsk` 之间切换 |
| `GET /api/bench`、`/api/evolution`、`/api/skills`、`/api/casestudy`、`/api/perf`、`/api/doctor`、`/api/traces` | 只读视图 |

**权限询问。** 运行时本来就留了钩子：循环会 `await` 一个 `confirm_fn(message) -> bool`。CLI 版本从标准输入读。Web 版本发布一个 `confirm_request` 事件，然后 `await` 一个 `asyncio.Future`；`POST …/confirm` 负责 resolve 它。这期间循环就停在这个 Future 上——这正是想要的行为：用户做出决定之前，什么都不执行。

## 长命令：实时输出，以及真的能停下来的“停止”

`run_shell` 通过 `asyncio.create_subprocess_shell` 执行（`tools.py::run_shell_async`）：

- 输出边产生边读取、增量解码，再小批量地作为 `tool_output` 事件发布（每秒最多约 12 批，单条命令最多 40000 个字符）。浏览器里看到的是命令在干活，而不是一个转圈。
- 事件循环不会被阻塞：其他会话、事件流和“停止”按钮都保持响应。其余同步的工具（读文件、搜索）出于同样的原因放到工作线程里执行。
- 超时或被取消时，杀掉的是**整棵进程树**——Windows 上用 `taskkill /T`，POSIX 上杀进程组。只杀 shell 的话，它背后的 `pytest` 或开发服务器会继续跑。
- 返回给模型的字符串格式不变（`Command failed (exit code N)…`），所以模型的行为和之前一致。

`scripts/smoke_live.py` 用真实模型验证了这一点：一条要跑 60 秒的命令在大约 3 秒时被停止，子进程确实消失了。

## 时间花在哪了

循环的每一步都包在 `events.span(name)` 里，它会发布一个带耗时的 `span` 事件：`model_stream`、`tool`、`permission`、`skill_retrieval`、`fold`，以及 `confirm_wait`（等人回答的时间，单独统计，免得被误当成延迟）。

检查器的“耗时”标签页把它们画成每一轮的瀑布图。三条演示轨迹的汇总（`python scripts/perf_report.py`）：

| 步骤 | 次数 | p50 | p95 | 时间占比 |
|---|---:|---:|---:|---:|
| 模型调用 | 20 | 2.15 s | 6.34 s | 94.43% |
| 工具执行 | 14 | 1.51 ms | 1.10 s | 5.56% |
| 权限检查 | 15 | 0.02 ms | 0.28 ms | < 0.01% |
| 技能检索 | 5 | 0.85 ms | 0.90 ms | 0.01% |

运行时自己的检查占一次运行的时间不到 0.01%。样本很小（三次短运行、一个模型、一台机器），它说明的是数量级，不是基准测试。

## 回放

一条轨迹就是按行写成 JSON 的事件流（`agents/web/recorder.py`）。回放发生在**浏览器里**：播放器读入轨迹，把事件按原有节奏喂给消费实时 SSE 流的同一个 reducer（`static/js/store.js`）。控制台的状态只由事件折叠而来，所以回放走的渲染代码和当初那次运行完全相同。

由此带来两点：

- 在线演示页不需要服务端。`scripts/build_pages.py` 把静态文件和 API 本应返回的 JSON 一起导出。
- 演示不会因为断网或没有 API Key 而失败。

**讲解模式**会在值得解释的事件上停下来——第一次权限判定、一次权限询问、一次拒绝、一次循环保护命中——高亮页面上对应的元素并显示一句说明。这些说明是按事件类型匹配的规则（`static/js/guide.js`），不是为某一条录像写的脚本，所以任何轨迹都能讲解，包括你自己的会话。

`scripts/record_demo.py` 在一次性工作区里运行真实任务来录制随仓库发布的轨迹。保存前会改写本机路径（`agents/web/scrub.py`）；如果轨迹里仍然含有用户名、Windows 绝对路径或形似 API Key 的内容，就拒绝保存。

## 前端

`agents/web/static/` 下是原生 HTML、CSS 和 ES 模块——没有框架、没有构建步骤、不依赖 CDN，因此离线可用，并且随 Python 包一起分发（字体也在内：Inter 和 JetBrains Mono，均为 SIL 开源字体许可）。

- `store.js` —— `apply(state, event)`，唯一修改状态的地方。
- `stream.js` —— `LiveStream`（SSE）和 `ReplayPlayer`，输出同一种事件结构。
- `session.js` —— 当前屏幕上是哪个会话或哪条录像；各视图调用的动作。
- `views/console.js` —— 带 key 的渲染：每个对话条目保留自己的 DOM 节点，只有版本号变化时才重建，并且每帧只渲染一次。流式输出一段长回答时，重绘的是一条消息而不是整个对话。
- `toolview.js` —— 每种工具一个渲染器：`run_shell` 是终端，文件修改是 diff，`read_file` 是带高亮的源码。
- `highlight.js` —— 一个小型语法高亮器：每种语言一条正则，从左到右第一个匹配的分支生效，输出时逐段转义。
- `markdown.js` —— 先转义，再只加回自己写出的标签；模型输出不会未经转义进入 `innerHTML`。
- `charts.js` —— SVG/CSS 图表，每张图都有对应的表格视图。
- `palette.js` —— Ctrl+K 命令面板。

## 安全

控制台能让 Agent 执行 shell 命令，而且没有登录。因此：

- **只绑定回环地址**，拒绝其他 `--host`。
- 会改变状态的请求必须是**同源的 JSON**。跨站表单不经过 CORS 预检就发不出 JSON，带有外部 `Origin` 的请求会被拒绝——这是对 CSRF 的防护。
- `Host` 头不是回环名称的请求会被拒绝，以防 DNS 重绑定。
- 控制台**不能选择 `bypassPermissions`**。关掉权限门只能在命令行上决定。

## 已知限制

- **所有会话共用一个工作目录。** 隔离的是对话，不是文件系统。两个会话同时改同一个文件会互相覆盖。
- **会话状态只在一个进程里。** 没有共享存储，所以控制台无法扩展到多个服务进程——这与它的定位一致：一个只绑定回环地址的本地工具。
- **Plan 模式的审批**还没有接入 Web 控制台，请用 CLI。
- 使用不支持流式的网关（`KEELSON_OPENAI_STREAM=0`）时，回答会作为一个事件整体到达，而不是逐 token 到达。工具输出仍然是实时的。
