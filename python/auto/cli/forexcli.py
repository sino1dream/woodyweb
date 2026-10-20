"""汇率抓取 CLI。

对应 PHP ``php/stock/chinamoney.php`` 的 ``GetChinaMoney()``（抓中间价写库）。

用法：

    cd F:\\woody\\woodyweb\\python

    python -B auto\\cli\\forexcli.py --check          # 只看各源能取到什么，不写库
    python -B auto\\cli\\forexcli.py                  # 抓取并写入 netvaluehistory
                                                    # （同时更新参考数据表的
                                                    #  USDCNH/USDCNY 实时快照）
    python -B auto\\cli\\forexcli.py --api            # 打印汇率源接口说明（换源参考）
    python -B auto\\cli\\forexcli.py --list-sources   # 列出已注册的汇率源
    python -B auto\\cli\\forexcli.py --compare        # 只做双源对比，不写库
    python -B auto\\cli\\forexcli.py --fx-only        # 只更新 USDCNH/USDCNY 实时快照

环境变量：

* ``PALMMICRO_FOREX_SOURCE``      汇率源名字（默认 ``crosscheck``）
* ``PALMMICRO_FOREX_TOLERANCE``   相对容差（默认 0.01，即 1%）

两类数据别混：``netvaluehistory`` 里是 USCNY 等**中间价历史**（货币网源，
``CnyReference`` 读它）；参考数据表里的 USDCNH/USDCNY 是**实时行情快照**
（新浪源，落 ``realtimequote``）—— 后者曾经没有更新链路，快照会停在
很久以前（见 ``forexupdater.update_page_forex`` 的说明）。
"""

from __future__ import annotations

import argparse
import io
import json
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

from source import forexsource  # noqa: E402
from updater.forexupdater import (  # noqa: E402
    PAGE_FOREX_SYMBOLS,
    ForexUpdater,
    build_default_updater,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="汇率抓取")
    parser.add_argument("--check", action="store_true", help="只抓取并打印，不写数据库")
    parser.add_argument("--source", help="汇率源名字，默认取环境变量或 crosscheck")
    parser.add_argument("--api", action="store_true", help="打印汇率源接口说明")
    parser.add_argument("--list-sources", action="store_true", help="列出已注册的汇率源")
    parser.add_argument("--compare", action="store_true", help="只做双源对比，不写库")
    parser.add_argument("--backfill", action="store_true",
                        help="尝试回补历史汇率（上游不稳定，尽力而为）")
    parser.add_argument("--fx-only", action="store_true",
                        help="只更新参考数据表的 USDCNH/USDCNY 实时快照，不写中间价")
    parser.add_argument("--fill-official-dates", action="store_true",
                        help="给基金官方日期补汇率（用相邻交易日真实值）")
    parser.add_argument("--purge-fake", action="store_true",
                        help="清掉种子留下的手工占位汇率（不是真实数据）")
    return parser.parse_args()


def print_api() -> int:
    """打印"换汇率源"需要的接口说明。"""
    print("汇率数据源接口（换源只改这里，估值链路不用动）")
    print("=" * 72)
    print("""
1) 抽象基类：python/auto/forexsource.py 的 ForexSource
   必须实现：
       name                     : str，数据源标识
       fetch_rate(symbol)       : {symbol,date,rate[,...]}，取不到返回 None
   可选覆写：
       fetch_all(symbols)       : 批量取（默认逐个调 fetch_rate）
       describe()               : 诊断信息

2) 入参与返回约定：
       * symbol 用**库里符号**：USCNY / HKCNY / EUCNY / JPCNY
         （与 PHP CnyReference 一致，也与 _get_default_cny_est() 一致）；
       * rate 是"1 单位外币 = 多少人民币"；
       * 中国货币网的 100JPY/CNY 要除以 100（已在实现里处理）。

3) 注册与切换：
       from source.forexsource import register_forex_source, get_forex_source
       register_forex_source("mysource", lambda **kw: MySource(**kw))
       # 然后设置 PALMMICRO_FOREX_SOURCE=mysource

4) 已注册的源：
       crosscheck   **默认**：中国货币网（中间价）为主 + 新浪（即期）对照 + 差异告警
       sina         新浪外汇实时价（fx_susdcny 等）
       chinamoney   中国货币网中间价（PHP chinamoney.php 原本用的源）
       fixed        固定值，仅测试/离线用

5) 汇率为什么要进库：
       fundcalc.calc_with_calibration() 里
           est_f = (1-p) * nv + p * est_value * cny / cal
       这个 cny 就是汇率。库里没有 USCNY 时它会退化成 1.0，
       等于把美元当人民币用，QDII 估值会差 7 倍左右。
""")
    print("当前已注册：%s" % ", ".join(forexsource.list_forex_sources()))
    print("默认汇率源：%s" % forexsource.DEFAULT_FOREX_SOURCE)
    print("当前实例自述：%s" % json.dumps(forexsource.get_forex_source().describe(), ensure_ascii=False))
    return 0


