# -*- coding: utf-8 -*-
"""接通黄金 QDII 基金的「实时 EST」：补 hf_GC 行、配对、GLD 校准值。

## 为什么黄金没有实时 EST

黄金基金走的是**两级**链路，比港股 / 日经多一跳（``^HSI`` 直接配期货）::

    SZ161116 ──est──> GLD ──期货──> hf_GC
    （A股 LOF）      （美股ETF）    （纽约黄金）

本地缺的是**后一跳**与它的换算系数：

1. ``stock`` 表里**没有 hf_GC 行**（``hf_CL`` 纽约原油倒是有，id=15）；
2. ``futurespair`` 表里没有 ``GLD → hf_GC`` —— 于是
   ``futurescalib.future_symbol_of('GLD')`` 返回 ``None``，
   ``fundestcalc._futures_realtime_quote()`` 直接短路，
   **实时 EST 恒为空**（列消失，不是显示 0）；
3. ``calibrationhistory`` 里 GLD **没有任何校准行** ——
   即使补上配对，``realtime_source_value()`` 拿不到 ``cal`` 也返回 ``None``。

## 校准值为什么是 10.94 而不是 1.0

``cal`` 在这里充当**单位换算**：纽约黄金期货按「美元/盎司」报（≈3935），
GLD 是按「约 1/10 盎司份额」报的 ETF（≈361.65）。两者比值才是
"一个 GLD 份额值几个 GC 合约"：

    10.9441  ≈  3957 / 361.55

来源：生产端 ``sz161116cn.php`` 的「基金指数对照表」实测
（2026-11-03 抓取）::

    代码    跟踪 仓位  校准      日期        参考值
    GLD     GC   1     10.9441   2026-11-02  361.55

**不能把它当成"ETF 价 / 净值"那种 ``cal_m``**：GLD 的 ``est_cal`` 走
``fundestcalc._est_quote_ref()``，取的是 GLD 的**行情收盘价 / 净值**，
与 ``calibrationhistory`` 里这一行无关。所以这里写 10.94 不会污染 est_cal。

量级体检窗口取 ``[5, 20]``：黄金与 GLD 的历史比值在 9~12 之间浮动
（份额比例固定 1/10，余下是期货升贴水与合约换月），偏出这个区间说明
写进来的根本不是"期货/ETF"口径。

## 为什么 ``fundpair`` 也要写一行 ``GLD → hf_GC``

``futurespair`` 是本项目 ``futurescalib`` / ``quoteupdater`` 用的表；
``fundpair`` 是 PHP ``FundPairReference`` 用的表（配对页「参考数据」表
里那行 ``GC`` 就从它来）。两张表各管一条路，都要有。

**但不写「基金 → GLD」**：生产端页面的 ``GLD`` 行来自 est 标的
（``fundestmap`` / ``QdiiGetEstArray``），不来自 ``fundpair``。
给 6 只基金塞 ``fundpair`` 反而会让配对页多出一堆"配对交易"语义的行，
而它们的净值历史还不全 —— 越界。

用法::

    python -B dbtools/patch_gold_futures.py            # 预演（默认）
    python -B dbtools/patch_gold_futures.py --apply    # 真写
"""

from __future__ import annotations

import io
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto"))

from core.stockdata import MySqlFundDataSource  # noqa: E402

#: 需要补进 ``stock`` 的期货符号（已存在的跳过）。
STOCK_ROWS = [
    ("hf_GC", "纽约黄金"),
]

#: 「标的 → 期货」配对，``fundpair`` 与 ``futurespair`` 两张表都要写。
PAIRS = [
    ("GLD", "hf_GC"),
]

#: 要补的校准值：``(符号, 日期, 时刻, 值, num)``。值是**期货 / 标的**的比值。
#:
#: ``time`` 列是 NOT NULL 且无默认值，必须显式给；取 GLD 的收盘时刻
#: （美东 16:00）。``num`` 是参与平均的样本数，手写值填 1。
CALIBRATIONS = [
    ("GLD", "2026-11-02", "16:00:00", 10.9441, 1),
]

#: 期货 / 标的 的正常量级（黄金 ≈ 10.94）。
CAL_SANE_LOW = 5.0
CAL_SANE_HIGH = 20.0


def _ensure_stock(cursor) -> list:
    planned = []
    for symbol, name in STOCK_ROWS:
        cursor.execute("SELECT id FROM stock WHERE symbol = %s", (symbol,))
        row = cursor.fetchone()
        if row is not None:
            print("  %-10s 已在 stock 表（id=%s），跳过" % (symbol, row["id"]))
            continue
        print("  %-10s 待插入（%s）" % (symbol, name))
        planned.append((symbol, name))
    return planned


def _ensure_pairs(cursor, table: str, pending: set = frozenset()) -> list:
    planned = []
    for index_symbol, future_symbol in PAIRS:
        cursor.execute("SELECT id FROM stock WHERE symbol = %s", (index_symbol,))
        index_row = cursor.fetchone()
        cursor.execute("SELECT id FROM stock WHERE symbol = %s", (future_symbol,))
        future_row = cursor.fetchone()
        if index_row is None or future_row is None:
            missing = [s for s, row in ((index_symbol, index_row), (future_symbol, future_row))
                       if row is None]
            if pending.intersection(missing):
                print("  %-8s -> %-8s 待创建 %s 后自动配对"
                      % (index_symbol, future_symbol, "/".join(missing)))
            else:
                print("  %-8s -> %-8s 跳过（stock 表里缺符号）" % (index_symbol, future_symbol))
            continue
        cursor.execute(
            "SELECT stock_id FROM %s WHERE id = %%s LIMIT 1" % table, (index_row["id"],)
        )
        existing = cursor.fetchone()
        if existing is not None:
            if int(existing["stock_id"]) == int(future_row["id"]):
                print("  %-8s -> %-8s 已存在，跳过" % (index_symbol, future_symbol))
            else:
                print("  %-8s -> %-8s 已存在但配的是 id=%s，将改正"
                      % (index_symbol, future_symbol, existing["stock_id"]))
                planned.append((index_row["id"], future_row["id"]))
            continue
        print("  %-8s -> %-8s 待插入" % (index_symbol, future_symbol))
        planned.append((index_row["id"], future_row["id"]))
    return planned


