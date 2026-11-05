# -*- coding: utf-8 -*-
"""接通白银 LOF（``SZ161226``）的「纽约白银」行与「实时 EST」。

## 缺的是什么

``sz161226cn.php`` 的参考数据表生产端是 4 行::

    AG0      12876     7.04%   2026-11-05 10:52   沪-白银连续
    SZ161226 1.579     4.78%   2026-11-05 10:52   国投白银LOF
    SI       53.29     1.92%   2026-11-04 21:51   纽约白银
    USDCNH   6.6677    -0.1%   2026-11-04 21:51   离岸人民币（香港）

本地原先只有 ``nf_AG0 → SZ161226`` 这一对（``fundpair``），
``hf_SI`` 连 ``stock`` 行都没有 —— 于是「SI」那行不存在，
「实时 EST」也恒等于「参考 EST」（没有期货可叠加）。

## 链路

::

    SZ161226 ──fundpair──> nf_AG0 ──futurespair──> hf_SI
    （国投白银LOF）        （沪银连续）           （纽约白银，美元/盎司）

PHP ``woody/res/php/_chinafuture.php:20-31`` 只在 ``SZ161226`` 上挂这条
实时回调，其余五只 A股商品基金没有（``$strRealtime = false``）::

    $strRealtime = 'hf_SI';   $strCNH = 'fx_susdcnh';
    $callback = '_RealtimeCallback';        // 1000 * SI * fx / 31.1035

换算与「为什么要换算」见 ``calc.futurescalib.FUTURES_VALUE_TRANSFORMS``。

## 为什么这张表也要写 ``fundpair``

两张表各管一条路（与黄金那次一致，见 ``patch_gold_futures.py``）：

* ``futurespair`` → ``futurescalib`` 求实时值 + ``futurescli`` 反推抓取名单；
* ``fundpair`` → 参考数据表里那一行（PHP ``FundPairReference`` 也是查它）。

**只写 ``futurespair``** 的话实时 EST 会通，但参考数据仍少一行；
**只写 ``fundpair``** 的话 ``futurescli`` 不会去抓 ``hf_SI``（除非命中
``FUTURES_VALUE_TRANSFORMS`` 那条兜底），快照停更、实时 EST 悄悄退回 AG0。

## 不写校准值

黄金那次要补 GLD 的 ``cal = 10.9441``（期货 / ETF），因为那条链路走
``÷ cal``。这里**不需要**：换算本身已经把美元/盎司折成元/千克，
与 ``est_cal``（AG0 的结算价）同空间，中间不再除任何 ``cal``。
``nf_AG0`` 在 ``calibrationhistory`` 里那行（274905）是另一个口径的旧数据，
本脚本不碰它 —— 换算分支不看它。

用法::

    python -B dbtools/patch_silver_si.py            # 预演（默认）
    python -B dbtools/patch_silver_si.py --apply    # 真写
"""

from __future__ import annotations

import io
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto"))

from core.stockdata import MySqlFundDataSource  # noqa: E402

#: 需要补进 ``stock`` 的符号（已存在的跳过）。名称照生产端参考数据表的「名称」列。
STOCK_ROWS = [
    ("hf_SI", "纽约白银"),
]

#: 「指数 → 期货」配对，``fundpair`` 与 ``futurespair`` 两张表都要写。
PAIRS = [
    ("nf_AG0", "hf_SI"),
]

#: 换算后的值应当落在 AG0 结算价的这个比例区间内。
#:
#: 沪银与纽约白银同属白银，扣掉汇率与单位换算后价差来自升贴水与
#: 交易时段，10% 已经比 ``MIDDLE_VALUE_TOLERANCE``（50%）严得多 ——
#: 这里只做"写完之后值像不像"的体检，不参与计算。
SANE_RATIO_LOW = 0.75
SANE_RATIO_HIGH = 1.25


def _ensure_stock(cursor) -> list:
    planned = []
    for symbol, name in STOCK_ROWS:
        cursor.execute("SELECT id FROM stock WHERE symbol = %s", (symbol,))
        row = cursor.fetchone()
        if row is not None:
            print("  %-8s 已在 stock 表（id=%s），跳过" % (symbol, row["id"]))
            continue
        print("  %-8s 待插入（%s）" % (symbol, name))
        planned.append((symbol, name))
    return planned


