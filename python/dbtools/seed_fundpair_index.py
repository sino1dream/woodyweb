"""给 ``fundpair`` 补"指数 → 期货"两行（实时 EST 的前置数据）。

## 为什么需要

生产端 ``sz161130cn.php`` 的参考数据表第 3 行是 ``hf_NQ``，它来自
``GetRealtimeRef()``::

    function GetRealtimeRef() {
        return method_exists($this->est_ref, 'GetPairRef') ? $this->est_ref->GetPairRef() : false;
    }

而 ``est_ref`` 是 ``FundPairReference('^NDX')``，构造时查
``GetFundPairSql()->GetPairSymbol('^NDX')`` —— 也就是 ``fundpair`` 里
``id = ^NDX`` 那一行的 ``stock_id``。

演示库里只有"基金 → ETF"三行（``SZ161130 -> QQQ`` 等），**没有"指数 → 期货"**，
所以：

* 参考数据表出不来 ``hf_NQ`` 行；
* ``refobj`` 的 ``get_realtime_ref()`` 恒为 ``None`` →
  **实时 EST 那一档永远是空的**（页面 EST 表少两列）。

本项目另有 ``futurespair`` 表（``^NDX -> hf_NQ``），那是接期货时给
``fundestcalc`` 用的。**两张表方向一致但用途不同**，PHP 那边实时 EST 走的是
``fundpair``，所以这里补的也是 ``fundpair``。

用法：

    python -B dbtools\\seed_fundpair_index.py            # 预演
    python -B dbtools\\seed_fundpair_index.py --apply    # 真写
"""

from __future__ import annotations

import io
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto"))

from core.stockdata import MySqlFundDataSource  # noqa: E402

#: （指数, 它跟踪的期货）。与 ``futurespair`` 的内容一致。
PAIRS = [
    ("^NDX", "hf_NQ"),
    ("^GSPC", "hf_ES"),
]


def main() -> int:
    apply = "--apply" in sys.argv
    source = MySqlFundDataSource.from_env()
    cursor = source.connection.cursor()

    print("=== 现有 fundpair ===")
    cursor.execute(
        "SELECT f.id, a.symbol AS self_symbol, b.symbol AS pair_symbol "
        "FROM fundpair f JOIN stock a ON a.id = f.id JOIN stock b ON b.id = f.stock_id "
        "ORDER BY a.symbol"
    )
    for row in cursor.fetchall():
        print("  %-10s -> %s" % (row["self_symbol"], row["pair_symbol"]))

    print("")
    print("=== 计划补的行 ===")
    planned = []
    for index_symbol, future_symbol in PAIRS:
        cursor.execute("SELECT id FROM stock WHERE symbol = %s", (index_symbol,))
        index_row = cursor.fetchone()
        cursor.execute("SELECT id FROM stock WHERE symbol = %s", (future_symbol,))
        future_row = cursor.fetchone()
        if index_row is None or future_row is None:
            print("  跳过 %s -> %s（stock 表里缺符号）" % (index_symbol, future_symbol))
            continue

        cursor.execute("SELECT 1 FROM fundpair WHERE id = %s LIMIT 1", (index_row["id"],))
        if cursor.fetchone() is not None:
            print("  %-8s -> %-8s 已存在，跳过" % (index_symbol, future_symbol))
            continue

        print("  %-8s (id=%-3s) -> %-8s (id=%s)"
              % (index_symbol, index_row["id"], future_symbol, future_row["id"]))
        planned.append((index_symbol, index_row["id"], future_symbol, future_row["id"]))

    print("")
    print("计划写入 %d 行。" % len(planned))
    if not planned:
        print("无需改动。")
        cursor.close()
        source.connection.close()
        return 0

    if not apply:
        print("这是预演。真正执行请加 --apply。")
        cursor.close()
        source.connection.close()
        return 0

    for _index_symbol, index_id, _future_symbol, future_id in planned:
        cursor.execute(
            "INSERT INTO fundpair (id, stock_id) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE stock_id = VALUES(stock_id)",
            (index_id, future_id),
        )
    source.connection.commit()

    print("")
    print("=== 写入后 ===")
    cursor.execute(
        "SELECT a.symbol AS self_symbol, b.symbol AS pair_symbol "
        "FROM fundpair f JOIN stock a ON a.id = f.id JOIN stock b ON b.id = f.stock_id "
        "ORDER BY a.symbol"
    )
    for row in cursor.fetchall():
        print("  %-10s -> %s" % (row["self_symbol"], row["pair_symbol"]))

    cursor.close()
    source.connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
