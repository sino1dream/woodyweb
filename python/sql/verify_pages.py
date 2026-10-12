"""用真实数据库验证分组列表与基金详情页。

用法：

    cd F:\\woody\\woodyweb\\python
    python -B sql\\verify_pages.py
"""

from __future__ import annotations

import io
import json
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

from core.stockdata import load_mysql_env, mysql_config_from_env, MySqlFundDataSource  # noqa: E402
from web.bootstrap import install_paths  # noqa: E402

install_paths()

from web.services.fund_list_service import FundListService  # noqa: E402
from web.services.fund_service import FundService  # noqa: E402

SYMBOLS = ("SZ161130", "SZ161125", "SH513100")
# 传入实时数据源时用的参数。注意：真实部署里这些值由行情层（尚未接进 Web）
# 从新浪/Yahoo 取；这里只是证明"传参能影响估值"。
#
# 量级提醒：种子数据里中间层（QQQ/SPY）的 calibration 我按指数点位填，
# 与这些临时价格不配对，所以下面的"带实时源"数字会明显偏大。真实库导入后
# 中间层的 calibration/netvalue 来自实际数据，两者自然一致。
SOURCES = {
    "SZ161130": {"QQQ": 560.0 * 1.02, "CNY": 7.1},
    "SZ161125": {"SPY": 700.0 * 1.02, "CNY": 7.1},
    "SH513100": {"QQQ": 560.0 * 1.02, "CNY": 7.1},
}


def _print_group(group) -> None:
    """打印一个分组列表。

    **按列名取值而不是按位置 unpack**：`columns` 会随迁移推进而增减
    （从 7 列加到 12 列就曾经让按位置 unpack 的写法直接报错）。
    """
    print("标题：%s   成员：%d 只" % (group["title"], group["count"]))
    print(" | ".join(group["columns"]))
    print("-" * 110)
    keys = (
        "symbol", "name", "est_symbol", "position", "calibration", "calibration_date",
        "quote_date", "quote_time", "price", "change", "premium", "quote_source",
    )
    filled = 0
    for row in group["rows"]:
        # 有最新价或有校准值就算"有数据"
        if not (row.get("price") or row.get("calibration")):
            continue
        filled += 1
        print(" | ".join(str(row.get(key, "")) for key in keys))
    print("（有数据的行：%d / %d）" % (filled, group["count"]))


def main() -> int:
    config = mysql_config_from_env(load_mysql_env(os.environ))
    import pymysql

    connection = pymysql.connect(
        host=config["host"],
        port=config["port"],
        user=config["user"],
        password=config["password"],
        database=config["database"],
        charset="utf8",
        cursorclass=pymysql.cursors.DictCursor,
    )

    source = MySqlFundDataSource(connection)
    list_service = FundListService(source)
    fund_service = FundService(source)

    try:
        print("=" * 110)
        print("分组列表 /fund/qdiilof")
        print("=" * 110)
        _print_group(list_service.get_group("qdiilof"))

        print("\n" + "=" * 110)
        print("分组列表 /fund/qdiietf")
        print("=" * 110)
        _print_group(list_service.get_group("qdiietf"))

        print("\n" + "=" * 78)
        print("基金详情（估值）")
        print("=" * 78)
        for symbol in SYMBOLS:
            print("\n--- %s ---" % symbol)
            data = fund_service.get_fund(symbol)
            print("  名称      : %s" % data["name"])
            print("  仓位      : %s" % data["position"])
            print("  官方日期  : %s" % data["official_date"])
            print("  静态估值  : %s" % data["estimate"])
            config_json = data["config"]
            print("  symbol_hedge = %s, calibration = %s, netvalue = %s" % (
                config_json.get("symbol_hedge"), config_json.get("calibration"), config_json.get("netvalue")))
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