def _ensure_pairs(cursor, table: str, pending=frozenset()) -> list:
    planned = []
    for index_symbol, future_symbol in PAIRS:
        cursor.execute("SELECT id FROM stock WHERE symbol = %s", (index_symbol,))
        index_row = cursor.fetchone()
        cursor.execute("SELECT id FROM stock WHERE symbol = %s", (future_symbol,))
        future_row = cursor.fetchone()
        if index_row is None or future_row is None:
            missing = [s for s, row in ((index_symbol, index_row), (future_symbol, future_row))
                       if row is None]
            if set(missing).issubset(set(pending)):
                print("  %-8s -> %-8s 待创建 %s 后自动配对"
                      % (index_symbol, future_symbol, "/".join(missing)))
            else:
                print("  %-8s -> %-8s 跳过（stock 表里缺符号）" % (index_symbol, future_symbol))
            continue
        cursor.execute("SELECT stock_id FROM %s WHERE id = %%s LIMIT 1" % table,
                       (index_row["id"],))
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


def _check_scale(source) -> None:
    """体检：换算后的值与 AG0 的快照价、三档估值各是多少。

    **纯读**，用的是当前快照（``hf_SI`` 还没抓到时第一行会是空的 ——
    先跑 ``futurescli --apply`` 再回来看）。
    """
    from calc.futurescalib import FUTURES_VALUE_TRANSFORMS
    from calc.fundestcalc import build_estimate_calculator

    transform = FUTURES_VALUE_TRANSFORMS["nf_AG0"]

    def quote_of(symbol):
        reader = getattr(source, "get_stored_quote", None)
        return reader(symbol) if reader is not None else None

    futures = quote_of(transform.future_symbol)
    fx = quote_of(transform.fx_symbol)
    ag = quote_of("nf_AG0")
    print("  %-8s %s" % (transform.future_symbol,
                         "-" if futures is None else futures.price))
    print("  %-8s %s" % (transform.fx_symbol, "-" if fx is None else fx.price))
    if futures is not None and fx is not None:
        try:
            converted = float(futures.price) * float(fx.price) * transform.factor
        except (TypeError, ValueError):
            converted = None
        if converted is not None:
            ratio = ""
            if ag is not None:
                try:
                    value = converted / float(ag.price)
                    ok = SANE_RATIO_LOW <= value <= SANE_RATIO_HIGH
                    ratio = "  对 AG0 快照价 %s = %.4f%s" % (
                        ag.price, value, "" if ok else "  ← 量级可疑")
                except (TypeError, ValueError, ZeroDivisionError):
                    pass
            print("  换算后 %.2f 元/千克%s" % (converted, ratio))

    calculator = build_estimate_calculator(source)
    for name in ("compute_official", "compute_fair", "compute_realtime"):
        record = getattr(calculator, name)("SZ161226")
        print("  %-18s %s" % (name, "-" if record is None else "%.4f" % record.value))


def main() -> int:
    apply = "--apply" in sys.argv
    source = MySqlFundDataSource.from_env()
    cursor = source.connection.cursor()

    print("=== ① stock 表 ===")
    new_stock = _ensure_stock(cursor)

    # 配对要查 stock.id，所以补 stock 行必须**先落库**才能配对；
    # 预演时把待建的符号记为 pending，让输出能说清"最终会被补上"。
    if apply and new_stock:
        for symbol, name in new_stock:
            cursor.execute("INSERT INTO stock (symbol, name) VALUES (%s, %s)", (symbol, name))
        source.connection.commit()
        print("  已插入 %d 行 stock。" % len(new_stock))
        new_stock = []
    pending = {symbol for symbol, _name in new_stock}

    print("")
    print("=== ② fundpair（参考数据行的来源）===")
    new_fundpair = _ensure_pairs(cursor, "fundpair", pending)

    print("")
    print("=== ③ futurespair（futurescalib / futurescli）===")
    new_futurespair = _ensure_pairs(cursor, "futurespair", pending)

    total = len(new_fundpair) + len(new_futurespair)
    print("")
    print("合计 %d 项改动（fundpair %d / futurespair %d）"
          % (total, len(new_fundpair), len(new_futurespair)))
    if pending:
        # 预演时 stock 行还没建，配对算不出 id —— 但 --apply 会在同一次运行里
        # 先建行再配对，所以这里说清"不是漏了"，免得看着像无需改动。
        waiting = [pair for pair in PAIRS if pair[0] in pending or pair[1] in pending]
        print("另有 %d 处配对要等上面的 stock 行建好（--apply 一次即可，先建行后配对）"
              % len(waiting))

    if apply and total:
        for table, rows in (("fundpair", new_fundpair), ("futurespair", new_futurespair)):
            for index_id, future_id in rows:
                cursor.execute(
                    "INSERT INTO %s (id, stock_id) VALUES (%%s, %%s) "
                    "ON DUPLICATE KEY UPDATE stock_id = VALUES(stock_id)" % table,
                    (index_id, future_id),
                )
        source.connection.commit()
        print("已写入。")

    print("")
    print("=== ④ 体检（纯读）===")
    _check_scale(source)

    if not apply:
        print("")
        print("这是预演。真正执行请加 --apply。")
    cursor.close()
    source.connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
