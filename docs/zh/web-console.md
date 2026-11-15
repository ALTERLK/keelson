# Web 控制台

[English](../web-console.md) | **中文**

`keelson web` 为 CLI 所用的同一个运行时打开一个浏览器控制台。它不增加任何 Agent 行为，只是另一种**观察**运行过程、并回答权限询问的方式。

```bash
pip install -e ".[web]"
keelson web              # 配置了 API Key 时为实时模式
keelson web --replay     # 只回放录制的轨迹，不调用模型
```

## 核心思路：一条事件流，多个消费者

```text
Agent 循环 ──emit──▶ EventBus ──┬─▶ 终端渲染器    agents/ui.py
            agents/events.py   ├─▶ SSE 广播      agents/web/server.py ──▶ 浏览器
                               └─▶ 轨迹录制器    agents/web/recorder.py ──▶ *.jsonl
```

改造之前，循环里直接调用 `print_*` 函数往终端画，“发生了什么”和“怎么显示”是同一段代码。现在 `ui.py` 里的 `print_*` 只负责发布事件，终端渲染器只是订阅者之一。循环本身只改了几处：补上了权限门的判定结果、循环保护命中、上下文折叠和 token 用量这几个信号——这些以前终端要么靠推断，要么根本不显示。

事件总线（`agents/events.py`，约 130 行）的几个性质：

- **同步、有序分发。** 订阅者看到的顺序与循环产生的顺序完全一致，终端的流式输出依赖这一点。
- **订阅者隔离。** 某个订阅者抛异常只会被记录并吞掉。关掉一个浏览器标签页，不应该让 Agent 停下来。
- **有界历史。** 保留最近 5000 条事件及其序号，晚连接或断线重连的消费者可以补齐。

## 服务端 → 浏览器：Server-Sent Events

`GET /api/events` 是一个长连接响应，每个事件一帧：

```text
id: 42
data: {"seq": 42, "ts": 1731640000.1, "type": "tool_call", "data": {...}}
```

为什么用 SSE 而不是 WebSocket：数据是单向的（浏览器自己的操作很少，用普通 POST 就够），SSE 就是普通 HTTP、不需要协议升级，而且 `EventSource` 会自动重连并带上 `Last-Event-ID`，服务端可以精确地从断点续传。

续传要做对，有一个细节：处理函数**先订阅，再读积压**。两步之间发布的事件会同时出现在两边，再用序号去重即可。如果反过来先读积压，中间就有一个会丢事件的窗口。

## 浏览器 → 服务端

| 接口 | 作用 |
|---|---|
| `POST /api/chat` | 开始一轮（已有一轮在跑时返回 409） |
| `POST /api/confirm` | 回答权限询问 |
| `POST /api/abort` | 停止当前这一轮 |
| `POST /api/reset` | 新会话 |
| `POST /api/mode` | 在 `default`、`acceptEdits`、`dontAsk` 之间切换 |
| `GET /api/state`、`/api/bench`、`/api/evolution`、`/api/skills`、`/api/traces` | 只读视图 |

**权限询问。** 运行时本来就留了钩子：循环会 `await` 一个 `confirm_fn(message) -> bool`。CLI 版本从标准输入读。Web 版本发布一个 `confirm_request` 事件，然后 `await` 一个 `asyncio.Future`；`POST /api/confirm` 负责 resolve 它。这期间循环就停在这个 Future 上——这正是想要的行为：用户做出决定之前，什么都不执行。

## 回放

一条轨迹就是按行写成 JSON 的事件流（`agents/web/recorder.py`）。回放发生在**浏览器里**：播放器读入轨迹，把事件按原有节奏喂给消费实时 SSE 流的同一个 reducer（`static/js/store.js`）。控制台的状态只由事件折叠而来，所以回放走的渲染代码和当初那次运行完全相同。

由此带来两点：

- 在线演示页不需要服务端。`scripts/build_pages.py` 把静态文件和 API 本应返回的 JSON 一起导出。
- 演示不会因为断网或没有 API Key 而失败。

`scripts/record_demo.py` 在一次性工作区里运行真实任务来录制随仓库发布的轨迹，并在保存前改写本机路径。

## 前端

`agents/web/static/` 下是原生 HTML、CSS 和 ES 模块——没有框架、没有构建步骤、不依赖 CDN，因此离线可用，并且随 Python 包一起分发。

- `store.js` —— `apply(state, event)`，唯一修改状态的地方。
- `stream.js` —— `LiveStream`（SSE）和 `ReplayPlayer`，输出同一种事件结构。
- `views/console.js` —— 带 key 的渲染：每个对话条目保留自己的 DOM 节点，只有版本号变化时才重建，并且每帧只渲染一次。流式输出一段长回答时，重绘的是一条消息而不是整个对话。
- `markdown.js` —— 先转义，再只加回自己写出的标签；模型输出不会未经转义进入 `innerHTML`。
- `charts.js` —— SVG/CSS 图表，每张图都有对应的表格视图。

## 安全

控制台能让 Agent 执行 shell 命令，而且没有登录。因此：

- **只绑定回环地址**，拒绝其他 `--host`。
- 会改变状态的请求必须是**同源的 JSON**。跨站表单不经过 CORS 预检就发不出 JSON，带有外部 `Origin` 的请求会被拒绝——这是对 CSRF 的防护。
- `Host` 头不是回环名称的请求会被拒绝，以防 DNS 重绑定。
- 控制台**不能选择 `bypassPermissions`**。关掉权限门只能在命令行上决定。

## 已知限制

- **每个进程一个会话。** 总线是进程级全局的。要同时托管多个会话，需要每个会话一条总线（例如用 `contextvars`）。
- **阻塞的工具会卡住事件流。** `run_shell` 用的是阻塞式子进程调用，长命令运行期间不会有事件流出。改成线程或异步子进程可以解决。
- **Plan 模式的审批**还没有接入 Web 控制台，请用 CLI。
- 使用不支持流式的网关（`KEELSON_OPENAI_STREAM=0`）时，回答会作为一个事件整体到达，而不是逐 token 到达。
