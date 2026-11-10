# -*- coding: utf-8 -*-
"""补齐「A股指数」分类页基金的**空壳数据**，默认 ``SZ160632``（酒LOF）。

## 为什么单独有一个脚本

``patch_china_index_symbols.py`` 只补 ``stock`` 行（让页面不再显示
「库里无此符号」）。但补完符号后 ``/fund/SZ160632`` 仍是空壳：估值表
三档全是 0、参考数据行显示「无行情快照」、五档盘口提示抓取进程没跑到 ——
实测 9 张相关表在这个 ``stock_id`` 上全是 0 行。

页面读的是这四张表，缺一不可::

    netvaluehistory   官方单位净值（估值/溢价的分母）
    stockhistory      A 股日线（基金溢价记录、均线、历史表）
    stockhistorydate  该符号最新行情日期（StockHistoryDateSql）
    realtimequote     实时价 + 五档盘口（跨进程共享的唯一快照）

## 逐块的数据源与写入语义

==============  ==========================================  ================================
表              数据源                                      语义
==============  ==========================================  ================================
netvaluehistory ``EastmoneyNavSource.fetch_history()``      **只补不覆盖**，与
                （东财 ``f10/lsjz``，与 navcli 同源）      ``backfill_nav_history.py``
                                                           完全一致；``pageSize`` 上限
                                                           约 20 个交易日
stockhistory    ``updater.asharehistory``                   ``ON DUPLICATE KEY UPDATE``，
                （新浪 ``CN_MarketData.getKLineData``）     复用 ``seed_index_history.
                                                           _write_history()``，幂等
stockhistorydate 取日线里**最新的那一天**                  ``QuoteStore.write_stock_history_
                                                           date()``，对齐 quotecli 的
                                                           ``save_reference()``
realtimequote   ``updater.quoteupdater.QuotesUpdater``      与 daemon/``--once`` 同一条
                （新浪实时行情 + 五档盘口）                 写入链路：没抓到价**不写库**，
                                                           避免把上一轮的好值覆盖成空串
==============  ==========================================  ================================

## 明确不写什么（两处容易手滑的地方）

* **不写 ``fundposition``**。生产端 ``sz160632cn.php`` 的「基金指数对照表」里
  仓位是 ``0.95``，但那不是管理员存进 ``fundposition`` 的值 —— 是
  ``StockSymbol::GetDefaultPosition()`` 对 ``IsLofA()`` 的回落
  （``python/auto/core/stocksymbol.py`` 的 ``get_default_position()``，
  PHP ``php/stock/stocksymbol.php:1003``）。``README_fundposition_migration.md``
  也写明「缺行回落 0.95 / 1.0，不是手工设过的值」。往表里塞一行 0.95
  会把代码默认伪装成实测仓位。
* **不写 ``fundpair`` / ``calibrationhistory``**。生产端 ``SZ160632`` 的
  配对是 ``SZ399987``（中证酒），校准值 ``14373.3143 @ 2026-11-09``。
  但本地 A 股指数这一族（基金 → 中证指数）的配对链路尚未整体迁移，
  ``SZ399987`` 等指数连 ``stock`` 行都没有。逐只手写校准值等于把
  「没迁的功能」伪装成「已迁的数据」，所以本脚本只补基础行情数据。

## 安全约定

* 默认 dry-run，``--apply`` 才写库；
* ``--apply`` 前走 ``backup_db.py --lock-tables``（MyISAM 安全），失败即停；
* ``--skip-backup`` 只在**已有本次改动的锁表备份**时使用；
* 净值只补不覆盖、日线幂等、快照 UPSERT —— 重复跑不会越补越乱；
* ``--symbols`` 可点名多只；**默认只补 ``SZ160632``**，不擅自给分类页
  其余 26 只灌数据（它们只补了 ``stock`` 行）。

用法::

    python -B dbtools\\patch_china_index_fund_data.py                       # 预演
    python -B dbtools\\patch_china_index_fund_data.py --apply               # 备份 + 真写
    python -B dbtools\\patch_china_index_fund_data.py --apply --skip-backup
    python -B dbtools\\patch_china_index_fund_data.py --symbols SZ160632,SZ160639 --apply
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

#: 同 ``seed_a_share_history.py`` / ``backfill_nav_history.py``：被 import 的
#: 脚本会在模块顶层再包一层 ``TextIOWrapper``，把本层最后一个引用顶掉后
#: 被 GC、``__del__`` 关掉共用的底层 buffer。import 期间先换成没有 ``buffer``
#: 属性的对象，让它们跳过包装。
_REAL_STDOUT = sys.stdout
sys.stdout = io.StringIO()

from core.stockdata import (  # noqa: E402
    MySqlFundDataSource, load_mysql_env, mysql_config_from_env,
)
from core.stocksymbol import StockSymbol  # noqa: E402
from source import quotesource  # noqa: E402
from source.navsource import EastmoneyNavSource  # noqa: E402
from sql.quotestore import EstQuoteStore, QuoteStore, RealtimeQuoteStore  # noqa: E402
from updater import asharehistory  # noqa: E402
from updater.quoteupdater import QuotesUpdater, default_est_symbols  # noqa: E402
import seed_index_history as seed_history  # noqa: E402
from backfill_nav_history import fund_code_of  # noqa: E402

sys.stdout = (io.TextIOWrapper(_REAL_STDOUT.buffer, encoding="utf-8", errors="replace")
              if hasattr(_REAL_STDOUT, "buffer") else _REAL_STDOUT)

#: 用户点名的那一只：分类页 27 只里唯一有 ``stock`` 行的老符号。
DEFAULT_SYMBOLS = ("SZ160632",)

#: 东财 ``pageSize`` 实测上限约 20 条，多给几天是为了覆盖接口偶尔缺页。
DEFAULT_NAV_DAYS = 40
#: 新浪日线给 30 个交易日，够盖住生产端「基金溢价记录」的可见区间。
DEFAULT_HISTORY_DAYS = 30


def resolve_symbols(raw: str | None) -> tuple:
    """``--symbols`` 去重保序，缺省用 :data:`DEFAULT_SYMBOLS`。

    拆成纯函数是为了能被单测钉住「默认只补一只、不连带 26 只」这件事。
    """
    if not raw:
        return DEFAULT_SYMBOLS
    result = []
    for part in raw.split(","):
        symbol = part.strip()
        if symbol and symbol not in result:
            result.append(symbol)
    return tuple(result) if result else DEFAULT_SYMBOLS


def plan_nav_rows(rows, existing_dates) -> list:
    """净值「只补不覆盖」：库里已有该日期就整行跳过。

    ``existing_dates`` 是 ``{"2026-11-09", ...}`` 形式的字符串集合；
    与 ``backfill_nav_history.main()`` 的判定逐字一致。
    """
    known = {str(date) for date in existing_dates}
    return [row for row in rows if str(row["date"]) not in known]


def _arg(name: str) -> str | None:
    if name not in sys.argv:
        return None
    index = sys.argv.index(name)
    return sys.argv[index + 1] if index + 1 < len(sys.argv) else None


def _arg_int(name: str, fallback: int) -> int:
    raw = _arg(name)
    if raw is None:
        return fallback
    try:
        return int(raw)
    except ValueError:
        return fallback


def _quote_pairs(source, symbols):
    """``[(库内符号, 新浪代码, stock_id), ...]``，缺 ``stock`` 行就直接停。"""
    pairs = []
    for symbol in symbols:
        stock_id = source.get_stock_id(symbol)
        if stock_id is None:
            raise SystemExit("stock 表里没有 %s，先跑 patch_china_index_symbols.py" % symbol)
        sina_symbol = StockSymbol(symbol).get_sina_symbol()
        if not sina_symbol:
            raise SystemExit("%s 没有新浪代码，无法抓实时行情" % symbol)
        pairs.append((symbol, sina_symbol, stock_id))
    return pairs


def main() -> int:
    apply = "--apply" in sys.argv
    skip_backup = "--skip-backup" in sys.argv
    symbols = resolve_symbols(_arg("--symbols"))
    nav_days = _arg_int("--nav-days", DEFAULT_NAV_DAYS)
    history_days = _arg_int("--history-days", DEFAULT_HISTORY_DAYS)

    source = MySqlFundDataSource.from_env()
    connection = source.connection
    cursor = connection.cursor()
    pairs = _quote_pairs(source, symbols)
    nav_source = EastmoneyNavSource()

    print("目标：%s | 净值 %d 天 / 日线 %d 天 | %s"
          % (", ".join(symbols), nav_days, history_days, "写入" if apply else "预演"))
    print("")

    plans = []
    for symbol, _sina_symbol, stock_id in pairs:
        print("=" * 74)
        print("%s  (stock_id=%s)" % (symbol, stock_id))
        print("=" * 74)

        nav_rows = nav_source.fetch_history(fund_code_of(symbol), limit=nav_days)
        if nav_rows:
            cursor.execute(
                "SELECT date FROM netvaluehistory WHERE stock_id = %s", (stock_id,))
            existing = [row["date"] for row in cursor.fetchall()]
            nav_planned = plan_nav_rows(nav_rows, existing)
            print("  ① netvaluehistory    抓到 %2d 条 %s ~ %s，库里已有 %2d 天，会补 %2d 天"
                  % (len(nav_rows), nav_rows[-1]["date"], nav_rows[0]["date"],
                     len(existing), len(nav_planned)))
        else:
            nav_planned = []
            print("  ① netvaluehistory    **抓不到净值历史，跳过**（东财接口不稳定，可重试）")

        history_rows = asharehistory.fetch_daily_history(symbol, limit=history_days)
        if history_rows:
            cursor.execute(
                "SELECT COUNT(*) AS n FROM stockhistory WHERE stock_id = %s", (stock_id,))
            print("  ② stockhistory       抓到 %2d 条 %s ~ %s，库里原有 %2d 行"
                  % (len(history_rows), history_rows[0]["date"], history_rows[-1]["date"],
                     cursor.fetchone()["n"]))
        else:
            print("  ② stockhistory       **抓不到日线，跳过**")

        plans.append({
            "symbol": symbol,
            "stock_id": stock_id,
            "nav": nav_planned,
            "history": history_rows,
        })

    # 实时行情在预演阶段也要真抓一次（只读网络），让「能不能抓到、有没有盘口」
    # 在写库之前就看得见。store/est_store 传 None 就不会落库 ——
    # apply 时会在**备份之后**再抓一次，走带快照表的同一条链路。
    preview = QuotesUpdater(
        source=quotesource.get_quote_source(),
        symbols_provider=lambda: pairs,
        store=None,
        est_store=None,
        est_symbols=default_est_symbols(),
    )
    print("")
    print("=" * 74)
    print("③ realtimequote（含五档盘口）")
    print("=" * 74)
    updated = preview.update(pairs)
    if preview.last_error:
        print("  **抓取报错：%s**" % preview.last_error)
    for symbol, _sina_symbol, _stock_id in pairs:
        quote = preview.get(symbol, ttl=10 ** 9)
        if quote is None:
            print("  %-10s 没抓到行情" % symbol)
        else:
            print("  %-10s 价=%-8s 昨收=%-8s %s %s 盘口=%s"
                  % (symbol, quote.price or "-", quote.prev_price or "-",
                     quote.date or "-", quote.time or "-",
                     "有" if quote.has_order_book() else "无"))
    print("  本轮抓到 %d 条。" % updated)

    if not apply:
        print("")
        print("这是预演。真正执行请加 --apply。")
        cursor.close()
        connection.close()
        return 0

    if not skip_backup:
        import backup_db

        code = backup_db.main([])
        if code != 0:
            print("备份失败（退出码 %s），停在这里，没有动库。" % code)
            cursor.close()
            connection.close()
            return 1
        print("")
    else:
        print("")
        print("**--skip-backup：本次不备份**")
        print("")

    # ①②④：净值 / 日线 / 最新行情日期，走 source 这一个连接。
    for plan in plans:
        stock_id = plan["stock_id"]
        for row in plan["nav"]:
            cursor.execute(
                "INSERT INTO netvaluehistory (stock_id, date, close) VALUES (%s, %s, %s) "
                "ON DUPLICATE KEY UPDATE close = VALUES(close)",
                (stock_id, row["date"], row["nav"]),
            )
        if plan["history"]:
            seed_history._write_history(cursor, stock_id, plan["history"])
            QuoteStore(connection).write_stock_history_date(
                stock_id, plan["history"][-1]["date"])
        connection.commit()
        print("  %-10s 净值 +%d 行，日线 +%d 行，stockhistorydate 已对齐"
              % (plan["symbol"], len(plan["nav"]), len(plan["history"])))

    # ③：实时快照单独一个连接。QuotesUpdater._save_to_store() 只 execute
    # 不 commit（daemon 靠常驻连接），这里必须自己 commit 才落得住；
    # 单独连接还能把「快照写失败时的 rollback」隔离在这张表，不牵连上面
    # 已经提交好的净值/日线。
    import pymysql

    config = mysql_config_from_env(load_mysql_env(os.environ))
    quote_conn = pymysql.connect(
        host=config["host"], port=config["port"], user=config["user"],
        password=config["password"], database=config["database"],
        charset="utf8", cursorclass=pymysql.cursors.DictCursor,
    )
    writer = QuotesUpdater(
        source=quotesource.get_quote_source(),
        symbols_provider=lambda: pairs,
        store=RealtimeQuoteStore(quote_conn),
        est_store=EstQuoteStore(quote_conn),
        est_symbols=default_est_symbols(),
    )
    written_quotes = writer.update(pairs)
    quote_conn.commit()
    quote_conn.close()
    if writer.last_error:
        print("  **快照写入报错：%s**" % writer.last_error)
    print("  实时快照写入 %d 条。" % written_quotes)

    print("")
    print("=== 写入后核对 ===")
    for symbol, _sina_symbol, stock_id in pairs:
        cursor.execute("SELECT COUNT(*) AS n, MAX(date) AS d FROM netvaluehistory "
                       "WHERE stock_id = %s", (stock_id,))
        nav = cursor.fetchone()
        cursor.execute("SELECT COUNT(*) AS n, MAX(date) AS d FROM stockhistory "
                       "WHERE stock_id = %s", (stock_id,))
        hist = cursor.fetchone()
        cursor.execute("SELECT price, prev_price, quote_date, quote_time, orderbook "
                       "FROM realtimequote WHERE id = %s", (stock_id,))
        snap = cursor.fetchone()
        print("  %-10s 净值 %2d 行(最新 %s) | 日线 %2d 行(最新 %s) | 快照 %s"
              % (symbol, nav["n"], nav["d"], hist["n"], hist["d"],
                 ("%s @ %s %s 盘口%s" % (snap["price"], snap["quote_date"],
                                        snap["quote_time"],
                                        "有" if snap["orderbook"] else "无"))
                 if snap else "**仍缺**"))
    cursor.close()
    connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
