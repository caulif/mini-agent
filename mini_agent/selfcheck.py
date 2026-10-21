import json
import re
import os
import shutil
import sys
import tempfile
import threading
import time
import traceback

from . import agent as agent_mod
from . import compaction, llm, prompt, session as session_mod, tools
from .config import setup_console
from .mutation_queue import FileMutationQueue

PASSED = []
FAILED = []


def check(name, fn):
    try:
        fn()
        PASSED.append(name)
        print(f"  {GREEN}[通过]{RESET} {name}")
    except Exception as e:
        FAILED.append((name, e))
        print(f"  {RED}[失败]{RESET} {name}")
        print(f"         {type(e).__name__}: {e}")
        tb = traceback.format_exc().strip().splitlines()
        for line in tb[-4:]:
            print(f"         {line}")


RED = "\033[31m"
GREEN = "\033[32m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


def eq(actual, expected, what=""):
    if actual != expected:
        raise AssertionError(f"{what}: 期望 {expected!r}，实际 {actual!r}")


def truthy(value, what=""):
    if not value:
        raise AssertionError(f"{what}: 期望为真，实际 {value!r}")


class FakeLLM:
    def __init__(self, script=None):
        self.script = list(script or [])
        self.calls = []
        self._original = None

    def __call__(self, messages, tools_, system=None, signal=None):
        self.calls.append(
            {
                "messages": json.loads(json.dumps(messages, default=str)),
                "tools": tools_,
                "system": system,
            }
        )
        if self.script:
            return self.script.pop(0)

        return llm.fake_text_response("(script exhausted)")

    def __enter__(self):
        self._original = llm.call_llm
        llm.call_llm = self
        return self

    def __exit__(self, *exc):
        llm.call_llm = self._original
        return False


def test_dispatch_unknown_tool():
    out = tools.submit("no_such_tool", {})
    truthy("unknown tool" in out, "应提示未知工具")
    truthy("bash" in out, "应列出可用工具")


def test_dispatch_routes_by_name():
    eq(tools.HANDLERS["bash"], tools.tool_bash, "bash 路由")
    eq(tools.HANDLERS["edit"], tools.tool_edit, "edit 路由")

    declared = {t["function"]["name"] for t in tools.TOOLS}
    eq(declared, set(tools.HANDLERS), "TOOLS 与 HANDLERS 必须一致")


def test_tool_error_becomes_text():
    out = tools.submit("read", {"path": "does-not-exist-xyz.txt"})
    truthy(out.startswith("Error:"), f"应返回 Error 开头，实际 {out[:60]!r}")


def test_path_escape_blocked():
    out = tools.submit("read", {"path": "../../../../etc/passwd"})
    truthy("Error" in out, "越界路径应报错")


def test_queue_serializes_same_file():
    q = FileMutationQueue()
    order = []

    def step_a():
        order.append("A-start")
        time.sleep(0.08)
        order.append("A-end")

    def step_b():
        order.append("B-start")
        time.sleep(0.02)
        order.append("B-end")

    t1 = threading.Thread(target=lambda: q.run("same.txt", step_a))
    t2 = threading.Thread(target=lambda: q.run("same.txt", step_b))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    eq(order, ["A-start", "A-end", "B-start", "B-end"], "同文件应严格串行")

def test_queue_parallel_across_files():
    q = FileMutationQueue()
    spans = {}
    lock = threading.Lock()

    def make_step(name):
        def step():
            begin = time.time()
            time.sleep(0.12)
            with lock:
                spans[name] = (begin, time.time())
        return step

    t1 = threading.Thread(target=lambda: q.run("file1.txt", make_step("a")))
    t2 = threading.Thread(target=lambda: q.run("file2.txt", make_step("b")))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    a0, a1 = spans["a"]
    b0, b1 = spans["b"]

    overlapped = a0 < b1 and b0 < a1
    truthy(
        overlapped,
        f"跨文件应并行（区间应重叠），实际 a={a0:.3f}..{a1:.3f} "
        f"b={b0:.3f}..{b1:.3f}",
    )


