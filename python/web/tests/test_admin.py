"""后台管理页（``/admin/*``）的路由测试。

对应 PHP ``woody/res/editstockgroupcn.php`` / ``submitgroup.php`` /
``_submitgroup.php``。**不连库**：数据源走 ``admin_source_factory``
注入的内存 fake（SQL 在 fake 里解释执行，覆盖路由真正会说的话）。

钉的事：

1. 列表页渲染全部分组与成员符号；
2. 新建：``insert_string`` + 逐成员 insert，重名保护（符号同名加 ``@``）；
3. 编辑：``update_string`` + diff（只插新的、只删旧的）；
4. 删除：POST、先删成员再删组（MyISAM 无级联）；
5. 符号解析：跳过的符号在 flash 里报数（不静默）；
6. 编辑表单对"组名撞符号"的分组禁用组名输入（PHP 的
   ``$strGroupNameDisabled``）。
"""

import re
import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[3]
PYTHON_DIR = ROOT_DIR / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from web.bootstrap import install_paths  # noqa: E402

install_paths()

from web.app import create_app  # noqa: E402


class FakeSource:
    """内存版数据源：解释 core.stockgroupsql 会发的 SQL。

    只实现路由用到的查询/写入；``stock`` 表预置几个符号。
    """

    def __init__(self):
        self.stock = [
            {"id": 2, "symbol": "SZ161125"},
            {"id": 3, "symbol": "SH513100"},
            {"id": 41, "symbol": "QQQ"},
            {"id": 42, "symbol": "SPY"},
        ]
        self.stockgroup = [
            {"id": 1, "member_id": 1, "groupname": "演示分组A"},
        ]
        self.stockgroupitem = [
            {"id": 1, "stockgroup_id": 1, "stock_id": 2, "quantity": 0,
             "cost": 0.0, "record": 0},
        ]
        #: 2026-10-24 ``stocktransaction`` 迁完后删成员/删分组会**级联删
        #: 交易**（``sql_update_stock_group`` 移除分支与
        #: ``sql_delete_stock_group_item_by_group_id``）。假库要认这两条 SQL。
        self.stocktransaction = [
            {"id": 1, "groupitem_id": 1, "quantity": "1000", "price": "1.65",
             "fees": "0.165", "filled": "2026-10-15 10:30:00", "remark": ""},
            {"id": 2, "groupitem_id": 1, "quantity": "-500", "price": "1.72",
             "fees": "1.29", "filled": "2026-10-20 14:00:00", "remark": ""},
        ]
        self._next = {"stockgroup": 2, "stockgroupitem": 2}

    # -- MySqlFundDataSource 的两个方法（路由与 stockgroupsql 都用） --------
    def get_stock_id(self, symbol):
        for row in self.stock:
            if row["symbol"] == symbol:
                return str(row["id"])
        return None

    def get_stock_symbol(self, stock_id):
        for row in self.stock:
            if str(row["id"]) == str(stock_id):
                return row["symbol"]
        return None

    # -- DB-API 外观（core.stockgroupsql 直接拿 .connection） ---------------
    @property
    def connection(self):
        return self

    def cursor(self):
        return FakeCursor(self)

    def _next_id(self, table):
        value = self._next[table]
        self._next[table] = value + 1
        return value


class FakeCursor:
    def __init__(self, source):
        self.source = source
        self._rows = []
        self.rowcount = 0
        self.lastrowid = 0

    def execute(self, sql, params=()):
        self._rows = []
        self.rowcount = 0
        self.lastrowid = 0
        squeezed = " ".join(sql.split())
        handler = _HANDLERS.get(squeezed)
        if handler is None:
            # 占位符个数随成员数变的语句（IN 列表）只能按形状匹配
            for pattern, candidate in _REGEX_HANDLERS:
                if pattern.match(squeezed):
                    handler = candidate
                    break
        if handler is None:
            raise AssertionError("测试 fake 不认识的 SQL: %s" % sql)
        handler(self, params)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass


