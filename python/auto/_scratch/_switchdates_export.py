# -*- coding: utf-8 -*-
"""导出真实净值序列给 ``GetSwitchDates()`` 对照用。

``_php_switchdates_check.php`` 的输入要求是 **``[{"date":..,"close":..}, ...]``**
的字典列表（PHP 里 ``$record['date']`` 取字符串键）—— 早先版本导出成二元元组，
``json.dump`` 后变成 JSON 的"数组数组"，PHP 侧 ``$record['date']`` 取不到，
于是每条都报 ``Undefined array key``。这里改成字典，从源头堵掉这个坑。

顺序必须是 **date DESC**，与 ``DailyCloseSql::GetAll()`` 的 ``_SqlOrderByDate()``
（``php/sql/_sqlcommon.php:89``）一致 —— 顺序错了拐点数组就是反的。

跑法::

    cd python/auto/_scratch
    D:/python/python.exe -B _switchdates_export.py
"""

from __future__ import annotations

import io
import json
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

AUTO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON_DIR = os.path.dirname(AUTO_DIR)
for _path in (AUTO_DIR, PYTHON_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core.stockdata import MySqlFundDataSource  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_FILE = os.path.join(HERE, "_switchdates_nav.json")

#: 演示库里有连续净值历史的 QDII 基金（够跑出多个拐点即可）
SYMBOLS = ("SZ161125", "SZ161130", "SH513100", "SZ164906")

#: PHP 页面默认只显示这么多行（fundpositioncn.php 的 $iNum）
LIMIT = 60


def main() -> int:
    source = MySqlFundDataSource.from_env()
    out = {}
    cursor = source.connection.cursor()
    try:
        for symbol in SYMBOLS:
            stock_id = source.get_stock_id(symbol)
            if stock_id is None:
                print("%-10s 不在 stock 表，跳过" % symbol)
                continue
            # date DESC：与 DailyCloseSql::GetAll() 的 _SqlOrderByDate() 一致
            cursor.execute(
                "SELECT date, close FROM netvaluehistory WHERE stock_id = %s "
                "ORDER BY date DESC LIMIT %s",
                (stock_id, LIMIT),
            )
            rows = cursor.fetchall()
            if not rows:
                print("%-10s 无净值记录，跳过" % symbol)
                continue
            # 字典列表：PHP 侧 $record['date'] 取的是字符串键
            out[symbol] = [{"date": str(r["date"]), "close": str(r["close"])} for r in rows]
            print("%-10s %2d 条  %s ~ %s" % (
                symbol, len(out[symbol]), rows[-1]["date"], rows[0]["date"]))
    finally:
        cursor.close()

    with open(OUT_FILE, "w", encoding="utf-8") as handle:
        json.dump(out, handle)
    print("\n已写入 %s" % OUT_FILE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
