# -*- coding: utf-8 -*-
"""导出「基金锚点对照」需要的 stock_id 清单。

给 ``_php_calibration_anchor_check.php`` 用：PHP 探针要连**真实演示库**跑
``AdjustFactor()`` 真身，但它只认 ``stock_id``，符号 → id 的映射交给本脚本
（Python 侧的 ``get_stock_id`` 与 PHP 的 ``SqlGetStockId()`` 是同一张 ``stock``
表，不会错位）。

产出 ``_calibration_anchor_input.json``::

    {"SZ161125": {"fund_id": 2, "est_symbol": "^GSPC", "est_id": 7, "cny_id": 8}, ...}

``est_symbol`` 由 :func:`calc.fundcalc.qdii_est_symbol` 给（与 PHP
``QdiiGetEstSymbol()`` 同源，README_fund_migration 里逐项核过），
``cny_id`` 固定是 ``USCNY``（PHP ``$this->GetCnyRef()`` 对应的中间价）。

跑法::

    cd python/auto/_scratch
    D:/python/python.exe -B _calibration_anchor_export.py
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

from calc.fundcalc import qdii_est_symbol, qdii_est_symbol_map  # noqa: E402
from core.stockdata import MySqlFundDataSource  # noqa: E402

OUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "_calibration_anchor_input.json")

#: 汇率的符号。PHP 侧 ``$this->GetCnyRef()`` 取的也是这一个。
CNY_SYMBOL = "USCNY"


def main() -> int:
    source = MySqlFundDataSource.from_env()
    cny_id = source.get_stock_id(CNY_SYMBOL)
    if cny_id is None:
        print("库里没有 %s" % CNY_SYMBOL)
        return 2

    payload = {}
    for symbol in sorted(qdii_est_symbol_map()):
        fund_id = source.get_stock_id(symbol)
        if fund_id is None:
            continue                      #: 不在 stock 表里的，PHP 也算不了
        est_symbol = qdii_est_symbol(symbol)
        est_id = source.get_stock_id(est_symbol) if est_symbol else None
        if est_id is None:
            continue
        payload[symbol] = {
            "fund_id": int(fund_id),
            "est_symbol": est_symbol,
            "est_id": int(est_id),
            "cny_id": int(cny_id),
        }

    with open(OUT_FILE, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1, sort_keys=True)
    print("写了 %d 只基金 -> %s" % (len(payload), os.path.basename(OUT_FILE)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
