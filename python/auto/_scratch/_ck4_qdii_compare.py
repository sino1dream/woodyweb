#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""CK4 收尾：QDII 持仓口径接线的 PHP↔Python 逐例对照。

对的是 ``php/stock/qdiiref.php:110`` ``_getOfficialEstVal()`` 的**选档顺序**：

```php
if ($str = $this->_getEstNetValue($est_ref, $strDate))  return $str;   // ①净值
if (method_exists($est_ref, 'GetHoldingsDate'))  return $est_ref->_estNetValue($strDate);  // ②持仓
return $this->_getEstPrice($est_ref, $strDate);                       // ③行情价
```

Python 侧是 ``auto/calc/fundestcalc.py::_official_est_value``（本次新增②那一档）。

**只比官方 EST、不比参考 EST**：Python 的参考 EST 走的是
``FundPairReference::GetFairNetValue()``（``fundpairref.php:256``）另一套语义，
与 ``_getFairEstVal()`` 不是同一个东西。PHP harness 仍然把 fair 打出来，
只为存档，不参与比对。

**为什么要比"选中的那一档"而不只是数值**：三档恰好给出同一个数时，
只比数是看不出档位错的（净值 1.0 与行情价 1.0 分不出来）。所以这里同时比
``_estNetValue`` 被传进去的日期。

用法::

    D:/python/python.exe -B auto/_scratch/_ck4_qdii_compare.py
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "python"))
sys.path.insert(0, os.path.join(ROOT, "python", "auto"))

PHP_EXE = r"D:/xampp/php/php.exe"
PHP_HARNESS = os.path.join(HERE, "_php_qdii_holdings_check.php")

DATE = "2026-10-14"
#: 持仓那一档的返回值（与 PHP harness 的 ``_estNetValue()`` 替身一致）
HOLDINGS_VALUE = "1.0263"
PREV_CLOSE = "749.58"
NET_VALUE = "1.0"
PRICE = "600.5"


# ---------------------------------------------------------------- 假连接/数据源
class FakeCursor:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, params=()):
        self._rows = self.connection.dispatch(sql, params)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class FakeConnection:
    """只要能答 ``SELECT COUNT(*) ... FROM `holdings```。"""

    def __init__(self, holdings=None):
        self.tables = {"holdings": [dict(r) for r in (holdings or [])]}

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        pass

    def dispatch(self, sql, params):
        match = re.search(r"FROM\s+`([^`]+)`", sql)
        rows = self.tables.get(match.group(1), []) if match else []
        if "COUNT(*)" in sql.upper():
            stock_id = params[0] if params else None
            return [{"n": len([r for r in rows if str(r["stock_id"]) == str(stock_id)])}]
        return rows


class FakeSource:
    def __init__(self, connection, ids=None):
        self.connection = connection
        self._ids = ids or {"QQQ": "4", "SH513050": "6"}

    def get_stock_id(self, symbol):
        return self._ids.get(symbol)


# ---------------------------------------------------------------- Python 侧
def python_scene(est_symbol: str, holdings: list, net_value, price: str) -> dict:
    """跑一次真实的 ``_official_est_value``，只替换两条**取数**通路。

    替换的是"从哪拿数"（净值表 / 行情），**不动选档逻辑本身** ——
    把选档也替掉的话，这个对照就只是在比替身了。
    """
    from calc.fundestcalc import EstimateCalculator
    from sql.fundeststore import FundEstStore

    connection = FakeConnection(holdings=holdings)
    source = FakeSource(connection)
    calc = EstimateCalculator(source, FundEstStore(connection), now_provider="04:00")

    calls = []

    class FakeRef:
        def __init__(self, symbol, source=None):
            self.symbol = symbol

        def _est_net_value(self, date=None, realtime=False):
            calls.append(date)
            return HOLDINGS_VALUE

    import ref.holdingsref

    original = ref.holdingsref.HoldingsReference
    ref.holdingsref.HoldingsReference = FakeRef
    try:
        calc._net_value = lambda est_id, date: net_value
        calc._middle_day_quote = lambda est_symbol, date, data=None: price
        official = calc._official_est_value(est_symbol, DATE, {})
    finally:
        ref.holdingsref.HoldingsReference = original

    return {
        "official": "" if official is None else str(official),
        # 官方那一档传进去的日期；没走到持仓档就是 None
        "official_call": calls[0] if calls else None,
    }


# ---------------------------------------------------------------- 场景
def holdings_rows(count: int, stock_id="6") -> list:
    return [{"id": i + 1, "stock_id": stock_id, "holding_id": 100 + i, "ratio": 30.0}
            for i in range(count)]


SCENES = [
    # (名字, est 符号, 持仓行数, 净值, 行情价)
    ("pair_with_netvalue", "QQQ", 0, NET_VALUE, PRICE),
    ("pair_without_netvalue", "QQQ", 0, None, PRICE),
    ("holdings_with_netvalue", "SH513050", 36, NET_VALUE, PRICE),
    ("holdings_without_netvalue", "SH513050", 36, None, PRICE),
]


def main() -> int:
    proc = subprocess.run(
        [PHP_EXE, "-n", "-d", "display_errors=stderr", PHP_HARNESS],
        capture_output=True, cwd=HERE,
    )
    if proc.returncode != 0:
        print("PHP harness 失败 rc=%d" % proc.returncode)
        print(proc.stderr.decode("utf-8", "replace")[:2000])
        return 1
    php = {item["name"]: item for item in json.loads(proc.stdout.decode("utf-8"))}

    print("%-26s %-14s %-14s %-10s %-10s %s" % (
        "场景", "PHP official", "Py official", "PHP 日期", "Py 日期", "结果"))
    print("-" * 96)

    bad = 0
    for name, est_symbol, count, net_value, price in SCENES:
        item = php[name]
        #: PHP 的 calls 里混着 fair 那次（传 false），官方那次是第一个非 false 的
        php_call = None
        for call in item["calls"]:
            if call is not False and call != False:  # noqa: E712
                php_call = call
                break

        py = python_scene(est_symbol, holdings_rows(count), net_value, price)

        ok_value = str(item["official"]) == str(py["official"])
        ok_call = (php_call or None) == (py["official_call"] or None)
        if ok_value and ok_call:
            verdict = "OK"
        else:
            verdict = "MISMATCH"
            bad += 1

        print("%-26s %-14s %-14s %-10s %-10s %s" % (
            name, item["official"], py["official"],
            str(php_call), str(py["official_call"]), verdict))

    print("-" * 96)
    print("差异 %d / %d" % (bad, len(SCENES)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
