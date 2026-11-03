# -*- coding: utf-8 -*-
"""接通港股 / 日经的「实时 EST」：补 stock 行、两张配对表、清脏校准值。

## 为什么需要这个补丁

实时 EST 的链路是（PHP ``_QdiiReference::EstRealtimeNetValue()``）::

    实时 EST = 官方 EST 的算法，但把中间层取值换成 期货价 / 校准值

它要三样东西同时在位，缺一样就静默回落到"中间层快照"（列还在，值却不是期货推的）：

1. ``stock`` 表里有期货符号（``hf_HSI``）；
2. 配对表里有「指数 → 期货」（``^HSI → hf_HSI``）：
   * ``fundpair``  —— PHP ``FundPairReference`` 走它，页面「参考数据」表靠它出期货行；
   * ``futurespair`` —— 本项目 ``futurescalib`` 走它，``quoteupdater`` 也靠它反推
     "该抓哪些期货"（``updater/quoteupdater.py`` 里那条 join）；
3. ``calibrationhistory`` 里指数那一行有 ``期货 / 指数`` 的校准值。

2026-11-03 之前的实际状况：

* ``hf_HSI`` 在 ``stock`` 里**没有行**（``hf_NK`` 有，id=16）；
* 两张配对表都只有美股两对，日经的 ``hf_NK`` 有行却从未被配对 → 从未被抓取；
* ``^HSI`` / ``znb_NKY`` 在 ``calibrationhistory`` 里各有一条 **2026-10-14 的
  演示遗留值**（``^HSI`` 120.3、``znb_NKY`` 302.5），量级完全不对
  （正确值应在 1.0 附近：期货与指数点位几乎相同）。留着它会让
  ``期货 / 校准值`` 算出 195 这种离谱点位，被量级保险挡掉，实时 EST 依旧为空。

## 恒生科技 / 国企为什么不在名单里

``^HSTECH`` / ``^HSCE`` 没有对应期货可取：新浪 ``hf_HSTECH`` / ``hf_HSCEI`` /
``hf_HTI`` / ``hf_HHI`` 实测全部返回空串。生产端 ``sh513010cn.php``（恒生科技）
与 ``sh510900cn.php``（国企）也确实没有「实时EST」列 —— **这是数据源缺，
不是配置漏**，所以不补。

用法::

    python -B dbtools/patch_hk_jp_futures.py            # 预演（默认）
    python -B dbtools/patch_hk_jp_futures.py --apply    # 真写
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
    ("hf_HSI", "恒生指数期货"),
]

#: 「指数 → 期货」配对，``fundpair`` 与 ``futurespair`` 两张表都要写。
PAIRS = [
    ("^HSI", "hf_HSI"),
    ("znb_NKY", "hf_NK"),
]

#: 需要体检的校准值所属指数。**注意 ``calibrationhistory.id`` 是自增主键，
#: 外键列叫 ``stock_id``** —— 按 ``id`` 关联会把基金行的校准值（120.3、302.5
#: 这种 `cal_f`）误当成指数的 `期货/指数` 比值，进而误判"有脏数据"。
STALE_CAL_SYMBOLS = ["^HSI", "znb_NKY"]

#: 期货 / 指数 的正常量级：两个几乎同点位的报价，比值必然在 1.0 附近。
#: 偏出这个区间说明是别的口径（``cal_f`` 之类）误入，必须删掉。
CAL_SANE_LOW = 0.5
CAL_SANE_HIGH = 2.0


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


def _stale_calibration(cursor) -> list:
    """找出量级不对的指数校准值（``期货 / 指数`` 应当在 1.0 附近）。"""
    planned = []
    for symbol in STALE_CAL_SYMBOLS:
        cursor.execute("SELECT id FROM stock WHERE symbol = %s", (symbol,))
        row = cursor.fetchone()
        if row is None:
            continue
        cursor.execute(
            "SELECT id, `date`, close FROM calibrationhistory "
            "WHERE stock_id = %s ORDER BY `date` DESC",
            (row["id"],),
        )
        records = cursor.fetchall()
        if not records:
            print("  %-8s 无校准行（等期货与指数对齐后写入）" % symbol)
            continue
        for record in records:
            value = float(record["close"])
            if CAL_SANE_LOW <= value <= CAL_SANE_HIGH:
                print("  %-8s %s = %s（量级正常，保留）"
                      % (symbol, record["date"], record["close"]))
                continue
            print("  %-8s %s = %s（量级不对，待删）"
                  % (symbol, record["date"], record["close"]))
            planned.append((record["id"],))
    return planned


def main() -> int:
    apply = "--apply" in sys.argv
    source = MySqlFundDataSource.from_env()
    cursor = source.connection.cursor()

    print("=== ① stock 表 ===")
    new_stock = _ensure_stock(cursor)

    # 配对要查 stock.id，所以补齐 stock 行必须**先落库**才能配对；
    # 预演时把待建的符号记为 pending，让输出能说清"这两行最终会被补上"。
    if apply and new_stock:
        for symbol, name in new_stock:
            cursor.execute("INSERT INTO stock (symbol, name) VALUES (%s, %s)", (symbol, name))
        source.connection.commit()
        inserted_stock = len(new_stock)
        print("  已插入 %d 行 stock。" % inserted_stock)
        new_stock = []          # 下面不再重复插入
    else:
        inserted_stock = 0
    pending = {symbol for symbol, _name in new_stock}

    print("")
    print("=== ② fundpair（PHP 侧）===")
    new_fundpair = _ensure_pairs(cursor, "fundpair", pending)

    print("")
    print("=== ③ futurespair（本项目 futurescalib / quoteupdater）===")
    new_futurespair = _ensure_pairs(cursor, "futurespair", pending)

    print("")
    print("=== ④ 演示遗留校准值（会算错点位，必须清）===")
    stale = _stale_calibration(cursor)
    if not stale:
        print("  没有需要清理的行。")

    total = inserted_stock + len(new_stock) + len(new_fundpair) + len(new_futurespair) + len(stale)
    print("")
    print("合计 %d 项改动（stock %d / fundpair %d / futurespair %d / 脏校准 %d）"
          % (total, inserted_stock + len(new_stock), len(new_fundpair),
             len(new_futurespair), len(stale)))

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

    for symbol, name in new_stock:
        cursor.execute("INSERT INTO stock (symbol, name) VALUES (%s, %s)", (symbol, name))
    for table, rows in (("fundpair", new_fundpair), ("futurespair", new_futurespair)):
        for index_id, future_id in rows:
            cursor.execute(
                "INSERT INTO %s (id, stock_id) VALUES (%%s, %%s) "
                "ON DUPLICATE KEY UPDATE stock_id = VALUES(stock_id)" % table,
                (index_id, future_id),
            )
    for (record_id,) in stale:
        cursor.execute("DELETE FROM calibrationhistory WHERE id = %s", (record_id,))
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
        "SELECT a.symbol AS index_symbol, b.symbol AS future_symbol "
        "FROM fundpair f JOIN stock a ON a.id = f.id JOIN stock b ON b.id = f.stock_id "
        "WHERE a.symbol LIKE '^%%' OR a.symbol LIKE 'znb%%' ORDER BY a.symbol"
    )
    print("  fundpair（指数行）:")
    for row in cursor.fetchall():
        print("    %-10s -> %s" % (row["index_symbol"], row["future_symbol"]))
    cursor.execute(
        "SELECT COUNT(*) AS n FROM calibrationhistory WHERE stock_id IN "
        "(SELECT id FROM stock WHERE symbol = %s)",
        ("^HSI",),
    )
    print("  ^HSI 剩余校准行数: %s" % cursor.fetchone()["n"])

    cursor.close()
    source.connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
