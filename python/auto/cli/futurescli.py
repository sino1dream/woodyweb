"""外盘期货命令行工具（实时 EST 的数据源与校准）。

对应 PHP 里这三件事，它们原本散在页面访问路径上：

1. ``LoadSinaFutureData()`` —— 抓期货行情（``php/stock/stockref.php:447``）；
2. ``AverageCalibraion()`` —— 期货与指数**时刻对齐**时写校准值
   （``php/stock/fundpairref.php:174``）；
3. ``EstRealtimeNetValue()`` —— 用期货价算实时 EST
   （``php/stock/qdiiref.php:161``）。

用法：

    cd F:\\woody\\woodyweb\\python

    python -B auto\\futurescli.py                  # 抓期货 + 落快照 + 尝试校准
    python -B auto\\futurescli.py --show           # 看当前期货行情与校准状态
    python -B auto\\futurescli.py --est            # 三个 EST（含期货来源的实时 EST）
    python -B auto\\futurescli.py --api            # 口径说明
"""

from __future__ import annotations

import argparse
import datetime
import io
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# cli/ 是 python/auto 的直接子目录，所以 auto 根目录要再往上一层。
AUTO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON_DIR = os.path.dirname(AUTO_DIR)
for _path in (AUTO_DIR, PYTHON_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from calc.fundestcalc import build_estimate_calculator  # noqa: E402
from calc.futurescalib import FALLBACK_FUTURES, build_futures_calibrator, check_adjust_time  # noqa: E402
from source.futuresquotes import FUTURES_NAMES, SinaFuturesClient  # noqa: E402
from sql.historytable import CalibrationSql  # noqa: E402
from core.stockdata import MySqlFundDataSource  # noqa: E402


def _source():
    return MySqlFundDataSource.from_env()


def _future_symbols(source) -> list:
    """要抓的期货符号：``futurespair`` 里的 + 内置兜底里的。"""
    symbols = set(FALLBACK_FUTURES.values())
    connection = getattr(source, "connection", None)
    if connection is not None:
        cursor = connection.cursor()
        try:
            cursor.execute(
                "SELECT u.symbol FROM futurespair fp JOIN stock u ON u.id = fp.stock_id"
            )
            for row in cursor.fetchall():
                symbols.add(str(row["symbol"]))
        except Exception:
            pass
        finally:
            cursor.close()
    return sorted(symbol for symbol in symbols if source.get_stock_id(symbol) is not None)


def _store_futures_quote(source, quote) -> bool:
    """把期货行情写进 ``realtimequote`` 快照（后台抓、Web 读）。"""
    stock_id = source.get_stock_id(quote.symbol)
    if stock_id is None:
        return False
    connection = getattr(source, "connection", None)
    if connection is None:
        return False

    from sql.quotestore import RealtimeQuoteStore

    store = RealtimeQuoteStore(connection)
    try:
        store.save(
            stock_id, quote.symbol, quote.sina_symbol,
            quote.price, quote.prev_price, quote.name,
            quote.date, quote.time, "sina",
            datetime.datetime.now().timestamp(),
        )
    except Exception as exc:
        print("  写快照失败 %s：%s: %s" % (quote.symbol, type(exc).__name__, exc))
        return False
    return True


def cmd_fetch(args) -> int:
    """抓期货 → 落快照 → 尝试校准。"""
    source = _source()
    client = SinaFuturesClient()
    calibrator = build_futures_calibrator(source)

    symbols = _future_symbols(source)
    if not symbols:
        print("没有可抓的期货符号（先跑 python -B sql\\_seed_futures_pair.py --apply）。")
        source.connection.close()
        return 0

    print("抓取期货：%s" % ", ".join(symbols))
    quotes = client.fetch(symbols)
    if not quotes:
        print("没抓到任何期货行情。")
        source.connection.close()
        return 1

    print("")
    print("%-8s %-13s %-13s %-12s %-10s %s" % ("符号", "最新价", "昨收", "日期", "时间", "名称"))
    print("-" * 82)
    stored = 0
    for symbol in sorted(quotes):
        quote = quotes[symbol]
        print("%-8s %-13s %-13s %-12s %-10s %s" % (
            symbol, quote.price, quote.prev_price, quote.date, quote.time, quote.name))
        if _store_futures_quote(source, quote):
            stored += 1
    print("")
    print("已落快照 %d 条" % stored)

    # 尝试校准：需要**指数**的行情时刻与期货一致
    print("")
    print("=== 尝试校准（需要期货与指数时刻对齐）===")
    for index_symbol, future_symbol in sorted(FALLBACK_FUTURES.items()):
        quote = quotes.get(future_symbol)
        if quote is None:
            continue
        index_id = source.get_stock_id(index_symbol)
        if index_id is None:
            print("  %-8s 跳过（stock 表里没有）" % index_symbol)
            continue
        index_quote = _stored_quote(source, index_symbol)
        if index_quote is None:
            print("  %-8s 跳过（库里没有 %s 的行情快照）" % (index_symbol, index_symbol))
            continue
        aligned = check_adjust_time(
            quote.date, quote.time,
            getattr(index_quote, "date", None), getattr(index_quote, "time", None),
        )
        print("  %-8s 期货 %s %s vs 指数 %s %s -> %s" % (
            index_symbol, quote.date, quote.time,
            getattr(index_quote, "date", None), getattr(index_quote, "time", None),
            "时刻对齐，可校准" if aligned else "时刻不一致，跳过"))
        if aligned and args.apply:
            factor = calibrator.calibrate(index_symbol, quote, index_quote, None, apply=False)
            print("      校准值（未写库，供核对）= %s" % factor)

    source.connection.close()
    return 0


def _stored_quote(source, symbol):
    """读 ``realtimequote`` 里的快照，返回带 ``date``/``time`` 的对象。"""
    connection = getattr(source, "connection", None)
    if connection is None:
        return None
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT price, quote_date, quote_time FROM realtimequote WHERE symbol = %s LIMIT 1",
            (symbol,),
        )
        row = cursor.fetchone()
    except Exception:
        return None
    finally:
        cursor.close()
    if not row:
        return None

    class _Quote:
        pass

    quote = _Quote()
    quote.price = row["price"]
    quote.date = row["quote_date"]
    quote.time = row["quote_time"]
    return quote


