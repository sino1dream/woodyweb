"""建三张编辑页要用、但演示库里没有的日频表（**幂等**，可重复执行）。

对应 PHP ``php/sql/sqldailyclose.php:283-320`` 的三个子类：

======================  ==========================  ================================
表                        PHP 类                      编辑页
======================  ==========================  ================================
``stocksplit``           ``StockSplitSql``           ``editstocksplit``（**未迁**）
``stockdividend``        ``StockDividendSql``        ``editstockdividend``（**未迁**）
``sharesdiff``           ``SharesDiffSql``           ``editsharesdiff``（**已迁**）
======================  ==========================  ================================

## 为什么只建表、不迁那两页

``sharesdiff`` 只是 "一天一个数字"，与 ``netvaluehistory`` 完全同形，
``_updateOptionDailySql()`` 一条路就够，所以建好表、编辑页立刻可用。

``stockdividend`` / ``stocksplit`` 不一样：写一次要**连带重算**该日之后的全部
复权价与校准值（``_updateStockHistoryAdjCloseByDividend`` /
``_updateStockHistoryAdjCloseBySplit``，还要改交易流水），属于估值链路。
只把表建出来而写入口留空，等于给一个能点但会算错的按钮 —— 所以这两页仍然
在"未迁"清单里，本脚本只是把**DLL 缺的那一块**补上，免得以后做的时候
还得从头查列定义。

## DDL 从哪抄

照 ``DailyCloseSql::Create()`` → ``CreateDailyCloseTable()``
（``php/sql/sqldailyclose.php``）与本地 ``stockhistory`` / ``futurepremium``
的实际形状：``id`` 自增主键、``stock_id`` + ``date`` 联合唯一、``stock_id``
单列索引、``close`` 是 ``double(13,6)``、引擎 **MyISAM**、字符集
``utf8/utf8_unicode_ci``。

**``close`` 用 ``double(13,6)`` 而不是 ``stockhistory`` 的 ``double(10,3)``** ——
份额（万）是整数级、分红有 6 位小数，都放得进 13 位；``10,3`` 存分红会丢精度。

用法::

    cd F:\\woody\\woodyweb\\python
    D:\\python\\python.exe -B dbtools\\seed_option_tables.py            # 预演
    D:\\python\\python.exe -B dbtools\\seed_option_tables.py --apply    # 真建表
"""

from __future__ import annotations

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

from core.stockdata import load_mysql_env, mysql_config_from_env  # noqa: E402

#: 要建的表。列定义与 ``DailyCloseSql::Create()`` 一致。
TABLES = ("sharesdiff", "stockdividend", "stocksplit")

DDL = """CREATE TABLE IF NOT EXISTS `{table}` (
    `id` int(10) unsigned NOT NULL AUTO_INCREMENT,
    `stock_id` int(10) unsigned NOT NULL,
    `date` date NOT NULL,
    `close` double(13,6) NOT NULL,
    PRIMARY KEY (`id`),
    UNIQUE KEY `date` (`date`,`stock_id`),
    KEY `stock_id` (`stock_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_unicode_ci"""


def main() -> int:
    apply = "--apply" in sys.argv
    config = mysql_config_from_env(load_mysql_env(os.environ))
    print("目标库：%s/%s" % (config["host"], config["database"]))
    if not apply:
        print("（预演模式，不写库；加 --apply 才建表）")

    import pymysql

    connection = pymysql.connect(
        host=config["host"],
        port=config.get("port", 3306),
        user=config["user"],
        password=config.get("password") or "",
        database=config["database"],
        charset=config.get("charset") or "utf8mb4",
    )
    created, existed, failed = [], [], []
    try:
        cursor = connection.cursor()
        for table in TABLES:
            cursor.execute("SHOW TABLES LIKE %s", (table,))
            if cursor.fetchone():
                existed.append(table)
                continue
            if not apply:
                created.append(table)
                continue
            try:
                cursor.execute(DDL.format(table=table))
                created.append(table)
            except Exception as exc:  # 建表失败要说清楚是哪张、为什么
                failed.append((table, str(exc)))
        cursor.close()
        connection.commit()
    finally:
        connection.close()

    if existed:
        print("已存在（未动）：%s" % ", ".join(existed))
    if created:
        print("%s：%s" % ("将建表" if not apply else "已建表", ", ".join(created)))
    for table, error in failed:
        print("建表失败 %s：%s" % (table, error))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
