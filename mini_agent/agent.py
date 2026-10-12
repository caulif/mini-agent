# -*- coding: utf-8 -*-
"""
mini_agent · agent 主循环（第 1 章建立，第 3、4、5、6 章扩写）

这是整个项目的心脏。前面所有文件都是为它服务的：
  config.py 提供配置，llm.py 负责和模型说话，tools.py 负责干活。

循环本身只有四步，反复执行：

    问模型 → 它说要调工具 → 执行工具 → 把结果塞回历史 → 再问模型

直到模型不再调工具（或者你中断、或者到了轮数上限）。

────────────────────────────────────────────────────────────
各章在这份代码里留下的痕迹（读代码时对照着看）：

  第 1 章：step 5→8 的骨架。串行执行工具，没有中断，没有落盘。
  第 3 章：execute_tool_calls 改成线程池并发。
          —— 这正是为什么需要 mutation_queue：工具跑得越并发，
             两个 edit 撞同一个文件的可能性就越大
  第 4 章：step 1/2/3/7 四个中断检查点 + 转向消息 + 轮数上限
  第 5 章：_append() + on_append 钩子（每条消息落盘）
  第 6 章：before_request 钩子（发送前压缩）+ make_compaction_hook()

注意后面几章**没有推翻**前面任何代码：主循环的骨架、函数名、
参数含义全都保留。加能力的方式只有两种 ——
在新位置插一个检查点，或者插一个可选钩子。
────────────────────────────────────────────────────────────
"""

from concurrent.futures import ThreadPoolExecutor
import json

from . import llm

from . import tools as toolbox
from .control import Aborted, Signal

# 一次最多并发跑几个工具。定成常数便于以后调。
MAX_PARALLEL_TOOLS = 8


# ══════════════════════════════════════════════════════════════
# 事件回调
# ══════════════════════════════════════════════════════════════
#
# 循环"发生了什么"不直接 print，而是通过 emit(kind, **kwargs) 报出去。
# 好处：同一份循环，CLI 可以打成彩色文字，测试可以只收集事件做断言，
# 以后接 TUI 也不用改循环。这叫"把副作用从逻辑里抽出来"。

def default_emit(kind, **kw):
    """默认事件打印。CLI 会换成更好看的版本。"""
    if kind == "text":
        print(kw["text"])
    elif kind == "tool_start":
        print(f"  → {kw['name']}({_brief(kw.get('input'))})")
    elif kind == "tool_end":
        pass  # 结果太长，默认不打印，由 CLI 决定怎么显示
    elif kind == "aborted":
        print(f"[已中断] {kw.get('reason')}")
    elif kind == "max_turns":
        print(f"[达到轮数上限 {kw['limit']}]")


def _brief(inp, limit=70):
    """把工具入参压成一行短摘要，便于看日志。"""
    if not isinstance(inp, dict):
        return repr(inp)[:limit]
    parts = []
    for k, v in inp.items():
        s = str(v).replace("\n", "\\n")
        if len(s) > 30:
            s = s[:30] + "…"
        parts.append(f"{k}={s}")
    return ", ".join(parts)[:limit]


# ══════════════════════════════════════════════════════════════
# 工具执行
# ══════════════════════════════════════════════════════════════
#
# 第 1 章这里是：
#     return [_run_one(c, signal, emit) for c in calls]
# 一个 for 循环就完事了，简单直接。
#
# 第 3 章改成并发：模型一轮里要读 5 个文件时，串行要等 5 次磁盘 IO。
# 但并发之后两个 edit 可能同时改同一个文件 —— 所以那一章才有
# mutation_queue.py。**先有并发，才需要队列**，顺序不要颠倒。

