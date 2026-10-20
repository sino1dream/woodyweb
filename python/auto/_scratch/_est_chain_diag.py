# -*- coding: utf-8 -*-
"""SZ161130 官方 EST 取数链全量诊断（只读，不写库）。

目的：把 fundestcalc 官方 EST 链路上每个入参的**实际取值**打出来，
对比 ^NDX 模型与 QQQ 模型，确认生产端口径。

用法：D:/python/python.exe -B auto/_scratch/_est_chain_diag.py
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import pymysql

from auto.core.stockdata import MySqlFundDataSource, load_mysql_env, mysql_config_from_env

FUND = "SZ161130"


def q(cur, sql, args=None):
    cur.execute(sql, args or ())
    return cur.fetchall()


def main():
    config = mysql_config_from_env(load_mysql_env(os.environ))
    conn = pymysql.connect(
        host=config["host"], port=config["port"], user=config["user"],
        password=config["password"], database=config["database"],
        charset="utf8", cursorclass=pymysql.cursors.DictCursor)
    cur = conn.cursor()
    source = MySqlFundDataSource(conn)

    print("=" * 100)
    print("① fundest 表现有记录（SZ161130）")
    print("=" * 100)
    for r in q(cur, """SELECT f.date, f.close, f.time FROM fundest f
                        JOIN stock s ON s.id = f.stock_id WHERE s.symbol = %s
                        ORDER BY f.date DESC""", (FUND,)):
        print("  %s  close=%-12s time=%s" % (r["date"], r["close"], r["time"]))

    print()
    print("=" * 100)
    print("② netvaluehistory（SZ161130 基金净值）")
    print("=" * 100)
    for r in q(cur, """SELECT n.date, n.close FROM netvaluehistory n
                        JOIN stock s ON s.id = n.stock_id WHERE s.symbol = %s
                        ORDER BY n.date DESC LIMIT 12""", (FUND,)):
        print("  %s  %s" % (r["date"], r["close"]))

    print()
    print("=" * 100)
    print("③ calibrationhistory（SZ161130）")
    print("=" * 100)
    for r in q(cur, """SELECT c.date, c.close FROM calibrationhistory c
                        JOIN stock s ON s.id = c.stock_id WHERE s.symbol = %s
                        ORDER BY c.date DESC LIMIT 6""", (FUND,)):
        print("  %s  %s" % (r["date"], r["close"]))

    print()
    print("=" * 100)
    print("④ USCNY：netvaluehistory（中间价）+ realtimequote")
    print("=" * 100)
    for r in q(cur, """SELECT n.date, n.close FROM netvaluehistory n
                        JOIN stock s ON s.id = n.stock_id WHERE s.symbol = 'USCNY'
                        ORDER BY n.date DESC LIMIT 15""", ):
        print("  nav %s  %s" % (r["date"], r["close"]))
    r = q(cur, "SELECT symbol, price, quote_date, quote_time, fetched_at FROM realtimequote WHERE symbol='USCNY'")
    print("  realtimequote:", r)

    print()
    print("=" * 100)
    print("⑤ 中间层四兄弟：stockhistory 末 8 行 + 快照")
    print("=" * 100)
    for sym in ("^NDX", "^GSPC", "QQQ", "SPY"):
        sid = source.get_stock_id(sym)
        print("--- %s (stock_id=%s) ---" % (sym, sid))
        rows = q(cur, """SELECT h.date, h.close FROM stockhistory h
                         WHERE h.stock_id = %s ORDER BY h.date DESC LIMIT 8""", (sid,))
        for r in rows:
            print("    hist %s  %s" % (r["date"], r["close"]))
        if not rows:
            print("    hist （无行）")
        rt = q(cur, "SELECT price, prev_price, quote_date, quote_time, fetched_at FROM realtimequote WHERE symbol=%s", (sym,))
        print("    realtimequote:", rt)
        eq = q(cur, "SELECT close, prev_close, quote_date, quote_time, fetched_at FROM estquote WHERE symbol=%s", (sym,))
        print("    estquote      :", eq)
        nv = q(cur, """SELECT n.date, n.close FROM netvaluehistory n
                       WHERE n.stock_id = %s ORDER BY n.date DESC LIMIT 4""", (sid,))
        print("    netvaluehistory:", nv if nv else "（无行）")
        cal = q(cur, """SELECT c.date, c.close FROM calibrationhistory c
                        WHERE c.stock_id = %s ORDER BY c.date DESC LIMIT 3""", (sid,))
        print("    calibrationhistory:", cal if cal else "（无行）")

    print()
    print("=" * 100)
    print("⑥ SZ161130 自身行情：stockhistory 末 6 行 + 快照")
    print("=" * 100)
    sid = source.get_stock_id(FUND)
    for r in q(cur, "SELECT date, close FROM stockhistory WHERE stock_id=%s ORDER BY date DESC LIMIT 6", (sid,)):
        print("    hist %s  %s" % (r["date"], r["close"]))
    rt = q(cur, "SELECT price, quote_date, quote_time, fetched_at FROM realtimequote WHERE symbol=%s", (FUND,))
    print("    realtimequote:", rt)

    conn.close()


if __name__ == "__main__":
    main()
