"""（已合并）行情落库诊断，请改用 ``_quote_probe.py``。

用法：``python -B _quote_probe.py`` 或 ``python -B _quote_probe.py --parse``
"""

from __future__ import annotations

import os
import runpy
import sys

_TARGET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_quote_probe.py")
if os.path.exists(_TARGET):
    runpy.run_path(_TARGET, run_name="__main__")
else:
    sys.stderr.write("_quote_probe.py 不存在\n")
