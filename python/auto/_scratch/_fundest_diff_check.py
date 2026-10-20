# 本地 vs 生产端「基金溢价记录」差异排查（SZ161130）。
# 只读诊断：把本地库里这张表用到的四类数据全部拉出来，逐行对照生产端页面。
# 运行：D:/python/python.exe -B auto/_scratch/_fundest_diff_check.py

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


def connect():
    cfg = mysql_config_from_env(load_mysql_env(os.environ))
    cfg["cursorclass"] = pymysql.cursors.DictCursor
    return pymysql.connect(**cfg)


def stock_id(cur, symbol):
    cur.execute("SELECT id, name FROM stock WHERE symbol = %s", (symbol,))
    return cur.fetchone()


def dump(cur, table, stock_id_value, limit=14):
    cur.execute(
        "SELECT * FROM `%s` WHERE stock_id = %%s ORDER BY `date` DESC LIMIT %d" % (table, limit),
        (stock_id_value,),
    )
    return cur.fetchall()


def main():
    fund = "SZ161130"
    est = "^NDX"
    conn = connect()
    cur = conn.cursor()

    fund_row = stock_id(cur, fund)
    est_row = stock_id(cur, est)
    print("基金行:", fund_row)
    print("标的行:", est_row)
    if not fund_row or not est_row:
        print("!! 符号不存在")
        return
    fid, eid = fund_row["id"], est_row["id"]

    for title, table, sid in (
        ("基金 stockhistory（场内价）", "stockhistory", fid),
        ("基金 netvaluehistory（净值）", "netvaluehistory", fid),
        ("基金 fundest（官方EST）", "fundest", fid),
        ("基金 calibrationhistory", "calibrationhistory", fid),
        ("^NDX stockhistory", "stockhistory", eid),
        ("^NDX netvaluehistory", "netvaluehistory", eid),
    ):
        rows = dump(cur, table, sid)
        print("\n== %s（%d 行） ==" % (title, len(rows)))
        for r in rows:
            print("  ", {k: str(v) for k, v in r.items()})

    # 汇率 USCNY
    cny_row = stock_id(cur, "USCNY")
    if cny_row:
        rows = dump(cur, "netvaluehistory", cny_row["id"], limit=8)
        print("\n== USCNY netvaluehistory（%d 行） ==" % len(rows))
        for r in rows:
            print("  ", {k: str(v) for k, v in r.items()})

    # 基金在 stock 表里的配置行（仓位/日期等）
    cur.execute("SELECT * FROM stock WHERE symbol = %s", (fund,))
    print("\n== stock 表配置行 ==")
    print("  ", {k: str(v) for k, v in cur.fetchone().items()})

    conn.close()


if __name__ == "__main__":
    main()
