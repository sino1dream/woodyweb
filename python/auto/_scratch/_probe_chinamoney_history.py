"""（已合并）汇率核对见 ``_debug_forex_verify.py`` 与 ``_verify_nav.py``。"""

from __future__ import annotations

import os
import runpy
import sys

_TARGET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_verify_nav.py")
if os.path.exists(_TARGET):
    runpy.run_path(_TARGET, run_name="__main__")
else:
    sys.stderr.write("_verify_nav.py 不存在\n")
