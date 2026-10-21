# mini agent

[English](README.md) | 简体中文

一份手写的 coding agent，附带 mini agent-1（第1—7章）和 mini agent-2（第8—13章）两组双语实现指南。实现参考了 [pi](https://github.com/badlogic/pi-mono) 和 [Codex](https://github.com/openai/codex) 的源码，用来看清循环本身：问模型、执行工具、把结果塞回历史，直到模型不再调用工具。

在线阅读 [英文教程](https://caulif.github.io/mini-agent/guide.html)，或切换到 [中文教程](https://caulif.github.io/mini-agent/guide.zh-CN.html)。两种语言对应同一份实现。后续内容见 [mini agent-2](https://caulif.github.io/mini-agent/advanced-guide.zh-CN.html)：重试、恢复、流式、权限与离线评估。CLI 提示使用英文；Python 源码不保留注释和文档字符串。

离线阅读时，克隆或下载仓库，用浏览器打开 `guide.html`。GitHub 文件页展示的是 HTML 源码，不能直接阅读网页。

运行只使用 Python 标准库。原 CLI 保留文件与 shell 工具；mini agent-2 使用独立执行日志、串行文件工具和显式写权限，不提供 shell。两者都不是操作系统沙箱。

## 启动

使用 Python 3.10 或更高版本。先克隆仓库并进入目录：

```sh
git clone https://github.com/caulif/mini-agent.git
cd mini-agent
```

Windows PowerShell：

```powershell
$env:OPENAI_BASE_URL = "https://api.openai.com/v1"
$env:OPENAI_API_KEY = "sk-..."
$env:MODEL_ID = "gpt-4o-mini"
python -m mini_agent
```

macOS / Linux（Bash）：

```sh
export OPENAI_BASE_URL="https://api.openai.com/v1"
export OPENAI_API_KEY="sk-..."
export MODEL_ID="gpt-4o-mini"
python3 -m mini_agent
```

`OPENAI_BASE_URL` 可以换成任何兼容 Chat Completions 的地址。不设 `MODEL_ID` 时默认是 `gpt-4o-mini`。

CLI 固定使用英文，仅教程提供中英文切换。模型回复跟随用户使用的语言，文件内容和命令输出保持原文。

如需从其他工作目录启动，先把仓库绝对路径加入 `PYTHONPATH`，再进入目标目录运行。文件工具和会话均以当前工作目录为准。

```powershell
python -m mini_agent --help
python -m mini_agent -c                  # 继续最近一次会话
python -m mini_agent --resume <id>       # 恢复指定会话，可只写前缀
python -m mini_agent -v --max-turns 8    # 显示工具输出，并限制每轮最多问模型几次
python -m mini_agent -p "列出当前目录的 py 文件"
```

进入对话后直接输入任务。运行中再输入的文字会在下一轮插入；`Ctrl+C` 中断当前这一轮。停在提示符时，`Ctrl+C` 或 `/exit` 退出。

```text
/help    /new     /list    /resume <id>
/skills  /compact /stats   /exit
```

会话写在当前目录的 `.mini_agent/sessions/`。当前目录下的 `skills/*.md` 会在启动时拼进系统提示词，改完后需要重新启动。

## mini agent-2

原 CLI 和 Session 格式继续保留。第8—13章通过独立入口运行：

```sh
python -m mini_agent.advanced "读取 README.md" --root .
python -m mini_agent.advanced "创建 hello.txt，内容为 hello" --allow-write --stream
python -m mini_agent.advanced --resume <日志路径> --status
python -m mini_agent.advanced --resume <日志路径>
```

新日志位于任务根目录 `.mini_agent/advanced/`，不能读取原 `.mini_agent/sessions/` 文件。恢复沿用日志中的根目录；写权限需在每次启动时显式传入 `--allow-write`。`--status` 不需要 API key，也不请求模型。工具结果未知时，恢复会先阻断；核对现场后才能传入 `--acknowledge-unknown` 继续，旧工具不会自动重跑。

只有 HTTP 429/502/503/504 自动重试，首次请求后最多重试两次；`MINI_AGENT_REQUEST_MAX_RETRIES=0` 关闭重试。超时、断连保留为未知，流式请求不自动重试。工具只在响应完整结束并通过校验后执行。退出码为正常完成0、轮数上限2、失败1、中断130；正常结束不等于任务产物正确。

## 本地检查

```sh
python -B -m mini_agent.selfcheck
python -B -m mini_agent.retry_selfcheck
python -B -m mini_agent.advanced_selfcheck
python -B -m mini_agent.evaluation --repeats 3
python -B scripts/check_guides.py
```

检查使用临时目录、子进程和本机 HTTP/SSE 服务，不调用模型。脚本评估检查控制流与文件产物，不衡量模型正确率。运行代码只用标准库；重建双语网页 `python -B scripts/build_guides.py` 另需 Pygments，页面源在 `docs/guide_source/`。

原版保留在 `v1` 分支，`main` 包含两组实现入口和双语指南。

## 许可

原创代码和教程内容使用 [MIT 许可证](LICENSE)。教程中的第三方摘录保留各自的上游许可，见 [引用声明](THIRD_PARTY_NOTICES.md)。
