from concurrent.futures import ThreadPoolExecutor
import json

from . import llm

from . import tools as toolbox
from .control import Aborted, Signal


MAX_PARALLEL_TOOLS = 8


def default_emit(kind, **kw):
    if kind == "text":
        print(kw["text"])
    elif kind == "tool_start":
        print(f"  → {kw['name']}({_brief(kw.get('input'))})")
    elif kind == "tool_end":
        pass
    elif kind == "aborted":
        print('[Aborted] {0}'.format(kw.get('reason')))
    elif kind == "max_turns":
        print('[Turn limit reached: {0}]'.format(kw['limit']))


def _brief(inp, limit=70):
    if not isinstance(inp, dict):
        return repr(inp)[:limit]
    parts = []
    for k, v in inp.items():
        s = str(v).replace("\n", "\\n")
        if len(s) > 30:
            s = s[:30] + "…"
        parts.append(f"{k}={s}")
    return ", ".join(parts)[:limit]


def execute_tool_calls(calls, *, signal, emit, executor=None, tool_runner=None):
    if tool_runner is not None:
        return [tool_runner(c, signal=signal, emit=emit) for c in calls]
    if not calls:
        return []

    if len(calls) == 1 or executor is None:
        return [_run_one(c, signal, emit) for c in calls]

    futures = [executor.submit(_run_one, c, signal, emit) for c in calls]
    return [f.result() for f in futures]


def _run_one(call, signal, emit):
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
        signal.check()
        output = toolbox.submit(name, inp)
    except Aborted as e:
        output = f"Error: aborted ({e})"
    except Exception as e:
        output = f"Error: {type(e).__name__}: {e}"

    emit("tool_end", name=name, output=output)

    return {"tool_call_id": call.get("id"), "content": output}


def _append(messages, msg, on_append):
    messages.append(msg)
    if on_append is not None:
        on_append(msg)
    return msg


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
    request_fn=None,
    tool_runner=None,
):
    tools = toolbox.TOOLS if tools is None else tools
    emit = emit or default_emit
    signal = signal or Signal()

    turn = 0
    while True:
        if signal.aborted:
            emit("aborted", reason=signal.reason)
            break

        if steering is not None:
            for text in steering.drain():
                _append(messages, {"role": "user", "content": text}, on_append)
                emit("steering", text=text)

        turn += 1
        if max_turns is not None and turn > max_turns:
            emit("max_turns", limit=max_turns)
            break

        if before_request is not None:
            before_request(messages)

        emit("request", turn=turn, history_len=len(messages))
        response = (request_fn or llm.call_llm)(messages, tools, system=system, signal=signal)

        _append(messages, llm.extract_message(response), on_append)

        text = llm.extract_text(response)
        if text:
            emit("text", text=text)

        calls = llm.extract_tool_calls(response)

        if not calls:
            emit("done", turns=turn)
            break

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

        results = execute_tool_calls(calls, signal=signal, emit=emit, executor=executor,
                                     tool_runner=tool_runner)
        for r in results:
            _append(messages, {"role": "tool", **r}, on_append)

    return messages


def make_executor(max_workers=MAX_PARALLEL_TOOLS):
    return ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="tool")


def make_compaction_hook(signal=None, emit=None):
    from . import compaction as _compaction

    def hook(messages):
        if not _compaction.needs_compaction(messages):
            return
        before = _compaction.estimate_tokens(messages)
        if emit:
            emit("compaction_start", tokens=before)

        compressed = _compaction.compact(messages, signal=signal)

        messages[:] = compressed

        if emit:
            emit(
                "compaction_end",
                before=before,
                after=_compaction.estimate_tokens(messages),
            )

    return hook
