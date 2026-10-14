"""清洗演示库里的 ``close = 1.0`` 占位行（QQQ / SPY 的种子垃圾）。

## 为什么要清

``1.0`` 是早期种子脚本"没有真实净值时先填个 1.0"留下的（pitfalls #60
记过同一个坑：照"优先取净值"抄会算出 ``30501.56 / 1.0 = 30501``，
那次事故的页面症状是官方 EST 345 万）。这些行本身不报错，只会在
**某条取数路径恰好落到它们头上时**静默给出荒谬比例：

* ``fundestcalc._est_quote_ref()`` 的第 2 级兜底是
  ``get_prev_history_close(est_id, cal_date)`` —— 校准日只要早于
  10-01，拿到的就是 09-29/09-30 的 ``1.0``；
* ``_net_value()`` 净值优先，10-01/10-02 的净值行也是 ``1.0``。

本次 P0.3 回填后校准日在 10-09、中间层最新日期在 10-07，恰好绕开了它们，
但这是运气不是设计 —— 清掉才安心。

## 怎么判定"这行是占位"

不按"值等于 1.0"一刀切（公募基金净值真的可能是 1.000）。规则：

1. 只查 **est 中间层符号**（``core.symbolinventory.est_symbols()``）——
   它们是美股 ETF / 指数，真实量级在几十到几万，``1.0`` 不可能是真值；
2. 且该符号**另有量级 > 10 的行** —— 用数据自己证明 ``1.0`` 是离群点。

两条同时满足才删。默认 dry-run，``--apply`` 才真删。
"""

from __future__ import annotations

import io
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

AUTO_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto")
for _path in (AUTO_DIR, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core.stockdata import MySqlFundDataSource  # noqa: E402
from core.symbolinventory import est_symbols  # noqa: E402

#: 量级门槛：本符号另有超过这个值的行，才认定 1.0 是离群占位。
SCALE_FLOOR = 10.0


def find_placeholders(cursor) -> list:
    """返回 ``[(表名, 符号, 日期), ...]``。"""
    targets = set(est_symbols())
    found = []
    for table in ("stockhistory", "netvaluehistory"):
        cursor.execute(
            "SELECT s.symbol, h.date, h.close FROM %s h "
            "JOIN stock s ON s.id = h.stock_id ORDER BY s.symbol, h.date" % table
        )
        by_symbol = {}
        for row in cursor.fetchall():
            by_symbol.setdefault(row["symbol"], []).append(row)
        for symbol, rows in by_symbol.items():
            if symbol not in targets:
                continue
            has_real_scale = any(float(row["close"] or 0) > SCALE_FLOOR for row in rows)
            if not has_real_scale:
                continue
            for row in rows:
                if abs(float(row["close"] or 0) - 1.0) < 1e-9:
                    found.append((table, symbol, str(row["date"])))
    return found


def main() -> int:
    apply = "--apply" in sys.argv
    source = MySqlFundDataSource.from_env()
    cursor = source.connection.cursor()

    rows = find_placeholders(cursor)
    print("=== 判定为占位的行：%d ===" % len(rows))
    for table, symbol, date in rows:
        print("  %-16s %-8s %s" % (table, symbol, date))

    if not apply:
        print("\n这是预演。真正删除请加 --apply。")
        cursor.close()
        source.connection.close()
        return 0

    deleted = 0
    for table, symbol, date in rows:
        cursor.execute("SELECT id FROM stock WHERE symbol = %s", (symbol,))
        stock = cursor.fetchone()
        if stock is None:
            continue
        cursor.execute(
            "DELETE FROM %s WHERE stock_id = %%s AND date = %%s AND close = 1.0" % table,
            (stock["id"], date),
        )
        deleted += cursor.rowcount
    source.connection.commit()
    print("\n已删除 %d 行。" % deleted)

    cursor.close()
    source.connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
