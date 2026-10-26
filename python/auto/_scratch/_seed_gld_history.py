"""一次性：给演示库补 GLD 的 ``stockhistory``（让穷举持仓页能真正算出比例）。

## 为什么要补

``/fund/qdiilof/SZ164701`` 是持仓型 LOF（黄金），成分股只有 ``GLD`` 一个，
正是穷举持仓页（``exhaustiveholdingscn.php``）的目标形状（持仓数 <= 4）。
但演示库的 ``stockhistory`` 里 GLD **只有 10-22 / 10-23 两天**：

* ``HoldingsReference.GetProportionArray()`` 要求每个成分股在两日之间都有
  ``adjclose``，缺一天就整行返回 ``None`` → 页面每行只剩「日期/净值/涨幅」三格，
  比例与仓位永远解不出来；
* 页面上看起来"没生效"，其实是我这边的数据缺口（与
  ``seed_index_history.py`` 补 ``^NDX``/``^GSPC`` 是同一类问题）。

## 数据来源

新浪美股日线（``updater/indexhistory``），与 ``seed_index_history.py`` 同源。
实测 GLD 的日线与 daemon 后来抓到的 10-22/10-23 两行一致（366.61 / 369.00）。

``adjclose`` 一律取 ``close``：这个脚本不做分红/拆股调整。GLD 近期没有拆股，
且演示库既有那两行的 ``adjclose`` 本来就等于 ``close``（照它对齐）。

用法（在 ``python/`` 下）::

    python -B auto/_scratch/_seed_gld_history.py            # 预演
    python -B auto/_scratch/_seed_gld_history.py --apply    # 真写
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))), ".env"))

from core.stockdata import MySqlFundDataSource  # noqa: E402
from updater import indexhistory  # noqa: E402

#: 要补的符号。GLD 是 SZ164701 的唯一成分股；顺带补 GDX（同族黄金 miner，
#: 若以后有基金持仓它就不用再跑一次）。
TARGETS = ["GLD"]

#: 60 个交易日 ≈ 3 个月，够穷举页算十几行（每行要一对反转日）。
DAYS = 60


def main():
    apply = "--apply" in sys.argv
    source = MySqlFundDataSource.from_env()
    connection = source.connection
    cursor = connection.cursor()
    written = 0
    for symbol in TARGETS:
        stock_id = source.get_stock_id(symbol)
        if stock_id is None:
            print("%s: stock 表里没有这个符号，跳过" % symbol)
            continue
        rows = indexhistory.fetch_daily_history(symbol, limit=DAYS)
        if not rows:
            print("%s: 抓不到历史，跳过" % symbol)
            continue
        cursor.execute(
            "SELECT COUNT(*) AS n FROM stockhistory WHERE stock_id = %s", (stock_id,))
        before = cursor.fetchone()["n"]
        print("%s (id=%s): 抓到 %d 条 %s ~ %s，库里原有 %d 行"
              % (symbol, stock_id, len(rows), rows[0]["date"], rows[-1]["date"], before))
        for row in rows:
            if not apply:
                continue
            cursor.execute(
                "INSERT INTO stockhistory (stock_id, date, close, volume, adjclose) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE close = VALUES(close), "
                "volume = VALUES(volume), adjclose = VALUES(adjclose)",
                (stock_id, row["date"], row["close"], row.get("volume") or 0,
                 row["close"]),
            )
            written += 1
        if apply:
            connection.commit()
            cursor.execute(
                "SELECT COUNT(*) AS n FROM stockhistory WHERE stock_id = %s", (stock_id,))
            print("    写入后 %d 行" % cursor.fetchone()["n"])
    if not apply:
        print("\n这是预演。真正执行请加 --apply。")
    else:
        print("\n写入 %d 条（含覆盖已有日期的 update）" % written)
    return 0


if __name__ == "__main__":
    sys.exit(main())