def execute_tool_calls(calls, *, signal, emit, executor=None):
    """执行本轮所有工具调用，返回能给模型回灌的 dict 列表。

    每个结果形如 {"tool_call_id":..., "content": 输出文本}，
    调用方会把它包成一条 role="tool" 的消息。
    **返回值顺序必须和 calls 一致** —— 模型的 tool_calls 是有序的，
    结果乱序它就对不上号了。
    """
    if not calls:
        return []

    # 只有一个工具，或者没给线程池：串行，不需要任何协调
    if len(calls) == 1 or executor is None:
        return [_run_one(c, signal, emit) for c in calls]

    # 多个工具：并发跑，但按原顺序收集结果
    futures = [executor.submit(_run_one, c, signal, emit) for c in calls]
    return [f.result() for f in futures]


def _run_one(call, signal, emit):
    """执行单个工具调用，返回 {"tool_call_id", "content"}。

    OpenAI 的每条 tool_call 是 {id, function:{name, arguments}}，
    arguments 是**字符串化的 JSON**，模型可能给出非法内容，这里兜底。
    """
    fn = call.get("function") or {}
    name = fn.get("name")
    raw_args = fn.get("arguments")
    if isinstance(raw_args, str):
        try:
            inp = json.loads(raw_args)
        except json.JSONDecodeError:
            inp = {}
    else:
        inp = raw_args or {}
    if not isinstance(inp, dict):
        inp = {}

    emit("tool_start", name=name, input=inp)

    try:
        signal.check()                      # 第 4 章：执行前也判中断
        output = toolbox.submit(name, inp)
    except Aborted as e:
        # 注意：这里把中断"吃掉"并转成工具结果，而不是往上抛。
        # 原因是对话历史里每条 tool_call 都必须有配对的 tool 消息，
        # 缺一个的话下次请求会被 API 直接拒绝。
        output = f"Error: aborted ({e})"
    except Exception as e:                  # 兜底：工具炸了不能让整个 agent 崩
        output = f"Error: {type(e).__name__}: {e}"

    emit("tool_end", name=name, output=output)

    return {"tool_call_id": call.get("id"), "content": output}


# ══════════════════════════════════════════════════════════════
# 历史追加（第 5 章加入的间接层）
# ══════════════════════════════════════════════════════════════
#
# 为什么不直接 messages.append(...)？
# 因为第 5 章的会话持久化要求"每一条历史变化都落盘"。如果散落着
# 4 处 append，你就得记得改 4 个地方；而且第 6 章压缩时会整体
# 替换历史，那也需要被感知到。
#
# 抽成 _append + on_append 钩子之后，加"历史变了要做什么"只改这里。

def _append(messages, msg, on_append):
    messages.append(msg)
    if on_append is not None:
        on_append(msg)
    return msg


# ══════════════════════════════════════════════════════════════
# 主循环
# ══════════════════════════════════════════════════════════════

