import argparse
import os
import sys
import threading

from . import agent as agent_mod
from . import compaction, prompt, session as session_mod
from .config import CONFIG, require_api_key, setup_console
from .control import Signal, SteeringQueue


DIM = "\033[2m"
BOLD = "\033[1m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
RED = "\033[31m"
RESET = "\033[0m"


HELP_TEXT = 'Available commands:\n  /help            Show this help\n  /new             Start a new conversation (keep the current one on disk)\n  /list            List saved sessions\n  /resume <id>     Switch to a saved session\n  /skills          List loaded skill files\n  /compact         Compact the context now\n  /stats           Show current session statistics\n  /exit            Exit (or press Ctrl+C)\n'


def make_renderer(verbose_tools=False):
    def render(kind, **kw):
        if kind == "text":
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
            print('{0}  ↳ Added your follow-up instruction{1}'.format(YELLOW, RESET))
        elif kind == "aborted":
            print('{0}[Aborted] {1}{2}'.format(RED, kw.get('reason', ''), RESET))
        elif kind == "max_turns":
            print('{0}[Turn limit reached: {1}; ending this turn]{2}'.format(YELLOW, kw['limit'], RESET))
        elif kind == "compaction_start":
            print('{0}  ◇ Context ~{1} tokens; compacting…{2}'.format(DIM, kw['tokens'], RESET))
        elif kind == "compaction_end":
            print(
                '{0}  ◇ Compacted: {1} → {2} tokens{3}'.format(DIM, kw['before'], kw['after'], RESET)
            )

    return render


class App:
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

    def _open_session(self):
        if self.args.resume:
            path = self._find_session_path(self.args.resume)
            loaded = session_mod.Session.load(path)
            print('{0}Resumed session {1} ({2} messages){3}'.format(DIM, loaded.id, len(loaded.messages), RESET))
            return loaded

        if self.args.continue_:
            path = session_mod.latest_session()
            if path:
                loaded = session_mod.Session.load(path)
                print('{0}Continuing session {1} ({2} messages){3}'.format(DIM, loaded.id, len(loaded.messages), RESET))
                return loaded
            print('{0}No previous session; starting a new one{1}'.format(DIM, RESET))

        return session_mod.Session.create()

    def _find_session_path(self, session_id):
        directory = CONFIG["session_dir"]
        path = os.path.join(directory, f"{session_id}.jsonl")
        if os.path.exists(path):
            return path

        for candidate in session_mod.list_sessions(directory):
            if os.path.basename(candidate).startswith(session_id):
                return candidate
        raise SystemExit('Session not found: {0} (use /list to see available sessions)'.format(session_id))

    def _make_before_request(self):
        compact_hook = agent_mod.make_compaction_hook(
            signal=self.signal, emit=self.render
        )

        def before_request(messages):
            before_len = len(messages)
            compact_hook(messages)
            if len(messages) != before_len:
                self.session.replace_messages(messages)

        return before_request

    def run_turn(self, user_text):
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
                    on_append=self.session.persist,
                    before_request=self.before_request,
                )
            except Exception as e:
                print('{0}[Error] {1}: {2}{3}'.format(RED, type(e).__name__, e, RESET))
            finally:
                done.set()

        thread = threading.Thread(target=worker, daemon=True, name="agent")
        thread.start()

        while not done.is_set():
            try:
                line = input('{0}(Running… add instructions, Ctrl+C to interrupt){1} '.format(DIM, RESET)).strip()
            except EOFError:
                self.signal.abort("stdin closed")
                break
            except KeyboardInterrupt:
                print('\n{0}Interrupting…{1}'.format(YELLOW, RESET))
                self.signal.abort("user pressed Ctrl+C")
                break

            if not line:
                continue
            if line.startswith("/"):
                self.handle_command(line)
            else:
                self.steering.push(line)
                print('{0}  ↳ Queued for the next model call{1}'.format(YELLOW, RESET))

        thread.join(timeout=1)

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
            print('{0}New session: {1}{2}'.format(DIM, self.session.id, RESET))
        elif cmd == "/list":
            files = session_mod.list_sessions()
            if not files:
                print('(No sessions yet)')
            for path in files[:20]:
                name = os.path.basename(path)
                size = os.path.getsize(path)
                print(f"  {name}  ({size} bytes)")
        elif cmd == "/resume":
            if not arg:
                print('Usage: /resume <session-id>')
            else:
                try:
                    self.session = session_mod.Session.load(self._find_session_path(arg))
                    print('{0}Switched to {1}{2}'.format(DIM, self.session.id, RESET))
                except SystemExit as e:
                    print(e)
        elif cmd == "/skills":
            if not self.skills:
                print('(No skills loaded. Add .md files under ./skills/)')
            for s in self.skills:
                print('  {0}  ({1} characters)'.format(s['name'], len(s['body'])))
            total = len(prompt.build_system_prompt(self.skills))
            print('{0}System prompt: {1} characters total{2}'.format(DIM, total, RESET))
        elif cmd == "/compact":
            before = compaction.estimate_tokens(self.session.messages)
            compacted = compaction.compact(self.session.messages, signal=self.signal)
            self.session.replace_messages(compacted)
            after = compaction.estimate_tokens(compacted)
            print('{0}Compacted: {1} → {2} tokens{3}'.format(DIM, before, after, RESET))
        elif cmd == "/stats":
            info = self.session.stats()
            info["tokens_estimate"] = compaction.estimate_tokens(self.session.messages)
            for k, v in info.items():
                print(f"  {k}: {v}")
        else:
            print('Unknown command {0}; use /help for help'.format(cmd))

    def repl(self):
        print('{0}mini_agent{1} {2}(/help for commands, Ctrl+C to interrupt, /exit to quit){3}'.format(BOLD, RESET, DIM, RESET))
        print('{0}Working directory: {1}{2}'.format(DIM, os.getcwd(), RESET))
        print('{0}Model: {1}   Session: {2}{3}'.format(DIM, CONFIG['model'], self.session.id, RESET))

        while True:
            try:
                user_text = input('\n{0}{1}You >{2} '.format(BOLD, CYAN, RESET)).strip()
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
        print('{0}Session saved: {1}{2}'.format(DIM, self.session.path, RESET))


def build_parser():
    p = argparse.ArgumentParser(
        prog="mini_agent",
        add_help=False,
        description='A minimal coding agent (educational project)',
    )
    p.add_argument("-h", "--help", action="help", help='Show this help message and exit')
    p.add_argument("-c", "--continue", dest="continue_", action="store_true",
                   help='Continue the most recent session')
    p.add_argument("--resume", metavar="SESSION_ID",
                   help='Resume a saved session')
    p.add_argument("--max-turns", type=int, default=None, metavar="N",
                   help='Maximum model calls per conversation turn')
    p.add_argument("-v", "--verbose", action="store_true",
                   help='Show tool output')
    p.add_argument("-p", "--print", dest="oneshot", metavar="PROMPT",
                   help='Run a task once and exit without entering interactive mode')
    return p


def main(argv=None):
    setup_console()
    args = build_parser().parse_args(argv)
    require_api_key()

    if args.oneshot:
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
