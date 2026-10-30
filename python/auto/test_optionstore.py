"""``optionstore.py`` 的单元测试（外加 ``historytable`` 本次新增的写方法）。

对应 PHP：

* ``php/sql/sqlval.php``      —— ``ValSql`` 的 ``ReadVal`` / ``WriteVal`` / ``DeleteById``
* ``php/sql/sqlint.php``      —— ``IntSql`` 的 ``ReadInt`` / ``Add``
* ``php/sql/sqlpair.php``     —— ``PairSql`` 的 ``ReadPair`` / ``WritePair``
* ``php/sql/sqlstockpair.php``—— ``StockPairSql`` 的符号解析
* ``php/sql/sqldailyclose.php``—— ``DeleteAll``（``sqlkey.php:75``）
* ``php/sql/sqlstocksymbol.php``—— ``UpdateClose`` / ``UpdateAdjClose`` / ``GetFromDate``

## 为什么用 sqlite 而不是手写假连接

``historytable`` 的测试用的是"按 SQL 特征分发"的手写假连接 —— 那里查询形态
固定、够用。编辑页这批方法不一样：它们**先查后写**（``write_val`` 要先判断
值有没有变），手写假连接必须**复刻"写完之后再读能得到新值"**这件事，
否则"值没变就不写"与"值变了要 UPDATE"这两条根本区分不开。

用内存 sqlite 是把真实语义交给数据库：建一次表、跑真语句，``rowcount``、
``UNIQUE`` 冲突、``NOT NULL`` 都由 sqlite 负责。代价是 SQL 要能两边通用 ——
本项目发的语句只有反引号、``%s`` 占位符与 ``LIMIT``，sqlite 都认；
占位符换成 ``?`` 在包装层统一替换即可。
"""

from __future__ import annotations

import sqlite3
import unittest

from sql.historytable import MIN_FLOAT_VAL, DailyCloseSql, StockHistorySql
from sql.optionstore import (
    AdrPairStore,
    AhPairStore,
    FundPairStore,
    FuturePremiumSql,
    GroupItemAmountStore,
    IntStore,
    LastCalibrationStore,
    PairStore,
    StockEmaSql,
    ValStore,
)
from sql.stocksql import StockSql


class SqliteCursor:
    """把 ``%s`` 占位符换成 ``?`` 的游标包装。"""

    def __init__(self, cursor):
        self._cursor = cursor
        self.rowcount = 0

    def execute(self, sql, params=()):
        # sqlite 默认编译选项不支持 ``UPDATE ... LIMIT``（要 SQLITE_ENABLE_
        # UPDATE_DELETE_LIMIT）。本项目只有 ``UpdateById`` 会带 ``LIMIT 1``，
        # 而它的 WHERE 已经是主键 —— 去掉 LIMIT 语义完全一致。
        if sql.lstrip().upper().startswith("UPDATE"):
            sql = sql.replace(" LIMIT 1", "")
        self._cursor.execute(sql.replace("%s", "?"), tuple(params))
        self.rowcount = self._cursor.rowcount

    def fetchone(self):
        row = self._cursor.fetchone()
        return None if row is None else dict(row)

    def fetchall(self):
        return [dict(row) for row in self._cursor.fetchall()]

    def close(self):
        self._cursor.close()


