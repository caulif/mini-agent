# -*- coding: utf-8 -*-
"""
mini_agent · 运行控制（第 4 章新增）

这个文件解决一个问题：**agent 跑起来之后，你怎么插手？**

前面三章的循环是"一跑到底"的 —— 你按下回车，它就一路跑到模型
不再调用工具为止。这在实际使用中不够用，你至少需要三种插手方式：

  1. 中断（abort）  —— "停！别跑了"            → Signal
  2. 转向（steering）—— "顺便，你改成做 X"      → SteeringQueue
  3. 轮数上限       —— "最多跑 N 轮，防止死循环" → 循环里的计数

放在独立文件里，是因为 llm.py（发请求前检查）和 agent.py（每轮检查）
都要用，而它们互相不能 import（会循环依赖）。
control.py 不依赖任何本项目模块，所以谁都能安全地用它。
"""

import threading


class Aborted(Exception):
    """中断异常。

    为什么用异常而不是返回值？因为中断可能发生在很深的地方
    （比如正在发 HTTP 请求、正在等工具执行完）。一层层往上 return
    会污染所有函数的签名；抛异常则任何一个检查点都能立刻跳出。
    """

    def __init__(self, reason="aborted"):
        super().__init__(reason)
        self.reason = reason


class Signal:
    """中断信号。

    为什么不用一个全局布尔变量？因为 agent 可能在子线程里跑
    （后面并发执行工具时就是），需要线程安全。threading.Event
    内部有锁，且跨线程可见，是标准做法。
    """

    def __init__(self):
        self._event = threading.Event()
        self.reason = None

    def abort(self, reason="user aborted"):
        """请求中断。可以被多次调用，第一次的 reason 生效。"""
        if not self._event.is_set():
            self.reason = reason
        self._event.set()

    @property
    def aborted(self):
        """是否已请求中断。不想抛异常时用它做判断。"""
        return self._event.is_set()

    def check(self):
        """检查点：已中断则抛 Aborted。想"就地退出"时用它。"""
        if self._event.is_set():
            raise Aborted(self.reason)

    def reset(self):
        """清除中断状态，让同一个 Signal 对象可以复用（REPL 里需要）。"""
        self._event.clear()
        self.reason = None


class SteeringQueue:
    """转向消息队列。

    使用场景：agent 正在跑（可能几十秒），你发现它方向错了，
    直接在终端再打一句话。这句话不能硬塞进当前这次请求 ——
    请求已经发出去了。所以要存起来，等下一轮开始时插进对话历史。

    为什么是队列不是单个变量？因为你在它跑的这段时间里
    可能连打两句，两句都要生效，且要保持先后顺序。
    """

    def __init__(self):
        self._items = []
        self._lock = threading.Lock()

    def push(self, text):
        """塞一条消息（主线程调用）。"""
        text = (text or "").strip()
        if not text:
            return
        with self._lock:
            self._items.append(text)

    def drain(self):
        """取走全部消息并清空（agent 线程调用）。

        必须是"取走并清空"这个原子动作：如果先 len() 再逐个 pop，
        中间主线程又 push 了一条，就可能漏掉或重复。
        """
        with self._lock:
            items, self._items = self._items, []
        return items