def _ensure_calibration(cursor) -> list:
    """补 ``calibrationhistory`` 行；顺带体检量级。"""
    planned = []
    for symbol, date, clock, value, num in CALIBRATIONS:
        cursor.execute("SELECT id FROM stock WHERE symbol = %s", (symbol,))
        row = cursor.fetchone()
        if row is None:
            print("  %-8s 跳过（stock 表里缺符号）" % symbol)
            continue
        stock_id = row["id"]
        if not (CAL_SANE_LOW <= value <= CAL_SANE_HIGH):
            print("  %-8s %s = %s 量级不在 [%s, %s]，拒绝写入"
                  % (symbol, date, value, CAL_SANE_LOW, CAL_SANE_HIGH))
            continue
        cursor.execute(
            "SELECT id, close FROM calibrationhistory WHERE stock_id = %s AND `date` = %s",
            (stock_id, date),
        )
        existing = cursor.fetchone()
        if existing is not None:
            if abs(float(existing["close"]) - value) < 1e-6:
                print("  %-8s %s = %s 已存在，跳过" % (symbol, date, value))
            else:
                print("  %-8s %s 已存在但值 = %s，将改正为 %s"
                      % (symbol, date, existing["close"], value))
                planned.append((stock_id, date, clock, value, num))
            continue
        print("  %-8s %s 待插入 = %s" % (symbol, date, value))
        planned.append((stock_id, date, clock, value, num))
    return planned


def main() -> int:
    apply = "--apply" in sys.argv
    source = MySqlFundDataSource.from_env()
    cursor = source.connection.cursor()

    print("=== ① stock 表 ===")
    new_stock = _ensure_stock(cursor)

    # 配对要查 stock.id，所以补齐 stock 行必须**先落库**才能配对；
    # 预演时把待建的符号记为 pending，让输出能说清"最终会被补上"。
    if apply and new_stock:
        for symbol, name in new_stock:
            cursor.execute("INSERT INTO stock (symbol, name) VALUES (%s, %s)", (symbol, name))
        source.connection.commit()
        print("  已插入 %d 行 stock。" % len(new_stock))
        new_stock = []
    pending = {symbol for symbol, _name in new_stock}

    print("")
    print("=== ② fundpair（PHP FundPairReference / 配对页）===")
    new_fundpair = _ensure_pairs(cursor, "fundpair", pending)

    print("")
    print("=== ③ futurespair（futurescalib / quoteupdater）===")
    new_futurespair = _ensure_pairs(cursor, "futurespair", pending)

    print("")
    print("=== ④ GLD 校准值（期货 / ETF 比值，≈10.94）===")
    new_calibration = _ensure_calibration(cursor)

    total = len(new_fundpair) + len(new_futurespair) + len(new_calibration)
    print("")
    print("合计 %d 项改动（fundpair %d / futurespair %d / 校准 %d）"
          % (total, len(new_fundpair), len(new_futurespair), len(new_calibration)))

    if not apply:
        print("这是预演。真正执行请加 --apply。")
        cursor.close()
        source.connection.close()
        return 0
    if not total:
        print("无需改动。")
        cursor.close()
        source.connection.close()
        return 0

    for table, rows in (("fundpair", new_fundpair), ("futurespair", new_futurespair)):
        for index_id, future_id in rows:
            cursor.execute(
                "INSERT INTO %s (id, stock_id) VALUES (%%s, %%s) "
                "ON DUPLICATE KEY UPDATE stock_id = VALUES(stock_id)" % table,
                (index_id, future_id),
            )
    for stock_id, date, clock, value, num in new_calibration:
        cursor.execute(
            "INSERT INTO calibrationhistory (stock_id, `date`, close, `time`, num) "
            "VALUES (%s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE close = VALUES(close)",
            (stock_id, date, value, clock, num),
        )
    source.connection.commit()

    print("")
    print("=== 写入后核对 ===")
    cursor.execute(
        "SELECT a.symbol AS index_symbol, b.symbol AS future_symbol "
        "FROM futurespair fp JOIN stock a ON a.id = fp.id JOIN stock b ON b.id = fp.stock_id "
        "ORDER BY a.symbol"
    )
    print("  futurespair:")
    for row in cursor.fetchall():
        print("    %-10s -> %s" % (row["index_symbol"], row["future_symbol"]))
    cursor.execute(
        "SELECT s.symbol, c.`date`, c.close FROM calibrationhistory c "
        "JOIN stock s ON s.id = c.stock_id WHERE s.symbol = %s ORDER BY c.`date` DESC",
        ("GLD",),
    )
    print("  GLD 校准行:")
    for row in cursor.fetchall():
        print("    %s = %s" % (row["date"], row["close"]))

    cursor.close()
    source.connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
