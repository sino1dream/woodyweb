"""把指数历史种进 ``stockhistory``（并算 EMA50/200），解开 `^NDX` 的鸡生蛋。

## 为什么需要这一步

行情 daemon 的 ``collect_symbols_from_source()`` 是**从 ``stockhistory`` 反推**
要抓哪些符号的：

.. code-block:: sql

    SELECT s.id, s.symbol FROM stock s
    INNER JOIN stockhistory h ON h.stock_id = s.id GROUP BY s.id

所以一个符号**没有历史就永远进不了抓取集合**。演示库里 ``^NDX`` 一行历史都没有，
于是它既不会被抓、也永远不会有历史 —— 而它正是 QDII 的**估值标的**，
均线表（``EchoQdiiSmaParagraph``）用的就是它。

本脚本按符号直接抓历史并落库，打破那个死循环。
之后 daemon 会自然把它纳入日常抓取（它进入集合后就能持续更新）。

## 数据来源与可信度

新浪美股日线接口（``indexhistory``）。实测 ``^NDX`` 最新一条
``2026-10-09 c=30883.15`` 与生产端 ``sz161130cn.php`` 参考数据表的
``30883.15`` **逐字一致**；``QQQ 2026-10-08 = 747.58`` 与生产端
``QQQnetvalueclosetable`` 也一致。所以这不是编出来的数，是同源公开行情。

## EMA 为什么要一起算

``StockHistory._get_EMA()`` 直接查 ``stockema50`` / ``stockema200``
（**不是**算出来的），而这两张表原来是空的 → ``GetBullBear()`` 恒为
``None``、页面上不会出现"牛市/熊市"。所以这里用标准 EMA 递推补上。

## 美股 ETF 也要靠这里补（2026-11-02 补的教训）

脚本原来只补 ``^NDX`` / ``^GSPC``，注释里写着"``QQQ``/``SPY`` 是 ETF，
本来就由 daemon 正常抓"。**那个前提在本地不成立**：行情 daemon 只写
``realtimequote`` **快照**，从不写 ``stockhistory``；ETF 的日线只有两条路
进来 —— 本脚本，或 ``quotecli --archive-snapshot``（把快照搬进历史，只能
搬"当天"那一行）。

代价是 2026-11-02 发现的一整类停摆：11 个美股 ETF（``AGG IBB IEO IXC
KWEB RSPH VGT VNQ XBI XLY XOP``）的日线全停在 10-23，而基金净值已经到
10-29。``calibrate_fund()`` 的锚点是"基金净值日"、est 只认那一天的收盘价
（没有快照兜底），于是 est 走 ETF 的 14 只基金**一只都校不出来**，页面上
``est_cal`` / ``cny_cal`` 就停在 10-23。est 走指数的基金没这个问题，因为
每日例程只归档了 ``^NDX ^GSPC`` 两个 —— 差别只是"例程覆盖了谁"。

所以 ``--symbols`` 不是锦上添花，是把 ETF 拉进同一个补数入口。

## 港股 / 日股 / 欧股（2026-11-02 加入默认清单）

``^HSI`` / ``^HSTECH`` / ``^HSCE`` 的日线**不在新浪**（港股日线接口整体下线），
``znb_NKY`` / ``znb_DAX`` / ``znb_CAC`` 的日线**也不在新浪**（全球指数那族只有
实时）。六个一起走东财 ``push2his``，``indexhistory.fetch_daily_history()``
按符号自动分派。

补它们之前，est 走这些指数的基金**一条校准记录都没有** —— ``calibrate_fund()``
锚在净值日、est 取不到收盘价就直接跳过，页面照样 200，日志也不报错。
（港股 28 只、日欧 6 只，两批是同一类缺口。）

``znb_TPX``（东证一部，只有 ``SH513800`` 在用）**补不了**：东财没有这个
指数、新浪只有实时。它的基金仍会报"没有净值/行情"，属真缺口。

用法：

    python -B dbtools\\seed_index_history.py                 # 预演（只抓不写）
    python -B dbtools\\seed_index_history.py --apply          # 真写
    python -B dbtools\\seed_index_history.py --apply --days 800
    python -B dbtools\\seed_index_history.py --symbols IEO QQQ --apply
    python -B dbtools\\seed_index_history.py --symbols IEO,QQQ --apply
    python -B dbtools\\seed_index_history.py --symbols '^HSI' '^HSTECH' --apply
"""

