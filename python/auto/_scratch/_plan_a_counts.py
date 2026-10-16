"""Plan A 数据修复行数核对（写文档用）。

    python -B auto/_scratch/_plan_a_counts.py
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

US_SYMBOLS = ("AGG", "IBB", "IEO", "IXC", "KWEB", "QQQ", "RSPH", "SPY",
              "VGT", "VNQ", "XBI", "XLY", "XOP", "^DJI")


def main() -> int:
    source = MySqlFundDataSource.from_env()
    try:
        cursor = source.connection.cursor()
        try:
            # ① 14 个美股符号的日线行数与日期范围
            ids = []
            for sym in US_SYMBOLS:
                sid = source.get_stock_id(sym)
                ids.append(str(sid) if sid is not None else "-1")
            cursor.execute(
                "SELECT COUNT(*) AS n, MIN(`date`) AS lo, MAX(`date`) AS hi "
                "FROM stockhistory WHERE stock_id IN (%s)" % ",".join(ids))
            row = cursor.fetchone()
            print("14 个美股符号 stockhistory：%s 行  %s ~ %s"
                  % (row["n"], row["lo"], row["hi"]))

            # ② 4 币种官方中间价行数
            ids = []
            for sym in ("USCNY", "HKCNY", "EUCNY", "JPCNY"):
                sid = source.get_stock_id(sym)
                ids.append(str(sid) if sid is not None else "-1")
            cursor.execute(
                "SELECT COUNT(*) AS n FROM netvaluehistory WHERE stock_id IN (%s)"
                % ",".join(ids))
            print("4 币种 netvaluehistory：%s 行" % cursor.fetchone()["n"])

            # ③ 13 个中间层 ETF 的派生净值最新日期
            etfs = ("AGG", "IBB", "IEO", "IXC", "KWEB", "QQQ", "RSPH", "SPY",
                    "VGT", "VNQ", "XBI", "XLY", "XOP")
            print("13 个中间层 ETF 派生净值最新日：")
            for sym in etfs:
                sid = source.get_stock_id(sym)
                cursor.execute(
                    "SELECT MAX(`date`) AS d FROM netvaluehistory WHERE stock_id = %s", (sid,))
                print("  %-6s %s" % (sym, cursor.fetchone()["d"]))

            # ④ 33 只基金净值最新日
            from calc.fundcalc import qdii_est_symbol_map
            latest = set()
            for sym in sorted(qdii_est_symbol_map()):
                sid = source.get_stock_id(sym)
                cursor.execute(
                    "SELECT MAX(`date`) AS d FROM netvaluehistory WHERE stock_id = %s", (sid,))
                latest.add(str(cursor.fetchone()["d"]))
            print("33 只基金净值最新日集合：%s" % sorted(latest))

            # ⑤ 基金层校准：10-13 / 10-14 行数
            cursor.execute(
                "SELECT COUNT(*) AS n FROM calibrationhistory ch "
                "JOIN stock s ON s.id = ch.stock_id "
                "WHERE ch.`date` IN ('2026-10-13', '2026-10-14') "
                "AND s.symbol IN %s", (tuple(sorted(qdii_est_symbol_map())),))
            print("33 只基金校准行（10-13 + 10-14）：%s 行" % cursor.fetchone()["n"])

            # ⑥ fundest 总量与最新日
            cursor.execute("SELECT COUNT(*) AS n, MAX(`date`) AS d FROM fundest")
            row = cursor.fetchone()
            print("fundest：%s 行，最新 %s" % (row["n"], row["d"]))
        finally:
            cursor.close()
    finally:
        source.connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
