# 全量扫描：每只 QDII 基金的 stockhistory / netvaluehistory / fundest 最新日期，
# 以及 ^NDX/^GSPC/USCNY 的历史缺口。只读。
# 运行：D:/python/python.exe -B auto/_scratch/_qdii_gap_scan.py

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_AUTO = os.path.dirname(_HERE)
_PYTHON = os.path.dirname(_AUTO)
sys.path.insert(0, _AUTO)
sys.path.insert(0, _PYTHON)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pymysql  # noqa: E402

from core.stockdata import load_mysql_env, mysql_config_from_env  # noqa: E402
from calc.fundcalc import qdii_est_symbol_map  # noqa: E402


def connect():
    cfg = mysql_config_from_env(load_mysql_env(os.environ))
    cfg["cursorclass"] = pymysql.cursors.DictCursor
    return pymysql.connect(**cfg)


def latest_dates(cur, table, stock_id_value, limit=3):
    cur.execute(
        "SELECT `date`, close FROM `%s` WHERE stock_id = %%s ORDER BY `date` DESC LIMIT %d"
        % (table, limit),
        (stock_id_value,),
    )
    return cur.fetchall()


def all_dates(cur, table, stock_id_value):
    cur.execute(
        "SELECT `date`, close, time FROM `%s` WHERE stock_id = %%s ORDER BY `date`" % table,
        (stock_id_value,),
    )
    return cur.fetchall()


def main():
    conn = connect()
    cur = conn.cursor()

    print("== QDII 基金数据现状 ==")
    print("%-10s %-8s %-22s %-22s %s" % ("基金", "est", "stockhistory最新", "netvaluehistory最新", "fundest日期"))
    for symbol, est in sorted(qdii_est_symbol_map().items()):
        cur.execute("SELECT id FROM stock WHERE symbol = %s", (symbol,))
        row = cur.fetchone()
        if not row:
            continue
        sid = row["id"]
        sh = latest_dates(cur, "stockhistory", sid)
        nv = latest_dates(cur, "netvaluehistory", sid)
        fe = all_dates(cur, "fundest", sid)
        sh_text = ",".join("%s=%s" % (str(r["date"]), r["close"]) for r in sh) or "（无）"
        nv_text = ",".join("%s=%s" % (str(r["date"]), r["close"]) for r in nv) or "（无）"
        fe_text = ",".join(str(r["date"]) for r in fe) or "（无）"
        print("%-10s %-8s %-22s %-22s %s" % (symbol, est, sh_text, nv_text, fe_text))

    print("\n== 标的与汇率 ==")
    for symbol in ("^NDX", "^GSPC", "USCNY", "QQQ", "SPY"):
        cur.execute("SELECT id FROM stock WHERE symbol = %s", (symbol,))
        row = cur.fetchone()
        if not row:
            print("%-8s （stock 表无此行）" % symbol)
            continue
        sid = row["id"]
        sh = latest_dates(cur, "stockhistory", sid, limit=6)
        nv = latest_dates(cur, "netvaluehistory", sid, limit=6)
        print("%-8s stockhistory: %s" % (symbol, ", ".join("%s=%s" % (str(r["date"]), r["close"]) for r in sh) or "（无）"))
        print("%-8s netvaluehistory: %s" % (symbol, ", ".join("%s=%s" % (str(r["date"]), r["close"]) for r in nv) or "（无）"))

    # SZ161130 的基金净值全量（回填 fundest 要用）
    print("\n== SZ161130 netvaluehistory 全量 ==")
    cur.execute("SELECT id FROM stock WHERE symbol = 'SZ161130'")
    sid = cur.fetchone()["id"]
    for r in all_dates(cur, "netvaluehistory", sid):
        print("   ", str(r["date"]), r["close"])

    conn.close()


if __name__ == "__main__":
    main()
