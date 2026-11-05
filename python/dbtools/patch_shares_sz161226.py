# -*- coding: utf-8 -*-
"""补 ``SZ161226`` 的「基金场内份额」，让详情页那一段出现。

## 为什么整段不出现

``php/ui/fundshareparagraph.php:27``::

    if (($iTotal = $shares_sql->Count($strStockId)) == 0)	return;

取不到记录就**不 echo 这张表**（本地 ``detail_sections.build_shares`` 同款守卫：
``rows`` 为空就返回 ``None``）。本地 ``shareshistory`` 里原先只有
``SZ162411`` 那 4 行渲染样本，``SZ161226`` 一行都没有 —— 所以
``sz161226cn.php`` 上「基金场内份额」整块消失。

## 数据从哪来

生产端 ``https://palmmicro.com/woody/res/sz161226cn.php`` 的这一段是**公开可见**的
（不需要登录），2026-11-05 10:52 抓取，原样照抄::

    日期        份额(万)     场内新增(万)  数量        换手     新增换手
    2026-11-05  365456.82   -0.26        117949348   3.23%
    2026-11-04  365457.08   0.27         134981971   3.69%    4999332%
    2026-11-03  365456.81   0.37         78783014    2.16%    2129271%
    2026-11-02  365456.44   -2.6         91624192    2.51%
    2026-10-30  365459.04   0.01         83828415    2.29%    83828415%
    2026-10-29  365459.03   0.94         49074733    1.34%    522072%
    2026-10-28  365458.09   0.21         46648326    1.28%    2221349%
    2026-10-27  365457.88   0.47         54827514    1.5%     1166543%
    2026-10-26  365457.41   -0.27        60203832    1.65%
    2026-10-23  365457.68   0.25         98781822    2.7%     3951273%

「数量」与「换手」两列**不写进** ``shareshistory`` —— 它们来自
``stockhistory.volume``（PHP 里是 ``$his_sql->GetVolume()``），本地这一列
在 10-23 ~ 11-03 已经齐全且与生产端**逐位相同**（78783014 / 91624192 / …），
所以照抄份额即可复原整张表。

## 两处刻意多写的数据

1. **``2026-10-22`` = 365457.43 万份** —— 这行不会显示（表格只取最近 10 行），
   但没有它，``2026-10-23`` 那行的「场内新增」就是空的（``get_close_prev``
   找不到上一行）。值由生产端 ``365457.68 − 0.25`` 反推。
2. **``stockhistory`` 的 ``2026-11-04``** —— 本地日线归档只到 11-03，
   少了这一天，份额表里 11-04 的「数量」会显示 0、换手 0.00%（看着像没成交）。
   收盘价 1.507 取自生产端「基金溢价记录」同一行，成交量 134981971 取自
   本表同一行。**``2026-11-05`` 不写**：那是当天，收盘价与成交量都还在变，
   等日常归档（``quoteupdatercli --archive-snapshot`` / 日线 seed）落库。

用法::

    python -B dbtools/patch_shares_sz161226.py            # 预演（默认）
    python -B dbtools/patch_shares_sz161226.py --apply    # 真写
"""

from __future__ import annotations

import io
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto"))

from core.stockdata import MySqlFundDataSource  # noqa: E402

SYMBOL = "SZ161226"

#: ``(日期, 份额万份)``，照抄生产端「基金场内份额」表（来源见模块 docstring）。
#: 顺序无所谓 —— 落库后一律按 ``date`` 倒序取。
SHARES = [
    ("2026-10-22", 365457.43),      # 只为首行差分服务，不显示（见 docstring 第 1 条）
    ("2026-10-23", 365457.68),
    ("2026-10-26", 365457.41),
    ("2026-10-27", 365457.88),
    ("2026-10-28", 365458.09),
    ("2026-10-29", 365459.03),
    ("2026-10-30", 365459.04),
    ("2026-11-02", 365456.44),
    ("2026-11-03", 365456.81),
    ("2026-11-04", 365457.08),
    ("2026-11-05", 365456.82),
]

