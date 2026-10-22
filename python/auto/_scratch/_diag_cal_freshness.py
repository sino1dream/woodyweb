# -*- coding: utf-8 -*-
"""诊断「基准价 / 基准汇率为什么停在旧日期」。

页面上的 ``est_cal`` / ``cny_cal`` 是**锚定最近一条校准记录那天**取的
（``fundestcalc._est_quote_ref_with_date`` / ``_calibration_cny_with_date``，
日期来自 ``_calibration_date_of()`` = ``calibrationhistory`` 的 ``date``）。
所以"基准价没更新"首先要看的是**校准记录停在哪天**，其次才是净值/行情
数据到了哪天。本脚本把这三层的日期全打出来，用来区分：

* 校准没跑（calibrationhistory 停在旧日） -> 要跑校准；
* 校准跑了但那天 est/cny 没数据 -> 兜底退到了更早那天，要补历史行。

跑法::

    cd python/auto/_scratch
    D:/python/python.exe -B _diag_cal_freshness.py
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

from core.stockdata import MySqlFundDataSource  # noqa: E402

#: 基金 + 它的 est 标的 + 汇率。^GSPC 是 SZ161125 的 est（est_symbol 由
#: qdii_est_symbol_map 决定），USCNY 是折算用的汇率。
SYMBOLS = ("SZ161125", "SZ161130", "^GSPC", "^NDX", "USCNY", "QQQ", "SPY")

#: 每个符号最多打印多少条最近数据
LIMIT = 8


def _latest(cursor, table: str, stock_id) -> str:
    cursor.execute(
        "SELECT date, close FROM %s WHERE stock_id = %%s ORDER BY date DESC LIMIT 1" % table,
        (stock_id,),
    )
    row = cursor.fetchone()
    return "%s = %s" % (row["date"], row["close"]) if row else "（无）"


def _recent(cursor, table: str, stock_id, limit: int) -> str:
    cursor.execute(
        "SELECT date, close FROM %s WHERE stock_id = %%s ORDER BY date DESC LIMIT %%s"
        % table,
        (stock_id, limit),
    )
    rows = cursor.fetchall()
    if not rows:
        return "（无）"
    return " ".join("%s:%s" % (r["date"], r["close"]) for r in rows)


def main() -> int:
    source = MySqlFundDataSource.from_env()
    cursor = source.connection.cursor()
    try:
        print("== 一、校准记录（calibrationhistory）最近 8 条 ==")
        for symbol in ("SZ161125", "SZ161130"):
            stock_id = source.get_stock_id(symbol)
            if stock_id is None:
                print("%-10s 不在 stock 表" % symbol)
                continue
            cursor.execute(
                "SELECT date, close, time, num FROM calibrationhistory "
                "WHERE stock_id = %s ORDER BY date DESC LIMIT 8",
                (stock_id,),
            )
            rows = cursor.fetchall()
            print("%-10s 共 %d 条（下面是最新的）" % (symbol, len(rows)))
            for r in rows:
                print("   %s  cal=%s  time=%s  num=%s" % (
                    r["date"], r["close"], r["time"], r["num"]))

        print("\n== 二、各符号数据新鲜度（净值 / 行情） ==")
        for symbol in SYMBOLS:
            stock_id = source.get_stock_id(symbol)
            if stock_id is None:
                print("%-10s 不在 stock 表" % symbol)
                continue
            print("%-10s id=%-4s 净值最新 %-24s 行情最新 %s" % (
                symbol, stock_id,
                _latest(cursor, "netvaluehistory", stock_id),
                _latest(cursor, "stockhistory", stock_id),
            ))

        print("\n== 三、^GSPC / USCNY 最近 %d 条历史 ==" % LIMIT)
        for symbol, table in (("^GSPC", "netvaluehistory"),
                              ("^GSPC", "stockhistory"),
                              ("USCNY", "netvaluehistory")):
            stock_id = source.get_stock_id(symbol)
            print("%-8s %-16s %s" % (symbol, table, _recent(cursor, table, stock_id, LIMIT)))

        print("\n== 四、SZ161125 净值最近 %d 条 ==" % LIMIT)
        stock_id = source.get_stock_id("SZ161125")
        print(_recent(cursor, "netvaluehistory", stock_id, LIMIT))
    finally:
        cursor.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
