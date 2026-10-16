"""Plan A 一次性数据修复第 2 步：按 PHP 口径重写基金层校准值（10-13 / 10-14）。

## 为什么

PHP ``_QdiiReference::AdjustFactor()``（``php/stock/qdiiref.php:178``）的机制：

* 闸门 ``UpdateOfficialNetValue()`` = ``StockCompareEstResult()``，
  只有 ``net_sql->InsertDaily()`` **插入新净值日期**时才返回 true；
* 于是每当净值表出现新日期 D，就用**同一天**的三个量重算一次::

      cal[D] = est[D] × cny[D] / nav[D]        （QdiiGetCalibration）

  ``est`` 取原始估值标的（指数 ^GSPC/^NDX/^DJI 或 ETF，净值优先、行情价兜底），
  ``cny`` 取 USCNY 当天中间价，``nav`` 取基金当天单位净值；
* 写入日期就是 D（``InsertFundCalibration()`` 用 ``$this->GetDate()``
  = 基金的净值日）。

我们漏掉了两个窗口：10-13 那批（P0.5，00:50）用的是**新浪即期汇率**，
今天外汇管理局中间价回填后已过期（SZ161125 重算 16517.03 vs 库里 16416.30）；
10-14 那批是今早 9:49 用**当时的旧数据**写的（nav 未补到 10-14、ETF 无 10-14
行情），只有 18 只原始 est 是指数的基金写成功了，值也不对。

本脚本把这两个日期的 33 只基金校准行**删掉重算**，与生产端
``calibrationhistory`` 同口径。``cal_f`` 在估值公式里会约掉，重写是安全的；
但它参与页面「校准值」显示与 ``get_stock_hedge``，所以值也要对。

用法::

    python -B auto/_scratch/_plan_a_recalibrate.py            # 预演
    python -B auto/_scratch/_plan_a_recalibrate.py --apply    # 真写
"""

from __future__ import annotations

import datetime
import io
import os
import sys

_SCRATCH_DIR = os.path.dirname(os.path.abspath(__file__))
_AUTO_DIR = os.path.dirname(_SCRATCH_DIR)
_PYTHON_DIR = os.path.dirname(_AUTO_DIR)
for _path in (_AUTO_DIR, _PYTHON_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

_REAL_STDOUT = sys.stdout
sys.stdout = io.StringIO()

from core.stockdata import MySqlFundDataSource  # noqa: E402
from calc.fundcalc import qdii_est_symbol, qdii_est_symbol_map  # noqa: E402

sys.stdout = (io.TextIOWrapper(_REAL_STDOUT.buffer, encoding="utf-8", errors="replace")
              if hasattr(_REAL_STDOUT, "buffer") else _REAL_STDOUT)

#: 要重算的日期：基金最新净值日（10-14）与它的前一天（10-13，旧汇率那批）。
DATES = ("2026-10-14", "2026-10-13")


def _fetch(source, sql, params):
    cursor = source.connection.cursor()
    try:
        cursor.execute(sql, params)
        return cursor.fetchall()
    finally:
        cursor.close()


def _netvalue(source, stock_id, date):
    rows = _fetch(source,
                  "SELECT close FROM netvaluehistory WHERE stock_id = %s AND date = %s",
                  (stock_id, date))
    return float(rows[0]["close"]) if rows else None


def _history_close(source, stock_id, date):
    rows = _fetch(source,
                  "SELECT close FROM stockhistory WHERE stock_id = %s AND date = %s",
                  (stock_id, date))
    return float(rows[0]["close"]) if rows else None


def _stored_calibration(source, stock_id, date):
    rows = _fetch(source,
                  "SELECT close FROM calibrationhistory WHERE stock_id = %s AND date = %s",
                  (stock_id, date))
    return float(rows[0]["close"]) if rows else None


def _est_value_on(source, est_symbol, date):
    """PHP ``_getEstNetValue`` -> ``est_ref->GetClose``：净值优先，行情价兜底。"""
    est_id = source.get_stock_id(est_symbol)
    if est_id is None:
        return None, "符号不在 stock 表"
    value = _netvalue(source, est_id, date)
    if value is not None:
        return value, "净值"
    value = _history_close(source, est_id, date)
    if value is not None:
        return value, "行情价"
    return None, "当天既无净值也无行情"


def main() -> int:
    apply = "--apply" in sys.argv
    source = MySqlFundDataSource.from_env()

    funds = sorted(qdii_est_symbol_map())
    cny_id = source.get_stock_id("USCNY")

    written = skipped = 0
    for date in DATES:
        cny = _netvalue(source, cny_id, date)
        print("=" * 96)
        print("%s   USCNY 中间价 = %s" % (date, cny))
        print("=" * 96)
        if cny is None:
            print("  **当天没有汇率，整个日期跳过**")
            continue

        for symbol in funds:
            stock_id = source.get_stock_id(symbol)
            if stock_id is None:
                continue
            nav = _netvalue(source, stock_id, date)
            if nav is None:
                print("  %-9s 跳过：当天没有单位净值" % symbol)
                skipped += 1
                continue
            est_symbol = qdii_est_symbol(symbol)
            est_value, est_kind = _est_value_on(source, est_symbol, date)
            if est_value is None:
                print("  %-9s 跳过：%s 在 %s 取不到值" % (symbol, est_symbol, date))
                skipped += 1
                continue

            calibration = est_value * cny / nav
            old = _stored_calibration(source, stock_id, date)
            old_text = "-" if old is None else "%.6f" % old
            delta = "" if old is None else "  (%+.2f%%)" % ((calibration / old - 1.0) * 100.0)
            print("  %-9s %-6s %-12s × %-8s / %-10s = %-16.6f  旧值 %s%s" % (
                symbol, est_symbol, est_value, cny, nav, calibration, old_text, delta))

            if not apply:
                continue
            cursor = source.connection.cursor()
            try:
                cursor.execute(
                    "DELETE FROM calibrationhistory WHERE stock_id = %s AND date = %s",
                    (stock_id, date))
                cursor.execute(
                    "INSERT INTO calibrationhistory (stock_id, `date`, close, `time`, num) "
                    "VALUES (%s, %s, %s, %s, 1)",
                    (stock_id, date, calibration,
                     datetime.datetime.now().strftime("%H:%M:%S")))
                source.connection.commit()
                written += 1
            finally:
                cursor.close()

    source.connection.close()
    print("")
    if apply:
        print("完成，重写 %d 行，跳过 %d 只。" % (written, skipped))
    else:
        print("这是预演。真正执行请加 --apply。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
