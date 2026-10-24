"""``sql.stocktransactiontable`` 的单元测试。

对应 PHP ``php/sql/sqlstocktransaction.php`` 的 ``StockTransactionSql``。
用内存 fake 连接（不连真库）：本模块的 SQL 形态规整（交易表只有
``groupitem_id`` 一个过滤列），fake 按"解释 SQL 子集"实现 ——
WHERE / ORDER BY / LIMIT / IN 列表都真解，不逐个方法分派
（分派替身不会因为 SQL 形状演进而报错，pitfalls #109 的教训）。

重点验证的语义：

1. ``get_all`` 排序是 ``filled`` DESC（不是 id DESC）；
2. ``filled`` 只在 insert 时生成，update 不碰（成交时间不可改）；
3. 空键三态：``count`` / ``delete`` / ``merge`` / ``get_record_by_id`` /
   ``delete_by_id`` 对 ``None`` 键返回 ``None`` 且**不发 SQL**
   （PHP 的 ``_SqlBuildWhere(false)`` 语义）；
4. ``count_all`` 空数组给 0（不是 None）；
5. ``delete`` 返回删除行数（PHP 的 ``DeleteData`` 先 COUNT 再 DELETE）；
6. LIMIT 三分支（``_php_build_limit``）。
"""

import re
import unittest

from sql.stocktransactiontable import (
    StockTransactionSql,
    _php_build_limit,
    debug_get_date_time,
)


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []
        self.rowcount = 0
        self.lastrowid = 0

    def execute(self, sql, params=()):
        self.connection.queries.append(sql)
        self.rows = []
        self.rowcount = 0
        self.lastrowid = 0
        self.connection.dispatch(sql, tuple(params), self)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def close(self):
        pass


class FakeConnection:
    """内存版 ``stocktransaction`` 表 + SQL 子集解释。"""

    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.queries = []
        self.next_id = 1

    def cursor(self):
        return FakeCursor(self)

    def dispatch(self, sql, params, cursor):
        text = " ".join(sql.split())
        upper = text.upper()
        if upper.startswith("SELECT *"):
            self._select(text, params, cursor)
        elif upper.startswith("SELECT COUNT(*)"):
            self._count(text, params, cursor)
        elif upper.startswith("DELETE FROM"):
            self._delete(text, params, cursor)
        elif upper.startswith("INSERT INTO"):
            self._insert(text, params, cursor)
        elif upper.startswith("UPDATE"):
            self._update(text, params, cursor)
        else:
            raise AssertionError("测试 fake 不认识的 SQL: %s" % sql)

    # -- 解释各语句 ---------------------------------------------------------
    def _select(self, text, params, cursor):
        rows = list(self.rows)
        if " WHERE " in text:
            rows = self._filter(rows, text, params)
        if " ORDER BY `filled` DESC" in text:
            rows.sort(key=lambda row: row["filled"], reverse=True)
        limit = self._limit_of(text)
        if limit:
            start, num = limit
            rows = rows[start:start + num] if num else rows[start:]
        cursor.rows = rows

    def _count(self, text, params, cursor):
        rows = list(self.rows)
        if " WHERE " in text:
            rows = self._filter(rows, text, params)
        cursor.rows = [{"n": len(rows)}]

    def _delete(self, text, params, cursor):
        rows = list(self.rows)
        if " WHERE " in text:
            where = text.split(" WHERE ", 1)[1]
            keep, drop = [], []
            for row in rows:
                (drop if self._match(row, where, params) else keep).append(row)
            self.rows = keep
        else:
            drop = rows
            self.rows = []
        cursor.rowcount = len(drop)

    def _insert(self, text, params, cursor):
        columns = self._columns_of(text)
        row = dict(zip(columns, params))
        row["id"] = self.next_id
        self.next_id += 1
        self.rows.append(row)
        cursor.rowcount = 1
        cursor.lastrowid = row["id"]

    def _update(self, text, params, cursor):
        assignments, where = self._split_update(text)
        # params 的前 len(assignments) 个喂 SET，剩下的才是 WHERE 的
        where_params = params[len(assignments):]
        touched = 0
        for row in self.rows:
            if not self._match(row, where, where_params):
                continue
            for column, value in zip(assignments, params[:len(assignments)]):
                row[column] = value
            touched += 1
        cursor.rowcount = touched

    # -- 小工具 -------------------------------------------------------------
    @staticmethod
    def _columns_of(text):
        """INSERT 的列清单：第一个 ``(`` 到 ``) VALUES`` 之间。"""
        head = text[text.index("(") + 1:text.index(") VALUES")]
        return [c.strip().strip("`") for c in head.split(",")]

    @staticmethod
    def _split_update(text):
        """拆 ``UPDATE t SET a = %s, b = %s WHERE ...`` → (列名, where 串)。"""
        body = text[text.index(" SET ") + 5:]
        if " WHERE " in body:
            set_part, where = body.split(" WHERE ", 1)
        else:
            set_part, where = body, ""
        columns = [piece.split("=")[0].strip().strip("`")
                   for piece in set_part.split(",")]
        return columns, where

    @staticmethod
    def _limit_of(text):
        if " LIMIT " not in text:
            return None
        tail = text.split(" LIMIT ", 1)[1]
        if " , " in tail:                       # PHP 的 'start , num' 写法
            start, num = tail.split(" , ")
            return int(start), int(num)
        return 0, int(tail)

    def _filter(self, rows, text, params):
        where = text.split(" WHERE ", 1)[1]
        return [row for row in rows if self._match(row, where, params)]

    #: WHERE 里的等值条件（`` `col` = %s ``）。SET 子句也有这个形状，
    #: 所以 :meth:`_match` 只接收拆好的 where 串。
    _EQ_RE = re.compile(r"`(\w+)`\s*=\s*%s")
    #: WHERE 里的 IN 列表（`` `col` IN (%s, %s) ``）
    _IN_RE = re.compile(r"`(\w+)`\s+IN\s+\(([^)]*)\)")

    def _match(self, row, where, params):
        """按 WHERE 串的形状判等值 / OR / IN（**OR 不是 AND**）。"""
        in_match = self._IN_RE.search(where)
        if in_match:
            column = in_match.group(1)
            count = in_match.group(2).count("%s")
            wanted = {str(value) for value in params[:count]}
            return str(row[column]) in wanted
        columns = self._EQ_RE.findall(where)
        if not columns:
            return True
        pairs = list(zip(columns, params))
        if " OR " in where:
            # 多值 OR（_buildWhereOr_groupitem 的括号串）：任一命中即可
            return any(str(row[column]) == str(value)
                       for column, value in pairs)
        # 单值/AND：全部命中
        return all(str(row[column]) == str(value)
                   for column, value in pairs)


