"""P0.6 探针：逐个基金走一遍 record_official 的三个闸门，报出被哪一条挡住。

只读（不调用任何写库路径），目的是回答两个问题：

1. ``fundestcli`` 为什么只有少数几只写进了 ``fundest``；
2. 期货实时 EST 这一路缺的是什么（期货行情 / 校准值 / 配对关系）。

用法：``python -B auto/_scratch/_p0_6_probe.py``
"""

from __future__ import annotations

import io
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

AUTO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON_DIR = os.path.dirname(AUTO_DIR)
for _path in (AUTO_DIR, PYTHON_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from calc.fundcalc import qdii_est_symbol_map  # noqa: E402
from calc.fundestcalc import build_estimate_calculator  # noqa: E402
from calc.futurescalib import FALLBACK_FUTURES, build_futures_calibrator  # noqa: E402
from core.stockdata import MySqlFundDataSource  # noqa: E402
from sql.fundeststore import FundEstStore  # noqa: E402


def probe_fundest(source, calculator) -> None:
    print("=== 官方 EST：每个基金卡在哪道闸门 ===")
    print("%-10s %-12s %-12s %-10s %-10s %s" % (
        "符号", "ost_date", "OST值", "已有VS", "净值已到", "结论"))
    print("-" * 78)
    for symbol in sorted(qdii_est_symbol_map()):
        stock_id = source.get_stock_id(symbol)
        if stock_id is None:
            print("%-10s %s" % (symbol, "不在 stock 表"))
            continue
        record = calculator.compute_official(symbol)
        if record is None:
            print("%-10s %-12s %-12s %s" % (symbol, "-", "-", "compute_official 返回 None（缺数据）"))
            continue
        has_time = record.time is not None
        nav_ready = False
        try:
            nav_ready = source.get_netvalue_close(stock_id, record.date) not in (None, "")
        except Exception:
            pass
        existing = FundEstStore(source.connection).get_close(stock_id, record.date)
        reason = ("不写：回退分支（time 非空，库里已有）" if has_time
                  else ("不写：当天净值已到" if nav_ready
                        else ("不写：该日期已有记录" if existing is not None else "**可写**")))
        print("%-10s %-12s %-12s %-10s %-10s %s" % (
            symbol, record.date, "%.6f" % record.value,
            str(existing), "是" if nav_ready else "否", reason))


def probe_futures(source) -> None:
    print("")
    print("=== 期货实时 EST：三样缺哪样 ===")
    calibrator = build_futures_calibrator(source)
    # 期货行情要自己起客户端抓：校准器**没有** futures_client 这个属性
    # （那是 fundestcalc 的计算器才有的注入点，别混）。
    from source.futuresquotes import SinaFuturesClient

    client = SinaFuturesClient()
    cursor = source.connection.cursor()
    try:
        cursor.execute("SELECT symbol FROM stock ORDER BY symbol")
        all_symbols = {str(row["symbol"]) for row in cursor.fetchall()}
    finally:
        cursor.close()

    for index_symbol, future_symbol in sorted(FALLBACK_FUTURES.items()):
        in_stock = future_symbol in all_symbols
        quote = None
        if in_stock:
            try:
                quote = client.fetch([future_symbol]).get(future_symbol)
            except Exception as exc:
                print("  %-8s 抓行情失败 %s: %s" % (future_symbol, type(exc).__name__, exc))
        cal = None
        index_id = source.get_stock_id(index_symbol)
        if index_id is not None:
            try:
                from sql.historytable import CalibrationSql

                rows = CalibrationSql(source.connection).get_all(index_id, 0, 1)
                cal = rows[0]["close"] if rows else None
            except Exception as exc:
                cal = "读不到(%s)" % type(exc).__name__
        print("  %-10s <- %-8s  在库=%s  行情=%s  校准值=%s" % (
            index_symbol, future_symbol,
            "是" if in_stock else "否",
            quote.price if quote else "无",
            cal if cal is not None else "无"))


def main() -> int:
    source = MySqlFundDataSource.from_env()
    try:
        calculator = build_estimate_calculator(source)
        probe_fundest(source, calculator)
        if calculator.warnings:
            print("")
            print("告警：")
            for warning in calculator.warnings:
                print("  %s" % warning)
        probe_futures(source)
    finally:
        source.connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
