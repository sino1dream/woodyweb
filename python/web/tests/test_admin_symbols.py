"""后台「符号管理」（``/admin/symbol*``）的路由测试。

这块后台能力**PHP 没有**：那边「改名」在 ``editstock.php``、「删除」在
``deletesymbol.php``、「新增」只能靠访问页面时 ``StockGetReference``
顺手入库。本地没有那个副作用，所以补了一个显式的增删入口。

钉的事：

1. 符号归一 —— 没有下划线就大写，**带下划线的原样保留**（``nf_AU0``
   必须保持大小写，新浪对 ``nf_au0`` 返回空串）；
2. 新增只写 ``stock`` 一行，重名不静默覆盖；
3. 改名只动 ``name``，不动符号本身；
4. 删除走 ``auto.deletesymbol``，被闸门挡住时**不删**且退回详情页；
5. 详情页把"删掉会连带删什么"摆在页面上。

删除口径本身（哪几张表、闸门顺序）在 ``auto/test_deletesymbol.py`` 里测，
这里只测接线，所以把 ``deletesymbol`` 换成一个记录调用的替身。
"""

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
from web.routes import admin as admin_module  # noqa: E402

#: 列表页整条 SQL 的前缀（列清单是拼出来的，只能按前缀认）
_LIST_PREFIX = "SELECT s.id, s.symbol, s.name,"
_NAME_SQL = "SELECT name FROM stock WHERE id = %s"


class FakeCursor:
    def __init__(self, source):
        self.source = source
        self._rows = []
        self.rowcount = 0

    def execute(self, sql, params=()):
        self._rows = []
        self.rowcount = 0
        squeezed = " ".join(sql.split())
        if squeezed.startswith(_LIST_PREFIX):
            self._rows = self.source.list_rows(params)
        elif squeezed == _NAME_SQL:
            row = self.source.find_by_id(params[0])
            self._rows = [{"name": row["name"]}] if row else []
        elif squeezed.startswith("INSERT INTO stock"):
            symbol, name = params
            new_id = self.source.next_id()
            self.source.stock.append({"id": new_id, "symbol": symbol, "name": name})
            self.rowcount = 1
        elif squeezed.startswith("UPDATE stock SET name"):
            name, stock_id = params
            row = self.source.find_by_id(stock_id)
            if row is not None:
                row["name"] = name
                self.rowcount = 1
        else:
            raise AssertionError("测试 fake 不认识的 SQL: %s" % sql)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass


class FakeSource:
    """内存版数据源：只实现符号管理用到的查询与写入。"""

    def __init__(self):
        self.stock = [
            {"id": 294, "symbol": "SZ161226", "name": "国投白银LOF"},
            {"id": 291, "symbol": "nf_AU0", "name": "黄金连续"},
        ]
        #: 每只符号在四张表里的行数（列表页的子查询结果）
        self.counts = {"SZ161226": [40, 20, 1, 1, 0], "nf_AU0": [800, 0, 0, 1, 0]}
        self._next = 300

    def next_id(self):
        self._next += 1
        return self._next

    def find_by_id(self, stock_id):
        for row in self.stock:
            if str(row["id"]) == str(stock_id):
                return row
        return None

    def get_stock_id(self, symbol):
        for row in self.stock:
            if row["symbol"] == symbol:
                return str(row["id"])
        return None

    def list_rows(self, params):
        """``_symbol_rows`` 那条 SQL 的结果：别名顺序照 ``SYMBOL_COUNT_TABLES``。"""
        aliases = [alias for alias, _t, _c, _l in admin_module.SYMBOL_COUNT_TABLES]
        rows = []
        query = (params[0] or "").strip("%") if params else ""
        for row in self.stock:
            if query and query not in row["symbol"] and query not in row["name"]:
                continue
            counts = self.counts.get(row["symbol"], [0] * len(aliases))
            item = {"id": row["id"], "symbol": row["symbol"], "name": row["name"]}
            for alias, value in zip(aliases, counts):
                item[alias] = value
            rows.append(item)
        return rows

    @property
    def connection(self):
        return self

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        pass

    def rollback(self):
        pass


class FakeDeletesymbol:
    """``auto.deletesymbol`` 的替身：记录调用，返回预设结果。"""

    def __init__(self, blocked=False, source=None):
        self.blocked = blocked
        self.source = source
        self.plan_calls = []
        self.apply_calls = []

    def plan(self, connection, symbol):
        self.plan_calls.append(symbol)
        result = self._result(symbol, applied=False)
        # 库里没有的符号：真实现会返回 found=False，路由据此退回列表页
        result["found"] = self.source.get_stock_id(symbol) is not None
        return result

    def apply(self, connection, symbol, *, include_orphans=False, commit=True):
        self.apply_calls.append((symbol, include_orphans))
        return self._result(symbol, applied=not self.blocked)

    def describe(self, result):
        return "已处理 %s" % result["symbol"]

    def _result(self, symbol, applied):
        return {
            "symbol": symbol,
            "stock_id": "294",
            "found": True,
            "blocked": self.blocked,
            "blocked_by": ({"table": "fundpair", "detail": "作为本体存在配对标的"}
                           if self.blocked else None),
            "gates": [],
            "deletes": [{"table": "stock", "column": "id", "label": "标的本身", "rows": 1}],
            "orphans": [{"table": "fundest", "columns": ("stock_id",), "label": "官方EST",
                         "note": "重加回符号后会拿旧 EST 当基准", "rows": 0}],
            "deleted_rows": 1,
            "orphan_rows": 0,
            "applied": applied,
            "applied_rows": 1 if applied else 0,
            "applied_orphans": 0,
        }


