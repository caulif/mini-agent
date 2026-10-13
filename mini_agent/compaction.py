# -*- coding: utf-8 -*-
"""Context compaction (chapter 6).

Replace older history with a model-generated handoff summary while retaining
recent turns. Cut only at complete turn boundaries so assistant tool calls
remain paired with their tool results. Token counts use a character-based
estimate with a reserve allowance rather than a provider tokenizer."""

from . import llm
from .config import CONFIG

# Summary instructions preserve details needed to continue the task.
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
# Token estimation.
# ══════════════════════════════════════════════════════════════

def estimate_tokens(messages):
    """Estimate tokens from content, tool names, arguments, and tool-call IDs.
    
    Use characters / 3 plus fixed message overhead. This is a rough heuristic
    for mixed English and Chinese text, not an exact model-specific count."""
    total_chars = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            total_chars += len(content)
        elif content:                       # Fallback for uncommon historical content shapes.
            total_chars += len(str(content))
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            total_chars += len(str(fn.get("name", "")))
            total_chars += len(str(fn.get("arguments", "")))
        if msg.get("role") == "tool":
            total_chars += len(str(msg.get("tool_call_id", "")))
    # Allow fixed overhead for roles and message structure.
    overhead = len(messages) * 12
    return total_chars // 3 + overhead


def needs_compaction(messages, limit=None, reserve=None):
    """Decide whether estimated tokens have reached the reserved threshold."""
    limit = limit if limit is not None else CONFIG["max_context_tokens"]
    reserve = reserve if reserve is not None else CONFIG["compaction_reserve_tokens"]
    return estimate_tokens(messages) >= (limit - reserve)


# ══════════════════════════════════════════════════════════════
# Cut boundary.
# ══════════════════════════════════════════════════════════════

def is_turn_start(msg):
    """Identify a user text message that starts a conversation turn."""

    if msg.get("role") != "user":
        return False
    return isinstance(msg.get("content"), str)


def find_cut_index(messages, keep_recent_turns=None):
    """Find a complete-turn boundary before the retained recent turns.
    
    messages[:cut] will be summarized; messages[cut:] stays unchanged.
    Return None when history is too short for a useful cut."""
    keep = (
        keep_recent_turns
        if keep_recent_turns is not None
        else CONFIG["compaction_keep_recent_turns"]
    )

    # Count recent turn starts backward and cut before them.
    turn_starts = [i for i, m in enumerate(messages) if is_turn_start(m)]
    if len(turn_starts) <= keep:
        return None                      # Too few turns to compact.

    cut = turn_starts[len(turn_starts) - keep]
    return cut if cut > 0 else None      # A cut at zero would summarize nothing.


# ══════════════════════════════════════════════════════════════
# Compaction flow.
# ══════════════════════════════════════════════════════════════

def compact(messages, *, signal=None, keep_recent_turns=None):
    """Return a summary followed by recent history without mutating the input.
    
    Use a user-role summary to present background information, rather than
    making it appear to be the assistant's own previous answer."""
    cut = find_cut_index(messages, keep_recent_turns)
    if cut is None:
        return messages                  # Nothing to compact; return the original list.

    older = messages[:cut]
    recent = messages[cut:]

    summary = summarize(older, signal=signal)
    header = "Here is a summary of the earlier part of this session:\n\n"
    summary_msg = {"role": "user", "content": header + summary}

    return [summary_msg] + recent


def summarize(messages, *, signal=None):
    """Generate handoff notes in a separate model request with no tools."""
    # Render older history as plain text for the model.
    # This is a separate summary request.
    # It does not append to the ongoing conversation.
    transcript = _render(messages)
    prompt = [
        {
            "role": "user",
            "content": f"{SUMMARY_INSTRUCTION}\n\n<conversation>\n{transcript}\n</conversation>",
        }
    ]
    # No tools are needed for summarization.
    response = llm.call_llm(prompt, [], system=None, signal=signal)
    text = llm.extract_text(response).strip()
    if not text:
        # Keep a recognizable placeholder if the model returns no summary.
        return "(summary unavailable)"
    return text


def _render(messages):
    """Render messages and tool calls as text for the summary request."""
    lines = []
    for msg in messages:
        role = msg.get("role", "?")
        content = msg.get("content")
        if isinstance(content, str) and content:
            lines.append(f"[{role}] {content}")
        # Assistant tool calls.
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            lines.append(f"[assistant] called {fn.get('name')} with {fn.get('arguments')}")
        # Tool-result messages.
        if role == "tool":
            body = str(content or "")
            if len(body) > 500:
                body = body[:500] + " …(truncated)"
            lines.append(f"[tool] result: {body}")
    return "\n".join(lines)