class SqliteConnection:
    """内存 sqlite 连接，接口对齐 ``pymysql`` 的用法（``cursor`` / ``commit``）。"""

    def __init__(self):
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        self.commits = 0

    def cursor(self):
        return SqliteCursor(self._conn.cursor())

    def commit(self):
        self.commits += 1

    def execute(self, sql, params=()):
        cursor = self._conn.cursor()
        cursor.execute(sql, params)
        rows = [dict(row) for row in cursor.fetchall()]
        cursor.close()
        return rows

    # -- 建表助手 -----------------------------------------------------------
    def create_val_table(self, table, column="close", sql_type="REAL NOT NULL"):
        self._conn.execute(
            "CREATE TABLE `%s` (`id` INTEGER PRIMARY KEY, `%s` %s)"
            % (table, column, sql_type)
        )

    def create_pair_table(self, table):
        self._conn.execute(
            "CREATE TABLE `%s` (`id` INTEGER PRIMARY KEY, `stock_id` INTEGER NOT NULL)"
            % table
        )

    def create_daily_table(self, table, extra=""):
        self._conn.execute(
            "CREATE TABLE `%s` (`id` INTEGER PRIMARY KEY AUTOINCREMENT, "
            "`stock_id` INTEGER NOT NULL, `date` TEXT NOT NULL, `close` REAL NOT NULL%s)"
            % (table, extra)
        )

    def create_stock_table(self):
        self._conn.execute(
            "CREATE TABLE `stock` (`id` INTEGER PRIMARY KEY, `symbol` TEXT UNIQUE, "
            "`name` TEXT NOT NULL)"
        )

    def add_symbol(self, stock_id, symbol, name=""):
        self._conn.execute(
            "INSERT INTO `stock` (`id`, `symbol`, `name`) VALUES (?, ?, ?)",
            (stock_id, symbol, name),
        )


class ValStoreTest(unittest.TestCase):
    """``ValSql``：``(id, close)`` 的读、写、删。"""

    def setUp(self):
        self.connection = SqliteConnection()
        self.connection.create_val_table("lastcalibration")
        self.store = LastCalibrationStore(self.connection)

    def test_missing_row_reads_as_none_not_zero(self):
        """没有行返回 ``None`` —— 与"值是 0"必须能区分。"""
        self.assertIsNone(self.store.read_val(59))
        # 0 是合法值：PHP 里 ``ReadVal`` 给 ``false`` 只在"没有行"时
        self.store.insert(59, 0)
        self.assertEqual(self.store.read_val(59), 0.0)

    def test_write_val_inserts_then_updates(self):
        self.assertTrue(self.store.write_val(59, "1.5"))
        self.assertTrue(self.store.write_val(59, "2.5"))
        self.assertEqual(self.store.read_val(59), 2.5)

    def test_write_val_skips_unchanged_value(self):
        """值没变不发 UPDATE（``sqlval.php:41-56`` 那道闸门）。

        没有它，持仓链路每次渲染都会翻来覆去改同一行。阈值是
        ``MIN_FLOAT_VAL``，所以差一丁点也算没变。
        """
        self.store.insert(59, 100.0)
        self.assertFalse(self.store.write_val(59, "100.0"))
        self.assertFalse(self.store.write_val(59, str(100.0 + MIN_FLOAT_VAL / 10)))
        self.assertTrue(self.store.write_val(59, str(100.0 + MIN_FLOAT_VAL * 10)))

    def test_string_mode_on_integer_column_compares_text(self):
        """``$bString = true`` 走文本比较（``IntSql::WriteString``）。

        ``num`` / ``stock_id`` 这类 **int 列**上文本比较是有效的：库里是
        ``100``，提交 ``'100'`` 就该判"没变"。持仓页每次渲染都会顺手写一次，
        没有这道闸门会把行翻来覆去地改。

        **不要把它套到 double 列上**：``close`` 是 ``double(13,6)``，PHP 的
        mysqli 读回来是 ``"100.000000"``，和输入的 ``"100"`` 文本不等 ——
        PHP 那边同样会写一次。各语言读回来的表示不同，但"不相等"这个结论
        两边一致，所以行为对齐。真要跳过得走数值模式（``MIN_FLOAT_VAL``）。
        """
        store = IntStore(self.connection, table="groupitemamount", column="num")
        self.connection.create_val_table(
            "groupitemamount", column="num", sql_type="INTEGER NOT NULL"
        )
        store.insert(7, 100)
        self.assertFalse(store.write_string(7, "100"))
        self.assertTrue(store.write_string(7, "101"))

    def test_delete_by_id_returns_rowcount(self):
        self.store.insert(59, 1.5)
        self.assertEqual(self.store.delete_by_id(59), 1)
        self.assertEqual(self.store.delete_by_id(59), 0)
        self.assertEqual(self.store.delete_by_id(None), 0)

    def test_empty_id_never_reaches_sql(self):
        """空 id 直接返回 —— 不能让 ``WHERE id = ''`` 变成 ``id = 0``。"""
        self.assertFalse(self.store.write_val("", 1.0))
        self.assertIsNone(self.store.get_record_by_id(""))


