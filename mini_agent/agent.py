# -*- coding: utf-8 -*-
"""The agent loop (chapters 1, 3, 4, 5, and 6).

Ask the model, save its complete response, execute requested tools, append
their results, and repeat until there are no tool calls. Later chapters add
concurrency, cancellation, steering, persistence, and compaction through
checkpoints and optional hooks rather than replacing the loop."""


from concurrent.futures import ThreadPoolExecutor
import json

from . import llm

from . import tools as toolbox
from .control import Aborted, Signal

# Maximum concurrent tools.
MAX_PARALLEL_TOOLS = 8


# ══════════════════════════════════════════════════════════════
# Event callbacks.
# ══════════════════════════════════════════════════════════════
#
# Report loop events through emit(kind, **kwargs), rather than printing directly.
# The CLI renders events; tests can collect them for assertions.
# A different interface can reuse the same loop without changing its logic.

def default_emit(kind, **kw):
    """Print basic loop events; the CLI supplies its own renderer."""
    if kind == "text":
        print(kw["text"])
    elif kind == "tool_start":
        print(f"  → {kw['name']}({_brief(kw.get('input'))})")
    elif kind == "tool_end":
        pass  # Let the CLI decide whether to show long tool output.
    elif kind == "aborted":
        print('[Aborted] {0}'.format(kw.get('reason')))
    elif kind == "max_turns":
        print('[Turn limit reached: {0}]'.format(kw['limit']))


def _brief(inp, limit=70):
    """Summarize tool arguments on one line for terminal output."""
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
# Tool execution.
# ══════════════════════════════════════════════════════════════
#
# Chapter 1 used a sequential loop here.
#     return [_run_one(c, signal, emit) for c in calls]
# A plain for-loop is enough without concurrency.
#
# Chapter 3 adds concurrent execution for independent tools.
# Concurrent edits may target the same file.
# The mutation queue coordinates those file changes.

def execute_tool_calls(calls, *, signal, emit, executor=None):
    """Execute tool calls and return result dictionaries in call order.
    
    Each result contains tool_call_id and content. The caller adds role='tool'.
    The executor allows concurrent work; results retain the original call order."""
    if not calls:
        return []

    # A single call or no executor: execute sequentially.
    if len(calls) == 1 or executor is None:
        return [_run_one(c, signal, emit) for c in calls]

    # Run concurrently, but collect results in the original call order.
    futures = [executor.submit(_run_one, c, signal, emit) for c in calls]
    return [f.result() for f in futures]


def _run_one(call, signal, emit):
    """Parse one call's JSON arguments, execute it, and return its result.
    
    Convert cancellation and tool exceptions to result text so every recorded
    assistant tool call can still receive its matching tool message."""
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
        signal.check()                      # Chapter 4: check cancellation before execution too.
        output = toolbox.submit(name, inp)
    except Aborted as e:
        # Convert cancellation to a tool result rather than propagating it here.
        # Every assistant tool call must have a corresponding tool message.
        # A missing result would make the next request invalid.
        output = f"Error: aborted ({e})"
    except Exception as e:                  # Convert unexpected tool failures to result text.
        output = f"Error: {type(e).__name__}: {e}"

    emit("tool_end", name=name, output=output)

    return {"tool_call_id": call.get("id"), "content": output}


# ══════════════════════════════════════════════════════════════
# History append hook (chapter 5).
# ══════════════════════════════════════════════════════════════
#
# Keep message appends in one helper.
# Persistence needs notification whenever history gains a message.
# Scattered append calls make it easier to forget a persistence hook.
# Compaction also needs to persist replaced history.
#
# The helper keeps the append contract in one place.

def _append(messages, msg, on_append):
    messages.append(msg)
    if on_append is not None:
        on_append(msg)
    return msg


# ══════════════════════════════════════════════════════════════
# Main loop.
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
    """Run a complete agent turn, modifying and returning messages in place.
    
    tools defaults to the registered tool schemas; system is passed to the LLM.
    signal checks cancellation, steering supplies queued user messages, and
    max_turns limits model requests. emit reports events. executor enables
    concurrent tools. on_append persists messages after the loop appends them.
    before_request may compact history in place before each model request."""
    tools = toolbox.TOOLS if tools is None else tools
    emit = emit or default_emit
    signal = signal or Signal()

    turn = 0
    while True:
        # Step 1: cancellation check (chapter 4).
        if signal.aborted:
            emit("aborted", reason=signal.reason)
            break

        # Step 2: drain steering instructions (chapter 4).
        # Add follow-ups before asking the model so the next request includes them.
        if steering is not None:
            for text in steering.drain():
                _append(messages, {"role": "user", "content": text}, on_append)
                emit("steering", text=text)

        # Step 3: model-call limit (chapter 4).
        turn += 1
        if max_turns is not None and turn > max_turns:
            emit("max_turns", limit=max_turns)
            break

        # Step 4: compaction check (chapter 6).
        # Compact before sending the next model request.
        # The hook must replace list contents in place, not assign a new list.
        # This preserves references held by the caller and session.
        if before_request is not None:
            before_request(messages)

        # Step 5: ask the model.
        emit("request", turn=turn, history_len=len(messages))
        response = llm.call_llm(messages, tools, system=system, signal=signal)

        # Step 6: save the complete assistant message.
        # Keep tool_calls as well as content.
        # Their IDs pair with tool results in the next request.
        _append(messages, llm.extract_message(response), on_append)

        text = llm.extract_text(response)
        if text:
            emit("text", text=text)

        calls = llm.extract_tool_calls(response)

        # Step 7a: no tool calls means the task is done.
        if not calls:
            emit("done", turns=turn)
            break

        # Step 7b: check cancellation after the assistant declares tools.
        #
        # Do not simply break: the assistant call is already in history.
        # Each declared tool call must receive a matching result.
        # Otherwise the next request has invalid message ordering.
        # Providers may report an unmatched tool_call_id.
        #
        # Append an interrupted result for every pending call before exiting.
        # One call requires one matching tool message.
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

        # Step 8: execute tools and append one message per result.
        # Multiple calls need separate tool messages with their own IDs.
        # Keep their order consistent with the declared tool calls.
        results = execute_tool_calls(calls, signal=signal, emit=emit, executor=executor)
        for r in results:
            _append(messages, {"role": "tool", **r}, on_append)

    return messages


def make_executor(max_workers=MAX_PARALLEL_TOOLS):
    """Create a tool thread pool that the CLI reuses for the session."""
    return ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="tool")


def make_compaction_hook(signal=None, emit=None):
    """Adapt compaction's returned list to an in-place before_request hook.
    
    Slice assignment preserves the list shared by the loop and session."""
    from . import compaction as _compaction

    def hook(messages):
        if not _compaction.needs_compaction(messages):
            return
        before = _compaction.estimate_tokens(messages)
        if emit:
            emit("compaction_start", tokens=before)

        compressed = _compaction.compact(messages, signal=signal)

        # Preserve the list identity with slice assignment.
        # Assigning messages = compressed would only rebind a local variable.
        # The caller would still hold the unchanged original list.
        messages[:] = compressed

        if emit:
            emit(
                "compaction_end",
                before=before,
                after=_compaction.estimate_tokens(messages),
            )

    return hook
