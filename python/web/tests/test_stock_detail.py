"""非基金详情页 ``/stock/<symbol>``（CK3）。

对应 ``woody/res/php/_mystock.php:144-170`` 的 **else 分支** ——
``_echoMyStockData()`` 四个分支里的最后一个（前三个是基金 / FundPair /
Holdings）。

## 分层

配对引用**怎么查**、引用对象**怎么构造**已经在
``auto/test_pairref.py`` 里覆盖了（22 例）。本文件测的是它**上层**两件事：

1. **段落组装**（``StockDetailService``）：三张对比表摆不摆、均线走四个槽位
   里的哪一个、尾部三段有没有接到 ``detail_sections``；
2. **路由接线**（``/stock/<symbol>``）：能不能渲染、缺数据时会不会 500。

所以这里用 monkeypatch 顶掉 ``StockGetPairReferences`` —— 不是图省事，是让
测试只盯着"分支选得对不对"，不再重复验证配对查找本身。

## 为什么全部离线

``create_app(...)`` 注入假服务，不连数据库也不联网。
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

import web.services.stock_detail_service as sds  # noqa: E402


class FakeRef:
    """通用引用对象替身。

    只实现 ``StockDetailService`` 会用到的那几个方法。真实引用对象是
    ``refobj.MysqlReference``，它要连库，这里用不着。
    """

    def __init__(self, symbol: str, pair_symbol: str = None):
        self._symbol = symbol
        self._pair = FakeRef(pair_symbol) if pair_symbol else None

    def get_symbol(self):
        return self._symbol

    def get_pair_ref(self):
        return self._pair

    def get_my_stock_link(self):
        return self._symbol

    def get_price_display(self):
        return "1.000"

    def __repr__(self):
        return "FakeRef(%s)" % self._symbol


class FakeSmaService:
    """``SmaService`` 替身：每个方法返回一张带 ``kind`` 记号的空表。

    空表就够了 —— ``data_table`` 宏在 ``rows`` 为空时照样渲染出表头容器，
    模板里 ``{% if table %}`` 判断的是"表存不存在"而不是"有没有行"。
    """

    def __init__(self):
        #: 记录每次调用的方法名与参数，用来断言"走了哪个分支"
        self.calls = []

    def _table(self, kind: str, memo=False):
        return {"kind": kind, "columns": [], "rows": [], "memo": memo}

    def sma_table(self, ref, memo=False, cb_ref=False, callback=False, callback2=False):
        self.calls.append(("sma_table", ref, memo, cb_ref, callback))
        return self._table("plain", {"start_date": "2026-01-01"})

    def ah_pair_table(self, ref, memo=False, callback2=False):
        self.calls.append(("ah_pair_table", ref, memo))
        return self._table("ah_pair", {"start_date": "2026-01-01"})

    def fund_pair_table(self, ref, memo=False, callback2=False):
        self.calls.append(("fund_pair_table", ref, memo))
        return self._table("fund_pair", memo)


class FakeHistoryService:
    """假历史服务：只有 ``stockhistory`` 有行，其余为空 —— 用来看隐藏逻辑。"""

    def get_table(self, kind, symbol, start=0, num=10):
        if kind == "stockhistory":
            return {
                "title": "历史价格",
                "columns": [{"text": "日期"}, {"text": "价格"}],
                "rows": [{"cells": ["2026-09-30", "4.796"]}],
            }
        return {"title": kind, "columns": [], "rows": []}


class StockDetailServiceTest(unittest.TestCase):
    """段落组装：三张对比表 + 均线四个槽位 + 尾部三段。"""

    def setUp(self):
        self.orig_factory = sds.StockGetPairReferences
        self.pair_result = (None, None, None)
        sds.StockGetPairReferences = lambda symbol, source=None: self.pair_result
        self.service = sds.StockDetailService(source=None)
        self.ref = FakeRef("SZ162411")
        self.sma = FakeSmaService()

    def tearDown(self):
        sds.StockGetPairReferences = self.orig_factory

    def _detail(self, symbol="SZ162411", **kwargs):
        return self.service.get_detail(
            symbol,
            ref=kwargs.pop("ref", self.ref),
            sma_service=kwargs.pop("sma_service", self.sma),
            history_service=kwargs.pop("history_service", FakeHistoryService()),
            **kwargs
        )

    # -- 对比表 -----------------------------------------------------------
    def test_no_pair_refs_hides_all_three_tables(self):
        """三张配对表都是空时，三张对比表都不出现（演示库的现状）。"""
        detail = self._detail()
        self.assertIsNone(detail["ab"])
        self.assertIsNone(detail["ah"])
        self.assertIsNone(detail["adr"])

    def test_ab_ref_shows_ab_table_with_title(self):
        """有 ab_ref 就摆 AB 对比表，标题来自 ``PAIR_TITLES``。"""
        self.pair_result = (FakeRef("SZ000001", pair_symbol="SZ200002"), None, None)
        detail = self._detail()
        self.assertIsNotNone(detail["ab"])
        self.assertEqual(detail["ab"]["title"], "AB对比")
        self.assertIsNone(detail["ah"])
        self.assertIsNone(detail["adr"])

    def test_ah_and_adr_refs_show_their_tables(self):
        """AH / ADR 各自的标题来自 PHP 的 ``*_COMPARE_DISPLAY`` 常量。"""
        self.pair_result = (None, FakeRef("SH600000", pair_symbol="00700"),
                            FakeRef("HWM", pair_symbol="00700"))
        detail = self._detail()
        self.assertEqual(detail["ah"]["title"], "AH对比")
        self.assertEqual(detail["adr"]["title"], "ADR和H股对比")

    # -- 均线四分支（_mystock.php:152-165）---------------------------------
    def test_sma_plain_when_no_pair_ref(self):
        """没有 ah / adr：走 ``EchoSmaParagraph($ref)``，且不带回调。"""
        detail = self._detail()
        self.assertIsNotNone(detail["sma"]["plain"])
        self.assertIsNone(detail["sma"]["ah_pair"])
        self.assertIsNone(detail["sma"]["fund_pair"])
        names = [call[0] for call in self.sma.calls]
        self.assertEqual(names, ["sma_table"])
        # 普通分支是「EchoSmaParagraph($ref)」：cb_ref 与 callback 都取默认 False。
        # 只有 fx_susdcnh 那个分支才会把 ref 自己当 cb_ref（单独一条用例覆盖）。
        _name, ref, memo, cb_ref, callback = self.sma.calls[0]
        self.assertIs(ref, self.ref)
        self.assertIs(cb_ref, False)
        self.assertIs(callback, False)

    def test_sma_ah_branch_calls_ah_and_fund_pair(self):
        """有 ah_ref：先 ``EchoAhPairSmaParagraph`` 再 ``EchoFundPairSmaParagraph``。"""
        ah_ref = FakeRef("SH600000", pair_symbol="00700")
        self.pair_result = (None, ah_ref, None)
        detail = self._detail()
        self.assertIsNotNone(detail["sma"]["ah_pair"])
        self.assertIsNotNone(detail["sma"]["fund_pair"])
        self.assertIsNone(detail["sma"]["adr_extra"])
        self.assertIsNone(detail["sma"]["plain"])
        names = [call[0] for call in self.sma.calls]
        self.assertEqual(names, ["ah_pair_table", "fund_pair_table"])

    def test_sma_ah_with_adr_appends_empty_title_table(self):
        """``EchoFundPairSmaParagraph($adr_ref, '')`` —— 空串标题要原样传下去。

        这不是笔误：``sma_table`` 里 ``memo is False`` 是**严格**判断，
        传空串就不会被替换成默认 memo，于是多张均线表并排时只有第一张带标题。
        """
        ah_ref = FakeRef("SH600000", pair_symbol="00700")
        adr_ref = FakeRef("HWM", pair_symbol="00700")
        self.pair_result = (None, ah_ref, adr_ref)
        detail = self._detail()
        self.assertIsNotNone(detail["sma"]["adr_extra"])
        # 第三个槽位必须带着空串标题，不是默认的 False
        kinds = [call[2] for call in self.sma.calls if call[0] == "fund_pair_table"]
        self.assertEqual(kinds[-1], "")

    def test_sma_only_adr_uses_fund_pair(self):
        """只有 adr_ref：``EchoFundPairSmaParagraph($adr_ref)``，标题用默认的。"""
        adr_ref = FakeRef("HWM", pair_symbol="00700")
        self.pair_result = (None, None, adr_ref)
        detail = self._detail()
        self.assertIsNone(detail["sma"]["ah_pair"])
        self.assertIsNotNone(detail["sma"]["fund_pair"])
        self.assertIsNone(detail["sma"]["plain"])
        names = [call[0] for call in self.sma.calls]
        self.assertEqual(names, ["fund_pair_table"])

    def test_sma_cnh_symbol_uses_cnh_callback(self):
        """``fx_susdcnh`` 是唯一带 ``_callbackCnhSma`` 的符号。"""
        detail = self._detail(symbol=sds.CNH_SYMBOL)
        names = [call[0] for call in self.sma.calls]
        self.assertEqual(names, ["sma_table"])
        _name, ref, memo, cb_ref, callback = self.sma.calls[0]
        self.assertIs(callback, sds.callback_cnh_sma)
        self.assertIs(cb_ref, self.ref)

    def test_sma_none_service_leaves_all_slots_empty(self):
        """没注入均线服务时四个槽位全空，不抛异常。"""
        detail = self._detail(sma_service=None)
        self.assertEqual(
            detail["sma"],
            {"ah_pair": None, "fund_pair": None, "adr_extra": None, "plain": None},
        )

    # -- 尾部三段 ---------------------------------------------------------
    def test_tail_sections_delegate_to_detail_sections(self):
        """``_mystock.php:167-169`` 三段：有行的出现，没行的隐藏。"""
        detail = self._detail()
        self.assertIsNotNone(detail["stockhistory"])
        self.assertIsNone(detail["netvalueclose"])  # 假服务里它是空的
        self.assertIsNone(detail["shares"])

    def test_tail_sections_none_without_service(self):
        """没注入历史服务时三段都是 ``None``。"""
        detail = self._detail(history_service=None)
        self.assertIsNone(detail["stockhistory"])
        self.assertIsNone(detail["netvalueclose"])
        self.assertIsNone(detail["shares"])

    # -- 降级 -------------------------------------------------------------
    def test_get_detail_returns_none_when_ref_unavailable(self):
        """连不上库、引用对象建不出来时返回 ``None``，不让页面 500。

        要触发这条必须让 ``build_stock_references`` 抛异常：``source`` 为
        ``None`` 时它**仍然建得出**引用对象（只是没有行情数据），
        所以不能拿 source=None 当"建不出来"的等价条件。
        """
        import web.services.reference_service as rs

        original = rs.build_stock_references

        def _boom(*_args, **_kwargs):
            raise RuntimeError("no db")

        rs.build_stock_references = _boom
        try:
            service = sds.StockDetailService(source=None)
            self.assertIsNone(service.get_detail("SZ162411", ref=None))
        finally:
            rs.build_stock_references = original

    def test_get_detail_builds_ref_itself_when_not_injected(self):
        """没传 ref 时服务层自己兜底建一个（供"直接用服务层"的调用者使用）。"""
        service = sds.StockDetailService(source=None)
        detail = service.get_detail("SZ162411")
        self.assertIsNotNone(detail)
        # source 为 None 时查不到配对表，三张对比表都是 None 而不是崩掉
        self.assertIsNone(detail["ab"])
        self.assertIsNone(detail["ah"])
        self.assertIsNone(detail["adr"])


class CallbackCnhSmaTest(unittest.TestCase):
    """``_callbackCnhSma``（``_mystock.php:104``）。"""

    def setUp(self):
        self.orig = sds.get_future_interest_premium
        sds.get_future_interest_premium = lambda rate, end, now=None: 1.05

    def tearDown(self):
        sds.get_future_interest_premium = self.orig

    def test_returns_ref_when_no_est(self):
        """``$strEst`` 为假值时返回 ref 自己（不是空串）。"""
        ref = FakeRef("fx_susdcnh")
        self.assertIs(sds.callback_cnh_sma(ref, False), ref)
        self.assertIs(sds.callback_cnh_sma(ref, None), ref)

    def test_scales_rounds_and_unscales(self):
        """先乘 2000 取整、再除回去 —— 精度钉在 0.0005 的整倍数上。

        固定溢价 1.05、est=7.0：2000 * 7.0 * 1.05 = 14700，除回 2000 = 7.35。
        """
        self.assertEqual(sds.callback_cnh_sma(None, 7.0), "7.3500")

    def test_result_is_multiple_of_half_cent(self):
        """结果必须是 0.0005 的整数倍 —— 这才是"放大-取整-缩小"的实质。

        不能只拿 ``est=7.0`` 验证：2000*7.0*1.05 = 14700 恰好整除，
        **就地保留四位小数**的写法也会给出 ``7.3500``，测不出差别。
        这里取一个落在两个 0.0005 刻度之间的值，两种写法才会分道扬镳。
        """
        value = float(sds.callback_cnh_sma(None, 7.00008))
        # PHP 语义：round(2000 * x) / 2000，于是 2000x 必为整数
        self.assertAlmostEqual(value * 2000.0, round(value * 2000.0), places=6)

    def test_keeps_four_decimals(self):
        """``number_format($f, 4)`` 是**保留**四位，不是去掉尾随零。"""
        # 2000 * 7.0 * 1.05 = 14700 -> 7.35 -> "7.3500"
        self.assertTrue(sds.callback_cnh_sma(None, 7.0).endswith("00"))

    def test_returns_ref_when_premium_missing(self):
        """取不到期货溢价时按 PHP 的 ``false`` 处理：返回 ref 而不是崩掉。"""
        sds.get_future_interest_premium = lambda rate, end, now=None: None
        ref = FakeRef("fx_susdcnh")
        self.assertIs(sds.callback_cnh_sma(ref, 7.0), ref)


class StockDetailRouteTest(unittest.TestCase):
    """``/stock/<symbol>`` 路由接线（离线）。"""

    def setUp(self):
        from web.app import create_app

        self.refs = [FakeRef("SZ162411")]
        self.app = create_app(
            {"TESTING": True},
            reference_refs_builder=lambda symbols: self.refs,
            sma_service_factory=lambda: FakeSmaService(),
            history_service_factory=lambda: FakeHistoryService(),
        )
        self.client = self.app.test_client()

    def test_page_renders(self):
        response = self.client.get("/stock/SZ162411")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("SZ162411", html)
        self.assertIn("stockhistory-table", html)

    def test_no_ref_does_not_500(self):
        """引用对象建不出来时仍返回 200 —— 旧链接不该因为暂时没行情就 404。"""
        self.app.extensions["reference_refs_builder"] = lambda symbols: []
        response = self.client.get("/stock/SZ999999")
        self.assertEqual(response.status_code, 200)
        self.assertIn("取不到该符号的行情数据", response.get_data(as_text=True))

    def test_back_links_present(self):
        html = self.client.get("/stock/SZ162411").get_data(as_text=True)
        self.assertIn('href="/stock"', html)
        self.assertIn("返回股票列表", html)


if __name__ == "__main__":
    unittest.main()
