"""（已合并）中间层标的新浪覆盖探查，已并入 ``_nav_source_probe.py``。

    python -B _nav_source_probe.py     # 含"新浪美股/全球指数覆盖"一节
"""

from __future__ import annotations

import os
import runpy
import sys

_TARGET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_nav_source_probe.py")
if os.path.exists(_TARGET):
    runpy.run_path(_TARGET, run_name="__main__")
else:
    sys.stderr.write("_nav_source_probe.py 不存在\n")
