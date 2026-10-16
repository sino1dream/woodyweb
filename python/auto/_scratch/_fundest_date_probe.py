"""fundest 日期口径调查：本地库新鲜度 vs 写入日期语义。

用户问题：「值是对的、日期差一天，会不会是数据库没更新最新的净值？」

要回答它，需要同时看三样东西的**日期**：

1. 基金自己的净值（``netvaluehistory``）—— 最新到哪天；
2. 官方 EST 实际写进去的日期（``fundest.date``）—— 记在哪天；
3. 决定日期的中间层数据（est 符号的净值/行情）—— 最新到哪天。

口径背景（PHP 侧实测）：

* 写：``_QdiiReference::EstNetValue()``（qdiiref.php:134）用
  ``$strDate = $est_ref->GetDate()`` —— **est 引用对象的数据日期**；
  ``StockUpdateEstResult()`` 在该日期真实净值未到时写库。
* 读：``fundhistoryparagraph.php:39-42`` 按**净值日**（价格日的前一交易日）
  查 ``fundest``。所以 PHP 里 fundest.date 必须等于净值日才对得上。

    python -B auto/_scratch/_fundest_date_probe.py
"""

from __future__ import annotations

import io
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

AUTO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON_DIR = os.path.dirname(AUTO_DIR)
for _path in (AUTO_DIR, PYTHON_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from calc.fundcalc import qdii_est_symbol_map  # noqa: E402
from core.stockdata import MySqlFundDataSource, get_leverage_hedge_symbol  # noqa: E402


def _rows(source, sql, params=()):
    cursor = source.connection.cursor()
    try:
        cursor.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]
    finally:
        cursor.close()


def _latest(source, table, stock_id):
    return _rows(source,
                 "SELECT `date`, close FROM %s WHERE stock_id = %%s ORDER BY `date` DESC LIMIT 1"
                 % table, (stock_id,))


def main() -> int:
    source = MySqlFundDataSource.from_env()
    try:
        symbol = sys.argv[1] if len(sys.argv) > 1 else "SZ161125"
        print("=" * 78)
        print("目标基金：%s" % symbol)
        print("=" * 78)

        profile = source.get_fund_profile(symbol)
        est_symbol_raw = getattr(profile, "est_symbol", None)
        pair_symbol = getattr(profile, "pair_symbol", None)
        leverage = get_leverage_hedge_symbol(symbol)
        est_symbol = leverage or est_symbol_raw or pair_symbol
        print("est_symbol(档案) = %s，pair_symbol = %s，杠杆替代 = %s → 实际取数 %s"
              % (est_symbol_raw, pair_symbol, leverage, est_symbol))
        print("")

        stock_id = source.get_stock_id(symbol)
        est_id = source.get_stock_id(est_symbol) if est_symbol else None

        # 1) 基金净值最新到哪天
        print("--- 基金净值 netvaluehistory（最近 12 行）---")
        rows = _rows(source,
                     "SELECT `date`, close FROM netvaluehistory WHERE stock_id = %s "
                     "ORDER BY `date` DESC LIMIT 12", (stock_id,))
        for row in reversed(rows):
            print("  %s  %s" % (row["date"], row["close"]))
        print("")

        # 2) 基金行情（价格日）最新到哪天
        print("--- 基金行情 stockhistory（最近 6 行）---")
        rows = _rows(source,
                     "SELECT `date`, close FROM stockhistory WHERE stock_id = %s "
                     "ORDER BY `date` DESC LIMIT 6", (stock_id,))
        for row in reversed(rows):
            print("  %s  %s" % (row["date"], row["close"]))
        print("")

        # 3) fundest 全部记录
        print("--- fundest 全部记录（写入日期语义）---")
        rows = _rows(source,
                     "SELECT `date`, close, time FROM fundest WHERE stock_id = %s "
                     "ORDER BY `date`", (stock_id,))
        if not rows:
            print("  （无记录）")
        for row in rows:
            print("  date=%s  close=%s  time=%s" % (row["date"], row["close"], row["time"]))
        print("")

        # 4) 中间层数据新鲜度
        if est_id:
            print("--- 中间层 %s 的数据新鲜度 ---" % est_symbol)
            for table in ("netvaluehistory", "stockhistory"):
                latest = _latest(source, table, est_id)
                if latest:
                    print("  %-16s 最新 %s = %s" % (table, latest[0]["date"], latest[0]["close"]))
                else:
                    print("  %-16s （无行）" % table)
            print("")
        else:
            print("--- 中间层 %s 不在 stock 表 ---" % est_symbol)
            print("")

        # 5) 汇率新鲜度
        cny_id = source.get_stock_id("USCNY")
        print("--- USCNY 汇率 ---")
        if cny_id:
            rows = _rows(source,
                         "SELECT `date`, close FROM netvaluehistory WHERE stock_id = %s "
                         "ORDER BY `date` DESC LIMIT 5", (cny_id,))
            for row in reversed(rows):
                print("  %s  %s" % (row["date"], row["close"]))
        else:
            print("  USCNY 不在 stock 表")
        print("")

        # 6) 横向：所有基金的 fundest 日期 vs 净值日期
        print("--- 横向对比：每只基金 净值最新日 / fundest 最新日 / 中间层最新日 ---")
        print("  %-10s %-12s %-12s %-12s %-10s" % ("符号", "净值最新", "fundest最新", "est最新", "差(天)"))
        funds = sorted(qdii_est_symbol_map())
        for sym in funds:
            sid = source.get_stock_id(sym)
            if sid is None:
                continue
            nav = _latest(source, "netvaluehistory", sid)
            fe = _rows(source, "SELECT `date` FROM fundest WHERE stock_id = %s "
                               "ORDER BY `date` DESC LIMIT 1", (sid,))
            prof = source.get_fund_profile(sym)
            raw = getattr(prof, "est_symbol", None) or getattr(prof, "pair_symbol", None)
            lev = get_leverage_hedge_symbol(sym)
            eff = lev or raw
            eid = source.get_stock_id(eff) if eff else None
            est_nav = _latest(source, "netvaluehistory", eid) if eid else []
            est_his = _latest(source, "stockhistory", eid) if eid else []
            est_latest = est_nav or est_his
            nav_d = nav[0]["date"] if nav else None
            fe_d = fe[0]["date"] if fe else None
            est_d = est_latest[0]["date"] if est_latest else None
            gap = ""
            if fe_d and est_d:
                import datetime
                gap = str((datetime.date.fromisoformat(str(fe_d))
                           - datetime.date.fromisoformat(str(est_d))).days)
            print("  %-10s %-12s %-12s %-12s %-10s" % (
                sym, nav_d or "-", fe_d or "-", est_d or "-", gap))
    finally:
        source.connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
