# -*- coding: utf-8 -*-
"""
mini_agent · 命令行交互（第 1 章建立，后续各章加命令）

职责只有一个：把主循环接到人的键盘上。

它做的事：
  - 读你的输入
  - 把输入交给 agent_loop 去跑
  - 把循环报上来的事件（emit）打成人能看懂的样子
  - 让你在 agent 跑的时候还能按 Ctrl+C 中断、或者再打一句话

它**不**做的事：任何 agent 逻辑。所有智能都在 agent.py 和 tools.py 里。
这是有意的分层 —— 换一个界面（TUI、网页）时，这一层整个换掉，
下面一行都不用动。

────────────────────────────────────────────────────────────
交互设计说明（为什么这么绕）：

agent 跑一轮可能要几十秒。如果主线程傻等，你就既不能中断、
也不能补话。所以：agent 跑在**后台线程**，主线程始终停在
input() 上等你。

  - 你按 Ctrl+C     → KeyboardInterrupt → signal.abort()
  - 你打一句话回车  → 若 agent 还在跑，进转向队列（第 4 章）
                    若 agent 已结束，作为下一条用户消息
────────────────────────────────────────────────────────────
"""

import argparse
import os
import sys
import threading

from . import agent as agent_mod
from . import compaction, prompt, session as session_mod
from .config import CONFIG, require_api_key, setup_console
from .control import Signal, SteeringQueue

# ── 终端颜色（Windows 10+ 的终端支持 ANSI 转义）────────────────
DIM = "\033[2m"
BOLD = "\033[1m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
RED = "\033[31m"
RESET = "\033[0m"


HELP_TEXT = """\
可用命令：
  /help            显示这份帮助
  /new             开一段新对话（当前这段仍留在磁盘上）
  /list            列出所有历史会话
  /resume <id>     切换到某个历史会话
  /skills          看看加载了哪些技能文件
  /compact         立刻压缩一次上下文（不等它自己触发）
  /stats           当前会话的统计信息
  /exit            退出（Ctrl+C 也行）
"""


# ══════════════════════════════════════════════════════════════
# 事件渲染
# ══════════════════════════════════════════════════════════════

def make_renderer(verbose_tools=False):
    """造一个把循环事件打成终端文本的函数。

    这就是 agent.py 里那个 emit 回调的 CLI 实现。
    同一份循环，测试时换成"收集到列表里"，这里换成"打彩色字"。
    """

    def render(kind, **kw):
        if kind == "text":
            # 模型的文字回答：加个标记，和工具输出区分开
            print(f"\n{BOLD}{kw['text']}{RESET}")
        elif kind == "tool_start":
            args = agent_mod._brief(kw.get("input"))
            print(f"{DIM}  ▸ {kw['name']}({args}){RESET}")
        elif kind == "tool_end":
            out = kw.get("output") or ""
            if verbose_tools:
                body = out if len(out) < 800 else out[:800] + " …"
                print(f"{DIM}{body}{RESET}")
        elif kind == "steering":
            print(f"{YELLOW}  ↳ 已插入你的补充指令{RESET}")
        elif kind == "aborted":
            print(f"{RED}[已中断] {kw.get('reason', '')}{RESET}")
        elif kind == "max_turns":
            print(f"{YELLOW}[达到轮数上限 {kw['limit']}，本轮结束]{RESET}")
        elif kind == "compaction_start":
            print(f"{DIM}  ◇ 上下文约 {kw['tokens']} tokens，开始压缩…{RESET}")
        elif kind == "compaction_end":
            print(
                f"{DIM}  ◇ 压缩完成：{kw['before']} → {kw['after']} tokens{RESET}"
            )

    return render


# ══════════════════════════════════════════════════════════════
# 一次对话
# ══════════════════════════════════════════════════════════════

