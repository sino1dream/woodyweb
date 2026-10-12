"""临时查看：当前 calibrationhistory 全表。"""

from __future__ import annotations

import io
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto"))

from core.stockdata import MySqlFundDataSource  # noqa: E402

source = MySqlFundDataSource.from_env()
cursor = source.connection.cursor()
cursor.execute(
    "SELECT c.id, s.symbol, c.`date`, c.close, c.num FROM calibrationhistory c "
    "JOIN stock s ON s.id = c.stock_id ORDER BY s.symbol, c.`date`"
)
print("%-6s %-10s %-12s %-20s %s" % ("id", "符号", "日期", "校准值", "num"))
print("-" * 62)
for row in cursor.fetchall():
    print("%-6s %-10s %-12s %-20s %s" % (
        row["id"], row["symbol"], row["date"], row["close"], row["num"]))
cursor.close()

print("")
print("=== 中间层的净值为多少 ===")
for symbol in ("QQQ", "SPY"):
    stock_id = source.get_stock_id(symbol)
    record = source.get_latest_net_value(stock_id)
    print("  %-4s 最新净值 = %s (%s)" % (
        symbol, getattr(record, "close", None), getattr(record, "date", None)))
    close = source.get_latest_history_close(stock_id)
    print("       最新收盘价 = %s" % close)

source.connection.close()
