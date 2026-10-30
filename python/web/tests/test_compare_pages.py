"""``web/services/compare_page_service.py`` 与 ``ah_history_service.py`` 的单测。

不打网络、不连数据库：``MysqlReference`` / ``StockGetPairReferences`` /
``AhPairReference`` / ``StockHistorySql`` 全部换成假对象。重点钉住三件事：

1. compare 三页的符号数组必须来自**对应的配对表**（不是硬编码）；
2. ``_echoStockGroupArray()`` 的分类口径：同符号的配对引用只收一次，
   且"宽屏 + 已配对"时不再进参考数据表；
3. AH 历史表按 PHP 的三条闸门过滤（中间价取不到整天跳过、H 价取不到
   只留前三格、比价非零才补两格）。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT_DIR = Path(__file__).resolve().parents[3]
PYTHON_DIR = ROOT_DIR / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from web.bootstrap import install_paths  # noqa: E402

install_paths()

from web.services import ah_history_service, compare_page_service  # noqa: E402
from web.services.ah_history_service import build_ah_history  # noqa: E402
from web.services.compare_page_service import (  # noqa: E402
    COMPARE_PAGES,
    CompareNotFoundError,
    build_compare_page,
    compare_symbols,
)


class FakePairRef:
    """够用的配对引用对象（只要 PairService 调得到的方法）。"""

    def __init__(self, symbol: str = "HK06030", price: float = 10.0, ratio: float = 1.2):
        self.symbol = symbol
        self.price = price
        self.ratio = ratio

    def get_symbol(self):
        return self.symbol

    def get_my_stock_link(self):
        return '<a href="/mystock/%s">%s</a>' % (self.symbol, self.symbol)

    def get_price(self):
        return self.price

    def get_price_display(self, disp=None, prev=None, precision=None):
        return "%.3f" % (self.price if disp is None else float(disp))

    def get_price_ratio(self, date=None):
        return self.ratio

    def get_stock_id(self):
        return 1

    def has_data(self):
        return True


class FakeRef:
    """普通引用对象；``fund=False`` 时走配对那一支。"""

    def __init__(self, symbol: str = "SH600030", pair=None, fund: bool = False):
        self.symbol = symbol
        self.pair = pair
        self.fund = fund

    def get_symbol(self):
        return self.symbol

    def is_fund_a(self):
        return self.fund

    def get_my_stock_link(self):
        return '<a href="/mystock/%s">%s</a>' % (self.symbol, self.symbol)

    def get_pair_ref(self):
        return self.pair

    def get_price_ratio(self, date=None):
        return 1.0 if self.pair is not None else None


class FakeSource:
    """没有 ``connection`` 的数据源：用来验"连不上库就是空态"。"""

    connection = None


class CompareSymbolsTest(unittest.TestCase):
    """``GetCategoryArray()`` 的三个 compare 分支。"""

    def test_unknown_name_raises(self):
        with self.assertRaises(CompareNotFoundError):
            compare_symbols("nosuch", FakeSource())

    def test_without_connection_returns_empty(self):
        self.assertEqual(compare_symbols("ahcompare", FakeSource()), [])

    def test_uses_the_matching_pair_table(self):
        """三页各查自己的表；注入 store 类比连真库更容易看清方向。"""
        calls = []

        class RecordingStore:
            def __init__(self, connection):
                self.connection = connection

            def get_symbol_array(self):
                calls.append(self.connection)
                return ["SH600030"]

        with mock.patch.dict(
            compare_page_service.PAIR_STORES,
            {"ahcompare": RecordingStore},
        ):
            source = mock.Mock(connection="CONN")
            self.assertEqual(compare_symbols("ahcompare", source), ["SH600030"])
        self.assertEqual(calls, ["CONN"])


class ClassifyTest(unittest.TestCase):
    """``_echoStockGroupArray()`` 的分类与去重。"""

    def _build(self, symbols, pair_refs, wide=False):
        refs_by_symbol = {}
        for symbol, refs in zip(symbols, pair_refs):
            refs_by_symbol[symbol] = refs
        made = []

        def fake_reference(symbol, source=None):
            made.append(symbol)
            return FakeRef(symbol)

        def fake_pairs(symbol, source=None):
            return refs_by_symbol.get(symbol, (None, None, None))

        with mock.patch.object(compare_page_service, "MysqlReference", fake_reference), \
             mock.patch.object(compare_page_service, "StockGetPairReferences", fake_pairs):
            result = compare_page_service.classify_symbols(
                symbols, source=FakeSource(), wide=wide
            )
        return result, made

    def test_same_pair_symbol_is_collected_once(self):
        ah_ref = FakePairRef()
        (_refs, _funds, pairs, _skipped), _ = self._build(
            ["SH600030", "SH600031"], [((None, ah_ref, None)), ((None, ah_ref, None))]
        )
        self.assertEqual(len(pairs["ah"]), 1)

    def test_narrow_screen_keeps_ref_even_when_paired(self):
        """PHP：``if ($bWide == false || $bIncluded == false) $arRef[] = $ref;``"""
        (_refs, _funds, _pairs, _skipped), _ = self._build(
            ["SH600030"], [((None, FakePairRef(), None))], wide=False
        )
        self.assertEqual(len(_refs), 1)

    def test_wide_screen_drops_ref_when_paired(self):
        (_refs, _funds, _pairs, _skipped), _ = self._build(
            ["SH600030"], [((None, FakePairRef(), None))], wide=True
        )
        self.assertEqual(len(_refs), 0)

    def test_wide_screen_keeps_ref_when_not_paired(self):
        (_refs, _funds, _pairs, _skipped), _ = self._build(
            ["SH600030"], [((None, None, None))], wide=True
        )
        self.assertEqual(len(_refs), 1)


class BuildComparePageTest(unittest.TestCase):
    def test_unknown_page_raises(self):
        with self.assertRaises(CompareNotFoundError):
            build_compare_page("nosuch", source=FakeSource(), symbols=[])

    def test_ah_section_is_built_and_history_link_only_for_one_row(self):
        def fake_reference(symbol, source=None):
            return FakeRef(symbol)

        def fake_pairs(symbol, source=None):
            # ``AhPairReference`` 的 GetSymbol() 是**A 股**那一侧，
            # H 股符号在它的 get_pair_ref() 里 —— 别把两者搞反。
            return (None, FakePairRef(symbol=symbol), None)

        with mock.patch.object(compare_page_service, "MysqlReference", fake_reference), \
             mock.patch.object(compare_page_service, "StockGetPairReferences", fake_pairs):
            page = build_compare_page(
                "ahcompare",
                source=FakeSource(),
                symbols=["SH600030"],
                history_link_builder=lambda symbol: "/ahhistory/%s" % symbol,
            )
        self.assertEqual(page["title"], "A股和H股对比")
        self.assertEqual([section["key"] for section in page["sections"]], ["ah"])
        self.assertEqual(page["sections"][0]["table"]["history_link"]["url"],
                         "/ahhistory/SH600030")
        self.assertEqual(page["counts"]["pairs"]["ah"], 1)

    def test_empty_symbols_gives_empty_sections(self):
        page = build_compare_page("abcompare", source=FakeSource(), symbols=[])
        self.assertEqual(page["sections"], [])
        self.assertIsNone(page["reference"])
        self.assertEqual(page["counts"]["symbols"], 0)

    def test_registry_covers_three_pages(self):
        self.assertEqual(
            sorted(COMPARE_PAGES), ["abcompare", "adrhcompare", "ahcompare"]
        )


class AhHistoryTest(unittest.TestCase):
    """``_ahhistory.php`` 的三道闸门。"""

    def _build(self, hkcny: bool = True, h_close: bool = True, ratio=1.25,
               hkcny_text="0.91", h_close_text="10.0", close="12.5"):
        class FakeH(FakePairRef):
            def get_close(self, date):
                return h_close_text if h_close else None

        class FakeCny:
            def get_close(self, date):
                return hkcny_text if hkcny else None

        class FakeRefAh(FakeRef):
            def get_stock_id(self):
                return 5

            def get_cny_ref(self):
                return FakeCny()

            def get_pair_ref(self):
                return FakeH()

            def get_price_ratio(self, date=None):
                return ratio

        class FakeHistorySql:
            def __init__(self, connection):
                pass

            def count(self, stock_id):
                return 1

            def get_all(self, stock_id, start=0, num=0):
                return [{"date": "2026-10-20", "close": close}]

        with mock.patch.object(ah_history_service, "AhPairReference",
                               lambda symbol, source=None: FakeRefAh(symbol)), \
             mock.patch.object(ah_history_service, "StockHistorySql", FakeHistorySql):
            return build_ah_history("SH600030", source=mock.Mock(connection="CONN"),
                                    start=0, num=30)

    def test_row_is_full_when_both_prices_exist(self):
        output = self._build()
        cells = output["table"]["rows"][0].cells
        # 日期 / 港币中间价 / A价 / H价 / A-H / H-A
        self.assertEqual(len(cells), 6)

    def test_day_is_skipped_without_cny(self):
        self.assertIsNone(self._build(hkcny=False)["table"]["rows"] or None)

    def test_h_missing_leaves_three_cells(self):
        output = self._build(h_close=False)
        self.assertEqual(len(output["table"]["rows"][0].cells), 3)

    def test_only_a_close_gets_rtrim0(self):
        """``_ahhistory.php:11-21``：三列里**只有 A 股收盘价**过了 ``rtrim0``。

        中间价与 H 股收盘价是 ``GetClose()`` 的原样，照抄时很容易顺手一起
        trim —— 那会让两列的长短看起来"统一"，实际与 PHP 不一致。
        """
        output = self._build(hkcny_text="0.9100", h_close_text="10.000",
                             close="12.500")
        cells = output["table"]["rows"][0].cells
        # 日期 / 中间价 / A价 / H价 / A-H / H-A
        self.assertEqual(cells[1], "0.9100")
        self.assertEqual(cells[2], "12.5")
        self.assertEqual(cells[3], "10.000")

    def test_no_connection_returns_none(self):
        self.assertIsNone(build_ah_history("SH600030", source=FakeSource()))


class RouteTest(unittest.TestCase):
    """路由层：页面能开、标题来自注册表、空态不 500。

    注入假服务（不连库）后三张配对表都是空的，页面走空态 —— 但标题 /
    说明 / 页尾导航仍来自注册表，能钉住"路由没接错"。
    """

    def _client(self):
        from web.app import create_app

        class FakeReferenceService:
            source = FakeSource()

            def get_table(self, refs, wide=False, admin=False, title=None):
                return {"columns": [], "rows": [], "count": 0}

        class FakeFundestService:
            def get_table(self, funds, wide=False, sort=None, title=None):
                return {"columns": [], "rows": [], "count": 0}

        app = create_app(
            {"TESTING": True},
            reference_service_factory=lambda: FakeReferenceService(),
            fundest_service_factory=lambda: FakeFundestService(),
        )
        return app.test_client()

    def test_three_pages_render_titles(self):
        client = self._client()
        for path, title in (
            ("/abcompare", "A股和B股对比"),
            ("/ahcompare", "A股和H股对比"),
            ("/adrhcompare", "ADR和H股对比"),
        ):
            response = client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertIn(title, response.get_data(as_text=True), path)

    def test_empty_state_is_not_500(self):
        """配对表空时页面照常渲染，并显示空态说明。"""
        response = self._client().get("/ahcompare")
        self.assertEqual(response.status_code, 200)
        self.assertIn("暂无数据", response.get_data(as_text=True))

    def test_ah_history_without_connection_shows_empty_state(self):
        response = self._client().get("/ahhistory/SH600030")
        self.assertEqual(response.status_code, 200)
        self.assertIn("没有 AH 配对记录", response.get_data(as_text=True))

    def test_other_compare_links_skip_current_page(self):
        """页尾只列**其它**两个对比页（PHP ``EchoLinks()`` 同样跳过自己）。

        注意不能用 ``"/ahcompare" in text`` 断言：页脚"生产端对照"那一段现在
        **还不会**再有它（已在 site_links 里移除），但以后任何外链都可能带这个
        子串。这里只看本页 ``<ul class="group-nav">`` 里的站内链接。
        """
        text = self._client().get("/ahcompare").get_data(as_text=True)
        nav = text.split('class="group-nav"')[-1].split("</ul>")[0]
        self.assertNotIn('href="/ahcompare"', nav)
        self.assertIn('href="/abcompare"', nav)
        self.assertIn('href="/adrhcompare"', nav)


if __name__ == "__main__":
    unittest.main()
