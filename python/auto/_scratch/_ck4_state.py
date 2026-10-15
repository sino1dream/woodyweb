"""CK4-D：看一眼写进去的样本与有没有误伤别的表。

    python -B auto/_scratch/_ck4_state.py
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

SYMBOL = "SH513050"


def main() -> int:
    source = MySqlFundDataSource.from_env()
    cursor = source.connection.cursor()

    def rows(sql, params=()):
        cursor.execute(sql, params)
        return [dict(r) for r in cursor.fetchall()]

    fund_id = source.get_stock_id(SYMBOL)
    print("基金 %s -> stock_id %s" % (SYMBOL, fund_id))

    print()
    print("holdingsdate:", rows(
        "SELECT id, `date` FROM holdingsdate WHERE id = %s", (fund_id,)))
    print("holdings 行数:", rows(
        "SELECT COUNT(*) AS n FROM holdings WHERE stock_id = %s", (fund_id,))[0]["n"])

    holdings = rows(
        "SELECT h.holding_id, h.ratio, s.symbol FROM holdings h "
        "JOIN stock s ON s.id = h.holding_id WHERE h.stock_id = %s "
        "ORDER BY h.ratio DESC", (fund_id,))
    print("持仓（前 6 只）:")
    for row in holdings[:6]:
        print("   %-8s %8.2f%%" % (row["symbol"], float(row["ratio"])))
    print("   共 %d 只，比例合计 %.2f%%" % (
        len(holdings), sum(float(r["ratio"]) for r in holdings)))

    print()
    print("净值:", rows(
        "SELECT `date`, close FROM netvaluehistory WHERE stock_id = %s "
        "ORDER BY `date` DESC LIMIT 3", (fund_id,)))

    #: 有没有误删别的表：10-13 那天还有多少行、都是谁
    day = rows(
        "SELECT s.symbol, h.close, h.adjclose FROM stockhistory h "
        "JOIN stock s ON s.id = h.stock_id WHERE h.`date` = '2026-10-13' "
        "ORDER BY s.symbol")
    print()
    print("stockhistory @2026-10-13 共 %d 行" % len(day))
    symbols = [r["symbol"] for r in day]
    print("   符号数 %d：%s" % (len(symbols), symbols[:12]))
    others = [s for s in symbols if s in ("^NDX", "^GSPC", "QQQ", "SPY", "hf_NQ")]
    print("   中间层/期货那几个还在不在：%s" % (others or "（都不在 —— 可能被误删）"))

    total = rows("SELECT COUNT(*) AS n FROM stockhistory")[0]["n"]
    print("stockhistory 总行数: %d" % total)

    cursor.close()
    source.connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
