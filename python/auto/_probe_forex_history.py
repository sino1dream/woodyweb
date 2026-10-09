"""（已合并）汇率历史接口探查，结论已写入 ``README_name_and_forex.md`` 第 4.2 节。

实测结果（四个上游都不可用）：

    东方财富 push2his.eastmoney.com  第一次通、之后持续 ConnectionError
    新浪 ForexService.getDayKLine    Service not found
    新浪 NewForexService.getDayKLine HTTP 500
    腾讯 web.ifzq.gtimg.cn           param error

所以历史回补改走"每日抓取持续积累 + 尽力回补"两条路径：

    python -B auto\\forexcli.py --backfill             # 尝试回补（上游不稳）
    python -B auto\\forexcli.py --fill-official-dates  # 给官方日期补（≤5 天）
    python -B auto\\forexcli.py                        # 抓当日（主路径）
"""

from __future__ import annotations

import os
import runpy
import sys

_TARGET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "forexcli.py")
if os.path.exists(_TARGET):
    sys.argv = [sys.argv[0], "--check"]
    runpy.run_path(_TARGET, run_name="__main__")
else:
    sys.stderr.write("forexcli.py 不存在\n")
