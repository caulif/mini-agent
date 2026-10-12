# mini agent

一份手写的 coding agent，附带七章教程。实现参考了 [pi](https://github.com/badlogic/pi-mono) 和 [Codex](https://github.com/openai/codex) 的源码，用来看清循环本身：问模型、执行工具、把结果塞回历史，直到模型不再调用工具。

教程用浏览器打开 [guide.html](guide.html)。

它只使用 Python 标准库。工具能读、写、修改当前工作目录里的文件，也能在这里执行命令。命令黑名单只是教学用的字面匹配，不是沙箱。

## 启动

在要让它工作的目录打开终端：

```powershell
$env:OPENAI_BASE_URL = "https://api.openai.com/v1"
$env:OPENAI_API_KEY = "sk-..."
$env:MODEL_ID = "gpt-4o-mini"
python -m mini_agent
```

`OPENAI_BASE_URL` 可以换成任何兼容 Chat Completions 的地址。不设 `MODEL_ID` 时默认是 `gpt-4o-mini`。

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

代码使用 [MIT 许可证](mini_agent/LICENSE)。教程中的 pi、Codex 摘录另行保留上游许可，见 [引用声明](mini_agent/THIRD_PARTY_NOTICES.md)。
