"""把演示基金的种子净值与真实抓取到的市价对齐，并重解校准值。

为什么需要这一步：

    `stockhistory` 现在装的是**真实新浪行情**（`quotecli.py` 抓的），
    而 `netvaluehistory` / `calibrationhistory` 还是种子里的占位值。
    两者口径不一致时，页面上的溢价率会被算成离谱的数字（例如 +200%），
    看起来像 bug，其实只是数据还没对齐。

本脚本做的事：

1. 读每只基金在 `stockhistory` 里的**实际最新收盘价**；
2. 按给定折溢价把 `netvaluehistory` 设成对应净值（默认折价 0.5%，
   即市价 = 净值 × 0.995）；
3. 复用 `seed_demo_data.py` 的求解逻辑，重算 `calibrationhistory`，
   让估值重新收敛到新净值。

用法：

    cd F:\\woody\\woodyweb\\python
    python -B sql\\sync_demo_netvalue.py
    python -B sql\\sync_demo_netvalue.py --premium 0.02    # 想要 2% 溢价
"""

from __future__ import annotations

import argparse
import io
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SQL_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.dirname(SQL_DIR)
AUTO_DIR = os.path.join(PYTHON_DIR, "auto")
for _path in (AUTO_DIR, PYTHON_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core.stockdata import (  # noqa: E402
    MySqlFundDataSource,
    load_mysql_env,
    mysql_config_from_env,
)
from sql.quotestore import get_premium_rate  # noqa: E402
from web.bootstrap import install_paths  # noqa: E402

install_paths()

import seed_demo_data as seed  # noqa: E402

# 与真实市价对齐的基金（要有 stockhistory 数据）
FUNDS = ("SZ161130", "SZ161125", "SH513100")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="把种子净值与真实市价对齐")
    parser.add_argument(
        "--premium",
        type=float,
        default=-0.005,
        help="目标溢价率（小数）。默认 -0.005 表示折价 0.5%%",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = mysql_config_from_env(load_mysql_env(os.environ))
    print("目标库：%s@%s/%s\n" % (config["user"], config["host"], config["database"]))

    import pymysql

    connection = pymysql.connect(
        host=config["host"], port=config["port"], user=config["user"],
        password=config["password"], database=config["database"],
        charset="utf8", cursorclass=pymysql.cursors.DictCursor,
    )
    source = MySqlFundDataSource(connection)

    try:
        print("%-10s %-10s %-12s %-12s %s" % ("基金", "市价", "新净值", "重解校准值", "页面溢价率"))
        with connection.cursor() as cursor:
            for symbol in FUNDS:
                stock_id = source.get_stock_id(symbol)
                if stock_id is None:
                    print("%-10s 库里没有该符号，跳过" % symbol)
                    continue
                price = source.get_latest_history_close(stock_id)
                if price is None:
                    print("%-10s 没有行情数据，先跑 quotecli.py 抓一次" % symbol)
                    continue

                # 市价 = 净值 × (1 + 溢价)  =>  净值 = 市价 / (1 + 溢价)
                net_value = round(price / (1.0 + args.premium), 4)

                # 写新净值
                cursor.execute(
                    "DELETE FROM netvaluehistory WHERE stock_id = %s",
                    (stock_id,),
                )
                cursor.execute(
                    "INSERT INTO netvaluehistory (stock_id, date, close) VALUES (%s, %s, %s)",
                    (stock_id, seed.SEED_DATE, net_value),
                )
                connection.commit()

                # 重解校准值（复用种子脚本的迭代求解）
                ids = {symbol: stock_id}
                calibration, estimate, _hedge = seed.solve(
                    source, cursor, ids, symbol, net_value
                )

                premium = get_premium_rate(price, net_value)
                print("%-10s %-10.4f %-12.4f %-12.4f %.2f%%" % (
                    symbol, price, net_value, calibration, premium))

        connection.commit()
    finally:
        connection.close()

    print("\n完成。用 python -B sql\\verify_pages.py 复核。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
