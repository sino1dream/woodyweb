"""``core.stockgroupsql`` 的单元测试。

对应 PHP ``php/sql/sqlstockgroup.php`` 的 ``StockGroupSql`` /
``StockGroupItemSql`` 与模块级函数。用内存 fake 连接（不连真库），
dispatch 本模块会发出的那几种固定形态 SQL。

重点验证的语义（都是迁移时容易改错的点）：

1. ``get_stock_id_array`` 的键是**字符串**（PHP 数字字符串键的坑，
   pitfalls #71/#72 家族）；
2. ``check_transaction=True`` 时跳过 ``record == 0`` 的成员；
3. ``sql_update_stock_group`` 的 diff 方向（只插新的、只删旧的、
   不动共有的）；
4. ``sql_get_stocks_array`` **不排序**（保持 id 顺序，PHP 注释明令）；
5. ``get_input_stock_id_array`` 的切分 / 大小写归一 / ref 兜底；
6. ``insert_string`` 重复名返回 None（PHP 返回 false）。
"""

import unittest

from core.stockgroupsql import (
    StockGroupItemSql,
    StockGroupSql,
    get_input_stock_id_array,
    is_group_name_reserved,
    prefix_reserved_group_name,
    sql_delete_stock_group,
    sql_delete_stock_group_by_member_id,
    sql_get_my_stock_group_item_id,
    sql_get_stock_group_id,
    sql_get_stock_group_item_id,
    sql_get_stocks_array,
    sql_update_stock_group,
)


class FakeCursor:
    """只认本模块生成的几种 SQL；不认识的直接抛，防止测试静默通过。"""

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
        handler = _HANDLERS.get(sql.split(" WHERE ")[0].split(" ORDER BY ")[0])
        # 精确匹配优先：先把整条 SQL 归一成 key
        key = _normalize(sql)
        if key in _HANDLERS:
            handler = _HANDLERS[key]
        elif handler is None:
            raise AssertionError("测试 fake 不认识的 SQL: %s" % sql)
        handler(self, sql, params)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def close(self):
        pass


def _normalize(sql):
    """把参数占位符统一成 %s 后的形态做 key（本模块全是 %s）。"""
    return " ".join(sql.split())


def _h_select_group_by_member_name(cursor, sql, params):
    member_id, group_name = params
    rows = [r for r in cursor.connection.data["stockgroup"]
            if str(r["member_id"]) == str(member_id)
            and r["groupname"] == group_name]
    cursor.rows = rows[:1]


def _h_select_group_by_id(cursor, sql, params):
    group_id = params[0]
    cursor.rows = [r for r in cursor.connection.data["stockgroup"]
                   if str(r["id"]) == str(group_id)][:1]


def _h_select_group_by_member(cursor, sql, params):
    member_id = params[0]
    rows = [r for r in cursor.connection.data["stockgroup"]
            if str(r["member_id"]) == str(member_id)]
    rows.sort(key=lambda r: r["groupname"])
    cursor.rows = rows


def _h_count_group_by_name(cursor, sql, params):
    group_name = params[0]
    n = sum(1 for r in cursor.connection.data["stockgroup"]
            if r["groupname"] == group_name)
    cursor.rows = [{"n": n}]


def _h_insert_group(cursor, sql, params):
    member_id, group_name = params
    table = cursor.connection.data["stockgroup"]
    for r in table:
        if str(r["member_id"]) == str(member_id) and r["groupname"] == group_name:
            return
    new_id = cursor.connection.next_id("stockgroup")
    table.append({"id": new_id, "member_id": member_id, "groupname": group_name})
    cursor.rowcount = 1
    cursor.lastrowid = new_id


def _h_update_group_name(cursor, sql, params):
    group_name, group_id = params
    for r in cursor.connection.data["stockgroup"]:
        if str(r["id"]) == str(group_id):
            r["groupname"] = group_name
            cursor.rowcount = 1


def _h_delete_group_by_id(cursor, sql, params):
    group_id = params[0]
    table = cursor.connection.data["stockgroup"]
    before = len(table)
    cursor.connection.data["stockgroup"] = [
        r for r in table if str(r["id"]) != str(group_id)]
    cursor.rowcount = before - len(cursor.connection.data["stockgroup"])


def _h_delete_group_by_name(cursor, sql, params):
    group_name = params[0]
    table = cursor.connection.data["stockgroup"]
    before = len(table)
    cursor.connection.data["stockgroup"] = [
        r for r in table if r["groupname"] != group_name]
    cursor.rowcount = before - len(cursor.connection.data["stockgroup"])


