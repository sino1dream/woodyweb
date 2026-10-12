"""官方 EST 命令行工具。

对应 PHP 里"用户访问页面时顺带记录官方 EST"这件事 —— 这里做成可独立运行的
定时任务，另外提供查看与误差扫描能力（PHP 侧那些是散在页面里的）。

用法：

    cd F:\\woody\\woodyweb\\python

    python -B auto\\fundestcli.py                     # 记录一批基金的官方 EST
    python -B auto\\fundestcli.py --symbol SZ161130   # 只记录一只
    python -B auto\\fundestcli.py --list              # 看已记录了哪些
    python -B auto\\fundestcli.py --list SZ161130     # 看某只的全部记录
    python -B auto\\fundestcli.py --check             # 扫描误差（真实净值 vs 当时估值）
    python -B auto\\fundestcli.py --strict            # 启用 PHP 的 20:55 闸门
    python -B auto\\fundestcli.py --api               # 说明这份工具的口径
"""

from __future__ import annotations

import argparse
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

from calc.fundestcalc import ESTIMATE_ERROR_THRESHOLD, build_estimate_calculator  # noqa: E402
from sql.fundeststore import FundEstStore, hour_minute_of  # noqa: E402
from updater.quoteupdater import _normalize_quote_time  # noqa: E402
from core.stockdata import MySqlFundDataSource  # noqa: E402


def _time_text(value) -> str:
    """把 ``fundest.time`` 显示成 ``HH:MM:SS``。

    MySQL ``TIME`` 列经 PyMySQL 取回是 ``datetime.timedelta``
    （实测 ``timedelta(seconds=2406)``）；直接 ``%s`` 会得到 ``'0:40:06'``，
    少了小时的前导零。复用 ``quoteupdater`` 里已经解决过同类问题的函数 ——
    它同时处理 ``timedelta`` / ``time`` / ``datetime`` / 字符串四种形态。
    """
    return _normalize_quote_time(value) or "-"


def _source():
    return MySqlFundDataSource.from_env()


def cmd_record(args) -> int:
    """记录官方 EST。"""
    source = _source()
    calculator = build_estimate_calculator(source)
    symbols = [args.symbol] if args.symbol else None
    gate = 2055 if args.strict else None

    print("闸门：%s" % ("严格（早于 20:55）" if args.strict else "跳过 20:55，只要求当天真实净值未到"))

    result = calculator.record_official_all(symbols, gate_hhmm=gate)
    written = [symbol for symbol, ok in sorted(result.items()) if ok]
    skipped = [symbol for symbol, ok in sorted(result.items()) if not ok]

    print("")
    print("已写入 %d 只：" % len(written))
    for symbol in written:
        stock_id = source.get_stock_id(symbol)
        record = FundEstStore(source.connection).get_latest(stock_id)
        if record:
            print("  %-10s %s  %-12s %s" % (
                symbol, record["date"], record["close"], _time_text(record["time"])))

    if skipped:
        print("")
        print("跳过 %d 只（当天真实净值已到 / 时刻超过闸门 / 缺数据）：" % len(skipped))
        for symbol in skipped:
            print("  %-10s" % symbol)

    for warning in calculator.warnings:
        print("[告警] %s" % warning)

    source.connection.close()
    return 0


def cmd_list(args) -> int:
    """列出已记录的官方 EST。"""
    source = _source()
    store = FundEstStore(source.connection)
    symbols = [args.list] if isinstance(args.list, str) else None
    if symbols is None:
        from calc.fundcalc import qdii_est_symbol_map

        symbols = sorted(qdii_est_symbol_map())

    print("%-10s %-12s %-12s %-10s %s" % ("符号", "日期", "官方EST", "时间", "记录数"))
    print("-" * 62)
    total = 0
    for symbol in symbols:
        stock_id = source.get_stock_id(symbol)
        if stock_id is None:
            continue
        count = store.count(stock_id)
        total += count
        latest = store.get_latest(stock_id)
        if latest:
            print("%-10s %-12s %-12s %-10s %d" % (
                symbol, latest["date"], latest["close"], _time_text(latest["time"]), count))
        else:
            print("%-10s %-12s %-12s %-10s %d" % (symbol, "-", "-", "-", 0))
    print("")
    print("合计 %d 条" % total)
    source.connection.close()
    return 0


def _default_symbols(source) -> list:
    """库里有估值配置、并且在 stock 表里存在的基金。"""
    from calc.fundcalc import qdii_est_symbol_map

    return [symbol for symbol in sorted(qdii_est_symbol_map())
            if source.get_stock_id(symbol) is not None]


def _latest_price(source, symbol):
    """最新价（用于算溢价）：取库里最新一条收盘价。"""
    stock_id = source.get_stock_id(symbol)
    if stock_id is None:
        return None
    reader = getattr(source, "get_latest_history_close", None)
    if reader is None:
        return None
    try:
        value = reader(stock_id)
    except Exception:
        return None
    return value if value not in (None, "") else None