class IntStoreTest(unittest.TestCase):
    """``IntSql``：``(id, num)`` 的整数语义。"""

    def setUp(self):
        self.connection = SqliteConnection()
        self.connection.create_val_table(
            "groupitemamount", column="num", sql_type="INTEGER NOT NULL"
        )
        self.store = GroupItemAmountStore(self.connection)

    def test_read_int_treats_zero_as_missing(self):
        """``'0'`` 读成 ``None`` —— PHP ``if ($str = ...)`` 把 ``'0'`` 当假值。"""
        self.store.insert(3, 0)
        self.assertIsNone(self.store.read_int(3))

    def test_add_starts_from_zero_when_missing(self):
        self.assertTrue(self.store.add(3, 5))
        self.assertEqual(self.store.read_int(3), 5)
        self.assertTrue(self.store.inc(3))
        self.assertEqual(self.store.read_int(3), 6)

    def test_add_skips_when_result_unchanged(self):
        self.store.insert(3, 5)
        self.assertFalse(self.store.add(3, 0))

    def test_read_amount_falls_back_to_default(self):
        """``ReadAmount()`` 读不到给 ``'100000'``（``sqlstockgroup.php:135``）。"""
        self.assertEqual(self.store.read_amount(99), "100000")
        self.store.insert(3, 250)
        self.assertEqual(self.store.read_amount(3), "250")