def show_check(source_name: str = None) -> int:
    """打印每个汇率源能取到什么（不写库）。"""
    print("%-12s %-8s %-12s %-16s %s" % ("源", "符号", "日期", "汇率", "名称"))
    for name in forexsource.list_forex_sources():
        if name == "fixed":
            continue
        if source_name and name != source_name:
            continue
        source = forexsource.get_forex_source(name)
        for record in source.fetch_all():
            print("%-12s %-8s %-12s %-16s %s" % (
                name, record["symbol"], record.get("date"), record.get("rate"),
                record.get("name") or ""))
    # 参考数据表用的两个实时符号（只走新浪，别的源没有离岸/在岸实时代码）
    sina = forexsource.SinaForexSource()
    for symbol in PAGE_FOREX_SYMBOLS:
        record = sina.fetch_rate(symbol)
        if record:
            print("%-12s %-8s %-12s %-16s %s" % (
                "sina(rt)", symbol, record.get("date"), record.get("rate"),
                record.get("name") or ""))
    return 0


def show_compare(source_name: str = None) -> int:
    """只做双源对比，不写库。"""
    source = forexsource.get_forex_source(source_name)
    print("汇率源：%s" % json.dumps(source.describe(), ensure_ascii=False))
    print("")
    print("%-8s %-14s %-14s %-12s %s" % ("符号", "主源汇率", "对照源汇率", "相对差", "结论"))
    for symbol in forexsource.FOREX_SYMBOLS:
        source.fetch_rate(symbol)
        comparison = source.last_comparison or {}
        primary = (comparison.get("primary") or {}).get("rate")
        secondary = (comparison.get("secondary") or {}).get("rate")
        diff = comparison.get("diff")
        print("%-8s %-14s %-14s %-12s %s" % (
            symbol,
            primary if primary is not None else "-",
            secondary if secondary is not None else "-",
            "%.4f%%" % (diff * 100.0) if diff is not None else "-",
            comparison.get("reason") or "-",
        ))
    return 0


def main() -> int:
    args = parse_args()

    if args.api:
        return print_api()
    if args.list_sources:
        print("已注册的汇率源：%s" % ", ".join(forexsource.list_forex_sources()))
        return 0
    if args.compare:
        return show_compare(args.source)
    if args.check:
        return show_check(args.source)

    source = forexsource.get_forex_source(args.source)
    print("汇率源：%s" % json.dumps(source.describe(), ensure_ascii=False))

    updater = build_default_updater(source=source)

    if args.fx_only:
        written = updater.update_page_forex(apply=True)
        print("外汇实时快照：写入 %d 条（USDCNH/USDCNY）。" % written)
        if updater.last_error:
            print("注意：%s" % updater.last_error)
        return 0

    if args.backfill:
        written = updater.backfill_history(apply=True)
        print("历史回补：写入 %d 条。" % written)
        if updater.last_error:
            print("说明：%s" % updater.last_error)
        return 0

    if args.purge_fake:
        removed = updater.purge_fake_rates(apply=True)
        if not removed:
            print("没有发现手工占位的汇率记录。")
        else:
            print("已清掉 %d 条占位汇率：" % len(removed))
            for item in removed:
                print("   %-8s %s  %s" % (item["symbol"], item["date"], item["rate"]))
        return 0

    if args.fill_official_dates:
        updated = updater.update(apply=True)
        print("已写入 %d 条当日汇率。" % updated)
        fills = updater.fill_official_dates(apply=True)
        if not fills:
            print("官方日期无需补汇率（要么已有、要么差距过大）。")
        else:
            print("给 %d 个官方日期补了汇率：" % len(fills))
            print("%-14s %-14s %-12s %s" % ("官方日期", "取自", "汇率", "相差天数"))
            for item in fills:
                print("%-14s %-14s %-12s %s" % (
                    item["date"], item["source_date"], item["rate"], item["gap_days"]))
        return 0

    updated = updater.update(apply=True)
    print("已写入 %d 条汇率。" % updated)
    if updater.last_error:
        print("注意：%s" % updater.last_error)

    # 参考数据表的 USDCNH/USDCNY 走的是实时快照（另一条链路，见
    # forexupdater.update_page_forex 的说明），这里顺手一起更新
    fx_written = updater.update_page_forex(apply=True)
    print("已写入 %d 条外汇实时快照（USDCNH/USDCNY）。" % fx_written)
    if updater.last_error:
        print("注意：%s" % updater.last_error)

    for comparison in updater.comparisons:
        if comparison.get("match") is False:
            print("汇率校验：%s 差异超容差（%s）" % (
                comparison.get("symbol"), comparison.get("reason")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
