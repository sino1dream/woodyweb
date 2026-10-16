"""建表 + 演示种子：``stockgroup`` / ``stockgroupitem`` 五张表。

用法：

    cd F:\\woody\\woodyweb\\python
    python -B dbtools\\seed_stock_group_tables.py            # dry-run，只打印将执行
    python -B dbtools\\seed_stock_group_tables.py --apply    # 建表 + 灌种子
    python -B dbtools\\seed_stock_group_tables.py --clear    # 清掉本脚本灌的种子

为什么单独一个脚本：后台管理页（``web/routes/admin.py``）依赖
``stockgroup`` / ``stockgroupitem`` 两张表，而演示库里**从来没有**这两张
表（``check_schema.py`` 的 14 张核心表里也没有）。PHP 靠
``TableSql::__construct()`` 构造即建表（``php/sql/sqltable.php:11``），
我们不在 web 长进程里发 DDL，所以收敛到这个脚本。

## 建哪些表、为什么是五张

PHP 侧 ``php/sql/sqlstockgroup.php`` 涉及五张表：

==================  ==========================================  ==============
表                   PHP 来源                                   后台是否使用
==================  ==========================================  ==============
``stockgroup``      ``KeyStringSql::Create()``                  是
``stockgroupitem``  ``SqlCreateStockGroupItemTable()`` 注释     是
``member``          ``sqlmember.php`` 的 FK 目标                是（FK 目标）
``groupitemamount`` ``GroupItemAmountSql extends IntSql``      否
``groupitemextra``  ``GroupItemExtraSql extends IntSql``       否
==================  ==========================================  ==============

后两张（分成员的金额与扩展记录）后台页用不到，但**照样建**：与 PHP
保持表级对称，将来迁 ``stocktrans`` / 资金汇总段落时不用再补 DDL。
``member`` 建最小集（``id + email + register + login + status +
activity``）——PHP 的 ``MemberSql`` 管的是 ``member2`` 表（遗留命名），
而 ``stockgroup`` 的 FK 指向 ``member``，登录体系未迁，我们只要 FK
有目标、能灌一条演示会员。

## DDL 与 PHP 的逐点对应

* 引擎 ``MYISAM`` + ``utf8 COLLATE utf8_unicode_ci`` —— 与 PHP
  ``CreateTable()`` 及演示库现有 25 张表一致。**FK 因此不生效**，
  删分组靠数据层手工先删 item（``core.stockgroupsql`` 已按此实现）。
* ``stockgroupitem`` 的 ``UNIQUE (stock_id, stockgroup_id)`` 来自 PHP
  注释版建表语句；PHP 的 ``SqlAlterStockGroupItemTable()`` 还补过
  ``INDEX (record)``，一并建上。
* 不建 ``FOREIGN KEY``？建。MyISAM 会**静默忽略** FK 声明，行为与
  PHP 逐字相同（PHP 也写了 FK 也被忽略），DDL 留个记录。
"""

from __future__ import annotations

import io
import os
import sys

