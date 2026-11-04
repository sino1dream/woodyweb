# -*- coding: utf-8 -*-
"""给 ``realtimequote`` 补 ``vwap`` 列（行情自带的当日均价）。

## 为什么又加一列（``estquote`` 不是已经有了吗）

``estquote.vwap`` 与 ``realtimequote.vwap`` 是**同一份行情**的两个落点，
消费方不同：

======================  ==========================================================
表                       谁读
======================  ==========================================================
``estquote.vwap``        ``fundestcalc`` 算官方 EST 的**估算器**（`fundestcli` 那一路）
``realtimequote.vwap``   ``FundPairReference.get_official_net_value()`` 读的
                         ``pair_ref->strVWAP``（页面那一路）
======================  ==========================================================

PHP 侧只有**一个** ``MyStockReference`` 对象：``strVWAP`` 是它身上的字段
（``stockref.php`` 里 ``_onSinaFutureCN()`` 填第 28 个字段），页面与命令行
共用。本项目把快照拆成了两张表，于是均价必须在**两张表**里都有，
否则总有一条路拿不到值：

* 只有 ``estquote`` 有 → :meth:`FundPairReference.get_official_net_value` 里
  ``getattr(pair_ref, "str_vwap", None)`` 恒为 ``None``，官方 EST 退回
  **结算价**（``GetNetValue``）。实测豆粕 2.0846 vs 生产端 2.054、白银
  1.3546 vs 1.342 —— 差的就是"结算价 / 均价"。
* 只有 ``realtimequote`` 有 → 估算器那条路要额外查一次别的表。

## 幂等

先查 ``information_schema``，列已存在就跳过。``--apply`` 才真改，
默认只预演（与 dbtools 里其它补丁脚本同款约定）。

用法::

    python -B dbtools/patch_realtimequote_vwap.py            # 预演
    python -B dbtools/patch_realtimequote_vwap.py --apply    # 真改
"""

from __future__ import annotations

import io
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto"))

from core.stockdata import MySqlFundDataSource  # noqa: E402

TABLE = "realtimequote"
COLUMN = "vwap"
DEFINITION = "varchar(32) COLLATE utf8_unicode_ci NOT NULL DEFAULT ''"


def _column_exists(cursor) -> bool:
    cursor.execute(
        "SELECT COUNT(*) AS n FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (TABLE, COLUMN),
    )
    row = cursor.fetchone()
    return bool(row and int(row["n"]))


def main() -> int:
    apply = "--apply" in sys.argv
    source = MySqlFundDataSource.from_env()
    cursor = source.connection.cursor()

    if _column_exists(cursor):
        print("  %s.%s 已存在，跳过" % (TABLE, COLUMN))
        print("无需改动。")
        cursor.close()
        source.connection.close()
        return 0

    print("  %s 待加列：%s %s" % (TABLE, COLUMN, DEFINITION))
    if not apply:
        print("这是预演。真正执行请加 --apply。")
        cursor.close()
        source.connection.close()
        return 0

    cursor.execute("ALTER TABLE `%s` ADD COLUMN `%s` %s AFTER `orderbook`"
                   % (TABLE, COLUMN, DEFINITION))
    source.connection.commit()
    print("  已加列。")

    cursor.execute("SELECT COUNT(*) AS n FROM `%s`" % TABLE)
    print("  %s 现有 %d 行（vwap 全为空串，等 daemon 抓一轮才有值）"
          % (TABLE, cursor.fetchone()["n"]))
    cursor.close()
    source.connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
