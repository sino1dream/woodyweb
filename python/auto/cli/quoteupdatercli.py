"""实时行情定时抓取脚本。

对应 PHP：``php/stock.php`` 的 ``StockPrefetchArrayData()`` +
``php/stock/stockprefetch.php`` 的间隔节流。差别是 PHP 在**每次页面请求内**同步抓，
这里做成**独立的后台定时进程**：页面只读缓存，响应快、也不容易触发频率限制。

用法：

    cd F:\\woody\\woodyweb\\python

    python -B auto\\quoteupdatercli.py --once            # 抓一轮就退出（可配合计划任务）
    python -B auto\\quoteupdatercli.py --daemon          # 常驻，按 --interval 定时抓
    python -B auto\\quoteupdatercli.py --daemon --interval 60
    python -B auto\\quoteupdatercli.py --only-open       # 只在有市场开市时才抓
    python -B auto\\quoteupdatercli.py --api             # 打印数据源接口说明（换源参考）
    python -B auto\\quoteupdatercli.py --list-sources    # 列出已注册的行情源

环境变量：

* ``PALMMICRO_QUOTE_SOURCE``          行情源名字（默认 ``sina``）
* ``PALMMICRO_QUOTE_INTERVAL``        间隔秒数（默认 60）
* ``PALMMICRO_QUOTE_REFRESH_ON_REQUEST`` 设为 1 时，页面请求内也会顺手更新一批
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# cli/ 是 python/auto 的直接子目录，所以 auto 根目录要再往上一层。
AUTO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON_DIR = os.path.dirname(AUTO_DIR)
for _path in (AUTO_DIR, PYTHON_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from source import quotesource  # noqa: E402
from updater.quoteupdater import (  # noqa: E402
    ENV_INTERVAL,
    build_default_updater,
    collect_symbols_from_source,
    is_any_market_open,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="实时行情定时抓取")
    parser.add_argument("--once", action="store_true", help="抓一轮就退出")
    parser.add_argument("--daemon", action="store_true", help="常驻定时抓取")
    parser.add_argument("--interval", type=int, default=None, help="间隔秒数，默认取环境变量或 60")
    parser.add_argument("--source", default=None, help="行情源名字，默认取环境变量或 sina")
    parser.add_argument("--only-open", action="store_true", help="只在有市场开市时才抓")
    parser.add_argument("--limit", type=int, default=0, help="最多抓多少个符号（0 表示不限）")
    parser.add_argument("--api", action="store_true", help="打印数据源接口说明")
    parser.add_argument("--list-sources", action="store_true", help="列出已注册的行情源")
    parser.add_argument("--verbose", action="store_true", help="每轮都打印明细")
    return parser.parse_args()


def print_api() -> int:
    """打印"换行情源"需要的接口说明。"""
    print("行情数据源接口（换源只改这里，Web 与解析层都不用动）")
    print("=" * 72)
    print("""
1) 抽象基类：python/auto/quotesource.py 的 MarketQuoteSource
   必须实现：
       name                      : str，数据源标识
       fetch_lines(symbols)      : {符号: 原始文本}，整批失败返回 None
   可选覆写：
       fetch_raw(symbols)        : 按行拼接的原始响应（默认已实现）
       describe()                : 诊断信息（默认已实现）

2) fetch_lines() 的返回约定：
       * 键 = 调用方传入的符号（数据源自己的代码），值 = 该符号的原始文本；
       * 保持这个约定，stockref 的解析器就不用改；
       * 返回 None 表示整批失败，与 PHP GetSinaQuotes() 返回 false 对齐。

3) 注册与切换：
       from source.quotesource import register_quote_source, get_quote_source
       register_quote_source("mysource", lambda **kw: MySource(**kw))
       # 然后设置 PALMMICRO_QUOTE_SOURCE=mysource，或直接 get_quote_source("mysource")

4) 解析层：python/auto/stockref.py 的 StockReference
       * 如果是"逐行 var hq_str_X=\\"...\\";"格式，直接复用，无需改动；
       * 如果是别的格式（例如 JSON），需要为它加一个 from_<source>() 之类的
         适配函数，把字段映射到 StockReference 的属性上。