def _h_select_item_by_group(cursor, sql, params):
    group_id = params[0]
    rows = [r for r in cursor.connection.data["stockgroupitem"]
            if str(r["stockgroup_id"]) == str(group_id)]
    rows.sort(key=lambda r: int(r["id"]))
    cursor.rows = rows


def _h_select_item_by_id(cursor, sql, params):
    item_id = params[0]
    cursor.rows = [r for r in cursor.connection.data["stockgroupitem"]
                   if str(r["id"]) == str(item_id)][:1]


def _h_select_item_by_group_stock(cursor, sql, params):
    group_id, stock_id = params
    cursor.rows = [r for r in cursor.connection.data["stockgroupitem"]
                   if str(r["stockgroup_id"]) == str(group_id)
                   and str(r["stock_id"]) == str(stock_id)][:1]


def _h_count_item_by_group(cursor, sql, params):
    group_id = params[0]
    n = sum(1 for r in cursor.connection.data["stockgroupitem"]
            if str(r["stockgroup_id"]) == str(group_id))
    cursor.rows = [{"n": n}]


def _h_count_item_by_stock(cursor, sql, params):
    stock_id = params[0]
    n = sum(1 for r in cursor.connection.data["stockgroupitem"]
            if str(r["stock_id"]) == str(stock_id))
    cursor.rows = [{"n": n}]


def _h_insert_item(cursor, sql, params):
    group_id, stock_id = params
    table = cursor.connection.data["stockgroupitem"]
    for r in table:
        if (str(r["stockgroup_id"]) == str(group_id)
                and str(r["stock_id"]) == str(stock_id)):
            return
    new_id = cursor.connection.next_id("stockgroupitem")
    table.append({"id": new_id, "stockgroup_id": group_id, "stock_id": stock_id,
                  "quantity": 0, "cost": 0.0, "record": 0})
    cursor.rowcount = 1
    cursor.lastrowid = new_id


def _h_update_item(cursor, sql, params):
    quantity, cost, record, item_id = params
    for r in cursor.connection.data["stockgroupitem"]:
        if str(r["id"]) == str(item_id):
            r["quantity"] = quantity
            r["cost"] = cost
            r["record"] = record
            cursor.rowcount = 1


def _h_delete_item_by_id(cursor, sql, params):
    item_id = params[0]
    table = cursor.connection.data["stockgroupitem"]
    before = len(table)
    cursor.connection.data["stockgroupitem"] = [
        r for r in table if str(r["id"]) != str(item_id)]
    cursor.rowcount = before - len(cursor.connection.data["stockgroupitem"])


def _h_delete_item_by_group(cursor, sql, params):
    group_id = params[0]
    table = cursor.connection.data["stockgroupitem"]
    before = len(table)
    cursor.connection.data["stockgroupitem"] = [
        r for r in table if str(r["stockgroup_id"]) != str(group_id)]
    cursor.rowcount = before - len(cursor.connection.data["stockgroupitem"])


def _h_select_symbol_by_id(cursor, sql, params):
    stock_id = params[0]
    cursor.rows = [r for r in cursor.connection.data["stock"]
                   if str(r["id"]) == str(stock_id)][:1]


def _h_select_id_by_symbol(cursor, sql, params):
    symbol = params[0]
    cursor.rows = [r for r in cursor.connection.data["stock"]
                   if r["symbol"] == symbol][:1]


