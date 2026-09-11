# Keelson

> Keelson（内龙骨）：船底把龙骨和肋骨锁死的那根梁。模型是帆，Keelson 是把帆的力传导到整条船骨架上的结构件。

一个**自研的自进化 Agent Harness**（Python，约 8k 行，无框架依赖）。模型只负责推理和提出工具调用意图；真正的环境操作由 Runtime 统一做**权限判断、工具执行、结果回写、上下文折叠和经验沉淀**。

不用 LangGraph / AutoGen —— 项目的价值就是那个循环本身（见 [`docs/design-tradeoffs.md`](docs/design-tradeoffs.md)）。

## 证据,不是设计意图

这个项目最容易被问穿的地方是"怎么证明有用"。三条证据链,数字和产物都在仓库里,不是口头描述:

| | 做了什么 | 结果 |
|---|---|---|
| **[ToolHop 基准](bench/REPORT.md)** | 自建四臂对照（裸 API / 裸 harness / 手写 Skill / 自进化 Skill）；n=60，配对 bootstrap 95% CI | 自己的基准跑出了自己 harness 的真实 bug（一处提示词让小模型陷入工具调用死循环），[定位](bench/spiral-diagnosis.md)、修复、重测：准确率 72.8%→79.4%，撞轮次上限 8.3%→0.0%，置信区间排除 0 |
| **[Sandbox 编码基准](bench/REPORT.md#sandbox-coding-benchmark)** | 14 个真实编码任务（bugfix/feature/重构/只给报错栈/两个诱导作弊的陷阱题），两个模型档位各跑一遍 | 全部 56 次运行 0% 测试篡改、0% 越界修改；抓到一次真实的"编辑完不验证就交"失败案例，多跑几次确认不是偶然 |
| **[自进化实测](docs/self-evolution-demo.md)** | 真实跑两条 Skill 演化链路 | 一次真实 **通过**（门禁核验后写入生效）、一次真实**隔离**（门禁拦截、live 文件回滚、留痕 Evolution Card）——不是单元测试模拟的，是真实链路里跑出来的 |

`docs/design-tradeoffs.md` 里还有一张"已知问题"清单——包括自己发现、暂未修的两个真实缺陷。诚实比看起来完美更有说服力。

## 里面有什么

**Runtime（`agents/`）**

- **Agent Loop** —— 流式响应、tool call 解析、权限门、工具执行、tool result 回写；OpenAI / Anthropic 双协议
- **权限门（`tools.py::check_permission`）** —— 5 种模式 + `settings.json` allow/deny 规则 + 危险命令正则；子 Agent 不超过父级权限；MCP / 未分类工具默认拦截
- **上下文折叠（`session_memory.py`）** —— 长任务把历史重写为 episodic / working / tool 三段结构化记忆；三层压缩（预算截断 → 陈旧结果剪除 → 空闲期微压缩）
- **循环守卫** —— 检测到模型在小窗口内反复调用同一工具且结果不再收敛时拦截并提示收尾，按工具名开窗口（不是按参数——观察到的失效模式是参数模糊化，不是完全重复）
- **长期 Memory** —— 按项目 hash 隔离
- **Skills** —— 项目级 / 用户级 `SKILL.md`，BM25 检索，inline / fork 执行
- **Skills 自进化** —— 从用户下一轮反馈中抽取可复用规则，`add / merge / discard`，非劣性门禁把关
- **MCP** —— 自研 stdio JSON-RPC client，外部工具包装为 `mcp__server__tool`
- **子 Agent** —— `explore` / `plan` / `general` + 自定义

**证据层（`agents/bench/`）**

- **ToolHop 离线 benchmark（`runner.py`）** —— 每题自带可执行工具函数体 + 标准答案，判分确定可复现；四臂对照，训练/测试分离，配对 bootstrap 95% CI + McNemar
- **非劣性晋级门禁（`skill_gate.py`）** —— 候选 Skill 必须在自身 replay 历史上不劣于旧版才允许写入生效，否则进隔离区并回滚；隔离事件和通过事件一样留痕
- **可回滚 + Evolution Card** —— `/skill-diff` `/skill-history` `/skill-rollback`；每次 add/merge/quarantine 产出一张审计卡（证据 → 候选 → 决策 → diff → 门禁 → 结果）
- **Sandbox 编码基准（`sandbox_runner.py`）** —— 真实 Agent 接入自建评分器，14 题、双模型
- **成本 gating（`skill_gating.py`）** —— 零模型前置分类器，只有反馈型输入才进入昂贵的抽取链路
- **性能 trace（`trace.py`）** + **折叠保真度探针（`fold_fidelity.py`）**（这两项机制已建好，实测数据待补,见 [`PROGRESS.md`](PROGRESS.md)）

当前进度见 [`PROGRESS.md`](PROGRESS.md)；183 个测试全部无需 API key（模型调用全 mock）。

## 项目架构

![Keelson 总体架构](wiki/assets/architecture/01-overall-architecture.svg)

核心运行链路：

```text
用户输入
  -> agents/main.py
  -> Agent.chat()
  -> 构建 Prompt / 检索 Skills / 预取 Memory / 初始化 MCP
  -> 调用 OpenAI-compatible 或 Anthropic-compatible 模型
  -> 模型返回文本或 tool call
  -> Runtime 做权限检查 + 循环守卫检查
  -> 执行工具 / Skill / MCP / 子 Agent
  -> tool result 回写模型
  -> 保存 Session
  -> 后台执行 Skill usage tracking 和 online skill evolution
```

## 目录结构

```text
keelson/
├── agents/
│   ├── main.py                    # CLI 入口、REPL、参数解析
│   ├── agent.py                   # Agent Runtime、模型调用、工具调度、上下文压缩、循环守卫
│   ├── tools.py                   # 内置工具和权限系统
│   ├── paths.py                   # 运行时路径解析（$KEELSON_HOME、项目级 .keelson/）
│   ├── prompt.py                  # System prompt 动态构建
│   ├── skills.py                  # Skills 加载、检索、执行、创建和演化封装
│   ├── online_skill_evolution.py  # 在线 Skill 抽取和 add/merge/discard 决策
│   ├── skill_evolution.py         # Skill 落盘、版本快照、审计统计
│   ├── skill_gate.py              # 非劣性晋级门禁
│   ├── skill_history.py           # diff / rollback
│   ├── evolution_card.py          # 审计卡渲染
│   ├── memory.py                  # 长期记忆系统
│   ├── mcp_client.py              # MCP stdio JSON-RPC 客户端
│   ├── subagent.py                # 子 Agent 配置
│   ├── session.py                 # 会话保存与恢复
│   ├── trace.py                   # 耗时 span 打点
│   ├── ui.py                      # 终端 UI 输出
│   └── bench/                     # ToolHop / sandbox 基准、评测统计
├── tests/                         # 183 个测试，无需 API key
├── bench/                         # 基准任务、split、REPORT.md、run 产物
├── docs/                          # 设计取舍、自进化实测记录
├── wiki/                          # 架构图与详细说明文档
├── scripts/                       # 数据拉取、报告生成、自进化演示
├── Dockerfile
├── pyproject.toml
└── README.md
```

## 快速启动

### 1. 准备环境

推荐环境：

- Python 3.11+
- macOS / Linux / Windows
- Git
- ripgrep，可选但推荐
- 一个 OpenAI-compatible 或 Anthropic-compatible 模型接口

安装：

```bash
python3 -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -e ".[dev,bench]"   # 装好 keelson 命令行入口 + 测试/评测依赖
pytest -q                       # 应该 183 passed，无需配置任何 API key
```

### 2. 配置 `.env`

复制 `.env.example` 为 `.env`，项目会自动读取当前目录或父目录中的 `.env`。

Anthropic-compatible 示例：

```env
APIKEY=sk-your-api-key
API=https://your-host/anthropic
MODEL=claude-sonnet-4-6
```

OpenAI-compatible 示例：

```env
OPENAI_API_KEY=sk-your-api-key
OPENAI_BASE_URL=https://your-host/v1
MODEL=gpt-4o
```

协议判断规则：

- `API` 或 `--api-base` 路径包含 `/anthropic` 时，按 Anthropic-compatible 调用。
- 否则有 OpenAI base URL 时，按 OpenAI-compatible 调用。
- `--model` 会覆盖 `.env` 中的 `MODEL`。

### 3. 启动 REPL

```bash
python3 -m agents.main
```

启动后直接输入任务，例如：

```text
阅读这个项目，告诉我 agent loop 是怎么跑起来的
```

### 4. 执行一次性任务

```bash
python3 -m agents.main "总结这个项目的目录结构和核心模块"
```

### 5. 使用 Plan Mode

Plan Mode 适合重构、复杂修复和多文件修改。它会先只读分析和写计划，用户审批后再执行。

```bash
python3 -m agents.main --plan "分析 Skills 检索逻辑应该如何优化"
```

REPL 中也可以输入 `/plan`。

### 6. 恢复最近会话

```bash
python3 -m agents.main --resume
```

## Skills 自进化

Keelson 的核心特色是 **Skills 自进化**。它可以从用户明确反馈中抽取未来可复用的规则，自动新增或合并到项目级或用户级 `SKILL.md`，并经过非劣性门禁才生效——真实跑出来的一次通过、一次隔离见 [`docs/self-evolution-demo.md`](docs/self-evolution-demo.md)。

### 1. 开启自动自进化

默认 `KEELSON_AUTO_SKILL_EVOLUTION` 是开启的。为了明确配置，建议在 `.env` 中写：

```env
KEELSON_AUTO_SKILL_EVOLUTION=1
KEELSON_AUTO_SKILL_TARGET=project
```

- `KEELSON_AUTO_SKILL_EVOLUTION=1`：启用在线 Skill 自进化
- `KEELSON_AUTO_SKILL_TARGET=project`：自动新增的 Skill 写入当前项目 `.keelson/skills/`（改成 `user` 则写入 `~/.keelson/skills/`，跨项目共享）

### 2. 用允许写入的权限模式启动

后台自动写入 Skill 需要当前权限模式允许写文件：

```bash
python3 -m agents.main --accept-edits
```

### 3. 给出可复用反馈

自进化不是把每一句话都写成 Skill，只适合沉淀稳定、明确、未来同类任务仍适用的规则——判定逻辑见 `agents/skill_gating.py::looks_like_feedback`。

不适合沉淀（一次性任务）：

```text
帮我写一篇 500 字政府报告。
```

适合沉淀（面向未来的规则）：

```text
以后写政府报告、工作汇报、调研材料时，默认先给可直接使用的初稿，不要连续追问；
结构按"标题、背景、主要情况、问题分析、工作举措、下一步计划"组织，语言要正式克制。
```

### 4. 自进化链路如何工作

```text
第 N 轮用户任务
  -> Agent 输出结果
  -> 保存 pending extraction window
第 N+1 轮用户反馈
  -> 合并进上一轮 window
  -> online_ingest()
  -> Extractor 抽取候选 Skill（只认用户反馈轮，不认模型自我评估）
  -> Maintainer 判断 add / merge / discard
  -> merge 走非劣性门禁：候选在该 Skill 的历史回放样本上不能变差
  -> 门禁通过 -> 写入 SKILL.md + Evolution Card
  -> 门禁拒绝 -> 隔离候选、live 文件回滚、同样写 Evolution Card（标 REJECTED）
```

核心文件：`agents/agent.py`、`agents/online_skill_evolution.py`、`agents/skill_gate.py`、`agents/skills.py`、`agents/skill_evolution.py`。真实跑一遍：`python scripts/demo_self_evolution.py`（需要 API key；`--dry-run` 不需要）。

## 评测

```bash
# ToolHop 四臂（每臂独立隔离，互不串味）
python -m agents.bench.runner --arm raw_api   --n 60
python -m agents.bench.runner --arm no_skill  --n 60
python -m agents.bench.runner --arm skill_v1  --n 60
python -m agents.bench.runner --arm no_skill --dry-run    # 只构建不调模型

# 用已有 run 产物重新生成报告表格
python scripts/bench_report.py

# Sandbox 编码基准（无需 API key，先验证任务本身有效）
python -m agents.bench.sandbox_runner --validate
python -m agents.bench.sandbox_runner --arm harness --model <model-name>
```

- ToolHop 数据集：`bench/toolhop_subset.json`（120 题固定子集，够复现 `bench/REPORT.md` 的全部数字）；完整集用 `python scripts/fetch_data.py toolhop` 拉取
- `pytest -q` 全绿（183 项，无需 API key，模型调用全 mock）

## 更多文档

- [ToolHop + Sandbox 完整评测报告](bench/REPORT.md)
- [自进化实测：一次真实通过 + 一次真实隔离](docs/self-evolution-demo.md)
- [设计取舍与已知问题](docs/design-tradeoffs.md)
- [从0到1学习了解项目](wiki/从0到1学习了解项目.md)
- [架构设计](wiki/架构设计.md)
- [核心源码阅读指南](wiki/核心源码阅读指南.md)
- [技术亮点](wiki/技术亮点.md)
- [Skills自进化逻辑与实现思路](wiki/Skills自进化逻辑与实现思路.md)
