import json
import os
import time
import uuid

from .config import CONFIG


class Session:
    def __init__(self, session_id, messages, path):
        self.id = session_id
        self.messages = messages
        self.path = path

    @classmethod
    def create(cls, session_id=None, session_dir=None):
        session_id = session_id or time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        directory = session_dir or CONFIG["session_dir"]
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, f"{session_id}.jsonl")
        return cls(session_id, [], path)

    @classmethod
    def load(cls, path):
        if not os.path.exists(path):
            raise FileNotFoundError('Session file not found: {0}'.format(path))
        messages = []
        skipped = 0
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    messages.append(json.loads(line))
                except json.JSONDecodeError:
                    skipped += 1
        session_id = os.path.splitext(os.path.basename(path))[0]
        if skipped:
            print('[Warning] Skipped {0} corrupt lines'.format(skipped))
        return cls(session_id, messages, path)

    def append(self, role, content):
        msg = {"role": role, "content": content}
        self.messages.append(msg)
        self._write_line(msg)
        return msg

    def append_message(self, msg):
        self.messages.append(msg)
        self._write_line(msg)
        return msg

    def persist(self, msg):
        self._write_line(msg)
        return msg

    def _write_line(self, msg):
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")

    def replace_messages(self, messages):
        self.messages[:] = messages
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for msg in messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        os.replace(tmp, self.path)

    def stats(self):
        return {
            "id": self.id,
            "path": self.path,
            "messages": len(self.messages),
            "bytes": os.path.getsize(self.path) if os.path.exists(self.path) else 0,
        }


def list_sessions(session_dir=None):
    directory = session_dir or CONFIG["session_dir"]
    if not os.path.isdir(directory):
        return []
    files = [
        os.path.join(directory, n)
        for n in os.listdir(directory)
        if n.endswith(".jsonl")
    ]
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return files


def latest_session(session_dir=None):
    files = list_sessions(session_dir)
    return files[0] if files else None
