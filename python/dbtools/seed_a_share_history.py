"""CK6-3：给 A 股 / 场内基金补 ``stockhistory``（新浪日线）。

## 为什么需要

``seed_index_history.py`` 只补**美股与指数**（``US_MinKService``），
A 股那一侧一直没有源，于是演示库里 QDII 基金的 ``stockhistory`` 是断的 ——
实测 SZ161125 只有 09-29 / 09-30 / 10-14 三行，而生产端
``sz161125cn.php`` 的「基金溢价记录」有七行（还缺 10-08 / 10-09 / 10-12 / 10-13）。

断在哪一天，表上就少哪一行，而且「基金溢价记录」是按**价格日**逐行出的，
所以这不是渲染问题，是数据问题。

## 数据源

``updater.asharehistory``（新浪 ``CN_MarketData.getKLineData``）。
实测与生产端逐行一致：``10-08=3.294 / 10-09=3.312 / 10-12=3.337 /
10-13=3.330 / 10-14=3.340``。

## 不写什么

* **不写净值**：``netvaluehistory`` 是官方净值，有它自己的那条路
  （``navcli``），拿收盘价当净值会把折溢价抹平；
* **不算 EMA**：本页的均线用的是**估值标的**（``^GSPC``）而不是基金自己，
  基金这一侧的 EMA 用不上（``seed_index_history.py`` 才是补它的地方）。

用法::

    python -B dbtools\\seed_a_share_history.py                    # 预演
    python -B dbtools\\seed_a_share_history.py --apply            # 备份 + 真写
    python -B dbtools\\seed_a_share_history.py --apply --symbols SZ161125,SH513100
    python -B dbtools\\seed_a_share_history.py --apply --days 60
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

#: import 的 ``seed_*`` 脚本会在模块顶层再包一层 ``TextIOWrapper``，把本层的
#: 最后一个引用顶掉后本层被 GC、``__del__`` 关掉共用的底层 buffer，后面所有
#: print 全是 ``ValueError: I/O operation on closed file``。所以 import 期间
#: 先把 ``sys.stdout`` 换成没有 ``buffer`` 属性的对象，让它们跳过包装。
_REAL_STDOUT = sys.stdout
sys.stdout = io.StringIO()

from core.stockdata import MySqlFundDataSource  # noqa: E402
from updater import asharehistory  # noqa: E402
import seed_index_history as seed_history  # noqa: E402
import seed_p0_symbols as seed_symbols  # noqa: E402

sys.stdout = (io.TextIOWrapper(_REAL_STDOUT.buffer, encoding="utf-8", errors="replace")
              if hasattr(_REAL_STDOUT, "buffer") else _REAL_STDOUT)

#: 默认补的符号。CK6 要对齐的是 ``/fund/SZ161125`` 那一页。
DEFAULT_SYMBOLS = ("SZ161125",)

#: 默认取最近多少个交易日。够盖住"最近两个月"的断档，不会把整段历史重写一遍。
DEFAULT_DAYS = 40


def _arg(name: str) -> str | None:
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

    print("要补的符号：%s" % ", ".join(symbols))
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

        rows = asharehistory.fetch_daily_history(symbol, limit=days)
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
