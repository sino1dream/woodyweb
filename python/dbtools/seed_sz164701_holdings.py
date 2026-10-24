"""给演示库补 **SZ164701（黄金LOF）的持仓数据**，照抄生产端真实值。

## 为什么要补

``/fund/qdiilof/SZ164701`` 走不走 ``EchoHoldingsEstParagraph`` 那一支（估值表
上方那句「基金持仓更新于……」），判据是 ``holdings`` 表有没有这只基金的明细行
（``SqlCountHoldings() > 0``）。演示库里 SZ164701 一条都没有，所以页面恒走
QDII 分支 —— 功能实现了也看不出来。

生产端 ``https://palmmicro.com/woody/res/sz164701cn.php``
（抓取时间 2026-10-23）::

    <a href=".../fundpositioncn.php?symbol=SZ164701">仓位估算</a>值使用1.00。
    <a href=".../holdingscn.php?symbol=SZ164701">基金持仓</a>更新于2026-10-22。
    <a href=".../exhaustiveholdingscn.php?symbol=SZ164701">穷举持仓比例</a>

持仓表只有**一行**（``holdingstable`` 三行：表头 + GLD + 合计尾行）::

    GLD | 100.00 | 369 | -0.59% | 100.00 | -0.6076 | 1.0002

即：**持有 GLD 一只，比例 100%**，持仓日 2026-10-22，GLD 当日收盘 369。

## 为什么默认 dry-run

演示库是要给其它用例看的公共环境（SH513050 那 36 行持仓样本就在里面）。
写入前先打印一遍要执行的 SQL，确认无误再加 ``--apply``。本脚本会**新建
GLD 这个符号**（演示库 ``stock`` 表里没有），属于不可逆的结构性改动，
所以不自动执行。

跑法::

    python -B dbtools/seed_sz164701_holdings.py            # 只看要做什么
    python -B dbtools/seed_sz164701_holdings.py --apply    # 真的写库
"""

from __future__ import annotations

import io
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "auto"))

from core.stockdata import MySqlFundDataSource  # noqa: E402

FUND_SYMBOL = "SZ164701"
HOLDING_SYMBOL = "GLD"
HOLDING_NAME = "金价ETF"       # 生产端参考数据表里 GLD 的名称
HOLDINGS_DATE = "2026-10-22"
RATIO = 100.0                  # 生产端显示 100.00（单位是百分点，不是小数）
HOLDING_CLOSE = 369.0          # 生产端持仓测算表「旧价格」列
#: 快照行情（2026-10-24 quotecli 实测，见下面 realtimequote 那步的注释）
QUOTE_PRICE = 366.61
QUOTE_PREV = 369.0
QUOTE_DATE = "2026-10-23"
QUOTE_TIME = "16:00:00"


def _stock_id(cursor, symbol: str):
    cursor.execute("SELECT id FROM stock WHERE symbol = %s", (symbol,))
    row = cursor.fetchone()
    return None if row is None else row["id"]


