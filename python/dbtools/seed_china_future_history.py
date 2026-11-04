"""给国内商品期货（``nf_AU0`` / ``nf_AG0`` / ``nf_M0``）补 ``stockhistory``。

## 为什么需要

「A股商品」那 6 只基金跟踪的是国内商品期货（见 ``patch_china_future.py``），
但期货自己的日线在库里**一条都没有** —— 直接后果是：

* 官方 EST 会退到 ``_middle_quote()``（快照），一旦没抓到快照就整列空；
* 参考 EST 走 ``_fair_quote()``（指定日期的 ``stockhistory`` 收盘价），
  没有日线就永远算不出来；
* 生产端 ``sh518800cn.php`` 的「AU0 历史价格」段（5/10/20 日均线、
  布林、周线共 10 行）需要长历史，短窗口补不出周线。

## 数据源

``updater.asharehistory.fetch_cn_future_history``（新浪
``InnerFuturesNewService.getDailyKLine``，返回 JSONP）。实测与生产端页面的
「参考数据」逐位一致：``AU0`` 2026-11-03 收盘 ``841.8``、
``M0`` ``3147``、``AG0`` ``11655``。

## 写什么、不写什么

* 只写 ``stockhistory``（``adjclose`` 取 ``close``）——
  期货没有除权，也不需要复权；
* **不写 EMA**：均线段用的是"估值标的"（``AU0``）自己的 EMA 表，
  本项目该段还没迁（见 ``README_pending_pages.md``），先不铺数据；
* **不写净值**：期货没有净值，``netvaluehistory`` 不能塞收盘价，
  否则 ``_official_est_value()`` 会把收盘价当净值、跳过行情价那一档。

用法::

    python -B dbtools\\seed_china_future_history.py                 # 预演
    python -B dbtools\\seed_china_future_history.py --apply         # 真写
    python -B dbtools\\seed_china_future_history.py --apply --days 800
"""

from __future__ import annotations

import io
import os
import sys

DBTOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.dirname(DBTOOLS_DIR)
AUTO_DIR = os.path.join(PYTHON_DIR, "auto")
for _path in (AUTO_DIR, PYTHON_DIR, DBTOOLS_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

#: ``seed_index_history`` 会在模块顶层包一层 ``TextIOWrapper``，把本层的引用
#: 顶掉后本层被 GC，``__del__`` 会关掉共用的底层 buffer —— 后面所有 print
#: 都变成 ``ValueError: I/O operation on closed file``。import 期间先把
#: ``sys.stdout`` 换成没有 ``buffer`` 的对象，让它们跳过包装（同
#: ``seed_a_share_history.py`` 的写法）。
_REAL_STDOUT = sys.stdout
sys.stdout = io.StringIO()

from core.stockdata import MySqlFundDataSource  # noqa: E402
from updater import asharehistory  # noqa: E402
import seed_index_history as seed_history  # noqa: E402
import seed_p0_symbols as seed_symbols  # noqa: E402

sys.stdout = (io.TextIOWrapper(_REAL_STDOUT.buffer, encoding="utf-8", errors="replace")
              if hasattr(_REAL_STDOUT, "buffer") else _REAL_STDOUT)

#: 默认补的三个品种，与 ``ChinaFutureGetSymbolArray()`` 里跟踪的期货一一对应。
DEFAULT_SYMBOLS = ("nf_AU0", "nf_AG0", "nf_M0")

#: 默认取最近多少个交易日。生产端「历史价格」段要 20 周均线与周布林，
#: 一年半（≈360 个交易日）足够；给 800 是为了与 ``seed_index_history`` 的
#: 620 天均线窗口口径保持一致，免得日后接均线段又要重跑一次。
DEFAULT_DAYS = 800


def _arg(name: str):
    if name not in sys.argv:
        return None
    index = sys.argv.index(name)
    if index + 1 >= len(sys.argv):
        return None
    return sys.argv[index + 1]


def _arg_int(name: str, fallback: int) -> int:
    raw = _arg(name)
    if raw is None:
        return fallback
    try:
        return int(raw)
    except ValueError:
        return fallback


def main() -> int:
    apply = "--apply" in sys.argv
    skip_backup = "--skip-backup" in sys.argv
    days = _arg_int("--days", DEFAULT_DAYS)
    raw_symbols = _arg("--symbols")
    symbols = tuple(
        part.strip() for part in raw_symbols.split(",") if part.strip()
    ) if raw_symbols else DEFAULT_SYMBOLS

    source = MySqlFundDataSource.from_env()
    connection = source.connection
    cursor = connection.cursor()

    if apply and not skip_backup:
        from core.stockdata import load_mysql_env, mysql_config_from_env
        config = mysql_config_from_env(load_mysql_env(os.environ))
        path = seed_symbols.backup_database(config)
        print("备份完成：%s" % path)
        print("")
    elif apply:
        print("**--skip-backup：本次不备份，出错只能手工恢复**")
        print("")

    print("要补的期货：%s" % ", ".join(symbols))
    print("每个最多取最近 %d 个交易日" % days)
    print("")

    for symbol in symbols:
        print("=" * 74)
        print(symbol)
        print("=" * 74)
        stock_id = source.get_stock_id(symbol)
        if stock_id is None:
            print("  **stock 表里没有这个符号，跳过**")
            continue

        rows = asharehistory.fetch_cn_future_history(symbol, limit=days)
        if not rows:
            print("  **抓不到历史，跳过**")
            continue
        print("  抓到 %d 条：%s ~ %s" % (len(rows), rows[0]["date"], rows[-1]["date"]))

        cursor.execute(
            "SELECT COUNT(*) AS n FROM stockhistory WHERE stock_id = %s", (stock_id,))
        print("  库里原有 %d 行" % cursor.fetchone()["n"])

        if not apply:
            print("  （预演）会写入 %d 行" % len(rows))
            continue

        written = seed_history._write_history(cursor, stock_id, rows)
        connection.commit()
        print("  写入 %d 行" % written)

    cursor.close()
    connection.close()
    print("")
    print("完成。" if apply else "这是预演。真正执行请加 --apply。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