class PairStoreTest(unittest.TestCase):
    """``PairSql`` / ``StockPairSql``：``(id, stock_id)`` 的方向语义。"""

    def setUp(self):
        self.connection = SqliteConnection()
        for table in ("ahpair", "adrpair", "fundpair"):
            self.connection.create_pair_table(table)
        self.connection.create_stock_table()
        self.connection.add_symbol(59, "SH600030")
        self.connection.add_symbol(60, "HK06030")
        self.connection.add_symbol(61, "US_CRD")
        self.connection.add_symbol(62, "^NDX")
        self.connection.add_symbol(63, "SH513100")

    def test_pair_points_from_host_to_pair(self):
        """``id`` = 本体、``stock_id`` = 配对标的（方向搞反就查不到）。"""
        store = AhPairStore(self.connection)
        self.assertTrue(store.write_pair_symbol("SH600030", "HK06030"))
        self.assertEqual(store.get_pair_symbol("SH600030"), "HK06030")
        self.assertIsNone(store.get_pair_symbol("HK06030"))

    def test_reverse_lookup(self):
        """``GetSymbol()``：配对侧 → 本体侧（反向）。"""
        store = AhPairStore(self.connection)
        store.write_pair_symbol("SH600030", "HK06030")
        self.assertEqual(store.get_symbol("HK06030"), "SH600030")
        self.assertIsNone(store.get_symbol("US_CRD"))

    def test_write_pair_symbol_needs_both_symbols_in_stock(self):
        store = FundPairStore(self.connection)
        self.assertFalse(store.write_pair_symbol("SH513100", "NOPE"))
        self.assertFalse(store.write_pair_symbol("NOPE", "^NDX"))
        self.assertEqual(store.get_all_id_val(), {})

    def test_delete_by_symbol_and_by_pair_symbol_differ(self):
        """AH 的两条删除方向**不一样**，这是最容易写错的一处。

        * ``editstockah``（值是 H）：清空 → ``DeleteBySymbol(A)``；
        * ``editstockha``（值是 A）：清空 → ``DeleteByPairSymbol(H)``。
        """
        store = AhPairStore(self.connection)
        store.write_pair_symbol("SH600030", "HK06030")
        self.assertEqual(store.delete_by_pair_symbol("HK06030"), 1)
        self.assertEqual(store.get_pair_symbol("SH600030"), None)

        store.write_pair_symbol("SH600030", "HK06030")
        self.assertEqual(store.delete_by_symbol("SH600030"), 1)
        self.assertEqual(store.get_symbol("HK06030"), None)

    def test_adr_pair_direction(self):
        """``adrpair`` 的本体是 **ADR**，配对是 H 股（PHP 写的是
        ``WritePairSymbol($strAdr, $strSymbol)``，``_submitstockoptions.php:121``）。"""
        store = AdrPairStore(self.connection)
        self.assertTrue(store.write_pair_symbol("US_CRD", "HK06030"))
        self.assertEqual(store.get_symbol("HK06030"), "US_CRD")
        self.assertIsNone(store.get_pair_symbol("HK06030"))

    def test_read_pair_returns_raw_stock_id(self):
        store = PairStore(self.connection, table="fundpair")
        store.write_pair(63, 62)
        self.assertEqual(str(store.read_pair(63)), "62")
        self.assertIsNone(store.read_pair(59))

    # -- GetSymbolArray（三个 compare 页的行序就靠它） ---------------------
    def test_symbol_array_is_sorted_by_symbol_not_by_id(self):
        """``sort($arSymbol)`` 是**字符串升序**，不是写入/主键顺序。

        故意让 id 小的符号排在字母序后面：SH600030(id 59) 与 SH513100(id 63)
        按主键是 59 在前，但按 sort() 是 "SH513100" < "SH600030"。
        """
        store = AhPairStore(self.connection)
        store.write_pair_symbol("SH600030", "HK06030")
        store.write_pair_symbol("SH513100", "HK06030")
        self.assertEqual(store.get_symbol_array(), ["SH513100", "SH600030"])

    def test_symbol_array_skips_ids_without_stock_row(self):
        """``GetStockSymbol()`` 查不到的 id 跳过（PHP 会把 false 塞进数组，
        但 compare 页随后建引用对象会 fatal，这里按跳过处理）。"""
        store = AhPairStore(self.connection)
        store.write_pair_symbol("SH600030", "HK06030")
        store.write_pair(999, 60)       # 999 不在 stock 表里
        self.assertEqual(store.get_symbol_array(), ["SH600030"])

    def test_symbol_array_empty_table_and_unknown_pair(self):
        store = AhPairStore(self.connection)
        self.assertEqual(store.get_symbol_array(), [])
        # 传了配对符号时：查不到它的 id 直接返回空数组（PHP 同款行为）
        self.assertEqual(store.get_symbol_array("NOPE"), [])

    def test_symbol_array_by_pair_symbol_returns_hosts(self):
        """传配对符号时只列"跟踪它"的本体，方向不能反。"""
        store = AhPairStore(self.connection)
        store.write_pair_symbol("SH600030", "HK06030")
        store.write_pair_symbol("SH513100", "HK06030")
        self.assertEqual(store.get_symbol_array("HK06030"), ["SH513100", "SH600030"])
        self.assertEqual(store.get_symbol_array("SH600030"), [])


