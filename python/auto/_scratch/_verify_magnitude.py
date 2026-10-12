"""（已合并）已并入 ``_verify_nav.py``。

量级验证的结论（测完就固化在这里，别再重复担心）：

    php/stock/qdiiref.php:193
        fFactor = QdiiGetCalibration(est, cny, netvalue) = est * cny / netvalue
    所以 calibrationhistory 是"源值 + 净值"反算出来的**派生量**，
    估算公式 est * cny / fFactor 恒等于净值，**与源用量纲无关** ——
    换源时让 calibrationhistory 跟着重算即可，不需要硬编码换算系数。
    （早先我在种子数据里手填的 QQQ calibration=560 是合成值，不是生产口径。）

    python -B _verify_nav.py            # 市价/净值/校准值/溢价率 对照
    python -B _verify_nav.py --est      # 看 estquote 中间层表
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
