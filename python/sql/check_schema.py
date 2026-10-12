"""数据库体检：连库、列表、行数、最近日期、缺失表。

用法：

    cd F:\\woody\\woodyweb\\python
    python -B sql\\check_schema.py

输出分两段：

1. **核心表**：`schema.sql` 定义的 14 张表是否都存在、行数、最大日期；
2. **Web 依赖表**：`MySqlFundDataSource` 实际会查的表，逐张给结论，
   用来回答"页面为什么没数据"。
"""

from __future__ import annotations

import io
import os
import sys

# 命令行脚本会打印中文，Windows 控制台默认编码可能是 GBK，这里强制 UTF-8。
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SQL_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.dirname(SQL_DIR)
AUTO_DIR = os.path.join(PYTHON_DIR, "auto")
for _path in (AUTO_DIR, PYTHON_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core.stockdata import load_mysql_env, mysql_config_from_env  # noqa: E402

# schema.sql 定义的表，以及各自用于展示"最新进度"的日期列
# 前 14 张是 PHP 原本就有的；后两张是 Python 侧新增（见 schema.sql 注释）
CORE_TABLES = [
    ("stock", None),
    ("fundposition", None),
    ("lastcalibration", None),
    ("holdingsdate", "date"),
    ("stockhistorydate", "date"),
    ("stockhistory", "date"),
    ("netvaluehistory", "date"),
    ("calibrationhistory", "date"),
    ("fundest", "date"),
    ("quarterreport", "date"),
    ("stockema50", "date"),
    ("stockema200", "date"),
    ("holdings", None),
    ("fundpair", None),
    # Python 新增：实时行情快照与中间层估值数值
    ("realtimequote", "fetched_at"),
    ("estquote", "fetched_at"),
]

# Web / 估值链路真正会查的表
WEB_DEPENDENCIES = [
    ("stock", "get_stock_id / get_stock_name / get_stock_symbol"),
    ("calibrationhistory", "get_calibration_now / get_calibration_from_date"),
    ("netvaluehistory", "get_netvalue_close"),
    ("fundposition", "get_position"),
    ("fundpair", "get_fund_pair_id"),
    ("holdings", "get_holdings（持仓型基金）"),
    ("holdingsdate", "官方日期回退"),
    ("stockhistory", "get_history_close / get_history_adjclose"),
]


def main() -> int:
    config = mysql_config_from_env(load_mysql_env(os.environ))
    print("连接：%s@%s:%s/%s" % (config["user"], config["host"], config["port"], config["database"]))

    import pymysql

    try:
        connection = pymysql.connect(
            host=config["host"],
            port=config["port"],
            user=config["user"],
            password=config["password"],
            database=config["database"],
            charset="utf8",
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=8,
        )
    except Exception as exc:
        print("连接失败：%s: %s" % (type(exc).__name__, exc))
        return 2

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT VERSION() AS v")
            print("服务端：%s" % cursor.fetchone()["v"])

            cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = %s", (config["database"],))
            existing = {row["table_name"] for row in cursor.fetchall()}

            print("\n=== 核心表（schema.sql）===")
            missing = []
            for table, date_column in CORE_TABLES:
                if table not in existing:
                    print("  %-20s 缺失" % table)
                    missing.append(table)
                    continue
                cursor.execute("SELECT COUNT(*) AS n FROM `%s`" % table)
                count = cursor.fetchone()["n"]
                latest = ""
                if date_column:
                    cursor.execute("SELECT MAX(`%s`) AS d FROM `%s`" % (date_column, table))
                    value = cursor.fetchone()["d"]
                    latest = "  最新 %s" % value if value else "  （无日期数据）"
                print("  %-20s %6d 行%s" % (table, count, latest))

            print("\n=== Web / 估值链路依赖 ===")
            for table, usage in WEB_DEPENDENCIES:
                if table not in existing:
                    print("  [缺少] %-20s %s" % (table, usage))
                    continue
                cursor.execute("SELECT COUNT(*) AS n FROM `%s`" % table)
                count = cursor.fetchone()["n"]
                flag = "有数据" if count else "空表"
                print("  [%s] %-20s %6d 行  %s" % (flag, table, count, usage))

            print("")
            if missing:
                print("结论：缺 %d 张表 -> %s" % (len(missing), ", ".join(missing)))
                print("执行 python -B sql\\build_schema.py 建表。")
            else:
                print("结论：%d 张表齐全。" % len(CORE_TABLES))
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
