"""探针：用**真实演示库**算三个 EST，与生产端页面逐个数比。

跑法（在 ``python/`` 下）::

    D:/python/python.exe -B auto/_scratch/_est_scale_verify.py

## 为什么写这个

``_qdii_value()`` 修完尺度错位（4648 倍）与汇率双算之后，要确认真实库上
``/qdii`` 与 ``/fund`` 页的官方 / 参考 / 实时 EST 回到净值量级。

生产端对照（2026-10-13，https://palmmicro.com/woody/res/qdiicn.php）::

    SH513100  官方 2.049  参考 2.038  实时 2.042
    SZ161130  官方 4.572  参考 4.577
    SZ161125  官方 3.182  参考 3.182

演示库的中间层数据停在 10-07 / 10-09（比生产端旧几天），所以对不上是预期的，
要看的是**量级与相对关系**：官方 EST ≈ 当天净值、三者互差在个位数百分比内。
只读不写。
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))  # .../python/auto/_scratch
_AUTO = os.path.dirname(_HERE)                      # .../python/auto
_PYTHON = os.path.dirname(_AUTO)                    # .../python

sys.path.insert(0, _AUTO)
sys.path.insert(0, _PYTHON)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pymysql  # noqa: E402

from core.stockdata import load_mysql_env, mysql_config_from_env, MySqlFundDataSource  # noqa: E402
from calc.fundestcalc import EstimateCalculator  # noqa: E402
from sql.fundeststore import FundEstStore  # noqa: E402

#: 生产端 2026-10-13 的数值（qdiicn.php / 各基金详情页）
PRODUCTION = {
    "SH513100": (2.049, 2.038, 2.042),
    "SZ161130": (4.572, 4.577, None),
    "SZ161125": (3.182, 3.182, None),
}

SYMBOLS = list(PRODUCTION)


def main():
    cfg = mysql_config_from_env(load_mysql_env(os.environ))
    cfg["cursorclass"] = pymysql.cursors.DictCursor
    connection = pymysql.connect(**cfg)
    try:
        source = MySqlFundDataSource(connection)
        store = FundEstStore(connection)
        calculator = EstimateCalculator(source, store)
        print("%-9s %-10s %-10s %-10s | %-10s %-10s %-10s" % (
            "符号", "官方EST", "参考EST", "实时EST", "官方(生产)", "参考(生产)", "实时(生产)"))
        print("-" * 78)
        for symbol in SYMBOLS:
            values = []
            for name in ("compute_official", "compute_fair", "compute_realtime"):
                record = getattr(calculator, name)(symbol)
                values.append(None if record is None else record.value)
            official, fair, realtime = PRODUCTION[symbol]

            def fmt(value):
                return "-" if value is None else "%.4f" % value

            def dev(mine, prod):
                if mine is None or prod is None:
                    return "-"
                return "%+.2f%%" % ((mine / prod - 1.0) * 100.0)

            print("%-9s %-10s %-10s %-10s | %-10s %-10s %-10s" % (
                symbol, fmt(values[0]), fmt(values[1]), fmt(values[2]),
                fmt(official), fmt(fair), fmt(realtime)))
            print("%-9s   偏差     %-10s %-10s %-10s" % (
                "", dev(values[0], official), dev(values[1], fair), dev(values[2], realtime)))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
