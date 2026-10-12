# -*- coding: utf-8 -*-
"""
mini_agent · 配置与全局常量

这是整个项目里唯一一个"所有章节都依赖"的文件。
每章新增的功能都从这里取配置，不要在各章里硬编码。

为什么单独抽出来？因为随着章节推进，配置项会越来越多。
集中放一处，后面的章节只需要 import，不需要改动前面已写好的代码。
"""

import os

# ── 可调配置 ────────────────────────────────────────────────
# 全部通过环境变量覆盖，这样你不用改代码就能换模型/换服务商。

CONFIG = {
    # 服务端：任何 OpenAI /chat/completions 兼容的端点都行
    "base_url": os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    "api_key": os.environ.get("OPENAI_API_KEY", ""),
    "model": os.environ.get("MODEL_ID", "gpt-4o-mini"),
    # 请求参数
    "max_tokens": 8000,
    "request_timeout_s": 300,

    # 工具执行护栏
    "bash_timeout_s": 120,
    "max_output_chars": 50_000,

    # 读文件
    "max_read_lines": 2000,

    # 会话持久化（第 5 章加的东西）
    # 默认落在工作目录下的 .mini_agent/sessions/，一节对话一个文件。
    "session_dir": os.environ.get("MINI_AGENT_SESSION_DIR", ".mini_agent/sessions"),

    # 上下文压缩（第 6 章加的东西）
    # max_context_tokens 是模型窗口的近似值，到期就用摘要替换旧历史。
    "max_context_tokens": int(os.environ.get("MINI_AGENT_MAX_CONTEXT_TOKENS", "60000")),
    # 触发压缩时给"这轮还要用的 token"留的余量
    "compaction_reserve_tokens": 8000,
    # 压缩时保留最近几轮用户对话（原文不摘要）
    "compaction_keep_recent_turns": 3,
}

# 工具执行的命令黑名单。
# 注意：这只是一个"让初学者意识到需要护栏"的演示，
# 真实产品里这种字面匹配很容易被绕过，不要当安全方案。
BLOCKED_PATTERNS = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]


def require_api_key():
    """统一的 API key 检查，各章的入口都调它。"""
    if not CONFIG["api_key"]:
        raise SystemExit(
            "缺少 OPENAI_API_KEY。\n"
            '请先设置环境变量，例如：\n'
            '  $env:OPENAI_BASE_URL="https://api.openai.com/v1"\n'
            '  $env:OPENAI_API_KEY="sk-..."\n'
            '  $env:MODEL_ID="gpt-4o-mini"\n'
        )


def setup_console():
    """Windows 控制台默认可能是 cp936，强制 UTF-8 以免中文输出报错。"""
    import sys

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
