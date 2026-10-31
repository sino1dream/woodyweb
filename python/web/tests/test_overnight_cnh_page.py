"""``web/services/overnight_cnh_service.py`` 与 ``/overnightcnh`` 的单测。

不连库、不打网络：引用对象、数据源、``overnight_refs_builder`` 全换成假对象。
重点钉住六件事：

1. 输入串的四种口径（``F`` / ``MCL`` / ``MGC`` / 纯金额）与方向判定；
2. ``F`` 那一支**溢价恒用卖一价**，与买卖方向无关；
3. 两套"建议对冲操作"文案：对冲值口径 vs 持仓折股口径（含 ``，共N股。``）；
4. 建议数量的凑整与"装不下就标红"；
5. 换算不出来时**不崩**：建议数量留空、符号进 ``missing``；
6. 闸门：输入为空只出表单、该方向无量只出代码一格。
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

from web.services.overnight_cnh_service import (  # noqa: E402
    DEFAULT_INPUT,
    NO_TOTAL_SYMBOLS,
    OVERNIGHT_CNH_DISPLAY,
    _strval_number,
    build_columns,
    build_page,
    build_row,
    convert_cnh_input,
    input_presets,
    is_sell_input,
    parse_input,
)


# ---------------------------------------------------------------------------
# 假对象
# ---------------------------------------------------------------------------
class FakeStockRef:
    """场内行情引用：盘口 + 价格显示。"""

    def __init__(self, symbol, bid_quantity="", bid_price="", ask_quantity="", ask_price=""):
        self.symbol = symbol
        self.bid_quantity = bid_quantity
        self.bid_price = bid_price
        self.ask_quantity = ask_quantity
        self.ask_price = ask_price

    def get_symbol(self):
        return self.symbol

    def get_available_quantity(self, is_sell, index=0):
        return self.bid_quantity if is_sell else self.ask_quantity

    def get_available_price(self, is_sell, index=0):
        return self.bid_price if is_sell else self.ask_price

    def get_price_display(self, disp=None, prev=None, precision=None):
        return "%.3f" % float(disp)


class FakeEstRef:
    def __init__(self, symbol):
        self.symbol = symbol

    def get_symbol(self):
        return self.symbol


class FakeFundRef:
    """QDII 那一族：包着场内引用 + 中间层（``GetStockRef`` / ``GetEstRef`` 都有）。"""

    def __init__(self, symbol, stock_ref=None, official=1.0, position=1.0,
                 est_symbol="QQQ", stock_id=1):
        self.symbol = symbol
        self.stock_ref = stock_ref if stock_ref is not None else FakeStockRef(symbol)
        self.official = official
        self.position = position
        self.est_ref = FakeEstRef(est_symbol) if est_symbol else None
        self.stock_id = stock_id

    def get_symbol(self):
        return self.symbol

    def get_stock_id(self):
        return self.stock_id

    def get_stock_ref(self):
        """PHP ``GetStockRef()`` —— 有它就说明走"对冲值"那支。"""
        return self.stock_ref

    def get_official_net_value(self):
        return self.official

    def get_position(self):
        return self.position

    def get_est_ref(self):
        return self.est_ref

    def get_realtime_net_value(self):
        return None

    def get_cny_ref(self):
        return FakeCnyRef()


class FakeCnyRef:
    def get_val(self, date=None):
        return 6.7


class FakeHoldingsRef:
    """持仓型：**没有 ``GetStockRef``**，只有 ``GetHoldingsDate``（判据靠这个）。"""

    def __init__(self, symbol, stock_ref=None, ratios=None, net_value=0.0,
                 holdings_date="2026-09-30", position=1.0, official=1.0):
        self.symbol = symbol
        self.stock_ref = stock_ref if stock_ref is not None else FakeStockRef(symbol)
        self.ratios = ratios or {}
        self.net_value = net_value
        self.holdings_date = holdings_date
        self.position = position
        self.official = official

    def get_symbol(self):
        return self.symbol

    def get_position(self):
        return self.position

    def get_official_net_value(self):
        return self.official

    def get_holdings_date(self):
        return self.holdings_date

    def get_holdings_ratio_array(self):
        return self.ratios

    def get_net_value_string(self):
        return self.net_value

    def get_cny_ref(self):
        return FakeCnyRef()


class FakeFutureRef:
    def __init__(self, price):
        self.price = price

    def get_val(self, date=None):
        return self.price


class FakeHistory:
    """``StockHistorySql`` 只用到 ``get_close``。"""

    def __init__(self, closes=None):
        self.closes = closes or {}

    def get_close(self, stock_id, date):
        return self.closes.get(stock_id)


class FakeSource:
    """``get_stock_hedge``（PHP ``GetStockHedge()``）+ ``connection`` + 符号查表。"""

    def __init__(self, hedges=None, symbols=None, closes=None):
        self.hedges = hedges or {}
        self.symbols = symbols or {}
        self.connection = object() if closes is not None else None
        self._history = FakeHistory(closes)

    def get_stock_hedge(self, symbol, stock_id):
        return self.hedges.get(symbol, 1.0)

    def get_stock_symbol(self, stock_id):
        return self.symbols.get(stock_id)


def _patch_history(source, history):
    """把 :func:`_history_sql` 换成恒返回给定假对象（避免真去建 Sql）。"""
    import web.services.overnight_cnh_service as module

    original = module._history_sql
    module._history_sql = lambda _source: history
    return original


def make_fund(symbol, quantity="", price="", is_sell=False, **kwargs):
    """造一只"某一档有挂单"的 QDII 引用。

    ``is_sell=False``（默认）填**卖盘**（ask）—— 本页正数输入是"买"，
    取的正是这一档；负数输入要显式传 ``is_sell=True`` 填买盘。
    """
    fields = {"bid_quantity": quantity, "bid_price": price} if is_sell else {
        "ask_quantity": quantity, "ask_price": price}
    stock_ref = FakeStockRef(symbol, **fields)
    return FakeFundRef(symbol, stock_ref=stock_ref, **kwargs)


# ---------------------------------------------------------------------------
# 1. 输入串
# ---------------------------------------------------------------------------
class InputTest(unittest.TestCase):
    def test_none_falls_back_to_default(self):
        self.assertEqual(parse_input(None), DEFAULT_INPUT)
        self.assertEqual(DEFAULT_INPUT, "100000")

    def test_clear_keeps_empty_string(self):
        """点"清除"给的 ``''`` 是**通过**的（PHP 那边 ``=== false`` 才回落）。"""
        self.assertEqual(parse_input(""), "")

    def test_zero_is_kept(self):
        self.assertEqual(parse_input("0"), "0")

    def test_direction_from_first_char(self):
        self.assertTrue(is_sell_input("-100000"))
        self.assertTrue(is_sell_input("-1MCL"))
        self.assertFalse(is_sell_input("100000"))
        self.assertFalse(is_sell_input(""))
        #: 负号不在首位就不算卖 —— PHP 只看 ``substr($strInput, 0, 1)``。
        self.assertFalse(is_sell_input("100-000"))

    def test_presets(self):
        """三个预设链接的目标值（``CopyPhpLink($strQuery.'1MCL', ...)``）。"""
        self.assertEqual([item["value"] for item in input_presets()],
                         ["1MCL", "1MGC", "100000F"])


# ---------------------------------------------------------------------------
# 2. 金额 → 对冲口径
# ---------------------------------------------------------------------------
class ConvertTest(unittest.TestCase):
    def test_plain_amount_is_just_abs(self):
        ref = make_fund("SZ161125")
        self.assertEqual(convert_cnh_input(ref, ref.stock_ref, "100000"), 100000.0)
        self.assertEqual(convert_cnh_input(ref, ref.stock_ref, "-50000"), 50000.0)

    def test_fund_suffix_divides_by_fee_and_multiplies_premium(self):
        """``SZ161125`` 的申购费 1.2%，卖一价 3.255、官方净值 3.14。"""
        ref = FakeFundRef("SZ161125", official=3.14, position=0.95,
                          stock_ref=FakeStockRef("SZ161125", ask_price="3.255"))
        value = convert_cnh_input(ref, ref.stock_ref, "100000F")
        expected = 100000 / 1.012 * (3.255 / 3.14) * 0.95
        self.assertAlmostEqual(value, expected, places=6)

    def test_premium_uses_the_ask_price_even_when_selling(self):
        """溢价**恒用卖一价**（``GetAvailablePrice(false)``），与方向无关。"""
        ref = FakeFundRef("SZ161125", official=3.14, position=1.0,
                          stock_ref=FakeStockRef("SZ161125", bid_price="3.2",
                                                 ask_price="3.5"))
        #: 买（正数）与卖（负数）算出来的**绝对值**一样。
        self.assertAlmostEqual(convert_cnh_input(ref, ref.stock_ref, "100000F"),
                               convert_cnh_input(ref, ref.stock_ref, "-100000F"))

    def test_missing_official_net_value_returns_none(self):
        """净值取不到 → ``None``（PHP 在这里除零，见偏离 2）。"""
        ref = FakeFundRef("SZ161125", official=0.0)
        self.assertIsNone(convert_cnh_input(ref, ref.stock_ref, "100000F"))

    def test_fund_fee_table_is_the_php_one(self):
        """``SZ161815`` 1.6%，``SZ164906`` 1.2% —— 费率不同，结果必须不同。"""
        def value(symbol, fee):
            ref = FakeFundRef(symbol, official=1.0, position=1.0,
                              stock_ref=FakeStockRef(symbol, ask_price="1.0"))
            return convert_cnh_input(ref, ref.stock_ref, "100000F") * (1.0 + fee)

        self.assertAlmostEqual(value("SZ161815", 0.016), 100000.0, places=6)
        self.assertAlmostEqual(value("SZ164906", 0.012), 100000.0, places=6)

    def test_future_suffix_multiplies_price_rate_and_lot(self):
        ref = FakeFundRef("SZ162411", position=1.0)
        builder = lambda symbol: {"hf_CL": FakeFutureRef(70.0),
                                  "hf_GC": FakeFutureRef(4000.0)}[symbol]
        #: 1 桶原油：70 美元 × 6.7 汇率 × 100 桶。
        self.assertAlmostEqual(
            convert_cnh_input(ref, ref.stock_ref, "1MCL", builder), 70 * 6.7 * 100.0)
        #: 1 盎司黄金：4000 × 6.7 × 10。
        self.assertAlmostEqual(
            convert_cnh_input(ref, ref.stock_ref, "1MGC", builder), 4000 * 6.7 * 10.0)

    def test_future_missing_price_returns_none(self):
        ref = FakeFundRef("SZ162411")
        self.assertIsNone(
            convert_cnh_input(ref, ref.stock_ref, "1MCL", lambda symbol: None))


# ---------------------------------------------------------------------------
# 3. strval 口径
# ---------------------------------------------------------------------------
class StrvalTest(unittest.TestCase):
    def test_integral_float_has_no_trailing_zero(self):
        """PHP ``strval(871000.0)`` 是 ``'871000'``，不是 ``'871000.0'``。"""
        self.assertEqual(_strval_number(871000.0), "871000")

    def test_one_decimal_is_kept(self):
        self.assertEqual(_strval_number(1234.5), "1234.5")

    def test_negative_zero_keeps_the_sign(self):
        """``round(-0.04, 1)`` 得 ``-0.0``，PHP 的 ``strval`` 给 ``'-0'``。"""
        self.assertEqual(_strval_number(-0.0), "-0")

    def test_none_is_empty(self):
        self.assertEqual(_strval_number(None), "")


# ---------------------------------------------------------------------------
# 4. 单行
# ---------------------------------------------------------------------------
class BuildRowTest(unittest.TestCase):
    def test_no_quantity_leaves_only_the_symbol_cell(self):
        ref = make_fund("SZ161125", quantity="", price="", is_sell=True)
        row, reason = build_row("SZ161125", ref, "100000", source=FakeSource())
        self.assertEqual(row.cells, ["SZ161125"])
        self.assertEqual(reason, "")

    def test_full_row_shape(self):
        ref = make_fund("SZ161125", "99999", "3.255", official=3.14, position=0.95)
        source = FakeSource({"SZ161125": 1731.0})
        row, _ = build_row("SZ161125", ref, "100000", source=source,
                           stock_link=lambda s: "/stock/" + s)
        #: 代码 | 价格 | 量 | 建议数量 | 对冲值 | 建议对冲操作
        self.assertEqual(len(row.cells), 6)
        self.assertEqual(row.cells[1], "3.255")
        self.assertEqual(row.cells[2], "99999")
        #: 100000 ÷ 0.95 ÷ 3.255 = 32337.4 股；÷ 对冲值 1731 → 18.68 → 19 股 SPY；
        #: 再按 19 × 1731 = 32889 折回去，凑整到 100 股得 32900。
        self.assertEqual(row.cells[3], "32900")
        self.assertEqual(row.cells[4], "1731")
        #: 前缀是"卖出"：**买**本基金 → **卖**掉对冲标的（与方向相反那一侧）。
        self.assertEqual(row.cells[5], '卖出19股<a href="/stock/SPY">SPY</a>')

    def test_hint_is_rounded_to_lots(self):
        #: CNH = 100000 纯金额；仓位 0.95、买一价 3.2 → 32894.7 股 → 32900。
        ref = make_fund("SZ161125", "999999", "3.2", official=1.0, position=0.95)
        row, _ = build_row("SZ161125", ref, "100000", source=FakeSource())
        self.assertEqual(row.cells[3], "32900")

    def test_over_quantity_is_red(self):
        ref = make_fund("SZ161125", "100", "3.2", official=1.0, position=0.95)
        row, _ = build_row("SZ161125", ref, "100000", source=FakeSource())
        self.assertEqual(row.cells[3], ("32900", "red"))

    def test_hedge_branch_uses_the_leverage_symbol(self):
        """``SZ161130`` 有 QQQ 杠杆配对 → 文案里的标的是 QQQ。"""
        ref = make_fund("SZ161130", "384", "4.636", is_sell=True,
                        official=4.39, position=0.95)
        source = FakeSource({"SZ161130": 1149.0})
        row, _ = build_row("SZ161130", ref, "-100000", source=source,
                           stock_link=lambda s: "/stock/" + s)
        self.assertIn("QQQ", row.cells[5])
        self.assertEqual(row.cells[4], "1149")

    def test_hedge_branch_without_hedge_leaves_the_memo_empty(self):
        """对冲值取不到（0）→ 两格都空，但建议数量还是算得出来。"""
        ref = make_fund("SZ161125", "999999", "3.2", official=1.0, position=0.95)
        row, _ = build_row("SZ161125", ref, "100000",
                           source=FakeSource({"SZ161125": 0.0}))
        self.assertEqual(row.cells[4], "")
        self.assertEqual(len(row.cells), 5)

    def test_holdings_branch_counts_constituents(self):
        """持仓型：按季报成分股逐只折股，末尾补"，共N股。"。"""
        ref = FakeHoldingsRef(
            "SZ160216",
            stock_ref=FakeStockRef("SZ160216", ask_quantity="200", ask_price="0.5"),
            ratios={11: "10.0", 12: "20.0"},
            net_value=1.0,
            position=1.0,
        )
        history = FakeHistory({11: 100.0, 12: 50.0})
        source = FakeSource(symbols={11: "AAA", 12: "BBB"})
        original = _patch_history(source, history)
        try:
            row, _ = build_row("SZ160216", ref, "10000", source=source,
                               stock_link=lambda s: "/stock/" + s)
        finally:
            import web.services.overnight_cnh_service as module

            module._history_sql = original
        #: usd = 1.0 × hint × 1.0 / 6.7；hint = 10000/1/0.5 = 20000 → usd ≈ 2985.07
        #: 数量 = 2985.07 × 10% / 100 = 2.985 → 3.0；×20%/50 = 11.9 → 11.9
        self.assertEqual(len(row.cells), 6)
        self.assertIn("AAA", row.cells[5])
        self.assertIn("BBB", row.cells[5])
        #: ``SZ160216`` 在 NO_TOTAL_SYMBOLS 里 → **不补**"，共N股。"。
        self.assertIn("SZ160216", NO_TOTAL_SYMBOLS)
        self.assertNotIn("共", row.cells[5])

    def test_holdings_branch_appends_total_for_other_symbols(self):
        ref = FakeHoldingsRef(
            "SH501018",
            stock_ref=FakeStockRef("SH501018", ask_quantity="100", ask_price="2.0"),
            ratios={11: "10.0"},
            net_value=1.0,
        )
        history = FakeHistory({11: 100.0})
        source = FakeSource(symbols={11: "AAA"})
        original = _patch_history(source, history)
        try:
            row, _ = build_row("SH501018", ref, "10000", source=source)
        finally:
            import web.services.overnight_cnh_service as module

            module._history_sql = original
        self.assertIn("共", row.cells[5])
        self.assertTrue(row.cells[5].endswith("股。"))

    def test_holdings_branch_without_holdings_still_says_zero(self):
        """PHP 那句"，共N股。"是**无条件补**的 —— 一只都没折出来也是"，共0股。"。"""
        ref = FakeHoldingsRef(
            "SH501018",
            stock_ref=FakeStockRef("SH501018", ask_quantity="100", ask_price="2.0"),
        )
        row, _ = build_row("SH501018", ref, "10000", source=FakeSource())
        #: ``get_holdings_ratio_array()`` 空 → 直接给后缀（连库都不用）。
        self.assertEqual(row.cells[5], "卖出，共0股。")

    def test_uncomputable_row_keeps_the_reason(self):
        """净值缺失 → 建议数量与对冲格留空，原因回给调用方。"""
        ref = make_fund("SZ161125", "22554", "3.255", official=0.0)
        row, reason = build_row("SZ161125", ref, "100000F", source=FakeSource())
        self.assertEqual(row.cells[3], "")
        self.assertEqual(row.cells[4], "")
        self.assertIn("算不出", reason)

    def test_quantity_is_never_divided_by_zero(self):
        """仓位 0 → 不做除法（PHP 会 ``DivisionByZeroError``）。"""
        ref = make_fund("SZ161125", "22554", "3.255", official=1.0, position=0.0)
        row, reason = build_row("SZ161125", ref, "100000", source=FakeSource())
        self.assertEqual(row.cells[3], "")
        self.assertIn("算不出", reason)

    def test_none_ref_is_skipped(self):
        row, reason = build_row("SZ161125", None, "100000")
        self.assertIsNone(row)
        self.assertTrue(reason)

    def test_first_cell_link(self):
        ref = make_fund("SZ161125", "1", "1")
        row, _ = build_row("SZ161125", ref, "100000", fund_link=lambda s: "/fund/" + s)
        self.assertEqual(row.href, "/fund/SZ161125")


# ---------------------------------------------------------------------------
# 5. 整页
# ---------------------------------------------------------------------------
class BuildPageTest(unittest.TestCase):
    def _symbols(self):
        return ["SZ161125", "SZ161130"]

    def _refs(self):
        return [
            make_fund("SZ161125", "22554", "3.255", is_sell=False,
                      official=3.14, position=0.95),
            make_fund("SZ161130", "384", "4.636", is_sell=False,
                      official=4.39, position=0.95),
        ]

    def test_empty_input_has_no_table(self):
        page = build_page("", self._symbols(), self._refs())
        self.assertIsNone(page["table"])
        self.assertEqual(page["input"], "")

    def test_default_input(self):
        page = build_page(None, [], [])
        self.assertEqual(page["input"], DEFAULT_INPUT)

    def test_prefix_flips_with_the_sign(self):
        page = build_page("100000", self._symbols(), self._refs())
        self.assertEqual(page["direction"]["prefix"], "可买")
        self.assertEqual(page["direction"]["op"], "买")
        page = build_page("-100000", self._symbols(), self._refs())
        self.assertEqual(page["direction"]["prefix"], "可卖")

    def test_six_columns(self):
        columns = build_columns("可买")
        self.assertEqual(len(columns), 6)
        self.assertEqual(columns[0]["text"], "代码")
        self.assertEqual(columns[1]["text"], "可买价格")
        self.assertEqual(columns[2]["text"], "可买数量")
        self.assertEqual(columns[3]["text"], "建议数量")
        self.assertEqual(columns[4]["text"], "对冲值")
        self.assertEqual(columns[5]["text"], "建议对冲操作")

    def test_last_column_eats_the_remaining_width(self):
        columns = build_columns("可买", layout_width=1000)
        self.assertEqual(columns[5]["width"], 1000 - sum(c["width"] for c in columns[:5]))

    def test_rows_follow_the_symbol_order(self):
        page = build_page("100000", self._symbols(), self._refs(), source=FakeSource())
        self.assertEqual([row.cells[0] for row in page["table"]["rows"]],
                         self._symbols())

    def test_none_ref_goes_to_missing(self):
        page = build_page("100000", ["SZ161125"], [None], source=FakeSource())
        self.assertEqual(page["missing"], [{"symbol": "SZ161125",
                                            "reason": "引用对象建不出来"}])
        self.assertEqual(page["table"]["count"], 0)

    def test_uncomputable_symbols_are_listed_but_kept_as_rows(self):
        refs = [make_fund("SZ161125", "22554", "3.255", is_sell=False, official=0.0)]
        page = build_page("100000F", ["SZ161125"], refs, source=FakeSource())
        self.assertEqual(len(page["missing"]), 1)
        self.assertEqual(page["table"]["count"], 1)

    def test_table_id(self):
        page = build_page("100000", [], [], source=FakeSource())
        self.assertEqual(page["table"]["id"], "overnightcnh-table")

    def test_title(self):
        self.assertEqual(build_page(None, [], [])["title"], OVERNIGHT_CNH_DISPLAY)


# ---------------------------------------------------------------------------
# 6. 路由
# ---------------------------------------------------------------------------
class RouteTest(unittest.TestCase):
    """路由层：默认值、查询串、清除、预设链接。"""

    def _client(self):
        from web.app import create_app

        registry = {
            "SZ161125": make_fund("SZ161125", "22554", "3.255", is_sell=False,
                                  official=3.14, position=0.95),
            "SZ161130": make_fund("SZ161130", "384", "4.636", is_sell=False,
                                  official=4.39, position=0.95),
        }

        class FakeReferenceService:
            source = FakeSource({"SZ161125": 1731.0, "SZ161130": 1149.0})

        def builder(symbols):
            return [registry.get(symbol) for symbol in symbols]

        app = create_app(
            {"TESTING": True},
            reference_service_factory=lambda: FakeReferenceService(),
            overnight_refs_builder=builder,
        )
        return app.test_client()

    def test_get_defaults_to_100000(self):
        text = self._client().get("/overnightcnh").get_data(as_text=True)
        self.assertIn(OVERNIGHT_CNH_DISPLAY, text)
        self.assertIn('value="100000"', text)
        self.assertIn("可买价格", text)

    def test_query_prefills_the_amount(self):
        text = self._client().get("/overnightcnh?overnightcnh=95000").get_data(as_text=True)
        self.assertIn('value="95000"', text)

    def test_negative_query_flips_the_direction(self):
        text = self._client().get("/overnightcnh?overnightcnh=-100000").get_data(as_text=True)
        self.assertIn("可卖价格", text)

    def test_post_submit(self):
        text = self._client().post(
            "/overnightcnh", data={"submit": "1", "input": "88000"}
        ).get_data(as_text=True)
        self.assertIn('value="88000"', text)

    def test_post_clear_renders_the_form_only(self):
        text = self._client().post(
            "/overnightcnh", data={"clear": "1"}
        ).get_data(as_text=True)
        self.assertNotIn("建议对冲操作", text)
        self.assertIn('value=""', text)

    def test_preset_links_are_on_the_page(self):
        text = self._client().get("/overnightcnh").get_data(as_text=True)
        for value in ("1MCL", "1MGC", "100000F"):
            self.assertIn("overnightcnh=" + value, text)

    def test_page_renders_rows(self):
        text = self._client().get("/overnightcnh?overnightcnh=100000").get_data(as_text=True)
        self.assertIn("SZ161125", text)
        self.assertIn("SZ161130", text)

    def test_status_is_always_200(self):
        """这一页没有"符号不存在"的分支 —— 任何输入都是 200。"""
        client = self._client()
        for url in ("/overnightcnh", "/overnightcnh?overnightcnh=", "/overnightcnh?overnightcnh=zzz"):
            self.assertEqual(client.get(url).status_code, 200)


if __name__ == "__main__":
    unittest.main()
