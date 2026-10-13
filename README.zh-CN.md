# mini agent

[English](README.md) | 简体中文

一份手写的 coding agent，附带七章教程。实现参考了 [pi](https://github.com/badlogic/pi-mono) 和 [Codex](https://github.com/openai/codex) 的源码，用来看清循环本身：问模型、执行工具、把结果塞回历史，直到模型不再调用工具。

在线阅读 [英文教程](https://caulif.github.io/mini-agent/guide.html)，或切换到 [中文教程](https://caulif.github.io/mini-agent/guide.zh-CN.html)。两种语言包含相同的七章内容和代码摘录。源码注释、文档字符串和 CLI 提示全部使用英文。

离线阅读时，克隆或下载仓库，用浏览器打开 `guide.html`。GitHub 文件页展示的是 HTML 源码，不能直接阅读网页。

它只使用 Python 标准库。工具能读、写、修改当前工作目录里的文件，也能在这里执行命令。命令黑名单只是教学用的字面匹配，不是沙箱。

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

## 许可

原创代码和教程内容使用 [MIT 许可证](LICENSE)。教程中的第三方摘录保留各自的上游许可，见 [引用声明](THIRD_PARTY_NOTICES.md)。
