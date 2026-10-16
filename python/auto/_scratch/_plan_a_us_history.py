"""Plan A 一次性数据修复第 1 步：补 14 个美股符号的日线（补 10-14 缺口）。

## 为什么

基金层校准与官方 EST 的取数链路上，``est_quote_ref``（校准日那天的中间层值）
要按**校准日**取数。今天校准日 = 10-14（基金最新净值日），而 14 个美股符号的
``stockhistory`` 在 10-14 **一行都没有**（daemon 只写快照不写日线；
``quotecli --archive-snapshot`` 只归档"今天"），于是 ``_est_quote_ref`` 只能退到
10-13 或干脆按比例 1.0 处理 —— 估值因此漏掉/重复计入一天的涨跌。

## 数据源

新浪美股日线（``updater/indexhistory.py``，JSONP 裸数据），与
``dbtools/seed_index_history.py`` 同源。指数走 ``INDEX_CODES`` 的点号代码
（``^DJI`` -> ``.DJI``），ETF 原样。

## 只补近 30 个交易日

不学 ``seed_index_history`` 那样取 800 天：本次只是补 10-14 的缺口，
取 30 天有足够冗余。**不算 EMA** —— ETF 本来就没有 stockema 行
（实测 SPY/QQQ/AGG/XBI 全为 0 行），用 30 天窗口算 EMA 反而是错的种子；
要补 EMA 应单独跑全窗口（见 README 的后续项）。

用法::

    python -B auto/_scratch/_plan_a_us_history.py            # 预演
    python -B auto/_scratch/_plan_a_us_history.py --apply    # 真写
"""

from __future__ import annotations

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
from updater import indexhistory  # noqa: E402

sys.stdout = (io.TextIOWrapper(_REAL_STDOUT.buffer, encoding="utf-8", errors="replace")
              if hasattr(_REAL_STDOUT, "buffer") else _REAL_STDOUT)

#: 33 只基金解析出来的 14 个中间层取数符号（含 ^DJI —— SH513400 的原始 est）。
SYMBOLS = ("AGG", "IBB", "IEO", "IXC", "KWEB", "QQQ", "RSPH", "SPY",
           "VGT", "VNQ", "XBI", "XLY", "XOP", "^DJI")

#: 取最近多少个交易日（补 10-14 缺口，30 天足够）。
DAYS = 30


def main() -> int:
    apply = "--apply" in sys.argv

    source = MySqlFundDataSource.from_env()
    connection = source.connection
    cursor = connection.cursor()

    total_new = 0
    for symbol in SYMBOLS:
        print("=" * 74)
        print(symbol)
        print("=" * 74)
        stock_id = source.get_stock_id(symbol)
        if stock_id is None:
            print("  **stock 表里没有这个符号，跳过**")
            continue

        rows = indexhistory.fetch_daily_history(symbol, limit=DAYS)
        if not rows:
            print("  **抓不到日线，跳过**")
            continue
        print("  抓到 %d 条：%s ~ %s" % (len(rows), rows[0]["date"], rows[-1]["date"]))

        cursor.execute(
            "SELECT date FROM stockhistory WHERE stock_id = %s", (stock_id,))
        existing = {str(row["date"]) for row in cursor.fetchall()}
        planned = [row for row in rows if str(row["date"]) not in existing]
        print("  库里原有 %d 行，会补 %d 行" % (len(existing), len(planned)))
        for row in planned:
            print("     + %s  close=%s" % (row["date"], row["close"]))

        if not apply or not planned:
            continue

        # 与 seed_index_history._write_history 同一句：upsert，adjclose 取 close
        # （指数/ETF 日线按复权价发布，接口也没有复权列 —— 已知偏差，见该脚本注释）。
        for row in planned:
            cursor.execute(
                "INSERT INTO stockhistory (stock_id, date, close, volume, adjclose) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE close = VALUES(close), volume = VALUES(volume), "
                "adjclose = VALUES(adjclose)",
                (stock_id, row["date"], row["close"], row.get("volume", 0), row["close"]),
            )
        connection.commit()
        total_new += len(planned)
        print("  写入 %d 行" % len(planned))

    cursor.close()
    connection.close()
    print("")
    if apply:
        print("完成，共补 %d 行日线。" % total_new)
    else:
        print("这是预演。真正执行请加 --apply。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