def test_queue_key_normalizes_path():
    q = FileMutationQueue()
    k1 = q._key_for("a.txt")
    k2 = q._key_for("./a.txt")
    k3 = q._key_for(os.path.join(os.getcwd(), "a.txt"))
    eq(k1, k2, "./ 前缀应归一化")
    eq(k1, k3, "绝对/相对应归一化")


def test_queue_uses_realpath_for_symlink():
    tmp = tempfile.mkdtemp()
    try:
        real = os.path.join(tmp, "real.txt")
        link = os.path.join(tmp, "link.txt")
        with open(real, "w", encoding="utf-8") as f:
            f.write("x")
        try:
            os.symlink(real, link)
        except (OSError, NotImplementedError):
            return

        q = FileMutationQueue()
        eq(q._key_for(real), q._key_for(link), "链接与目标应同 key")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_edit_requires_unique_match():
    tmp = tempfile.mkdtemp()
    cwd = os.getcwd()
    try:
        os.chdir(tmp)
        with open("dup.txt", "w", encoding="utf-8") as f:
            f.write("hello\nhello\n")
        out = tools.submit("edit", {
            "path": "dup.txt", "old_string": "hello", "new_string": "X"
        })
        truthy("2 times" in out or "unique" in out, f"应拒绝重复匹配，实际 {out!r}")

        with open("uniq.txt", "w", encoding="utf-8") as f:
            f.write("alpha beta gamma\n")
        out2 = tools.submit("edit", {
            "path": "uniq.txt", "old_string": "beta", "new_string": "BETA"
        })
        truthy(out2.startswith("Edited"), f"唯一匹配应成功，实际 {out2!r}")
        with open("uniq.txt", encoding="utf-8") as f:
            eq(f.read(), "alpha BETA gamma\n", "替换结果")
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmp, ignore_errors=True)


def test_loop_executes_tool_then_stops():
    messages = [{"role": "user", "content": "go"}]
    fake = FakeLLM([
        llm.fake_tool_response(("bash", {"command": "echo hi"})),
        llm.fake_text_response("all done"),
    ])
    with fake:
        agent_mod.agent_loop(messages, emit=lambda *a, **k: None)

    eq(len(messages), 4, "消息条数")
    eq(messages[1]["role"], "assistant", "第 2 条是 assistant")
    eq(messages[1]["tool_calls"][0]["function"]["name"], "bash", "工具名")
    eq(messages[2]["role"], "tool", "第 3 条是 tool 消息")
    eq(messages[2]["tool_call_id"], messages[1]["tool_calls"][0]["id"],
       "tool 消息必须指定配对的 tool_call_id")
    truthy("hi" in messages[2]["content"], "工具真的执行了")


def test_loop_pairs_every_tool_use():
    messages = [{"role": "user", "content": "go"}]
    fake = FakeLLM([
        llm.fake_tool_response(
            ("bash", {"command": "echo a"}),
            ("bash", {"command": "echo b"}),
        ),
        llm.fake_text_response("done"),
    ])
    with fake:
        agent_mod.agent_loop(messages, emit=lambda *a, **k: None)

    _assert_pairs(messages)


def _assert_pairs(messages):
    used_ids = set()
    result_ids = set()
    for msg in messages:
        for tc in msg.get("tool_calls") or []:
            used_ids.add(tc["id"])
        if msg.get("role") == "tool":
            result_ids.add(msg["tool_call_id"])
    eq(used_ids, result_ids, "tool_calls 与 tool 消息必须完全配对")


def test_loop_max_turns():
    messages = [{"role": "user", "content": "loop forever"}]

    script = [llm.fake_tool_response(("bash", {"command": "echo x"}))] * 10
    fake = FakeLLM(script)
    with fake:
        agent_mod.agent_loop(
            messages, max_turns=2, emit=lambda *a, **k: None
        )

    eq(len(fake.calls), 2, "应只调用模型 2 次")


def test_loop_abort_stops_early():
    from .control import Signal

    messages = [{"role": "user", "content": "go"}]
    signal = Signal()
    signal.abort("test abort")

    fake = FakeLLM([llm.fake_text_response("should not be reached")])
    with fake:
        agent_mod.agent_loop(
            messages, signal=signal, emit=lambda *a, **k: None
        )
    eq(len(fake.calls), 0, "已中断时不应再调用模型")


