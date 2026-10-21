import os


CONFIG = {

    "base_url": os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    "api_key": os.environ.get("OPENAI_API_KEY", ""),
    "model": os.environ.get("MODEL_ID", "gpt-4o-mini"),

    "max_tokens": 8000,
    "request_timeout_s": 300,

    "bash_timeout_s": 120,
    "max_output_chars": 50_000,

    "max_read_lines": 2000,

    "session_dir": os.environ.get("MINI_AGENT_SESSION_DIR", ".mini_agent/sessions"),

    "max_context_tokens": int(os.environ.get("MINI_AGENT_MAX_CONTEXT_TOKENS", "60000")),

    "compaction_reserve_tokens": 8000,

    "compaction_keep_recent_turns": 3,
}


BLOCKED_PATTERNS = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]


def require_api_key():
    if not CONFIG["api_key"]:
        raise SystemExit(
            'Missing OPENAI_API_KEY.\nSet the environment variables, for example:\n  $env:OPENAI_BASE_URL="https://api.openai.com/v1"\n  $env:OPENAI_API_KEY="sk-..."\n  $env:MODEL_ID="gpt-4o-mini"\n'
        )


def setup_console():
    import sys

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

CONFIG.update({
    "request_max_retries": int(os.environ.get("MINI_AGENT_REQUEST_MAX_RETRIES", "2")),
    "request_retry_base_s": 0.5,
    "request_retry_cap_s": 10.0,
})
