"""参考数据表的测试（`php/ui/referenceparagraph.php` → `reference_service.py`）。

**全部离线**：引用对象与数据源都是内存假实现，不联网、不连 MySQL。
"""

import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[3]
PYTHON_DIR = ROOT_DIR / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from web.app import create_app
from web.bootstrap import install_paths
from web.services.fund_service import FundService
from web.services.reference_service import (
    REFERENCE_NAME_WIDTH,
    ReferenceService,
    build_stock_references,
    percentage_cell,
)

install_paths()

from web.tests.test_web import make_source  # noqa: E402


class FakeRef:
    """引用对象替身，只实现参考数据表会问到的那几个动作。"""

    def __init__(
        self,
        symbol="SZ161130",
        has_data=True,
        price="4.796",
        change="-1.23%",
        date="2026-10-02",
        time="15:00:00",
        session=None,
        extended=None,
    ):
        self.symbol = symbol
        self.has_data = has_data
        self._price = price
        self._change = change
        self._date = date
        self._time = time
        self._session = session
        self.extended_ref = extended

    def get_symbol(self):
        return self.symbol

    def get_external_link(self):
        return '<a href="https://example.test/%s">新浪</a>' % self.symbol

    def get_price_display(self):
        return self._price

    def get_percentage_text(self):
        return self._change

    def get_date(self):
        return self._date

    def get_time_hm(self):
        # 15:00:00 -> 15:00，对应 PHP GetTimeHM()
        return self._time[:5]

    def get_market_session(self):
        return self._session or ""

    def debug_link(self):
        return "debug.link/%s" % self.symbol


class FakeNameSource:
    def __init__(self, names=None):
        self.names = names or {}

    def get_stock_name(self, symbol):
        return self.names.get(symbol)


class ColumnTest(unittest.TestCase):
    """列定义对照 PHP ``EchoReferenceParagraph()`` 那段"塞进去再 pop 掉"的组装。"""

    def test_six_columns_in_php_order(self):
        table = ReferenceService().get_table([FakeRef()])
        self.assertEqual(
            [column["text"] for column in table["columns"]],
            ["代码", "价格", "涨幅", "日期", "时间", "名称"],
        )

    def test_widths_match_php(self):
        """名称列被 PHP 换成 270 宽的那一个（默认 150 不够放全名）。"""
        table = ReferenceService().get_table([FakeRef()])
        widths = [column["width"] for column in table["columns"]]
        self.assertEqual(widths, [80, 70, 70, 100, 50, REFERENCE_NAME_WIDTH])
        self.assertEqual(REFERENCE_NAME_WIDTH, 270)

    def test_column_colors_come_from_uicolumn(self):
        """代码 maroon、价格 blue —— 这两条来自 stocktable.php 的预置。"""
        table = ReferenceService().get_table([FakeRef()])
        colors = [column["color"] for column in table["columns"]]
        self.assertEqual(colors, ["maroon", "blue", "", "", "", ""])

    def test_admin_appends_debug_column_with_remaining_width(self):
        """调试列吃剩余宽度（PHP ``TableColumnGetLastWidth``）。"""
        table = ReferenceService(layout_width=980).get_table([FakeRef()], admin=True)
        last = table["columns"][-1]
        self.assertEqual(last["text"], "调试数据")
        self.assertEqual(last["width"], 980 - (80 + 70 + 70 + 100 + 50 + 270))

    def test_admin_without_layout_width_is_zero(self):
        """没给布局宽度就算 0，不去猜屏幕宽度。"""
        table = ReferenceService().get_table([FakeRef()], admin=True)
        self.assertEqual(table["columns"][-1]["width"], 0)


class RowTest(unittest.TestCase):
    def test_row_order_and_cells(self):
        service = ReferenceService(FakeNameSource({"SZ161130": "易方达纳斯达克100LOF"}))
        row = service.get_table([FakeRef()])["rows"][0]

        # 首格外链 + 中间四格 + 名称
        self.assertEqual(row.cells[0], '<a href="https://example.test/SZ161130">新浪</a>')
        self.assertEqual(row.cells[1], "4.796")
        self.assertEqual(row.cells[2], ("-1.23%", "red"))
        self.assertEqual(row.cells[3], "2026-10-02")
        self.assertEqual(row.cells[4], "15:00")
        self.assertEqual(row.cells[5], "易方达纳斯达克100LOF")

    def test_without_quote_data_row_is_four_blanks(self):
        """**列数必须对齐**：没有行情时填四个空串而不是省略，
        否则整行会往左边串位。
        """
        row = ReferenceService().get_table([FakeRef(has_data=False)])["rows"][0]
        self.assertEqual(row.cells[1:5], ["", "", "", ""])
        self.assertEqual(len(row.cells), 6)

    def test_extended_row_moves_name_to_tooltip(self):
        """PHP 用 ``array_pop`` 把名称拎出来降级成首格 title，
        最后一格换成"盘前/盘后"说明。
        """
        extended = FakeRef(session="盘后交易")
        ref = FakeRef(extended=extended)
        service = ReferenceService(FakeNameSource({"SZ161130": "易方达LOF"}))

        rows = service.get_table([ref])["rows"]

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1].title, "易方达LOF")
        self.assertEqual(rows[1].cells[-1], ("盘后交易", "gray"))
        # 主行没有说明文字，名称照常显示在最后一格
        self.assertEqual(rows[0].title, "")
        self.assertEqual(rows[0].cells[-1], "易方达LOF")

    def test_none_refs_are_skipped(self):
        """PHP 的 ``_echoReferenceTableData`` 也是 ``if ($ref)`` 才渲染。"""
        self.assertEqual(ReferenceService().get_table([None, FakeRef(), None])["count"], 1)

    def test_missing_name_is_empty_string(self):
        row = ReferenceService(FakeNameSource()).get_table([FakeRef()])["rows"][0]
        self.assertEqual(row.cells[-1], "")

    def test_admin_row_has_extra_cell(self):
        row = ReferenceService(layout_width=980).get_table([FakeRef()], admin=True)["rows"][0]
        self.assertEqual(row.cells[-1], "debug.link/SZ161130")

    def test_name_lookup_failure_does_not_break_the_table(self):
        class Broken:
            def get_stock_name(self, symbol):
                raise RuntimeError("模拟数据库异常")

        row = ReferenceService(Broken()).get_table([FakeRef()])["rows"][0]
        self.assertEqual(row.cells[-1], "")
        self.assertEqual(len(row.cells), 6)


