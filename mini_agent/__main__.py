# -*- coding: utf-8 -*-
"""让 `python -m mini_agent` 直接启动 CLI。"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
