# -*- coding: utf-8 -*-
"""临时探针：打印 A股商品 6 只基金的三档 EST 与上下文。

跑法::

    cd python/auto && D:/python/python.exe -B _scratch/_china_future_probe.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.stockdata import MySqlFundDataSource  # noqa: E402
from calc.fundestcalc import EstimateCalculator  # noqa: E402
from sql.fundeststore import FundEstStore  # noqa: E402

SYMBOLS = ("SH518800", "SH518880", "SZ159934", "SZ159937", "SZ159985", "SZ161226")


def main() -> int:
    source = MySqlFundDataSource.from_env()
    calculator = EstimateCalculator(source, FundEstStore(source.connection))
    for symbol in SYMBOLS:
        ctx = calculator._resolve(symbol)
        print("=" * 66)
        print(symbol)
        if ctx is None:
            print("  _resolve 返回 None")
            continue
        print("  est=%s mid_date=%s cny_symbol=%s cny_mid=%s cny_fair=%s"
              % (ctx["est_symbol"], ctx["mid_date"], ctx["cny_symbol"],
                 ctx["cny_mid"], ctx["cny_fair"]))
        print("  realtime_quote=%s day_quote=%s mid_net_value=%s est_ref=%s@%s"
              % (ctx["realtime_quote"], ctx["day_quote"], ctx["mid_net_value"],
                 ctx["est_quote_ref"], ctx["est_quote_ref_date"]))
        official = calculator.compute_official(symbol)
        fair = calculator.compute_fair(symbol)
        print("  官方 EST = %s" % (official.value if official else None))
        print("  参考 EST = %s" % (fair.value if fair else None))
    source.connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
