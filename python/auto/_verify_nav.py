"""净值与中间层标的的库内核对工具（开发用）。

与 ``_nav_source_probe.py`` 的分工：
* ``_nav_source_probe.py``  打网络，看**数据源**是否可用、返回什么；
* 本工具（``_verify_nav.py``）读库，看**抓下来的数据**对不对、量级是否自洽。

用法：

    cd F:\\woody\\woodyweb\\python
    python -B auto\\_verify_nav.py            # 净值/市价/校准值/中间层对照
    python -B auto\\_verify_nav.py --est      # 只看 estquote 中间层表

核对什么
--------
1. **净值是否真实**：`netvaluehistory` 与市价应当同量级（QDII 滞后一天）；
2. **溢价率是否合理**：市价 / 净值，QDII-LOF 通常在 ±10% 内；
3. **量级是否自洽**：`calibrationhistory` 是"源值 + 净值"反算的派生量
   （``php/stock/qdiiref.php:193``），所以它和源值、净值的比例关系必须成立 ——
   这里把三者的比值打出来，换源后一眼能看出有没有错。
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

from quotestore import format_premium_rate, get_premium_rate  # noqa: E402
from stockdata import load_mysql_env, mysql_config_from_env  # noqa: E402


def connect():
    import pymysql

    config = mysql_config_from_env(load_mysql_env(os.environ))
    print("目标库：%s@%s/%s\n" % (config["user"], config["host"], config["database"]))
    return pymysql.connect(
        host=config["host"], port=config["port"], user=config["user"],
        password=config["password"], database=config["database"],
        charset="utf8", cursorclass=pymysql.cursors.DictCursor,
    )


def latest(cursor, table, stock_id, column="close"):
    cursor.execute(
        "SELECT %s AS value, date FROM %s WHERE stock_id = %%s ORDER BY date DESC LIMIT 1"
        % (column, table),
        (stock_id,),
    )
    row = cursor.fetchone()
    return row["value"] if row else None


def verify_all(connection) -> int:
    """三表对照：市价 / 净值 / 校准值，并算出溢价率。"""
    with connection.cursor() as cursor:
        cursor.execute("SELECT id, symbol FROM stock ORDER BY id")
        stocks = cursor.fetchall()

        print("=" * 100)
        print("市价 / 净值 / 校准值 对照（QDII 净值天然滞后一天，日期不同是正常的）")
        print("=" * 100)
        print("%-10s %-13s %-12s %-12s %-12s %-12s %s" % (
            "符号", "最新市价", "市价日期", "最新净值", "净值日期", "校准值", "溢价率"))
        for row in stocks:
            stock_id, symbol = row["id"], row["symbol"]
            price = latest(cursor, "stockhistory", stock_id)
            net_value = latest(cursor, "netvaluehistory", stock_id)
            calibration = latest(cursor, "calibrationhistory", stock_id)

            cursor.execute(
                "SELECT date FROM stockhistory WHERE stock_id = %s ORDER BY date DESC LIMIT 1",
                (stock_id,),
            )
            price_date = (cursor.fetchone() or {}).get("date")
            cursor.execute(
                "SELECT date FROM netvaluehistory WHERE stock_id = %s ORDER BY date DESC LIMIT 1",
                (stock_id,),
            )
            net_date = (cursor.fetchone() or {}).get("date")

            premium = get_premium_rate(price, net_value)
            print("%-10s %-13s %-12s %-12s %-12s %-12s %s" % (
                symbol,
                "%.4f" % float(price) if price is not None else "-",
                str(price_date) if price_date else "-",
                "%.4f" % float(net_value) if net_value is not None else "-",
                str(net_date) if net_date else "-",
                "%.6f" % float(calibration) if calibration is not None else "-",
                format_premium_rate(premium) or "-",
            ))
    print("")
    return 0


def verify_est(connection) -> int:
    """只看中间层表：确认换源后中间层数值是新写入的、量级正常。"""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT symbol, source_symbol, close, prev_close, quote_date, quote_time, "
            "source, fetched_at FROM estquote ORDER BY symbol"
        )
        rows = cursor.fetchall()

    if not rows:
        print("estquote 表是空的：中间层数值还没抓过。")
        print("执行： python -B auto\\quoteupdatercli.py --once")
        return 1

    print("estquote 共 %d 行（中间层标的；这些值来自新浪 gb_*/int_* 行情）" % len(rows))
    print("%-10s %-11s %-13s %-13s %-11s %-10s %s" % (
        "符号", "源代码", "最新值", "昨值", "行情日期", "行情时间", "来源"))
    for row in rows:
        print("%-10s %-11s %-13s %-13s %-11s %-10s %s" % (
            row["symbol"], row["source_symbol"], row["close"], row["prev_close"],
            str(row["quote_date"]), str(row["quote_time"]), row["source"]))
    print("")
    print("提示：这些值与 calibrationhistory 是**派生关系**（qdiiref.php:193），")
    print("      换源后只要让 calibrationhistory 跟着重算即可，不需要换算系数。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="净值与中间层数据核对")
    parser.add_argument("--est", action="store_true", help="只看 estquote 中间层表")
    args = parser.parse_args()

    connection = connect()
    try:
        if args.est:
            return verify_est(connection)
        verify_all(connection)
        return verify_est(connection)
    finally:
        connection.close()


if __name__ == "__main__":
    sys.exit(main())
