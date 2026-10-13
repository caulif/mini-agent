# -*- coding: utf-8 -*-
"""Model transport (chapters 1 and 4).

Use standard-library HTTP so the request body remains visible. Higher layers
call call_llm without depending on a particular compatible provider.
Cancellation is checked before sending and after receiving a response."""


import json
import urllib.error
import urllib.request

from .config import CONFIG

# Fallback system prompt.
# Chapter 7 assembles behavior, environment information, and Markdown skills.
# This constant remains a minimal fallback for direct callers.
SYSTEM_PROMPT = (
    "You are a coding agent. "
    "Use the provided tools to solve the task. Act, don't explain."
)


def call_llm(messages, tools, system=SYSTEM_PROMPT, signal=None):
    """Send one Chat Completions request and return the response dictionary.
    
    messages is the full conversation history; the model does not retain it
    between requests. tools contains function schemas. system=None uses the
    fallback prompt. signal performs cooperative cancellation checks."""
    # Chapter 4: check cancellation before sending.
    # Avoid sending a request after the user has already cancelled.
    if signal is not None:
        signal.check()

    # The protocol represents system instructions as a message.
    # Prepend it without changing the stored conversation history.
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
        raise RuntimeError('LLM request failed {0}: {1}'.format(e.code, detail)) from e
    except urllib.error.URLError as e:
        raise RuntimeError('LLM connection failed: {0}'.format(e.reason)) from e

    # Chapter 4: check cancellation again after receiving the response.
    # Discard the response if cancellation occurred while waiting.
    # urllib's blocking read cannot be interrupted by this check.
    # Immediate transport cancellation would require a different request mechanism.
    if signal is not None:
        signal.check()

    return data


def extract_message(response):
    """Return the complete assistant message, including content and tool_calls."""
    return response["choices"][0]["message"]


def extract_text(response):
    """Return assistant text, using an empty string when content is null."""
    return extract_message(response).get("content") or ""


def extract_tool_calls(response):
    """Return tool calls, or an empty list when the assistant requests none.
    
    The loop uses these calls to decide whether to continue, rather than
    depending on provider-specific finish_reason metadata."""
    return extract_message(response).get("tool_calls") or []

