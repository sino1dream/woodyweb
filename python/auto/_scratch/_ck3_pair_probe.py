# -*- coding: utf-8 -*-
"""CK3 侦察：查演示库里四张配对表到底有什么数据。

## 为什么需要

CK3 要新建 ``/stock/<symbol>`` 承载非基金详情（AB/AH/ADR 对比 + trading +
SMA）。但 CK2 迁的 ``PairReference`` 靠 **SQL 查配对表** 才能构造 ——
如果演示库里 ``abpair`` / ``ahpair`` / ``adrpair`` 是空的，新路由就是个空壳，
页面什么都渲染不出来。所以动手前先查清楚：

1. 四张配对表各有多少行、都是什么符号；
2. 这些符号在 ``stock`` 表里有没有对应记录（没有的话 ``GetStockId`` 会失败）；
3. 有没有行情数据（决定页面格子是否为空）。

只读，不改库。用法：

    cd python/auto && python -B _scratch/_ck3_pair_probe.py
"""

from __future__ import annotations

import io
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.stockdata import MySqlFundDataSource  # noqa: E402

#: 四张配对表。表名 -> (表内 A 侧列名, 配对侧列名)
PAIR_TABLES = [
    ("abpair", "ab"),
    ("ahpair", "ah"),
    ("adrpair", "adr"),
    ("fundpair", "fund"),
]


def _dump_table(cursor, table: str, tag: str) -> None:
    """打印一张配对表的全部行（A 侧符号 -> 配对符号）。"""
    print("=== %s ===" % table)
    try:
        cursor.execute(
            "SELECT a.symbol AS self_symbol, b.symbol AS pair_symbol "
            "FROM %s p JOIN stock a ON a.id = p.id JOIN stock b ON b.id = p.stock_id "
            "ORDER BY a.symbol" % table
        )
        rows = cursor.fetchall()
    except Exception as exc:  # noqa: BLE001 - 表不存在也要继续，别中断侦察
        print("  查询失败（表可能不存在）：%s" % exc)
        return

    if not rows:
        print("  (空表)")
        return
    for row in rows:
        print("  %-12s -> %-12s" % (row["self_symbol"], row["pair_symbol"]))
    print("  共 %d 行" % len(rows))
    print("")


def _dump_non_fund_symbols(cursor) -> None:
    """打印 stock 表里所有非基金符号，看有哪些候选可以冒烟。"""
    print("=== stock 表全部符号（按前缀归类）===")
    cursor.execute("SELECT symbol, name FROM stock ORDER BY symbol")
    rows = cursor.fetchall()
    if not rows:
        print("  (空表)")
        return

    buckets = {"A股": [], "H股": [], "美股/ADR": [], "外汇/期货/指数": [], "其它": []}
    for row in rows:
        symbol = row["symbol"]
        if symbol.startswith(("SH", "SZ", "BJ")):
            buckets["A股"].append(symbol)
        elif len(symbol) == 5 and symbol.isdigit() and symbol.startswith("0"):
            buckets["H股"].append(symbol)
        elif symbol.startswith(("fx_", "hf_", "nf_", "^", "znb_")):
            buckets["外汇/期货/指数"].append(symbol)
        elif symbol.isalpha() and symbol.isupper():
            buckets["美股/ADR"].append(symbol)
        else:
            buckets["其它"].append(symbol)

    for label, symbols in buckets.items():
        if symbols:
            print("  %-14s %2d 个：%s" % (label, len(symbols), ", ".join(symbols[:20])))
    print("")


def _dump_quote_coverage(cursor, symbols) -> None:
    """抽查这些符号有没有行情数据。"""
    if not symbols:
        return
    print("=== 行情覆盖抽查 ===")
    for symbol in symbols[:12]:
        cursor.execute("SELECT id FROM stock WHERE symbol = %s", (symbol,))
        row = cursor.fetchone()
        if row is None:
            print("  %-12s stock 表缺记录" % symbol)
            continue
        cursor.execute(
            "SELECT COUNT(*) AS cnt FROM stockhistory WHERE stock_id = %s", (row["id"],)
        )
        hist = cursor.fetchone()["cnt"]
        cursor.execute(
            "SELECT COUNT(*) AS cnt FROM realtimequote WHERE stock_id = %s", (row["id"],)
        )
        rt = cursor.fetchone()["cnt"]
        print("  %-12s history=%-5d realtime=%d" % (symbol, hist, rt))
    print("")


def main() -> int:
    source = MySqlFundDataSource.from_env()
    cursor = source.connection.cursor()

    for table, _tag in PAIR_TABLES:
        _dump_table(cursor, table, _tag)

    _dump_non_fund_symbols(cursor)

    # 把四张配对表里出现过的符号汇总起来抽查行情覆盖
    probe = []
    for table, _tag in PAIR_TABLES:
        try:
            cursor.execute(
                "SELECT a.symbol AS s FROM %s p JOIN stock a ON a.id = p.id" % table
            )
            probe.extend(row["s"] for row in cursor.fetchall())
            cursor.execute(
                "SELECT b.symbol AS s FROM %s p JOIN stock b ON b.id = p.stock_id" % table
            )
            probe.extend(row["s"] for row in cursor.fetchall())
        except Exception:  # noqa: BLE001
            continue
    _dump_quote_coverage(cursor, sorted(set(probe)))

    cursor.close()
    source.connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
