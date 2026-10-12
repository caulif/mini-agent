# -*- coding: utf-8 -*-
"""
mini_agent · LLM 调用层（第 1 章建立，第 4 章加中断检查）

这是集中处理 LLM 请求的地方。上层（循环）只调 call_llm()，
不知道背后是 OpenAI 还是别的什么。

第 1 章用裸 HTTP 是为了让你看清请求体里的每个字段 —— 用 SDK 的话
这些字段被藏起来了，你调试时不知道到底发了什么。

后续章节都是在这个函数上叠加能力，不改它的签名语义。
"""

import json
import urllib.error
import urllib.request

from .config import CONFIG

# 默认系统提示词。
# 第 7 章会教你怎么把它拆成"固定骨架 + 动态环境信息 + 外部技能文件"，
# 那时这里就只是最朴素的兜底版本了。
SYSTEM_PROMPT = (
    "You are a coding agent. "
    "Use the provided tools to solve the task. Act, don't explain."
)


def call_llm(messages, tools, system=SYSTEM_PROMPT, signal=None):
    """向 OpenAI Chat Completions 接口发一次请求，返回响应 dict。

    :param messages: 完整对话历史（**每次都要把全部历史发回去** ——
                     模型是无状态的，它不记得上一轮，全靠你重发）
    :param tools:    工具定义列表（OpenAI 的 function schema 形状）
    :param system:   系统提示词；传 None 表示用默认值
    :param signal:   中断信号（第 4 章）。在**发请求前**检查一次，
                     避免白白发一个已经没人要的请求。
    :return: API 返回的 dict（含 choices[0].message 和 finish_reason）
    """
    # ── 第 4 章：发出去之前先看看还需不需要发 ──────────────────
    # 这一步能省掉"用户已经按了中断、但我们还在为它烧 token"的浪费。
    if signal is not None:
        signal.check()

    # OpenAI 里 system 不是顶层字段，而是一条 role="system" 的消息，
    # 放在 messages 最前面。历史本身不含 system，所以这里现拼。
    system_text = SYSTEM_PROMPT if system is None else system
    full_messages = [{"role": "system", "content": system_text}] + messages

    payload = json.dumps(
        {
            "model": CONFIG["model"],
            "max_tokens": CONFIG["max_tokens"],
            "messages": full_messages,
            "tools": tools,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        f"{CONFIG['base_url']}/chat/completions",
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {CONFIG['api_key']}",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=CONFIG["request_timeout_s"]) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"LLM 请求失败 {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"LLM 连接失败: {e.reason}") from e

    # ── 第 4 章：请求已发出，等回来的这段时间里你可能又按了中断 ──
    # 中断已经发生的话，把结果丢弃，让上层尽快跳出。
    # 局限：urllib 的阻塞读**没法**真正打断，所以这是"事后补救"。
    # 真要能随时掐断正在飞的请求，得上流式 + 主动关连接（进阶内容）。
    if signal is not None:
        signal.check()

    return data


def extract_message(response):
    """取出响应里的 assistant 消息 dict（含 content 和 tool_calls）。

    OpenAI 的响应是 {choices: [{message: {...}}]}。
    这条 message 要**原样**存进历史 —— 下一轮它要和 tool 结果配对。
    """
    return response["choices"][0]["message"]


def extract_text(response):
    """取出这一轮模型写的文字回答。

    OpenAI 里 assistant 的 content 就是字符串（或 null，当它只顾着
    调工具的时候）。为什么抽成函数：循环在两处都要用到它 ——
    判断完是否继续后，把模型的文字回答打出来。
    """
    return extract_message(response).get("content") or ""


def extract_tool_calls(response):
    """取出这一轮响应里所有的 tool_calls。

    这是循环是否继续的**唯一判据** —— 不看 finish_reason。
    原因：finish_reason 是 provider 的元数据，各家命名不一样，
    而"message 里有没有 tool_calls"是你在构造历史时亲手写进的事实。
    用事实判断，比用元数据判断可靠。

    返回列表，每项形态：{id, type:"function", function:{name, arguments}}
    """
    return extract_message(response).get("tool_calls") or []

