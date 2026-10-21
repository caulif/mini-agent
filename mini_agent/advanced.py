import argparse
from pathlib import Path
import uuid

from .config import require_api_key, setup_console
from .journal import Journal, RecoveryRequired
from .runtime import Runtime
from .retry import RequestFailure


def main(argv=None):
    setup_console()
    parser = argparse.ArgumentParser(description='mini agent-2: read-only by default, no shell tool')
    parser.add_argument('prompt', nargs='?')
    parser.add_argument('--root', default='.', help='Root for new sessions; resume uses the journal root')
    parser.add_argument('--resume', type=Path, help='Resume a journal (not a v1 Session JSONL file)')
    parser.add_argument('--status', action='store_true', help='Print execution stages without model requests')
    parser.add_argument('--stream', action='store_true')
    parser.add_argument('--allow-write', action='store_true', help='Allow write/edit for this run')
    parser.add_argument('--acknowledge-unknown', action='store_true', help='Acknowledge inspected unknown outcomes and continue')
    parser.add_argument('--max-turns', type=int, default=8)
    args = parser.parse_args(argv)
    if args.status:
        if not args.resume:
            parser.error('--status requires --resume')
        journal = Journal(args.resume)
        with journal.locked():
            for event in journal.read():
                if event['kind'] in {'started', 'result'}:
                    print(event['kind'], event['id'])
        return 0
    if args.resume is None and not args.prompt:
        parser.error('A new session requires task text')
    if args.max_turns < 1:
        parser.error('--max-turns must be positive')
    require_api_key()
    journal = Journal(args.resume) if args.resume else Journal.create(
        Path(args.root) / '.mini_agent' / 'advanced' / (uuid.uuid4().hex + '.jsonl'), args.root)
    print('Journal: ', journal.path)
    print('Streamed text is a preview; only complete validated responses enter history.' if args.stream else 'Non-streaming mode')
    finished = False
    def render(kind, **kw):
        nonlocal finished
        if kind == 'text_delta':
            print(kw['text'], end='', flush=True)
        elif kind == 'text' and not args.stream:
            print(kw['text'])
        elif kind == 'done':
            finished = True
        elif kind in {'aborted', 'max_turns'}:
            print('\nRun stopped: ', kind)
    runtime = Runtime(journal, allow_write=args.allow_write, stream=args.stream, emit=render)
    try:
        messages = runtime.run(args.prompt, max_turns=args.max_turns,
                              acknowledge_unknown=args.acknowledge_unknown)
        if messages[-1]['role'] == 'assistant' and not messages[-1].get('tool_calls'):
            finished = True
        return 0 if finished else 2
    except KeyboardInterrupt:
        runtime.signal.abort('Ctrl+C')
        print('\nStopped; inspect the journal before resuming. Preview text is not committed.')
        return 130
    except (RecoveryRequired, RuntimeError, OSError, ValueError) as error:
        print('\nRun incomplete: ', str(error) if isinstance(error, (RecoveryRequired, RequestFailure))
              else type(error).__name__)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
