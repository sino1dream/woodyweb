# -*- coding: utf-8 -*-
"""导出 ``fundposition`` 对照所需的全部输入给 PHP 探针。

PHP 那一侧不连库（只有 libmysqli 的那些函数没法对数组用），所以把算法真正
吃到的东西全部导出来：净值序列、汇率序列、est 标的序列、登记仓位。

**必须导出 est 的两套取数**（``EchoNetValueItem`` 里 ``$fEst = $est_ref->GetNetValue($strDate)``）：

* est 有净值记录（QQQ / KWEB 这类 ETF）→ 取 ``netvaluehistory``；
* est 没有净值记录（^GSPC / ^NDX 这类**指数**）→ PHP 的 ``GetNetValue()``
  第三支回落到行情价（``mysqlref.php:72``），Python 侧对应
  ``source.get_history_close()``，也就是 ``stockhistory`` 表。

少导一套，对照出来的仓位就是空的，而空和"算不出来"在 diff 里长得一样。

跑法::

    cd python/auto/_scratch
    D:/python/python.exe -B _fundposition_export.py
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
from core.stocksymbol import (  # noqa: E402
    in_array_lof_mix,
    in_array_qdii,
    in_array_qdii_eu,
    in_array_qdii_hk,
    in_array_qdii_jp,
)
from ref.refobj import QDII_REGIONS, QdiiReference  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_FILE = os.path.join(HERE, "_fundposition_input.json")

#: 演示库里净值序列够长的符号
SYMBOLS = ("SZ161125", "SZ161130", "SH513100", "SZ164906")
NUM = 10


def _series(cursor, table, stock_id) -> dict:
    cursor.execute(
        "SELECT date, close FROM `%s` WHERE stock_id = %%s ORDER BY date DESC LIMIT 200" % table,
        (stock_id,),
    )
    return {str(r["date"]): str(r["close"]) for r in cursor.fetchall()}


def _region(symbol: str):
    """``StockGetQdiiReference()`` 的分派顺序（``get_table`` 里 `_qdii_region` 同款）。"""
    if in_array_qdii(symbol):
        return "us"
    if in_array_qdii_hk(symbol):
        return "hk"
    if in_array_qdii_jp(symbol):
        return "jp"
    if in_array_qdii_eu(symbol):
        return "eu"
    return None


def main() -> int:
    source = MySqlFundDataSource.from_env()
    cursor = source.connection.cursor()
    out = {}
    try:
        for symbol in SYMBOLS:
            stock_id = source.get_stock_id(symbol)
            if stock_id is None:
                print("%-10s 不在 stock 表，跳过" % symbol)
                continue
            region = _region(symbol)
            if region is None:
                print("%-10s 不是 QDII 家族（mix=%s），跳过" % (symbol, in_array_lof_mix(symbol)))
                continue
            cny_symbol = QDII_REGIONS[region][0]
            est_symbol = QdiiReference._est_symbol_of(symbol, region)
            cny_id = source.get_stock_id(cny_symbol)
            est_id = source.get_stock_id(est_symbol) if est_symbol else None

            #: date DESC —— GetAll() 的排序，也就是 PHP 状态机的输入顺序
            nav_rows = [[date, close] for date, close in _series(cursor, "netvaluehistory", stock_id).items()]
            est_netvalue = _series(cursor, "netvaluehistory", est_id) if est_id else {}
            out[symbol] = {
                "num": NUM,
                "position": str(source.get_position(stock_id)),
                "nav": nav_rows,
                "cny": _series(cursor, "netvaluehistory", cny_id) if cny_id else {},
                "est_netvalue": est_netvalue,
                "est_close": _series(cursor, "stockhistory", est_id) if est_id else {},
                "est_symbol": str(est_symbol) if est_symbol else "",
                "cny_symbol": str(cny_symbol),
            }
            print("%-10s est=%-8s 净值 %2d 条  %s" % (
                symbol, est_symbol, len(nav_rows),
                "est 有净值记录" if est_netvalue else "est 回落到行情价"))
    finally:
        cursor.close()

    with open(OUT_FILE, "w", encoding="utf-8") as handle:
        json.dump(out, handle)
    print("\n已写入 %s" % OUT_FILE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