def agent_loop(
    messages,
    *,
    tools=None,
    system=None,
    signal=None,
    steering=None,
    max_turns=None,
    emit=None,
    executor=None,
    on_append=None,
    before_request=None,
):
    """跑一次完整的 agent 循环，就地修改 messages 并返回它。

    :param messages:  对话历史列表。**就地修改**，调用方持有同一个列表。
    :param tools:     工具定义，默认用 tools.TOOLS
    :param system:    系统提示词，None 则用 llm 里的默认值
    :param signal:    中断信号（第 4 章）
    :param steering:  转向队列（第 4 章）
    :param max_turns: 最多几轮，None 表示不限
    :param emit:      事件回调
    :param executor:  线程池，None 表示串行执行工具
    :param on_append: 每追加一条消息就回调一次（第 5 章：落盘）
    :param before_request: 每轮问模型之前的钩子（第 6 章：压缩）
    :return: messages
    """
    tools = toolbox.TOOLS if tools is None else tools
    emit = emit or default_emit
    signal = signal or Signal()

    turn = 0
    while True:
        # ── step 1：中断检查（第 4 章）────────────────────────
        if signal.aborted:
            emit("aborted", reason=signal.reason)
            break

        # ── step 2：吸收转向消息（第 4 章）────────────────────
        # 放在问模型之前，这样这一轮就能用上你刚说的话。
        if steering is not None:
            for text in steering.drain():
                _append(messages, {"role": "user", "content": text}, on_append)
                emit("steering", text=text)

        # ── step 3：轮数上限（第 4 章）────────────────────────
        turn += 1
        if max_turns is not None and turn > max_turns:
            emit("max_turns", limit=max_turns)
            break

        # ── step 4：压缩检查（第 6 章）────────────────────────
        # 放在问模型之前是必须的 —— 压完才知道这一轮实际要发多少。
        # 钩子要求**就地替换**（messages[:] = ...）而不是返回新列表，
        # 这样调用方和 session 对象持有的列表引用始终有效。
        if before_request is not None:
            before_request(messages)

        # ── step 5：问模型 ────────────────────────────────────
        emit("request", turn=turn, history_len=len(messages))
        response = llm.call_llm(messages, tools, system=system, signal=signal)

        # ── step 6：把这轮 assistant 回复原样存进历史 ─────────
        # 必须"原样"存：message 里除了 content，还有 tool_calls，
        # 下一轮要拿它们的 id 和 tool 消息配对。别只存文字。
        _append(messages, llm.extract_message(response), on_append)

        text = llm.extract_text(response)
        if text:
            emit("text", text=text)

        calls = llm.extract_tool_calls(response)

        # ── step 7a：没有工具调用 → 收工 ──────────────────────
        if not calls:
            emit("done", turns=turn)
            break

        # ── step 7b：模型刚说要调工具，但你可能在这期间按了中断 ──
        #
        # 这里**不能直接 break**。因为 step 6 已经把带 tool_calls 的
        # assistant 消息存进历史了，而 OpenAI 要求每条 tool_call 都
        # 必须有配对的 role="tool" 消息 —— 少一个，下次请求会被整个
        # 拒绝（报错常见原文：tool_call_id must be a previous tool call）。
        #
        # 所以正确做法是：补上被中断的 tool 消息，再退出。OpenAI 规定
        # 一条 tool_call 配一条 tool 消息，所以这里按条补。
        if signal.aborted:
            for c in calls:
                _append(
                    messages,
                    {
                        "role": "tool",
                        "tool_call_id": c.get("id"),
                        "content": f"Error: aborted ({signal.reason})",
                    },
                    on_append,
                )
            emit("aborted", reason=signal.reason)
            break

        # ── step 8：执行工具，每条结果回灌成一条 role="tool" 消息 ──
        # 注意：多个工具是**多条** tool 消息（每条带自己的 tool_call_id），
        # 不是合成一条。这和第 3 章"并发但保序"的承诺一致。
        results = execute_tool_calls(calls, signal=signal, emit=emit, executor=executor)
        for r in results:
            _append(messages, {"role": "tool", **r}, on_append)

    return messages


def make_executor(max_workers=MAX_PARALLEL_TOOLS):
    """建一个线程池。CLI 启动时建一次，整个会话复用。"""
    return ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="tool")


def make_compaction_hook(signal=None, emit=None):
    """造一个第 6 章用的 before_request 钩子。

    为什么返回闭包而不是直接把 compact 传进去：循环要求钩子
    "就地修改"，而 compaction.compact 是"返回新列表"。这里把
    返回式接口包装成就地式，让两边对上。
    """
    from . import compaction as _compaction

    def hook(messages):
        if not _compaction.needs_compaction(messages):
            return
        before = _compaction.estimate_tokens(messages)
        if emit:
            emit("compaction_start", tokens=before)

        compressed = _compaction.compact(messages, signal=signal)

        # 关键：就地替换，保持列表对象的身份不变。
        # 写成 `messages = compressed` 是**无效的** —— 那只是把局部
        # 变量指向别处，调用方的列表一点没变。这是 Python 常见的坑。
        messages[:] = compressed

        if emit:
            emit(
                "compaction_end",
                before=before,
                after=_compaction.estimate_tokens(messages),
            )

    return hook
