# -*- coding: utf-8 -*-
"""
mini_agent · 会话持久化（第 5 章新增）

问题：前三章的 agent 一关掉，对话就没了。哪怕只是 Ctrl+C 出去看一眼
别的文件再回来，也得从头把上下文重新喂一遍。上下文本身就是 agent
最贵的东西，丢掉它等于把之前花的 token 全浪费了。

解决办法：每追加一条消息就往磁盘写一行 JSON（JSONL 格式）。

为什么是 JSONL 而不是一整个 JSON 数组？
  1. 追加便宜 —— 一行一条，直接 append 就行。整个 JSON 数组要
     读出来、解析、push、再序列化、再整份覆盖写。
  2. 崩溃安全 —— 写到第 7 行时断电，前 6 行还是完整可解析的。
     整个 JSON 文件损坏的话，一个字节都不剩。
  3. 能和很长的会话共存 —— 不会因为内存里存不下而失败。

这就是"事件日志"（event log）思路：只追加，不修改，当前状态
由重放日志算出来。
"""

import json
import os
import time
import uuid

from .config import CONFIG


class Session:
    """一段可持久化的对话。

    用法：
        s = Session.create()          # 新建
        s.append("user", "你好")       # 追加消息，立即落盘
        s2 = Session.load(path)        # 从磁盘恢复

    内存里的 messages 和磁盘上的文件保持一致：每次 append 都写盘。
    """

    def __init__(self, session_id, messages, path):
        self.id = session_id
        self.messages = messages      # 直接给 agent_loop 用的那个列表
        self.path = path

    # ── 构造 ──────────────────────────────────────────────────

    @classmethod
    def create(cls, session_id=None, session_dir=None):
        """新建一段会话。session_id 为空时自动生成一个。"""
        session_id = session_id or time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        directory = session_dir or CONFIG["session_dir"]
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, f"{session_id}.jsonl")
        return cls(session_id, [], path)

    @classmethod
    def load(cls, path):
        """从 JSONL 文件恢复。

        这里体现了 JSONL 的第三个好处：**单行坏掉不影响整体**。
        下面的读取逻辑跳过解析失败的行，而不是整个文件报错。
        """
        if not os.path.exists(path):
            raise FileNotFoundError(f"会话文件不存在: {path}")
        messages = []
        skipped = 0
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    messages.append(json.loads(line))
                except json.JSONDecodeError:
                    skipped += 1
        session_id = os.path.splitext(os.path.basename(path))[0]
        if skipped:
            print(f"[警告] 跳过 {skipped} 行损坏数据")
        return cls(session_id, messages, path)

    # ── 追加 ──────────────────────────────────────────────────

    def append(self, role, content):
        """追加一条消息并立即落盘。返回这条消息本身。"""
        msg = {"role": role, "content": content}
        self.messages.append(msg)
        self._write_line(msg)
        return msg

    def append_message(self, msg):
        """追加一条完整消息 dict 到内存，并落盘。返回它。

        给"我们自己新增历史"的场景用（比如 extend 恢复历史）。
        **不要**把它当作 agent_loop 的 on_append 钩子 —— 循环已经
        自己 append 过了，那样会一条消息存两份（踩过这个坑）。
        """
        self.messages.append(msg)
        self._write_line(msg)
        return msg

    def persist(self, msg):
        """只把一条**已经在 self.messages 里**的消息写到磁盘。

        这才是 agent_loop 的 on_append 钩子该用的方法。
        契约：循环负责改内存，我们负责落盘 —— 各管一件事，
        谁也不重复对方的活。
        """
        self._write_line(msg)
        return msg

    def _write_line(self, msg):
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")

    # ── 与 agent 的衔接 ────────────────────────────────────────

    def replace_messages(self, messages):
        """整体替换内存里的历史，并**重写磁盘**。

        这是给第 6 章的压缩用的：压缩会把前 N 条换成 1 条摘要，
        这不是"追加"，是"重写"。所以要有这么一个明确的入口 ——
        别偷偷改 self.messages 而不落盘，那样磁盘和内存就不一致了。
        """
        # 必须**就地**替换，不能写成 self.messages = messages。
        # 因为 agent_loop 和调用方都持有原来那个列表对象；
        # 换成新对象会让它们的后续 append 写进一个"已经没人看"的
        # 列表里 —— 内存与磁盘从此脱钩（踩过这个坑）。
        self.messages[:] = messages
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for msg in messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        os.replace(tmp, self.path)      # 原子替换，避免写一半崩溃

    # ── 清点 ──────────────────────────────────────────────────

    def stats(self):
        return {
            "id": self.id,
            "path": self.path,
            "messages": len(self.messages),
            "bytes": os.path.getsize(self.path) if os.path.exists(self.path) else 0,
        }


def list_sessions(session_dir=None):
    """列出所有会话文件，最近的排前面。"""
    directory = session_dir or CONFIG["session_dir"]
    if not os.path.isdir(directory):
        return []
    files = [
        os.path.join(directory, n)
        for n in os.listdir(directory)
        if n.endswith(".jsonl")
    ]
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return files


def latest_session(session_dir=None):
    """返回最近改动的那个会话文件路径，没有则 None。

    CLI 的 --continue 参数就靠它 —— "接着上次那一段继续"。
    """
    files = list_sessions(session_dir)
    return files[0] if files else None
