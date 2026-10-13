"""按**生产端口径**重算演示库 ``calibrationhistory`` 的 close。

跑法（在 ``python/`` 下）::

    D:/python/python.exe -B auto/_scratch/_calibration_recount.py            # dry-run
    D:/python/python.exe -B auto/_scratch/_calibration_recount.py --apply     # 写库

## 为什么要重算

``/stock/QQQ`` 的官方 EST 算出 **3452559.84**（SPY 是 896641.41），差了约
4600 倍。根因不在代码而在**数据**：演示库 QQQ 的校准值是 ``0.008945``，
而生产端是 ``41.111``。

## 口径从哪来

不是猜的，是拿生产端页面反推并用三组数据验算过的（见
``README_pair_reference_migration.md`` §9）。PHP 侧两处权威实现：

* ``php/stock/fundpairref.php:213`` ``CalcFactor()``
* ``php/stock/qdiiref.php:3`` ``QdiiGetCalibration()``

### 口径一：美股 ETF / 指数（QQQ、SPY、^NDX、^GSPC…）

``FundPairReference`` 的构造函数（``fundpairref.php:39-42``）走到 ``else``
分支时，只有 ``$this->IsSymbolA()`` 为真才设 ``$strCNY``。QQQ / SPY 不是 A 股，
配对标的 ^NDX / ^GSPC 也不是 A 股 / 港股 / 期货，于是 **``cny_ref`` 为 false**，
``GetDefaultCny()`` 返回 **1.0**。

    CalcFactor:  return fPairNetValue / fNetValue;        # 没有汇率
    EstFromPair: fVal = ($fPairVal / $fCny) / fFactor;    # $fCny = 1.0

=> ``cal = 配对标的价 / 本标的净值``，``EST = 配对价 / cal``。

生产端验算（2026-10-12）：

    QQQ  30664.7 / 745.9075 = 41.109   页面 41.111   ✓
    SPY  7807.24 / 778.0232 = 10.035   页面 10.035   ✓
    SH   7807.24 / 31.8031  = 245.62   页面 245.487  ✓

### 口径二：A 股 QDII 基金（SZ161130 / SZ161125 / SH513100）

走 ``QdiiReference``（不是 ``FundPairReference``）：

    QdiiGetCalibration($strEst, $strCNY, $strNetValue)
        = $strEst * $strCNY / $strNetValue;              # qdiiref.php:3

est 标的取自 ``QdiiGetEstArray()``（``qdiiref.php:33-34``）：纳指系 → ``^NDX``、
标普系 → ``^GSPC``。

生产端验算（sz161125cn.php，2026-10-12）：

    7807.24 * 6.7298 / 3.1817 = 16517.2   页面 16517.232  ✓

## 一条容易踩的坑：净值 ``1.0`` 是播种占位

演示库 QQQ / SPY 在 2026-10-01 / 10-02 的净值被播种成 ``1.0``（stockhistory
里 09-30 也是 ``1.0``）。照抄"优先取净值"就会算出 ``30501.56 / 1.0 = 30501``
这种数。所以这里取净值时**显式跳过 1.0**，退回当日价格 —— 与真净值只差
约 1%，不影响口径。
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))  # .../python/auto/_scratch
_AUTO = os.path.dirname(_HERE)                      # .../python/auto
_PYTHON = os.path.dirname(_AUTO)                    # .../python

sys.path.insert(0, _AUTO)
sys.path.insert(0, _PYTHON)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pymysql  # noqa: E402

from core.stockdata import load_mysql_env, mysql_config_from_env  # noqa: E402

#: 播种留下的占位净值，不能当真值用（见模块 docstring 最后一节）。
PLACEHOLDER_NET_VALUES = {1.0}

#: 待重算的符号。``kind`` 决定用哪条公式：
#:   pair —— 口径一（美股 ETF/指数）：cal = 配对价 / 本净值，无汇率
#:   qdii —— 口径二（A 股 QDII 基金）：cal = est价 * 汇率 / 本净值
#: est 是配对/估值的标的符号；汇率符号固定 USCNY（口径一不用）。
PLAN = [
    ("QQQ", "^NDX", "pair"),
    ("SPY", "^GSPC", "pair"),
    ("SZ161130", "^NDX", "qdii"),
    ("SZ161125", "^GSPC", "qdii"),
    ("SH513100", "^NDX", "qdii"),
]

CNY_SYMBOL = "USCNY"


def connect():
    cfg = mysql_config_from_env(load_mysql_env(os.environ))
    cfg["cursorclass"] = pymysql.cursors.DictCursor
    return pymysql.connect(**cfg)


def stock_id(cur, symbol):
    cur.execute("SELECT id FROM stock WHERE symbol = %s", (symbol,))
    row = cur.fetchone()
    return None if row is None else row["id"]


def close_on(cur, table, stock_id_value, date):
    """某天某表的 close（净值表优先，取不到再看行情表）。"""
    for name in (table,):
        cur.execute(
            "SELECT close FROM %s WHERE stock_id = %%s AND `date` = %%s" % name,
            (stock_id_value, date),
        )
        row = cur.fetchone()
        if row is not None:
            return float(row["close"])
    return None


def latest_on_or_before(cur, table, sid, date):
    """当日没有就退到**之前最近一条**，返回 ``(close, 那天)``。

    对应 PHP 那边的实际行为：``DailyCalibration()``（``fundpairref.php:193``）
    拿 ``$net_sql->GetDateNow($strStockId)`` —— 也就是**最新净值日** —— 当校准日期，
    所以"净值日"与"指数有报价的日"本来就不一定同一天。
    """
    cur.execute(
        "SELECT `date`, close FROM %s WHERE stock_id = %%s AND `date` <= %%s "
        "ORDER BY `date` DESC LIMIT 1" % table,
        (sid, date),
    )
    row = cur.fetchone()
    if row is None:
        return None, None
    return float(row["close"]), str(row["date"])


def my_value(cur, sid, date):
    """本标的当日的取值：**净值优先，占位 1.0 退回价格**。

    当日三类都取不到时，退到**之前最近一条净值**（``latest_on_or_before``），
    因为校准本来就是"净值日"驱动的。返回 ``(值, 来源)`` —— 来源写进
    dry-run 表里便于人工核对。
    """
    nav = close_on(cur, "netvaluehistory", sid, date)
    if nav is not None and nav not in PLACEHOLDER_NET_VALUES:
        return nav, "净值"
    price = close_on(cur, "stockhistory", sid, date)
    if price is not None:
        return price, "价格(净值占位)" if nav is not None else "价格"
    for name, label in (("netvaluehistory", "净值"), ("stockhistory", "价格")):
        value, that_day = latest_on_or_before(cur, name, sid, date)
        if value is not None and value not in PLACEHOLDER_NET_VALUES:
            return value, "%s(%s,<=%s)" % (label, value, that_day)
    return None, "无数据"


def pair_value(cur, sid, date):
    """配对 / est 标的当日的价。指数只有 ``stockhistory``，ETF 优先净值。"""
    nav = close_on(cur, "netvaluehistory", sid, date)
    if nav is not None and nav not in PLACEHOLDER_NET_VALUES:
        return nav
    price = close_on(cur, "stockhistory", sid, date)
    return price


def cny_on(cur, sid, date):
    """当日汇率；当日没有就退到之前最近一条（周末 / 假期）。"""
    cur.execute(
        "SELECT close FROM netvaluehistory WHERE stock_id = %s AND `date` <= %s "
        "ORDER BY `date` DESC LIMIT 1",
        (sid, date),
    )
    row = cur.fetchone()
    return None if row is None else float(row["close"])


def compute(cur, symbol, est_symbol, kind, date):
    """返回 ``(新值, 说明)``；算不出来返回 ``(None, 原因)``。"""
    sid = stock_id(cur, symbol)
    est_id = stock_id(cur, est_symbol)
    if sid is None or est_id is None:
        return None, "符号缺"

    mine, source = my_value(cur, sid, date)
    if mine is None:
        return None, "本标的%s无数据" % date
    est = pair_value(cur, est_id, date)
    if est is None:
        return None, "%s %s 无数据" % (est_symbol, date)

    if kind == "pair":
        return est / mine, "%s %s / 本%s(%s)" % (est_symbol, est, mine, source)

    cny_id = stock_id(cur, CNY_SYMBOL)
    cny = cny_on(cur, cny_id, date) if cny_id else None
    if cny is None:
        return None, "%s %s 无汇率" % (CNY_SYMBOL, date)
    return est * cny / mine, "%s %s * %s %s / 本%s(%s)" % (
        est_symbol, est, CNY_SYMBOL, cny, mine, source)


def main(argv):
    apply_changes = "--apply" in argv
    conn = connect()
    cur = conn.cursor()

    print("=== 校准值重算（生产端口径）%s ===" % ("【写库】" if apply_changes else "【dry-run】"))
    print()
    header = "%-9s %-8s %-10s %-14s %-14s %-8s %s"
    print(header % ("符号", "日期", "口径", "旧值", "新值", "倍数", "算式"))
    print("-" * 110)

    updates = []
    for symbol, est_symbol, kind in PLAN:
        sid = stock_id(cur, symbol)
        if sid is None:
            print("%-9s 库里没有这个符号，跳过" % symbol)
            continue
        cur.execute(
            "SELECT id, `date`, close FROM calibrationhistory WHERE stock_id = %s "
            "ORDER BY `date`", (sid,))
        rows = cur.fetchall()
        if not rows:
            print("%-9s 没有校准记录，跳过" % symbol)
            continue
        for row in rows:
            date = str(row["date"])
            old = float(row["close"])
            new, note = compute(cur, symbol, est_symbol, kind, date)
            if new is None:
                print("%-9s %-8s %-10s %-14s %-14s %-8s 跳过：%s"
                      % (symbol, date, kind, "%.6f" % old, "-", "-", note))
                continue
            ratio = new / old if old else float("inf")
            print(header % (symbol, date, kind, "%.6f" % old, "%.6f" % new,
                            "%.4g" % ratio, note))
            updates.append((row["id"], new))

    print()
    if not updates:
        print("没有可写的行。")
        return 0
    if not apply_changes:
        print("共 %d 行待更新。确认无误后加 --apply 写库。" % len(updates))
        return 0

    for row_id, new in updates:
        cur.execute(
            "UPDATE calibrationhistory SET close = %s WHERE id = %s",
            ("%.6f" % new, row_id),
        )
    conn.commit()
    print("已更新 %d 行。" % len(updates))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
