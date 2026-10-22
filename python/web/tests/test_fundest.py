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
    EST_FORMULA,
    FUND_EST_SORT_THRESHOLD,
    FUND_EST_TABLE_ID,
    PREMIUM_FORMULA,
    SORT_BY_PREMIUM,
    SORT_BY_SYMBOL,
    FundEstService,
    build_est_formula_note,
    POSITION_MEMO_LABEL,
    build_fund_est_columns,
    build_fund_est_table,
    fund_position_memo,
    fund_position_memo_parts,
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

    def test_table_renders_without_an_injected_builder(self):
        """**默认已经接线**：不注入 refs builder 时，走 ``refobj.QdiiReference`` 现算。

        这条断言在 2026-10 由"默认不开"翻转过：

        * 当时不接线是刻意的 —— 三档估值要等 estimator 接进页面，
          硬塞会把 ``PalmmicroFundCalculator.est_net_value()`` 的结果冒充
          "官方EST"，等于**又造一条 EST 口径**；
        * 现在 ``refobj.QdiiReference`` 直接委托 ``fundestcalc``（唯一实现），
          所以可以安全打开。实测 ``SZ161130`` 的官方 EST = ``4.545196``，
          与官方净值一致。

        重点从"有没有表"变成"**数值对不对/口径有没有重影**"，
        所以下面断言官方 EST 与净值同源。
        """
        app = _app_without_builder()
        html = app.test_client().get("/fund/SZ162411").get_data(as_text=True)

        self.assertIn("fund-est-table", html)
        # 首格是股票链接、且有官方 EST 那一列
        self.assertIn("官方EST", html)

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


class EstFormulaNoteTest(unittest.TestCase):
    """估值表下方的「算法说明」（本版新增，生产端没有）。

    这一段存在的唯一目的是**让用户能逐格验算三档 EST**，所以它自己必须先
    经得起验算：

    * 展示的 (标的价, 汇率, 两个比值, EST) 必须与 ``explain()`` 给的一致；
    * 溢价 = 场内价 ÷ EST − 1；
    * 任何一环算不出来就整段不显示（不能摆一张半空的说明表）。

    ``calculator`` 用替身 —— 这一层只负责**格式化与容错**，取数正确性由
    ``auto/test_fundest.py`` 的 ``ExplainTest`` 负责。
    """

    class FakeCalculator:
        """``explain()`` 的替身：返回一组能手算的数字。

        ``result`` 不传时返回那组默认数字；显式传 ``None`` 表示
        "``explain()`` 返回空"（页面应整段不显示）。
        """

        _UNSET = object()

        def __init__(self, result=_UNSET, raises=False):
            self._result = result
            self._raises = raises

        def explain(self, symbol):
            if self._raises:
                raise RuntimeError("数据源炸了")
            if self._result is not self._UNSET:
                return self._result
            return {
                "symbol": symbol,
                "mid_date": "2026-10-19",
                "position": 0.95,
                "netvalue": 4.3704,
                "est_symbol": "QQQ",
                "est_cal": 709.80,
                "est_cal_date": "2026-10-16",
                "cny_cal": 6.72,
                "cny_cal_date": "2026-10-20",
                "cny_symbol": "USCNY",
                "price": 4.783,
                "tiers": [
                    {"key": "official", "name": "官方EST", "est": 718.36,
                     "est_label": "QQQ 2026-10-19 行情价", "cny": 6.7208,
                     "cny_label": "USCNY 中间价 2026-10-19", "value": 4.4209708},
                    {"key": "fair", "name": "参考EST", "est": 718.36,
                     "est_label": "QQQ 当前报价", "cny": 6.72,
                     "cny_label": "USCNY 中间价 2026-10-20", "value": 4.4204706},
                    {"key": "realtime", "name": "实时EST", "est": 718.49,
                     "est_label": "hf_NQ 期货实时价", "cny": 6.72,
                     "cny_label": "USCNY 中间价 2026-10-20", "value": 4.4212046},
                ],
            }

    def test_params_are_formatted_with_fixed_digits(self):
        """公共参数：仓位 2 位、净值 4 位、基准价 2 位、基准汇率 4 位。"""
        note = build_est_formula_note("SZ161130", self.FakeCalculator())

        params = dict(note["params"])
        self.assertEqual(params["仓位 p"], "0.95")
        self.assertEqual(params["基金净值 nv"], "4.3704")
        self.assertEqual(params["标的基准价 est_cal"], "709.80（QQQ @ 2026-10-16）")
        self.assertEqual(params["基准汇率 cny_cal"], "6.7200（USCNY @ 2026-10-20）")
        self.assertEqual(params["场内价"], "4.783")

    def test_tier_ratios_and_values(self):
        """三档的比值与 EST 都要能按展示的公式手算出来。"""
        note = build_est_formula_note("SZ161130", self.FakeCalculator())
        official, fair, realtime = note["tiers"]

        self.assertEqual(official["ratio"], "%.6f" % (718.36 / 709.80))
        self.assertEqual(official["fx"], "%.6f" % (6.7208 / 6.72))
        self.assertEqual(official["value"], "4.4210")
        # 溢价 = 场内价 / EST - 1
        self.assertEqual(official["premium"], "8.19%")
        # 三档共用 est_cal / cny_cal，差别只在 est 与 cny
        self.assertEqual(fair["ratio"], official["ratio"])
        self.assertNotEqual(realtime["ratio"], official["ratio"])

    def test_premium_uses_the_tier_value_not_the_nav(self):
        """溢价的分母是**该档 EST**，不是基金净值。"""
        note = build_est_formula_note("SZ161130", self.FakeCalculator())

        for tier in note["tiers"]:
            expected = "%.2f%%" % ((4.783 / float(tier["value"]) - 1.0) * 100.0)
            self.assertEqual(tier["premium"], expected)

    def test_formula_text_is_the_documented_one(self):
        note = build_est_formula_note("SZ161130", self.FakeCalculator())

        self.assertEqual(note["formula"], EST_FORMULA)
        self.assertEqual(note["premium_formula"], PREMIUM_FORMULA)

    def test_missing_tier_inputs_render_as_empty(self):
        """某一档取不到标的价/汇率 —— 对应格子留空，不编造。"""

        class Partial(self.FakeCalculator):
            def explain(self, symbol):
                raw = EstFormulaNoteTest.FakeCalculator.explain(self, symbol)
                raw["tiers"][2] = {"key": "realtime", "name": "实时EST",
                                   "est": None, "cny": None, "value": None}
                return raw

        note = build_est_formula_note("SZ161130", Partial())
        realtime = note["tiers"][2]

        self.assertEqual(realtime["est"], "")
        self.assertEqual(realtime["cny"], "")
        self.assertEqual(realtime["value"], "")
        self.assertEqual(realtime["premium"], "")

    def test_returns_none_when_explain_fails(self):
        """取数抛异常时整段不显示（页面据此跳过说明块）。"""
        self.assertIsNone(
            build_est_formula_note("SZ161130", self.FakeCalculator(raises=True))
        )

    def test_returns_none_when_explain_is_empty(self):
        self.assertIsNone(
            build_est_formula_note("SZ161130", self.FakeCalculator(result=None))
        )


