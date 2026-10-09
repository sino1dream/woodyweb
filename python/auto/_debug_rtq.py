"""（已合并）实时行情快照表诊断，请改用 ``_quote_probe.py --realtime``。

    python -B _quote_probe.py --realtime     # 看 realtimequote 表与新鲜度
    python -B _quote_probe.py                # 看 stockhistory / netvaluehistory
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