def cmd_show(args) -> int:
    """看期货行情与校准状态。"""
    source = _source()
    print("%-8s %-10s %-13s %-12s %-10s %s" % ("期货", "指数", "最新价", "日期", "时间", "名称"))
    print("-" * 82)
    for index_symbol, future_symbol in sorted(FALLBACK_FUTURES.items()):
        quote = _stored_quote(source, future_symbol)
        name = FUTURES_NAMES.get(future_symbol, "")
        if quote is None:
            print("%-8s %-10s %-13s %-12s %-10s %s" % (
                future_symbol, index_symbol, "-", "-", "-", name))
        else:
            print("%-8s %-10s %-13s %-12s %-10s %s" % (
                future_symbol, index_symbol, quote.price, quote.date,
                str(quote.time)[:8], name))

    print("")
    print("=== 指数校正状态 ===")
    table = CalibrationSql(source.connection)
    for index_symbol in sorted(FALLBACK_FUTURES):
        index_id = source.get_stock_id(index_symbol)
        if index_id is None:
            print("  %-8s 不在 stock 表里" % index_symbol)
            continue
        record = table.get_latest(index_id) if hasattr(table, "get_latest") else None
        if record is None:
            # DailyCloseSql 没有 get_latest，用 get_all 取一条
            rows = table.get_all(index_id, 0, 1)
            record = rows[0] if rows else None
        if record:
            print("  %-8s 校准值=%s 日期=%s" % (index_symbol, record["close"], record["date"]))
        else:
            print("  %-8s 无校准记录（期货与指数时刻对齐后才会写）" % index_symbol)
    source.connection.close()
    return 0


def cmd_est(args) -> int:
    """三个 EST，并标出实时 EST 用的是期货还是快照。"""
    source = _source()
    client = SinaFuturesClient()
    calculator = build_estimate_calculator(source)
    # 注入期货客户端：实时 EST 会优先用配对期货的实时价
    calculator.futures_client = client

    symbols = [args.symbol] if args.symbol else None
    if symbols is None:
        from calc.fundcalc import qdii_est_symbol_map

        symbols = [s for s in sorted(qdii_est_symbol_map()) if source.get_stock_id(s) is not None]

    print("%-10s %-11s %-11s %-11s %s" % ("符号", "官方EST", "参考EST", "实时EST", "实时 EST 来源"))
    print("-" * 78)
    for symbol in symbols:
        official = calculator.compute_official(symbol)
        fair = calculator.compute_fair(symbol)
        realtime = calculator.compute_realtime(symbol)
        origin = _realtime_origin(calculator, symbol, official, realtime)
        print("%-10s %-11s %-11s %-11s %s" % (
            symbol,
            "-" if official is None else "%.6f" % official.value,
            "-" if fair is None else "%.6f" % fair.value,
            "-" if realtime is None else "%.6f" % realtime.value,
            origin))
    source.connection.close()
    return 0


