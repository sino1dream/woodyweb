"""（已合并）汇率数据核对请改用 ``_verify_nav.py``。

结论（测完就固化，别再重复排查）：

    USCNY 在 2026-01-02 的 7.1 曾是**种子手工占位**，不是真实汇率。
    它会被 as-of 回退当成"9 个月前的最近值"取到，污染估值。

    现在：占位已清（``forexcli.py --purge-fake``），三层日期统一到
    "已有汇率的最近一天"（``_reset_est_scale.py``），并且回退超过
    ``stockdata.MAX_RATE_FALLBACK_DAYS``（10 天）会写进
    ``source.rate_warnings`` 供核对时打印。

    python -B _verify_nav.py            # 看净值/市价/校准值对照
    python -B auto\\forexcli.py --compare  # 汇率双源对比
"""

from __future__ import annotations

import os
import runpy
import sys

_TARGET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_verify_nav.py")
if os.path.exists(_TARGET):
    runpy.run_path(_TARGET, run_name="__main__")
else:
    sys.stderr.write("_verify_nav.py 不存在\n")
