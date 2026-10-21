import json
import urllib.request

from .config import CONFIG
from .transport import post_json


SYSTEM_PROMPT = (
    "You are a coding agent. "
    "Use the provided tools to solve the task. Act, don't explain."
)


def call_llm(messages, tools, system=SYSTEM_PROMPT, signal=None):
    if signal is not None:
        signal.check()

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
        CONFIG["base_url"].rstrip("/") + "/chat/completions",
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {CONFIG['api_key']}",
        },
    )

    data = post_json(req, signal=signal)

    if signal is not None:
        signal.check()

    return data


def extract_message(response):
    return response["choices"][0]["message"]


def extract_text(response):
    return extract_message(response).get("content") or ""


def extract_tool_calls(response):
    return extract_message(response).get("tool_calls") or []


def fake_text_response(text):
    return {
        "choices": [
            {"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
        ]
    }


_fake_id_counter = 0


def fake_tool_response(*calls, text=None, ids=None):
    global _fake_id_counter
    calls_blocks = []
    for i, (name, inp) in enumerate(calls):
        if ids is not None:
            call_id = ids[i]
        else:
            call_id = f"call_{_fake_id_counter:03d}"
            _fake_id_counter += 1

        calls_blocks.append(
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(inp)},
            }
        )
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": text,
                    "tool_calls": calls_blocks,
                },
                "finish_reason": "tool_calls",
            }
        ]
    }
