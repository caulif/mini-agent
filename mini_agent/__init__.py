# -*- coding: utf-8 -*-
"""A minimal coding agent developed across seven teaching chapters.

See guide.html for the English guide or guide.zh-CN.html for Chinese.
Run python -m mini_agent for the CLI, or add --help for its options."""

from .agent import agent_loop, make_executor

__version__ = "0.7.0"  # One minor version for each of the seven chapters.

__all__ = ["agent_loop", "make_executor", "__version__"]
