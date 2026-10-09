"""行情更新 CLI：抓取新浪行情并落库。

对应 PHP：``php/stock.php`` 的 ``StockPrefetchArrayData()`` +
``php/test/updatechinastock.php`` 那条更新链路里"取行情写历史"的部分。

用法：

    cd F:\\woody\\woodyweb\\python
    python -B auto\\quotecli.py --check                     # 只抓不写，打印解析结果
    python -B auto\\quotecli.py SZ161130 SZ161125 --check     # 指定符号
    python -B auto\\quotecli.py SZ161130 SZ161125           # 抓取并落库
    python -B auto\\quotecli.py --group qdiilof --check      # 按分组批量抓

说明：

* 只写 ``stockhistory`` / ``stockhistorydate`` / ``netvaluehistory``；
  不写校准值（那是 ``calibrationhistory``，由估值流程决定）。
* ``--check`` 不打网络以外的地方，先看能不能取到数据再决定是否落库。
"""

from __future__ import annotations

import argparse
import io
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

AUTO_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.dirname(AUTO_DIR)
for _path in (AUTO_DIR, PYTHON_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from fundgroups import get_fund_group  # noqa: E402
from quotestore import QuoteStore  # noqa: E402
from sinaquotes import SinaQuoteClient  # noqa: E402
from stockdata import (  # noqa: E402
    MySqlFundDataSource,
    load_mysql_env,
    mysql_config_from_env,
)
from stockref import StockReference, sina_quote_file_name  # noqa: E402

from stocksymbol import StockSymbol  # noqa: E402

DEFAULT_SYMBOLS = ["SZ161130", "SZ161125", "SH513100", "QQQ", "SPY"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="抓取新浪行情并落库")
    parser.add_argument("symbols", nargs="*", help="要更新的符号，缺省用内置示例")
    parser.add_argument("--group", help="按基金分组批量取符号（qdiilof / qdiietf）")
    parser.add_argument("--check", action="store_true", help="只抓取并打印，不写数据库")
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP 超时秒数")
    return parser.parse_args()


def collect_symbols(args: argparse.Namespace) -> list:
    if args.symbols:
        return list(args.symbols)
    if args.group:
        definition = get_fund_group(args.group)
        if definition is None:
            raise SystemExit("未知分组：%s" % args.group)
        return list(definition["symbols"]())
    return list(DEFAULT_SYMBOLS)


def main() -> int:
    args = parse_args()
    symbols = collect_symbols(args)
    client = SinaQuoteClient(timeout=args.timeout)

    # 新浪符号：SZ161130 -> sz161130；QQQ -> gb_qqq
    sina_symbols = []
    mapping = {}
    for symbol in symbols:
        sina_symbol = StockSymbol(symbol).get_sina_symbol()
        if not sina_symbol:
            print("跳过（没有新浪符号）：%s" % symbol)
            continue
        sina_symbols.append(sina_symbol)
        mapping[sina_symbol] = symbol

    if not sina_symbols:
        print("没有可抓取的符号。")
        return 1

    print("请求 %d 个符号：%s" % (len(sina_symbols), ",".join(sina_symbols)))
    text = client.fetch(sina_symbols)
    if text is None:
        print("抓取失败：%s" % getattr(client, "last_error", "unknown"))
        return 2
    lines = text.split("\n")
    print("取回 %d 行\n" % len(lines))

    # 把"缓存文件名 -> 新浪符号"登记好，让解析走 prefetch/stockref 的真实链路
    file_map = {}
    for sina_symbol in sina_symbols:
        file_map[sina_quote_file_name(sina_symbol)] = sina_symbol
    client.cache_quote_files(file_map)

    connection = None
    store = None
    source = None
    if not args.check:
        config = mysql_config_from_env(load_mysql_env(os.environ))
        import pymysql

        connection = pymysql.connect(
            host=config["host"], port=config["port"], user=config["user"],
            password=config["password"], database=config["database"],
            charset="utf8", cursorclass=pymysql.cursors.DictCursor,
        )
        source = MySqlFundDataSource(connection)
        store = QuoteStore(connection)
        print("目标库：%s/%s\n" % (config["host"], config["database"]))

    try:
        written = 0
        for sina_symbol in sina_symbols:
            symbol = mapping[sina_symbol]
            ref = StockReference(symbol, quote_loader=client.load)
            ref.load_sina_data()

            print("%-10s -> %-10s 名称=%-22s 价=%-10s 昨收=%-10s 日期=%-11s 时间=%s" % (
                symbol,
                sina_symbol,
                (ref.str_name or "")[:22],
                ref.get_price() if ref.has_quote_data() else "-",
                ref.get_prev_price() if ref.has_quote_data() else "-",
                ref.str_date or "-",
                ref.str_time or "-",
            ))

            if not args.check and ref.has_quote_data():
                stock_id = source.get_stock_id(symbol)
                if stock_id is None:
                    print("    （库里没有 %s，跳过落库）" % symbol)
                    continue
                result = store.save_reference(stock_id, ref, save_net_value=False)
                if result["history"]:
                    written += 1

        if connection is not None:
            connection.commit()
            print("\n已写入 %d 条行情历史。" % written)
    finally:
        if connection is not None:
            connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