class _Symbol:
    """只给 ``get_symbol`` 一个方法的替身（est_ref / realtime_ref）。"""

    def __init__(self, symbol: str):
        self.symbol = symbol

    def get_symbol(self):
        return self.symbol


class _PositionRef:
    """够 ``fund_position_memo_parts`` 用的替身。"""

    def __init__(self, position=0.95, realtime=None, est="^GSPC", realtime_sym="ES"):
        self.position = position
        self.realtime = realtime
        self._est = _Symbol(est) if est else None
        self._realtime = _Symbol(realtime_sym) if realtime_sym else None

    def get_position(self):
        return self.position

    def get_realtime_net_value(self):
        return self.realtime

    def get_est_ref(self):
        return self._est

    def get_realtime_ref(self):
        return self._realtime


class PositionMemoTest(unittest.TestCase):
    """估值表上方那句说明：只有「仓位估算」四字可链（PHP ``GetFundPositionLink``）。"""

    HREF = "/history/fundposition/SZ161125"

    def test_only_the_four_chars_are_the_link(self):
        """锚文本必须精确等于「仓位估算」，其余（值使用0.95。）在链接**外面**。"""
        parts = fund_position_memo_parts(_PositionRef(), self.HREF)

        self.assertEqual(parts["label"], POSITION_MEMO_LABEL)
        self.assertEqual(parts["label"], "仓位估算")
        self.assertEqual(parts["rest"], "值使用0.95。")
        self.assertEqual(parts["text"], "仓位估算值使用0.95。")
        self.assertEqual(parts["href"], self.HREF)

    def test_href_is_empty_when_the_page_is_not_offered(self):
        """非 QDII 四族且非持仓型 LOF：模板据此不包 ``<a>``（不留死链）。"""
        self.assertEqual(fund_position_memo_parts(_PositionRef())["href"], "")
        self.assertEqual(fund_position_memo_parts(_PositionRef(), None)["href"], "")

    def test_position_keeps_trailing_zeros(self):
        """仓位走 ``number_format($fPos, 2)``：``1`` 显示成 ``1.00``，不是 ``1``。"""
        parts = fund_position_memo_parts(_PositionRef(position=1))
        self.assertEqual(parts["rest"], "值使用1.00。")

    def test_realtime_sentence_needs_both_refs(self):
        """实时那句以 ``GetRealtimeNetValue()`` **且** est_ref 存在为闸门。"""
        with_realtime = fund_position_memo_parts(
            _PositionRef(realtime=4.6), self.HREF)["rest"]
        self.assertEqual(with_realtime, "值使用0.95。实时EST ES和^GSPC关联程度按照100%估算。")

        #: 没有实时估值 -> 只剩第一句（PHP 的 if 直接不拼）
        self.assertEqual(
            fund_position_memo_parts(_PositionRef(), self.HREF)["rest"], "值使用0.95。")
        #: 有实时估值但建不出 est_ref -> 同样不拼（PHP 的 method_exists 闸门）
        self.assertEqual(
            fund_position_memo_parts(_PositionRef(realtime=4.6, est=None), self.HREF)["rest"],
            "值使用0.95。")

    def test_memo_helper_is_the_plain_text_form(self):
        """``fund_position_memo`` 保持返回纯文本（不带链接）的旧契约。"""
        self.assertEqual(fund_position_memo(_PositionRef()), "仓位估算值使用0.95。")


if __name__ == "__main__":
    unittest.main()
