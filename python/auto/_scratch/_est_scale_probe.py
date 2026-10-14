"""探针：把 QDII 基金三个 EST 的**完整输入链**打出来，并对照三种公式。

跑法（在 ``python/`` 下）::

    D:/python/python.exe -B auto/_scratch/_est_scale_probe.py

## 为什么写这个

``_qdii_value()`` 的旧实现用恒等式 ``est_quote_ref = cny_ref / cal_m`` 反推
中间层基准价。该恒等式只对**旧尺度**的中间层校准值成立（演示库早期把 QQQ
播成 ``0.008945`` = 汇率/报价）；2026-10-13 按生产端口径重算后 QQQ 变成
``41.58``（= 指数/净值），恒等式失效，``/qdii`` 页官方 EST 放大 4648 倍。

本探针把生产端验算过的两条口径（见 ``_calibration_recount.py`` 模块 docstring）
与 Python 现行公式放在一起逐个数比。只读不写。
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

from core.stockdata import load_mysql_env, mysql_config_from_env, get_leverage_hedge_symbol  # noqa: E402
from calc.fundcalc import qdii_est_symbol  # noqa: E402

FUNDS = ["SH513100", "SZ161130", "SZ161125"]
CNY_SYMBOL = "USCNY"


def connect():
    cfg = mysql_config_from_env(load_mysql_env(os.environ))
    cfg["cursorclass"] = pymysql.cursors.DictCursor
    return pymysql.connect(**cfg)


def q(cur, sql, args=None):
    cur.execute(sql, args or ())
    return cur.fetchall()


def one(cur, sql, args=None):
    rows = q(cur, sql, args)
    return rows[0] if rows else None


def sid_of(cur, symbol):
    row = one(cur, "SELECT id FROM stock WHERE symbol = %s", (symbol,))
    return None if row is None else row["id"]


def dump_table(cur, table, sid, limit=8, label=""):
    rows = q(cur, "SELECT `date`, close FROM %s WHERE stock_id = %%s "
                  "ORDER BY `date` DESC LIMIT %d" % (table, limit), (sid,))
    print("    %-26s %s" % (label or table,
                            ", ".join("%s=%s" % (r["date"], r["close"]) for r in rows) or "(空)"))


def main():
    conn = connect()
    cur = conn.cursor()
    try:
        for symbol in FUNDS:
            print("=" * 100)
            print(symbol)
            print("=" * 100)
            sid = sid_of(cur, symbol)
            if sid is None:
                print("  (不在 stock 表)")
                continue
            raw_est = qdii_est_symbol(symbol)
            est_symbol = get_leverage_hedge_symbol(symbol) or raw_est
            est_id = sid_of(cur, est_symbol) if est_symbol else None
            index_id = sid_of(cur, raw_est) if raw_est else None
            cny_id = sid_of(cur, CNY_SYMBOL)
            print("    stock_id=%s  est(原始)=%s  est(杠杆替代)=%s(id=%s)  指数id=%s"
                  % (sid, raw_est, est_symbol, est_id, index_id))

            dump_table(cur, "calibrationhistory", sid, label="基金校准(全)")
            dump_table(cur, "netvaluehistory", sid, limit=6, label="基金净值")
            dump_table(cur, "stockhistory", sid, limit=4, label="基金行情")
            if est_id:
                dump_table(cur, "calibrationhistory", est_id, label="中间层校准(全)")
                dump_table(cur, "netvaluehistory", est_id, limit=6, label="中间层净值")
                dump_table(cur, "stockhistory", est_id, limit=6, label="中间层行情")
            if index_id and index_id != est_id:
                dump_table(cur, "stockhistory", index_id, limit=6, label="指数行情(%s)" % raw_est)
                dump_table(cur, "netvaluehistory", index_id, limit=4, label="指数净值(%s)" % raw_est)
            dump_table(cur, "netvaluehistory", cny_id, limit=6, label="汇率")

            # 逐条校准记录验算口径
            print("    -- 逐条校准记录验算（口径二：cal = est * cny / 本净值） --")
            for row in q(cur, "SELECT id, `date`, close FROM calibrationhistory "
                              "WHERE stock_id = %s ORDER BY `date`", (sid,)):
                date, cal_f = str(row["date"]), float(row["close"])
                parts = []
                for cand_id, cand_name in ((est_id, est_symbol), (index_id, raw_est)):
                    if not cand_id:
                        continue
                    est = one(cur, "SELECT close FROM netvaluehistory WHERE stock_id = %s AND `date` = %s",
                              (cand_id, date))
                    est_src = "nav"
                    if est is None:
                        est = one(cur, "SELECT close FROM stockhistory WHERE stock_id = %s AND `date` = %s",
                                  (cand_id, date))
                        est_src = "price"
                    if est is None:
                        continue
                    est = float(est["close"])
                    mine = one(cur, "SELECT close FROM netvaluehistory WHERE stock_id = %s AND `date` = %s",
                               (sid, date))
                    mine_src = "nav"
                    if mine is None:
                        mine = one(cur, "SELECT close FROM stockhistory WHERE stock_id = %s AND `date` = %s",
                                   (sid, date))
                        mine_src = "price"
                    if mine is None:
                        # 退到之前最近一条净值
                        mine = one(cur, "SELECT close FROM netvaluehistory WHERE stock_id = %s "
                                        "AND `date` <= %s ORDER BY `date` DESC LIMIT 1", (sid, date))
                        mine_src = "nav(<=)"
                    if mine is None:
                        continue
                    mine = float(mine["close"])
                    cny = one(cur, "SELECT close FROM netvaluehistory WHERE stock_id = %s "
                                   "AND `date` <= %s ORDER BY `date` DESC LIMIT 1", (cny_id, date))
                    if cny is None:
                        continue
                    cny = float(cny["close"])
                    implied = est * cny / mine
                    parts.append("est=%s(%s) cny=%s mine=%s(%s) -> %.4f  [%.4f×]"
                                 % (est, cand_name, cny, mine, mine_src, implied, implied / cal_f))
                print("      %s cal=%s" % (date, cal_f))
                for part in parts:
                    print("        %s" % part)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
