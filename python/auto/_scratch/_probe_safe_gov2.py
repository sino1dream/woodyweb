"""（已合并）见 ``_probe_akshare_src.py``；实现见 ``forexsource.SafeForexSource``。"""

from __future__ import annotations

import os
import runpy
import sys

_TARGET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_probe_akshare_src.py")
if os.path.exists(_TARGET):
    runpy.run_path(_TARGET, run_name="__main__")
else:
    sys.stderr.write("_probe_akshare_src.py 不存在\n")