def _realtime_origin(calculator, symbol, official, realtime) -> str:
    """判断实时 EST 的值是哪儿来的（期货 / 快照 / 与官方相同）。"""
    if realtime is None:
        return "无"
    ctx = calculator._resolve(symbol)
    if ctx is None:
        return "无"
    index_symbol = calculator._index_symbol(ctx)
    future_symbol = (calculator.futures_calibrator.future_symbol_of(index_symbol)
                     if calculator.futures_calibrator and index_symbol else None)
    quote = calculator._futures_realtime_quote(ctx) if future_symbol else None
    if quote is not None:
        return "期货 %s" % future_symbol
    if ctx.get("realtime_quote") is not None:
        return "中间层快照"
    return "无实时数据"


def cmd_api(args) -> int:
    print("""外盘期货与实时 EST 口径说明
============================

## 数据源

新浪外盘期货，**裸符号**取数（`hf_NQ`），不是 `gb_hf_nq`：

    GET https://hq.sinajs.cn/list=hf_NQ
        var hq_str_hf_NQ="31365.390,,31349.500,31350.000,31521.000,31143.000,
                          23:44:39,31483.250,31510.000,0,3,30,2026-10-07,纳斯达克100指数期货,0";

字段映射与 PHP `_onSinaFuture($ar)` 完全一致（实测 15 个字段）：
    [0] 最新价  [4] 最高  [5] 最低  [6] 时间  [7] 昨收  [8] 开盘
    [12] 日期   [13] 名称

**注意**：`StockSymbol('hf_NQ').get_sina_symbol()` 会返回 `gb_hf_nq`（错的）。
PHP 的 `GetSinaSymbol()` 有同样的兜底，所以它的期货取数走的是
`DebugGetSinaFileName($strSymbol)` —— 文件名就是裸符号。

## 期货与指数的配对

    ^NDX  -> hf_NQ    纳斯达克100 → NQ 期货
    ^GSPC -> hf_ES    标普500    → ES 期货

存在 `futurespair` 表（`id`=指数，`stock_id`=期货 —— 与 PHP `PairSql` 的列语义一致）。
表里没有时退回 `futurescalib.FALLBACK_FUTURES`，避免少一行配置就静默失效。

## 校准：只在"时刻对齐"时做

对应 `CheckAdjustFactorTime()`（`php/stock/stockref.php:235`）：

    if ($strDate != $etf_ref->GetDate())            return false;
    if (GetHM($strTime) != $etf_ref->GetTimeHM())   return false;

只有两个市场**同时收着盘**的那一瞬间，`期货价 / 指数` 才是干净的比值。
校准值 = 期货价 × 汇率 / 指数价（`CalcFactor()`），
写入用**平均**（`WriteDailyAverage()`），同一天多次校准不会互相覆盖。

## 实时 EST

`实时 EST = 参考 EST + 期货`（作者原话）。
实现上等价于把中间层的取值换成 `期货价 / 校准值`：

    est_f = (nv_f × cal_f / cny_ref) × (期货价 / 校准值 / est_quote_ref) × (cny / cny_ref)

没有期货配对 / 没有期货行情 / 没有校准值时，回落到中间层的实时快照价。""")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="外盘期货：抓取 / 校准 / 实时 EST")
    parser.add_argument("--show", action="store_true", help="看期货行情与校准状态")
    parser.add_argument("--est", action="store_true", help="三个 EST（实时 EST 用期货）")
    parser.add_argument("--symbol", help="只处理这一只基金（配合 --est）")
    parser.add_argument("--apply", action="store_true", help="把校准值写库（默认只核对）")
    parser.add_argument("--api", action="store_true", help="口径说明")
    args = parser.parse_args(argv)

    if args.api:
        return cmd_api(args)
    if args.show:
        return cmd_show(args)
    if args.est:
        return cmd_est(args)
    return cmd_fetch(args)


if __name__ == "__main__":
    sys.exit(main())
