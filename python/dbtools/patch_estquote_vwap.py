# -*- coding: utf-8 -*-
"""给 ``estquote`` 补 ``vwap`` 列（国内商品期货的当日均价）。

## 为什么需要这一列

「A股商品」那 6 只基金（``SH518800`` / ``SH518880`` / ``SZ159934`` /
``SZ159937`` / ``SZ159985`` / ``SZ161226``）跟踪的是国内商品期货
``nf_AU0`` / ``nf_AG0`` / ``nf_M0``。**商品期货基金的净值按当日均价算**，
不是按收盘价 —— PHP 在 ``fundpairref.php:242`` 写得很明确::

    if ($this->pair_ref->IsSinaFutureExceptGoldCN())
        $fEst = floatval($this->pair_ref->strVWAP);

``IsSinaFutureExceptGoldCN()`` 是"``nf_`` 前缀且不是 ``nf_AU0``"，
所以豆粕（``nf_M0``）与白银（``nf_AG0``）走均价，黄金（``nf_AU0``）走现价。
生产端实测佐证（2026-11-04 11:30）::

    SZ159985  官方 EST 2.054   ← 均价 3119.003 / cal 1518.7566
              参考 EST 2.049   ← 现价 3112     / cal 1518.7566
    SZ161226  官方 EST 1.342   ← 均价 11903.35 / cal 8868.3006
              参考 EST 1.328   ← 现价 11779    / cal 8868.3006

若官方 EST 也用现价，两只都会偏高（实测 2.061 / 1.368），
跟生产端差 0.3% 与 1.9% —— 差值就是"均价 vs 现价"。

## 为什么要单独一列

新浪把均价放在行情行的第 **28** 个字段（``_on_sina_future_cn`` 存进
``str_vwap``），而 ``estquote`` 原来只有 ``close`` / ``prev_close``。
不落库的话，页面算估值时只能重新抓一遍行情 —— 那违反
"web 只读快照、daemon 只写快照" 的分层（见 ``auto/refquote.py``）。

## 幂等

先查 ``information_schema``，列已存在就跳过。``--apply`` 才真改，
默认只预演（与 dbtools 里其它补丁脚本同款约定）。

用法::

    python -B dbtools/patch_estquote_vwap.py            # 预演
    python -B dbtools/patch_estquote_vwap.py --apply    # 真改
"""

from __future__ import annotations

import io
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto"))

from core.stockdata import MySqlFundDataSource  # noqa: E402

TABLE = "estquote"
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

    cursor.execute("ALTER TABLE `%s` ADD COLUMN `%s` %s AFTER `fetched_at`"
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
