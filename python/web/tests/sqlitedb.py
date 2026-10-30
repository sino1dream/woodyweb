"""web 层测试用的内存假库（sqlite 版）。

## 为什么用 sqlite 而不是手写 SQL 分发

`web/tests/` 里既有的假库（``test_quarter_report.FakeDb`` 等）是"按 SQL 特征
分发"的手写实现 —— 那里只有一两张表、查询形态固定，够用。

编辑页这批不一样：``StockOptionService`` 覆盖 10 个选项、十几张表，而且
**写方法都是"先查后写"**（值没变就不 UPDATE、已有同日行就不 INSERT）。
手写分发必须复刻"写完再读能读出新值"，否则"写成功"与"没写"根本分不开。
交给 sqlite 是让它承担真实语义：``UNIQUE``、``NOT NULL``、``rowcount``
都由数据库算，测试只负责建表和断言。

## 与 MySQL 的两处差异（都在包装层抹平）

1. ``%s`` 占位符 → ``?``；
2. ``UPDATE ... LIMIT 1`` → sqlite 默认编译不出这个语法（要
   ``SQLITE_ENABLE_UPDATE_DELETE_LIMIT``）。本项目只有 ``UpdateById`` 会带
   ``LIMIT 1``，而它的 ``WHERE`` 已经是主键 —— 去掉 LIMIT 语义不变。
"""

from __future__ import annotations

import sqlite3
from typing import Any


class SqliteCursor:
    """``%s`` → ``?`` 的游标包装，其余接口对齐 ``pymysql``。"""

    def __init__(self, cursor: Any, queries: list = None):
        self._cursor = cursor
        self._queries = queries if queries is not None else []
        self.rowcount = 0

    def execute(self, sql: str, params: tuple = ()) -> None:
        if sql.lstrip().upper().startswith("UPDATE"):
            sql = sql.replace(" LIMIT 1", "")
        self._queries.append((sql, params))
        self._cursor.execute(sql.replace("%s", "?"), tuple(params))
        self.rowcount = self._cursor.rowcount

    def fetchone(self):
        row = self._cursor.fetchone()
        return None if row is None else dict(row)

    def fetchall(self):
        return [dict(row) for row in self._cursor.fetchall()]

    def close(self) -> None:
        pass


