"""`python/auto/stocksql.py` 的单元测试。

对应 PHP ``php/sql/sqlstocksymbol.php:94`` 的 ``StockSql``（``extends KeyNameSql``）。
用假连接，不打网络、不连数据库。重点钉住三件事：

1. ``_SqlBuildLimit()`` 的三分支语义，包括 ``start != 0 且 num == 0`` 那条
   会拼出 ``LIMIT start, 0``（返回 0 行）的"看着像 bug"的行为；
2. 排序恒为 ``symbol ASC`` —— 列表页的顺序就靠这一句；
3. 三个单行查询（``get_id`` / ``get_name`` / ``get_symbol``）查不到时返回 ``None``
   而不是抛异常。
"""

from __future__ import annotations

import unittest

from stocksql import (
    STOCK_KEY_NAME,
    STOCK_TABLE,
    SqlBuildLimit,
    StockSql,
    build_limit,
    stock_paging_columns,
)


ROWS = [
    {"id": 1, "symbol": "SZ161130", "name": "纳斯达克100"},
    {"id": 2, "symbol": "SZ162411", "name": "华宝油气"},
    {"id": 3, "symbol": "SH518880", "name": "黄金ETF"},
]


class FakeCursor:
    """记录 SQL 并回放预置结果的假游标（与 test_historytable 同一套路）。"""

    def __init__(self, connection):
        self.connection = connection
        self.rows = []
        self.sql = ""
        self.params = ()

    def execute(self, sql, params=()):
        self.sql = sql
        self.params = params
        self.connection.queries.append((sql, params))
        self.rows = self.connection.dispatch(sql, params)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class FakeConnection:
    """按 SQL 特征分发的假连接：只认 ``stock`` 表那几种查询。"""

    def __init__(self, rows=None, total=None):
        self.queries = []
        self.rows = list(rows if rows is not None else ROWS)
        self.total = len(self.rows) if total is None else total

    def cursor(self):
        return FakeCursor(self)

    def dispatch(self, sql, params):
        if "COUNT(*)" in sql:
            return [{"n": self.total}]
        if sql.strip().startswith("SELECT") and "WHERE" in sql:
            if "`id` = %s" in sql:
                return [r for r in self.rows if str(r["id"]) == str(params[0])][:1]
            return [r for r in self.rows if r[STOCK_KEY_NAME] == params[0]][:1]
        # 列表查询：按 symbol 升序，再按 SQL 里的 LIMIT 切片
        rows = sorted(self.rows, key=lambda row: row[STOCK_KEY_NAME])
        return rows[self.limit_slice(sql)] if "LIMIT" in sql else rows

    @staticmethod
    def limit_slice(sql):
        """从 ``LIMIT a , b`` / ``LIMIT a`` 还原成 Python 切片。"""
        tail = sql.split("LIMIT", 1)[1].strip()
        if "," in tail:
            start, num = (int(part) for part in tail.split(","))
            return slice(start, start + num)
        start, num = 0, int(tail)
        return slice(start, start + num)


class BuildLimitTest(unittest.TestCase):
    """``_SqlBuildLimit()`` 的三分支。"""

    def test_start_zero_with_num_returns_plain_limit(self):
        self.assertEqual(build_limit(0, 10), "10")

    def test_start_zero_without_num_returns_empty(self):
        # num == 0 表示"取全部"，所以不加 limit
        self.assertEqual(build_limit(0, 0), "")

    def test_start_nonzero_returns_offset_pair(self):
        self.assertEqual(build_limit(20, 10), "20 , 10")

    def test_start_nonzero_with_zero_num_is_a_php_quirk(self):
        # PHP 会拼出 "LIMIT 20 , 0"，MySQL 返回 0 行。照抄，见模块 docstring。
        self.assertEqual(build_limit(20, 0), "20 , 0")

    def test_camel_alias_points_at_the_same_function(self):
        self.assertIs(SqlBuildLimit, build_limit)


class CountDataTest(unittest.TestCase):
    def test_count_reads_the_table_total(self):
        self.assertEqual(StockSql(FakeConnection()).count_data(), 3)

    def test_count_uses_the_driven_total(self):
        connection = FakeConnection(rows=[], total=1234)
        self.assertEqual(StockSql(connection).count_data(), 1234)

    def test_count_does_not_filter(self):
        connection = FakeConnection()
        StockSql(connection).count_data()
        self.assertNotIn("WHERE", connection.queries[0][0])


class GetAllTest(unittest.TestCase):
    def test_orders_by_symbol_ascending(self):
        rows = StockSql(FakeConnection()).get_all()
        self.assertEqual([row["symbol"] for row in rows], ["SH518880", "SZ161130", "SZ162411"])

    def test_num_zero_takes_everything(self):
        connection = FakeConnection()
        StockSql(connection).get_all(0, 0)
        self.assertNotIn("LIMIT", connection.queries[-1][0])

    def test_start_and_num_slice_the_page(self):
        connection = FakeConnection()
        rows = StockSql(connection).get_all(1, 1)
        self.assertEqual([row["symbol"] for row in rows], ["SZ161130"])
        self.assertIn("LIMIT 1 , 1", connection.queries[-1][0])

    def test_default_arguments_take_all_rows(self):
        # get_all() 等价于 get_all(0, 0)：PHP 的 KeyNameSql::GetAll() 默认就是全取
        connection = FakeConnection()
        self.assertEqual(len(StockSql(connection).get_all()), 3)

    def test_rows_carry_id_symbol_and_name(self):
        row = StockSql(FakeConnection()).get_all()[0]
        self.assertEqual(set(row), {"id", "symbol", "name"})


class SingleRecordTest(unittest.TestCase):
    def test_get_id(self):
        self.assertEqual(StockSql(FakeConnection()).get_id("SZ162411"), 2)

    def test_get_name(self):
        self.assertEqual(StockSql(FakeConnection()).get_name("SZ162411"), "华宝油气")

    def test_get_symbol_is_the_reverse_lookup(self):
        self.assertEqual(StockSql(FakeConnection()).get_symbol(3), "SH518880")

    def test_missing_symbol_gives_none(self):
        sql = StockSql(FakeConnection())
        self.assertIsNone(sql.get_id("SH999999"))
        self.assertIsNone(sql.get_name("SH999999"))

    def test_missing_id_gives_none(self):
        self.assertIsNone(StockSql(FakeConnection()).get_symbol(999))

    def test_get_record_returns_the_whole_row(self):
        row = StockSql(FakeConnection()).get_record("SZ161130")
        self.assertEqual(row, {"id": 1, "symbol": "SZ161130", "name": "纳斯达克100"})


class PagingColumnsTest(unittest.TestCase):
    """``stock_paging_columns()``：翻页控件的上一页/下一页 start。"""

    def test_first_page_has_no_previous(self):
        self.assertEqual(stock_paging_columns(0, 100, 500), (None, 100))

    def test_middle_page_has_both(self):
        self.assertEqual(stock_paging_columns(100, 100, 500), (0, 200))

    def test_prev_start_clamps_at_zero(self):
        # PHP：($iStart > $iNum) ? ($iStart - $iNum) : 0
        self.assertEqual(stock_paging_columns(50, 100, 500), (0, 150))

    def test_last_page_has_no_next(self):
        self.assertEqual(stock_paging_columns(400, 100, 500), (300, None))

    def test_total_zero_has_neither(self):
        self.assertEqual(stock_paging_columns(0, 100, 0), (None, None))


if __name__ == "__main__":
    unittest.main()
