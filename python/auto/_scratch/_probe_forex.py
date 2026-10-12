"""（已合并）汇率数据源探查，已并入可复用的检查脚本。

    python -B _nav_source_probe.py            # 净值源 + 新浪美股覆盖
    python -B auto\\forexcli.py --compare      # 汇率双源对比（推荐）
    python -B auto\\forexcli.py --check        # 各汇率源逐项打印

结论：新浪（``fx_susdcny`` 等）与中国货币网（``chinamoney.com.cn``）都可用，
已分别实现为 ``forexsource.SinaForexSource`` 与 ``ChinaMoneyForexSource``。
"""

from __future__ import annotations

import os
import runpy
import sys

_TARGET = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cli", "forexcli.py")
if os.path.exists(_TARGET):
    sys.argv = [sys.argv[0], "--compare"]
    runpy.run_path(_TARGET, run_name="__main__")
else:
    sys.stderr.write("forexcli.py 不存在\n")
