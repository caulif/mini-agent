from . import llm
from .config import CONFIG


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


def estimate_tokens(messages):
    total_chars = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            total_chars += len(content)
        elif content:
            total_chars += len(str(content))
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            total_chars += len(str(fn.get("name", "")))
            total_chars += len(str(fn.get("arguments", "")))
        if msg.get("role") == "tool":
            total_chars += len(str(msg.get("tool_call_id", "")))

    overhead = len(messages) * 12
    return total_chars // 3 + overhead


def needs_compaction(messages, limit=None, reserve=None):
    limit = limit if limit is not None else CONFIG["max_context_tokens"]
    reserve = reserve if reserve is not None else CONFIG["compaction_reserve_tokens"]
    return estimate_tokens(messages) >= (limit - reserve)


def is_turn_start(msg):
    if msg.get("role") != "user":
        return False
    return isinstance(msg.get("content"), str)


def find_cut_index(messages, keep_recent_turns=None):
    keep = (
        keep_recent_turns
        if keep_recent_turns is not None
        else CONFIG["compaction_keep_recent_turns"]
    )

    turn_starts = [i for i, m in enumerate(messages) if is_turn_start(m)]
    if len(turn_starts) <= keep:
        return None

    cut = turn_starts[len(turn_starts) - keep]
    return cut if cut > 0 else None


def compact(messages, *, signal=None, keep_recent_turns=None):
    cut = find_cut_index(messages, keep_recent_turns)
    if cut is None:
        return messages

    older = messages[:cut]
    recent = messages[cut:]

    summary = summarize(older, signal=signal)
    header = "Here is a summary of the earlier part of this session:\n\n"
    summary_msg = {"role": "user", "content": header + summary}

    return [summary_msg] + recent


def summarize(messages, *, signal=None):
    transcript = _render(messages)
    prompt = [
        {
            "role": "user",
            "content": f"{SUMMARY_INSTRUCTION}\n\n<conversation>\n{transcript}\n</conversation>",
        }
    ]

    response = llm.call_llm(prompt, [], system=None, signal=signal)
    text = llm.extract_text(response).strip()
    if not text:
        return "(summary unavailable)"
    return text


def _render(messages):
    lines = []
    for msg in messages:
        role = msg.get("role", "?")
        content = msg.get("content")
        if isinstance(content, str) and content:
            lines.append(f"[{role}] {content}")

        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            lines.append(f"[assistant] called {fn.get('name')} with {fn.get('arguments')}")

        if role == "tool":
            body = str(content or "")
            if len(body) > 500:
                body = body[:500] + " …(truncated)"
            lines.append(f"[tool] result: {body}")
    return "\n".join(lines)
