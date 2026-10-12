# -*- coding: utf-8 -*-
"""
mini_agent —— 手搓一个 coding agent 的最小实现

这个包是按章节**逐步长出来的**。每一章新增一个文件，
旧文件只做小幅扩写，不改已有函数的名字和参数含义。

所以你现在看到的这份代码，就是"全部 7 章都走完之后"的终态。
想跟着章节走，请配合 guide.html 使用 —— 每章会告诉你
"这一章新增/修改了哪个文件"。

────────────────────────────────────────────────────────────
模块地图（按章节顺序）：

    config.py         第 1 章 · 所有配置集中一处
    llm.py            第 1 章 · 与模型通信（唯一的对外出口）
    agent.py          第 1 章 · 主循环（后面几章往里加检查点）
    tools.py          第 2 章 · 工具表 + dispatch + 三个文件工具
    mutation_queue.py 第 3 章 · 同文件写盘串行化
    control.py        第 4 章 · 中断信号与转向队列
    session.py        第 5 章 · 对话落盘与恢复
    compaction.py     第 6 章 · 上下文压缩
    prompt.py         第 7 章 · 系统提示词组装
    cli.py            第 1 章建立、逐章加命令 · 终端交互

────────────────────────────────────────────────────────────
启动：

    python -m mini_agent              # 启动交互式 agent
    python -m mini_agent --help       # 看命令行参数
"""

from .agent import agent_loop, make_executor

__version__ = "0.7.0"  # 7 章，每章一个小版本

__all__ = ["agent_loop", "make_executor", "__version__"]
