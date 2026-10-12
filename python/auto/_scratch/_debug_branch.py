"""临时诊断：打印 est_net_value 各分支的中间量，确定校准值的真实作用。

用法：cd F:\\woody\\woodyweb\\python ; python -B auto\\_debug_branch.py
"""

from __future__ import annotations

import io
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

AUTO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _path in (os.path.join(PYTHON_DIR, "auto"), PYTHON_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core.stockdata import (  # noqa: E402
    MySqlFundDataSource,
    StockDataArrayBuilder,
    load_mysql_env,
    mysql_config_from_env,
)
from calc.fundcalc import PalmmicroFundCalculator  # noqa: E402


def main() -> int:
    config = mysql_config_from_env(load_mysql_env(os.environ))
    import pymysql

    connection = pymysql.connect(
        host=config["host"], port=config["port"], user=config["user"],
        password=config["password"], database=config["database"],
        charset="utf8", cursorclass=pymysql.cursors.DictCursor,
    )
    source = MySqlFundDataSource(connection)

    try:
        for symbol in ("SZ161130", "SZ161125", "SH513100"):
            data = StockDataArrayBuilder(source).build(symbol)
            calculator = PalmmicroFundCalculator(data)
            own = data[symbol]
            hedge = own["symbol_hedge"]
            middle = data[hedge]

            position = float(own["position"])
            netvalue = float(own["netvalue"])
            calibration = float(own["calibration"])
            cny = float(own.get("CNY", 1.0))

            # 复刻 est_net_value 的两条路径
            without_source = calculator.calc_with_calibration(own)
            with_inner = calculator.calc_with_calibration(own, {hedge: float(middle["netvalue"])})

            print("--- %s ---" % symbol)
            print("  own: pos=%s netvalue=%s calibration=%s CNY=%s hedge=%s" % (
                position, netvalue, calibration, cny, hedge))
            print("  中间层 %s: calibration=%s netvalue=%s hedge=%s" % (
                hedge, middle.get("calibration"), middle.get("netvalue"), middle.get("symbol_hedge")))
            print("  calc_with_calibration(own)            = %s" % without_source)
            print("  calc_with_calibration(own, 中间层净值) = %s" % with_inner)
            print("  est_net_value(symbol)                 = %s" % calculator.est_net_value(symbol))
            print("  反算：要得到 est=%s，需要 own.calibration=%.4f" % (
                round(netvalue, 4), calibration * calculator.est_net_value(symbol) / netvalue))
            print("")
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