#: 本地 ``stockhistory`` 缺的那一天：``(日期, 收盘价, 成交量, 复权价)``。
HISTORY = [
    ("2026-11-04", 1.507, 134981971, 1.507),
]

#: 份额的合理区间（万份）。用来抓"少写一位小数点"——365456 写成 36545.6
#: 页面照样能出，只是换手率差 10 倍。
SHARE_SANE_LOW = 300000.0
SHARE_SANE_HIGH = 450000.0


def _stock_id(cursor, symbol: str):
    cursor.execute("SELECT id FROM stock WHERE symbol = %s", (symbol,))
    row = cursor.fetchone()
    return None if row is None else row["id"]


def _plan_shares(cursor, stock_id) -> list:
    planned = []
    for date, share in SHARES:
        if not (SHARE_SANE_LOW <= share <= SHARE_SANE_HIGH):
            print("  %s 份额 %s 万份不在 [%s, %s]，拒绝写入"
                  % (date, share, SHARE_SANE_LOW, SHARE_SANE_HIGH))
            continue
        cursor.execute(
            "SELECT close FROM shareshistory WHERE stock_id = %s AND `date` = %s",
            (stock_id, date),
        )
        existing = cursor.fetchone()
        if existing is not None:
            if abs(float(existing["close"]) - share) < 1e-6:
                print("  %s %12.2f 已存在，跳过" % (date, share))
            else:
                print("  %s %12.2f 已存在但值 = %s，将改正" % (date, share, existing["close"]))
                planned.append((date, share))
            continue
        print("  %s %12.2f 待插入" % (date, share))
        planned.append((date, share))
    return planned


def _plan_history(cursor, stock_id) -> list:
    planned = []
    for date, close, volume, adjclose in HISTORY:
        cursor.execute(
            "SELECT close, volume, adjclose FROM stockhistory "
            "WHERE stock_id = %s AND `date` = %s",
            (stock_id, date),
        )
        existing = cursor.fetchone()
        if existing is not None:
            print("  %s 已存在（close=%s volume=%s），跳过"
                  % (date, existing["close"], existing["volume"]))
            continue
        print("  %s 待插入（close=%s volume=%s）" % (date, close, volume))
        planned.append((date, close, volume, adjclose))
    return planned


def main() -> int:
    apply = "--apply" in sys.argv
    source = MySqlFundDataSource.from_env()
    cursor = source.connection.cursor()

    stock_id = _stock_id(cursor, SYMBOL)
    if stock_id is None:
        print("stock 表里没有 %s —— 先跑 dbtools/patch_china_future.py。" % SYMBOL)
        cursor.close()
        source.connection.close()
        return 1
    print("%s = stock.id %s" % (SYMBOL, stock_id))

    print("")
    print("=== ① shareshistory（份额，万份）===")
    new_shares = _plan_shares(cursor, stock_id)

    print("")
    print("=== ② stockhistory（补齐缺的一天，供「数量」列用）===")
    new_history = _plan_history(cursor, stock_id)

    total = len(new_shares) + len(new_history)
    print("")
    print("合计 %d 项改动（份额 %d / 日线 %d）" % (total, len(new_shares), len(new_history)))

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

    for date, share in new_shares:
        cursor.execute(
            "INSERT INTO shareshistory (stock_id, `date`, close) VALUES (%s, %s, %s) "
            "ON DUPLICATE KEY UPDATE close = VALUES(close)",
            (stock_id, date, share),
        )
    for date, close, volume, adjclose in new_history:
        cursor.execute(
            "INSERT INTO stockhistory (stock_id, `date`, close, volume, adjclose) "
            "VALUES (%s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE close = VALUES(close)",
            (stock_id, date, close, volume, adjclose),
        )
    source.connection.commit()

    print("")
    print("=== 写入后核对（最近 12 条份额）===")
    cursor.execute(
        "SELECT `date`, close FROM shareshistory WHERE stock_id = %s "
        "ORDER BY `date` DESC LIMIT 12",
        (stock_id,),
    )
    for row in cursor.fetchall():
        print("  %s %12.2f" % (row["date"], row["close"]))

    cursor.close()
    source.connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