from __future__ import annotations

import io
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto"))

from updater import indexhistory  # noqa: E402
from core.stockdata import MySqlFundDataSource  # noqa: E402

#: 默认要补的符号。
#:
#: **八个估值标的指数**：``^NDX`` → 纳指、``^GSPC`` → 标普、``^HSI`` /
#: ``^HSTECH`` / ``^HSCE`` → 恒生 / 恒生科技 / 国企、``znb_NKY`` / ``znb_DAX``
#: / ``znb_CAC`` → 日经 225 / 德国 DAX / 法国 CAC。
#:
#: 港股三个 2026-11-02 加进来，日欧三个同一天加进来 —— 两批都是同一类缺口：
#: est 走这些指数的基金**一条校准记录都没有**（页面 200、日志不报错，
#: 属整族静默失效）。见模块 docstring 与 ``README_p0_data_layer.md`` §10.6。
#: 港股 / 日股 / 欧股走东财，美股走新浪，由
#: ``indexhistory.fetch_daily_history()`` 按符号分派。
#:
#: ``znb_TPX``（东证一部，只有 ``SH513800`` 用）**不在清单里** —— 东财没有
#: 这个指数，新浪也只有实时，补不了（见 ``indexhistory`` 的说明）。
#:
#: **``QQQ``/``SPY`` 这类 ETF 不在默认清单里、但同样需要补** —— 别被
#: 上面 docstring 那段"ETF 由 daemon 正常抓"的老说法带偏：本地 daemon 只写
#: 快照。要补就用 ``--symbols`` 显式点名，理由见模块 docstring。
TARGETS = ["^NDX", "^GSPC", "^HSI", "^HSTECH", "^HSCE",
           "znb_NKY", "znb_DAX", "znb_CAC"]

#: 默认补多少天。``MAX_QUOTES_DAYS = 620`` 是均线要的窗口，
#: 但月线要凑够 20 个月、EMA200 也要 200 天，所以给宽一点。
DEFAULT_DAYS = 800

#: EMA 周期，与 ``StockHistory._get_EMA(50/200)`` 一致。
EMA_DAYS = (50, 200)


def main() -> int:
    apply = "--apply" in sys.argv
    days = _arg_int("--days", DEFAULT_DAYS)
    # --symbols 显式点名时只补那几个（ETF 一律走这条路），否则用默认的两个指数。
    targets = _arg_list("--symbols") or TARGETS

    print("要补的符号：%s" % ", ".join(targets))
    print("每个最多取最近 %d 个交易日" % days)
    print("")

    source = MySqlFundDataSource.from_env()
    connection = source.connection
    cursor = connection.cursor()

    total_written = 0
    for symbol in targets:
        print("=" * 74)
        print(symbol)
        print("=" * 74)

        stock_id = source.get_stock_id(symbol)
        if stock_id is None:
            print("  **stock 表里没有这个符号，跳过**（先跑 seed_reference_symbols.py）")
            continue

        rows = indexhistory.fetch_daily_history(symbol, limit=days)
        if not rows:
            print("  **抓不到历史，跳过**")
            continue
        print("  抓到 %d 条：%s ~ %s" % (len(rows), rows[0]["date"], rows[-1]["date"]))

        cursor.execute(
            "SELECT COUNT(*) AS n FROM stockhistory WHERE stock_id = %s", (stock_id,))
        before = cursor.fetchone()["n"]
        print("  库里原有 %d 行" % before)

        if not apply:
            print("  （预演）会写入 %d 行，并算 EMA%s" % (len(rows), "/".join(map(str, EMA_DAYS))))
            continue

        written = _write_history(cursor, stock_id, rows)
        connection.commit()
        total_written += written
        print("  写入 %d 行" % written)

        for ema_days in EMA_DAYS:
            count = _write_ema(cursor, connection, stock_id, ema_days, rows)
            print("  EMA%d 写入 %d 行" % (ema_days, count))

    cursor.close()
    connection.close()

    print("")
    if apply:
        print("完成，共写入 %d 行历史。" % total_written)
        print("接着可以：")
        print("  python -B quotecli.py %s    # 补当日快照" % " ".join(targets))
        print("  python -B _verify_sma_on_page.py             # 看均线表出不出来")
    else:
        print("这是预演。真正执行请加 --apply。")
    return 0


