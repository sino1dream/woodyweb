"""CK4-B 的 PHP 逐例对照：``HoldingsReference`` 估值式（``holdingsref.php:287``）。

跑法::

    python auto/_scratch/_php_holdings_compare.py

左边是真 PHP（``_php_holdings_check.php`` 从 ``php/stock/holdingsref.php``
现场切出 ``_estNetValue`` 等方法体，一个字不改，见那个文件的头注释），
右边是本项目的 ``ref/holdingsref.py``。**两边用同一组输入**，逐位比。

对照的是这四组（对应生产端持仓页上的那几列）：

============  ============================================================
组             页面上的位置
============  ============================================================
``dated``      「官方EST」列（按成分股收盘价估）
``with_fx``    「参考EST」列（按成分股当前价估，含汇率调整）
``realtime``   「参考EST」列里能对上配对的那些成分股
``adjust_*``   汇率调整系数（持仓页最后那列「汇率调整」）
============  ============================================================

**为什么值得单独做一遍**：这些数算错了不报错，只是给出一个"看着合理"的
结果 —— 生产端 SH513050 的官方 EST 1.025 与参考 EST 1.006 差 1.9%，
差 10 倍才会被肉眼发现，差 2% 不会。
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
sys.path.insert(0, AUTO)

from ref.holdingsref import HoldingsReference  # noqa: E402

PHP_EXE = r"D:\xampp\php\php.exe"
PHP_SCRIPT = os.path.join(HERE, "_php_holdings_check.php")

HOLDINGS_DATE = "2026-10-13"

#: 与 PHP 脚本里 ``$g_his_sql->adjclose`` 一一对应（"id|date" -> 值）
ADJCLOSE = {
    (13, "2026-10-13"): "1500.0",
    (11, "2026-10-13"): "425.2",
    (12, "2026-10-13"): "81.96",
    (13, "2026-10-14"): "1510.0",
    (11, "2026-10-14"): "420.0",
    (12, "2026-10-14"): "82.5",
}


# ---------------------------------------------------------------------------
# 替身（与 test_holdingsref.py 同款，这里要能额外给 get_val）
# ---------------------------------------------------------------------------
import re  # noqa: E402


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self._rows: list = []

    def execute(self, sql, params=()):
        self._rows = self.connection.dispatch(sql, params)
        return len(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class FakeConnection:
    """只认 ``stockhistory``：估值式要的旧价/指定日价都从这儿来。"""

    def __init__(self, adjclose=None):
        self.adjclose = dict(adjclose or ADJCLOSE)

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        pass

    def dispatch(self, sql, params):
        table = re.search(r"FROM\s+`([^`]+)`", sql)
        if not table or table.group(1) != "stockhistory":
            return []
        rows = []
        for (stock_id, date), value in self.adjclose.items():
            rows.append({"id": 0, "stock_id": stock_id, "date": date,
                         "close": value, "adjclose": value, "volume": "0"})
        rows = [r for r in rows if str(r["stock_id"]) == str(params[0])]
        if "`date` = %s" in sql:
            rows = [r for r in rows if str(r["date"]) == str(params[1])]
        elif "`date` < %s" in sql:
            rows = [r for r in rows if str(r["date"]) < str(params[1])]
            rows.sort(key=lambda r: str(r["date"]), reverse=True)
        return rows[:1] if "LIMIT 1" in sql else rows


class FakeSource:
    def __init__(self, connection):
        self.connection = connection

    def get_stock_id(self, symbol):
        return {"SH513050": 1, "SH600519": 13, "00700": 11, "PDD": 12}.get(symbol)

    def get_stock_symbol(self, stock_id):
        return {1: "SH513050", 13: "SH600519", 11: "00700", 12: "PDD"}.get(int(stock_id))

    def get_stock_name(self, symbol):
        return None

    def get_netvalue_close(self, stock_id, date):
        return None

    def get_position(self, stock_id):
        return 1.0

    def get_history_close(self, stock_id, date):
        return None

    def get_history_adjclose(self, stock_id, date):
        return None

    def count_net_value(self, stock_id):
        return 2

    def get_calibration_now(self, stock_id):
        return None

    def get_last_calibration(self, stock_id):
        return None


class FakeRef:
    """成分股引用替身；``kind`` 决定汇率那一路怎么走。"""

    def __init__(self, symbol, stock_id, price, kind, est_from_pair=None):
        self._symbol = symbol
        self._stock_id = stock_id
        self._price = price
        self._kind = kind
        self._est = est_from_pair

    def get_symbol(self):
        return self._symbol

    def get_stock_id(self):
        return self._stock_id

    def get_price(self):
        return self._price

    def get_date(self):
        return "2026-10-15"

    def est_from_pair(self, *_a, **_k):
        return self._est

    def is_symbol_a(self):
        return self._kind == "a"

    def is_symbol_h(self):
        return self._kind == "hk"

    def is_etf_a(self):
        return False

    def is_fund_a(self):
        return False


class FakeCny:
    def __init__(self, price, closes=None, date="2026-10-15"):
        self._price = price
        self._closes = closes or {}
        self._date = date

    def get_price(self):
        return self._price

    def get_date(self):
        return self._date

    def get_close(self, date):
        return self._closes.get(str(date))

    def get_val(self, date):
        return self._closes.get(str(date), self._price)


def build(with_fx: bool, realtime: bool = False):
    """装配一只与 PHP 侧**同输入**的持仓引用。"""
    source = FakeSource(FakeConnection())
    ref = HoldingsReference("SH513050", source=source)
    ref.str_symbol = "SH513050"   # is_symbol_a / is_etf_a 靠它判定
    ref.net_value = "1.0115"
    ref.holdings_date = HOLDINGS_DATE
    ref.ratio = 1.0
    #: 顺序必须与 PHP 的 ``array($a, $h, $u)`` 一致 —— 浮点累加对顺序敏感
    ref.holdings_refs = [
        FakeRef("SH600519", 13, "1510", "a"),
        FakeRef("00700", 11, "418", "hk", est_from_pair="500" if realtime else None),
        FakeRef("PDD", 12, "81.45", "us"),
    ]
    ref.holdings_ratio = {13: 5.0, 11: 30.68, 12: 7.31}
    ref.ratio_cn, ref.ratio_hk, ref.ratio_us = 5.0, 30.68, 7.31
    ref._holdings_date_history = {}
    if with_fx:
        ref.uscny_ref = FakeCny("7.1", {"2026-10-13": "7.12", "2026-10-14": "7.11"})
        ref.usdcny_ref = FakeCny("7.09")
        ref.hkcny_ref = FakeCny("0.9", {"2026-10-13": "0.91", "2026-10-14": "0.905"})
        ref.hkdcny_ref = FakeCny("0.89")
    return ref


def python_side() -> dict:
    r1 = build(False)
    r2 = build(True)
    r4 = build(True, True)
    return {
        "plain": r1._est_net_value(),
        "adjust_usd_plain": r1.get_adjust_usd(),
        "adjust_hkd_plain": r1.get_adjust_hkd(),
        "with_fx": r2._est_net_value(),
        "adjust_usd_etf": r2.get_adjust_usd(),
        "adjust_hkd_etf": r2.get_adjust_hkd(),
        "adjust_usd_dated": r2.get_adjust_usd("2026-10-14"),
        "adjust_hkd_dated": r2.get_adjust_hkd("2026-10-14"),
        "dated": r2._est_net_value("2026-10-14"),
        "realtime": r4._est_net_value(None, True),
        "ratio_display": r2.get_holdings_ratio_display(),
        "proportion": r2.get_proportion_array("2026-10-14", "2026-10-13"),
    }


def php_side() -> dict:
    result = subprocess.run(
        [PHP_EXE, "-n", "-d", "display_errors=stderr", PHP_SCRIPT],
        capture_output=True,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr.decode("utf-8", "replace"))
        raise SystemExit("PHP 对照脚本失败：%d" % result.returncode)
    return json.loads(result.stdout.decode("utf-8").strip().splitlines()[-1])


def main() -> int:
    out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    php = php_side()
    local = python_side()

    print("项                     PHP                      Python              差", file=out)
    print("-" * 78, file=out)
    bad = 0
    for key in php:
        a, b = php[key], local[key]
        if isinstance(a, list):
            same = len(a) == len(b) and all(
                abs(x - y) <= 1e-12 for x, y in zip(a, b))
            diff = "" if same else "不一致"
            shown_a = ", ".join("%.10f" % x for x in a)[:34]
            shown_b = ", ".join("%.10f" % x for x in b)[:34]
        elif isinstance(a, str):
            same = a == b
            diff = "" if same else "不一致"
            shown_a, shown_b = a, b
        else:
            same = abs(a - b) <= 1e-12
            diff = "" if same else "差 %.3g" % (b - a)
            shown_a, shown_b = "%.12f" % a, "%.12f" % b
        if not same:
            bad += 1
        print("%-20s %-24s %-22s %s" % (key, shown_a, shown_b, diff), file=out)

    print("-" * 78, file=out)
    print("共 %d 项，不一致 %d 项" % (len(php), bad), file=out)
    out.flush()
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
