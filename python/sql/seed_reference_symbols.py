"""P2：把演示库的符号补到能撑起生产端的 8 行参考数据。

## 补什么

生产端 ``sz161130cn.php`` 参考数据表 8 行::

    SZ161130   基金自己
    ^NDX       估值标的
    hf_NQ      实时标的（期货）
    QQQ        杠杆标的
    TQQQ       杠杆标的
    USDCNH     即期外汇（离岸）
    USDCNY     即期外汇（在岸）
    USCNY      中间价

本库原本只有 ``SZ161130/^NDX/USDCNY/USSCNY`` 四条能出数据，缺的：

============  ==========================================================
缺的符号       为什么缺 / 补什么
============  ==========================================================
``TQQQ``       不在 ``stock`` 表 → 补行。它是 3 倍做多纳指 ETF，跟踪 ``^NDX``
``USDCNH``     不在 ``stock`` 表 → 补行（离岸人民币，参考数据表用它合计美元敞口）
``USDCNY``     不在 ``stock`` 表 → 补行（在岸人民币）
``QQQ → ^NDX`` ``fundpair`` 缺"ETF 跟踪指数"行。**注意本库另有
               ``SZ161130 → QQQ``（基金 → ETF），两者含义不同、都要有**
``SPY → ^GSPC`` 同上
============  ==========================================================

## 为什么要 ``QQQ → ^NDX`` 才出得来 ``QQQ`` 行

``reference_service._leverage_symbols()`` 查的是"**谁跟踪我**"：
``fundpair.stock_id = ^NDX`` 的那些 ``id``。本库原来只有
``SZ161130/SH513100 → QQQ`` 与 ``SZ161125 → SPY``，都是"基金 → ETF"，
所以查 ``^NDX`` 谁跟踪它，答案是空的。

用法：

    python -B sql\\seed_reference_symbols.py            # 预演
    python -B sql\\seed_reference_symbols.py --apply    # 真写
"""

from __future__ import annotations

import io
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto"))

from core.stockdata import MySqlFundDataSource  # noqa: E402

#: 要补的标的（符号, 中文名）。名字按行业惯例，与生产端页面口径一致。
#:
#: **即期外汇用 ``fx_`` 前缀而不是 ``USDCNH``**：``refobj.QDII_REGIONS`` 里
#: 四个地区的即期符号就是 ``fx_susdcny`` / ``fx_shkdcny`` / ``fx_sjpycny`` /
#: ``fx_seurcny``，而它们**都不在 ``stock`` 表里** —— 所以参考数据表的外汇行
#: 一直取不到行情。补这四个才是对得上代码的做法；
#: 加 ``USDCNH``/``USDCNY`` 那种裸符号代码从不引用，只会造出"另一个种写法的
#: 同一个东西"（这正是要避免的口径重影）。
STOCKS = [
    ("TQQQ", "三倍做多纳斯达克100ETF"),
    ("fx_susdcny", "美元离岸人民币即期"),
    ("fx_shkdcny", "港元人民币即期"),
    ("fx_sjpycny", "日元人民币即期"),
    ("fx_seurcny", "欧元人民币即期"),
]

#: 要补的配对（本体 -> 被跟踪标的）。只补"ETF 跟踪指数"，不动"基金跟踪 ETF"。
PAIRS = [
    ("QQQ", "^NDX"),
    ("TQQQ", "^NDX"),
    ("SPY", "^GSPC"),
]

#: 要**删掉**的历史遗留错误行（符号 -> 原因）。
#:
#: 这两个是我在 2026-10-10 第一版脚本里按生产端页面显示名加的，
#: 但代码里没有任何地方引用它们 —— 参考数据表的外汇行走的是
#: ``QDII_REGIONS`` 的 ``fx_*`` 符号。留着只会让"两个名字指同一个东西"。
REMOVE_STOCKS = [
    ("USDCNH", "代码引用的是 fx_susdcny，不是 USDCNH"),
    ("USDCNY", "代码引用的是 fx_susdcny，不是 USDCNY"),
]