def _rows(source, table):
    return source.__dict__[table]


def _h_group_by_member_name(cursor, params):
    member_id, group_name = params
    cursor._rows = [r for r in _rows(cursor.source, "stockgroup")
                    if str(r["member_id"]) == str(member_id)
                    and r["groupname"] == group_name][:1]


def _h_group_by_id(cursor, params):
    group_id = params[0]
    cursor._rows = [r for r in _rows(cursor.source, "stockgroup")
                    if str(r["id"]) == str(group_id)][:1]


def _h_group_by_member(cursor, params):
    member_id = params[0]
    rows = [r for r in _rows(cursor.source, "stockgroup")
            if str(r["member_id"]) == str(member_id)]
    rows.sort(key=lambda r: r["groupname"])
    cursor._rows = rows


def _h_count_group_by_name(cursor, params):
    group_name = params[0]
    n = sum(1 for r in _rows(cursor.source, "stockgroup")
            if r["groupname"] == group_name)
    cursor._rows = [{"n": n}]


def _h_insert_group(cursor, params):
    member_id, group_name = params
    table = cursor.source.stockgroup
    for r in table:
        if str(r["member_id"]) == str(member_id) and r["groupname"] == group_name:
            return
    new_id = cursor.source._next_id("stockgroup")
    table.append({"id": new_id, "member_id": member_id, "groupname": group_name})
    cursor.rowcount = 1
    cursor.lastrowid = new_id


def _h_update_group_name(cursor, params):
    group_name, group_id = params
    for r in cursor.source.stockgroup:
        if str(r["id"]) == str(group_id):
            r["groupname"] = group_name
            cursor.rowcount = 1


def _h_delete_group_by_id(cursor, params):
    group_id = params[0]
    table = cursor.source.stockgroup
    before = len(table)
    cursor.source.stockgroup = [r for r in table if str(r["id"]) != str(group_id)]
    cursor.rowcount = before - len(cursor.source.stockgroup)


def _h_item_by_group(cursor, params):
    group_id = params[0]
    rows = [r for r in cursor.source.stockgroupitem
            if str(r["stockgroup_id"]) == str(group_id)]
    rows.sort(key=lambda r: int(r["id"]))
    cursor._rows = rows


def _h_item_by_id(cursor, params):
    item_id = params[0]
    cursor._rows = [r for r in cursor.source.stockgroupitem
                    if str(r["id"]) == str(item_id)][:1]


def _h_item_by_group_stock(cursor, params):
    group_id, stock_id = params
    cursor._rows = [r for r in cursor.source.stockgroupitem
                    if str(r["stockgroup_id"]) == str(group_id)
                    and str(r["stock_id"]) == str(stock_id)][:1]


def _h_count_item_by_group(cursor, params):
    group_id = params[0]
    n = sum(1 for r in cursor.source.stockgroupitem
            if str(r["stockgroup_id"]) == str(group_id))
    cursor._rows = [{"n": n}]


def _h_count_item_by_stock(cursor, params):
    stock_id = params[0]
    n = sum(1 for r in cursor.source.stockgroupitem
            if str(r["stock_id"]) == str(stock_id))
    cursor._rows = [{"n": n}]


def _h_insert_item(cursor, params):
    group_id, stock_id = params
    table = cursor.source.stockgroupitem
    for r in table:
        if (str(r["stockgroup_id"]) == str(group_id)
                and str(r["stock_id"]) == str(stock_id)):
            return
    new_id = cursor.source._next_id("stockgroupitem")
    table.append({"id": new_id, "stockgroup_id": group_id, "stock_id": stock_id,
                  "quantity": 0, "cost": 0.0, "record": 0})
    cursor.rowcount = 1
    cursor.lastrowid = new_id


def _h_delete_item_by_id(cursor, params):
    item_id = params[0]
    table = cursor.source.stockgroupitem
    before = len(table)
    cursor.source.stockgroupitem = [
        r for r in table if str(r["id"]) != str(item_id)]
    cursor.rowcount = before - len(cursor.source.stockgroupitem)