def test_loop_abort_after_tool_use_still_pairs():
    from .control import Signal

    messages = [{"role": "user", "content": "go"}]
    signal = Signal()

    class AbortingLLM(FakeLLM):
        def __call__(self, messages, tools_, system=None, signal=None):
            resp = super().__call__(messages, tools_, system, signal)
            signal.abort("ctrl-c")
            return resp

    fake = AbortingLLM([
        llm.fake_tool_response(("bash", {"command": "sleep 5"})),
    ])
    with fake:
        agent_mod.agent_loop(
            messages, signal=signal, emit=lambda *a, **k: None
        )

    _assert_pairs(messages)
    truthy(len(messages) >= 3, "应补上被打断的 tool 消息")


def test_loop_steering_inserted():
    from .control import SteeringQueue

    messages = [{"role": "user", "content": "go"}]
    steering = SteeringQueue()
    steering.push("actually, do this instead")

    fake = FakeLLM([llm.fake_text_response("ok")])
    with fake:
        agent_mod.agent_loop(
            messages, steering=steering, emit=lambda *a, **k: None
        )

    contents = [m["content"] for m in messages if isinstance(m["content"], str)]
    truthy(
        any("do this instead" in c for c in contents),
        "转向消息应出现在历史里",
    )


def test_tools_are_parallel_but_ordered():
    messages = [{"role": "user", "content": "go"}]
    fake = FakeLLM([
        llm.fake_tool_response(
            ("bash", {"command": "echo first"}),
            ("bash", {"command": "echo second"}),
            ("bash", {"command": "echo third"}),
        ),
        llm.fake_text_response("done"),
    ])
    with fake:
        executor = agent_mod.make_executor()
        try:
            agent_mod.agent_loop(
                messages, executor=executor, emit=lambda *a, **k: None
            )
        finally:
            executor.shutdown(wait=False)

    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    eq(len(tool_msgs), 3, "应有 3 条 tool 消息")

    called = [tc["id"] for tc in messages[1]["tool_calls"]]
    returned = [m["tool_call_id"] for m in tool_msgs]
    eq(len(called), 3, "应有 3 个 tool_calls")
    eq(returned, called, "结果顺序必须与调用顺序一致")
    eq(len(set(called)), 3, "三个 id 必须互不相同（否则掩盖配对错误）")