class DailyTableWriteTest(unittest.TestCase):
    """本次给 ``historytable`` 新增的三个方法。"""

    def setUp(self):
        self.connection = SqliteConnection()
        self.connection.create_daily_table(
            "stockhistory",
            extra=", `volume` INTEGER NOT NULL DEFAULT 0, `adjclose` REAL NOT NULL",
        )
        self.connection.create_daily_table("futurepremium")
        self.connection.execute(
            "INSERT INTO `stockhistory` (`stock_id`, `date`, `close`, `volume`, `adjclose`) "
            "VALUES (59, '2026-10-01', 2.0, 100, 2.0), (59, '2026-10-02', 2.1, 200, 2.1), "
            "(59, '2026-10-05', 2.2, 300, 2.2)"
        )
        self.history = StockHistorySql(self.connection)

    def test_update_close_writes_both_close_and_adjclose(self):
        """``UpdateClose`` 同时改复权价（``sqlstocksymbol.php:50``）—— 别"优化"掉。"""
        record_id = self.history.get_record(59, "2026-10-02")["id"]
        self.assertTrue(self.history.update_close(record_id, "9.99"))
        row = self.history.get_record(59, "2026-10-02")
        self.assertEqual(row["close"], 9.99)
        self.assertEqual(row["adjclose"], 9.99)
        # 别的日期不许动
        self.assertEqual(self.history.get_record(59, "2026-10-01")["close"], 2.0)

    def test_update_adjclose_leaves_close_alone(self):
        record_id = self.history.get_record(59, "2026-10-02")["id"]
        self.assertTrue(self.history.update_adjclose(record_id, "1.05"))
        row = self.history.get_record(59, "2026-10-02")
        self.assertEqual(row["close"], 2.1)
        self.assertEqual(row["adjclose"], 1.05)

    def test_get_from_date_is_inclusive_and_desc(self):
        """``date <= $strDate``，倒序 —— 拆股重算要靠"含当天"这个边界。"""
        rows = self.history.get_from_date(59, "2026-10-02")
        self.assertEqual([row["date"] for row in rows],
                         ["2026-10-02", "2026-10-01"])
        self.assertEqual(len(self.history.get_from_date(59, "2026-10-02", num=1)), 1)

    def test_delete_all_refuses_false_key(self):
        """``DeleteAll(false)`` 一行都不删（``sqlkey.php:77`` 的 ``!== false``）。"""
        self.assertEqual(self.history.delete_all(False), 0)
        self.assertEqual(self.history.count(59), 3)

    def test_delete_all_removes_only_that_stock(self):
        self.assertEqual(self.history.delete_all(59), 3)
        self.assertEqual(self.history.count(59), 0)

    def test_ema_table_name_is_concatenated(self):
        """``'stockema'.strval($iDays)`` —— 只有 50 与 200 两张表存在。"""
        self.assertEqual(StockEmaSql.table_name_for(50), "stockema50")
        self.assertEqual(StockEmaSql.table_name_for("200"), "stockema200")
        self.assertEqual(StockEmaSql(self.connection, 50).table, "stockema50")

    def test_future_premium_table(self):
        self.assertEqual(FuturePremiumSql(self.connection).table, "futurepremium")


class DailyCloseDeleteAllTest(unittest.TestCase):
    """``DeleteAll`` 长在基类上：所有日频表都能用。"""

    def test_base_class_table(self):
        connection = SqliteConnection()
        connection.create_daily_table("futurepremium")
        connection.execute(
            "INSERT INTO `futurepremium` (`stock_id`, `date`, `close`) "
            "VALUES (59, '2026-10-01', 4.5), (59, '2026-10-02', 4.6)"
        )
        sql = DailyCloseSql(connection, "futurepremium")
        self.assertEqual(sql.delete_all(59), 2)
        self.assertEqual(sql.count(59), 0)


class StockSqlSeenByPairStoreTest(unittest.TestCase):
    """``StockPairStore`` 允许注入 ``StockSql``（测试与调用方都能换）。"""

    def test_injected_stock_sql_is_used(self):
        connection = SqliteConnection()
        connection.create_pair_table("ahpair")
        connection.create_stock_table()
        connection.add_symbol(59, "SH600030")
        connection.add_symbol(60, "HK06030")
        store = AhPairStore(connection, stock_sql=StockSql(connection))
        self.assertTrue(store.write_pair_symbol("SH600030", "HK06030"))
        self.assertEqual(store.get_pair_symbol("SH600030"), "HK06030")


if __name__ == "__main__":
    unittest.main()
