"""临时工具：建立期货与指数的配对（``futurespair``）并写入基础数据。

## 配对依据

实时 EST 的做法（作者 2020-09-15 / 2024-04-19 两篇文档）：

> 实时 EST 则是在**参考 EST** 的基础上，叠加了 **NQ 期货**实时数据的影响。

所以每只纳指/标普基金要能找到"它该看哪个期货"：

===========  ==========  ==========================================
指数          期货         说明
===========  ==========  ==========================================
``^NDX``     ``hf_NQ``   纳斯达克100 → NQ 期货（E-mini Nasdaq 100）
``^GSPC``    ``hf_ES``   标普500 → ES 期货（E-mini S&P 500）
===========  ==========  ==========================================

原油类用 ``hf_CL``，日经用 ``hf_NK``（本项目暂未涉及对应基金）。

**这个配对必须先有**：实时 EST = 期货实时价 × 校准比例，没有配对就不知道该拿哪个期货。

## 与 PHP 的对应

``FundPairReference::CheckAdjustFactorTime()`` 要求期货与指数的
**日期与时刻相同**才做自动校准，然后 ``AverageCalibraion()`` 写
``校准值 = QdiiGetCalibration(期货价, 汇率, 指数价)``。
本脚本只建配对；校准值由 ``futurescalib.py`` 在两者时刻对齐时写入。
"""

from __future__ import annotations

import io
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto"))

from core.stockdata import MySqlFundDataSource  # noqa: E402

# (指数符号, 期货符号, 期货中文名)
PAIRS = [
    ("^NDX", "hf_NQ", "纳斯达克100指数期货"),
    ("^GSPC", "hf_ES", "标普500指数期货"),
]

#: 还需要有 ``stock`` 行的符号（期货要能被抓行情，就得先在 stock 表里）
EXTRA_STOCKS = [
    ("hf_NQ", "纳斯达克100指数期货"),
    ("hf_ES", "标普500指数期货"),
    ("hf_CL", "纽约原油"),
    ("hf_NK", "日经225指数期货"),
]


def main() -> int:
    apply = "--apply" in sys.argv
    source = MySqlFundDataSource.from_env()
    cursor = source.connection.cursor()

    print("=== 计划 ===")
    print("  1) 补 stock 行：%s" % ", ".join(s for s, _ in EXTRA_STOCKS))
    print("  2) 写 futurespair：")
    for index, future, name in PAIRS:
        print("       %-8s -> %-8s (%s)" % (index, future, name))

    if not apply:
        print("\n这是预演。真正执行请加 --apply。")
        cursor.close()
        source.connection.close()
        return 0

    # 1) 补 stock 行
    for symbol, name in EXTRA_STOCKS:
        cursor.execute(
            "INSERT INTO stock (symbol, name) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE name = VALUES(name)",
            (symbol, name),
        )
    source.connection.commit()

    # 2) 写 futurespair（id = 指数，stock_id = 期货 —— 与 PHP PairSql 的列语义一致）
    for index_symbol, future_symbol, _name in PAIRS:
        cursor.execute("SELECT id FROM stock WHERE symbol = %s", (index_symbol,))
        row = cursor.fetchone()
        if row is None:
            print("  跳过（stock 表里没有 %s）" % index_symbol)
            continue
        index_id = row["id"]
        cursor.execute("SELECT id FROM stock WHERE symbol = %s", (future_symbol,))
        row = cursor.fetchone()
        if row is None:
            print("  跳过（stock 表里没有 %s）" % future_symbol)
            continue
        future_id = row["id"]
        cursor.execute(
            "INSERT INTO futurespair (id, stock_id) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE stock_id = VALUES(stock_id)",
            (index_id, future_id),
        )
        print("  已写 %s(id=%s) -> %s(id=%s)" % (index_symbol, index_id, future_symbol, future_id))
    source.connection.commit()

    print("")
    print("=== 核对 ===")
    cursor.execute(
        "SELECT f.id, i.symbol AS index_symbol, f.stock_id, u.symbol AS future_symbol "
        "FROM futurespair f "
        "JOIN stock i ON i.id = f.id "
        "JOIN stock u ON u.id = f.stock_id"
    )
    for row in cursor.fetchall():
        print("  %-8s -> %-8s" % (row["index_symbol"], row["future_symbol"]))

    cursor.close()
    source.connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
