"""P0.4 收尾探针：验证 estquote 表的中间层覆盖（只读，不写库）。

跑法（在 ``python/`` 下）::

    D:/python/python.exe -B auto/_scratch/_p0_4_estquote_probe.py

## 背景

P0.4 之前 estquote 只有 QQQ/SPY 等美股中间层 16 行（README_estquote_and_navcheck.md
§十一 记录"只覆盖抓过的中间层标的"）。``default_est_symbols()`` 补齐港日欧 7 个
符号（^HSI/^HSTECH/^HSCE/SH000869/znb_NKY/znb_CAC/znb_DAX）后，daemon 四轮循环
应把这 7 行也写进 estquote。本探针对照 ``symbolinventory.est_symbols()`` 全量
名单，报出缺失与陈旧行，作为 README §五 的实测依据。
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
from core.symbolinventory import est_symbols  # noqa: E402

#: P0.4 新接入 daemon 的港日欧中间层（README_p0_data_layer.md §五）。
NEW_HK_JP_EU = ["^HSI", "^HSTECH", "^HSCE", "SH000869", "znb_NKY", "znb_CAC", "znb_DAX"]


def connect():
    cfg = mysql_config_from_env(load_mysql_env(os.environ))
    cfg["cursorclass"] = pymysql.cursors.DictCursor
    return pymysql.connect(**cfg)


def main():
    wanted = sorted(est_symbols())
    print("symbolinventory.est_symbols() 共 %d 个中间层符号" % len(wanted))
    conn = connect()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT e.id, e.symbol, e.source_symbol, e.close, e.quote_date, "
            "e.quote_time, e.source, e.fetched_at "
            "FROM estquote e"
        )
        rows = {row["symbol"]: row for row in cursor.fetchall()}
    finally:
        conn.close()

    print("estquote 表现有 %d 行" % len(rows))
    print("")

    missing = [s for s in wanted if s not in rows]
    print("== 缺失 %d 个 ==" % len(missing))
    for s in missing:
        print("  %s" % s)
    if not missing:
        print("  （无）")
    print("")

    extra = sorted(set(rows) - set(wanted))
    if extra:
        print("== 多出（不在 est_symbols 名单） %d 个 ==" % len(extra))
        for s in extra:
            print("  %s" % s)
        print("")

    print("== P0.4 新接入的 7 个港日欧中间层 ==")
    for s in NEW_HK_JP_EU:
        row = rows.get(s)
        if row is None:
            print("  %-10s 缺失" % s)
        else:
            print("  %-10s close=%-14s quote_date=%s quote_time=%s fetched_at=%s"
                  % (s, row["close"], row["quote_date"], row["quote_time"],
                     row["fetched_at"]))
    print("")

    print("== 全部 estquote 行（按符号） ==")
    for s in sorted(rows):
        row = rows[s]
        print("  %-10s close=%-14s date=%s time=%s fetched=%s src=%s"
              % (s, row["close"], row["quote_date"], row["quote_time"],
                 row["fetched_at"], row["source"]))


if __name__ == "__main__":
    main()