_HANDLERS = {
    _normalize("SELECT * FROM stockgroup WHERE member_id = %s AND groupname = %s LIMIT 1"):
        _h_select_group_by_member_name,
    _normalize("SELECT * FROM stockgroup WHERE id = %s LIMIT 1"):
        _h_select_group_by_id,
    _normalize("SELECT * FROM stockgroup WHERE member_id = %s ORDER BY groupname ASC"):
        _h_select_group_by_member,
    _normalize("SELECT COUNT(*) AS n FROM stockgroup WHERE groupname = %s"):
        _h_count_group_by_name,
    _normalize("INSERT INTO stockgroup (member_id, groupname) VALUES (%s, %s)"):
        _h_insert_group,
    _normalize("UPDATE stockgroup SET groupname = %s WHERE id = %s LIMIT 1"):
        _h_update_group_name,
    _normalize("DELETE FROM stockgroup WHERE id = %s"):
        _h_delete_group_by_id,
    _normalize("DELETE FROM stockgroup WHERE groupname = %s"):
        _h_delete_group_by_name,
    _normalize("SELECT * FROM stockgroupitem WHERE stockgroup_id = %s ORDER BY id ASC"):
        _h_select_item_by_group,
    _normalize("SELECT * FROM stockgroupitem WHERE stockgroup_id = %s ORDER BY id ASC LIMIT 10"):
        _h_select_item_by_group,
    _normalize("SELECT * FROM stockgroupitem WHERE stockgroup_id = %s ORDER BY id ASC LIMIT 5 , 10"):
        _h_select_item_by_group,
    _normalize("SELECT * FROM stockgroupitem WHERE id = %s LIMIT 1"):
        _h_select_item_by_id,
    _normalize("SELECT * FROM stockgroupitem WHERE stockgroup_id = %s AND stock_id = %s LIMIT 1"):
        _h_select_item_by_group_stock,
    _normalize("SELECT COUNT(*) AS n FROM stockgroupitem WHERE stockgroup_id = %s"):
        _h_count_item_by_group,
    _normalize("SELECT COUNT(*) AS n FROM stockgroupitem WHERE stock_id = %s"):
        _h_count_item_by_stock,
    _normalize("INSERT INTO stockgroupitem (stockgroup_id, stock_id, quantity, cost, record) VALUES (%s, %s, 0, 0.0, 0)"):
        _h_insert_item,
    _normalize("UPDATE stockgroupitem SET quantity = %s, cost = %s, record = %s WHERE id = %s LIMIT 1"):
        _h_update_item,
    _normalize("DELETE FROM stockgroupitem WHERE id = %s"):
        _h_delete_item_by_id,
    _normalize("DELETE FROM stockgroupitem WHERE stockgroup_id = %s"):
        _h_delete_item_by_group,
    _normalize("SELECT symbol FROM stock WHERE id = %s LIMIT 1"):
        _h_select_symbol_by_id,
    _normalize("SELECT id FROM stock WHERE symbol = %s LIMIT 1"):
        _h_select_id_by_symbol,
}


class FakeConnection:
    def __init__(self):
        self.queries = []
        self._next = {"stockgroup": 1, "stockgroupitem": 1}
        self.data = {
            "stockgroup": [],
            "stockgroupitem": [],
            "stock": [
                {"id": "2", "symbol": "SZ161125"},
                {"id": "3", "symbol": "SH513100"},
                {"id": "41", "symbol": "QQQ"},
                {"id": "42", "symbol": "SPY"},
            ],
        }

    def next_id(self, table):
        # 从表内现有 max(id)+1 起算 —— 预置数据的 id 可能是任意值，
        # 固定从 1 开始会撞 UNIQUE，让 insert 静默不生效
        existing = [int(r["id"]) for r in self.data[table]]
        value = max(existing, default=0) + 1
        return value

    def cursor(self):
        return FakeCursor(self)


class StockGroupSqlTest(unittest.TestCase):
    def setUp(self):
        self.conn = FakeConnection()
        self.sql = StockGroupSql(self.conn)
        self.conn.data["stockgroup"] = [
            {"id": 1, "member_id": 1, "groupname": "分组B"},
            {"id": 2, "member_id": 1, "groupname": "分组A"},
            {"id": 3, "member_id": 2, "groupname": "别人的"},
        ]

    def test_get_group_id_hit_and_miss(self):
        self.assertEqual(self.sql.get_group_id(1, "分组A"), "2")
        self.assertIsNone(self.sql.get_group_id(1, "不存在"))

    def test_get_all_orders_by_groupname(self):
        # KeyStringSql::BuildOrderBy() 是 groupname ASC
        names = [r["groupname"] for r in self.sql.get_all(1)]
        self.assertEqual(names, ["分组A", "分组B"])

    def test_insert_string_and_duplicate(self):
        new_id = self.sql.insert_string(1, "新分组")
        self.assertEqual(new_id, 4)
        self.assertIsNone(self.sql.insert_string(1, "新分组"))

    def test_update_string(self):
        self.assertEqual(self.sql.update_string(2, "改名"), 1)
        self.assertEqual(self.sql.get_string(2), "改名")

    def test_delete_by_id(self):
        self.assertEqual(self.sql.delete_by_id(2), 1)
        self.assertIsNone(self.sql.get_record_by_id(2))

    def test_count_by_string(self):
        self.assertEqual(self.sql.count_by_string("分组A"), 1)
        self.assertEqual(self.sql.count_by_string("无"), 0)

    def test_delete_by_string_empty_guard(self):
        # PHP DeleteByString 的 if ($strString) 守卫
        self.assertEqual(self.sql.delete_by_string(""), 0)