def cmd_est(args) -> int:
    """并排显示三个 EST（官方 / 参考 / 实时）。

    对应生产端「EST网页链接」表（``php/ui/fundestparagraph.php``）：

    ```text
    代码 | 官方EST | EST日期 | 溢价 | 参考EST | 溢价 | 实时EST | 溢价
    ```

    三者关系：

    * **官方 EST** —— 对应 ETF 的**净值**，用中间层日期那条汇率；
    * **参考 EST** —— **当前**汇率（与当日行情价）；
    * **实时 EST** —— **参考 EST + 实时快照**。

    参考 EST 在「汇率日期与中间层日期一致」时**按定义不显示** ——
    那时它与官方 EST 相同，没有信息量（PHP ``GetFairNetValue()`` 返回 false）。
    """
    from web.bootstrap import install_paths

    install_paths()
    from sql.quotestore import format_premium_rate, get_premium_rate

    source = _source()
    calculator = build_estimate_calculator(source)
    symbols = [args.symbol] if args.symbol else _default_symbols(source)
    if not symbols:
        print("没有可用于估算的基金。")
        source.connection.close()
        return 0

    print("%-10s %-11s %-11s %-11s %-9s %-10s %-10s %s" % (
        "符号", "官方EST", "参考EST", "实时EST", "最新价", "官方溢价", "参考溢价", "实时溢价"))
    print("-" * 92)

    for symbol in symbols:
        records = (calculator.compute_official(symbol),
                   calculator.compute_fair(symbol),
                   calculator.compute_realtime(symbol))
        price = _latest_price(source, symbol)
        values = ["-" if record is None else "%.6f" % record.value for record in records]
        premiums = [
            format_premium_rate(get_premium_rate(price, record.value))
            if (record is not None and price is not None) else "-"
            for record in records
        ]
        print("%-10s %-11s %-11s %-11s %-9s %-10s %-10s %s" % (
            symbol, values[0], values[1], values[2],
            "-" if price is None else price,
            premiums[0], premiums[1], premiums[2]))

    print("")
    print("说明：参考 EST 在「汇率日期与中间层日期一致」时不显示（与官方 EST 相同，没有信息量）。")
    for warning in calculator.warnings:
        print("[告警] %s" % warning)
    source.connection.close()
    return 0


def cmd_check(args) -> int:
    """扫描误差：真实净值 vs 当时记录的官方 EST。

    对应 PHP ``StockCompareEstResult()`` 的告警逻辑。
    """
    source = _source()
    calculator = build_estimate_calculator(source)

    rows = calculator.scan_errors()
    print("误差阈值：%.1f%%（对应 PHP ``if (abs($fPercentage) > 1.0)``）" % ESTIMATE_ERROR_THRESHOLD)
    print("")
    if not rows:
        print("没有可比对的记录。")
        source.connection.close()
        return 0

    print("%-10s %-12s %-12s %-14s %s" % ("符号", "日期", "真实净值", "当时官方EST", "误差"))
    print("-" * 70)
    for symbol, date, real, estimate, change in rows:
        flag = "  ← 超阈值" if abs(change) > ESTIMATE_ERROR_THRESHOLD else ""
        print("%-10s %-12s %-12s %-14s %+.2f%%%s" % (symbol, date, real, estimate, change, flag))

    print("")
    if calculator.warnings:
        print("告警 %d 条：" % len(calculator.warnings))
        for warning in calculator.warnings:
            print("  %s" % warning)
    else:
        print("全部在阈值内。")
    source.connection.close()
    return 0


def cmd_api(args) -> int:
    """打印口径说明，便于换数据源或排查。"""
    print("""官方 EST 口径说明
================

## 它是什么

`fundest` 表存的**不是官方净值**，而是「**官方净值出来之前**那一刻算出来的估值」。
依据 PHP `StockUpdateEstResult()`（php/stock.php:148）的注释：

    // Only update when net value is NOT ready

真实净值一旦到货，就再也不写这一天的估值 —— 这样事后拿真实净值一比，
"当时算得准不准"就一目了然。页面上「官方EST / 时间 / 误差」三列就是它。

## 三个写入条件

1. **当天真实净值还没到**（`netvaluehistory` 里没有该 (stock_id, date) 行）；
2. **中间层时刻早于 20:55**（php/stock/fundpairref.php:252）—— 用 `--strict` 启用。
   非严格模式跳过这条，因为演示环境里的行情快照可能是几天前的，
   套上 20:55 会一行都写不进去；
3. **该日期还没有记录**（有就不覆盖 —— 这一列的价值在"第一次算出来的那个数"）。

## 三个 EST 的关系

    est_f = (nv_f × cal_f / cny_ref) × (est_quote / est_quote_ref) × (cny / cny_ref)

其中 `est_quote_ref × cal_m = cny_ref`。这就是 `GetQdiiValue()` 的等价形式，
`--check` 会拿它跟真实净值比，偏差超过 1% 就告警。

## 误差阈值

对应 PHP `StockCompareEstResult()`（php/stock.php:136）：

    if (abs($fPercentage) > 1.0)    trigger_error('Net value estimation error ...');

即 |真实净值 → 当时估值| 的偏差超过 1.0% 就告警。""")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="官方 EST 的工具（记录 / 查看 / 误差扫描）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--symbol", help="只处理这一只基金")
    parser.add_argument("--list", nargs="?", const=True, default=None,
                        metavar="SYMBOL", help="列出已记录的官方 EST（可指定符号）")
    parser.add_argument("--check", action="store_true", help="扫描误差并告警")
    parser.add_argument("--est", action="store_true",
                        help="并排显示三个 EST（官方 / 参考 / 实时）")
    parser.add_argument("--strict", action="store_true",
                        help="启用 PHP 的 20:55 闸门（默认跳过）")
    parser.add_argument("--api", action="store_true", help="打印口径说明")
    args = parser.parse_args(argv)

    if args.api:
        return cmd_api(args)
    if args.est:
        return cmd_est(args)
    if args.check:
        return cmd_check(args)
    if args.list is not None:
        return cmd_list(args)
    return cmd_record(args)


if __name__ == "__main__":
    sys.exit(main())
