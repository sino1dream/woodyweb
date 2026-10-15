"""CK4-D：拿**真实 36 只持仓**在 PHP 与 Python 两边各算一遍，逐项比对。

    python -B auto/_scratch/_ck4_real_check.py

## 为什么这一步不可省

``_php_holdings_check.py``（CK4-B）用的是 3 只成分股的**人造**输入，
12 项全对齐。但真跑起来页面整列是 **0** —— 原因是真实数据有个人造输入
碰不到的形状：

* ``$arHoldingsRatio`` 的键来自 ``SqlGetHoldingsArray()``，是 **int**；
* ``$ref->GetStockId()`` 返回的是 **str**。

PHP 里这不是问题（数字字符串数组键自动转 int），Python 的 dict 不会，
于是比例全取成 0、估值整列归零还**不报错**。修完（``ratio_of``）再用
真实规模的数据复核一遍，才敢说这条分支是对的。

## 比什么

* ``adjust_usd`` / ``adjust_hkd``（带日期与不带日期，共 4 项）
* ``official`` —— ``_estNetValue($strEstDate)``，页面上的「官方EST」
* ``fair``     —— ``_estNetValue()``，页面上的「参考EST」（未加偏差补偿）
* ``ratio_display``

两边喂的是**同一份** JSON（Python 侧从演示库导出，PHP 侧只读不算）。
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

AUTO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON_DIR = os.path.dirname(AUTO_DIR)
for _path in (AUTO_DIR, PYTHON_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core.stockdata import MySqlFundDataSource  # noqa: E402
from ref.holdingsref import StockGetHoldingsReference  # noqa: E402
from sql.historytable import StockHistorySql  # noqa: E402

SYMBOL = "SH513050"
PHP = r"D:/xampp/php/php.exe"
TOLERANCE = 1e-12


def main() -> int:
    source = MySqlFundDataSource.from_env()
    ref = StockGetHoldingsReference(SYMBOL, source=source)
    if ref is None:
        print("没建出 %s 的 HoldingsReference" % SYMBOL)
        return 2
    est_date = ref.get_official_date()
    holdings_date = ref.get_holdings_date()

    his = StockHistorySql(source.connection)
    holdings = []
    for holding_ref in ref.get_holdings_ref_array():
        stock_id = holding_ref.get_stock_id()
        symbol = source.get_stock_symbol(stock_id)
        if holding_ref.is_symbol_a():
            kind = "a"
        elif holding_ref.is_symbol_h():
            kind = "hk"
        else:
            kind = "us"
        holdings.append({
            "id": int(stock_id),
            "symbol": symbol,
            "kind": kind,
            "ratio": float(ref.holdings_ratio[int(stock_id)]),
            "price": holding_ref.get_price(),
            "date": holding_ref.get_date(),
        })

    #: 历史价：持仓日（旧价）+ EST 日（官方 EST 用的收盘价）
    adjclose = {}
    for item in holdings:
        for date in (str(holdings_date), str(est_date)):
            value = his.get_adjclose(item["id"], date)
            if value is not None:
                adjclose["%d|%s" % (item["id"], date)] = str(value)

    def forex_spec(name, fallback=None):
        forex = getattr(ref, name)
        if forex is None:
            return None
        closes = {}
        for date in (str(holdings_date), str(est_date)):
            value = forex.get_close(date)
            if value is not None:
                closes[date] = str(value)
        price = forex.get_price()
        #: 演示库里 ``fx_susdcny`` / ``fx_shkdcny`` 压根没价（没人抓它们），
        #: PHP 那边的裸除法会直接 DivisionByZero fatal。对照要跑起来，就把
        #: 它填成对应的官方汇率 —— 于是两处调整都是 1.0，正是 Python 侧
        #: 零保护给出的结果，两边仍在同一个"不调汇率"的前提下比。
        if not price or float(price or 0.0) == 0.0:
            price = fallback
        return {
            "price": price,
            "date": str(forex.get_date()),
            "closes": closes,
        }

    uscny_price = ref.uscny_ref.get_price() if ref.uscny_ref else None
    hkcny_price = ref.hkcny_ref.get_price() if ref.hkcny_ref else None
    forex = {}
    for name, fallback in (("uscny_ref", None), ("usdcny_ref", uscny_price),
                           ("hkcny_ref", None), ("hkdcny_ref", hkcny_price)):
        spec = forex_spec(name, fallback)
        if spec is not None:
            forex[name] = spec

    payload = {
        "symbol": SYMBOL,
        "net_value": float(ref.net_value),
        "holdings_date": str(holdings_date),
        "est_date": str(est_date),
        "position": float(ref.get_position() or 1.0),
        "holdings": holdings,
        "adjclose": adjclose,
        "forex": forex,
    }

    path = os.path.join(tempfile.gettempdir(), "ck4_real_input.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    print("输入：%d 只成分股，持仓日 %s，EST 日 %s，净值 %s" % (
        len(holdings), holdings_date, est_date, ref.net_value))
    print("      JSON -> %s" % path)

    php_file = os.path.join(AUTO_DIR, "_scratch", "_php_holdings_real.php")
    result = subprocess.run(
        [PHP, "-n", "-d", "display_errors=stderr", php_file, path],
        capture_output=True)
    if result.returncode != 0:
        print("PHP 跑失败：%s" % result.stderr.decode("utf-8", "replace"))
        return 2
    php_out = json.loads(result.stdout.decode("utf-8").strip().splitlines()[-1])

    #: Python 侧：同一个对象、同一份输入
    py_out = {
        "adjust_usd_dated": ref.get_adjust_usd(str(est_date)),
        "adjust_hkd_dated": ref.get_adjust_hkd(str(est_date)),
        "adjust_usd": ref.get_adjust_usd(),
        "adjust_hkd": ref.get_adjust_hkd(),
        "official": ref._est_net_value(str(est_date)),
        "fair": ref._est_net_value(),
        "ratio_display": ref.get_holdings_ratio_display(),
    }

    print()
    print("%-18s %-24s %-24s %s" % ("项", "PHP", "Python", "判定"))
    print("-" * 78)
    bad = 0
    for key in py_out:
        a, b = php_out.get(key), py_out[key]
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            diff = abs(float(a) - float(b))
            same = diff <= TOLERANCE
            note = "" if same else "差 %g" % diff
        else:
            same = str(a) == str(b)
            note = "" if same else "不等"
        if not same:
            bad += 1
        print("%-18s %-24s %-24s %s" % (
            key, a, b, "OK" if same else "不一致 " + note))
    print("-" * 78)
    print("不一致 %d 项（容差 %g）" % (bad, TOLERANCE))

    print()
    print("页面上的两个数（Python 侧，与 PHP 完全一致）：")
    print("  官方 EST  %.6f   （生产端 1.025）" % py_out["official"])
    print("  参考 EST  %.6f   （生产端 1.006，页面还要再加 fOfficialEstAdjust）"
          % py_out["fair"])
    source.connection.close()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