class StockGroupItemSqlTest(unittest.TestCase):
    def setUp(self):
        self.conn = FakeConnection()
        self.items = StockGroupItemSql(self.conn, group_id=1)
        self.conn.data["stockgroupitem"] = [
            {"id": 1, "stockgroup_id": 1, "stock_id": "2", "quantity": 0,
             "cost": 0.0, "record": 0},
            {"id": 2, "stockgroup_id": 1, "stock_id": "3", "quantity": 10,
             "cost": 1.234, "record": 2},
            {"id": 3, "stockgroup_id": 2, "stock_id": "41", "quantity": 0,
             "cost": 0.0, "record": 0},
        ]

    def test_get_all_only_own_group(self):
        rows = self.items.get_all()
        self.assertEqual([r["id"] for r in rows], [1, 2])

    def test_stock_id_array_keys_are_strings(self):
        # PHP $ar[$record['id']] 数字字符串键的坑：Python 侧必须 str
        mapping = self.items.get_stock_id_array()
        self.assertEqual(mapping, {"1": "2", "2": "3"})
        for key in mapping:
            self.assertIsInstance(key, str)

    def test_stock_id_array_skips_zero_record(self):
        mapping = self.items.get_stock_id_array(check_transaction=True)
        self.assertEqual(mapping, {"2": "3"})

    def test_insert_update_delete(self):
        new_id = self.items.insert("41")
        self.assertEqual(new_id, 4)
        self.assertEqual(self.items.update(new_id, 5, "1.5", 3), 1)
        row = self.items.get_record_by_id(new_id)
        self.assertEqual((row["quantity"], row["cost"], row["record"]), (5, 1.5, 3))
        self.assertEqual(self.items.delete_by_id(new_id), 1)
        self.assertIsNone(self.items.get_record_by_id(new_id))

    def test_get_id_and_record(self):
        self.assertEqual(self.items.get_id("3"), "2")
        self.assertEqual(self.items.get_record("3")["quantity"], 10)
        self.assertIsNone(self.items.get_record("41"))   # 别的组的

    def test_count_by_stock_id_ignores_group(self):
        # WHERE 只有 stock_id（_SqlBuildWhere_stock），不限本组
        self.assertEqual(self.items.count_by_stock_id("41"), 1)

    def test_delete_all(self):
        self.assertEqual(self.items.delete_all(), 2)
        self.assertEqual(self.items.get_all(), [])

    def test_get_all_limit_branches(self):
        # _SqlBuildLimit 的三个分支（PHP 逐例对照的 limit_* 三例）：
        # (0,0) 无 LIMIT；(0,n) LIMIT n；(s,n) LIMIT s , n（逗号两侧有空格）
        self.items.get_all(0, 0)
        self.assertNotIn("LIMIT", self.conn.queries[-1])
        self.items.get_all(0, 10)
        self.assertTrue(self.conn.queries[-1].endswith("LIMIT 10"))
        self.items.get_all(5, 10)
        self.assertTrue(self.conn.queries[-1].endswith("LIMIT 5 , 10"))

    def test_no_group_instance_guards(self):
        loose = StockGroupItemSql(self.conn, group_id=None)
        self.assertEqual(loose.get_all(), [])
        self.assertEqual(loose.delete_all(), 0)
        self.assertIsNone(loose.insert("2"))


