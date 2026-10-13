# -*- coding: utf-8 -*-
"""Shared configuration and constants for all seven chapters.

Keep model, tool, session, and compaction settings together so later
chapters can reuse configuration without changing earlier interfaces."""


import os

# Configurable settings.
# Use environment variables to change providers without editing code.

CONFIG = {
    # Any compatible Chat Completions endpoint.
    "base_url": os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    "api_key": os.environ.get("OPENAI_API_KEY", ""),
    "model": os.environ.get("MODEL_ID", "gpt-4o-mini"),
    # Request parameters.
    "max_tokens": 8000,
    "request_timeout_s": 300,

    # Tool execution limits.
    "bash_timeout_s": 120,
    "max_output_chars": 50_000,

    # File reading.
    "max_read_lines": 2000,

    # Session persistence (chapter 5).
    # One JSONL file per session under the working directory by default.
    "session_dir": os.environ.get("MINI_AGENT_SESSION_DIR", ".mini_agent/sessions"),

    # Context compaction (chapter 6).
    # Approximate model context limit used to trigger summarization.
    "max_context_tokens": int(os.environ.get("MINI_AGENT_MAX_CONTEXT_TOKENS", "60000")),
    # Reserve room for the next request and response.
    "compaction_reserve_tokens": 8000,
    # Keep these recent user turns verbatim.
    "compaction_keep_recent_turns": 3,
}

# Literal shell-command blacklist.
# This demonstrates the idea of execution guardrails.
# Literal matching is easy to bypass; it is not a sandbox.
BLOCKED_PATTERNS = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]


def require_api_key():
    """Require an API key before starting an agent session."""
    if not CONFIG["api_key"]:
        raise SystemExit(
            'Missing OPENAI_API_KEY.\nSet the environment variables, for example:\n  $env:OPENAI_BASE_URL="https://api.openai.com/v1"\n  $env:OPENAI_API_KEY="sk-..."\n  $env:MODEL_ID="gpt-4o-mini"\n'
        )


def setup_console():
    """Use UTF-8 streams so Windows terminals can display Unicode text."""
    import sys

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
