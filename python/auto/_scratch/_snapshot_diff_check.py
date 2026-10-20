# 快照表诊断：官方 EST 的 mid_date 来自 realtimequote，est_value 来自 estquote，
# 两者不同步就会"日期是新的、价格是旧的"。只读。
# 运行：D:/python/python.exe -B auto/_scratch/_snapshot_diff_check.py

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


def connect():
    cfg = mysql_config_from_env(load_mysql_env(os.environ))
    cfg["cursorclass"] = pymysql.cursors.DictCursor
    return pymysql.connect(**cfg)


def main():
    conn = connect()
    cur = conn.cursor()

    for symbol in ("^NDX", "SZ161130", "USCNY"):
        cur.execute("SELECT id, symbol, name FROM stock WHERE symbol = %s", (symbol,))
        row = cur.fetchone()
        print("== %s -> %s" % (symbol, row))
        if not row:
            continue
        sid = row["id"]
        for table in ("realtimequote", "estquote"):
            cur.execute("SELECT * FROM `%s` WHERE stock_id = %%s" % table, (sid,))
            rows = cur.fetchall()
            print("   %s: %s" % (table, [{k: str(v) for k, v in r.items()} for r in rows] or "（空）"))

    # 表结构（看看有哪些列）
    for table in ("realtimequote", "estquote"):
        cur.execute("SHOW COLUMNS FROM `%s`" % table)
        print("%s 列: %s" % (table, [r["Field"] for r in cur.fetchall()]))

    conn.close()


if __name__ == "__main__":
    main()
