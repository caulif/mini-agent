# -*- coding: utf-8 -*-
"""Tool schemas, handlers, and dispatch (chapters 2 and 3).

TOOLS describes functions to the model; HANDLERS maps names to implementations.
Each handler accepts a dictionary and returns text, so the loop only needs
submit(name, inp). File writes and edits share the per-path mutation queue."""

import os
import subprocess

from .config import BLOCKED_PATTERNS, CONFIG
from .mutation_queue import mutation_queue

# ═══════════════════════════════════════════════════════════
# Tool schemas sent to the model.
# ═══════════════════════════════════════════════════════════
#
# These schemas are serialized into the request's tools field.
# Every available handler needs a corresponding declaration.

def _function(name, description, properties, required):
    """Build an OpenAI-style function tool schema."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


TOOLS = [
    _function(
        "bash",
        "Run a shell command.",
        {"command": {"type": "string"}},
        ["command"],
    ),
    _function(
        "read",
        "Read a file's text content.",
        {
            "path": {"type": "string"},
            "offset": {"type": "integer", "description": "1-indexed start line"},
            "limit": {"type": "integer", "description": "max lines to read"},
        },
        ["path"],
    ),
    _function(
        "write",
        "Write content to a file (overwrites the whole file).",
        {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        ["path", "content"],
    ),
    _function(
        "edit",
        "Replace old_string with new_string in a file. "
        "old_string must appear exactly once in the file.",
        {
            "path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
        },
        ["path", "old_string", "new_string"],
    ),
]

# ═══════════════════════════════════════════════════════════
# Path validation.
# ═══════════════════════════════════════════════════════════


def safe_path(raw):
    """Resolve a path and reject targets outside the working directory.
    
    realpath also resolves symbolic links. Raise ValueError for an outside path.
    This check applies to file tools; it does not sandbox shell commands."""
    workdir = os.path.realpath(os.getcwd())
    target = os.path.realpath(os.path.join(workdir, raw or "."))
    if target != workdir and not target.startswith(workdir + os.sep):
        raise ValueError(f"path outside workdir: {raw}")
    return target


# ═══════════════════════════════════════════════════════════
# Tool implementations.
# ═══════════════════════════════════════════════════════════
#
# All handlers share the signature (input: dict) -> str.
# This uniform contract enables one dispatch table.


def tool_bash(inp):
    """Run a shell command with a literal blacklist, timeout, and output limit."""
    command = str(inp.get("command", ""))
    if any(pattern in command for pattern in BLOCKED_PATTERNS):
        return "Error: Dangerous command blocked"

    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=CONFIG["bash_timeout_s"],
        )
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {CONFIG['bash_timeout_s']}s"

    output = f"{proc.stdout or ''}{proc.stderr or ''}".strip()
    if not output:
        return "(no output)"
    return output[: CONFIG["max_output_chars"]]


def tool_read(inp):
    """Read numbered UTF-8 text with offset/limit pagination, without a shell."""
    try:
        abs_path = safe_path(str(inp.get("path", "")))
    except ValueError as e:
        return f"Error: {e}"

    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError as e:
        return f"Error: {e}"

    offset = int(inp.get("offset") or 1)  # 1-indexed
    limit = int(inp.get("limit") or CONFIG["max_read_lines"])
    start = max(0, offset - 1)
    selected = lines[start : start + limit]

    # Include line numbers so the model can reference exact locations.
    numbered = "".join(
        f"{start + i + 1:>6}\t{line}" for i, line in enumerate(selected)
    )
    remaining = len(lines) - (start + len(selected))
    if remaining > 0:
        numbered += f"\n... ({remaining} more lines)"
    return numbered or "(empty file)"


def tool_write(inp):
    """Overwrite a whole file under the shared per-path mutation lock."""
    path = str(inp.get("path", ""))
    content = str(inp.get("content", ""))
    try:
        abs_path = safe_path(path)
    except ValueError as e:
        return f"Error: {e}"

    def mutate():
        try:
            os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
            with open(abs_path, "w", encoding="utf-8", newline="") as f:
                f.write(content)
        except OSError as e:
            return f"Error: {e}"
        return f"Wrote {len(content)} bytes to {path}"

    return mutation_queue.run(abs_path, mutate)


def tool_edit(inp):
    """Replace one exact match under the shared per-path mutation lock.
    
    Read, count matches, and write within the same protected operation. Require
    exactly one old_string match to avoid changing an unintended occurrence."""
    path = str(inp.get("path", ""))
    old_string = str(inp.get("old_string", ""))
    new_string = str(inp.get("new_string", ""))

    if not old_string:
        return "Error: old_string cannot be empty"

    try:
        abs_path = safe_path(path)
    except ValueError as e:
        return f"Error: {e}"

    def mutate():
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError as e:
            return f"Error: {e}"

        count = content.count(old_string)
        if count == 0:
            return f"Error: old_string not found in {path}"
        if count > 1:
            return (
                f"Error: old_string appears {count} times in {path}, must be unique. "
                "Include more surrounding context to make it unique."
            )

        try:
            with open(abs_path, "w", encoding="utf-8", newline="") as f:
                f.write(content.replace(old_string, new_string, 1))
        except OSError as e:
            return f"Error: {e}"
        return f"Edited {path}"

    # Protect the entire read, match, and write operation.
    return mutation_queue.run(abs_path, mutate)


# ═══════════════════════════════════════════════════════════
# Dispatch table.
# ═══════════════════════════════════════════════════════════
#
# Add handlers here without changing the loop.

HANDLERS = {
    "bash": tool_bash,
    "read": tool_read,
    "write": tool_write,
    "edit": tool_edit,
}


def submit(name, inp):
    """Dispatch a tool by name and return its result as text.
    
    Return errors to the model so it can correct a call instead of crashing
    the agent. Unexpected handler exceptions are also converted to result text."""
    handler = HANDLERS.get(name)
    if handler is None:
        known = ", ".join(sorted(HANDLERS))
        return f"Error: unknown tool '{name}'. Available tools: {known}"
    try:
        return handler(inp)
    except Exception as e:  # Convert unexpected handler failures into model-visible errors.
        return f"Error: {type(e).__name__}: {e}"
