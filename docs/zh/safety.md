# 安全与撤销

[English](../safety.md) | **中文**

两个承诺，以及它们到底管到哪里。

1. **Keelson 不会闯大祸。** 不会真删任何东西；主目录、盘符根、系统目录碰不得。
2. **它在项目里做的一切都能撤销**，包括它跑的 shell 命令做的事。

## 拦什么、怎么拦

| 风险 | Keelson 的做法 | 代码 |
|---|---|---|
| `rm -rf ~`、`Remove-Item $env:USERPROFILE -Recurse`、`del /s` 等 | shell 里所有删除方式一律拒绝（`bash -c`、`cmd /c`、`ForEach-Object { … }`、`xargs`、`find -delete` 里的也算），并告诉模型改用 `delete_files`。 | `keelson/safety.py` |
| 变量没设置：`rm -rf $DIR/` 变成 `rm -rf /` | 判断路径前，先按 shell 的方式展开变量。展开不了的，整条命令按危险处理：要询问，而且只能允许一次。 | `safety.expand` |
| 移动或删除主目录、盘符根、项目目录本身或它的上级 | 任何模式都拒绝，`--yolo` 也一样。 | `safety.describe_protected` |
| 写系统目录（`C:\Windows`、`Program Files`、`/usr`、`/etc` 等）或 Keelson 的检查点目录 | 任何模式都拒绝。 | 同上 |
| 写项目外的文件 | 询问，并写明路径。只能允许一次。 | `safety.assess_path` |
| `/undo` 撤不回的：装包、联网、`git push`、改写历史 | 询问，并写明原因。可以选本会话允许，或在本项目中一直允许。 | `safety.assess_shell` |
| 强推、`git clean -x`、发布、`sudo`、`curl … \| sh`、会删文件的内联代码 | 询问，而且永远不能“一直允许”。 | 同上 |
| 克隆来的仓库自带 `run_shell(*)` 或 MCP 服务 | 在你信任该目录前，它的 allow 规则和 MCP 服务都不生效。 | `keelson/trust.py` |
| 通过 Keelson 删除 | `delete_files` 把东西移到回收站。移动失败就什么都不删。 | `keelson/platforms/trash.py` |

## 撤销

每一轮开始时，Keelson 把项目快照到它自己的 git 仓库里（`~/.keelson/projects/<哈希>/checkpoints/`）。`/undo` 还原上一轮改过的文件，不管是哪个工具改的；`/redo` 反悔撤销；`/rewind N` 和 `/revert 文件` 能撤得更远或更精确。如果那一轮之后你又改过某个文件，`/undo` 会停下并列出它，不会覆盖你的修改。详见[撤销：检查点](features.md#撤销检查点)。

项目自己的 `.git` 绝不会被读写（独立的 `GIT_DIR`、索引和配置；不跑钩子、不走过滤器、不转换换行）。

## 覆盖不了的

说清楚，免得有人依赖它做不到的事：

- **这不是沙箱。** Keelson 跑的命令（测试、构建脚本、`python script.py`）以你的账户权限运行。Keelson 只能判断它看得到的命令行，看不到程序运行起来之后做了什么。脚本里删文件，拦不住；不过它在*项目内*造成的改动，`/undo` 仍然能还原。
- **撤不回的：** 装好的包、提交和推送、数据库、发到网络上的东西，以及 shell 命令改动的项目外文件。这些正是权限门会先问你的操作。
- **不进快照的：** `.gitignore` 忽略的文件（除非是 Keelson 自己的文件工具改的）、`node_modules`、`.venv` 之类的目录、超过 50 MB 的新文件。用 `delete_files` 删它们照样进回收站。
- **没有 git 时**，只有 Keelson 自己的文件工具能撤销。终端会明确提示，普通 shell 命令也会改为先询问再执行。
- 项目目录如果是主目录或盘符根，太大无法快照；请打开具体的项目目录。
- 命令解析是启发式的。特殊的引号写法或动态生成的命令（`Invoke-Expression`、`eval`）可能藏住真实意图；这类命令会以“无法检查”为由询问你。

## 关掉某些功能

- `KEELSON_CHECKPOINTS=0` 关闭快照。
- `--yolo`（`bypassPermissions`）关掉所有询问，但第一张表里的“拒绝”仍然有效。
- `keelson trust --revoke` 撤销对某个目录的信任。