def _write_history(cursor, stock_id, rows) -> int:
    """写 ``stockhistory``。

    ``adjclose`` 取 ``close``：指数/ETF 的日线本来就按复权价发布
    （项目里 ``stockhistory.adjclose`` 对指数就是这么用的），接口也没有复权列。
    """
    written = 0
    for row in rows:
        cursor.execute(
            "INSERT INTO stockhistory (stock_id, date, close, volume, adjclose) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE close = VALUES(close), volume = VALUES(volume), "
            "adjclose = VALUES(adjclose)",
            (stock_id, row["date"], row["close"], row.get("volume", 0), row["close"]),
        )
        written += 1
    return written


def _write_ema(cursor, connection, stock_id, ema_days, rows) -> int:
    """用标准递推算 EMA 并写入 ``stockema{ema_days}``。

    递推：``EMA_t = k * close_t + (1 - k) * EMA_{t-1}``，``k = 2 / (n + 1)``。
    种子取**前 ``n`` 个的简单均值**（业界常见做法；PHP 那边 EMA 是外部
    算好写库的，所以这里只需要"合理"，不需要与某个 PHP 公式逐字对齐 ——
    这点已在 README 里标注为已知偏差）。

    返回写入的行数。
    """
    if len(rows) < ema_days:
        return 0

    closes = [row["close"] for row in rows]
    dates = [row["date"] for row in rows]
    k = 2.0 / (ema_days + 1.0)

    value = sum(closes[:ema_days]) / ema_days
    table = "stockema%d" % ema_days
    written = 0
    # 种子那一天的 EMA 就是它的简单均值，一并写上
    for index in range(ema_days - 1, len(closes)):
        if index == ema_days - 1:
            value = sum(closes[:ema_days]) / ema_days
        else:
            value = k * closes[index] + (1.0 - k) * value
        cursor.execute(
            "INSERT INTO `%s` (stock_id, date, close) VALUES (%%s, %%s, %%s) "
            "ON DUPLICATE KEY UPDATE close = VALUES(close)" % table,
            (stock_id, dates[index], value),
        )
        written += 1
    connection.commit()
    return written


def _arg_list(name: str) -> list:
    """取 ``--symbols A B C`` 或 ``--symbols A,B,C`` 的符号清单。

    与 :func:`_arg_int` 同一种"土解析"，因为这个脚本一直没上 argparse。
    遇到下一个 ``--`` 开头的参数就停 —— 否则 ``--symbols IEO --apply`` 会把
    ``--apply`` 当成符号去查 stock 表。
    """
    if name not in sys.argv:
        return []
    symbols = []
    for token in sys.argv[sys.argv.index(name) + 1:]:
        if token.startswith("--"):
            break
        symbols.extend(part for part in token.split(",") if part)
    return symbols


def _arg_int(name: str, fallback: int) -> int:
    if name not in sys.argv:
        return fallback
    index = sys.argv.index(name)
    if index + 1 >= len(sys.argv):
        return fallback
    try:
        return int(sys.argv[index + 1])
    except ValueError:
        return fallback


if __name__ == "__main__":
    sys.exit(main())
