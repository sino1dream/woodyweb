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

    ``symbol_a`` 对应 PHP 的 ``IsSymbolA()`` —— 五档交易表只给 A 股摆
    （``_mystock.php:150``）。默认 ``True``：测试里用的 SZ/SH 符号本来就是
    A 股；要验"非 A 股不摆"时显式传 ``False``。
    """

    def __init__(self, symbol: str, pair_symbol: str = None, symbol_a: bool = True):
        self._symbol = symbol
        self._pair = FakeRef(pair_symbol) if pair_symbol else None
        self._symbol_a = symbol_a

    def get_symbol(self):
        return self._symbol

    def is_symbol_a(self):
        return self._symbol_a

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


class FakeTradingService:
    """``TradingService`` 替身：记下收到的参数，返回一张形态逼真的假五档表。

    表的形状照 ``trading_service._table()`` 的返回值抄（``id`` / ``title`` /
    ``columns`` / ``rows``，每行带 ``cells`` / ``hrefs`` / ``href``）——
    ``_data_table.html`` 会逐行取这几个键，缺一个就是 UndefinedError
    而不是留空白。数值本身不重要，这里验的是"接没接上、参数怎么传的"。

    ``table=None`` 表示"服务层判没有挂载量，整段不摆"（对应 PHP
    ``_checkTradingQuantity()`` 为真时 ``_echoTradingParagraph`` 直接 return）。
    """

    #: 哨兵：区分"没传 table 参数"与"显式传了 None"
    _UNSET = object()

    def __init__(self, symbol: str = "SZ162411", table=_UNSET):
        self.calls = []
        self.table = {
            "id": "trading",
            "title": "%s当前5档交易价格" % symbol,
            "columns": [{"text": "交易", "width": 50}, {"text": "价格"}, {"text": "数量"}],
            "rows": [
                {"cells": ["涨停", "5.451", ""], "hrefs": {}, "href": ""},
                {"cells": ["买1", "4.955", "1200"], "hrefs": {}, "href": ""},
            ],
        } if table is self._UNSET else table

    def get_table(self, ref, ah_ref=None, adr_ref=None):
        self.calls.append((ref, ah_ref, adr_ref))
        return self.table


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


class FakeEstRef:
    """第 2 分支（``FundPairReference``）的替身，**带三个 EST 方法**。

    ``fundest_service`` 靠 ``_invoke`` 探测 ``get_fair_net_value`` /
    ``get_realtime_net_value`` 决定要不要多画两列（参考 / 实时 EST），
    所以这个类必须把它们都实现出来 —— 少了任何一个，页面就少一档数据，
    而测试所要验证的正是"这一层迁过来之后它们有没有被接上"。

    每个方法被调用时都记账，测试据此断言消费链路真的通了。
    """

    def __init__(self, symbol="^NDX", official=100.0, fair=101.0, realtime=99.0):
        self._symbol = symbol
        self._official = official
        self._fair = fair
        self._realtime = realtime
        self.calls = []

    def _log(self, name):
        self.calls.append(name)

    def get_symbol(self):
        return self._symbol

    def get_stock_link(self):
        return self._symbol

    def get_official_net_value(self):
        self._log("get_official_net_value")
        return self._official

    def get_fair_net_value(self):
        self._log("get_fair_net_value")
        return self._fair

    def get_realtime_net_value(self):
        self._log("get_realtime_net_value")
        return self._realtime

    def get_official_date(self):
        self._log("get_official_date")
        return "2026-01-05"

    def get_price_display(self, value=False):
        self._log("get_price_display")
        return "%.2f" % (value if value else 0.0)

    def has_data(self):
        """``RefHasData()``（``stockcomm.php:3``）—— **没有它整行会被跳过**。

        ``fundest_service._row`` 开头就是 ``if not ref_has_data(ref): return None``，
        而真实的 ``FundPairReference`` 从 ``MysqlReference`` 继承到了这个方法。
        演示库里的 ``TQQQ`` 之所以没有估值表，就是因为它在库里没行情、
        ``has_data`` 为假 —— PHP 那边同样跳过，属于正确行为。
        """
        return True

    def get_percentage_text(self, value=False):
        self._log("get_percentage_text")
        return "1.00%"

    def __repr__(self):
        return "FakeEstRef(%s)" % self._symbol


class RecordingFundEst:
    """``FundEstService`` 替身：记下收到的 refs，返回一张带标记的假表。

    用替身而不是真服务，是因为真服务要查符号名称（连库）；
    而这里要验的是"**是不是把它接上了**"，不是"它内部怎么算"。

    首格刻意做成 ``<a>`` 标签串：真服务那一格是 ``GetStockLink()``
    （``fundest_service.py:225``），交给模板的就是 HTML，不是纯文本。
    CK2b 把这张表接进 ``/stock/<symbol>`` 时漏了 ``first_cell_is_html=True``，
    页面上直接显示了这串标签 —— 用真形态的替身才能钉住这个回归。
    """

    #: 首格的 HTML，形态取自 PHP ``GetStockLink()``。
    FIRST_CELL_HTML = '<a href="/woody/res/mystockcn.php?symbol=^NDX">^NDX</a>'

    def __init__(self):
        self.refs_seen = []
        #: ``hrefs`` / ``href`` 不能少 —— ``_data_table.html`` 会对每一行取
        #: ``row.hrefs.get(index)``，缺了它会抛 UndefinedError 而不是留空白。
        self.table = {
            "id": "estimation",
            "title": "估值",
            "columns": [{"text": "代码"}],
            "rows": [{"cells": [self.FIRST_CELL_HTML], "hrefs": {}, "href": ""}],
        }

    def get_table(self, refs, wide=False, title=None, sort=None):
        self.refs_seen.extend(refs)
        return self.table


class FakeFundHistoryService(FakeHistoryService):
    """``fundhistory`` 有行的历史服务 —— 用来验证那一段**真的接上了**。

    单独开一个而不是改 :class:`FakeHistoryService`，是因为那边"只有
    ``stockhistory`` 有行"正是尾部三段那条用例要钉的前提。
    """

    def get_table(self, kind, symbol, start=0, num=10):
        if kind == "fundhistory":
            return {
                "title": "基金溢价记录",
                "columns": [{"text": "日期"}, {"text": "价格"}],
                "rows": [{"cells": ["2026-10-01", "742.03"]}],
            }
        return FakeHistoryService.get_table(self, kind, symbol, start, num)


class FakePairSource:
    """假数据源：只回答"这条符号有没有配对记录"。"""

    def __init__(self, pairs=None):
        self.pairs = pairs or {}

    def get_stock_id(self, symbol):
        return "1"


class _SourceHolder:
    """``create_app`` 的 ``reference_service_factory`` 要一个对象给它取 ``source``。"""

    def __init__(self, source):
        self.source = source


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

    # -- A 股五档交易表（_mystock.php:150）---------------------------------
    def test_trading_table_shown_for_symbol_a(self):
        """A 股：``EchoTradingParagraph($ref, $ah_ref, $adr_ref)`` 接上了。"""
        trading = FakeTradingService()
        detail = self._detail(trading_service=trading)
        self.assertIs(detail["trading"], trading.table)
        # 参数必须原样传：(ref, ah_ref, adr_ref) —— 后两个决定要不要多两列溢价
        ref, ah_ref, adr_ref = trading.calls[0]
        self.assertIs(ref, self.ref)
        self.assertIsNone(ah_ref)
        self.assertIsNone(adr_ref)

    def test_trading_table_forwards_pair_refs(self):
        """有 ah / adr 时它们要传进 ``get_table``（PHP 据此加两列）。"""
        ah_ref = FakeRef("SH600000", pair_symbol="00700")
        adr_ref = FakeRef("HWM", pair_symbol="00700")
        self.pair_result = (None, ah_ref, adr_ref)
        trading = FakeTradingService()
        self._detail(trading_service=trading)
        _ref, seen_ah, seen_adr = trading.calls[0]
        self.assertIs(seen_ah, ah_ref)
        self.assertIs(seen_adr, adr_ref)

    def test_trading_table_hidden_for_non_a(self):
        """非 A 股不摆（PHP 的 ``IsSymbolA()`` 守卫），服务根本不该被调用。"""
        self.ref = FakeRef("^NDX", symbol_a=False)
        trading = FakeTradingService()
        detail = self._detail(trading_service=trading)
        self.assertIsNone(detail["trading"])
        self.assertEqual(trading.calls, [])

    def test_trading_table_none_without_service(self):
        """没注入交易服务时是 ``None``，不抛。"""
        detail = self._detail()
        self.assertIsNone(detail["trading"])

    def test_trading_table_none_when_no_quantity(self):
        """服务层判"没有挂单量"返回 ``None`` 时照原样透出（整段不摆）。"""
        detail = self._detail(trading_service=FakeTradingService(table=None))
        self.assertIsNone(detail["trading"])

    def test_trading_table_swallows_service_exception(self):
        """服务抛异常不该让整页 500 —— 退化成不摆这张表。"""
        class BoomTradingService(FakeTradingService):
            def get_table(self, ref, ah_ref=None, adr_ref=None):
                raise RuntimeError("boom")

        detail = self._detail(trading_service=BoomTradingService())
        self.assertIsNone(detail["trading"])

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

        #: SH501225 —— 演示库里**不在** ``fundpair`` 表的 A 股符号，所以路由
        #: 的"第 2 分支优先"会落空、真的走到第 4 分支（CK3 要验的那条路）。
        #: 用 SZ162411 会走到第 2 分支（它在 fundpair 里），那张页没有五档表。
        self.refs = [FakeRef("SH501225")]
        self.app = create_app(
            {"TESTING": True},
            reference_refs_builder=lambda symbols: self.refs,
            sma_service_factory=lambda: FakeSmaService(),
            history_service_factory=lambda: FakeHistoryService(),
            trading_service_factory=lambda: FakeTradingService("SH501225"),
        )
        self.client = self.app.test_client()

    def test_page_renders(self):
        response = self.client.get("/stock/SH501225")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("SH501225", html)
        self.assertIn("stockhistory-table", html)

    def test_trading_table_rendered_for_symbol_a(self):
        """A 股详情页摆五档交易表（``_mystock.php:150``）。

        表头标题、行内容、CSS 类都验 —— 少任何一样都说明模板那段没接上
        （CK2b 就是这么漏掉 ``first_cell_is_html`` 的，见 pitfalls #59）。
        """
        html = self.client.get("/stock/SH501225").get_data(as_text=True)
        self.assertIn("五档交易", html)
        self.assertIn('class="data-table trading-table"', html)
        self.assertIn("SH501225当前5档交易价格", html)
        self.assertIn("买1", html)

    def test_live_refresh_only_when_requested(self):
        """``?live=1`` 才联网补盘口；默认一次都不联网。

        ``apply_network`` 在路由函数体内 ``from ref.refquote import`` ——
        调用时才取模块属性，所以补丁打在模块上就能被看到（与
        ``routes/fund.py`` 的同款路径）。
        """
        import ref.refquote

        seen = []
        original = ref.refquote.apply_network

        def _fake_apply_network(ref, network=None):
            seen.append(ref)
            return True

        ref.refquote.apply_network = _fake_apply_network
        try:
            self.client.get("/stock/SH501225")
            self.assertEqual(seen, [])
            self.client.get("/stock/SH501225?live=1")
            self.assertEqual(len(seen), 1)
            self.assertIs(seen[0], self.refs[0])
        finally:
            ref.refquote.apply_network = original

    def test_no_ref_does_not_500(self):
        """引用对象建不出来时仍返回 200 —— 旧链接不该因为暂时没行情就 404。"""
        self.app.extensions["reference_refs_builder"] = lambda symbols: []
        response = self.client.get("/stock/SZ999999")
        self.assertEqual(response.status_code, 200)
        self.assertIn("取不到该符号的行情数据", response.get_data(as_text=True))

    def test_back_links_present(self):
        html = self.client.get("/stock/SH501225").get_data(as_text=True)
        self.assertIn('href="/stock"', html)
        self.assertIn("返回股票列表", html)


class FundPairDetailTest(unittest.TestCase):
    """``get_fund_pair_detail`` —— ``_mystock.php:131`` 第 2 分支（CK2b）。

    ref 直接注入而不走 ``StockGetFundPairReference``：真实类的构造要连库，
    而这里要验的是**段落组装**与**EST 那几个方法有没有被接上**。
    """

    def setUp(self):
        self.ref = FakeEstRef()
        self.fundest = RecordingFundEst()
        self.sma = FakeSmaService()
        self.history = FakeHistoryService()

    def _detail(self, **kwargs):
        svc = sds.StockDetailService(source=None)
        return svc.get_fund_pair_detail(
            kwargs.pop("symbol", "^NDX"), ref=kwargs.pop("ref", self.ref),
            sma_service=kwargs.pop("sma_service", self.sma),
            history_service=kwargs.pop("history_service", self.history),
            fundest_service=kwargs.pop("fundest_service", self.fundest), **kwargs)

    def test_returns_none_without_ref(self):
        """ref 建不出来返回 ``None`` —— 由路由层退化到第 4 分支。"""
        svc = sds.StockDetailService(source=None)
        self.assertIsNone(svc.get_fund_pair_detail("ZZ999", ref=None))

    def test_branch_marker(self):
        """``branch`` 字段不能删 —— 模板靠它避开 Undefined 链式访问。

        Jinja 里写 ``detail.sma.ah_pair`` 而 dict 没有 ``sma`` 键时会抛
        ``UndefinedError`` 而不是返回假值，所以两个分支必须显式区分。
        """
        self.assertEqual(self._detail()["branch"], "fundpair")

    def test_generic_branch_marker(self):
        """第 4 分支的标记同样要有，两个分支各管一段渲染。"""
        svc = sds.StockDetailService(source=None)
        detail = svc.get_detail("SZ162411", ref=FakeRef("SZ162411"),
                                sma_service=self.sma, history_service=self.history)
        self.assertEqual(detail["branch"], "generic")

    def test_est_table_uses_fundest_service(self):
        """``EchoFundArrayEstParagraph`` 走的是注入的估值服务。"""
        detail = self._detail()
        self.assertIs(detail["est"], self.fundest.table)
        self.assertEqual(len(self.fundest.refs_seen), 1)
        self.assertIs(self.fundest.refs_seen[0], self.ref)

    def test_est_methods_actually_consumed(self):
        """三个 EST 方法被真正调用 —— **CK2b 迁移的意义就在这儿**。

        **这里必须用真实的 ``FundEstService``，不能用替身**：替身压根不会去调
        ref 上的方法，这条断言会永远成立 —— 等于替身把缺口盖住了（同 AGENTS.md
        里"替身多实现一个方法把缺口盖住"那条踩过的坑）。

        ``fundest_service`` 探测它们决定列数：只有官方档是 3 列，有参考 /
        实时估值时加到 5 / 7 列。换句说，这一层迁不迁，页面上少的就是两"档"
        数据，而不只是"代码里少几个方法"。
        """
        from web.services.fundest_service import FundEstService

        ref = FakeEstRef()
        table = FundEstService(source=None).get_table([ref])
        self.assertIsNotNone(table, "真实的估值表应当能画出来")

        called = set(ref.calls)
        self.assertIn("get_official_net_value", called)
        self.assertIn("get_fair_net_value", called)
        self.assertIn("get_realtime_net_value", called)

    def test_real_fund_pair_reference_has_est_methods(self):
        """真实的 ``FundPairReference`` **必须**提供这四个方法。

        上一条测的是"消费方会不会调用"，这一条测的是"生产方有没有提供" ——
        **两条都不能少**：只留上一条时，替身自己带着这三个方法，把真实类里的
        方法改个名照样全绿（变异测试确认过）。穷的时候正是这样漏的：
        ``fundest_service`` 靠 ``_invoke`` 探测，方法不存在时不报错、
        只是悄悄少两列 —— 页面上少的是"档"，不是报错。
        """
        from ref.refobj import FundPairReference

        for name in ("get_official_net_value", "get_fair_net_value",
                     "get_realtime_net_value", "get_official_date"):
            #: 查 ``__dict__`` 而不是 ``hasattr`` —— 继承链上本来就有这四个方法
            #: （``FundReference`` 里的同名 getters），``hasattr`` 恒为真、把
            #: "本类没覆盖"这个缺口藏住。这里要钉的是**本类重写了**它们。
            self.assertIsNotNone(
                FundPairReference.__dict__.get(name),
                "%s 应当在 FundPairReference 里被覆盖" % name,
            )
            #: camelCase 别名同样要有 —— ``_invoke`` 两条腿走路
            camel = "".join(part.capitalize() for part in name.split("_"))
            self.assertTrue(hasattr(FundPairReference, camel), camel)

    def test_sma_and_history_sections_present(self):
        detail = self._detail()
        self.assertIsNotNone(detail["fund_pair_sma"])
        names = [call[0] for call in self.sma.calls]
        self.assertIn("fund_pair_table", names)
        self.assertIsNotNone(detail["stockhistory"])

    def test_none_when_service_missing(self):
        """没注入服务时段落为 ``None`` 而不是崩 —— 缺什么少什么。"""
        svc = sds.StockDetailService(source=None)
        detail = svc.get_fund_pair_detail("^NDX", ref=self.ref)
        self.assertIsNone(detail["est"])
        self.assertIsNone(detail["fund_pair_sma"])
        self.assertIsNone(detail["fundhistory"])

    # -- 跟踪表（EchoFundListParagraph，_mystock.php:133）-------------------
    def test_fundlist_is_the_reference_variant(self):
        """末列是「参考值」而不是 QDII 那张杠杆表的「对冲值」。

        PHP 里两者是同一个函数的两个分支（``$callback`` 传不传）；个股详情页
        只传一个 ref（本符号自己），没有"谁跟踪了我的标的"这个问题。
        """
        detail = self._detail()
        self.assertIsNotNone(detail["fundlist"])
        self.assertEqual(detail["fundlist"]["columns"][-1]["text"], "参考值")
        self.assertEqual(len(detail["fundlist"]["rows"]), 1)

    def test_fundlist_links_need_the_injected_builder(self):
        """链接构造器由装配层注入 —— 服务层不认识 ``url_for``。"""
        svc = sds.StockDetailService(
            source=None, link_builder=lambda symbol, kind: "/%s/%s" % (kind, symbol)
        )
        detail = svc.get_fund_pair_detail("^NDX", ref=self.ref)
        row = detail["fundlist"]["rows"][0]
        self.assertEqual(row.href, "/calibrationhistory/^NDX")

    # -- 基金溢价记录（EchoFundHistoryParagraph，_mystock.php:135）----------
    def test_fundhistory_uses_the_history_service(self):
        """与 ``/history/fundhistory/<sym>`` 同一张表 —— 没有第二套取数。"""
        detail = self._detail(history_service=FakeFundHistoryService())
        self.assertIsNotNone(detail["fundhistory"])
        self.assertEqual(detail["fundhistory"]["title"], "基金溢价记录")

    def test_fundhistory_hidden_when_there_are_no_rows(self):
        """取不到行就整段不摆 —— 与尾部三段同一条约定。"""
        detail = self._detail()
        self.assertIsNone(detail["fundhistory"])

    def test_fundhistory_hidden_for_yahoo_net_value_symbols(self):
        """``if ($ref->IsYahooNetValue()) return;``（``fundhistoryparagraph.php:52``）。

        雅虎净值符号（``^SZ162411-IV``）没有场内价格，摆溢价表没有意义。
        **必须换一个真的有行的服务**：用空服务的话，这条断言在守卫被删掉时
        照样成立 —— 空表本来就不显示。
        """
        detail = self._detail(
            symbol="^SZ162411-IV", history_service=FakeFundHistoryService()
        )
        self.assertIsNone(detail["fundhistory"])


class FundPairRouteTest(unittest.TestCase):
    """``/stock/<symbol>`` 的第 2 分支渲染（离线）。"""

    def setUp(self):
        from web.app import create_app

        #: 假 source：带一条 fundpair 记录，够工厂的门禁通过就好
        from unittest.mock import patch

        fake_source = FakePairSource({"^NDX": "hf_NQ"})
        self.app = create_app(
            {"TESTING": True},
            reference_service_factory=lambda: _SourceHolder(fake_source),
            sma_service_factory=lambda: FakeSmaService(),
            history_service_factory=lambda: FakeHistoryService(),
            fundest_service_factory=lambda: RecordingFundEst(),
        )
        self.client = self.app.test_client()
        self.ref = FakeEstRef()
        target = "web.services.stock_detail_service.StockGetFundPairReference"
        self._patch = patch(target, lambda symbol, source=None, **kw: (
            self.ref if symbol == "^NDX" else None))
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_fundpair_branch_renders_est(self):
        html = self.client.get("/stock/^NDX").get_data(as_text=True)
        self.assertIn("est-table", html)
        self.assertIn("估值", html)

    def test_est_first_cell_is_a_link_not_escaped_html(self):
        """估值表首格是 ``GetStockLink()`` 的 ``<a>``，不能被引擎转义。

        PHP 是 ``$ar = array($ref->GetStockLink())``（``fundestparagraph.php:9``），
        ``fund_detail.html`` / ``qdii.html`` 那两处都为此传了
        ``first_cell_is_html=True``；CK2b 新接的 ``/stock/<symbol>`` 漏了，
        页面上整段 ``<a href=...>`` 变成可见文本。
        """
        html = self.client.get("/stock/^NDX").get_data(as_text=True)
        self.assertIn(RecordingFundEst.FIRST_CELL_HTML, html)
        self.assertNotIn("&lt;a ", html)

    def test_fundlist_table_renders(self):
        """跟踪表：末列是「参考值」那一版（``_mystock.php:133``）。"""
        html = self.client.get("/stock/^NDX").get_data(as_text=True)
        self.assertIn("fundlist-table", html)
        self.assertIn("参考值", html)

    def test_fundhistory_table_renders(self):
        """基金溢价记录：历史服务给出 ``fundhistory`` 的行时才出现。"""
        self.app.extensions["history_service_factory"] = lambda: FakeFundHistoryService()
        html = self.client.get("/stock/^NDX").get_data(as_text=True)
        self.assertIn("fundhistory-table", html)
        self.assertIn("查看全部基金溢价记录", html)

    def test_non_paired_symbol_falls_through_to_generic(self):
        """没有配对记录的符号不能因为 Jinja 缺键就 500。"""
        response = self.client.get("/stock/SZ162411")
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
