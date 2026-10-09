"""`python/web/services/fundest_service.py` 的单元测试。

对应 PHP ``php/ui/fundestparagraph.php``（174 行）的
``EchoFundArrayEstParagraph`` / ``EchoFundEstParagraph`` / ``EchoHoldingsEstParagraph``。

用假 ref，不打网络、不连数据库。重点钉住四件事：

1. 表头由"**扫一遍所有 ref**"决定（任意一个有参考/实时估值就给所有人加列）；
2. 没有行情的 ref **整行跳过**；
3. 排序只在超过 2 行时生效；
4. ⚠️ "实时估值"那一档**缺一次补齐** —— PHP 的错位 bug，见
   :class:`RowLengthTest`。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[3]
PYTHON_DIR = ROOT_DIR / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from web.bootstrap import install_paths  # noqa: E402

install_paths()

from web.services.fundest_service import (  # noqa: E402
    FUND_EST_SORT_THRESHOLD,
    FUND_EST_TABLE_ID,
    SORT_BY_PREMIUM,
    SORT_BY_SYMBOL,
    FundEstService,
    build_fund_est_columns,
    build_fund_est_table,
)


class FakeStockRef:
    """``GetStockRef()`` 的替身：估值百分比要以"价格 vs 净值"算。"""

    def __init__(self, price: float = 4.796):
        self.price = price
        self.has_data = True

    def get_percentage(self, divisor=None, dividend=None):
        divisor = self.price if divisor is None else float(divisor)
        dividend = self.price if dividend is None else float(dividend)
        return (dividend / divisor - 1.0) * 100.0

    def get_price_display(self, disp=None, prev=None, precision=None):
        return "%.3f" % (self.price if disp is None else float(disp))

    def get_percentage_text(self, divisor=None, dividend=None):
        if divisor is None:
            return "1.23%"
        return "%.2f%%" % self.get_percentage(divisor, dividend)

    def get_date(self):
        return "2026-10-02"

    def get_time_hm(self):
        return "15:00"


class FakeFundRef:
    """够用的假基金引用对象。"""

    def __init__(
        self,
        symbol: str = "SZ162411",
        official: float = 4.5,
        fair: float = None,
        realtime: float = None,
        has_data: bool = True,
        price: float = 4.796,
        date: str = "2026-10-02",
    ):
        self.symbol = symbol
        self.official = official
        self.fair = fair
        self.realtime = realtime
        self.has_data = has_data
        self.price = price
        self.date = date
        self.stock_ref = FakeStockRef(price)

    # -- 本段落用得到的 ---------------------------------------------------
    def get_symbol(self):
        return self.symbol

    def get_stock_link(self):
        return '<a href="/fund/%s">%s</a>' % (self.symbol, self.symbol)

    def get_stock_ref(self):
        return self.stock_ref

    def get_official_net_value(self):
        return self.official

    def get_fair_net_value(self):
        return self.fair

    def get_realtime_net_value(self):
        return self.realtime

    def get_official_date(self):
        return self.date

    def get_price_display(self, disp=None, prev=None, precision=None):
        return "%.3f" % (self.official if disp is None else float(disp))

    def get_percentage_text(self, divisor=None, dividend=None):
        return self.stock_ref.get_percentage_text(divisor, dividend)


class ColumnTest(unittest.TestCase):
    """表头由"扫一遍所有 ref"决定。"""

    def test_base_columns(self):
        columns, has_fair, has_realtime = build_fund_est_columns([])
        self.assertEqual([c["text"] for c in columns], ["代码", "官方EST", "EST日期", "溢价"])
        self.assertFalse(has_fair)
        self.assertFalse(has_realtime)

    def test_wide_inserts_five_quote_columns(self):
        columns, _, _ = build_fund_est_columns([], wide=True)
        self.assertEqual([c["text"] for c in columns][:6], ["代码", "价格", "涨幅", "日期", "时间", "名称"])
        self.assertEqual(len(columns), 9)

    def test_one_fair_ref_adds_columns_for_everyone(self):
        """**任意一个** ref 有参考估值，就给所有人加两列。"""
        refs = [FakeFundRef(), FakeFundRef(fair=4.6)]
        columns, has_fair, has_realtime = build_fund_est_columns(refs)
        self.assertTrue(has_fair)
        self.assertFalse(has_realtime)
        self.assertEqual([c["text"] for c in columns][-2:], ["参考EST", "溢价"])
        self.assertEqual(len(columns), 6)

    def test_one_realtime_ref_adds_columns_for_everyone(self):
        refs = [FakeFundRef(), FakeFundRef(realtime=4.7)]
        columns, has_fair, has_realtime = build_fund_est_columns(refs)
        self.assertFalse(has_fair)
        self.assertTrue(has_realtime)
        self.assertEqual([c["text"] for c in columns][-2:], ["实时EST", "溢价"])

    def test_both_present(self):
        refs = [FakeFundRef(fair=4.6), FakeFundRef(realtime=4.7)]
        columns, has_fair, has_realtime = build_fund_est_columns(refs)
        self.assertTrue(has_fair)
        self.assertTrue(has_realtime)
        self.assertEqual(len(columns), 8)

    def test_generator_refs_are_consumed_once(self):
        """``get_table`` 要用 refs 两次（定列 + 定行），生成器不能只遍历一遍。"""
        table = build_fund_est_table((FakeFundRef() for _ in range(3)))
        self.assertEqual(table["count"], 3)


class RowTest(unittest.TestCase):
    """行的组装。"""

    def test_ref_without_data_is_skipped(self):
        """``if (RefHasData($ref) == false) return;`` —— 整行不出现。"""
        table = FundEstService().get_table([FakeFundRef(has_data=False)])
        self.assertIsNone(table)

    def test_none_refs_are_filtered(self):
        self.assertEqual(FundEstService().get_table([None, FakeFundRef(), None])["count"], 1)

    def test_official_only_row(self):
        row = FundEstService().get_table([FakeFundRef()])["rows"][0]
        # 链接 / 官方估值 / 官方日期 / 官方溢价
        self.assertEqual(len(row.cells), 4)
        self.assertIn("SZ162411", row.cells[0])
        self.assertEqual(row.cells[1], "4.500")
        self.assertEqual(row.cells[2], "2026-10-02")
        self.assertEqual(row.cells[3], ("6.58%", "red"))   # 价格 4.796 / 净值 4.5

    def test_discount_is_green(self):
        """折价（负溢价）染绿 —— 与分组列表页的 ``premium_class`` 一致。"""
        row = FundEstService().get_table([FakeFundRef(official=5.0)])["rows"][0]
        self.assertEqual(row.cells[3][1], "green")

    def test_full_row_has_eight_cells(self):
        row = FundEstService().get_table([FakeFundRef(fair=4.6, realtime=4.7)])["rows"][0]
        self.assertEqual(len(row.cells), 8)
        # 溢价 = (价格/估值 - 1) * 100，价格是场内的 4.796
        self.assertEqual(row.cells[5][0], "4.26%")   # 参考：4.796/4.6
        self.assertEqual(row.cells[7][0], "2.04%")   # 实时：4.796/4.7

    def test_fair_is_padded_when_absent(self):
        """**参考估值这一档有补齐**：别的标的有、自己没有时补两个空串。"""
        refs = [FakeFundRef(fair=4.6), FakeFundRef(symbol="SZ161130")]
        rows = FundEstService().get_table(refs)["rows"]
        short = [row for row in rows if "SZ161130" in row.cells[0]][0]
        full = [row for row in rows if "SZ162411" in row.cells[0]][0]
        self.assertEqual(len(short.cells), len(full.cells))
        self.assertEqual(short.cells[4:6], ["", ""])

    def test_narrow_moves_name_to_tooltip(self):
        class Source:
            def get_stock_name(self, symbol):
                return "华宝油气"

        row = FundEstService(Source()).get_table([FakeFundRef()])["rows"][0]
        self.assertEqual(row.title, "华宝油气")

    def test_wide_puts_name_in_a_cell(self):
        class Source:
            def get_stock_name(self, symbol):
                return "华宝油气"

        row = FundEstService(Source()).get_table([FakeFundRef()], wide=True)["rows"][0]
        self.assertEqual(row.cells[5], "华宝油气")
        self.assertEqual(row.title, "")

    def test_wide_uses_the_stock_ref_for_quote_cells(self):
        """PHP 取的是 ``GetStockRef()`` 而不是 ref 自己 —— 价格/日期得自场内标的。"""
        row = FundEstService().get_table([FakeFundRef()], wide=True)["rows"][0]
        self.assertEqual(row.cells[1], "4.796")     # 场内价格，不是估值
        self.assertEqual(row.cells[3], "2026-10-02")


class RowLengthTest(unittest.TestCase):
    """⚠️ PHP 的错位 bug：实时估值那一档**没有补齐**。

    参考估值有 ``else if ($bFair) { $ar[] = ''; $ar[] = ''; }``，
    实时估值只有 ``if`` —— 于是"别人有实时估值、自己没有"时，
    这一行会比别人**少两格**，后面的单元格整体左移。

    本版照抄这个行为（静默补空格等于掩盖错位），并用 ``cell_count``
    把差异暴露出来。如果哪天有人"顺手对齐"了，这里会失败并提醒先看模块 docstring。
    """

    def test_fair_gap_is_padded_but_realtime_gap_is_not(self):
        refs = [FakeFundRef(realtime=4.7), FakeFundRef(symbol="SZ161130")]
        table = FundEstService().get_table(refs)

        self.assertEqual(table["has_realtime"], True)
        self.assertEqual(len(table["columns"]), 6)   # 4 基础 + 2 实时

        full = [row for row in table["rows"] if "SZ162411" in row.cells[0]][0]
        short = [row for row in table["rows"] if "SZ161130" in row.cells[0]][0]

        self.assertEqual(len(full.cells), 6)
        self.assertEqual(len(short.cells), 4)   # ← 少两格，这就是那个 bug
        self.assertEqual(table["cell_count"], 6)

    def test_the_same_gap_with_fair_is_padded(self):
        """对照组：同样的情形换成参考估值，PHP 是补了空格的。"""
        refs = [FakeFundRef(fair=4.6), FakeFundRef(symbol="SZ161130")]
        table = FundEstService().get_table(refs)

        full = [row for row in table["rows"] if "SZ162411" in row.cells[0]][0]
        short = [row for row in table["rows"] if "SZ161130" in row.cells[0]][0]
        self.assertEqual(len(full.cells), len(short.cells))


class SortTest(unittest.TestCase):
    """``if ($iCount > 2)`` 里的两个排序分支。"""

    def test_two_refs_are_never_sorted(self):
        refs = [FakeFundRef(symbol="SZ000002"), FakeFundRef(symbol="SZ000001")]
        rows = FundEstService().get_table(refs, sort=SORT_BY_SYMBOL)["rows"]
        self.assertIn("SZ000002", rows[0].cells[0])

    def test_sort_by_symbol(self):
        refs = [
            FakeFundRef(symbol="SZ000003"),
            FakeFundRef(symbol="SZ000001"),
            FakeFundRef(symbol="SZ000002"),
        ]
        rows = FundEstService().get_table(refs, sort=SORT_BY_SYMBOL)["rows"]
        self.assertEqual(
            [_symbol_of(row) for row in rows], ["SZ000001", "SZ000002", "SZ000003"]
        )

    def test_sort_by_premium(self):
        """排序键是溢价率：官方净值越小于价格（溢价越高）越靠后。"""
        refs = [
            FakeFundRef(symbol="SZ000003", official=4.0),   # 溢价 +19.9%
            FakeFundRef(symbol="SZ000001", official=5.0),   # 溢价 -4.1%
            FakeFundRef(symbol="SZ000002", official=4.5),   # 溢价 +6.6%
        ]
        rows = FundEstService().get_table(refs, sort=SORT_BY_PREMIUM)["rows"]
        self.assertEqual(
            [_symbol_of(row) for row in rows], ["SZ000001", "SZ000002", "SZ000003"]
        )

    def test_without_sort_keeps_the_given_order(self):
        refs = [
            FakeFundRef(symbol="SZ000003"),
            FakeFundRef(symbol="SZ000001"),
            FakeFundRef(symbol="SZ000002"),
        ]
        rows = FundEstService().get_table(refs)["rows"]
        self.assertEqual(
            [_symbol_of(row) for row in rows], ["SZ000003", "SZ000001", "SZ000002"]
        )

    def test_threshold_is_two(self):
        self.assertEqual(FUND_EST_SORT_THRESHOLD, 2)


class TableTest(unittest.TestCase):
    """整张表的形状。"""

    def test_id_and_defaults(self):
        table = build_fund_est_table([FakeFundRef()])
        self.assertEqual(table["id"], FUND_EST_TABLE_ID)
        self.assertEqual(table["title"], "")
        self.assertEqual(table["count"], 1)

    def test_empty_returns_none(self):
        self.assertIsNone(build_fund_est_table([]))


class FundEstRouteTest(unittest.TestCase):
    """基金详情页接线：注入假引用对象，不打网络、不连数据库。"""

    def setUp(self):
        from web.app import create_app
        from web.services.fund_service import FundService
        from web.tests.test_web import make_source

        class Source:
            def get_stock_name(self, symbol):
                return "华宝油气"

        self.app = create_app(
            {"TESTING": True},
            fund_service_factory=lambda: FundService(make_source()),
            fundest_service_factory=lambda: FundEstService(Source()),
            fundest_refs_builder=lambda symbols: [FakeFundRef(symbol) for symbol in symbols],
        )
        self.client = self.app.test_client()

    def test_fund_page_renders_the_estimation_table(self):
        html = self.client.get("/fund/SZ162411").get_data(as_text=True)

        self.assertIn('class="data-table fund-est-table"', html)
        self.assertIn('id="estimation"', html)
        for text in ("代码", "官方EST", "EST日期", "溢价"):
            self.assertIn(text, html)
        self.assertIn("华宝油气", html)

    def test_table_is_absent_without_a_refs_builder(self):
        """**默认不开**：没有显式注入 refs builder 时整块不出现。

        这不是漏接线 —— 三档估值要等 estimator 接进页面，
        详见 ``web/routes/fund.py`` 的 ``_build_fundest()``。
        """
        app = _app_without_builder()
        html = app.test_client().get("/fund/SZ162411").get_data(as_text=True)
        self.assertNotIn("fund-est-table", html)

    def test_builder_failure_does_not_break_the_page(self):
        def boom(symbols):
            raise RuntimeError("模拟估值计算失败")

        self.app.extensions["fundest_refs_builder"] = boom
        response = self.client.get("/fund/SZ162411")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("fund-est-table", response.get_data(as_text=True))


def _app_without_builder():
    from web.app import create_app
    from web.services.fund_service import FundService
    from web.tests.test_web import make_source

    return create_app(
        {"TESTING": True},
        fund_service_factory=lambda: FundService(make_source()),
        fundest_service_factory=lambda: FundEstService(),
    )


def _symbol_of(row) -> str:
    """从首格的 ``<a href="/fund/SZ000001">SZ000001</a>`` 里取出符号。"""
    return row.cells[0].split(">")[1].split("<")[0]


if __name__ == "__main__":
    unittest.main()