""")
    print("当前已注册的行情源：%s" % ", ".join(quotesource.list_quote_sources()))
    print("默认行情源：%s" % quotesource.DEFAULT_QUOTE_SOURCE)
    source = quotesource.get_quote_source()
    print("当前实例自述：%s" % json.dumps(source.describe(), ensure_ascii=False))
    return 0


def main() -> int:
    args = parse_args()

    if args.api:
        return print_api()
    if args.list_sources:
        print("已注册的行情源：%s" % ", ".join(quotesource.list_quote_sources()))
        return 0

    interval = args.interval or int(os.getenv(ENV_INTERVAL, "60"))

    # 符号来源：连库取"最近有行情"的符号
    from core.stockdata import MySqlFundDataSource, load_mysql_env, mysql_config_from_env

    config = mysql_config_from_env(load_mysql_env(os.environ))
    print("目标库：%s@%s/%s" % (config["user"], config["host"], config["database"]))
    fund_source = MySqlFundDataSource.from_env()
    pairs = collect_symbols_from_source(fund_source, limit=args.limit)
    print("待抓符号 %d 个：%s" % (len(pairs), ", ".join(symbol for symbol, _source, _id in pairs)))
    if not pairs:
        print("没有可抓的符号：stockhistory 里还没有数据。先用 quotecli.py 落一次库，")
        print("或运行 python -B auto\\chinastock.py 建立符号名录。")
        return 1

    # build_default_updater 会带上 realtimequote 表，这样本进程写的行情
    # Web 进程也能读到（内存缓存跨不了进程）。
    updater = build_default_updater(
        source=quotesource.get_quote_source(args.source),
        symbols_provider=lambda: pairs,
        ttl=interval,
    )

    if args.once:
        if args.only_open and not is_any_market_open([symbol for symbol, _source, _id in pairs]):
            print("当前没有市场处于交易时段，跳过（--only-open）。")
            return 0
        updated = updater.update(pairs)
        print("已更新 %d 条行情。" % updated)
        for symbol, quote in sorted(updater.get_all(ttl=10 ** 9).items()):
            if quote.price:
                print("  %-10s 价=%-10s 昨收=%-10s %s %s" % (
                    symbol, quote.price, quote.prev_price, quote.date, quote.time))
        if updater.last_error:
            print("注意：%s" % updater.last_error)
        return 0

    if not args.daemon:
        print("请指定 --once 或 --daemon（--api 可查看数据源接口说明）。")
        return 1

    print("进入定时抓取，间隔 %d 秒；Ctrl+C 退出。" % interval)
    while True:
        started = time.time()
        try:
            if args.only_open and not is_any_market_open([symbol for symbol, _source, _id in pairs]):
                if args.verbose:
                    print("[%s] 无市场开市，跳过" % time.strftime("%H:%M:%S"))
            else:
                updated = updater.update(pairs)
                line = "[%s] 更新 %d 条" % (time.strftime("%H:%M:%S"), updated)
                if updater.last_error:
                    line += "  错误：%s" % updater.last_error
                elif args.verbose:
                    line += "  " + " ".join(
                        "%s=%s" % (symbol, quote.price)
                        for symbol, quote in sorted(updater.get_all(ttl=10 ** 9).items())
                        if quote.price
                    )
                # flush 让后台运行时也能及时看到日志（stdout 默认带缓冲）
                print(line, flush=True)
        except KeyboardInterrupt:
            print("\n已退出。")
            return 0
        except Exception as exc:
            print("[%s] 抓取异常：%s: %s" % (time.strftime("%H:%M:%S"), type(exc).__name__, exc), flush=True)

        # 扣掉本轮耗时，避免间隔漂移
        remaining = interval - (time.time() - started)
        try:
            time.sleep(max(1.0, remaining))
        except KeyboardInterrupt:
            print("\n已退出。")
            return 0


if __name__ == "__main__":
    sys.exit(main())