def plan(source) -> list[tuple[str, tuple]]:
    """算出要执行的 SQL；返回 ``[(sql, params), ...]``。

    用 ``SELECT`` 探路而不是直接拼 INSERT：``GLD`` 可能已经存在（重跑时），
    那时应该复用它的 id 而不是再插一行。
    """
    cursor = source.connection.cursor()
    try:
        fund_id = _stock_id(cursor, FUND_SYMBOL)
        holding_id = _stock_id(cursor, HOLDING_SYMBOL)
    finally:
        cursor.close()
    if fund_id is None:
        raise SystemExit("演示库里没有 %s，先跑 seed_demo_data.py" % FUND_SYMBOL)

    steps: list[tuple[str, tuple]] = []
    if holding_id is None:
        steps.append((
            "INSERT INTO stock (symbol, name) VALUES (%s, %s)",
            (HOLDING_SYMBOL, HOLDING_NAME),
        ))
        # 新符号的 id 要插完才知道，写库时再查（见 apply）
    else:
        steps.append(("-- GLD 已存在，id=%d，跳过建符号" % holding_id, ()))

    steps.append((
        "DELETE FROM holdings WHERE stock_id = %s",
        (fund_id,),
    ))
    steps.append((
        "INSERT INTO holdings (stock_id, holding_id, ratio) VALUES (%s, %s, %s)",
        (fund_id, "@holding_id@", RATIO),
    ))
    steps.append((
        "INSERT INTO holdingsdate (id, date) VALUES (%s, %s) "
        "ON DUPLICATE KEY UPDATE date = VALUES(date)",
        (fund_id, HOLDINGS_DATE),
    ))
    # 仓位（fundposition）：生产端是管理员提交持仓时按比例和写进去的
    # （php woody/res/php/_updateholdings.php 的 UpdateStockOptionHoldings ->
    #  FundPositionSql::WritePos($fPos)，$fPos = 各比例之和 / 100）。
    # 演示库里 SZ164701 原有 0.95，与"单只成分股 100%"不符，一并刷成 1.00。
    steps.append((
        "INSERT INTO fundposition (id, close) VALUES (%s, %s) "
        "ON DUPLICATE KEY UPDATE close = VALUES(close)",
        (fund_id, RATIO / 100.0),
    ))
    steps.append((
        "INSERT INTO stockhistory (stock_id, date, close, volume, adjclose) "
        "VALUES (%s, %s, %s, 0, %s) "
        "ON DUPLICATE KEY UPDATE close = VALUES(close)",
        ("@holding_id@", HOLDINGS_DATE, HOLDING_CLOSE, HOLDING_CLOSE),
    ))
    # 行情快照（realtimequote）：GLD 不在 daemon 的符号清单里（它的集合来自
    # symbolinventory，不从 stockhistory 反推），所以快照不会自己出现 ——
    # 与 seed_sh_hedge.py 手工写 fx 快照同一个道理。没有这一行，持仓测算表
    # 「此后涨幅」列会是 -100%（price=0），与生产端的 -0.59% 对不上。
    # 值是 2026-10-24 用 quotecli 抓的真实行情（新浪 gb_gld）::
    #     python -B -m auto.cli.quotecli --check GLD
    #     GLD -> gb_gld  名称=金价ETF  价=366.61  昨收=369  日期=2026-10-23 16:00
    # quotecli 只写 stockhistory 不写快照，所以这里补一行 UPSERT。
    steps.append((
        "INSERT INTO realtimequote (id, symbol, source_symbol, price, prev_price, name, "
        "quote_date, quote_time, source, fetched_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE price = VALUES(price), prev_price = VALUES(prev_price), "
        "name = VALUES(name), quote_date = VALUES(quote_date), "
        "quote_time = VALUES(quote_time), fetched_at = VALUES(fetched_at)",
        ("@holding_id@", HOLDING_SYMBOL, "gb_gld", str(QUOTE_PRICE), str(QUOTE_PREV),
         HOLDING_NAME, QUOTE_DATE, QUOTE_TIME, "sina", "2026-10-24 15:45:00"),
    ))
    return steps


def apply(source, steps) -> None:
    cursor = source.connection.cursor()
    try:
        cursor.execute("SELECT id FROM stock WHERE symbol = %s", (HOLDING_SYMBOL,))
        row = cursor.fetchone()
        if row is None:
            cursor.execute("INSERT INTO stock (symbol, name) VALUES (%s, %s)",
                           (HOLDING_SYMBOL, HOLDING_NAME))
            cursor.execute("SELECT id FROM stock WHERE symbol = %s", (HOLDING_SYMBOL,))
            row = cursor.fetchone()
        holding_id = row["id"]
        for sql, params in steps:
            if sql.startswith("--"):
                continue
            params = tuple(holding_id if p == "@holding_id@" else p for p in params)
            cursor.execute(sql, params)
        source.connection.commit()
        print("已写入，GLD 的 stock_id = %d" % holding_id)
    except Exception:
        source.connection.rollback()
        raise
    finally:
        cursor.close()


def main() -> int:
    do_apply = "--apply" in sys.argv
    source = MySqlFundDataSource.from_env()
    steps = plan(source)

    print("目标库：%s" % source.connection.db.decode() if isinstance(source.connection.db, bytes)
          else "目标库：%s" % source.connection.db)
    print("基金：%s（持仓日 %s，持有 %s %.2f%%）"
          % (FUND_SYMBOL, HOLDINGS_DATE, HOLDING_SYMBOL, RATIO))
    print("-" * 70)
    for sql, params in steps:
        if sql.startswith("--"):
            print(sql)
        else:
            print("%s  %s" % (sql, params))
    print("-" * 70)
    if not do_apply:
        print("以上是 dry-run，没有写库。确认无误后加 --apply。")
        return 0
    apply(source, steps)
    return 0


if __name__ == "__main__":
    sys.exit(main())
