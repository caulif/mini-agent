from copy import deepcopy
from pathlib import Path

from .tools import TOOLS


class WorkspaceTools:
    def __init__(self, root, *, allow_write=False):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError('Workspace root must be a directory')
        self.allow_write = allow_write
        self.reserved = set()
        self.registry = {}
        for schema in TOOLS:
            name = schema['function']['name']
            if name in {'read', 'write', 'edit'}:
                self.register(name, schema, getattr(self, '_' + name), mutating=name != 'read')

    def register(self, name, schema, handler, *, mutating=True):
        if name in self.registry or schema['function']['name'] != name or not callable(handler):
            raise ValueError('Duplicate tool or invalid registration')
        self.registry[name] = (deepcopy(schema), handler, mutating)

    def definitions(self):
        return [deepcopy(schema) for schema, _, write in self.registry.values()
                if self.allow_write or not write]

    def path(self, value):
        if not isinstance(value, str) or not value:
            raise ValueError('path must be a nonempty string')
        path = (self.root / value).resolve()
        if not path.is_relative_to(self.root):
            raise PermissionError('Path is outside the workspace')
        relative = path.relative_to(self.root)
        if any(':' in part or part.endswith((' ', '.')) or
               part.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL',
                   *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}
               for part in relative.parts):
            raise PermissionError('Device paths, alternate data streams, and ambiguous names are not supported')
        if (relative.parts and relative.parts[0].lower() in {'.mini_agent', '.git'}) or path in self.reserved:
            raise PermissionError('Model tools cannot access journals or Git metadata')
        return path

    def submit(self, name, inp, *, signal):
        signal.check()
        if name not in self.registry:
            raise PermissionError('Tool is not registered')
        _, handler, write = self.registry[name]
        if write and not self.allow_write:
            raise PermissionError('Write access requires --allow-write')
        if not isinstance(inp, dict):
            raise ValueError('Tool arguments must be an object')
        return handler(inp, signal=signal)

    def _read(self, inp, *, signal):
        path = self.path(inp.get('path'))
        offset, limit = inp.get('offset', 1), inp.get('limit', 200)
        if type(offset) is not int or offset < 1 or type(limit) is not int or not 1 <= limit <= 2000:
            raise ValueError('offset must be >= 1; limit must be between 1 and 2000')
        rows, size = [], 0
        with path.open(encoding='utf-8') as src:
            for index in range(offset - 1 + limit):
                signal.check()
                line = src.readline(50001)
                if not line:
                    break
                if len(line) > 50000:
                    raise ValueError('Line exceeds size limit; read refused')
                if index >= offset - 1:
                    rows.append(f'{index + 1}: {line}')
                    size += len(line)
                    if size >= 50000:
                        break
        return ''.join(rows)[:50000]

    def _write(self, inp, *, signal):
        path, content = self.path(inp.get('path')), inp.get('content')
        if not isinstance(content, str):
            raise ValueError('content must be a string')
        signal.check()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        return f'Wrote {len(content)} characters'

    def _edit(self, inp, *, signal):
        path = self.path(inp.get('path'))
        old, new = inp.get('old_string'), inp.get('new_string')
        if not isinstance(old, str) or not old or not isinstance(new, str):
            raise ValueError('old_string must be nonempty and new_string must be a string')
        content = path.read_text(encoding='utf-8')
        if content.count(old) != 1:
            raise ValueError('old_string must match exactly once')
        signal.check()
        path.write_text(content.replace(old, new, 1), encoding='utf-8')
        return 'Edited file'
