"""（已合并）见 ``_probe_akshare_src.py``；实现见 ``forexsource.SafeForexSource``。

外汇管理局表格结构（实测真实响应）：

    <table>  ← 页面里最后一张表才是数据表
      <tr><th>日期</th><th>美元</th><th>欧元</th><th>日元</th><th>港元</th>...</tr>
      <tr><td>2026-09-29</td><td>674.11</td><td>764.94</td><td>4.275</td><td>85.935</td>...</tr>
    </table>

按 100 外币挂牌 → 除以 100：674.11/100 = 6.7411。
"""

from __future__ import annotations

import os
import runpy
import sys

_TARGET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_probe_akshare_src.py")
if os.path.exists(_TARGET):
    runpy.run_path(_TARGET, run_name="__main__")
else:
    sys.stderr.write("_probe_akshare_src.py 不存在\n")