def _h_delete_item_by_group(cursor, params):
    group_id = params[0]
    table = cursor.source.stockgroupitem
    before = len(table)
    cursor.source.stockgroupitem = [
        r for r in table if str(r["stockgroup_id"]) != str(group_id)]
    cursor.rowcount = before - len(cursor.source.stockgroupitem)


def _h_all_symbols(cursor, params):
    cursor._rows = [{"symbol": r["symbol"]} for r in cursor.source.stock]


# -- stocktransaction（删成员/删分组的级联清理） -----------------------------
def _h_trans_count_by_item(cursor, params):
    """``StockTransactionSql.count`` 的 COUNT 前置（``DeleteData`` 同款）。"""
    n = sum(1 for r in cursor.source.stocktransaction
            if str(r["groupitem_id"]) == str(params[0]))
    cursor._rows = [{"n": n}]


def _h_trans_delete_by_item(cursor, params):
    item_id = params[0]
    table = cursor.source.stocktransaction
    before = len(table)
    cursor.source.stocktransaction = [
        r for r in table if str(r["groupitem_id"]) != str(item_id)]
    cursor.rowcount = before - len(cursor.source.stocktransaction)


def _h_trans_delete_in(cursor, params):
    """``delete_all_by_ids``：``groupitem_id IN (%s, %s, ...)``。"""
    wanted = {str(value) for value in params}
    table = cursor.source.stocktransaction
    before = len(table)
    cursor.source.stocktransaction = [
        r for r in table if str(r["groupitem_id"]) not in wanted]
    cursor.rowcount = before - len(cursor.source.stocktransaction)


_HANDLERS = {
    " ".join("SELECT * FROM stockgroup WHERE member_id = %s AND groupname = %s LIMIT 1".split()):
        _h_group_by_member_name,
    " ".join("SELECT * FROM stockgroup WHERE id = %s LIMIT 1".split()):
        _h_group_by_id,
    " ".join("SELECT * FROM stockgroup WHERE member_id = %s ORDER BY groupname ASC".split()):
        _h_group_by_member,
    " ".join("SELECT COUNT(*) AS n FROM stockgroup WHERE groupname = %s".split()):
        _h_count_group_by_name,
    " ".join("INSERT INTO stockgroup (member_id, groupname) VALUES (%s, %s)".split()):
        _h_insert_group,
    " ".join("UPDATE stockgroup SET groupname = %s WHERE id = %s LIMIT 1".split()):
        _h_update_group_name,
    " ".join("DELETE FROM stockgroup WHERE id = %s".split()):
        _h_delete_group_by_id,
    " ".join("SELECT * FROM stockgroupitem WHERE stockgroup_id = %s ORDER BY id ASC".split()):
        _h_item_by_group,
    " ".join("SELECT * FROM stockgroupitem WHERE id = %s LIMIT 1".split()):
        _h_item_by_id,
    " ".join("SELECT * FROM stockgroupitem WHERE stockgroup_id = %s AND stock_id = %s LIMIT 1".split()):
        _h_item_by_group_stock,
    " ".join("SELECT COUNT(*) AS n FROM stockgroupitem WHERE stockgroup_id = %s".split()):
        _h_count_item_by_group,
    " ".join("SELECT COUNT(*) AS n FROM stockgroupitem WHERE stock_id = %s".split()):
        _h_count_item_by_stock,
    " ".join("INSERT INTO stockgroupitem (stockgroup_id, stock_id, quantity, cost, record) VALUES (%s, %s, 0, 0.0, 0)".split()):
        _h_insert_item,
    " ".join("DELETE FROM stockgroupitem WHERE id = %s".split()):
        _h_delete_item_by_id,
    " ".join("DELETE FROM stockgroupitem WHERE stockgroup_id = %s".split()):
        _h_delete_item_by_group,
    " ".join("SELECT symbol FROM stock".split()):
        _h_all_symbols,
    " ".join("SELECT COUNT(*) AS n FROM `stocktransaction` WHERE `groupitem_id` = %s".split()):
        _h_trans_count_by_item,
    " ".join("DELETE FROM `stocktransaction` WHERE `groupitem_id` = %s".split()):
        _h_trans_delete_by_item,
}

