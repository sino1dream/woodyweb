"""建表 + 演示种子：``stockgroup`` / ``stockgroupitem`` 等六张表。

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

## 建哪些表、为什么是六张

PHP 侧 ``php/sql/sqlstockgroup.php`` 涉及五张表：

==================  ==========================================  ==============
表                   PHP 来源                                   后台是否使用
==================  ==========================================  ==============
``stockgroup``      ``KeyStringSql::Create()``                  是
``stockgroupitem``  ``SqlCreateStockGroupItemTable()`` 注释     是
``member``          ``sqlmember.php`` 的 FK 目标                是（FK 目标）
``groupitemamount`` ``GroupItemAmountSql extends IntSql``      是（编辑页）
``groupitemextra``  ``GroupItemExtraSql extends IntSql``       否
==================  ==========================================  ==============

``groupitemamount`` 现在有页面写了：``/stock/<symbol>/edit/editstockamount``
（PHP ``_updateFundPurchaseAmount()``），一个会员一只基金一行。
``groupitemextra`` 仍然没人写，但**照样建**：与 PHP 保持表级对称，
将来迁 ``stocktrans`` / 资金汇总段落时不用再补 DDL。
``member`` 建最小集（``id + email + register + login + status +
activity``）——PHP 的 ``MemberSql`` 管的是 ``member2`` 表（遗留命名），
而 ``stockgroup`` 的 FK 指向 ``member``，登录体系未迁，我们只要 FK
有目标、能灌一条演示会员。

**第六张 ``stocktransaction``**（2026-10-24 补）：交易记录表。PHP 侧
``StockTransactionSql extends TableSql``（``php/sql/sqlstocktransaction.php:5``），
而 ``TableSql::Create()`` 只建 ``id`` 主键列 —— 生产库的其余列是**手工
ALTER 上去的**，PHP 源码里没有完整 DDL。本脚本的 DDL 是从**用法反推**的
（``_makePrivateFieldArray`` 的字段清单 + ``GetSqlTransactionDate`` 对
``filled`` 取空格前段 → DATETIME），不是抄来的；迁移 README 里记明。

## DDL 与 PHP 的逐点对应

* 引擎 ``MYISAM`` + ``utf8 COLLATE utf8_unicode_ci`` —— 与 PHP
  ``CreateTable()`` 及演示库现有 25 张表一致。**FK 因此不生效**，
  删分组靠数据层手工先删 item（``core.stockgroupsql`` 已按此实现）。
* ``stockgroupitem`` 的 ``UNIQUE (stock_id, stockgroup_id)`` 来自 PHP
  注释版建表语句；PHP 的 ``SqlAlterStockGroupItemTable()`` 还补过
  ``INDEX (record)``，一并建上。
* 不建 ``FOREIGN KEY``？建。MyISAM 会**静默忽略** FK 声明，行为与
  PHP 逐字相同（PHP 也写了 FK 也被忽略），DDL 留个记录。
* ``stocktransaction`` 的 ``quantity`` 是**有符号 INT** —— 卖出记负数
  （``AddSqlTransaction`` 直接把它加进总股数）；``filled`` 是 DATETIME
  （``DebugGetDateTime()`` 的 ``'Y-m-d H:i:s'`` 形状）。
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
    #: **分组名 = 符号**这一条是生产端"我的自选"的形状：每个会员给每只关注的
    #: 标的建一个同名分组，编辑页的"基金申购金额"靠它把 stock_id 换成
    #: ``stockgroupitem.id``（``SqlGetMyStockGroupItemId``）。本地后台页
    #: （``/admin/groups``）不允许建这种名字 —— 与符号重名会被加 ``@`` 前缀
    #: （``is_group_name_reserved``），所以演示库只能由种子灌一份样例，
    #: 否则那一项在演示环境里"永远没有可写的对象"。
    "SPY": ("SPY",),
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

#: ``stocktransaction`` 演示交易：分组名 → [(符号, 数量, 价格, 费用, 成交时间, 备注)]。
#: 数量**有符号** —— 卖出是负数（PHP ``AddSqlTransaction`` 直接累加进总股数）。
#: 每个成员一条买入 + SZ161125 再补一条卖出，让"总股数/笔数"非平凡
#: （1000 - 500 = 500 股、两笔），页面上的测算列才有东西可显示。
#: 价格取 2026-10-22 前后的量级，不追求与行情精确一致（演示数据）。
DEMO_TRANSACTIONS = {
    "演示分组A": [
        ("SZ161125", 1000, 1.650, 5.0, "2026-10-15 10:30:00", "建仓"),
        ("SZ161125", -500, 1.720, 5.0, "2026-10-20 14:00:00", "波段减仓"),
        ("SH513100", 500, 2.400, 5.0, "2026-10-16 09:45:00", ""),
        ("SZ159501", 2000, 0.850, 5.0, "2026-10-17 13:20:00", "定投"),
    ],
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
    # StockTransactionSql extends TableSql —— Create() 只建 id 列，
    # 其余列是生产库手工 ALTER 的（见模块 docstring）。DDL 从用法反推：
    #   _makePrivateFieldArray: groupitem_id / quantity / price / fees /
    #                          filled / remark
    #   GetSqlTransactionDate: strstr($record['filled'], ' ', true)
    #                          -> filled 是 'Y-m-d H:i:s' 形状的 DATETIME
    #   AddSqlTransaction: quantity 直接累加进总股数 -> 有符号 INT
    # price/fees 用 DOUBLE(13,6)，与 ComposeCloseStr 一致；
    # remark 用 VARCHAR(8192)，与 ComposeVarcharStr 默认长度一致。
    "stocktransaction": """
        CREATE TABLE IF NOT EXISTS `stocktransaction` (
          `id` INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY ,
          `groupitem_id` INT UNSIGNED NOT NULL ,
          `quantity` INT NOT NULL ,
          `price` DOUBLE(13,6) NOT NULL ,
          `fees` DOUBLE(13,6) NOT NULL ,
          `filled` DATETIME NOT NULL ,
          `remark` VARCHAR( 8192 ) CHARACTER SET utf8 COLLATE utf8_unicode_ci NOT NULL ,
          INDEX ( `groupitem_id` ) ,
          FOREIGN KEY (`groupitem_id`) REFERENCES `stockgroupitem`(`id`) ON DELETE CASCADE
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
    """灌演示会员 + 演示分组 + 演示交易，返回各行数统计。"""
    stats = {"member": 0, "groups": 0, "items": 0, "transactions": 0,
             "skipped_symbols": []}

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
        # 交易挂在成员（stockgroupitem）上，上面的循环必须先把 item 建好。
        # 幂等：**按单条特征**查重（同成员同数量同价格同时间视为同一条），
        # 不能按成员查 —— 一个成员可以有多条交易（买入+卖出），按成员查会
        # 把第二条起全跳过（SZ161125 的卖出条就这么被漏掉的，2026-10-24）。
        # 注意这行在 for 循环**内**：每个分组灌自己的交易。
        stats["transactions"] += _seed_transactions(source, group_id, group_name)
    return stats


def _seed_transactions(source: MySqlFundDataSource, group_id: Any,
                       group_name: str) -> int:
    """给一个分组灌演示交易（``DEMO_TRANSACTIONS`` 里配了才有）。

    ``filled`` 用固定时间戳而不是 NOW() —— 种子要可重现（同一份种子
    两次 apply 得到同一份数据），页面上的"成交日期"列也稳定。

    查重键是 ``groupitem_id + quantity + price + filled`` 四列：演示种子
    是常量表，重复 apply 不该翻倍；而**同一成员的多条交易**（如
    SZ161125 的买入+卖出）必须都能灌进去。
    """
    rows = DEMO_TRANSACTIONS.get(group_name)
    if not rows:
        return 0
    from core.stockgroupsql import StockGroupItemSql

    items = StockGroupItemSql(source, group_id)
    seeded = 0
    cursor = source.connection.cursor()
    try:
        for symbol, quantity, price, fees, filled, remark in rows:
            stock_id = source.get_stock_id(symbol)
            if stock_id is None:
                continue
            item_id = items.get_id(stock_id)
            if item_id is None:
                continue
            cursor.execute(
                "SELECT COUNT(*) AS n FROM stocktransaction "
                "WHERE groupitem_id = %s AND quantity = %s "
                "AND price = %s AND filled = %s",
                (item_id, quantity, price, filled))
            row = cursor.fetchone()
            if row and int(row["n"]) > 0:
                continue
            # PHP Insert(): groupitem_id/quantity/price/fees/filled/remark
            # 六列全给（filled 由 DebugGetDateTime() 生成，种子照形状给）。
            cursor.execute(
                "INSERT INTO stocktransaction "
                "(groupitem_id, quantity, price, fees, filled, remark) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (item_id, quantity, price, fees, filled, remark))
            seeded += int(cursor.rowcount or 0)
    finally:
        close = getattr(cursor, "close", None)
        if close:
            close()
    return seeded


def _clear(source: MySqlFundDataSource) -> dict:
    """清掉本脚本灌的种子（保留表）。只删演示会员的分组。"""
    from core.stockgroupsql import sql_delete_stock_group_by_member_id

    # MyISAM 静默忽略 FK：删分组**不会**级联删 stocktransaction，
    # 必须先按 groupitem_id 把交易删掉（PHP 那边 StockGroupItemSql 的
    # 删除分支同样手工 trans_sql->Delete，见 stockgroupsql 的偏差标注）。
    deleted_transactions = _clear_transactions(source)
    deleted_groups = sql_delete_stock_group_by_member_id(source, DEMO_MEMBER_ID)
    cursor = source.connection.cursor()
    try:
        cursor.execute("DELETE FROM member WHERE id = %s", (DEMO_MEMBER_ID,))
        deleted_member = int(cursor.rowcount or 0)
    finally:
        close = getattr(cursor, "close", None)
        if close:
            close()
    return {"groups": deleted_groups, "member": deleted_member,
            "transactions": deleted_transactions}


def _clear_transactions(source: MySqlFundDataSource) -> int:
    """删演示会员全部分组下的交易，返回行数。"""
    cursor = source.connection.cursor()
    try:
        # 先查 item id 再删（不用多表 DELETE，避开 MyISAM 的语法差异）
        cursor.execute(
            "SELECT t.id FROM stocktransaction t "
            "JOIN stockgroupitem i ON t.groupitem_id = i.id "
            "JOIN stockgroup g ON i.stockgroup_id = g.id "
            "WHERE g.member_id = %s", (DEMO_MEMBER_ID,))
        rows = cursor.fetchall() or []
        for row in rows:
            cursor.execute("DELETE FROM stocktransaction WHERE id = %s",
                           (row["id"],))
        return len(rows)
    finally:
        close = getattr(cursor, "close", None)
        if close:
            close()


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
            print("--clear：删演示会员(id=%d)的分组、交易与会员行（保留表）"
                  % DEMO_MEMBER_ID)
            if not apply:
                print("dry-run，加 --apply 执行")
                return 0
            stats = _clear(source)
            print("已删分组 %d 个、会员 %d 行、交易 %d 条"
                  % (stats["groups"], stats["member"], stats["transactions"]))
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
        print("种子：会员 %d 行、分组 %d 个、成员 %d 条、交易 %d 条" % (
            stats["member"], stats["groups"], stats["items"],
            stats["transactions"]))
        if stats["skipped_symbols"]:
            print("跳过（stock 表无此符号）：%s" % ", ".join(stats["skipped_symbols"]))
        return 0
    finally:
        source.connection.close()


if __name__ == "__main__":
    sys.exit(main())
