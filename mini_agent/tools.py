# -*- coding: utf-8 -*-
"""
mini_agent · 工具注册表（S02 建立，S03 加入 edit + 变更队列）

核心思想：**工具名 → 处理函数** 的一张表。

循环里只写一行 `submit(name, input)`，加工具只改这个文件，
不用碰 agent.py。这是整个项目最重要的一处解耦。

每章新增的工具都往下面的 TABLES 段里加，不要改 dispatch 逻辑本身。
"""

import os
import subprocess

from .config import BLOCKED_PATTERNS, CONFIG
from .mutation_queue import mutation_queue

# ═══════════════════════════════════════════════════════════
# 工具定义（发给 LLM 的 schema）
# ═══════════════════════════════════════════════════════════
#
# 这部分会被原样序列化进请求体的 tools 字段。
# 每加一个工具，这里要有对应的声明，否则模型不知道它能用。

def _function(name, description, properties, required):
    """包装一条 OpenAI 函数工具定义。"""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


TOOLS = [
    _function(
        "bash",
        "Run a shell command.",
        {"command": {"type": "string"}},
        ["command"],
    ),
    _function(
        "read",
        "Read a file's text content.",
        {
            "path": {"type": "string"},
            "offset": {"type": "integer", "description": "1-indexed start line"},
            "limit": {"type": "integer", "description": "max lines to read"},
        },
        ["path"],
    ),
    _function(
        "write",
        "Write content to a file (overwrites the whole file).",
        {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        ["path", "content"],
    ),
    _function(
        "edit",
        "Replace old_string with new_string in a file. "
        "old_string must appear exactly once in the file.",
        {
            "path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
        },
        ["path", "old_string", "new_string"],
    ),
]

# ═══════════════════════════════════════════════════════════
# 路径校验
# ═══════════════════════════════════════════════════════════


def safe_path(raw):
    """解析为绝对路径，并确认落在工作目录的子树内。

    为什么需要它：模型可能给出 ../../etc/passwd 这样的路径。
    教学版用 realpath 解析（同时处理符号链接），然后比对前缀。

    :raises ValueError: 路径逃出了工作目录
    """
    workdir = os.path.realpath(os.getcwd())
    target = os.path.realpath(os.path.join(workdir, raw or "."))
    if target != workdir and not target.startswith(workdir + os.sep):
        raise ValueError(f"path outside workdir: {raw}")
    return target


# ═══════════════════════════════════════════════════════════
# 工具实现
# ═══════════════════════════════════════════════════════════
#
# 所有工具函数签名统一： (input: dict) -> str
# 这样才能塞进同一张 dispatch 表。


def tool_bash(inp):
    """执行 shell 命令。三道护栏：黑名单 → 超时 → 输出截断。"""
    command = str(inp.get("command", ""))
    if any(pattern in command for pattern in BLOCKED_PATTERNS):
        return "Error: Dangerous command blocked"

    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=CONFIG["bash_timeout_s"],
        )
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {CONFIG['bash_timeout_s']}s"

    output = f"{proc.stdout or ''}{proc.stderr or ''}".strip()
    if not output:
        return "(no output)"
    return output[: CONFIG["max_output_chars"]]


def tool_read(inp):
    """读文件文本内容，支持 offset/limit 分页。

    文件操作不走 shell —— 没有引号转义、没有 glob 展开，
    路径就是路径，内容就是内容。
    """
    try:
        abs_path = safe_path(str(inp.get("path", "")))
    except ValueError as e:
        return f"Error: {e}"

    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError as e:
        return f"Error: {e}"

    offset = int(inp.get("offset") or 1)  # 1-indexed
    limit = int(inp.get("limit") or CONFIG["max_read_lines"])
    start = max(0, offset - 1)
    selected = lines[start : start + limit]

    # 带上行号，方便模型引用具体位置
    numbered = "".join(
        f"{start + i + 1:>6}\t{line}" for i, line in enumerate(selected)
    )
    remaining = len(lines) - (start + len(selected))
    if remaining > 0:
        numbered += f"\n... ({remaining} more lines)"
    return numbered or "(empty file)"


def tool_write(inp):
    """写文件（整文件覆盖）。

    S03 起，整个写盘动作进变更队列 —— 和 edit 共用一条队列，
    这样两个操作改同一个文件时不会交错。
    """
    path = str(inp.get("path", ""))
    content = str(inp.get("content", ""))
    try:
        abs_path = safe_path(path)
    except ValueError as e:
        return f"Error: {e}"

    def mutate():
        try:
            os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
            with open(abs_path, "w", encoding="utf-8", newline="") as f:
                f.write(content)
        except OSError as e:
            return f"Error: {e}"
        return f"Wrote {len(content)} bytes to {path}"

    return mutation_queue.run(abs_path, mutate)


def tool_edit(inp):
    """定点替换文件里的一小段内容（S03 新增）。

    和 write 的区别：write 是整文件覆盖，edit 是"手术"。
    整文件重写既浪费 token，又容易在无关的地方引入笔误。

    关键：替换前必须确认 old_string 在文件里**唯一**，
    否则你不知道改的是哪一处。
    """
    path = str(inp.get("path", ""))
    old_string = str(inp.get("old_string", ""))
    new_string = str(inp.get("new_string", ""))

    if not old_string:
        return "Error: old_string cannot be empty"

    try:
        abs_path = safe_path(path)
    except ValueError as e:
        return f"Error: {e}"

    def mutate():
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError as e:
            return f"Error: {e}"

        count = content.count(old_string)
        if count == 0:
            return f"Error: old_string not found in {path}"
        if count > 1:
            return (
                f"Error: old_string appears {count} times in {path}, must be unique. "
                "Include more surrounding context to make it unique."
            )

        try:
            with open(abs_path, "w", encoding="utf-8", newline="") as f:
                f.write(content.replace(old_string, new_string, 1))
        except OSError as e:
            return f"Error: {e}"
        return f"Edited {path}"

    # 整个"读 → 查重 → 写"序列作为一个原子单元排队
    return mutation_queue.run(abs_path, mutate)


# ═══════════════════════════════════════════════════════════
# dispatch 表
# ═══════════════════════════════════════════════════════════
#
# 加工具 = 在 TABLES 里加一项。循环一行都不用改。

HANDLERS = {
    "bash": tool_bash,
    "read": tool_read,
    "write": tool_write,
    "edit": tool_edit,
}


def submit(name, inp):
    """执行一个工具调用，返回结果文本。

    这是循环唯一需要的入口。未知工具名不抛异常，
    而是把错误作为工具结果返回给模型 —— **让模型自己纠正**。
    """
    handler = HANDLERS.get(name)
    if handler is None:
        known = ", ".join(sorted(HANDLERS))
        return f"Error: unknown tool '{name}'. Available tools: {known}"
    try:
        return handler(inp)
    except Exception as e:  # 工具内部意外异常也不能让 agent 崩掉
        return f"Error: {type(e).__name__}: {e}"
