"""给演示库补 ``netvaluehistory`` 的历史段（东方财富净值接口）。

## 为什么需要

``navcli`` 只写**最新一条**（"今天"的净值），没有回填能力。于是演示库里
QDII 基金的净值表是断的 —— 实测 SZ161125 只有 09-29 / 09-30 / 10-01 /
10-08 / 10-12 五行，缺 **10-09 / 10-13 / 10-14**。

断在哪一天，「基金溢价记录」（``fundhistoryparagraph``）就少哪一行 ——
那段是拿**价格日的前一个交易日**去查净值，净值没有就整行跳过
（``if ($strNetValue = $net_sql->GetClose(...))``）。生产端
``sz161125cn.php`` 那张表有七行，本地只有四行，差的就是这几天。

## 数据源与可信度

``source.navsource.EastmoneyNavSource``（``api.fund.eastmoney.com/f10/lsjz``），
与 ``navcli`` 的默认源**同一个**。实测与生产端逐格一致::

    10-09=3.1843  10-12=3.1817  10-13=3.1803  10-14=3.1448

## 只补不覆盖

已存在的**（日期, 净值）一律不动** —— 库里那些行可能是当时手工核准过的，
覆盖掉就再也回不来了。只写库里**没有那一天**的行。

用法::

    python -B dbtools\\backfill_nav_history.py                     # 预演
    python -B dbtools\\backfill_nav_history.py --apply             # 备份 + 真写
    python -B dbtools\\backfill_nav_history.py --symbols SZ161125 --days 40
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

#: 同 ``seed_a_share_history.py``：import 期间把 stdout 换成没有 ``buffer``
#: 的对象，免得被 import 的脚本再包一层把底层 buffer 关掉。
_REAL_STDOUT = sys.stdout
sys.stdout = io.StringIO()

from core.stockdata import MySqlFundDataSource  # noqa: E402
from source.navsource import EastmoneyNavSource  # noqa: E402
import seed_p0_symbols as seed_symbols  # noqa: E402

sys.stdout = (io.TextIOWrapper(_REAL_STDOUT.buffer, encoding="utf-8", errors="replace")
              if hasattr(_REAL_STDOUT, "buffer") else _REAL_STDOUT)

#: 默认补的基金。CK6 要对齐的是 ``/fund/SZ161125`` 那一页。
DEFAULT_SYMBOLS = ("SZ161125",)

#: 默认往回取多少条净值记录。
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


def fund_code_of(symbol: str) -> str:
    """库内符号 -> 净值接口的代码（``SZ161125`` -> ``161125``）。"""
    text = str(symbol).strip()
    for prefix in ("SZ", "SH", "BJ"):
        if text.upper().startswith(prefix):
            return text[len(prefix):]
    return text


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

    nav_source = EastmoneyNavSource()

    for symbol in symbols:
        print("=" * 74)
        print(symbol)
        print("=" * 74)
        stock_id = source.get_stock_id(symbol)
        if stock_id is None:
            print("  **stock 表里没有这个符号，跳过**")
            continue

        rows = nav_source.fetch_history(fund_code_of(symbol), limit=days)
        if not rows:
            print("  **抓不到净值历史，跳过**（东方财富接口不稳定，可重试）")
            continue
        print("  抓到 %d 条：%s ~ %s" % (len(rows), rows[-1]["date"], rows[0]["date"]))

        cursor.execute(
            "SELECT date, close FROM netvaluehistory WHERE stock_id = %s", (stock_id,))
        existing = {str(row["date"]) for row in cursor.fetchall()}

        planned = [row for row in rows if str(row["date"]) not in existing]
        print("  库里已有 %d 天，会补 %d 天" % (len(existing), len(planned)))
        for row in planned:
            print("     + %s  %s" % (row["date"], row["nav"]))

        if not apply:
            continue

        written = 0
        for row in planned:
            cursor.execute(
                "INSERT INTO netvaluehistory (stock_id, date, close) VALUES (%s, %s, %s) "
                "ON DUPLICATE KEY UPDATE close = VALUES(close)",
                (stock_id, row["date"], row["nav"]),
            )
            written += 1
        connection.commit()
        print("  写入 %d 行" % written)

    cursor.close()
    connection.close()
    print("")
    print("完成。" if apply else "这是预演。真正执行请加 --apply。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
