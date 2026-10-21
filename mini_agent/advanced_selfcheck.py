import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

from .control import Aborted, Signal
from .journal import Journal, RecoveryRequired
from .llm import fake_text_response, fake_tool_response
from .permissions import WorkspaceTools
from .retry import RequestFailure
from .runtime import Runtime
from .streaming import call_stream, collect_stream


def sse(*deltas, finish='stop', done=True):
    chunks = [{'choices': [{'index': 0, 'delta': d, 'finish_reason': None}]} for d in deltas]
    chunks.append({'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}]})
    data = ''.join('data: ' + json.dumps(c, ensure_ascii=False) + '\r\n\r\n' for c in chunks)
    if done:
        data += 'data: [DONE]\r\n\r\n'
    return data.encode('utf-8').splitlines(keepends=True)


class AdvancedChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.journal = Journal.create(self.root / '.mini_agent' / 'test.jsonl', self.root)

    def test_real_loop_write_edit_and_resume_finished(self):
        responses = iter([fake_tool_response(('write', {'path': 'a.txt', 'content': 'A'})),
                          fake_tool_response(('edit', {'path': 'a.txt', 'old_string': 'A', 'new_string': 'B'})),
                          fake_text_response('done')])
        runtime = Runtime(self.journal, provider=lambda *a, **kw: next(responses), allow_write=True)
        messages = runtime.run('task')
        self.assertEqual((self.root / 'a.txt').read_text(), 'B')
        self.assertEqual([m['role'] for m in messages], ['user', 'assistant', 'tool', 'assistant', 'tool', 'assistant'])
        runtime.provider = lambda *a, **kw: self.fail('不应重问模型')
        self.assertEqual(runtime.run(), messages)

    def test_default_deny_and_no_shell(self):
        tools = WorkspaceTools(self.root)
        self.assertEqual([s['function']['name'] for s in tools.definitions()], ['read'])
        for name in ('write', 'bash'):
            with self.assertRaises(PermissionError):
                tools.submit(name, {'path': 'x', 'content': 'bad'}, signal=Signal())
        self.assertFalse((self.root / 'x').exists())

    def test_invalid_finish_reason_never_executes_tools(self):
        for case in ('missing', 'null', 'unknown', 'stop_with_calls', 'tool_calls_without_calls'):
            with self.subTest(case=case):
                root = self.root / case
                root.mkdir()
                journal = Journal.create(root / '.mini_agent' / 'test.jsonl', root)
                response = fake_tool_response(('write', {'path': 'x', 'content': 'bad'}))
                choice = response['choices'][0]
                if case == 'missing':
                    del choice['finish_reason']
                elif case == 'null':
                    choice['finish_reason'] = None
                elif case == 'unknown':
                    choice['finish_reason'] = 'unknown'
                elif case == 'stop_with_calls':
                    choice['finish_reason'] = 'stop'
                else:
                    choice['message']['tool_calls'] = []
                    choice['message']['content'] = 'done'
                runtime = Runtime(journal, provider=lambda *a, **kw: response, allow_write=True)
                with patch.object(runtime.tools, 'submit', wraps=runtime.tools.submit) as submit:
                    with self.assertRaises(RequestFailure):
                        runtime.run('task')
                    submit.assert_not_called()
                self.assertFalse((root / 'x').exists())
                events = journal.read()
                self.assertFalse(any(e['kind'] in {'started', 'result'} for e in events))
                self.assertFalse(any(e['kind'] == 'message' and e['message']['role'] == 'assistant'
                                     for e in events))

    def test_traversal_absolute_and_reserved_denied(self):
        tools = WorkspaceTools(self.root, allow_write=True)
        for value in ('../escape', str(self.root.parent / 'escape'), '.mini_agent/test.jsonl',
                      '.git/config', 'x:secret', 'NUL.txt'):
            with self.subTest(path=value), self.assertRaises(PermissionError):
                tools.submit('write', {'path': value, 'content': 'bad'}, signal=Signal())

    def test_directory_link_escape_denied(self):
        with tempfile.TemporaryDirectory() as outside:
            link = self.root / 'link'
            if sys.platform == 'win32':
                result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), outside], capture_output=True)
                if result.returncode:
                    self.skipTest('无法创建 junction')
                self.addCleanup(lambda: link.rmdir() if link.exists() else None)
            else:
                link.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(PermissionError):
                WorkspaceTools(self.root).path('link/secret')

    def test_stop_before_tool_no_artifact(self):
        runtime = Runtime(self.journal, provider=lambda *a, **kw: fake_text_response('unused'), allow_write=True)
        runtime.signal.abort('test')
        with self.assertRaises(Aborted):
            runtime.run('task')
        self.assertEqual(len(self.journal.read()), 1)

    def test_tail_corruption_blocks(self):
        with self.journal.path.open('ab') as out:
            out.write(b'{"kind":')
        before = self.journal.path.read_bytes()
        with self.assertRaises(RecoveryRequired):
            Runtime(self.journal).run('task')
        self.assertEqual(self.journal.path.read_bytes(), before)

    def crash(self, phase):
        code = '''
import os, sys
from mini_agent.journal import Journal
from mini_agent.runtime import Runtime
from mini_agent.llm import fake_tool_response
journal = Journal(sys.argv[1])
original = journal.append
def append(kind, **fields):
    if kind == 'result' and sys.argv[2] == 'before_result':
        os._exit(71)
    original(kind, **fields)
    if kind == 'result' and sys.argv[2] == 'after_result':
        os._exit(72)
journal.append = append
Runtime(journal, allow_write=True, provider=lambda *a, **kw: fake_tool_response(
    ('write', {'path':'artifact.txt', 'content':'once'}))).run('task')
'''
        return subprocess.run([sys.executable, '-B', '-c', code, str(self.journal.path), phase],
                              cwd=Path(__file__).resolve().parents[1], capture_output=True)

    def test_real_crash_unknown_blocks_then_explicit_ack(self):
        result = self.crash('before_result')
        self.assertEqual(result.returncode, 71, result.stderr)
        self.assertEqual((self.root / 'artifact.txt').read_text(), 'once')
        runtime = Runtime(self.journal, provider=lambda *a, **kw: fake_text_response('inspect'))
        before = self.journal.path.read_bytes()
        with self.assertRaises(RecoveryRequired):
            runtime.run()
        self.assertEqual(self.journal.path.read_bytes(), before)
        messages = runtime.run(acknowledge_unknown=True)
        self.assertIn('outcome unknown', messages[-2]['content'])
        self.assertEqual(sum(e['kind'] == 'started' for e in self.journal.read()), 1)

    def test_real_crash_after_result_reuses_saved_output(self):
        result = self.crash('after_result')
        self.assertEqual(result.returncode, 72, result.stderr)
        messages = Runtime(self.journal, provider=lambda *a, **kw: fake_text_response('done')).run()
        self.assertIn('Wrote', messages[-2]['content'])
        self.assertEqual(sum(e['kind'] == 'started' for e in self.journal.read()), 1)

    def test_unstarted_intent_not_replayed(self):
        call = fake_tool_response(('write', {'path': 'x', 'content': 'bad'}))['choices'][0]['message']
        self.journal.append('message', message=call)
        messages = Runtime(self.journal, provider=lambda *a, **kw: fake_text_response('done')).run()
        self.assertIn('not executed', messages[1]['content'])
        self.assertFalse((self.root / 'x').exists())

    def test_stream_unicode_and_fragmented_arguments(self):
        lines = sse({'role': 'assistant', 'content': '你好'},
            {'tool_calls': [{'index': 0, 'id': 'call_1', 'type': 'function',
                             'function': {'name': 'write', 'arguments': '{"path":'}}]},
            {'tool_calls': [{'index': 0, 'function': {'arguments': '"a", "content":"中"}'}}]},
            finish='tool_calls')
        preview = []
        response = collect_stream(lines, signal=Signal(), on_delta=preview.append)
        self.assertEqual(preview, ['你好'])
        self.assertEqual(json.loads(response['choices'][0]['message']['tool_calls'][0]['function']['arguments'])['content'], '中')

    def test_partial_stream_never_enters_history_or_tools(self):
        lines = sse({'content': 'preview'}, done=False)
        runtime = Runtime(self.journal, provider=lambda *a, signal, **kw: collect_stream(lines, signal=signal))
        with self.assertRaises(RequestFailure):
            runtime.run('task')
        self.assertEqual([e['kind'] for e in self.journal.read()], ['header', 'message'])

    def test_stream_malformed_truncated_and_missing_finish(self):
        cases = [sse({'content': 'x'}, finish='length'),
                 [b'data: [DONE]\n', b'\n'],
                 [b'data: {bad}\n', b'\n'],
                 sse({'tool_calls': [{'index': 0, 'id': 'c', 'function': {'name': 'read', 'arguments': '{'}}]}, finish='tool_calls')]
        for lines in cases:
            with self.subTest(lines=lines), self.assertRaises(RequestFailure):
                collect_stream(lines, signal=Signal())

    def test_stream_abort(self):
        signal = Signal()
        with self.assertRaises(Aborted):
            collect_stream(sse({'content': 'one'}, {'content': 'two'}), signal=signal,
                           on_delta=lambda text: signal.abort('stop'))

    def test_actual_local_sse_http(self):
        payload = b''.join(sse({'content': 'local SSE'}))
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if not body.get('stream'):
                    self.send_error(400)
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
                self.end_headers()
                self.wfile.write(payload)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            from .config import CONFIG
            with patch.dict(CONFIG, base_url=f'http://127.0.0.1:{server.server_port}', api_key='offline-test'):
                response = call_stream([], [], signal=Signal(),
                    opener=urllib.request.build_opener(urllib.request.ProxyHandler({})).open)
            self.assertEqual(response['choices'][0]['message']['content'], 'local SSE')
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_sessions_and_plugins_are_independent(self):
        other = self.root / 'other'
        other.mkdir()
        j2 = Journal.create(other / 'run.jsonl', other)
        a, b = Runtime(self.journal), Runtime(j2)
        schema = {'type': 'function', 'function': {'name': 'hello', 'description': 'trusted',
                  'parameters': {'type': 'object', 'properties': {}}}}
        a.tools.register('hello', schema, lambda inp, **kw: 'hello', mutating=False)
        self.assertNotIn('hello', b.tools.registry)
        a.signal.abort('one')
        self.assertFalse(b.signal.aborted)
        self.assertNotEqual(a.tools.root, b.tools.root)
        with self.assertRaises(ValueError):
            a.tools.register('hello', schema, lambda inp: '')

    def test_duplicate_provider_ids_rejected_before_second_execution(self):
        def provider(*args, **kwargs):
            return fake_tool_response(('write', {'path': 'a', 'content': 'once'}), ids=['same'])
        with self.assertRaises(RecoveryRequired):
            Runtime(self.journal, provider=provider, allow_write=True).run('task')
        self.assertEqual(sum(e['kind'] == 'started' for e in self.journal.read()), 1)

    def test_second_process_cannot_write_locked_session(self):
        code = 'import sys; from mini_agent.journal import Journal\nwith Journal(sys.argv[1]).locked(): pass'
        with self.journal.locked():
            result = subprocess.run([sys.executable, '-B', '-c', code, str(self.journal.path)],
                cwd=Path(__file__).resolve().parents[1], capture_output=True)
        self.assertNotEqual(result.returncode, 0)

    def test_intent_persistence_failure_has_no_side_effect(self):
        runtime = Runtime(self.journal, allow_write=True, provider=lambda *a, **kw:
                          fake_tool_response(('write', {'path': 'x', 'content': 'bad'})))
        original = self.journal.append
        def append(kind, **fields):
            if kind == 'message' and fields['message']['role'] == 'assistant':
                raise OSError('disk full')
            original(kind, **fields)
        with patch.object(self.journal, 'append', side_effect=append), self.assertRaises(OSError):
            runtime.run('task')
        self.assertFalse((self.root / 'x').exists())

    def test_plugin_failure_is_paired_without_leaking_exception_text(self):
        responses = iter([fake_tool_response(('broken', {})), fake_text_response('done')])
        runtime = Runtime(self.journal, provider=lambda *a, **kw: next(responses))
        def broken(inp, *, signal):
            raise RuntimeError('secret-content')
        runtime.tools.register('broken', {'function': {'name': 'broken'}}, broken, mutating=False)
        messages = runtime.run('task')
        self.assertEqual(messages[-2]['content'], 'Error: RuntimeError')
        self.assertNotIn('secret-content', self.journal.path.read_text(encoding='utf-8'))

    def test_evaluation_records_runtime_failures(self):
        from .evaluation import evaluate
        with patch('mini_agent.evaluation.Runtime.run', side_effect=RuntimeError('private-detail')):
            report = evaluate(1)
        self.assertEqual(report['summary']['passed'], 0)
        self.assertEqual(report['summary']['total'], 3)
        self.assertTrue(all(r['error'] == 'RuntimeError' for r in report['attempts']))
        self.assertNotIn('private-detail', json.dumps(report))


if __name__ == '__main__':
    unittest.main(verbosity=2)
