# -*- coding: utf-8 -*-
"""
mini_agent · 文件变更队列（S03 新增）

解决什么问题：如果 agent 一轮里发两个 edit 改同一个文件，
"读-改-写"不是原子操作，两次写盘会交错，导致丢更新：

    edit#1: 读到 "A"  ────────────┐
    edit#2: 读到 "A"        ─────┼── 两者都读到 A
    edit#1: 写入 "A1"       ─────┘
    edit#2: 写入 "A2"                  ← 覆盖了 edit#1

解决办法：所有写盘操作排队，同一个文件的"读-改-写"整体串行。

设计要点（三点，缺一不可）：
  1. **按路径分桶** —— 同文件串行，不同文件仍可并行。
     如果只用一条全局队列，无关文件的写盘也被串起来，白白变慢。
  2. **key 用 realpath 归一化** —— 否则 ./a.txt、a.txt、符号链接
     会被当成三个不同的 key，队列就失效了。
  3. **算 key 这一步本身也要串行** —— 否则两个并发调用可能同时
     拿到"还没建立的锁"，各自建一把，等于没有队列。
"""

import os
import threading


class FileMutationQueue:
    """按「规范化后的绝对路径」串行化文件变更。"""

    def __init__(self):
        # key(规范路径) -> Lock
        self._locks = {}
        # 保护 _locks 本身。对应要点 3。
        self._registry = threading.Lock()

    @staticmethod
    def _key_for(path):
        """算出分桶用的 key。对应要点 2。

        用 realpath 归一化。文件还不存在时（比如 write 新建文件），
        realpath 仍会给出规范化后的路径。
        """
        return os.path.realpath(os.path.abspath(path))

    def run(self, path, fn):
        """把一个变更操作排进队列，返回 fn 的结果。

        :param path: 目标文件路径（用来算分桶 key）
        :param fn:   无参函数，实际执行变更并返回结果文本
        """
        key = self._key_for(path)

        # 要点 3：取锁这一步本身要串行，避免并发时各建一把锁。
        with self._registry:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock

        # 要点 1：按 key 串行；不同 key 互不影响。
        with lock:
            return fn()


# 模块级单例：tools.py 里的 write/edit 都用这一个实例
mutation_queue = FileMutationQueue()