class App:
    """把各模块拼装起来的地方。"""

    def __init__(self, args):
        self.args = args
        self.session = self._open_session()
        self.signal = Signal()
        self.steering = SteeringQueue()
        self.executor = agent_mod.make_executor()
        self.render = make_renderer(args.verbose)
        self.skills = prompt.load_skills()
        self.system_prompt = prompt.build_system_prompt(self.skills)
        self.before_request = self._make_before_request()

    # ── 组装 ──────────────────────────────────────────────────

    def _open_session(self):
        """按命令行参数决定：新建、续用最近、还是打开指定 id。"""
        if self.args.resume:
            path = self._find_session_path(self.args.resume)
            loaded = session_mod.Session.load(path)
            print(f"{DIM}已恢复会话 {loaded.id}（{len(loaded.messages)} 条消息）{RESET}")
            return loaded

        if self.args.continue_:
            path = session_mod.latest_session()
            if path:
                loaded = session_mod.Session.load(path)
                print(f"{DIM}继续上次的会话 {loaded.id}（{len(loaded.messages)} 条消息）{RESET}")
                return loaded
            print(f"{DIM}没有历史会话，新建一段{RESET}")

        return session_mod.Session.create()

    def _find_session_path(self, session_id):
        directory = CONFIG["session_dir"]
        path = os.path.join(directory, f"{session_id}.jsonl")
        if os.path.exists(path):
            return path
        # 允许只给前缀（时间戳开头那段就能唯一确定）
        for candidate in session_mod.list_sessions(directory):
            if os.path.basename(candidate).startswith(session_id):
                return candidate
        raise SystemExit(f"找不到会话: {session_id}（用 /list 看看有哪些）")

    def _make_before_request(self):
        """把第 6 章的压缩钩子包一层，顺便让压缩结果落盘。

        为什么要包这一层：压缩是"整体替换历史"，而 session 的
        追加钩子（on_append）只在"追加"时触发。压缩之后必须重写
        磁盘文件，否则重启就从压缩前的旧历史开始了。
        """
        compact_hook = agent_mod.make_compaction_hook(
            signal=self.signal, emit=self.render
        )

        def before_request(messages):
            before_len = len(messages)
            compact_hook(messages)
            if len(messages) != before_len:
                self.session.replace_messages(messages)

        return before_request

    # ── 跑一轮 ────────────────────────────────────────────────

    def run_turn(self, user_text):
        """把你这一句交给 agent，在后台线程里跑，期间你仍能操作。"""
        self.signal.reset()
        self.session.append("user", user_text)

        done = threading.Event()

        def worker():
            try:
                agent_mod.agent_loop(
                    self.session.messages,
                    system=self.system_prompt,
                    signal=self.signal,
                    steering=self.steering,
                    max_turns=self.args.max_turns,
                    emit=self.render,
                    executor=self.executor,
                    on_append=self.session.persist,           # 第 5 章：只落盘
                    before_request=self.before_request,      # 第 6 章：压缩
                )
            except Exception as e:
                print(f"{RED}[出错] {type(e).__name__}: {e}{RESET}")
            finally:
                done.set()

        thread = threading.Thread(target=worker, daemon=True, name="agent")
        thread.start()

        # 主线程回到 input()：你可以打话，或者按 Ctrl+C。
        while not done.is_set():
            try:
                line = input(f"{DIM}（运行中…可补充指令，Ctrl+C 中断）{RESET} ").strip()
            except EOFError:
                self.signal.abort("stdin closed")
                break
            except KeyboardInterrupt:
                print(f"\n{YELLOW}正在中断…{RESET}")
                self.signal.abort("user pressed Ctrl+C")
                break

            if not line:
                continue
            if line.startswith("/"):
                self.handle_command(line)          # 命令立刻执行
            else:
                self.steering.push(line)           # 其余进转向队列
                print(f"{YELLOW}  ↳ 已排队，下一轮生效{RESET}")

        thread.join(timeout=1)

    # ── 命令 ──────────────────────────────────────────────────

    def handle_command(self, line):
        parts = line.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("/exit", "/quit", "/q"):
            raise SystemExit(0)
        if cmd == "/help":
            print(HELP_TEXT)
        elif cmd == "/new":
            self.session = session_mod.Session.create()
            print(f"{DIM}新会话: {self.session.id}{RESET}")
        elif cmd == "/list":
            files = session_mod.list_sessions()
            if not files:
                print("(还没有任何会话)")
            for path in files[:20]:
                name = os.path.basename(path)
                size = os.path.getsize(path)
                print(f"  {name}  ({size} bytes)")
        elif cmd == "/resume":
            if not arg:
                print("用法: /resume <会话id>")
            else:
                try:
                    self.session = session_mod.Session.load(self._find_session_path(arg))
                    print(f"{DIM}已切换到 {self.session.id}{RESET}")
                except SystemExit as e:
                    print(e)
        elif cmd == "/skills":
            if not self.skills:
                print(f"(没有技能文件。在 ./skills/ 下放 .md 文件即可)")
            for s in self.skills:
                print(f"  {s['name']}  ({len(s['body'])} 字符)")
            total = len(prompt.build_system_prompt(self.skills))
            print(f"{DIM}提示词合计 {total} 字符{RESET}")
        elif cmd == "/compact":
            before = compaction.estimate_tokens(self.session.messages)
            compacted = compaction.compact(self.session.messages, signal=self.signal)
            self.session.replace_messages(compacted)
            after = compaction.estimate_tokens(compacted)
            print(f"{DIM}压缩完成：{before} → {after} tokens{RESET}")
        elif cmd == "/stats":
            info = self.session.stats()
            info["tokens_estimate"] = compaction.estimate_tokens(self.session.messages)
            for k, v in info.items():
                print(f"  {k}: {v}")
        else:
            print(f"未知命令 {cmd}，/help 看帮助")

    # ── 主循环 ────────────────────────────────────────────────

    def repl(self):
        print(f"{BOLD}mini_agent{RESET} {DIM}（/help 看命令，Ctrl+C 中断，/exit 退出）{RESET}")
        print(f"{DIM}工作目录: {os.getcwd()}{RESET}")
        print(f"{DIM}模型: {CONFIG['model']}   会话: {self.session.id}{RESET}")

        while True:
            try:
                user_text = input(f"\n{BOLD}{CYAN}你 >{RESET} ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not user_text:
                continue
            if user_text.startswith("/"):
                try:
                    self.handle_command(user_text)
                except SystemExit:
                    break
                continue

            self.run_turn(user_text)

        self.executor.shutdown(wait=False)
        print(f"{DIM}会话已保存: {self.session.path}{RESET}")


# ══════════════════════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════════════════════

def build_parser():
    p = argparse.ArgumentParser(
        prog="mini_agent",
        description="一个最小的 coding agent（教学项目）",
    )
    p.add_argument("-c", "--continue", dest="continue_", action="store_true",
                   help="接着最近一次会话继续")
    p.add_argument("--resume", metavar="SESSION_ID",
                   help="恢复指定的历史会话")
    p.add_argument("--max-turns", type=int, default=None, metavar="N",
                   help="每轮对话最多调用模型几次（防失控）")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="把工具的输出也打出来")
    p.add_argument("-p", "--print", dest="oneshot", metavar="PROMPT",
                   help="一次性执行这个任务然后退出（不进入交互）")
    return p


def main(argv=None):
    setup_console()
    args = build_parser().parse_args(argv)
    require_api_key()

    if args.oneshot:
        # 非交互模式：跑完就退出，适合脚本里调用
        app = App(args)
        app.session.append("user", args.oneshot)
        agent_mod.agent_loop(
            app.session.messages,
            system=app.system_prompt,
            signal=app.signal,
            max_turns=args.max_turns,
            emit=app.render,
            executor=app.executor,
            on_append=app.session.persist,
            before_request=app.before_request,
        )
        return 0

    App(args).repl()
    return 0


if __name__ == "__main__":
    sys.exit(main())
