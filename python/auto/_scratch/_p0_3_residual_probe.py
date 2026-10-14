"""P0.3 回填后的 EST 残留偏差归因探针（只读，不写库）。

跑法（在 ``python/`` 下）::

    D:/python/python.exe -B auto/_scratch/_p0_3_residual_probe.py

## 要回答的问题

P0.3 回填后三个 QDII 基金的官方 EST 是 2.0731 / 4.6216 / 3.1980，
生产端是 2.049 / 4.572 / 3.182（2026-10-12 抓的页面快照），差 0.5%~1.2%。
这个探针把 ``est_cal`` 与 ``est_now`` 各自的**取值来源和日期**打出来，
定位偏差是"数据陈旧"还是"公式错"。

## 预期结论（写进 README §四 的依据）

* ``est_cal``（中间层校准基准值）取不到校准日当天的值，只能退到更早的
  行情价；
* ``est_now``（中间层当前值）取的是中间层最新日期的净值；
* 两者**跨日混口径**（一个价格、一个净值），比值因此偏离 1；
* 根因是中间层净值表只有一条真值 —— 这就是 P0.5 的"中间层净值缺口"。
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

#: 三个 A 股 QDII 基金与它们的 est 中间层（口径二，qdiiref.php:33-34）。
PLAN = [
    ("SZ161130", "^NDX"),
    ("SZ161125", "^GSPC"),
    ("SH513100", "^NDX"),
]

#: 生产端页面数值（2026-10-12 抓取，README_fundest_migration.md §十 有记录）。
PRODUCTION = {"SZ161130": 4.572, "SZ161125": 3.182, "SH513100": 2.049}

#: 回填后本地算出的官方 EST（P0.3 commit 344d5383 的实测记录）。
LOCAL = {"SZ161130": 4.6216, "SZ161125": 3.1980, "SH513100": 2.0731}


def connect():
    cfg = mysql_config_from_env(load_mysql_env(os.environ))
    cfg["cursorclass"] = pymysql.cursors.DictCursor
    return pymysql.connect(**cfg)


def stock_id(cur, symbol):
    cur.execute("SELECT id FROM stock WHERE symbol = %s", (symbol,))
    row = cur.fetchone()
    return None if row is None else row["id"]


def rows_of(cur, table, sid, limit=8):
    cur.execute(
        "SELECT `date`, close FROM %s WHERE stock_id = %%s "
        "ORDER BY `date` DESC LIMIT %d" % (table, int(limit)),
        (sid,),
    )
    return [(str(r["date"]), float(r["close"])) for r in cur.fetchall()]


def probe_symbol(cur, symbol, label):
    sid = stock_id(cur, symbol)
    print("  %s %s (stock_id=%s)" % (label, symbol, sid))
    if sid is None:
        return
    for table in ("netvaluehistory", "stockhistory", "calibrationhistory"):
        rows = rows_of(cur, table, sid)
        if not rows:
            print("    %-20s （无行）" % table)
            continue
        shown = ", ".join("%s=%.4f" % (d, c) for d, c in rows[:5])
        print("    %-20s %d 行，最新 5 条: %s" % (table, len(rows), shown))


def main():
    conn = connect()
    cur = conn.cursor()
    try:
        print("=== 中间层净值/行情存量（缺口证据） ===")
        for est in ("^NDX", "^GSPC", "QQQ", "SPY"):
            probe_symbol(cur, est, "中间层")
            print()

        print("=== 三个 QDII 基金的校准与净值存量 ===")
        for fund, _est in PLAN:
            probe_symbol(cur, fund, "基金")
            print()

        print("=== 残留偏差对照 ===")
        print("  基金       本地 EST    生产 EST    偏差")
        for fund, _est in PLAN:
            local, prod = LOCAL[fund], PRODUCTION[fund]
            print("  %-10s %-11.4f %-11.4f %+.2f%%" % (
                fund, local, prod, (local / prod - 1) * 100))

        print()
        print("=== 中间层最新净值日期 vs 基金最新净值日期 ===")
        for fund, est in PLAN:
            fund_sid = stock_id(cur, fund)
            est_sid = stock_id(cur, est)
            cur.execute(
                "SELECT MAX(`date`) AS d FROM netvaluehistory WHERE stock_id = %s",
                (fund_sid,))
            fund_nav_date = cur.fetchone()["d"]
            cur.execute(
                "SELECT MAX(`date`) AS d FROM netvaluehistory WHERE stock_id = %s",
                (est_sid,))
            est_nav_date = cur.fetchone()["d"]
            cur.execute(
                "SELECT MAX(`date`) AS d FROM stockhistory WHERE stock_id = %s",
                (est_sid,))
            est_quote_date = cur.fetchone()["d"]
            print("  %s -> %s：基金净值日 %s / 中间层净值日 %s / 中间层行情日 %s"
                  % (fund, est, fund_nav_date, est_nav_date, est_quote_date))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
