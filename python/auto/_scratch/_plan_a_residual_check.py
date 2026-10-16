"""Plan A 残留核对：SZ164906 的 2.3% 构成 + 4 只 ETF-est 基金 10-13 历史记录。

    python -B auto/_scratch/_plan_a_residual_check.py
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


def _rows(source, sql, params=()):
    cursor = source.connection.cursor()
    try:
        cursor.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]
    finally:
        cursor.close()


def main() -> int:
    source = MySqlFundDataSource.from_env()
    try:
        # ① SZ164906（KWEB）：我们用的两条中间层值 vs 基金净值 vs fundest
        kweb_id = source.get_stock_id("KWEB")
        fund_id = source.get_stock_id("SZ164906")
        print("--- KWEB（我们的中间层取数）---")
        for table in ("netvaluehistory", "stockhistory"):
            rows = _rows(source,
                         "SELECT `date`, close FROM %s WHERE stock_id = %%s "
                         "AND `date` >= '2026-10-12' ORDER BY `date`" % table, (kweb_id,))
            for row in rows:
                print("  %-16s %s = %s" % (table, row["date"], row["close"]))
        print("--- SZ164906 净值 / fundest ---")
        for row in _rows(source,
                         "SELECT `date`, close FROM netvaluehistory WHERE stock_id = %s "
                         "AND `date` >= '2026-10-12' ORDER BY `date`", (fund_id,)):
            print("  netvaluehistory  %s = %s" % (row["date"], row["close"]))
        for row in _rows(source,
                         "SELECT `date`, close, time FROM fundest WHERE stock_id = %s "
                         "ORDER BY `date`", (fund_id,)):
            print("  fundest          %s = %s  time=%s" % (row["date"], row["close"], row["time"]))
        print("")

        # ② 4 只 ETF-est 基金的 10-13 记录：est 价 vs 后来收盘价
        print("--- 4 只 ETF-est 基金 10-13 记录核对（est 当日快照 vs 收盘）---")
        for sym, est in (("SH513290", "IBB"), ("SH513350", "XBI"),
                         ("SZ159502", "XBI"), ("SZ159518", "XOP")):
            sid = source.get_stock_id(sym)
            eid = source.get_stock_id(est)
            fe = _rows(source, "SELECT close, time FROM fundest WHERE stock_id = %s "
                               "AND `date` = '2026-10-13'", (sid,))
            nav = _rows(source, "SELECT close FROM netvaluehistory WHERE stock_id = %s "
                                "AND `date` = '2026-10-13'", (sid,))
            print("  %-9s fundest[10-13] = %s (time=%s)  nav[10-13] = %s  est=%s"
                  % (sym, fe[0]["close"] if fe else "-",
                     fe[0]["time"] if fe else "-",
                     nav[0]["close"] if nav else "-", est))
    finally:
        source.connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
