#!/usr/bin/env python
"""每日欧洲油画壁纸 —— 启动入口。

用法：
    python main.py                # 启动托盘程序
    python main.py change         # 立刻换一张
    python main.py --help         # 查看全部命令
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from dop.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