class SqliteConnection:
    """内存 sqlite 连接（``cursor`` / ``commit`` 两个方法就够本项目的写方法用）。"""

    def __init__(self) -> None:
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        self.queries: list = []
        self.commits = 0

    # -- 连接协议 -----------------------------------------------------------
    def cursor(self) -> SqliteCursor:
        return SqliteCursor(self._conn.cursor(), self.queries)

    def commit(self) -> None:
        self.commits += 1

    # -- 直接执行（建表 / 断言用）-------------------------------------------
    def execute(self, sql: str, params: tuple = ()) -> list:
        cursor = self._conn.cursor()
        cursor.execute(sql, params)
        rows = [dict(row) for row in cursor.fetchall()]
        cursor.close()
        return rows

    def single(self, sql: str, params: tuple = ()) -> Any:
        rows = self.execute(sql, params)
        return rows[0] if rows else None

    # -- 建表助手 -----------------------------------------------------------
    def create_stock(self, rows: list) -> None:
        """``stock`` 表；``rows`` 是 ``(id, symbol, name)`` 三元组。"""
        self._conn.execute(
            "CREATE TABLE `stock` (`id` INTEGER PRIMARY KEY, `symbol` TEXT UNIQUE, "
            "`name` TEXT NOT NULL)"
        )
        for stock_id, symbol, name in rows:
            self._conn.execute(
                "INSERT INTO `stock` (`id`, `symbol`, `name`) VALUES (?, ?, ?)",
                (stock_id, symbol, name),
            )

    def create_daily(self, table: str, extra: str = "") -> None:
        """``(id, stock_id, date, close)`` 形状的日频表。"""
        self._conn.execute(
            "CREATE TABLE `%s` (`id` INTEGER PRIMARY KEY AUTOINCREMENT, "
            "`stock_id` INTEGER NOT NULL, `date` TEXT NOT NULL, `close` REAL NOT NULL%s)"
            % (table, extra)
        )

    def create_history(self) -> None:
        """``stockhistory``：多 ``volume`` / ``adjclose`` 两列且都 NOT NULL。"""
        self.create_daily(
            "stockhistory",
            extra=", `volume` INTEGER NOT NULL DEFAULT 0, `adjclose` REAL NOT NULL",
        )

    def create_calibration(self) -> None:
        """``calibrationhistory``：多 ``time`` / ``num``。"""
        self.create_daily(
            "calibrationhistory",
            extra=", `time` TEXT NOT NULL DEFAULT '00:00:00', `num` INTEGER NOT NULL DEFAULT 1",
        )

    def create_val(self, table: str, column: str = "close",
                   sql_type: str = "REAL NOT NULL") -> None:
        """``(id, 值)`` 的键值表（``fundposition`` / ``lastcalibration`` / ``groupitemamount``）。"""
        self._conn.execute(
            "CREATE TABLE `%s` (`id` INTEGER PRIMARY KEY, `%s` %s)"
            % (table, column, sql_type)
        )

    def create_pair(self, table: str) -> None:
        """``(id, stock_id)`` 的配对表。"""
        self._conn.execute(
            "CREATE TABLE `%s` (`id` INTEGER PRIMARY KEY, `stock_id` INTEGER NOT NULL)"
            % table
        )

    def create_group_tables(self) -> None:
        """分组三张表：``stockgroup`` / ``stockgroupitem`` / ``stocktransaction``。

        形状照 ``dbtools/seed_stock_group_tables.py``（列名与 MySQL 一致）。
        拆股写入口要遍历**全部**分组、给持有该标的的成员补两笔流水、再回填
        成员预统计（``quantity/cost/record``）—— 三张表缺一张就走不通。
        """
        self._conn.execute(
            "CREATE TABLE `stockgroup` (`id` INTEGER PRIMARY KEY, "
            "`member_id` INTEGER NOT NULL, `groupname` TEXT NOT NULL)"
        )
        self._conn.execute(
            "CREATE TABLE `stockgroupitem` (`id` INTEGER PRIMARY KEY AUTOINCREMENT, "
            "`stockgroup_id` INTEGER NOT NULL, `stock_id` INTEGER NOT NULL, "
            "`quantity` INTEGER NOT NULL DEFAULT 0, `cost` REAL NOT NULL DEFAULT 0, "
            "`record` INTEGER NOT NULL DEFAULT 0)"
        )
        self._conn.execute(
            "CREATE TABLE `stocktransaction` (`id` INTEGER PRIMARY KEY AUTOINCREMENT, "
            "`groupitem_id` INTEGER NOT NULL, `quantity` INTEGER NOT NULL, "
            "`price` REAL NOT NULL, `fees` REAL NOT NULL DEFAULT 0, "
            "`filled` TEXT NOT NULL DEFAULT '', `remark` TEXT NOT NULL DEFAULT '')"
        )

    def insert_daily(self, table: str, stock_id: Any, date: str, close: Any,
                     extra_columns: str = "", extra_values: tuple = ()) -> None:
        columns = "`stock_id`, `date`, `close`"
        marks = "?, ?, ?"
        if extra_columns:
            columns += ", " + extra_columns
            marks += ", " + ", ".join(["?"] * len(extra_values))
        self._conn.execute(
            "INSERT INTO `%s` (%s) VALUES (%s)" % (table, columns, marks),
            (stock_id, date, close) + tuple(extra_values),
        )


class FakeSource:
    """最小的"数据源"：只提供 ``connection`` 与可选的实时快照。

    ``StockOptionService`` 只用到 ``.connection`` 和可选的
    ``get_stored_quote`` / ``get_stored_quote_date``（"更新收盘价"的默认值
    取快照里的昨收）。不实现后两个也能跑 —— 服务层用 ``getattr`` 探测。
    """

    def __init__(self, connection: SqliteConnection, quote: Any = None):
        self.connection = connection
        self.quote = quote

    def get_stored_quote(self, symbol: str) -> Any:
        return self.quote

    def get_stored_quote_date(self, symbol: str) -> Any:
        return getattr(self.quote, "date", None) if self.quote else None


class FakeQuote:
    """``RecentQuote`` 的最小替身：只要 ``prev_price`` 与 ``date``。"""

    def __init__(self, prev_price: str, date: str = ""):
        self.prev_price = prev_price
        self.date = date