# 命令行脚本会打印中文，Windows 控制台默认编码可能是 GBK，这里强制 UTF-8。
# 注意：只在 __main__ 分支包 stdout —— 本模块可能被别的脚本 import，
# 导入时替换 sys.stdout 会把调用方的输出流关掉（#83 变体，已踩过）。
if __name__ == "__main__" and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SQL_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.dirname(SQL_DIR)
AUTO_DIR = os.path.join(PYTHON_DIR, "auto")
for _path in (AUTO_DIR, PYTHON_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core.stockdata import MySqlFundDataSource  # noqa: E402
from core.stockgroupsql import DEMO_MEMBER_ID  # noqa: E402

#: 演示分组：分组名 → 成员符号（会先查 ``stock`` 表拿 id）。
#: 用真实符号而不是编造 —— 后台页的成员列表要能点到 /fund/<symbol>。
DEMO_GROUPS = {
    "演示分组A": ("SZ161125", "SH513100", "SZ159501"),
    "演示分组B": ("QQQ", "SPY"),
}

#: ``member`` 表演示行（PHP ``SqlInsertMember`` 的字段子集）。
DEMO_MEMBER = {
    "id": DEMO_MEMBER_ID,
    "email": "demo@palmmicro.com",
    "register": "2026-01-01",
    "login": "2026-01-01",
    "status": 1,
    "activity": 0,
}

# 五张表的 DDL。逐条与 PHP 对应关系见模块 docstring。
DDL = {
    # KeyStringSql(TABLE_STOCK_GROUP, TABLE_MEMBER, 'groupname', 64)::Create()
    #   = CreateIdTable(member_id INT + groupname VARCHAR(64) + FK + UNIQUE)
    "stockgroup": """
        CREATE TABLE IF NOT EXISTS `stockgroup` (
          `id` INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY ,
          `member_id` INT UNSIGNED NOT NULL ,
          `groupname` VARCHAR( 64 ) CHARACTER SET utf8 COLLATE utf8_unicode_ci NOT NULL ,
          FOREIGN KEY (`member_id`) REFERENCES `member`(`id`) ON DELETE CASCADE ,
          UNIQUE ( `groupname`, `member_id` )
        ) ENGINE = MYISAM CHARACTER SET utf8 COLLATE utf8_unicode_ci
    """,
    # php/sql/sqlstockgroup.php:154 注释版 SqlCreateStockGroupItemTable()
    # + SqlAlterStockGroupItemTable() 的 INDEX (record)
    "stockgroupitem": """
        CREATE TABLE IF NOT EXISTS `stockgroupitem` (
          `id` INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY ,
          `stockgroup_id` INT UNSIGNED NOT NULL ,
          `stock_id` INT UNSIGNED NOT NULL ,
          `quantity` INT NOT NULL ,
          `cost` DOUBLE(10,3) NOT NULL ,
          `record` INT NOT NULL ,
          INDEX ( `record` ) ,
          FOREIGN KEY (`stockgroup_id`) REFERENCES `stockgroup`(`id`) ON DELETE CASCADE ,
          UNIQUE ( `stock_id`, `stockgroup_id` )
        ) ENGINE = MYISAM CHARACTER SET utf8 COLLATE utf8_unicode_ci
    """,
    # sqlmember.php 的 FK 目标。PHP 的 MemberSql 管 member2（遗留），
    # 这里建 member 最小集，只服务 stockgroup 的 FK 与后台归属列。
    "member": """
        CREATE TABLE IF NOT EXISTS `member` (
          `id` INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY ,
          `email` VARCHAR( 128 ) CHARACTER SET utf8 COLLATE utf8_unicode_ci NOT NULL ,
          `register` DATE NOT NULL ,
          `login` DATE NOT NULL ,
          `status` TINYINT UNSIGNED NOT NULL ,
          `activity` INT UNSIGNED NOT NULL ,
          UNIQUE ( `email` )
        ) ENGINE = MYISAM CHARACTER SET utf8 COLLATE utf8_unicode_ci
    """,
    # GroupItemAmountSql extends IntSql：id PK + num（ValSql 的默认列名）
    "groupitemamount": """
        CREATE TABLE IF NOT EXISTS `groupitemamount` (
          `id` INT UNSIGNED NOT NULL PRIMARY KEY ,
          `num` INT UNSIGNED NOT NULL
        ) ENGINE = MYISAM CHARACTER SET utf8 COLLATE utf8_unicode_ci
    """,
    # GroupItemExtraSql extends IntSql('groupitemextra', 'record')：
    # 键列叫 record 而不是 num；Create() 追加 quantity / cost 两列。
    "groupitemextra": """
        CREATE TABLE IF NOT EXISTS `groupitemextra` (
          `id` INT UNSIGNED NOT NULL PRIMARY KEY ,
          `record` INT UNSIGNED NOT NULL ,
          `quantity` INT NOT NULL ,
          `cost` DOUBLE(10,3) NOT NULL
        ) ENGINE = MYISAM CHARACTER SET utf8 COLLATE utf8_unicode_ci
    """,
}


def _table_exists(source: MySqlFundDataSource, table: str) -> bool:
    cursor = source.connection.cursor()
    try:
        cursor.execute(
            "SELECT COUNT(*) AS n FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s", (table,))
        row = cursor.fetchone()
        return bool(row and int(row["n"]) > 0)
    finally:
        close = getattr(cursor, "close", None)
        if close:
            close()


def _count(source: MySqlFundDataSource, table: str) -> int:
    cursor = source.connection.cursor()
    try:
        cursor.execute("SELECT COUNT(*) AS n FROM %s" % table)
        row = cursor.fetchone()
        return int(row["n"]) if row else 0
    finally:
        close = getattr(cursor, "close", None)
        if close:
            close()


def _seed(source: MySqlFundDataSource) -> dict:
    """灌演示会员 + 演示分组，返回各行数统计。"""
    stats = {"member": 0, "groups": 0, "items": 0, "skipped_symbols": []}

    # 会员：PHP SqlInsertMember 是显式给 id 的 INSERT，照做
    cursor = source.connection.cursor()
    try:
        cursor.execute(
            "INSERT INTO member (id, email, register, login, status, activity) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE email = VALUES(email)",
            (DEMO_MEMBER["id"], DEMO_MEMBER["email"], DEMO_MEMBER["register"],
             DEMO_MEMBER["login"], DEMO_MEMBER["status"], DEMO_MEMBER["activity"]))
        stats["member"] = int(cursor.rowcount or 0)
    finally:
        close = getattr(cursor, "close", None)
        if close:
            close()

    from core.stockgroupsql import StockGroupItemSql, StockGroupSql

    group_sql = StockGroupSql(source)
    item_sql = StockGroupItemSql(source)
    for group_name, symbols in DEMO_GROUPS.items():
        group_id = group_sql.get_group_id(DEMO_MEMBER_ID, group_name)
        if group_id is None:
            group_id = group_sql.insert_string(DEMO_MEMBER_ID, group_name)
        if group_id is None:
            continue
        stats["groups"] += 1
        items = StockGroupItemSql(source, group_id)
        for symbol in symbols:
            stock_id = source.get_stock_id(symbol)
            if stock_id is None:
                # 演示库里没这个符号：跳过并记账（不静默）
                stats["skipped_symbols"].append(symbol)
                continue
            if items.get_id(stock_id) is not None:
                continue
            items.insert(stock_id)
            stats["items"] += 1
    return stats


def _clear(source: MySqlFundDataSource) -> dict:
    """清掉本脚本灌的种子（保留表）。只删演示会员的分组。"""
    from core.stockgroupsql import sql_delete_stock_group_by_member_id

    deleted_groups = sql_delete_stock_group_by_member_id(source, DEMO_MEMBER_ID)
    cursor = source.connection.cursor()
    try:
        cursor.execute("DELETE FROM member WHERE id = %s", (DEMO_MEMBER_ID,))
        deleted_member = int(cursor.rowcount or 0)
    finally:
        close = getattr(cursor, "close", None)
        if close:
            close()
    return {"groups": deleted_groups, "member": deleted_member}


def main() -> int:
    apply = "--apply" in sys.argv
    clear = "--clear" in sys.argv

    source = MySqlFundDataSource.from_env()
    try:
        for table, ddl in DDL.items():
            exists = _table_exists(source, table)
            rows = _count(source, table) if exists else 0
            state = "已存在(%d 行)" % rows if exists else "不存在"
            print("%-18s %s" % (table, state))
        print()

        if clear:
            print("--clear：删演示会员(id=%d)的分组与会员行（保留表）" % DEMO_MEMBER_ID)
            if not apply:
                print("dry-run，加 --apply 执行")
                return 0
            stats = _clear(source)
            print("已删分组 %d 个、会员 %d 行" % (stats["groups"], stats["member"]))
            return 0

        print("将建 %d 张表（IF NOT EXISTS，幂等）" % len(DDL))
        if not apply:
            print("dry-run，加 --apply 执行")
            return 0

        for table, ddl in DDL.items():
            cursor = source.connection.cursor()
            try:
                cursor.execute(ddl)
            finally:
                close = getattr(cursor, "close", None)
                if close:
                    close()
            print("  OK %s" % table)

        stats = _seed(source)
        print("种子：会员 %d 行、分组 %d 个、成员 %d 条" % (
            stats["member"], stats["groups"], stats["items"]))
        if stats["skipped_symbols"]:
            print("跳过（stock 表无此符号）：%s" % ", ".join(stats["skipped_symbols"]))
        return 0
    finally:
        source.connection.close()


if __name__ == "__main__":
    sys.exit(main())
