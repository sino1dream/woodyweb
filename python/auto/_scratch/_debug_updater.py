"""（已合并）更新器构造诊断，请改用 ``_quote_probe.py --realtime``。

排查"页面最新价不显示"的顺序见 ``README_realtime_quotes.md`` 第五节。
"""

from __future__ import annotations

import os
import runpy
import sys

_TARGET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_quote_probe.py")
if os.path.exists(_TARGET):
    sys.argv = [sys.argv[0], "--realtime"]
    runpy.run_path(_TARGET, run_name="__main__")
else:
    sys.stderr.write("_quote_probe.py 不存在\n")
