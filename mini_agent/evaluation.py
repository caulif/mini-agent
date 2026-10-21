import argparse
import json
from pathlib import Path
import statistics
import tempfile
import time

from .journal import Journal
from .llm import fake_text_response, fake_tool_response
from .runtime import Runtime


def evaluate(repeats=3):
    if type(repeats) is not int or repeats < 1:
        raise ValueError('repeats must be a positive integer')
    rows = []
    for task in ('read', 'write_edit', 'deny_write'):
        for repeat in range(1, repeats + 1):
            with tempfile.TemporaryDirectory(prefix='mini-agent-eval-') as tmp:
                root = Path(tmp)
                (root / 'input.txt').write_text('alpha\nbeta\n', encoding='utf-8')
                journal = Journal.create(root / '.mini_agent' / 'run.jsonl', root)
                requests, tool_count = 0, 0
                def provider(messages, tools, **kwargs):
                    nonlocal requests
                    requests += 1
                    if requests == 1:
                        if task == 'read':
                            return fake_tool_response(('read', {'path': 'input.txt', 'offset': 2, 'limit': 1}))
                        return fake_tool_response(('write', {'path': 'answer.txt', 'content': 'hello'}))
                    if task == 'write_edit' and requests == 2:
                        return fake_tool_response(('edit', {'path': 'answer.txt',
                                                           'old_string': 'hello', 'new_string': 'hello world'}))
                    return fake_text_response(messages[-1]['content'])
                def emit(kind, **kw):
                    nonlocal tool_count
                    if kind == 'tool_start':
                        tool_count += 1
                start = time.perf_counter()
                messages, error_kind = [], None
                try:
                    messages = Runtime(journal, provider=provider, allow_write=task == 'write_edit',
                                       emit=emit).run('Execute the task contract', max_turns=4)
                except Exception as error:
                    error_kind = type(error).__name__
                elapsed = time.perf_counter() - start
                answer = root / 'answer.txt'

                passed = not error_kind and bool(messages) and ((task == 'read' and messages[-1]['content'] == '2: beta\n') or
                          (task == 'write_edit' and answer.is_file() and answer.read_text(encoding='utf-8') == 'hello world') or
                          (task == 'deny_write' and not answer.exists() and
                           'PermissionError' in messages[-1]['content']))
                rows.append({'task': task, 'repeat': repeat, 'passed': passed,
                             'error': error_kind,
                             'requests': requests, 'tools': tool_count, 'elapsed_s': elapsed,
                             'tokens': None, 'cost': None})
    return {'mode': 'scripted-control-flow', 'real_model': False,
            'attempts': rows, 'summary': {'passed': sum(r['passed'] for r in rows),
            'total': len(rows), 'pass_rate': sum(r['passed'] for r in rows) / len(rows),
            'mean_elapsed_s': statistics.mean(r['elapsed_s'] for r in rows)},
            'limitations': ['Scripted providers do not measure model accuracy', 'Real tokens and costs were not measured',
                            'This small task set is not a general coding benchmark']}


def main():
    parser = argparse.ArgumentParser(description='Run a deterministic offline control-flow evaluation')
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = evaluate(args.repeats)
    value = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(value + '\n', encoding='utf-8')
    print(value)
    return 0 if report['summary']['passed'] == report['summary']['total'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
