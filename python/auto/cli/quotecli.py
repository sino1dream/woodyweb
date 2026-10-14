"""行情更新 CLI：抓取新浪行情并落库。

对应 PHP：``php/stock.php`` 的 ``StockPrefetchArrayData()`` +
``php/test/updatechinastock.php`` 那条更新链路里"取行情写历史"的部分。

用法：

    cd F:\\woody\\woodyweb\\python
    python -B auto\\quotecli.py --check                     # 只抓不写，打印解析结果
    python -B auto\\quotecli.py SZ161130 SZ161125 --check     # 指定符号
    python -B auto\\quotecli.py SZ161130 SZ161125           # 抓取并落库
    python -B auto\\quotecli.py --group qdiilof --check      # 按分组批量抓
    python -B auto\\quotecli.py --all-qdii --check           # 清单里的全部符号
    python -B auto\\quotecli.py --archive-snapshot ^NDX ^GSPC  # 把收盘快照归档进行情历史

## ``--archive-snapshot`` 是干什么的

后台 daemon（``quoteupdatercli --daemon``）只把当天行情写进 ``realtimequote``
**快照**（一行、覆盖式），从不写 ``stockhistory`` —— 后者得靠"收盘后跑一次
quotecli"才能得到。但有两个前提常常不成立：

1. 指数（``^NDX`` / ``^GSPC``）只能从新浪取**当前点位**，取不到历史序列，
   所以除了某几次手工回填，它们的 ``stockhistory`` 一直是空的/旧的；
2. 收盘后再跑 quotecli 时，某些市场已经开盘/数据已变，写进去的不是原来那天的收盘价。

而快照里存着的正是**那一天的收盘价**（daemon 在市场收盘后抓的），把它搬进
``stockhistory`` 才是"补历史"的正解。这一步是 P0.6 的前置：``cal_f``
（``AdjustFactor``）要求"标的行情日 == 锚点日"，指数行情停在 10-09、
基金净值已经到 10-12 时，30 只基金**一只都算不出校准值**。

行为：按快照的 ``quote_date`` 写一行；同名 (stock_id, date) 已存在就跳过
（幂等，重复跑无害）。

* 只写 ``stockhistory`` / ``stockhistorydate`` / ``netvaluehistory``；
  不写校准值（那是 ``calibrationhistory``，由估值流程决定）。
* ``--check`` 不打网络以外的地方，先看能不能取到数据再决定是否落库。
* ``--all-qdii`` 取 ``core.symbolinventory.sina_quote_symbols()``
  （102 只基金 + 22 个 est 中间层 = 124 个），与 P0.2 写 ``stock``
  行同一个清单模块，不会漂移。**不含**期货（``hf_*``，走 futurescli）、
  即期外汇与中间价（走 forexcli）—— ``get_sina_symbol()`` 对它们
  会给出 ``gb_hf_*`` 这种没有入口用的形式。
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

from calc.fundgroups import get_fund_group  # noqa: E402
from sql.quotestore import QuoteStore  # noqa: E402
from source.sinaquotes import SinaQuoteClient  # noqa: E402
from core.stockdata import (  # noqa: E402
    MySqlFundDataSource,
    load_mysql_env,
    mysql_config_from_env,
)
from ref.stockref import StockReference, sina_quote_file_name  # noqa: E402

from core.stocksymbol import StockSymbol  # noqa: E402

DEFAULT_SYMBOLS = ["SZ161130", "SZ161125", "SH513100", "QQQ", "SPY"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="抓取新浪行情并落库")
    parser.add_argument("symbols", nargs="*", help="要更新的符号，缺省用内置示例")
    parser.add_argument("--group", help="按基金分组批量取符号（qdiilof / qdiietf）")
    parser.add_argument("--all-qdii", action="store_true",
                        help="取符号清单里的全部 QDII 符号（基金 + est + 汇率/期货）")
    parser.add_argument("--check", action="store_true", help="只抓取并打印，不写数据库")
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP 超时秒数")
    parser.add_argument("--archive-snapshot", action="store_true",
                        help="不抓网络：把库里的收盘快照（realtimequote）归档进 stockhistory")
    return parser.parse_args()


def collect_symbols(args: argparse.Namespace) -> list:
    if args.symbols:
        return list(args.symbols)
    if args.all_qdii:
        # 与 P0.2 写 stock 行同一个清单（core/symbolinventory.py）。
        # 用 ``sina_quote_symbols()`` 而不是 ``required_stock_symbols()``：
        # 后者含期货/外汇，它们各有取数入口（futurescli / forexcli），
        # 且 ``get_sina_symbol()`` 对 ``hf_*`` 会给出没人用的 ``gb_hf_*``。
        from core.symbolinventory import sina_quote_symbols

        return list(sina_quote_symbols())
    if args.group:
        definition = get_fund_group(args.group)
        if definition is None:
            raise SystemExit("未知分组：%s" % args.group)
        return list(definition["symbols"]())
    return list(DEFAULT_SYMBOLS)


def cmd_archive_snapshot(args: argparse.Namespace) -> int:
    """把快照里的收盘价归档进 ``stockhistory``（见模块 docstring）。

    只读 ``realtimequote``、只写 ``stockhistory`` / ``stockhistorydate``，
    **不碰网络** —— 网络取数那一环 daemon 已经做过了。
    """
    symbols = collect_symbols(args)
    if not symbols:
        print("没有指定符号（--archive-snapshot 需要显式给出符号或用 --all-qdii）。")
        return 1

    config = mysql_config_from_env(load_mysql_env(os.environ))
    import pymysql

    connection = pymysql.connect(
        host=config["host"], port=config["port"], user=config["user"],
        password=config["password"], database=config["database"],
        charset="utf8", cursorclass=pymysql.cursors.DictCursor,
    )
    source = MySqlFundDataSource(connection)
    store = QuoteStore(connection)

    print("%-10s %-12s %-14s %s" % ("符号", "快照日期", "收盘价", "结果"))
    print("-" * 62)
    written = 0
    try:
        for symbol in symbols:
            stock_id = source.get_stock_id(symbol)
            if stock_id is None:
                print("%-10s %-12s %-14s %s" % (symbol, "-", "-", "不在 stock 表里"))
                continue
            quote = source.get_stored_quote(symbol)
            if quote is None:
                print("%-10s %-12s %-14s %s" % (symbol, "-", "-", "没有快照"))
                continue
            date = getattr(quote, "date", None)
            price = getattr(quote, "price", None)
            if not date or price in (None, ""):
                print("%-10s %-12s %-14s %s" % (symbol, date or "-", price or "-", "快照没有日期或价格"))
                continue
            existing = source.get_history_close(stock_id, str(date))
            if existing not in (None, ""):
                print("%-10s %-12s %-14s %s" % (symbol, date, existing, "已有历史行，跳过"))
                continue
            store.write_history(stock_id, str(date), price)
            store.write_stock_history_date(stock_id, str(date))
            written += 1
            print("%-10s %-12s %-14s %s" % (symbol, date, price, "已归档"))
        connection.commit()
    finally:
        connection.close()
    print("")
    print("归档 %d 条。" % written)
    return 0


def main() -> int:
    args = parse_args()
    if args.archive_snapshot:
        return cmd_archive_snapshot(args)
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