class ModuleFunctionTest(unittest.TestCase):
    def setUp(self):
        self.conn = FakeConnection()
        self.conn.data["stockgroup"] = [
            {"id": 1, "member_id": 1, "groupname": "分组A"},
        ]
        self.conn.data["stockgroupitem"] = [
            {"id": 1, "stockgroup_id": 1, "stock_id": "2", "quantity": 0,
             "cost": 0.0, "record": 0},
        ]

    def test_update_stock_group_diff(self):
        # 2 已有；41 新增；插入顺序=new 顺序
        stats = sql_update_stock_group(self.conn, 1, ["41", "2"])
        self.assertEqual(stats["added"], ["41"])
        self.assertEqual(stats["removed"], [])

        # 删掉 2、加 42；41 不动
        stats = sql_update_stock_group(self.conn, 1, ["41", "42"])
        self.assertEqual(stats["added"], ["42"])
        self.assertEqual(stats["removed"], ["2"])
        remaining = [r["stock_id"] for r in self.conn.data["stockgroupitem"]]
        self.assertEqual(remaining, ["41", "42"])

    def test_get_stocks_array_keeps_order(self):
        # PHP 注释明令不能 sort：顺序 = item 的 id 顺序
        self.assertEqual(sql_get_stocks_array(self.conn, 1), ["SZ161125"])

    def test_delete_stock_group_items_first(self):
        stats = sql_delete_stock_group(self.conn, 1)
        self.assertEqual(stats, {"items": 1, "group": 1})
        self.assertEqual(self.conn.data["stockgroupitem"], [])
        self.assertEqual(self.conn.data["stockgroup"], [])

    def test_delete_by_member_id(self):
        self.conn.data["stockgroup"].append(
            {"id": 2, "member_id": 1, "groupname": "分组B"})
        self.assertEqual(sql_delete_stock_group_by_member_id(self.conn, 1), 2)
        self.assertEqual(self.conn.data["stockgroup"], [])

    def test_get_stock_group_id(self):
        self.assertEqual(sql_get_stock_group_id(self.conn, 1), "1")
        self.assertIsNone(sql_get_stock_group_id(self.conn, 99))

    def test_get_stock_group_item_id(self):
        self.assertEqual(sql_get_stock_group_item_id(self.conn, 1, "2"), "1")
        self.assertIsNone(sql_get_stock_group_item_id(self.conn, 1, "42"))

    def test_get_my_stock_group_item_id(self):
        # 分组名 == 股票符号的自选路径
        self.conn.data["stockgroup"].append(
            {"id": 5, "member_id": 1, "groupname": "QQQ"})
        self.conn.data["stockgroupitem"].append(
            {"id": 9, "stockgroup_id": 5, "stock_id": "41", "quantity": 0,
             "cost": 0.0, "record": 0})
        self.assertEqual(sql_get_my_stock_group_item_id(self.conn, 1, "41"), "9")


class InputSymbolParseTest(unittest.TestCase):
    """``_getStockIdArray`` 的切分与大小写归一。"""

    def setUp(self):
        self.conn = FakeConnection()

    def test_mixed_separators(self):
        ids = get_input_stock_id_array(self.conn, "SZ161125, SH513100\nQQQ\tSPY")
        self.assertEqual(ids, ["2", "3", "41", "42"])

    def test_lowercase_normalized(self):
        # PHP GetInputSymbolArray 对无下划线符号 strtoupper
        self.assertEqual(get_input_stock_id_array(self.conn, "sz161125"), ["2"])

    def test_underscore_symbol_untouched(self):
        # 外汇符号 fx_susdcny 不能大写；stock 表里没有它 → 跳过
        self.assertEqual(get_input_stock_id_array(self.conn, "fx_susdcny"), [])

    def test_unknown_symbol_skipped(self):
        self.assertEqual(get_input_stock_id_array(self.conn, "NOSUCH"), [])

    def test_duplicates_collapsed(self):
        self.assertEqual(get_input_stock_id_array(self.conn, "QQQ, qqq"), ["41"])

    def test_ref_builder_fallback(self):
        # 库里没有的符号：ref_builder 建出带数据的 ref 就收（照 PHP）
        class FakeRef:
            has_data = True

            def get_stock_id(self):
                return "99"

        ids = get_input_stock_id_array(self.conn, "TLT", ref_builder=lambda s: FakeRef())
        self.assertEqual(ids, ["99"])

    def test_ref_builder_no_data_skipped(self):
        class FakeRef:
            has_data = False

            def get_stock_id(self):
                return "99"

        self.assertEqual(get_input_stock_id_array(
            self.conn, "TLT", ref_builder=lambda s: FakeRef()), [])


class GroupNameGuardTest(unittest.TestCase):
    def test_reserved_detection(self):
        self.assertTrue(is_group_name_reserved("QQQ", ["QQQ", "SPY"]))
        self.assertFalse(is_group_name_reserved("我的分组", ["QQQ", "SPY"]))

    def test_prefix_once(self):
        self.assertEqual(prefix_reserved_group_name("QQQ"), "@QQQ")
        self.assertEqual(prefix_reserved_group_name("@QQQ"), "@QQQ")


if __name__ == "__main__":
    unittest.main()