def _row(item_id, quantity, price, fees, filled, remark="", row_id=None):
    return {"id": row_id, "groupitem_id": str(item_id),
            "quantity": quantity, "price": price, "fees": fees,
            "filled": filled, "remark": remark}


class LimitBuilderTest(unittest.TestCase):
    """``_php_build_limit`` 三分支（``_sqlcommon.php:63``）。"""

    def test_num_only(self):
        self.assertEqual(_php_build_limit(0, 10), "10")

    def test_start_and_num(self):
        self.assertEqual(_php_build_limit(20, 10), "20 , 10")

    def test_no_limit(self):
        self.assertEqual(_php_build_limit(0, 0), "")

    def test_string_numbers(self):
        """PHP 是松散比较，数字串也当数（``'10' == 10``）。"""
        self.assertEqual(_php_build_limit("0", "5"), "5")


class DebugDateTimeTest(unittest.TestCase):
    def test_shape_and_timezone(self):
        """``'Y-m-d H:i:s'`` 且是**上海时区**（DEBUG_TIME_ZONE）。"""
        import datetime

        # 2026-01-01 12:00 UTC = 2026-01-01 20:00 上海（无夏令时）
        moment = datetime.datetime(2026, 1, 1, 12, 0, 0,
                                   tzinfo=datetime.timezone.utc)
        self.assertEqual(debug_get_date_time(moment), "2026-01-01 20:00:00")

    def test_naive_moment_localized(self):
        import datetime

        moment = datetime.datetime(2026, 7, 1, 9, 30, 0)
        self.assertEqual(debug_get_date_time(moment), "2026-07-01 09:30:00")


