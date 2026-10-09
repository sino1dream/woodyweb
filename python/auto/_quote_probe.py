"""行情落库与溢价率的探查工具（开发用）。

用法：

    cd F:\\woody\\woodyweb\\python
    python -B auto\\_quote_probe.py                # 查库里的最新市价/净值/溢价率
    python -B auto\\_quote_probe.py --parse         # 用固定样本走一遍"解析 -> 落库"

它回答的问题：**为什么页面上的市价/溢价率是空的？**

排查顺序（本脚本按这个顺序输出）：

1. `stock` 表里有没有这个符号（没有 id 就没法落库）；
2. `stockhistory` 里有没有行情（空的话溢价率分子取不到）；
3. `netvaluehistory` 里有没有净值（空的话分母取不到）；
4. 两边都有才会有溢价率 = 市价 / 净值 - 1。

常用命令：

    python -B auto\\quotecli.py SZ161130 --check   # 先确认能抓到时新行情
    python -B auto\\quotecli.py SZ161130           # 再落库
    python -B auto\\_quote_probe.py                # 最后看库里情况
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import time

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

AUTO_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.dirname(AUTO_DIR)
for _path in (AUTO_DIR, PYTHON_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from quoteupdater import _parse_epoch  # noqa: E402
from quotestore import MemoryQuoteStore, format_premium_rate, get_premium_rate  # noqa: E402
from sinaquotes import SinaQuoteClient  # noqa: E402
from stockdata import (  # noqa: E402
    MySqlFundDataSource,
    load_mysql_env,
    mysql_config_from_env,
)
from stockref import StockReference, sina_quote_file_name  # noqa: E402
from stocksymbol import StockSymbol  # noqa: E402

SYMBOLS = ("SZ161130", "SZ161125", "SH513100")

# 离线样本：A 股/基金 32 字段，date(30)/time(31) 落在正确位置
SAMPLE_CN = 'var hq_str_sz161130="纳指LOF,4.700,4.727,4.796,4.800,4.690,4.795,4.796,1000000,4796000,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,2026-09-30,15:00:00,00";'


def parse_demo() -> int:
    """用固定样本走一遍解析 -> 落库（不连数据库、不打网络）。"""
    client = SinaQuoteClient(session=_OfflineSession(SAMPLE_CN.encode("gb18030")))
    symbol = "SZ161130"
    sina_symbol = StockSymbol(symbol).get_sina_symbol()
    client.cache_quote_files({sina_quote_file_name(sina_symbol): sina_symbol})

    reference = StockReference(symbol, quote_loader=client.load)
    reference.load_sina_data()

    print("解析结果：")
    print("  名称 : %s" % reference.str_name)
    print("  价   : %s   昨收: %s" % (reference.get_price(), reference.get_prev_price()))
    print("  开/高/低: %s / %s / %s" % (reference.str_open, reference.str_high, reference.str_low))
    print("  日期 : %s %s" % (reference.str_date, reference.str_time))

    store = MemoryQuoteStore()
    result = store.save_reference("1", reference)
    print("\n落库结果：%s" % result)
    print("  stockhistory      : %s" % store.history)
    print("  stockhistorydate  : %s" % store.dates)

    price = reference.get_price()
    net_value = "4.8201"
    premium = get_premium_rate(price, net_value)
    print("\n溢价率按 市价=%s 净值=%s 计算：%s" % (price, net_value, format_premium_rate(premium)))
    return 0


class _OfflineSession:
    def __init__(self, content):
        self.content = content

    def get(self, url, headers=None, timeout=None):
        class _Response:
            def __init__(self, content):
                self.content = content

            def raise_for_status(self):
                return None

        return _Response(self.content)


def probe_database() -> int:
    config = mysql_config_from_env(load_mysql_env(os.environ))
    import pymysql

    connection = pymysql.connect(
        host=config["host"], port=config["port"], user=config["user"],
        password=config["password"], database=config["database"],
        charset="utf8", cursorclass=pymysql.cursors.DictCursor,
    )
    source = MySqlFundDataSource(connection)

    try:
        print("%-10s %-8s %-12s %-12s %s" % ("符号", "stock_id", "最新市价", "最新净值", "溢价率"))
        for symbol in SYMBOLS:
            stock_id = source.get_stock_id(symbol)
            if stock_id is None:
                print("%-10s %-8s %s" % (symbol, "-", "库里没有该符号（先跑 chinastock.py 或 seed_demo_data.py）"))
                continue
            price = source.get_latest_history_close(stock_id)
            net_value = source.get_latest_net_value_close(stock_id)
            premium = get_premium_rate(price, net_value)
            print("%-10s %-8s %-12s %-12s %s" % (
                symbol,
                stock_id,
                "%.4f" % price if price is not None else "无",
                "%.4f" % net_value if net_value is not None else "无",
                format_premium_rate(premium) or "算不出",
            ))
    finally:
        connection.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="行情落库与溢价率探查")
    parser.add_argument("--parse", action="store_true", help="用固定样本走解析->落库，不连库")
    parser.add_argument("--realtime", action="store_true", help="看 realtimequote 实时快照表")
    args = parser.parse_args()
    if args.parse:
        return parse_demo()
    if args.realtime:
        return probe_realtime()
    return probe_database()


def probe_realtime() -> int:
    """显示 realtimequote 表：价格、来源、抓取时刻与"多久之前"。

    页面上的"最新价"就是从这里（或内存缓存）来的；如果这张表是空的，
    说明后台抓取还没跑过。
    """
    config = mysql_config_from_env(load_mysql_env(os.environ))
    import pymysql

    connection = pymysql.connect(
        host=config["host"], port=config["port"], user=config["user"],
        password=config["password"], database=config["database"],
        charset="utf8", cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT symbol, source_symbol, price, prev_price, quote_time, source, fetched_at "
                "FROM realtimequote ORDER BY symbol"
            )
            rows = cursor.fetchall()
    finally:
        connection.close()

    if not rows:
        print("realtimequote 表是空的：还没跑过抓取。")
        print("执行： python -B auto\\quoteupdatercli.py --once")
        return 1

    now = time.time()
    print("realtimequote 共 %d 行（当前 %s）" % (len(rows), _now_text()))
    print("%-10s %-10s %-12s %-12s %-10s %-9s %s" % (
        "符号", "源代码", "最新价", "昨收", "行情时间", "距今", "来源"))
    for row in rows:
        epoch = _parse_epoch(row["fetched_at"])
        age = "?" if epoch is None else "%.0fs" % (now - epoch)
        print("%-10s %-10s %-12s %-12s %-10s %-9s %s" % (
            row["symbol"], row["source_symbol"], row["price"], row["prev_price"],
            str(row["quote_time"]), age, row["source"]))
    return 0


def _now_text() -> str:
    import datetime

    return datetime.datetime.now().strftime("%H:%M:%S")


if __name__ == "__main__":
    sys.exit(main())
