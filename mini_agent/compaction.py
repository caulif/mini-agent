# -*- coding: utf-8 -*-
"""
mini_agent · 上下文压缩（第 6 章新增）

问题：agent 每跑一轮，都要把**全部历史**发给模型。历史只增不减，
迟早会撑爆模型的上下文窗口，API 直接报错。而且就算没爆，
历史越长每一轮越贵、越慢。

解决办法：当估算的 token 数逼近上限时，把**最老的一段历史**
压成一小段摘要，用摘要替换原文。新历史 = [摘要] + [最近若干轮]。

关键设计点（这章的重点全在这三条）：

  1. **切点必须落在"整轮"边界上**。一轮 = 一条 user 消息 + 它引发的
     所有 assistant/tool 消息，中途切断会出现"孤儿 tool 消息"
     （tool 消息的 tool_call_id 在上文找不到配对的 tool_calls），
     API 会直接拒绝整个请求。这是压缩最容易踩的坑。

  2. **不精确数 token，用估算**。精确要按模型的分词器算，各家的
     还不一样。教学版用"字符数 / 3"这种粗糙近似就够 —— 只要有
     余量（reserve）兜住误差就行。

  3. **摘要也要由模型来写**，而且要明确告诉它"目的是给后续对话当
     背景"。如果只写"总结一下"，它可能写成给人看的散文，丢掉
     文件路径、函数名这些后续真正要用的细节。
"""

from . import llm
from .config import CONFIG

# 摘要指令。措辞很重要 —— 见上面设计点 3。
SUMMARY_INSTRUCTION = """\
You are compacting a coding session so it can continue in a fresh context.
Write a dense handoff summary of the conversation below.

You MUST preserve, verbatim where possible:
- file paths that were read or modified
- names of functions, classes, variables that were changed
- decisions made and the reason for them
- what is still unfinished

Do NOT include chit-chat. Do NOT invent anything not present below.
Write it as notes to your future self, not as prose for a human.
"""


# ══════════════════════════════════════════════════════════════
# token 估算
# ══════════════════════════════════════════════════════════════

def estimate_tokens(messages):
    """粗略估算一段历史的 token 数。

    算法：把每条消息里所有对 token 有贡献的文本（content、tool_calls
    的工具名与参数、tool 消息的 id 与正文）加起来，然后 chars / 3。
    为什么除 3？英文大约 4 字符 = 1 token，中文大约 1 字符 = 1 token
    往上，取个中间偏保守的值。

    保守（估多一点）比估少了安全 —— 估少了会漏掉该压缩的时机，
    然后直接撞上 API 的硬限制。
    """
    total_chars = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            total_chars += len(content)
        elif content:                       # 兜底：罕见的历史兼容形状
            total_chars += len(str(content))
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            total_chars += len(str(fn.get("name", "")))
            total_chars += len(str(fn.get("arguments", "")))
        if msg.get("role") == "tool":
            total_chars += len(str(msg.get("tool_call_id", "")))
    # 每条消息还有 role、结构开销，给个固定补贴
    overhead = len(messages) * 12
    return total_chars // 3 + overhead


def needs_compaction(messages, limit=None, reserve=None):
    """判断是否该压缩了。"""
    limit = limit if limit is not None else CONFIG["max_context_tokens"]
    reserve = reserve if reserve is not None else CONFIG["compaction_reserve_tokens"]
    return estimate_tokens(messages) >= (limit - reserve)


# ══════════════════════════════════════════════════════════════
# 切点
# ══════════════════════════════════════════════════════════════

def is_turn_start(msg):
    """这条消息是否是一"轮"的开头。

    判据：role == "user" 且 content 是纯字符串。
    OpenAI 里工具结果回灌用的是 role="tool"（而不是 user），
    所以它们天然不是轮次开头；user 的纯字符串才是。

    这个区分是全部切点逻辑的基础。
    """

    if msg.get("role") != "user":
        return False
    return isinstance(msg.get("content"), str)


def find_cut_index(messages, keep_recent_turns=None):
    """算出一个安全的切点下标。

    返回 i 表示：messages[:i] 拿去摘要，messages[i:] 原样保留。
    i 一定落在某"轮"的开头（见 is_turn_start）。

    返回 None 表示"找不到安全切点"（历史太短，不该压缩）。
    """
    keep = (
        keep_recent_turns
        if keep_recent_turns is not None
        else CONFIG["compaction_keep_recent_turns"]
    )

    # 从后往前数出 keep 个"轮的开头"，切在它前面
    turn_starts = [i for i, m in enumerate(messages) if is_turn_start(m)]
    if len(turn_starts) <= keep:
        return None                      # 轮数还不够多，没什么可压的

    cut = turn_starts[len(turn_starts) - keep]
    return cut if cut > 0 else None      # 切在 0 等于没压


# ══════════════════════════════════════════════════════════════
# 压缩主流程
# ══════════════════════════════════════════════════════════════

def compact(messages, *, signal=None, keep_recent_turns=None):
    """压缩历史。返回新列表，不改原来的。

    返回值形状：[{role:"user", content:"<摘要>"}] + 保留的最近几轮

    注意摘要用 role="user" 而不是 "assistant"：对模型来说
    "这些是背景信息"更像是一条用户交代的前提，而不是它自己
    之前说过的话。用 assistant 反而会让它以为那是自己的结论。
    """
    cut = find_cut_index(messages, keep_recent_turns)
    if cut is None:
        return messages                  # 没得压，原样返回

    older = messages[:cut]
    recent = messages[cut:]

    summary = summarize(older, signal=signal)
    header = "Here is a summary of the earlier part of this session:\n\n"
    summary_msg = {"role": "user", "content": header + summary}

    return [summary_msg] + recent


def summarize(messages, *, signal=None):
    """调模型把一段历史压成摘要文本。

    为什么单独抽出来：你可能想单独测"摘要质量"，
    也可能想换一种压缩策略（比如保留全部工具调用、只压文字）。
    """
    # 把待压缩的历史渲染成一段纯文本喂给模型。
    # 注意这里发的是一个**独立的一次性请求** —— 不是往当前
    # 对话里追加，不会污染正在进行的 messages。
    transcript = _render(messages)
    prompt = [
        {
            "role": "user",
            "content": f"{SUMMARY_INSTRUCTION}\n\n<conversation>\n{transcript}\n</conversation>",
        }
    ]
    # tools=[] ：摘要请求不需要工具，避免模型想着调工具
    response = llm.call_llm(prompt, [], system=None, signal=signal)
    text = llm.extract_text(response).strip()
    if not text:
        # 兜底：模型什么都没返回时，至少留下一个可辨认的占位
        return "(summary unavailable)"
    return text


def _render(messages):
    """把消息列表渲染成人类/模型可读的文本。"""
    lines = []
    for msg in messages:
        role = msg.get("role", "?")
        content = msg.get("content")
        if isinstance(content, str) and content:
            lines.append(f"[{role}] {content}")
        # assistant 发起的工具调用
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            lines.append(f"[assistant] called {fn.get('name')} with {fn.get('arguments')}")
        # tool 消息（工具结果回灌）
        if role == "tool":
            body = str(content or "")
            if len(body) > 500:
                body = body[:500] + " …(truncated)"
            lines.append(f"[tool] result: {body}")
    return "\n".join(lines)