class StockTransactionSqlTest(unittest.TestCase):
    def setUp(self):
        self.connection = FakeConnection([
            _row(1, 1000, 1.65, 5.0, "2026-10-15 10:30:00", "建仓", row_id=1),
            _row(1, -500, 1.72, 5.0, "2026-10-20 14:00:00", "波段减仓", row_id=2),
            _row(2, 500, 2.40, 5.0, "2026-10-16 09:45:00", "", row_id=3),
        ])
        self.sql = StockTransactionSql(self.connection)

    # -- 读 -----------------------------------------------------------------
    def test_get_all_orders_by_filled_desc(self):
        rows = self.sql.get_all([1])
        self.assertEqual([row["filled"] for row in rows],
                         ["2026-10-20 14:00:00", "2026-10-15 10:30:00"])

    def test_get_all_or_array(self):
        rows = self.sql.get_all([1, 2])
        self.assertEqual(len(rows), 3)

    def test_get_all_limit(self):
        rows = self.sql.get_all([1, 2], 1, 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["filled"], "2026-10-16 09:45:00")

    def test_get_all_empty_ids_returns_none(self):
        """PHP ``GetAll(array())`` → ``_buildWhereOr`` false → 返回 false。"""
        self.assertIsNone(self.sql.get_all([]))

    def test_get_record_wraps_single_id(self):
        rows = self.sql.get_record(1)
        self.assertEqual([row["id"] for row in rows], [2, 1])

    def test_count_all(self):
        self.assertEqual(self.sql.count_all([1]), 2)
        self.assertEqual(self.sql.count_all([1, 2]), 3)
        self.assertEqual(self.sql.count_all([]), 0)      # 不是 None

    def test_count_none_key_returns_none(self):
        """PHP ``Count(false)`` 不发 SQL、函数无 return → null。"""
        self.assertIsNone(self.sql.count(None))
        self.assertEqual(self.connection.queries, [])     # 真的没发 SQL

    def test_count(self):
        self.assertEqual(self.sql.count(1), 2)
        self.assertEqual(self.sql.count(99), 0)

    def test_get_record_by_id(self):
        """``TableSql::GetRecordById``（``sqltable.php:195``）：按主键取一行。"""
        record = self.sql.get_record_by_id(2)
        self.assertEqual(record["quantity"], -500)
        self.assertEqual(record["remark"], "波段减仓")

    def test_get_record_by_id_missing(self):
        self.assertIsNone(self.sql.get_record_by_id(99))

    def test_get_record_by_id_none_is_no_query(self):
        """PHP 的 ``_SqlBuildWhere_id(false)`` 给 false → 无 WHERE → 全表扫
        → 行数不为 1 → 回 false。这里照语义不发 SQL。"""
        self.assertIsNone(self.sql.get_record_by_id(None))
        self.assertEqual(self.connection.queries, [])

    # -- 写 -----------------------------------------------------------------
    def test_delete_returns_rowcount(self):
        self.assertEqual(self.sql.delete(2), 1)
        self.assertEqual(self.sql.count(2), 0)

    def test_delete_by_id_returns_rowcount(self):
        self.assertEqual(self.sql.delete_by_id(3), 1)
        self.assertIsNone(self.sql.get_record_by_id(3))

    def test_delete_by_id_missing_returns_zero(self):
        self.assertEqual(self.sql.delete_by_id(99), 0)

    def test_delete_by_id_none_is_no_query(self):
        self.assertIsNone(self.sql.delete_by_id(None))
        self.assertEqual(self.connection.queries, [])

    def test_delete_missing_returns_zero(self):
        self.assertEqual(self.sql.delete(99), 0)

    def test_delete_none_key_returns_none(self):
        self.assertIsNone(self.sql.delete(None))
        self.assertEqual(self.connection.queries, [])

    def test_insert_generates_filled(self):
        before = debug_get_date_time()
        self.sql.insert(2, 300, 2.45, 5.0, "补仓")
        row = self.connection.rows[-1]
        self.assertEqual(row["groupitem_id"], 2)
        self.assertEqual(row["quantity"], 300)
        self.assertEqual(row["price"], 2.45)
        self.assertEqual(row["fees"], 5.0)
        self.assertEqual(row["remark"], "补仓")
        self.assertGreaterEqual(row["filled"], before)   # 自动生成
        self.assertIn("INSERT INTO `stocktransaction`", self.connection.queries[-1])

    def test_insert_default_fees_and_remark(self):
        """PHP 签名默认值 ``fees='0.0'`` / ``remark=''``。"""
        self.sql.insert(1, 100, 1.60)
        row = self.connection.rows[-1]
        self.assertEqual(row["fees"], "0.0")
        self.assertEqual(row["remark"], "")

    def test_update_does_not_touch_filled(self):
        self.sql.update(1, 1, 1200, 1.70, 6.0, "改单")
        row = self.connection.rows[0]
        self.assertEqual(row["quantity"], 1200)
        self.assertEqual(row["price"], 1.70)
        self.assertEqual(row["fees"], 6.0)
        self.assertEqual(row["remark"], "改单")
        self.assertEqual(row["filled"], "2026-10-15 10:30:00")   # 没动
        self.assertIn("LIMIT 1", self.connection.queries[-1])
        self.assertNotIn("filled", self.connection.queries[-1])

    def test_update_default_remark(self):
        self.sql.update(1, 1, 1200, 1.70, 6.0)
        self.assertEqual(self.connection.rows[0]["remark"], "")

    def test_merge_moves_records(self):
        self.assertEqual(self.sql.merge(1, 2), 2)
        self.assertEqual(self.sql.count(1), 0)
        self.assertEqual(self.sql.count(2), 3)

    def test_merge_none_source_returns_none(self):
        self.assertIsNone(self.sql.merge(None, 2))
        self.assertEqual(self.connection.queries, [])

    def test_delete_all_by_ids(self):
        self.assertEqual(self.sql.delete_all_by_ids([1, 2]), 3)
        self.assertEqual(self.sql.delete_all_by_ids([]), 0)


if __name__ == "__main__":
    unittest.main()
