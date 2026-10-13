# -*- coding: utf-8 -*-
"""Conversation persistence using JSON Lines (chapter 5).

Append one JSON object per message so history can be reloaded later.
The loop owns in-memory appends; its persistence hook writes only to disk.
Compaction replaces history and therefore requires a full file rewrite."""


import json
import os
import time
import uuid

from .config import CONFIG


class Session:
    """A conversation with an in-memory message list and a JSONL file."""

    def __init__(self, session_id, messages, path):
        self.id = session_id
        self.messages = messages      # The same message list used by agent_loop.
        self.path = path

    # Construction.

    @classmethod
    def create(cls, session_id=None, session_dir=None):
        """Create a session, generating an ID when none is provided."""
        session_id = session_id or time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        directory = session_dir or CONFIG["session_dir"]
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, f"{session_id}.jsonl")
        return cls(session_id, [], path)

    @classmethod
    def load(cls, path):
        """Reload JSONL messages, warning and skipping lines with invalid JSON."""
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

    # Appending messages.

    def append(self, role, content):
        """Append a role/content message to memory and disk, then return it."""
        msg = {"role": role, "content": content}
        self.messages.append(msg)
        self._write_line(msg)
        return msg

    def append_message(self, msg):
        """Append a complete message to memory and disk, then return it.
        
        Do not use this as the loop's on_append hook: the loop has already appended
        the message, so doing it here would duplicate the in-memory entry."""
        self.messages.append(msg)
        self._write_line(msg)
        return msg

    def persist(self, msg):
        """Save a message that the loop has already appended to memory.
        
        Use this as on_append: the loop changes memory; this method writes to disk."""
        self._write_line(msg)
        return msg

    def _write_line(self, msg):
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")

    # Integration with the agent loop.

    def replace_messages(self, messages):
        """Replace history in place and rewrite the JSONL file via a temporary file.
        
        Preserve the list object shared by the session and loop."""
        # Replace contents in place rather than assigning a new list.
        # The loop and caller still hold references to this list object.
        # Rebinding would leave later appends on an outdated list.
        # Slice assignment keeps memory and persistence connected.
        self.messages[:] = messages
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for msg in messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        os.replace(tmp, self.path)      # Replace the file atomically after writing the temporary copy.

    # Session inventory.

    def stats(self):
        return {
            "id": self.id,
            "path": self.path,
            "messages": len(self.messages),
            "bytes": os.path.getsize(self.path) if os.path.exists(self.path) else 0,
        }


def list_sessions(session_dir=None):
    """List session files, most recent first."""
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
    """Return the most recently modified session path, or None."""
    files = list_sessions(session_dir)
    return files[0] if files else None
