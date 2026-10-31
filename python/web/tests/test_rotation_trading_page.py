"""``web/services/rotation_service.py`` 与 ``/stock/<symbol>/rotationtrading`` 的单测。

不连库、不打网络：引用对象、数据源、``reference_refs_builder`` 全换成假对象。
重点钉住四件事：

1. 四族**分派顺序**，以及标普那一支放宽到 ``SpyQdii`` 的刻意偏离；
2. 方向的两层翻转（本基金卖 → 候选取卖价、表头前缀"可买"）；
3. 建议数量的两层取整（``round(x/100)*100``）与"装不下就标红"；
4. 闸门：非族 → 只有标题；本基金无档位量 → 无说明无表；对冲值为 0 → 不崩。
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

from web.services.rotation_service import (  # noqa: E402
    DEFAULT_ROTATION_INPUT,
    ROTATION_TRADING_DISPLAY,
    build_rotation_page,
    build_row,
    hint_quantity,
    number_display,
    rotation_peers,
    rotation_symbol_array,
)


# ---------------------------------------------------------------------------
# 假对象
# ---------------------------------------------------------------------------
class FakeEstRef:
    """中间层引用：只需要一个符号（XOP/XBI 两族的表头用它）。"""

    def __init__(self, symbol: str = "^SPXOP"):
        self.symbol = symbol

    def get_symbol(self):
        return self.symbol


class FakeStockRef:
    """场内行情引用：盘口 + 价格显示 + 折溢价。"""

    def __init__(self, symbol: str, bid_quantity: str = "", bid_price: str = "",
                 ask_quantity: str = "", ask_price: str = "", stock_id: int = 1):
        self.symbol = symbol
        self.stock_id = stock_id
        self.bid_quantity = bid_quantity
        self.bid_price = bid_price
        self.ask_quantity = ask_quantity
        self.ask_price = ask_price
        self.external_link = "https://example.com/%s" % symbol

    def get_symbol(self):
        return self.symbol

    def get_stock_id(self):
        return self.stock_id

    def get_available_quantity(self, is_sell, index=0):
        return self.bid_quantity if is_sell else self.ask_quantity

    def get_available_price(self, is_sell, index=0):
        return self.bid_price if is_sell else self.ask_price

    def get_price_display(self, disp=None, prev=None, precision=None):
        return "%.3f" % float(disp)

    def get_percentage_text(self, divisor=None, dividend=None):
        if not divisor:
            return ""
        return "%.2f%%" % ((float(dividend) / float(divisor) - 1.0) * 100.0)

    def get_external_link(self):
        return self.external_link


class FakeFundRef:
    """基金引用：包着场内引用 + 一档估值。"""

    def __init__(self, symbol: str, stock_ref: FakeStockRef = None, est=None,
                 est_symbol: str = "^NDX"):
        self.symbol = symbol
        self.stock_ref = stock_ref if stock_ref is not None else FakeStockRef(symbol)
        self.est = est
        self.est_ref = FakeEstRef(est_symbol)

    def get_symbol(self):
        return self.symbol

    def get_stock_id(self):
        return self.stock_ref.get_stock_id()

    def get_est_net_value(self):
        return self.est

    def get_est_ref(self):
        return self.est_ref


class FakeSource:
    """只提供 ``get_stock_hedge``（PHP ``GetStockHedge()``）。"""

    def __init__(self, hedges=None):
        self.hedges = hedges or {}

    def get_stock_hedge(self, symbol, stock_id):
        return self.hedges.get(symbol, 1.0)


def make_fund(symbol: str, quantity: str = "", price: str = "", is_sell: bool = True,
              est=None, stock_id: int = 1) -> FakeFundRef:
    """造一个"某一档有挂单"的基金引用。"""
    kwargs = {"stock_id": stock_id}
    if is_sell:
        kwargs.update(bid_quantity=quantity, bid_price=price)
    else:
        kwargs.update(ask_quantity=quantity, ask_price=price)
    return FakeFundRef(symbol, FakeStockRef(symbol, **kwargs), est=est)


# ---------------------------------------------------------------------------
# 1. 族分派
# ---------------------------------------------------------------------------
class SymbolArrayTest(unittest.TestCase):
    def test_qqq_match_returns_twelve(self):
        self.assertEqual(len(rotation_symbol_array("SH513100")), 12)

    def test_spy_match_returns_four(self):
        array = rotation_symbol_array("SZ161125")
        self.assertEqual(array, ["SH513500", "SH513650", "SZ159612", "SZ161125"])

    def test_spy_qdii_only_symbol_gets_the_page_now(self):
        """``SZ159655`` 在 ``SpyQdii`` 里但**不在** ``SpyMatch`` 里。

        PHP 的入口链接判 ``SpyQdii``、页面判 ``SpyMatch``，于是它"有链接、
        无内容"（生产端实测确认）。本版把页面这一侧放宽到 ``SpyQdii``，
        所以这里必须拿得到那 4 只候选 —— 不是空。
        """
        array = rotation_symbol_array("SZ159655")
        self.assertEqual(array, ["SH513500", "SH513650", "SZ159612", "SZ161125"])

    def test_xop_and_xbi(self):
        self.assertEqual(rotation_symbol_array("SH513350"), ["SH513350", "SZ159518", "SZ162411"])
        self.assertEqual(rotation_symbol_array("SZ159502"), ["SZ159502", "SZ161127"])

    def test_unknown_symbol_returns_none(self):
        """``SPY`` 本身、以及不在四族里的基金都不进这一页。"""
        self.assertIsNone(rotation_symbol_array("SPY"))
        self.assertIsNone(rotation_symbol_array("SZ164906"))
        self.assertIsNone(rotation_symbol_array(""))

    def test_order_matters_qqq_before_spy(self):
        """``SZ161130`` 同时在 ``QqqMatch`` 与……只在一族里。

        分派的顺序是 XOP → XBI → QQQ → SPY（照抄 PHP）。用一只属于 QQQ 族
        的符号确认它拿到的是 12 只那一组，而不是标普那 4 只。
        """
        self.assertEqual(len(rotation_symbol_array("SZ161130")), 12)

    def test_peers_drop_self(self):
        self.assertNotIn("SH513100", rotation_peers("SH513100", rotation_symbol_array("SH513100")))
        # 放宽后 SZ159655 自己不在候选数组里 → 4 只全留
        self.assertEqual(len(rotation_peers("SZ159655", rotation_symbol_array("SZ159655"))), 4)


# ---------------------------------------------------------------------------
# 2. 数字显示
# ---------------------------------------------------------------------------
class NumberDisplayTest(unittest.TestCase):
    def test_negative_is_red_and_rounded_to_integer(self):
        self.assertEqual(number_display(-409.49, 0), ("-409", "red"))

    def test_positive_is_not_coloured(self):
        """PHP ``GetNumberDisplay`` 的 ``$strGreater = 'black'`` —— 正数不染色。"""
        self.assertEqual(number_display(2442.03, 0), ("2442", ""))

    def test_near_zero_is_grey(self):
        self.assertEqual(number_display(0.4, 0), ("0", "grey"))

    def test_rounds_half_away_from_zero(self):
        """``number_format`` 是 half-up：``2126.909`` → ``2127``。"""
        self.assertEqual(number_display(2126.909, 0)[0], "2127")


# ---------------------------------------------------------------------------
# 3. 建议数量
# ---------------------------------------------------------------------------
class HintQuantityTest(unittest.TestCase):
    def test_rounds_to_lots_of_one_hundred(self):
        self.assertEqual(hint_quantity(1149.3, -409.49), 470600.0)

    def test_negative_est_quantity_becomes_positive(self):
        """卖出方向时 ``est_quantity`` 是负数，``abs()`` 之后数量本身只关心大小。"""
        self.assertEqual(hint_quantity(1000, -500), 500000.0)

    def test_zero_hedge_gives_zero(self):
        self.assertEqual(hint_quantity(0, -409.0), 0.0)


# ---------------------------------------------------------------------------
# 4. 单行
# ---------------------------------------------------------------------------
class BuildRowTest(unittest.TestCase):
    def _row(self, ref, is_rotate_sell=False, est_quantity=-409.49, hedge=1149.3):
        return build_row(ref, is_rotate_sell, est_quantity,
                         lambda symbol, _ref: hedge,
                         link_builder=lambda s: "/stock/%s/rotationtrading" % s)

    def test_no_quantity_leaves_a_single_cell(self):
        """PHP 先压代码那格，再看有没有量 —— 没量就只剩一格。"""
        row = self._row(make_fund("SH513110"))
        self.assertEqual(len(row.cells), 1)
        self.assertEqual(row.cells[0], "SH513110")
        self.assertEqual(row.href, "/stock/SH513110/rotationtrading")

    def test_full_row_has_seven_cells(self):
        ref = make_fund("SH513110", quantity="353900", price="2.456", is_sell=False, est=2.18)
        row = self._row(ref, is_rotate_sell=False)
        self.assertEqual(len(row.cells), 7)
        self.assertEqual(row.cells[1], "2.456")
        self.assertEqual(row.cells[2], "353900")
        self.assertEqual(row.cells[5], ("-409", "red"))

    def test_overflow_marks_the_hint_quantity_red(self):
        """建议数量 > 可成交数量 → 红字（PHP ``GetFontElement()`` 默认红）。"""
        ref = make_fund("SH513110", quantity="353900", price="2.456", is_sell=False, est=2.18)
        row = self._row(ref, is_rotate_sell=False)
        self.assertEqual(row.cells[3], ("470600", "red"))

    def test_quantity_within_the_level_is_plain(self):
        ref = make_fund("SH513110", quantity="99999900", price="2.456", is_sell=False, est=2.18)
        row = self._row(ref, is_rotate_sell=False)
        self.assertEqual(row.cells[3], "470600")

    def test_missing_estimate_skips_the_premium_cell(self):
        """``if ($fEst = $rotation_ref->GetEstNetValue())`` —— 没估值就少一格。"""
        ref = make_fund("SH513110", quantity="353900", price="2.456", is_sell=False)
        self.assertEqual(len(self._row(ref, is_rotate_sell=False).cells), 6)

    def test_none_ref_is_skipped(self):
        self.assertIsNone(build_row(None, False, -1.0, lambda s, r: 1.0))


# ---------------------------------------------------------------------------
# 5. 整页
# ---------------------------------------------------------------------------
class BuildPageTest(unittest.TestCase):
    def _page(self, input_text=None, fund=None, peers=None, hedge=None, symbols=None,
              symbol="SH513100"):
        fund = fund if fund is not None else make_fund(symbol, "2615400", "2.289", est=1.9)
        peers = peers if peers is not None else [make_fund("SH513110", "353900", "2.456", is_sell=False, est=2.18)]
        source = FakeSource(hedge if hedge is not None else {"SH513100": 2442.03, "SH513110": 2126.9})
        return build_rotation_page(
            symbol,
            rotation_symbols=symbols if symbols is not None else rotation_symbol_array(symbol),
            fund_ref=fund,
            source=source,
            rotation_refs=peers,
            input_text=input_text,
            link_builder=lambda s: "/stock/%s/rotationtrading" % s,
        )

    def test_default_input_is_the_placeholder_and_sells(self):
        page = self._page()
        self.assertEqual(page["input"], "-1000000")
        self.assertTrue(page["direction"]["is_sell"])
        self.assertEqual(page["direction"]["op"], "卖")
        self.assertEqual(page["direction"]["rotate_op"], "买")
        self.assertEqual(page["direction"]["prefix"], "可买")

    def test_clear_falls_back_to_the_placeholder(self):
        """``GetEditInput()`` 对 ``''`` 返回假值 → 仍是 -1000000，不是 0。"""
        self.assertEqual(self._page("")["input"], "-1000000")
        self.assertEqual(self._page("0")["input"], "-1000000")

    def test_positive_input_flips_the_direction(self):
        page = self._page("100000")
        self.assertFalse(page["direction"]["is_sell"])
        self.assertEqual(page["direction"]["prefix"], "可卖")
        self.assertEqual(page["input"], "100000")

    def test_summary_reads_the_bid_side_when_selling(self):
        page = self._page()
        self.assertEqual(page["summary"]["price"], "2.289")
        self.assertEqual(page["summary"]["quantity"], "2615400")
        self.assertTrue(page["summary"]["premium"].endswith("%"))
        self.assertTrue(page["summary"]["link"]["url"].startswith("https://example.com/"))

    def test_hedge_symbol_prefers_the_leverage_pair(self):
        self.assertEqual(self._page()["hedge_symbol"], "QQQ")

    def test_hedge_symbol_falls_back_to_the_est_ref(self):
        """XOP/XBI 两族没有 QQQ/SPY 配对 → 表头用中间层符号。"""
        fund = make_fund("SH513350", "10000", "1.2", est=1.1)
        fund.est_ref = FakeEstRef("^SPXOP")
        page = self._page(fund=fund, peers=[], symbol="SH513350")
        self.assertEqual(page["hedge_symbol"], "^SPXOP")

    def test_no_quote_means_no_summary_and_no_table(self):
        """本基金这一档没量 → PHP 那个 ``if`` 包住了说明行与表。"""
        page = self._page(fund=make_fund("SH513100"))
        self.assertIsNone(page["summary"])
        self.assertIsNone(page["table"])

    def test_zero_hedge_does_not_divide_by_zero(self):
        """PHP 8 这里会 ``DivisionByZeroError`` 整页 500；本版退化成说明文字。"""
        page = self._page(hedge={"SH513100": 0.0})
        self.assertIsNotNone(page["summary"])
        self.assertIn("对冲值为 0", page["summary"]["error"])
        self.assertIsNone(page["table"])

    def test_not_in_any_group_has_no_table(self):
        page = build_rotation_page("SPY", rotation_symbols=None, fund_ref=make_fund("SPY"))
        self.assertFalse(page["available"])
        self.assertIsNone(page["table"])
        self.assertEqual(page["title"], "SPY" + ROTATION_TRADING_DISPLAY)

    def test_table_id_and_columns(self):
        page = self._page()
        self.assertEqual(page["table"]["id"], "SH513100rotationtrading")
        texts = [column["text"] for column in page["table"]["columns"]]
        self.assertEqual(texts, ["代码", "可买价格", "可买数量", "建议数量", "对冲值", "QQQ数量", "溢价"])

    def test_default_constant_is_negative(self):
        self.assertLess(DEFAULT_ROTATION_INPUT, 0)


# ---------------------------------------------------------------------------
# 6. 路由
# ---------------------------------------------------------------------------
class RouteTest(unittest.TestCase):
    """路由层：三族页面能开、方向翻转、放宽后的 SZ159655 有内容。"""

    def _client(self):
        from web.app import create_app

        fund = make_fund("SH513100", "2615400", "2.289", est=1.9, stock_id=3)
        #: 买/卖两侧都有挂单 —— 否则 POST 正数（买本基金）会掉进"一档无量"空态，
        #: 那不是方向翻转的问题，而是演示库的真实现状（多数标的只有一侧数据）。
        fund.stock_ref.ask_quantity = "8104"
        fund.stock_ref.ask_price = "2.290"
        spx = make_fund("SZ159655", "1376800", "1.984", est=1.83, stock_id=170)
        peers = {
            "SH513110": make_fund("SH513110", "353900", "2.456", is_sell=False, est=2.18),
            "SH513500": make_fund("SH513500", "849300", "2.658", is_sell=False, est=2.4),
            "SH513650": make_fund("SH513650", "2531000", "1.983", is_sell=False, est=1.83),
            "SZ159612": make_fund("SZ159612", "286793", "2.052", is_sell=False, est=1.9),
            "SZ161125": make_fund("SZ161125", "22554", "3.255", is_sell=False, est=3.1),
        }
        registry = {"SH513100": fund, "SZ159655": spx, **peers}

        class FakeReferenceService:
            source = FakeSource({"SH513100": 2442.03, "SZ159655": 2760.0,
                                 "SH513110": 2126.9, "SH513500": 2107.7})

        def builder(symbols):
            return [registry.get(item) for item in symbols]

        app = create_app(
            {"TESTING": True},
            reference_service_factory=lambda: FakeReferenceService(),
            reference_refs_builder=builder,
        )
        return app.test_client()

    def test_page_renders(self):
        text = self._client().get("/stock/SH513100/rotationtrading").get_data(as_text=True)
        self.assertIn("SH513100轮动交易", text)
        self.assertIn("目前可卖价格2.289", text)
        self.assertIn("SH513110", text)

    def test_post_flips_the_prefix(self):
        text = self._client().post(
            "/stock/SH513100/rotationtrading", data={"submit": "1", "input": "100000"}
        ).get_data(as_text=True)
        self.assertIn("目前可买价格", text)
        self.assertIn("可卖价格", text)   # 表头前缀跟着翻

    def test_spy_qdii_symbol_is_no_longer_blank(self):
        """放宽到 ``SpyQdii`` 之后 ``SZ159655`` 有 4 行候选，不再是空页面。"""
        text = self._client().get("/stock/SZ159655/rotationtrading").get_data(as_text=True)
        self.assertIn("目前可卖价格1.984", text)
        for symbol in ("SH513500", "SH513650", "SZ159612", "SZ161125"):
            self.assertIn(symbol, text)

    def test_symbol_outside_the_four_groups_shows_empty_state(self):
        text = self._client().get("/stock/SPY/rotationtrading").get_data(as_text=True)
        self.assertIn("不在轮动分组里", text)


if __name__ == "__main__":
    unittest.main()