class SymbolAdminTestBase(unittest.TestCase):
    def setUp(self):
        self.source = FakeSource()
        self.deletesymbol = FakeDeletesymbol(source=self.source)
        self._fake_patch = _patch_attr(admin_module, "deletesymbol", self.deletesymbol)
        self.app = create_app(
            {"TESTING": True},
            admin_source_factory=lambda: self.source,
        )
        self.client = self.app.test_client()
        self._fake_patch.start()
        self.addCleanup(self._fake_patch.stop)

    def get(self, path):
        return self.client.get(path)

    def post(self, path, data):
        return self.client.post(path, data=data)


class _patch_attr:
    """极简 ``mock.patch.object``（避免为了一行再 import mock）。"""

    def __init__(self, target, name, value):
        self.target = target
        self.name = name
        self.value = value
        self.saved = None

    def start(self):
        self.saved = getattr(self.target, self.name)
        setattr(self.target, self.name, self.value)

    def stop(self):
        setattr(self.target, self.name, self.saved)


class SymbolListTest(SymbolAdminTestBase):
    def test_list_renders_symbols_with_counts(self):
        html = self.get("/admin/symbols").get_data(as_text=True)
        self.assertIn("SZ161226", html)
        self.assertIn("国投白银LOF", html)
        # 四张表里非零的行数要显示出来，全零的不显示
        self.assertIn("历史价格 40", html)
        self.assertIn("单位净值 20", html)

    def test_search_narrows_the_list(self):
        html = self.get("/admin/symbols?q=白银").get_data(as_text=True)
        self.assertIn("SZ161226", html)
        self.assertNotIn("nf_AU0", html)


class SymbolCreateTest(SymbolAdminTestBase):
    def test_new_form_renders(self):
        html = self.get("/admin/symbol/new").get_data(as_text=True)
        self.assertIn("新增符号", html)

    def test_create_normalizes_uppercase(self):
        response = self.post("/admin/symbol/submit",
                             {"mode": "new", "symbol": "sh513100", "name": "沪深300ETF"})
        self.assertEqual(302, response.status_code)
        self.assertEqual("SH513100", self.source.stock[-1]["symbol"])

    def test_create_keeps_lowercase_after_underscore(self):
        """``nf_au0`` 不能被大写 —— 新浪只认 ``nf_AU0``，符号改了行情就没了。

        这里用 ``nf_AU0`` 加个尾巴来测：带下划线的一律原样保留。
        """
        self.post("/admin/symbol/submit",
                  {"mode": "new", "symbol": "hf_GC", "name": "纽约黄金"})
        self.assertEqual("hf_GC", self.source.stock[-1]["symbol"])

    def test_duplicate_symbol_is_reported_not_overwritten(self):
        before = len(self.source.stock)
        response = self.post("/admin/symbol/submit",
                             {"mode": "new", "symbol": "SZ161226", "name": "换个名"})
        self.assertEqual(302, response.status_code)
        self.assertEqual(before, len(self.source.stock))
        self.assertEqual("国投白银LOF", self.source.stock[0]["name"])

    def test_empty_symbol_is_rejected(self):
        before = len(self.source.stock)
        self.post("/admin/symbol/submit", {"mode": "new", "symbol": "  ", "name": "x"})
        self.assertEqual(before, len(self.source.stock))


class SymbolRenameTest(SymbolAdminTestBase):
    def test_rename_only_touches_the_name(self):
        response = self.post("/admin/symbol/submit",
                             {"mode": "edit", "symbol": "SZ161226", "name": "白银LOF"})
        self.assertEqual(302, response.status_code)
        self.assertEqual("白银LOF", self.source.stock[0]["name"])
        self.assertEqual("SZ161226", self.source.stock[0]["symbol"])

    def test_blank_name_is_rejected(self):
        self.post("/admin/symbol/submit",
                  {"mode": "edit", "symbol": "SZ161226", "name": "  "})
        self.assertEqual("国投白银LOF", self.source.stock[0]["name"])


class SymbolDeleteTest(SymbolAdminTestBase):
    def test_detail_page_shows_what_would_be_deleted(self):
        html = self.get("/admin/symbol/SZ161226").get_data(as_text=True)
        self.assertIn("关联数据", html)
        self.assertIn("官方EST", html)
        self.assertIn("一并清理残留表", html)
        self.assertEqual(["SZ161226"], self.deletesymbol.plan_calls)

    def test_delete_passes_orphan_flag(self):
        response = self.post("/admin/symbol/SZ161226/delete", {"include_orphans": "1"})
        self.assertEqual(302, response.status_code)
        self.assertEqual([("SZ161226", True)], self.deletesymbol.apply_calls)

    def test_delete_defaults_to_no_orphans(self):
        self.post("/admin/symbol/SZ161226/delete", {})
        self.assertEqual([("SZ161226", False)], self.deletesymbol.apply_calls)

    def test_blocked_symbol_returns_to_detail_page(self):
        self.deletesymbol.blocked = True
        response = self.post("/admin/symbol/SZ161226/delete", {})
        self.assertEqual(302, response.status_code)
        self.assertIn("/admin/symbol/SZ161226", response.headers["Location"])

    def test_unknown_symbol_redirects_to_list(self):
        response = self.get("/admin/symbol/SH999999")
        self.assertEqual(302, response.status_code)
        self.assertIn("/admin/symbols", response.headers["Location"])


if __name__ == "__main__":
    unittest.main()
