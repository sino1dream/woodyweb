"""股票列表表的测试（`php/ui/stockparagraph.php` → `stock_service.py`）。

**全部离线**：``StockSql`` 接的是假连接，不联网、不连 MySQL、不需要 flask。
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

from web.services.stock_service import (  # noqa: E402
    DEFAULT_PAGE_NUM,
    MAX_PAGE_NUM,
    STOCK_ADMIN_WIDTH,
    STOCK_TABLE_ID,
    StockService,
    build_stock_columns,
    build_stock_rows,
    menu_link_paging,
    menu_page_sizes,
)


ROWS = [
    {"id": 1, "symbol": "SZ161130", "name": "纳斯达克100"},
    {"id": 2, "symbol": "SZ162411", "name": "华宝油气"},
    {"id": 3, "symbol": "SH518880", "name": "黄金ETF"},
]


class FakeCursor:
    """记录 SQL 并回放预置结果的假游标（与 test_stocksql 同一套路）。"""

    def __init__(self, connection):
        self.connection = connection
        self.rows = []
        self.sql = ""
        self.params = ()

    def execute(self, sql, params=()):
        self.sql = sql
        self.params = tuple(params)
        self.rows = self.connection.answer(sql)

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def close(self):
        self.connection.closed = self.connection.closed + 1


class FakeConnection:
    """按 SQL 关键字回放结果的假连接。

    ``fail_on`` 里的子串会让对应查询抛异常，用来验证"列表页不该整体 500"。
    """

    def __init__(self, rows=None, total=0, tuple_rows=False, fail_on=()):
        self.rows = ROWS if rows is None else rows
        self.total = total if total else len(self.rows)
        self.tuple_rows = tuple_rows
        self.fail_on = fail_on
        self.executed = []
        self.closed = 0

    def cursor(self):
        return FakeCursor(self)

    def answer(self, sql):
        self.executed.append(sql)
        for fragment in self.fail_on:
            if fragment in sql:
                raise RuntimeError("boom: " + fragment)
        if "COUNT(*)" in sql:
            return [{"n": self.total}]
        if self.tuple_rows:
            return [(row["id"], row["symbol"], row["name"]) for row in self.rows]
        return self.rows


def service(connection, **kwargs):
    return StockService(_sql(connection), **kwargs)


def _sql(connection):
    from sql.stocksql import StockSql

    return StockSql(connection)


class ColumnTest(unittest.TestCase):
    def test_two_columns_for_plain_users(self):
        columns = build_stock_columns()
        self.assertEqual([c.text for c in columns], ["代码", "名称"])
        self.assertEqual([c.width for c in columns], [80, 150])

    def test_admin_adds_an_empty_wide_column(self):
        """``new TableColumn('', 270)`` —— 空表头、宽 270。"""
        columns = build_stock_columns(admin=True)
        self.assertEqual(len(columns), 3)
        self.assertEqual(columns[2].text, "")
        self.assertEqual(columns[2].width, STOCK_ADMIN_WIDTH)

    def test_colors_follow_stocktable_php(self):
        self.assertEqual([c.color for c in build_stock_columns()], ["maroon", ""])


class RowTest(unittest.TestCase):
    def test_row_is_symbol_then_name(self):
        rows = build_stock_rows(ROWS)
        self.assertEqual(rows[0].cells, ["SZ161130", "纳斯达克100"])

    def test_internal_link_lands_on_the_first_cell(self):
        rows = build_stock_rows(
            ROWS, link_builder=lambda symbol: "/fund/" + symbol)
        self.assertEqual(rows[0].href, "/fund/SZ161130")

    def test_external_link_lands_on_the_name_cell(self):
        rows = build_stock_rows(
            ROWS, external_link_builder=lambda symbol: "https://xueqiu.com/S/" + symbol)
        self.assertEqual(rows[1].hrefs[1], "https://xueqiu.com/S/SZ162411")

    def test_html_is_never_put_into_cells(self):
        """cells 里只能有文本 —— 链接一律走 href / hrefs。"""
        rows = build_stock_rows(
            ROWS,
            link_builder=lambda symbol: "/fund/" + symbol,
            external_link_builder=lambda symbol: "https://xueqiu.com/S/" + symbol,
        )
        self.assertEqual(rows[0].cells[0], "SZ161130")
        self.assertEqual(rows[0].cells[1], "纳斯达克100")
        self.assertNotIn("<a", str(rows[0].cells))

    def test_admin_cell_holds_the_action_list(self):
        def admin_links(symbol):
            return [
                {"text": "编辑", "url": "/stock/edit/" + symbol},
                {"text": "删除", "url": "/stock/delete/" + symbol},
            ]

        rows = build_stock_rows(ROWS, admin=True, admin_link_builder=admin_links)
        self.assertEqual(rows[0].cells, ["SZ161130", "纳斯达克100", ""])
        self.assertEqual(rows[0].hrefs[2][0]["url"], "/stock/edit/SZ161130")
        self.assertEqual(len(rows[0].hrefs[2]), 2)

    def test_admin_without_builder_leaves_an_empty_cell(self):
        """没注入 builder 时不编 URL —— 账号那一档还没迁。"""
        rows = build_stock_rows(ROWS, admin=True)
        self.assertEqual(rows[0].cells[2], "")
        self.assertEqual(rows[0].hrefs.get(2), None)

    def test_tuple_records_are_supported(self):
        """tuple 行也要认 —— pymysql 换游标类型会让行从 dict 变成 tuple。

        ``StockSql`` 自己会做 ``dict(row)``，所以这里是**防御性**的一层：
        一个对象改动就有两种写法，而行解析静默变空是最难查的故障。
        """
        rows = build_stock_rows([(1, "SZ161130", "纳斯达克100")])
        self.assertEqual(rows[0].cells, ["SZ161130", "纳斯达克100"])

    def test_record_without_symbol_is_skipped(self):
        connection = FakeConnection(rows=[{"symbol": "", "name": "无名"}, ROWS[0]])
        rows = service(connection).get_table()["rows"]
        self.assertEqual(len(rows), 1)


class GetTableTest(unittest.TestCase):
    def test_table_id_and_counts(self):
        table = service(FakeConnection()).get_table()
        self.assertEqual(table["id"], STOCK_TABLE_ID)
        self.assertEqual(table["count"], 3)
        self.assertEqual(table["total"], 3)

    def test_empty_result_still_returns_a_table(self):
        """列表页是目录表 —— 摆一张空表配"总数 0"是对的（与盘口那段不同）。"""
        table = service(FakeConnection(rows=[], total=0)).get_table()
        self.assertEqual(table["rows"], [])
        self.assertEqual(table["total"], 0)
        self.assertFalse(table["paging"]["show"])

    def test_paging_parameters_are_passed_through(self):
        connection = FakeConnection()
        table = service(connection).get_table(start=100, num=50)
        self.assertEqual(table["start"], 100)
        self.assertEqual(table["num"], 50)
        self.assertIn("LIMIT 100 , 50", connection.executed[-1])

    def test_query_failure_does_not_raise(self):
        """PHP 那边 ``GetStockSql()`` 是全局单例，取不到会让整页挂掉；这里降级。"""
        connection = FakeConnection(fail_on=("COUNT(*)",))
        table = service(connection).get_table()
        self.assertEqual(table["total"], 0)

    def test_record_query_failure_gives_no_rows(self):
        connection = FakeConnection(fail_on=("ORDER BY",))
        table = service(connection).get_table()
        self.assertEqual(table["rows"], [])


class PageSizeTest(unittest.TestCase):
    def test_small_database_still_offers_the_default_page_size(self):
        """上界是 ``总数 + 步长 - 1``，所以 30 条也给出「每页 100」。"""
        self.assertEqual(menu_page_sizes(30), [100])

    def test_steps_by_default_page_num(self):
        # 候选允许超出总数：250 条时仍给到 300（照抄 PHP，见 menu_page_sizes 的注释）
        self.assertEqual(menu_page_sizes(250), [100, 200, 300])

    def test_capped_at_five_hundred(self):
        self.assertEqual(menu_page_sizes(5000),
                         list(range(DEFAULT_PAGE_NUM, MAX_PAGE_NUM + 1, DEFAULT_PAGE_NUM)))

    def test_zero_total_has_no_candidates(self):
        self.assertEqual(menu_page_sizes(0), [])


class PagingTest(unittest.TestCase):
    def test_start_zero_and_num_zero_show_nothing(self):
        """``$iStart == 0 && $iNum == 0`` —— PHP 直接只返回总数。"""
        self.assertFalse(menu_link_paging(500, 0, 0)["show"])

    def test_zero_total_shows_nothing(self):
        self.assertFalse(menu_link_paging(0, 0, 100)["show"])

    def test_first_page_has_only_next(self):
        paging = menu_link_paging(500, 0, 100)
        self.assertTrue(paging["show"])
        self.assertIsNone(paging["prev_start"])
        self.assertIsNone(paging["first_start"])
        self.assertEqual(paging["next_start"], 100)
        self.assertEqual(paging["last_start"], 400)

    def test_middle_page_offers_everything(self):
        paging = menu_link_paging(500, 200, 100)
        self.assertEqual(paging["prev_start"], 100)
        self.assertEqual(paging["first_start"], 0)
        self.assertEqual(paging["next_start"], 300)
        self.assertEqual(paging["last_start"], 400)

    def test_second_page_has_no_separate_first_link(self):
        """``$iPrevStart != 0`` 时才给"首页" —— 第二页的 prev 已经是 0，不重复给。"""
        paging = menu_link_paging(500, 100, 100)
        self.assertEqual(paging["prev_start"], 0)
        self.assertIsNone(paging["first_start"])

    def test_last_page_has_only_prev(self):
        paging = menu_link_paging(500, 400, 100)
        self.assertIsNone(paging["next_start"])
        self.assertIsNone(paging["last_start"])

    def test_display_range_is_one_based_and_clamped(self):
        paging = menu_link_paging(250, 200, 100)
        self.assertEqual(paging["first_index"], 201)
        self.assertEqual(paging["last_index"], 250)  # min(start+num, total)

    def test_next_page_already_reaches_the_end(self):
        """``$iNextStart + $iNum < $iTotal`` 为假时不给"末页"。

        total=400 / start=200 / num=100：下一页就是最后一页，再给"末页"是死链。
        """
        paging = menu_link_paging(400, 200, 100)
        self.assertEqual(paging["next_start"], 300)
        self.assertIsNone(paging["last_start"])

    def test_but_a_full_page_beyond_next_does_have_a_last_link(self):
        paging = menu_link_paging(400, 100, 100)
        self.assertEqual(paging["next_start"], 200)
        self.assertEqual(paging["last_start"], 300)


if __name__ == "__main__":
    unittest.main()