def main() -> int:
    apply = "--apply" in sys.argv
    source = MySqlFundDataSource.from_env()
    cursor = source.connection.cursor()

    if not apply:
        print("=== 预演（不改数据）===")
        _plan(cursor)
        print("")
        print("这是预演。真正执行请加 --apply。")
        cursor.close()
        source.connection.close()
        return 0

    print("=== ① 清掉遗留的错误符号 ===")
    for symbol, reason in REMOVE_STOCKS:
        cursor.execute("SELECT id FROM stock WHERE symbol = %s", (symbol,))
        row = cursor.fetchone()
        if row is None:
            print("  %-9s 不在库里，跳过" % symbol)
            continue
        # 只有"没有任何引用"时才删 —— 删之前先确认没被别的表引用
        cursor.execute("SELECT 1 FROM fundpair WHERE id = %s OR stock_id = %s LIMIT 1",
                       (row["id"], row["id"]))
        if cursor.fetchone() is not None:
            print("  %-9s 被 fundpair 引用，**保留**（原因：%s）" % (symbol, reason))
            continue
        print("  %-9s 删除（%s）" % (symbol, reason))
        cursor.execute("DELETE FROM stock WHERE id = %s", (row["id"],))

    print("")
    print("=== ② 补 stock 表的符号 ===")
    for symbol, name in STOCKS:
        cursor.execute("SELECT id FROM stock WHERE symbol = %s", (symbol,))
        if cursor.fetchone() is not None:
            print("  %-12s 已存在，跳过" % symbol)
            continue
        print("  %-12s 新增  %s" % (symbol, name))
        cursor.execute(
            "INSERT INTO stock (symbol, name) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE name = VALUES(name)",
            (symbol, name),
        )

    # 标的必须先写库再解析配对 —— TQQQ 的 id 这时才存在
    print("")
    print("=== ③ 补 fundpair 的'ETF 跟踪指数' ===")
    for self_symbol, pair_symbol in PAIRS:
        cursor.execute("SELECT id FROM stock WHERE symbol = %s", (self_symbol,))
        self_row = cursor.fetchone()
        cursor.execute("SELECT id FROM stock WHERE symbol = %s", (pair_symbol,))
        pair_row = cursor.fetchone()
        if self_row is None or pair_row is None:
            print("  跳过 %s -> %s（缺符号）" % (self_symbol, pair_symbol))
            continue
        cursor.execute("SELECT 1 FROM fundpair WHERE id = %s LIMIT 1", (self_row["id"],))
        if cursor.fetchone() is not None:
            print("  %-8s -> %-8s 已存在，跳过" % (self_symbol, pair_symbol))
            continue
        print("  %-8s (id=%-3s) -> %-8s (id=%s)"
              % (self_symbol, self_row["id"], pair_symbol, pair_row["id"]))
        cursor.execute(
            "INSERT INTO fundpair (id, stock_id) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE stock_id = VALUES(stock_id)",
            (self_row["id"], pair_row["id"]),
        )
    source.connection.commit()

    print("")
    print("=== 写入后的 fundpair ===")
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


def _plan(cursor) -> None:
    """预演：只报告要改什么，不写库。"""
    print("=== ① stock 表要清掉的遗留符号 ===")
    for symbol, reason in REMOVE_STOCKS:
        cursor.execute("SELECT id FROM stock WHERE symbol = %s", (symbol,))
        row = cursor.fetchone()
        if row is None:
            print("  %-12s 不在库里" % symbol)
            continue
        cursor.execute("SELECT 1 FROM fundpair WHERE id = %s OR stock_id = %s LIMIT 1",
                       (row["id"], row["id"]))
        if cursor.fetchone() is not None:
            print("  %-12s 被引用，保留" % symbol)
        else:
            print("  %-12s 待删除（%s）" % (symbol, reason))

    print("")
    print("=== ② stock 表要补的符号 ===")
    for symbol, name in STOCKS:
        cursor.execute("SELECT id FROM stock WHERE symbol = %s", (symbol,))
        if cursor.fetchone() is not None:
            print("  %-12s 已存在，跳过" % symbol)
        else:
            print("  %-12s 新增  %s" % (symbol, name))

    print("")
    print("=== ③ fundpair 要补的配对 ===")
    # 预演阶段 TQQQ 可能还没进 stock 表，所以"符号不全"要当成"待补"而不是"跳过"
    known = {symbol for symbol, _ in STOCKS}
    for self_symbol, pair_symbol in PAIRS:
        cursor.execute("SELECT 1 FROM fundpair f JOIN stock s ON s.id = f.id "
                       "WHERE s.symbol = %s LIMIT 1", (self_symbol,))
        if cursor.fetchone() is not None:
            print("  %-8s -> %-8s 已存在，跳过" % (self_symbol, pair_symbol))
            continue
        missing = None
        for symbol in (self_symbol, pair_symbol):
            cursor.execute("SELECT id FROM stock WHERE symbol = %s", (symbol,))
            if cursor.fetchone() is None and symbol not in known:
                missing = symbol
                break
        if missing:
            print("  %-8s -> %-8s **缺符号 %s**" % (self_symbol, pair_symbol, missing))
        else:
            print("  %-8s -> %-8s 待补" % (self_symbol, pair_symbol))


if __name__ == "__main__":
    sys.exit(main())
