from contextlib import contextmanager
import json
import os
from pathlib import Path


class RecoveryRequired(RuntimeError):
    pass


class Journal:
    def __init__(self, path):
        self.path = Path(path).resolve()

    @contextmanager
    def locked(self):
        with open(str(self.path) + '.lock', 'a+b') as lock:
            lock.seek(0)
            if not lock.read(1):
                lock.write(b'0')
                lock.flush()
            lock.seek(0)
            if os.name == 'nt':
                import msvcrt
                try:
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    raise RuntimeError('Session is in use by another runtime') from None
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                lock.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock, fcntl.LOCK_UN)

    @classmethod
    def create(cls, path, root):
        journal = cls(path)
        journal.path.parent.mkdir(parents=True, exist_ok=True)
        with journal.path.open('x', encoding='utf-8', newline='\n') as out:
            out.write(json.dumps({'kind': 'header', 'version': 1,
                                  'root': str(Path(root).resolve())}) + '\n')
            out.flush()
            os.fsync(out.fileno())
        return journal

    def read(self):
        events = []
        with self.path.open(encoding='utf-8') as src:
            for number, line in enumerate(src, 1):
                try:
                    event = json.loads(line)
                    if not line.endswith('\n') or not isinstance(event, dict):
                        raise ValueError()
                    if event.get('kind') not in {'header', 'message', 'started', 'result'}:
                        raise ValueError()
                    events.append(event)
                except (ValueError, TypeError):
                    raise RecoveryRequired(f'Journal line {number} is corrupt; preserve the original; no lines skipped') from None
        if not events or events[0].get('kind') != 'header' or events[0].get('version') != 1:
            raise RecoveryRequired('Journal is missing a supported header')
        return events

    def append(self, kind, **fields):
        record = json.dumps({'kind': kind, **fields}, ensure_ascii=False, allow_nan=False)
        with self.path.open('a', encoding='utf-8', newline='\n') as out:
            out.write(record + '\n')
            out.flush()
            os.fsync(out.fileno())

    def restore(self, *, acknowledge_unknown=False):
        events = self.read()
        messages, pending, starts, results, used = [], {}, set(), {}, set()
        for event in events[1:]:
            kind = event['kind']
            if kind == 'message':
                msg = event['message']
                role = msg.get('role')
                if role == 'assistant':
                    if pending:
                        raise RecoveryRequired('Previous tool results are incomplete')
                    for call in msg.get('tool_calls') or []:
                        key = call['id']
                        if key in used:
                            raise RecoveryRequired('Duplicate tool ID within session')
                        used.add(key)
                        pending[key] = call
                elif role == 'tool':
                    key = msg['tool_call_id']
                    if key not in pending:
                        raise RecoveryRequired('Tool result has no matching intent')
                    del pending[key]
                elif role != 'user' or pending:
                    raise RecoveryRequired('Invalid message order')
                messages.append(msg)
            elif kind == 'started':
                key = event['id']
                if key not in pending or key in starts:
                    raise RecoveryRequired('Tool start has no matching intent or is duplicated')
                starts.add(key)
            elif kind == 'result':
                key = event['id']
                if key not in starts or key in results:
                    raise RecoveryRequired('Tool result has no matching start or is duplicated')
                results[key] = event['output']
            else:
                raise RecoveryRequired('Duplicate journal header')
        unknown = [key for key in pending if key in starts and key not in results]
        if unknown and not acknowledge_unknown:
            raise RecoveryRequired('Tool outcome unknown: ' + ', '.join(unknown) +
                                   '; inspect the workspace, then acknowledge explicitly. No automatic replay.')
        for key in pending:
            output = results.get(key, 'Error: outcome unknown; inspect workspace; do not replay'
                                 if key in starts else 'Error: not executed before interruption')
            msg = {'role': 'tool', 'tool_call_id': key, 'content': output}
            self.append('message', message=msg)
            messages.append(msg)
        return messages
