"""临时工具：把 SH513100 的仓位改成 1.0（ETF 默认仓位）。

## 依据

生产端 ``sh513100cn.php`` 的校准记录：

    2026-09-29  校准值 100358.446  时间 21:06  次数 1  净值 2.0379  对冲值 100358

按 ``StockCalcHedge()``（``php/stock.php:389``）``hedge = 校准值 / 仓位`` 反推：

    仓位 = 100358.446 / 100358 = 1.000004  ->  生产用的是 1.0

SH513100 是**上海 51 号段的 ETF**，`IsLofA()` 为假，所以
`GetDefaultPosition()` 返回 1.0。种子里原来写的 0.98 是错的。

同样反推另外三只，也全部与 `GetDefaultPosition()` 吻合：

    SZ161130  44997.025 / 47365 = 0.9500  （LOF -> 0.95）
    SZ161125  16505.969 / 17375 = 0.9500  （LOF -> 0.95）
    SZ162411   1289.731 /  1358 = 0.9497  （LOF -> 0.95）
"""

from __future__ import annotations

import io
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, "auto")

from stockdata import MySqlFundDataSource  # noqa: E402
from stocksymbol import StockSymbol  # noqa: E402

FIXES = {"SH513100": 1.0}


def main() -> int:
    source = MySqlFundDataSource.from_env()
    cursor = source.connection.cursor()

    print("=== 修改前 ===")
    cursor.execute("SELECT id, close FROM fundposition ORDER BY id")
    for row in cursor.fetchall():
        print("  id=%s close=%s" % (row["id"], row["close"]))

    print("")
    print("=== 计划修改 ===")
    for symbol, position in FIXES.items():
        stock_id = source.get_stock_id(symbol)
        is_lof = StockSymbol(symbol).is_lof_a()
        print("  %-10s id=%s is_lof_a()=%-5s -> 仓位 %.2f（对应 GetDefaultPosition()）"
              % (symbol, stock_id, is_lof, position))

    if "--apply" not in sys.argv:
        print("\n这是预演。真正执行请加 --apply。")
        cursor.close()
        source.connection.close()
        return 0

    for symbol, position in FIXES.items():
        stock_id = source.get_stock_id(symbol)
        cursor.execute("UPDATE fundposition SET close = %s WHERE id = %s", (position, stock_id))
        print("  已更新 %s（%d 行）" % (symbol, cursor.rowcount))
    source.connection.commit()

    print("")
    print("=== 修改后 ===")
    cursor.execute("SELECT id, close FROM fundposition ORDER BY id")
    for row in cursor.fetchall():
        print("  id=%s close=%s" % (row["id"], row["close"]))

    cursor.close()
    source.connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