def test_session_roundtrip():
    tmp = tempfile.mkdtemp()
    try:
        s = session_mod.Session.create("test-id", session_dir=tmp)
        s.append("user", "hello")
        s.append("assistant", "hi there")
        s.append_message({"role": "user", "content": [{"type": "text", "text": "x"}]})

        loaded = session_mod.Session.load(s.path)
        eq(len(loaded.messages), 3, "恢复的消息条数")
        eq(loaded.messages[0]["content"], "hello", "第一条内容")
        eq(loaded.messages[2]["content"][0]["type"], "text", "块结构保留")
        eq(loaded.id, "test-id", "会话 id")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_session_jsonl_survives_corrupt_line():
    tmp = tempfile.mkdtemp()
    try:
        s = session_mod.Session.create("corrupt", session_dir=tmp)
        s.append("user", "good line 1")

        with open(s.path, "a", encoding="utf-8") as f:
            f.write("{ this is not json\n")
        s.append("user", "good line 2")

        loaded = session_mod.Session.load(s.path)
        eq(len(loaded.messages), 2, "应跳过坏行、保留好行")
        eq(loaded.messages[0]["content"], "good line 1", "第一条仍可读")
        eq(loaded.messages[1]["content"], "good line 2", "第二条仍可读")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_session_resume_feeds_history_to_model():
    tmp = tempfile.mkdtemp()
    try:
        s = session_mod.Session.create("resume", session_dir=tmp)
        s.append("user", "earlier question")
        s.append("assistant", "earlier answer")
        loaded = session_mod.Session.load(s.path)

        fake = FakeLLM([llm.fake_text_response("continuing")])
        with fake:
            agent_mod.agent_loop(
                loaded.messages, emit=lambda *a, **k: None
            )

        sent = fake.calls[0]["messages"]
        truthy(
            any(m.get("content") == "earlier question" for m in sent),
            "历史应被发给模型",
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_replace_messages_rewrites_disk():
    tmp = tempfile.mkdtemp()
    try:
        s = session_mod.Session.create("replace", session_dir=tmp)
        s.append("user", "a")
        s.append("assistant", "b")
        s.replace_messages([{"role": "user", "content": "only this"}])

        loaded = session_mod.Session.load(s.path)
        eq(len(loaded.messages), 1, "磁盘应只剩 1 条")
        truthy(
            not os.path.exists(s.path + ".tmp"),
            "原子替换后不该留 .tmp 文件",
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_session_memory_and_disk_stay_in_sync():
    tmp = tempfile.mkdtemp()
    try:
        s = session_mod.Session.create("sync", session_dir=tmp)
        s.append("user", "go")

        fake = FakeLLM([
            llm.fake_tool_response(("bash", {"command": "echo hi"})),
            llm.fake_text_response("done"),
        ])
        with fake:
            agent_mod.agent_loop(
                s.messages,
                emit=lambda *a, **k: None,
                on_append=s.persist,
            )

        eq(len(s.messages), 4, "内存应有 4 条")
        disk = session_mod.Session.load(s.path)
        eq(
            len(disk.messages), len(s.messages),
            "磁盘行数必须等于内存条数（曾是 3 vs 5）",
        )
        eq(
            [m["role"] for m in disk.messages],
            [m["role"] for m in s.messages],
            "角色序列应一致",
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_persist_does_not_touch_memory():
    tmp = tempfile.mkdtemp()
    try:
        s = session_mod.Session.create("twice", session_dir=tmp)
        s.append_message({"role": "user", "content": "one"})
        eq(len(s.messages), 1, "append_message 追加一条")

        s.persist(s.messages[0])
        eq(len(s.messages), 1, "persist 不该再追加到内存")
        disk = session_mod.Session.load(s.path)
        eq(len(disk.messages), 2, "磁盘应有 2 行（同一内容写了两遍）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_replace_messages_keeps_list_identity():
    tmp = tempfile.mkdtemp()
    try:
        s = session_mod.Session.create("ident", session_dir=tmp)
        s.append("user", "a")
        identity = id(s.messages)

        s.replace_messages([{"role": "user", "content": "only"}])
        eq(id(s.messages), identity, "列表对象身份必须不变")

        s.append_message({"role": "assistant", "content": "after compact"})
        disk = session_mod.Session.load(s.path)
        eq(len(disk.messages), len(s.messages), "追加后仍应一致")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _make_history(turns):
    messages = []
    for i in range(turns):
        messages.append({"role": "user", "content": f"question {i}"})
        messages.append({
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": f"call_{i}", "type": "function",
                "function": {"name": "bash", "arguments": f'{{"command": "echo {i}"}}'},
            }],
        })
        messages.append({
            "role": "tool",
            "tool_call_id": f"call_{i}",
            "content": f"output {i}",
        })
    return messages


def test_token_estimate_grows():
    small = _make_history(1)
    big = _make_history(10)
    truthy(
        compaction.estimate_tokens(big) > compaction.estimate_tokens(small),
        "更长的历史应估出更多 token",
    )


def test_is_turn_start_distinguishes_tool_results():
    user_ask = {"role": "user", "content": "hi"}
    tool_msg = {"role": "tool", "tool_call_id": "call_x", "content": "out"}
    assistant = {"role": "assistant", "content": "yo"}

    truthy(compaction.is_turn_start(user_ask), "纯文本 user 是轮次开头")
    truthy(not compaction.is_turn_start(tool_msg), "工具结果不是轮次开头")
    truthy(not compaction.is_turn_start(assistant), "assistant 不是轮次开头")


def test_cut_index_never_orphans_tool_results():
    messages = _make_history(10)
    cut = compaction.find_cut_index(messages, keep_recent_turns=3)
    truthy(cut is not None, "10 轮历史应能找到切点")
    truthy(compaction.is_turn_start(messages[cut]), "切点必须落在轮次开头")

    back = messages[cut:]
    used = set()
    for msg in back:
        for tc in msg.get("tool_calls") or []:
            used.add(tc["id"])
    for msg in back:
        if msg.get("role") == "tool":
            truthy(
                msg["tool_call_id"] in used,
                f"孤儿 tool 消息: {msg['tool_call_id']}",
            )


def test_cut_index_none_when_too_short():
    messages = _make_history(2)
    eq(compaction.find_cut_index(messages, keep_recent_turns=3), None, "太短不压")


def test_compact_preserves_pairs_and_reduces():
    messages = _make_history(10)
    before_tokens = compaction.estimate_tokens(messages)

    fake = FakeLLM([llm.fake_text_response("SUMMARY: user was doing stuff")])
    with fake:
        compressed = compaction.compact(messages, keep_recent_turns=3)

    truthy(len(compressed) < len(messages), "压缩后消息应更少")
    truthy(
        compaction.estimate_tokens(compressed) < before_tokens,
        "压缩后 token 应更少",
    )
    eq(compressed[0]["role"], "user", "摘要用 user 角色")
    truthy("SUMMARY" in compressed[0]["content"], "摘要内容来自模型")

    _assert_pairs(compressed)
    truthy(len(fake.calls) == 1, "摘要应恰好调用模型一次")


def test_compaction_hook_replaces_in_place():
    fake = FakeLLM([llm.fake_text_response("condensed")])
    hook = agent_mod.make_compaction_hook()
    messages = _make_history(10)
    identity = id(messages)

    with fake:
        import os as _os
        old = _os.environ.get("MINI_AGENT_MAX_CONTEXT_TOKENS")
        try:
            from .config import CONFIG
            original = CONFIG["max_context_tokens"]
            CONFIG["max_context_tokens"] = 100
            CONFIG["compaction_reserve_tokens"] = 0
            try:
                hook(messages)
            finally:
                CONFIG["max_context_tokens"] = original
        finally:
            if old is None:
                _os.environ.pop("MINI_AGENT_MAX_CONTEXT_TOKENS", None)
            else:
                _os.environ["MINI_AGENT_MAX_CONTEXT_TOKENS"] = old

    eq(id(messages), identity, "列表对象身份必须保持不变")
    truthy(len(messages) < 30, "内容应被压缩")
    _assert_pairs(messages)


def test_prompt_has_three_layers():
    text = prompt.build_system_prompt(skills=[])
    truthy("coding agent" in text, "骨架层")
    truthy("working directory" in text, "环境层")
    truthy(os.getcwd().replace("\\", "/")[:6] in text.replace("\\", "/"), "应含工作目录")


def test_prompt_loads_skills_from_disk():
    tmp = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(tmp, "skills"))
        with open(os.path.join(tmp, "skills", "commit.md"), "w",
                  encoding="utf-8") as f:
            f.write("Always write commit messages in the imperative mood.")

        loaded = prompt.load_skills(tmp)
        eq(len(loaded), 1, "应加载 1 个技能")
        eq(loaded[0]["name"], "commit", "技能名去扩展名")

        text = prompt.build_system_prompt(skills=loaded, root=tmp)
        truthy("imperative mood" in text, "技能正文应进入提示词")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_prompt_missing_skills_dir_is_ok():
    tmp = tempfile.mkdtemp()
    try:
        eq(prompt.load_skills(tmp), [], "缺目录应返回空列表")
        text = prompt.build_system_prompt(skills=None, root=tmp)
        truthy("coding agent" in text, "仍应能拼出提示词")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_signal_is_thread_safe():
    from .control import Aborted, Signal

    signal = Signal()
    raised = []

    def worker():
        try:
            for _ in range(200):
                signal.check()
                time.sleep(0.005)
        except Aborted:
            raised.append(True)

    t = threading.Thread(target=worker)
    t.start()
    time.sleep(0.05)
    signal.abort("from main thread")
    t.join(timeout=2)

    truthy(raised, "子线程应收到中断")


def test_steering_drain_is_atomic():
    from .control import SteeringQueue

    q = SteeringQueue()
    q.push("one")
    q.push("two")
    eq(q.drain(), ["one", "two"], "应取走两条")
    eq(q.drain(), [], "第二次应为空")


def test_steering_ignores_blank():
    from .control import SteeringQueue

    q = SteeringQueue()
    q.push("   ")
    q.push("")
    eq(q.drain(), [], "空白应被忽略")


GROUPS = [
    ("工具 dispatch（第 2 章）", [
        test_dispatch_unknown_tool,
        test_dispatch_routes_by_name,
        test_tool_error_becomes_text,
        test_path_escape_blocked,
    ]),
    ("变更队列（第 3 章）", [
        test_queue_serializes_same_file,
        test_queue_parallel_across_files,
        test_queue_key_normalizes_path,
        test_queue_uses_realpath_for_symlink,
        test_edit_requires_unique_match,
    ]),
    ("主循环（第 1、3、4 章）", [
        test_loop_executes_tool_then_stops,
        test_loop_pairs_every_tool_use,
        test_loop_max_turns,
        test_loop_abort_stops_early,
        test_loop_abort_after_tool_use_still_pairs,
        test_loop_steering_inserted,
        test_tools_are_parallel_but_ordered,
    ]),
    ("会话持久化（第 5 章）", [
        test_session_roundtrip,
        test_session_jsonl_survives_corrupt_line,
        test_session_resume_feeds_history_to_model,
        test_replace_messages_rewrites_disk,
        test_session_memory_and_disk_stay_in_sync,
        test_persist_does_not_touch_memory,
        test_replace_messages_keeps_list_identity,
    ]),
    ("上下文压缩（第 6 章）", [
        test_token_estimate_grows,
        test_is_turn_start_distinguishes_tool_results,
        test_cut_index_never_orphans_tool_results,
        test_cut_index_none_when_too_short,
        test_compact_preserves_pairs_and_reduces,
        test_compaction_hook_replaces_in_place,
    ]),
    ("系统提示词（第 7 章）", [
        test_prompt_has_three_layers,
        test_prompt_loads_skills_from_disk,
        test_prompt_missing_skills_dir_is_ok,
    ]),
    ("中断控制（第 4 章）", [
        test_signal_is_thread_safe,
        test_steering_drain_is_atomic,
        test_steering_ignores_blank,
    ]),
]
def _chapter_number(text):
    if not text:
        return None
    m = re.search(r"第\s*(\d+)\s*章", text)
    if m:
        return int(m.group(1))
    if text.strip().isdigit():
        return int(text.strip())
    return None


def _chapters_in_title(title):
    nums = set()
    for group in re.findall(r"第\s*([\d、,\s]+)\s*章", title):
        for part in re.split(r"[、,\s]+", group):
            if part.isdigit():
                nums.add(int(part))
    return nums


def main(argv=None):
    setup_console()
    argv = sys.argv[1:] if argv is None else argv

    raw = argv[0] if argv else None
    wanted = _chapter_number(raw)
    groups = GROUPS
    if wanted is not None:
        groups = [g for g in GROUPS if wanted in _chapters_in_title(g[0])]
        if not groups:
            print(f"{RED}没有匹配 '{raw}' 的分组{RESET}")
            print("可用的分组：")
            for title, _ in GROUPS:
                print(f"  {title}")
            return 1

    total_checks = sum(len(g[1]) for g in groups)
    print(f"\n{BOLD}mini_agent 离线自检{RESET}")
    hint = f"（只跑匹配 '{raw}' 的 {len(groups)} 组）" if wanted is not None else ""
    print(f"{DIM}不需要 API key，不联网。共 {total_checks} 项检查。{hint}{RESET}\n")

    for title, tests in groups:
        print(f"{BOLD}{title}{RESET}")
        for fn in tests:
            check(fn.__name__, fn)
        print()

    total = len(PASSED) + len(FAILED)
    print("─" * 60)
    if FAILED:
        print(f"{RED}{BOLD}{len(FAILED)}/{total} 项失败{RESET}")
        for name, e in FAILED:
            print(f"  · {name}: {e}")
        return 1
    print(f"{GREEN}{BOLD}全部 {total} 项通过{RESET}")
    print(f"{DIM}现在可以设置 OPENAI_API_KEY 后运行 python -m mini_agent{RESET}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
