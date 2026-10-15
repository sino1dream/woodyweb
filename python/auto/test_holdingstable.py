"""``sql/holdingstable.py`` 的单测（CK4-A）。

假连接照 ``test_fundest.py`` 的套路（``dispatch`` 用正则分派），但**两张表的
条件列不同**：``holdings`` 用 ``stock_id``，``holdingsdate`` 用 ``id`` ——
这个差异是本模块最容易写错的地方，所以它自己就是一条测试（见
:class:`HoldingsDateSqlTest`）。
"""

from __future__ import annotations

import re
import unittest

from sql.holdingstable import HoldingsDateSql, HoldingsSql


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self._rows: list = []
        self.lastrowid = 0

    def execute(self, sql, params=()):
        self._rows = self.connection.dispatch(sql, params)
        self.lastrowid = len(self._rows) + 1
        return len(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class FakeConnection:
    """自带 ``holdings`` 与 ``holdingsdate`` 两张表的假连接。"""

    def __init__(self, holdings=None, holdingsdate=None):
        self.queries: list = []
        self.tables = {
            "holdings": [dict(r) for r in (holdings or [])],
            "holdingsdate": [dict(r) for r in (holdingsdate or [])],
        }
        self.commits = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def dispatch(self, sql, params):
        self.queries.append(sql)
        upper = sql.strip().upper()
        table_match = re.search(r"FROM\s+`([^`]+)`", sql) or re.search(r"INTO\s+`([^`]+)`", sql)
        table = table_match.group(1) if table_match else ""
        rows = self.tables.get(table, [])

        if upper.startswith("INSERT"):
            if table == "holdingsdate":
                # (id, date, date)
                self._upsert_date(rows, params[0], params[1])
                return []
            # (stock_id, holding_id, ratio)
            rows.append({
                "id": len(rows) + 1,
                "stock_id": params[0],
                "holding_id": params[1],
                "ratio": params[2],
            })
            return []

        if "COUNT(*)" in sql:
            return [{"n": len([r for r in rows if str(r["stock_id"]) == str(params[0])])}]

        if table == "holdingsdate":
            # 条件列是 id（= stock_id 的值），不是 stock_id
            matched = [r for r in rows if str(r["id"]) == str(params[0])]
            if "LIMIT 1" in sql:
                matched = matched[:1]
            return matched

        matched = [r for r in rows if str(r["stock_id"]) == str(params[0])]
        matched.sort(key=lambda r: float(r["ratio"]), reverse=True)
        return matched

    @staticmethod
    def _upsert_date(rows, key, date):
        for row in rows:
            if str(row["id"]) == str(key):
                row["date"] = date
                return
        rows.append({"id": key, "date": date})


def _holding(stock_id, holding_id, ratio):
    return {"id": len(str(holding_id)), "stock_id": stock_id, "holding_id": holding_id, "ratio": ratio}


class HoldingsSqlTest(unittest.TestCase):
    def _sql(self, rows=None):
        connection = FakeConnection(holdings=rows)
        return HoldingsSql(connection), connection

    def test_empty_table_counts_zero(self):
        """没有持仓行 → ``StockGetHoldingsReference`` 的判据为假，整条分支不生效。"""
        sql, _ = self._sql()
        self.assertEqual(sql.count("1"), 0)
        self.assertEqual(sql.get_holdings_array("1"), {})

    def test_count_only_this_fund(self):
        sql, _ = self._sql([
            _holding("1", "11", 30.68),
            _holding("1", "12", 27.34),
            _holding("2", "11", 99.0),
        ])
        self.assertEqual(sql.count("1"), 2)
        self.assertEqual(sql.count("2"), 1)

    def test_get_all_is_sorted_by_ratio_desc(self):
        """``BuildOrderBy()`` 是 ``ratio DESC``：重仓排前面。

        顺序会一路带到页面的持仓表，也决定了 ``get_holdings_array`` 的键序。
        """
        sql, _ = self._sql([
            _holding("1", "11", 5.93),
            _holding("1", "12", 30.68),
            _holding("1", "13", 27.34),
        ])
        self.assertEqual([r["holding_id"] for r in sql.get_all("1")], ["12", "13", "11"])

    def test_get_holdings_array_int_keys_and_float_values(self):
        """PHP 那边的数组键是数字字符串（会被转 int），值是字符串（取值处 floatval）。

        这里一步到位：int 键 + float 值，调用方不必再转。
        """
        sql, _ = self._sql([
            _holding("1", "11", "30.68"),
            _holding("1", "12", "27.34"),
        ])
        array = sql.get_holdings_array("1")
        self.assertEqual(array, {11: 30.68, 12: 27.34})
        self.assertIsInstance(list(array)[0], int)
        self.assertIsInstance(array[11], float)

    def test_insert_and_insert_array(self):
        sql, connection = self._sql()
        sql.insert("1", "11", 30.68)
        self.assertEqual(sql.count("1"), 1)

        sql.insert_array("2", {"21": 1.5, "22": 2.5})
        self.assertEqual(sql.count("2"), 2)
        self.assertEqual(sql.get_holdings_array("2"), {21: 1.5, 22: 2.5})
        self.assertIn("INSERT INTO `holdings`", connection.queries[0])

    def test_camel_alias_maps_to_snake(self):
        """逐行对照 PHP 时用 camelCase 名更直观。"""
        self.assertIs(HoldingsSql.GetHoldingsArray, HoldingsSql.get_holdings_array)
        self.assertIs(HoldingsDateSql.ReadDate, HoldingsDateSql.read_date)


class HoldingsDateSqlTest(unittest.TestCase):
    """``holdingsdate`` 的条件列是 ``id`` —— 本模块最容易写错的一处。"""

    def _sql(self, rows=None):
        connection = FakeConnection(holdingsdate=rows)
        return HoldingsDateSql(connection), connection

    def test_read_date(self):
        sql, _ = self._sql([{"id": 1, "date": "2026-10-13"}])
        self.assertEqual(sql.read_date("1"), "2026-10-13")

    def test_missing_returns_none(self):
        sql, _ = self._sql()
        self.assertIsNone(sql.read_date("1"))

    def test_condition_column_is_id_not_stock_id(self):
        """**钉住命名陷阱**：``holdingsdate`` 没有 ``stock_id`` 列。

        表结构只有 ``(id, date)``，``id`` 里存的就是 stock_id 的值
        （PHP ``DateSql::ComposePrimaryIdStr()``）。照抄 ``DailyCloseSql``
        写成 ``WHERE stock_id = %s`` 会永远查不到 —— 症状是持仓分支静默不生效，
        而且不报错。这条测试保证没有人再把它改回去。
        """
        sql, connection = self._sql([{"id": 1, "date": "2026-10-13"}])
        sql.read_date("1")
        self.assertIn("WHERE id = %s", connection.queries[-1])
        self.assertNotIn("stock_id", connection.queries[-1])

    def test_write_date_upserts(self):
        sql, _ = self._sql()
        sql.write_date("1", "2026-10-13")
        self.assertEqual(sql.read_date("1"), "2026-10-13")
        sql.write_date("1", "2026-10-14")
        self.assertEqual(sql.read_date("1"), "2026-10-14")

    def test_write_date_for_second_fund(self):
        sql, _ = self._sql()
        sql.write_date("1", "2026-10-13")
        sql.write_date("2", "2026-10-12")
        self.assertEqual(sql.read_date("1"), "2026-10-13")
        self.assertEqual(sql.read_date("2"), "2026-10-12")


if __name__ == "__main__":
    unittest.main()
