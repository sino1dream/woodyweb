"""P0.6 收尾取样：把落库结果（校准两层 + fundest + 期货）打成一张表。

只读。写文档 / 复盘时用它取数，免得凭记忆写数字。

    python -B auto/_scratch/_p0_6_state.py
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
        print("=== fundest ===")
        rows = _rows(source, "SELECT COUNT(*) c, COUNT(DISTINCT stock_id) s FROM fundest")
        print("行数 %s，覆盖符号 %s" % (rows[0]["c"], rows[0]["s"]))
        for row in _rows(source, "SELECT `date`, COUNT(*) c FROM fundest GROUP BY `date` ORDER BY `date` DESC LIMIT 5"):
            print("  %s  %s 行" % (row["date"], row["c"]))

        print("")
        print("=== calibrationhistory：最近一次按日期 ===")
        for row in _rows(source, "SELECT `date`, COUNT(*) c FROM calibrationhistory GROUP BY `date` ORDER BY `date` DESC LIMIT 5"):
            print("  %s  %s 行" % (row["date"], row["c"]))

        print("")
        print("=== 中间层与指数的校准值 ===")
        for symbol in ("QQQ", "SPY", "^NDX", "^GSPC"):
            stock_id = source.get_stock_id(symbol)
            if stock_id is None:
                print("  %-8s 不在 stock 表" % symbol)
                continue
            rows = _rows(source,
                         "SELECT `date`, close FROM calibrationhistory WHERE stock_id = %s "
                         "ORDER BY `date` DESC LIMIT 1", (stock_id,))
            print("  %-8s %s" % (symbol, ("%s @ %s" % (rows[0]["close"], rows[0]["date"])) if rows else "无"))

        print("")
        print("=== 基金层（有 est 符号的基金）===")
        funds = sorted(qdii_est_symbol_map())
        missing = []
        print("  %-10s %-16s %-12s" % ("符号", "校准值", "日期"))
        for symbol in funds:
            stock_id = source.get_stock_id(symbol)
            if stock_id is None:
                missing.append(symbol)
                continue
            rows = _rows(source,
                         "SELECT `date`, close FROM calibrationhistory WHERE stock_id = %s "
                         "ORDER BY `date` DESC LIMIT 1", (stock_id,))
            print("  %-10s %-16s %-12s" % (
                symbol, rows[0]["close"] if rows else "-", rows[0]["date"] if rows else "-"))
        print("")
        print("有 est 符号的基金 %d 只，缺 stock 行 %d 只" % (len(funds), len(missing)))
    finally:
        source.connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