class PercentageColorTest(unittest.TestCase):
    """涨跌幅染色：逐条对照 ``php/stock/stockref.php:147``。"""

    def test_negative_is_red(self):
        self.assertEqual(percentage_cell("-1.23%"), ("-1.23%", "red"))

    def test_positive_is_black(self):
        self.assertEqual(percentage_cell("1.23%"), ("1.23%", "black"))

    def test_without_percent_sign_is_gray(self):
        """`GetPercentageText()` 返回空串（没有昨收可比）时 PHP 染灰。"""
        self.assertEqual(percentage_cell(""), ("", "gray"))
        self.assertEqual(percentage_cell(None), ("", "gray"))

    def test_conflicts_with_fund_list_convention(self):
        """**记录在案的冲突**：本表照抄 ``stockref.php:147`` 的"跌红涨黑"，
        而分组列表页（``fund_list_service``）对同样的跌幅给的是"跌绿"，
        即"涨红跌绿"。两处口径相反，这条用例把现状钉住，等裁决后再改这里。
        """
        self.assertEqual(percentage_cell("-1.23%")[1], "red")
        # 分组列表页（fund_list_service）对同样的跌幅给的是绿色：
        self.assertNotEqual(percentage_cell("-1.23%")[1], "green")


class ReferenceRouteTest(unittest.TestCase):
    """基金详情页接线：注入假引用对象，不打网络。"""

    def setUp(self):
        source = make_source()
        self.app = create_app(
            {"TESTING": True},
            fund_service_factory=lambda: FundService(source),
            reference_service_factory=lambda: ReferenceService(
                FakeNameSource({"SZ162411": "华宝油气"})
            ),
            reference_refs_builder=lambda symbols: [FakeRef(symbol) for symbol in symbols],
        )
        self.client = self.app.test_client()

    def test_fund_page_renders_reference_table(self):
        html = self.client.get("/fund/SZ162411").get_data(as_text=True)

        self.assertIn('class="data-table reference-table"', html)
        for text in ("代码", "价格", "涨幅", "日期", "时间", "名称"):
            self.assertIn(text, html)
        self.assertIn("华宝油气", html)
        self.assertIn("参考数据来自外部行情源", html)

    def test_fund_page_hides_table_when_there_are_no_rows(self):
        """没有任何行时整段跳过 —— 宁可少一块，也不摆一张空表。"""
        self.app.extensions["reference_refs_builder"] = lambda symbols: []
        html = self.client.get("/fund/SZ162411").get_data(as_text=True)

        self.assertNotIn("reference-table", html)

    def test_reference_failure_does_not_break_the_page(self):
        """参考数据取不到时，基金详情页必须照常可用。"""

        def boom(symbols):
            raise RuntimeError("模拟行情抓取失败")

        self.app.extensions["reference_refs_builder"] = boom
        response = self.client.get("/fund/SZ162411")

        self.assertEqual(response.status_code, 200)
        self.assertIn("估值配置", response.get_data(as_text=True))


class RealReferenceBuildingTest(unittest.TestCase):
    """`build_stock_references()` 用真实 ``StockReference``，**构造本身不联网**。

    ``StockReference.__init__`` 只组装；``load_sina_data()`` 没有 loader 时取不到
    数据，会把 ``has_data`` 置 False —— 那时表格应该输出空行而不是抛异常。
    """

    def test_load_failure_degrades_to_blank_row(self):
        refs = build_stock_references(["SZ161130"])
        self.assertEqual(len(refs), 1)

        row = ReferenceService().get_table(refs)["rows"][0]
        self.assertEqual(row.cells[1:5], ["", "", "", ""])


if __name__ == "__main__":
    unittest.main()
