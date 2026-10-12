"""临时工具：把期货与指数的校准值写进 ``calibrationhistory``。

## 为什么要写

实时 EST = 用**期货价**算的中间层估值，它需要"期货 / 指数"的校准比例：

    est_f = (nv_f × cal_f / cny_ref) × (期货价 / 校准值 / est_quote_ref) × (cny / cny_ref)

校准值 = ``期货价 × 汇率 / 指数价``（PHP ``CalcFactor()``）。
指数点位由中间层 ETF 反推：``指数 = ETF价 / ETF校准值``
（本项目实测恒等式，见 ``futurescalib.implied_index_price()``）。

不写这个校准值，实时 EST 就永远退回"中间层快照"那条路。

## 与 PHP 的差别

PHP 的 ``CheckAdjustFactorTime()`` 要求期货与**指数**时刻严格相同才校准。
本项目里指数（``^NDX``）没有行情快照（点位从新浪直接抓、不落 stockhistory），
所以这里改用 ``calibrate_from_proxy()`` —— 只要求**日期相同**。
"""

from __future__ import annotations

import io
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "auto"))

from calc.futurescalib import build_futures_calibrator  # noqa: E402
from source.futuresquotes import SinaFuturesClient  # noqa: E402
from sql.historytable import CalibrationSql  # noqa: E402
from core.stockdata import MySqlFundDataSource  # noqa: E402


def main() -> int:
    apply = "--apply" in sys.argv
    source = MySqlFundDataSource.from_env()
    calibrator = build_futures_calibrator(source)

    # 先抓一条最新期货
    futures = SinaFuturesClient().fetch(["hf_NQ", "hf_ES"])
    if not futures:
        print("没抓到期货行情，无法校准。")
        source.connection.close()
        return 1

    table = CalibrationSql(source.connection)
    print("%-8s %-8s %-4s %-12s %-14s %s" % ("指数", "期货", "ETF", "ETF快照价", "反推指数点位", "校准值"))
    print("-" * 82)

    planned = []
    for index_symbol in ("^NDX", "^GSPC"):
        future_symbol = calibrator.future_symbol_of(index_symbol)
        quote = futures.get(future_symbol)
        if quote is None:
            print("%-8s %-8s 跳过（没抓到 %s）" % (index_symbol, future_symbol, future_symbol))
            continue
        proxy = calibrator.proxy_symbol_of(index_symbol)
        implied = calibrator.implied_index_price(proxy)
        factor = calibrator.calibrate_from_proxy(index_symbol, quote, apply=False)
        print("%-8s %-8s %-4s %-12s %-14s %s" % (
            index_symbol, future_symbol, proxy,
            round(float(calibrator._proxy_price(proxy)), 4),
            round(implied, 2) if implied else "-",
            round(factor, 8) if factor else "-"))
        if factor is not None:
            planned.append((index_symbol, future_symbol, factor, quote))

    if not planned:
        print("")
        print("没有可写的校准值（通常是日期不一致）。")
        source.connection.close()
        return 0

    print("")
    if not apply:
        print("这是预演。真正执行请加 --apply。")
        source.connection.close()
        return 0

    for index_symbol, future_symbol, _factor, quote in planned:
        factor = calibrator.calibrate_from_proxy(index_symbol, quote, apply=True)
        index_id = source.get_stock_id(index_symbol)
        record = table.get_record(index_id, quote.date)
        print("  已写 %-8s 校准值=%s 日期=%s num=%s" % (
            index_symbol,
            round(float(record["close"]), 8) if record else "-",
            quote.date,
            record.get("num") if record else "-"))

    print("")
    print("=== 核对 ===")
    for index_symbol in ("^NDX", "^GSPC"):
        index_id = source.get_stock_id(index_symbol)
        row = table.get_all(index_id, 0, 1)
        if row:
            print("  %-8s %s = %s" % (index_symbol, row[0]["date"], row[0]["close"]))
        else:
            print("  %-8s 无记录" % index_symbol)

    source.connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