#: 占位符个数随入参变的语句（``IN (%s, %s, ...)``）按形状匹配。
_REGEX_HANDLERS = [
    (re.compile(r"^DELETE FROM `stocktransaction` WHERE `groupitem_id` IN "
                r"\((?:%s(?:, %s)*)\)$"), _h_trans_delete_in),
]


def _squeezed(html):
    return re.sub(r"\s+", " ", html)


class AdminRouteTest(unittest.TestCase):
    def setUp(self):
        self.source = FakeSource()
        self.app = create_app(
            {"TESTING": True},
            admin_source_factory=lambda: self.source)
        self.client = self.app.test_client()

    # -- 列表 ---------------------------------------------------------------
    def test_bare_admin_renders_console(self):
        """``/admin`` 与 ``/admin/`` 是后台控制台，不是 404、也不再 302。

        2026-11-04 之前这里是 302 跳 ``/admin/groups``。换成控制台是因为
        分组只是后台能力的一小块，而「生产端只能手敲 URL」那 20 个页面
        没有别的地方能发现它们（见 ``tests/test_admin_console.py``）。
        """
        for path in ("/admin", "/admin/"):
            response = self.client.get(path)
            self.assertEqual(200, response.status_code, path)
            self.assertIn("后台 · 控制台", response.get_data(as_text=True), path)

    def test_groups_list(self):
        response = self.client.get("/admin/groups")
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn("演示分组A", html)
        self.assertIn("SZ161125", html)
        # 常驻的演示环境说明（登录态未接）
        self.assertIn("member_id=1", html)

    def test_groups_list_empty(self):
        self.source.stockgroup = []
        self.source.stockgroupitem = []
        response = self.client.get("/admin/groups")
        self.assertIn("还没有分组", response.get_data(as_text=True))

    # -- 新建 ---------------------------------------------------------------
    def test_new_form_prefills_symbol(self):
        # PHP ?new=QQQ：组名 @QQQ、成员 QQQ
        response = self.client.get("/admin/group/new?symbol=QQQ")
        html = response.get_data(as_text=True)
        self.assertIn('value="@QQQ"', html)
        self.assertIn(">QQQ<", html)

    def test_submit_new(self):
        response = self.client.post("/admin/group/submit", data={
            "submit": "新建",
            "groupname": "新分组",
            "symbols": "QQQ, SPY",
        })
        self.assertEqual(302, response.status_code)
        names = [r["groupname"] for r in self.source.stockgroup]
        self.assertIn("新分组", names)
        group_id = [r["id"] for r in self.source.stockgroup
                    if r["groupname"] == "新分组"][0]
        stock_ids = {str(r["stock_id"]) for r in self.source.stockgroupitem
                     if str(r["stockgroup_id"]) == str(group_id)}
        self.assertEqual(stock_ids, {"41", "42"})

    def test_submit_new_skips_unknown_symbol(self):
        response = self.client.post("/admin/group/submit", data={
            "submit": "新建",
            "groupname": "带跳过",
            "symbols": "QQQ, NOSUCH",
        })
        self.assertEqual(302, response.status_code)
        # flash 要报出跳过的符号（PHP 静默 continue，我们不静默）
        with self.client.session_transaction() as session:
            flashes = session["_flashes"]
        self.assertTrue(any("NOSUCH" in text for _cat, text in flashes))

    def test_submit_new_reserved_name_prefixed(self):
        # 组名撞股票符号 → 加 @（PHP _submitgroup.php:72）
        self.client.post("/admin/group/submit", data={
            "submit": "新建",
            "groupname": "QQQ",
            "symbols": "QQQ",
        })
        names = [r["groupname"] for r in self.source.stockgroup]
        self.assertIn("@QQQ", names)
        self.assertNotIn("QQQ", names)

    # -- 编辑 ---------------------------------------------------------------
    def test_edit_form_disables_reserved_name(self):
        # 组名与符号重名的分组：组名输入框禁用（PHP $strGroupNameDisabled）
        self.source.stockgroup.append(
            {"id": 5, "member_id": 1, "groupname": "QQQ"})
        response = self.client.get("/admin/group/5")
        html = response.get_data(as_text=True)
        self.assertIn("disabled", html)
        self.assertIn("不可修改", html)

    def test_submit_edit_diff(self):
        # 旧集合 {2}；新集合 {41, 2} → 只插 41；再改 {41} → 删 2
        self.client.post("/admin/group/submit", data={
            "submit": "编辑",
            "group_id": "1",
            "groupname": "演示分组A",
            "symbols": "SZ161125, QQQ",
        })
        stock_ids = {str(r["stock_id"]) for r in self.source.stockgroupitem
                     if str(r["stockgroup_id"]) == "1"}
        self.assertEqual(stock_ids, {"2", "41"})

        self.client.post("/admin/group/submit", data={
            "submit": "编辑",
            "group_id": "1",
            "groupname": "演示分组A",
            "symbols": "QQQ",
        })
        stock_ids = {str(r["stock_id"]) for r in self.source.stockgroupitem
                     if str(r["stockgroup_id"]) == "1"}
        self.assertEqual(stock_ids, {"41"})

    def test_submit_edit_keeps_old_name_when_reserved(self):
        # 旧名撞符号：丢弃提交的新名（PHP _submitgroup.php:59-60）
        self.source.stockgroup.append(
            {"id": 5, "member_id": 1, "groupname": "QQQ"})
        self.client.post("/admin/group/submit", data={
            "submit": "编辑",
            "group_id": "5",
            "groupname": "想改的名字",
            "symbols": "QQQ",
        })
        name = [r["groupname"] for r in self.source.stockgroup
                if r["id"] == 5][0]
        self.assertEqual(name, "QQQ")

    # -- 删除 ---------------------------------------------------------------
    def test_delete_removes_items_then_group(self):
        response = self.client.post("/admin/group/1/delete")
        self.assertEqual(302, response.status_code)
        self.assertEqual(self.source.stockgroup, [])
        self.assertEqual(
            [r for r in self.source.stockgroupitem
             if str(r["stockgroup_id"]) == "1"], [])

    def test_delete_cascades_transactions(self):
        """删分组 → 删成员 → **也要删交易**（2026-10-24 交易表迁完后的反填）。

        PHP 的 ``SqlDeleteStockGroupItemByGroupId`` 先 ``trans_sql->Delete``
        再删 item；本地合成一条 ``groupitem_id IN (...)``，语义相同。
        """
        self.assertEqual(len(self.source.stocktransaction), 2)
        self.client.post("/admin/group/1/delete")
        self.assertEqual(self.source.stocktransaction, [])

    def test_edit_removing_member_cascades_transactions(self):
        """编辑时移除成员（diff 的删除侧）同样级联删交易。"""
        self.client.post("/admin/group/submit", data={
            "submit": "编辑",
            "group_id": "1",
            "groupname": "演示分组A",
            "symbols": "QQQ",
        })
        item_ids = {str(r["id"]) for r in self.source.stockgroupitem}
        self.assertEqual(
            [r for r in self.source.stocktransaction
             if str(r["groupitem_id"]) in item_ids], [])

    def test_delete_missing_group(self):
        response = self.client.post("/admin/group/999/delete")
        self.assertEqual(302, response.status_code)
        with self.client.session_transaction() as session:
            flashes = session["_flashes"]
        self.assertTrue(any("999" in text for _cat, text in flashes))


if __name__ == "__main__":
    unittest.main()
