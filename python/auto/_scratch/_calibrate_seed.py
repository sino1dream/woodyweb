"""（已合并）校准值探查工具，请改用 ``_calibration_probe.py``。

保留这个文件名只是为了不留下"看不懂的脚本"；内容已移走。
"""

from __future__ import annotations

import os
import runpy
import sys

_TARGET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_calibration_probe.py")
if os.path.exists(_TARGET):
    runpy.run_path(_TARGET, run_name="__main__")
else:
    sys.stderr.write("_calibration_probe.py 不存在\n")
