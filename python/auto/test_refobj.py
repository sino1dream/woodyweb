"""`python/auto/refobj.py` 的单元测试。

对应 PHP `php/stock/mysqlref.php`、`fundref.php`、`qdiiref.php`、`cnyref.php`。

与 `test_autocalibration.py` 一样，**这些用例不许联网**：数据源是内存假实现，
行情加载回调默认不传（传了也不会被自动调用）。
"""

from __future__ import annotations

import unittest

from ref import refobj
from ref import stockref
from ref.refobj import (
    NETVALUE_PRECISION,
    QDII_REGIONS,
    CnyReference,
    EstReference,
    FundReference,
    MysqlReference,
    QdiiReference,
    build_est_reference,
    build_qdii_reference,
    count_holdings,
    gb_to_utf8,
)


class Rec:
    """日频记录（只需要 ``date`` / ``close``）。"""

    def __init__(self, date=None, close=None):
        self.date = date
        self.close = close


class FakeCursor:
    """假游标：只伺候 ``fundpair`` 那条"我跟踪谁"的查询。

    真的 ``_PAIR_SYMBOL_SQL`` 会传一个 ``(symbol,)`` 参数，这里按映射直接答。
    ``rows`` 支持 tuple / dict 两种形状 —— 真驱动（PyMySQL 的 DictCursor）
    给 dict，测试里两种都要能过，否则会把"只认一种行形状"的 bug 放过去。
    """

    def __init__(self, pairs=None, as_dict=False, counts=None):
        self.pairs = pairs or {}
        self.counts = counts or {}
        self.as_dict = as_dict
        self.param = None
        self.sql = ""
        self.closed = False

    def execute(self, sql, params=None):
        self.sql = sql or ""
        self.param = params[0] if params else None

    def fetchone(self):
        #: ``SqlCountHoldings`` 那条 COUNT —— 用于"标的是不是持仓型"的判据
        if "COUNT(*)" in self.sql.upper():
            return {"n": self.counts.get(self.param, 0)}
        symbol = self.pairs.get(self.param)
        if symbol is None:
            return None
        return {"symbol": symbol} if self.as_dict else (symbol,)

    def close(self):
        self.closed = True


class FakeConnection:
    """假连接：只提供 ``cursor()``。"""

    def __init__(self, pairs=None, as_dict=False, counts=None):
        self.pairs = pairs or {}
        self.as_dict = as_dict
        #: ``stock_id -> 持仓行数``，喂给 ``SELECT COUNT(*)`` 那条查询
        self.counts = counts or {}
        self.cursors = []

    def cursor(self):
        cursor = FakeCursor(self.pairs, self.as_dict, self.counts)
        self.cursors.append(cursor)
        return cursor


class FakeSource:
    """内存数据源。

    默认数据取自演示库里 ``SZ161130`` 那一组（见 README_autocalibration）：
    校准值 32.079543、校准日净值 4.5452、仓位 0.95。
    """

    STOCK = {
        "SZ161130": 1,
        "SH513100": 2,
        "QQQ": 4,
        "^NDX": 6,
        "USCNY": 8,
        "fx_susdcny": 9,
        "HKCNY": 10,
        "fx_shkdcny": 11,
        "hf_NQ": 13,
    }

    def __init__(self, names=None, calibration=None, netvalue=None, history=None,
                 position=None, adjclose=None, net_value_count=2,
                 pairs=None, pairs_as_dict=False, counts=None):
        self.names = names or {}
        self.calibration = calibration or {}
        self.netvalue = netvalue or {}
        self.history = history or {}
        self.adjclose = adjclose or {}
        self.position = position or {}
        self.net_value_count = net_value_count
        self.written_calibration = []
        #: ``fundpair`` 的"我跟踪谁"：符号 -> 被跟踪标的
        self.connection = FakeConnection(pairs, pairs_as_dict, counts)

    def get_stock_id(self, symbol):
        return self.STOCK.get(symbol)

    def get_stock_name(self, symbol):
        return self.names.get(symbol)

    def get_position(self, stock_id):
        return self.position.get(stock_id)

    def get_calibration_now(self, stock_id):
        record = self.calibration.get(stock_id)
        return Rec(*record) if record else None

    def get_netvalue_close(self, stock_id, date):
        return self.netvalue.get((stock_id, str(date)))

    def get_history_close(self, stock_id, date):
        return self.history.get((stock_id, str(date)))

    def get_history_adjclose(self, stock_id, date):
        return self.adjclose.get((stock_id, str(date)))

    def get_latest_net_value_close(self, stock_id):
        return self.netvalue.get((stock_id, "latest"))

    def get_latest_net_value_date(self, stock_id):
        return self.netvalue.get((stock_id, "latest_date"))

    def get_latest_history_close(self, stock_id):
        return self.history.get((stock_id, "latest"))

    def get_latest_history_date(self, stock_id):
        return self.history.get((stock_id, "latest_date"))

    def get_prev_history_close(self, stock_id, date):
        return self.history.get((stock_id, "prev"))

    def count_net_value(self, stock_id):
        return self.net_value_count

    def write_calibration(self, stock_id, date, value):
        self.written_calibration.append((stock_id, date, value))
        return True


class GbToUtf8Test(unittest.TestCase):
    """`GbToUtf8()`：PHP 是手写查表，Python 用标准库 ``gb18030``。"""

    def test_decodes_bytes(self):
        self.assertEqual(gb_to_utf8("易方达".encode("gb18030")), "易方达")

    def test_returns_chinese_text_unchanged(self):
        """已经是中文的 str 不能再转（会乱码），必须原样返回。"""
        self.assertEqual(gb_to_utf8("易方达"), "易方达")

    def test_handles_none(self):
        self.assertIsNone(gb_to_utf8(None))

    def test_ascii_passes_through(self):
        self.assertEqual(gb_to_utf8("QQQ"), "QQQ")


class MysqlReferenceTest(unittest.TestCase):
    def test_position_falls_back_to_symbol_default(self):
        """库里没有仓位时回落 ``GetDefaultPosition()``：LOF 是 0.95。

        这条盯着一个真 bug：早期版本把默认值硬编码成 1.0，等于"满仓"，
        而 PHP 官网 `sz161125cn.php` 明写"仓位估算值使用0.95"。
        """
        self.assertAlmostEqual(MysqlReference("SZ161130", source=FakeSource()).get_position(), 0.95)
        self.assertAlmostEqual(MysqlReference("QQQ", source=FakeSource()).get_position(), 1.0)

    def test_stored_position_wins(self):
        source = FakeSource(position={1: 0.88})
        self.assertAlmostEqual(MysqlReference("SZ161130", source=source).get_position(), 0.88)

    def test_zero_position_is_treated_as_missing(self):
        """PHP 用真值判断，仓位写成 0 等同于"没读到"，回落默认值。"""
        source = FakeSource(position={1: 0.0})
        self.assertAlmostEqual(MysqlReference("SZ161130", source=source).get_position(), 0.95)

    def test_is_fund_by_net_value_count(self):
        self.assertTrue(MysqlReference("SZ161130", source=FakeSource(net_value_count=3)).is_fund())
        self.assertFalse(MysqlReference("QQQ", source=FakeSource(net_value_count=0)).is_fund())

    def test_get_close_uses_price_for_today(self):
        ref = MysqlReference("SZ161130", source=FakeSource())
        ref.str_price = "4.796"
        ref.str_date = "2026-10-02"
        self.assertEqual(ref.get_close("2026-10-02"), "4.796")

    def test_get_close_falls_back_to_history(self):
        source = FakeSource(history={(1, "2026-10-01"): 4.75})
        ref = MysqlReference("SZ161130", source=source)
        self.assertEqual(ref.get_close("2026-10-01"), 4.75)

    def test_get_val_without_date_is_current_price(self):
        ref = MysqlReference("SZ161130", source=FakeSource())
        ref.str_price = "4.796"
        self.assertAlmostEqual(ref.get_val(), 4.796)

    def test_get_net_value_prefers_netvalue_when_count_positive(self):
        source = FakeSource(net_value_count=2, netvalue={(1, "2026-10-01"): 4.5452},
                            history={(1, "2026-10-01"): 4.75})
        ref = MysqlReference("SZ161130", source=source)
        # 有净值记录 -> 取净值（4.5452），不是行情价（4.75）
        self.assertAlmostEqual(ref.get_net_value("2026-10-01"), 4.5452)

    def test_get_net_value_falls_back_to_price_without_records(self):
        """没有净值记录时拿行情当净值用 —— 这是 PHP 的原设计，不是兜底 bug。"""
        source = FakeSource(net_value_count=0, history={(1, "2026-10-01"): 4.75})
        ref = MysqlReference("SZ161130", source=source)
        self.assertAlmostEqual(ref.get_net_value("2026-10-01"), 4.75)

    def test_get_net_value_display_precision(self):
        """有净值记录用 4 位（``NETVALUE_PRECISION``），没有就用符号精度。"""
        with_netvalue = MysqlReference("SZ161130", source=FakeSource(net_value_count=2))
        self.assertEqual(with_netvalue.get_net_value_display(4.5452), "4.5452")
        self.assertEqual(NETVALUE_PRECISION, 4)

        without = MysqlReference("SZ161130", source=FakeSource(net_value_count=0))
        # 没有净值时显示的是行情价，按符号精度（3 位）
        self.assertEqual(without.get_net_value_display(4.5452), "4.545")

    def test_get_net_value_display_of_false_is_empty(self):
        ref = MysqlReference("SZ161130", source=FakeSource())
        self.assertEqual(ref.get_net_value_display(None), "")

    def test_chinese_name_falls_back_to_english(self):
        """数据里常常只有一个名字，那时统一放在 strName。"""
        ref = MysqlReference("SZ161130", source=FakeSource())
        ref.str_name = "易方达纳斯达克100LOF"
        self.assertEqual(ref.get_chinese_name(), "易方达纳斯达克100LOF")

    def test_est_net_value_priority_is_realtime_fair_official(self):
        """新鲜度优先：实时 > 参考 > 官方。

        三个估值字段是 ``FundReference`` 才有的（PHP 声明在 ``fundref.php:26-28``）；
        ``MysqlReference::GetOfficialNetValue()`` 是**写死 ``return false``**
        （``mysqlref.php:81``），所以这里必须用 ``FundReference``。
        """
        ref = FundReference("SZ161130", source=FakeSource())
        self.assertIsNone(ref.get_est_net_value())

        ref.official_net_value = 4.5
        self.assertAlmostEqual(ref.get_est_net_value(), 4.5)
        ref.fair_net_value = 4.6
        self.assertAlmostEqual(ref.get_est_net_value(), 4.6)
        ref.realtime_net_value = 4.7
        self.assertAlmostEqual(ref.get_est_net_value(), 4.7)

    def test_base_class_has_no_estimation_at_all(self):
        """`MysqlReference` 基类的三个估值恒为 ``None`` —— 对齐 PHP 的 ``return false``。"""
        ref = MysqlReference("SZ161130", source=FakeSource())
        ref.official_net_value = 4.5
        self.assertIsNone(ref.get_official_net_value())
        self.assertIsNone(ref.get_fair_net_value())
        self.assertIsNone(ref.get_realtime_net_value())

    def test_missing_source_methods_do_not_raise(self):
        """数据源缺方法时按"取不到"处理，与 PHP 的 false 对齐。"""
        ref = MysqlReference("SZ161130", source=object())
        self.assertIsNone(ref.get_stock_id())
        self.assertIsNone(ref.get_close("2026-10-01"))

        # IsFund() 的第二条路是 IsFundA()，只看符号本身、与数据源无关；
        # SZ161130 是 A 股基金，返回 True 才是对的，所以这里用非基金符号。
        self.assertFalse(MysqlReference("QQQ", source=object()).is_fund())


class ConstructionIsOfflineTest(unittest.TestCase):
    """**回归**：构造引用对象不许联网。

    PHP 的 ``FundReference::LoadData()`` 会调 ``LoadSinaFundData()`` 抓基金名。
    本版改成从数据源取名字（见 ``refobj`` 模块 docstring 取舍 1），
    所以这里把新浪加载换成"一调用就抛异常"，断言构造过程不会碰到它。
    """

    def test_construction_does_not_load_sina_data(self):
        def _boom(self, *args, **kwargs):
            raise AssertionError("构造引用对象不该联网抓行情")

        original = stockref.StockReference.load_sina_data
        stockref.StockReference.load_sina_data = _boom
        try:
            source = FakeSource(names={"SZ161130": "易方达纳斯达克100LOF"})
            ref = QdiiReference("SZ161130", source=source)
            self.assertEqual(ref.get_chinese_name(), "易方达纳斯达克100LOF")
        finally:
            stockref.StockReference.load_sina_data = original

    def test_missing_name_keeps_has_data_false(self):
        """数据源没有名字时等价于 PHP 抓失败：``SetHasData(false)``。"""
        ref = FundReference("SZ161130", source=FakeSource())
        self.assertFalse(ref.has_quote_data())


class FundReferenceTest(unittest.TestCase):
    def test_factor_comes_from_calibration(self):
        source = FakeSource(calibration={1: ("2026-10-01", 32.079543)})
        self.assertAlmostEqual(FundReference("SZ161130", source=source).get_factor(), 32.079543)

    def test_factor_defaults_to_one(self):
        self.assertAlmostEqual(FundReference("SZ161130", source=FakeSource()).get_factor(), 1.0)

    def test_adjust_and_reverse_are_inverse(self):
        source = FakeSource(calibration={1: ("2026-10-01", 32.079543)},
                            netvalue={(1, "2026-10-01"): 4.5452}, position={1: 0.95})
        ref = FundReference("SZ161130", source=source)

        self.assertAlmostEqual(ref._get_calibration_base_val(), 4.5452)
        adjusted = ref.adjust_position(100.0)
        self.assertAlmostEqual(ref.reverse_adjust_position(adjusted), 100.0, places=6)

    def test_forex_ref_uses_spot_for_etf_and_cny_for_lof(self):
        """ETF 按盘中即期汇率申赎，LOF 只能按当天中间价。"""
        lof = QdiiReference("SZ161130", source=FakeSource(), auto_estimate=False)
        self.assertIs(lof.get_forex_ref(), lof.cny_ref)

        etf = QdiiReference("SH513100", source=FakeSource(), auto_estimate=False)
        self.assertIs(etf.get_forex_ref(), etf.forex_ref)

    def test_set_est_ref_accepts_symbol_string(self):
        ref = FundReference("SZ161130", source=FakeSource())
        ref.set_est_ref("QQQ")
        self.assertIsInstance(ref.get_est_ref(), EstReference)
        self.assertEqual(ref.get_est_ref().get_symbol(), "QQQ")

    def test_realtime_ref_requires_pair_ref(self):
        ref = FundReference("SZ161130", source=FakeSource())
        self.assertIsNone(ref.get_realtime_ref())

        class WithPair:
            def get_pair_ref(self):
                return "pair"

        ref.set_est_ref(WithPair())
        self.assertEqual(ref.get_realtime_ref(), "pair")

    def test_delegates_to_stock_ref(self):
        """A 股基金的场内行情才是"真价格"，PHP 把这几个方法委托给 stock_ref。"""

        class StockRef:
            def get_symbol(self):
                return "SZ161130A"

            def get_stock_id(self):
                return 99

            def get_price_display(self, disp=None, prev=None, precision=None):
                return "delegated"

            def get_percentage_text(self, divisor=None, dividend=None):
                return "1.23%"

        ref = FundReference("SZ161130", source=FakeSource(), stock_ref=StockRef())
        self.assertEqual(ref.get_symbol(), "SZ161130A")
        self.assertEqual(ref.get_stock_id(), 99)
        self.assertEqual(ref.get_price_display(), "delegated")
        self.assertEqual(ref.get_percentage_text(), "1.23%")

class StockRefAutoBuildTest(unittest.TestCase):
    """``php/stock/fundref.php:19`` 那行 ``if ($this->IsFundA())`` 的迁移版。

    补上它之前，``GetStockRef()`` 恒为 ``None``，QDII 均线表的换算列整列空白
    （生产端 ``sz161125cn.php`` 该列是 ``SZ161125 / 3.179``）。
    """

    def test_a_fund_builds_one_automatically(self):
        """A 股基金（LOF）：构造时就挂上场内行情引用。"""
        ref = FundReference("SZ161130", source=FakeSource())
        stock_ref = ref.get_stock_ref()
        self.assertIsNotNone(stock_ref)
        self.assertEqual(stock_ref.get_symbol(), "SZ161130")

    def test_qdii_reference_inherits_it(self):
        """``_callbackQdiiSma`` 走的是 ``QdiiReference``，它也必须带上。"""
        ref = QdiiReference("SZ161130", source=FakeSource(), auto_estimate=False)
        self.assertIsNotNone(ref.get_stock_ref())

    def test_non_a_fund_has_none(self):
        """``IsFundA()`` 为假的符号一律不建 —— PHP 那边 ``$stock_ref`` 保持 ``false``。"""
        for symbol in ("QQQ", "^NDX", "hf_NQ", "USCNY"):
            ref = FundReference(symbol, source=FakeSource())
            self.assertIsNone(ref.get_stock_ref(), symbol)

    def test_injected_one_wins(self):
        """调用方显式给的优先 —— 自动建只是兜底。"""

        class Injected:
            def get_symbol(self):
                return "INJECTED"

        ref = FundReference("SZ161130", source=FakeSource(), stock_ref=Injected())
        self.assertIsInstance(ref.get_stock_ref(), Injected)

    def test_the_built_one_is_not_a_fund_reference(self):
        """**不能**是 ``FundReference`` —— 否则构造时又去建一个 stock_ref，递归。"""
        ref = FundReference("SZ161130", source=FakeSource())
        stock_ref = ref.get_stock_ref()
        self.assertIsInstance(stock_ref, MysqlReference)
        self.assertNotIsInstance(stock_ref, FundReference)

    def test_delegation_stays_identity(self):
        """自动建的 stock_ref 与自己同符号，所以委托**必须是恒等变换**。

        这条最要紧：``get_symbol()`` / ``get_stock_id()`` 会转发给 stock_ref，
        一旦两者不一致，详情页上几乎所有段落（估值 / 校准 / 参考数据）都会跟着
        错 —— 所以特意钉住"补这行前后这两个方法的返回值不变"。
        """
        source = FakeSource()
        plain = MysqlReference("SZ161130", source=source)
        ref = FundReference("SZ161130", source=source)

        self.assertEqual(ref.get_symbol(), plain.get_symbol())
        self.assertEqual(ref.get_stock_id(), plain.get_stock_id())
        self.assertEqual(ref.get_symbol(), "SZ161130")

    def test_build_failure_keeps_none(self):
        """建不出来就回退 ``None`` —— 这一列只是装饰，不该让整页 500。"""

        def boom(symbol):
            raise RuntimeError("no source")

        original = FundReference._build_stock_ref
        FundReference._build_stock_ref = lambda self, symbol: boom(symbol)
        try:
            ref = FundReference("SZ161130", source=FakeSource())
            self.assertIsNone(ref.get_stock_ref())
            self.assertEqual(ref.get_symbol(), "SZ161130")
        finally:
            FundReference._build_stock_ref = original

    def test_source_is_passed_through(self):
        """stock_ref 要共享同一个数据源，否则它读不到库快照。"""
        source = FakeSource()
        ref = FundReference("SZ161130", source=source)
        self.assertIs(ref.get_stock_ref().source, source)

    def test_insert_fund_calibration_prefers_source_writer(self):
        source = FakeSource()
        ref = FundReference("SZ161130", source=source)
        ref.str_date = "2026-10-02"
        ref.factor = 41.5

        self.assertTrue(ref.insert_fund_calibration())
        self.assertEqual(source.written_calibration, [(1, "2026-10-02", 41.5)])

    def test_insert_fund_calibration_without_date_is_noop(self):
        ref = FundReference("SZ161130", source=FakeSource())
        self.assertFalse(ref.insert_fund_calibration())


class CnyReferenceTest(unittest.TestCase):
    """`CnyReference`：汇率只有每天一个中间价，且不在行情表里。"""

    def test_loads_from_netvalue_and_fixes_time(self):
        source = FakeSource(netvalue={(8, "latest"): 6.705, (8, "latest_date"): "2026-10-02"},
                            history={(8, "prev"): 6.7})
        ref = CnyReference("USCNY", source=source)

        self.assertEqual(ref.get_price(), "6.705")
        self.assertEqual(ref.get_date(), "2026-10-02")
        # 中国外汇交易中心 09:15 公布中间价
        self.assertEqual(ref.get_time(), "09:15:00")

    def test_get_close_reads_netvalue_table(self):
        """汇率压根不在 stockhistory 里，`CnyReference` 把 GetClose 改成查净值表。"""
        source = FakeSource(netvalue={(8, "2026-10-01"): 6.71}, history={(8, "2026-10-01"): 1.0})
        ref = CnyReference("USCNY", source=source)
        self.assertEqual(ref.get_close("2026-10-01"), 6.71)


class QdiiRegionTest(unittest.TestCase):
    """四个地区的中间价与即期外汇符号（``qdiiref.php`` 底部四个子类）。"""

    def test_region_symbols(self):
        self.assertEqual(QDII_REGIONS["us"], ("USCNY", "fx_susdcny"))
        self.assertEqual(QDII_REGIONS["hk"], ("HKCNY", "fx_shkdcny"))
        self.assertEqual(QDII_REGIONS["jp"], ("JPCNY", "fx_sjpycny"))
        self.assertEqual(QDII_REGIONS["eu"], ("EUCNY", "fx_seurcny"))

    def test_us_fund_uses_us_cny(self):
        ref = QdiiReference("SZ161130", source=FakeSource(), auto_estimate=False)
        self.assertEqual(ref.cny_symbol, "USCNY")
        self.assertEqual(ref.forex_symbol, "fx_susdcny")
        self.assertEqual(ref.get_cny_ref().get_symbol(), "USCNY")

    def test_hk_fund_uses_hk_cny(self):
        ref = QdiiReference("SH510900", source=FakeSource(), region="hk", auto_estimate=False)
        self.assertEqual(ref.cny_symbol, "HKCNY")
        self.assertEqual(ref.forex_symbol, "fx_shkdcny")

    def test_est_symbol_resolution(self):
        """``SZ161130`` 的估值标的是 ``^NDX``（不是 ``QQQ``）。"""
        ref = QdiiReference("SZ161130", source=FakeSource(), auto_estimate=False)
        self.assertEqual(ref.get_est_ref().get_symbol(), "^NDX")

    def test_build_guesses_region(self):
        source = FakeSource()
        self.assertEqual(build_qdii_reference("SZ161130", source=source, auto_estimate=False).region, "us")
        self.assertEqual(build_qdii_reference("SH510900", source=source, auto_estimate=False).region, "hk")

    def test_build_uppercases_symbol(self):
        """小写 URL 符号（``/fund/sz161125``）也必须解析出 est_ref。

        对应 PHP ``StockGetSymbol()``（``stock.php:30``）的 strtoupper 分支：
        ``QdiiGetEstSymbol()`` 是查表，符号表键全大写，Python dict 不区分
        大小写就静默查不到（估值全空、不报错）。详见
        :func:`ref.refobj.build_qdii_reference` 的 docstring。
        """
        ref = build_qdii_reference("sz161125", source=FakeSource(), auto_estimate=False)
        self.assertEqual(ref.get_symbol(), "SZ161125")
        self.assertIsNotNone(ref.get_est_ref())
        self.assertEqual(ref.get_est_ref().get_symbol(), "^GSPC")
        # 带下划线的符号（外汇那类）不大写 —— PHP 的 strpos('_') 守卫
        ref2 = build_qdii_reference("fx_susdcny", source=FakeSource(), auto_estimate=False)
        self.assertEqual(ref2.get_symbol(), "fx_susdcny")


class QdiiValueTest(unittest.TestCase):
    """`GetQdiiValue` / `GetEstValue` / 份额换算。"""

    def _reference(self):
        source = FakeSource(
            calibration={1: ("2026-10-01", 32.079543)},
            netvalue={(1, "2026-10-01"): 4.5452,
                      # LOF 的汇率走中间价引用，所以 USCNY(8) 也得有最新中间价
                      (8, "latest"): 6.705, (8, "latest_date"): "2026-10-02"},
            position={1: 0.95},
        )
        ref = QdiiReference("SZ161130", source=source, auto_estimate=False)
        # 不传汇率时取的正是这个中间价，两个方向必须用同一个数
        self.assertEqual(ref.get_forex_ref().get_price(), "6.705")
        return ref

    def test_get_qdii_value_formula(self):
        """``QdiiGetVal`` 之后再过一次仓位调整。"""
        ref = self._reference()
        raw = 31098.7787 * 6.705 / 32.079543
        expected = 0.95 * raw + 0.05 * 4.5452
        self.assertAlmostEqual(ref.get_qdii_value(31098.7787, 6.705), expected, places=6)

    def test_get_est_value_is_the_inverse(self):
        """``GetEstValue(GetQdiiValue(x)) == x`` —— 这是它能自证的唯一约束。

        注意两个方向取汇率的方式必须一致：``GetQdiiValue`` 不传汇率时用
        ``GetForexRef()->GetPrice()``，``GetEstValue`` 也用它。所以假数据里
        USCNY 必须有最新中间价，否则算出来的逆是 0.0。
        """
        ref = self._reference()
        for value in (31098.7787, 7790.5298, 100.0):
            self.assertAlmostEqual(ref.get_est_value(ref.get_qdii_value(value, 6.705)), value, places=4)

    def test_get_qdii_value_without_factor_is_zero(self):
        ref = QdiiReference("SZ161130", source=FakeSource(), auto_estimate=False)
        self.assertEqual(ref.factor, 1.0)
        self.assertGreater(ref.get_qdii_value(1.0, 1.0), 0.0)

    def test_quantity_conversion(self):
        ref = self._reference()
        self.assertEqual(ref.get_est_quantity(3200), int(3200 / 32.079543))
        self.assertEqual(ref.get_qdii_quantity(100), int(100 * 32.079543))

    def test_quantity_round_trip_loses_less_than_one_unit(self):
        """份额往返会丢一点 —— ``int()`` 截断造成的，PHP 同样如此。

        丢的量**小于一个中间层单位**：先把基金份额除以校准值再截断，最多丢
        不到 1 份中间层，乘回去就是不到 ``fFactor``。100000 份实测回到 99991，
        差 9 —— 校准值 32.08，所以这不是 bug，是截断的正常上界。
        """
        ref = self._reference()
        factor = ref.get_factor()
        for quantity in (100000, 50000, 12345):
            back = ref.get_qdii_quantity(ref.get_est_quantity(quantity))
            self.assertLessEqual(back, quantity)
            self.assertGreater(back, quantity - factor)


class QdiiEstimateTest(unittest.TestCase):
    """``EstNetValue()``：**只做流程，算法委托给 fundestcalc**。

    用一个假 estimator 断言"三个字段被正确填上"，而不是在这里重算一遍公式
    —— 两条实现并存正是本项目出过事故的原因。
    """

    class FakeEstimator:
        def __init__(self, official=None, fair=None, realtime=None):
            self.official = official
            self.fair = fair
            self.realtime = realtime
            self.store = None
            self.recorded = []

        def compute_official(self, symbol):
            return self.official

        def compute_fair(self, symbol):
            return self.fair

        def compute_realtime(self, symbol):
            return self.realtime

        def record_official(self, symbol):
            self.recorded.append(symbol)
            return True

    class EstRecord:
        def __init__(self, date, value):
            self.date = date
            self.value = value

    def _reference(self, estimator):
        source = FakeSource(
            calibration={1: ("2026-10-01", 32.079543)},
            netvalue={(1, "2026-10-01"): 4.5452, (8, "2026-10-02"): 6.705},
            position={1: 0.95},
        )
        ref = QdiiReference("SZ161130", source=source, auto_estimate=False, estimator=estimator)
        # 中间层（^NDX）与官方净值同日：官方 EST 用那天的中间价
        ref.get_est_ref().str_date = "2026-10-02"
        ref.get_cny_ref().str_price = "6.705"
        # 中间价已经翻到 10-03。这个"错开"是必要的：PHP 只在
        # 汇率日期或中间层日期 != 官方日期时才算参考 EST —— 都相同说明
        # 参考 EST 就是官方 EST，没有信息量。
        ref.get_cny_ref().str_date = "2026-10-03"
        return ref

    def test_fills_three_fields_from_estimator(self):
        estimator = self.FakeEstimator(
            official=self.EstRecord("2026-10-02", 4.592143),
            fair=self.EstRecord("2026-10-02", 4.6),
            realtime=self.EstRecord("2026-10-02", 4.61),
        )
        ref = self._reference(estimator)
        # 让 get_realtime_ref 有值，实时 EST 才会算
        ref.get_est_ref().get_pair_ref = lambda: "pair"

        ref.est_net_value()

        self.assertAlmostEqual(ref.get_official_net_value(), 4.592143)
        self.assertEqual(ref.get_official_date(), "2026-10-02")
        self.assertAlmostEqual(ref.get_fair_net_value(), 4.6)
        self.assertAlmostEqual(ref.get_realtime_net_value(), 4.61)
        # 新鲜度优先
        self.assertAlmostEqual(ref.get_est_net_value(), 4.61)

    def test_records_official_est(self):
        estimator = self.FakeEstimator(official=self.EstRecord("2026-10-02", 4.59))
        ref = self._reference(estimator)

        ref.est_net_value()

        self.assertEqual(estimator.recorded, ["SZ161130"])

    def test_fair_est_needs_realtime_ref(self):
        """实时 EST 只在中间层带配对引用时才存在（``$est_ref->EstFromPair()``）。"""
        estimator = self.FakeEstimator(official=self.EstRecord("2026-10-02", 4.59),
                                       realtime=self.EstRecord("2026-10-02", 4.61))
        ref = self._reference(estimator)

        ref.est_net_value()

        self.assertIsNone(ref.get_realtime_net_value())

    def test_without_estimator_is_not_fatal(self):
        """算不出 EST 也不能让页面挂：三个字段保持空。"""
        source = FakeSource()
        ref = QdiiReference("SZ161130", source=source)
        self.assertIsNone(ref.get_est_net_value())

    def test_estimator_exception_is_swallowed(self):
        class Boom:
            def compute_official(self, symbol):
                raise RuntimeError("模拟估算失败")

            def compute_fair(self, symbol):
                raise RuntimeError("模拟估算失败")

            def compute_realtime(self, symbol):
                raise RuntimeError("模拟估算失败")

        ref = self._reference(Boom())
        ref.est_net_value()
        self.assertIsNone(ref.get_official_net_value())


class QdiiAdjustFactorTest(unittest.TestCase):
    """``AdjustFactor()``：**只在新的官方净值到货那天**重算校准值。"""

    def test_does_not_recalculate_when_net_value_already_known(self):
        """该日期已有净值 -> PHP 的 ``InsertDaily`` 返回 false -> 不重算。"""
        source = FakeSource(netvalue={(1, "2026-10-02"): 4.5452})
        ref = QdiiReference("SZ161130", source=source, auto_estimate=False)
        ref.str_date = "2026-10-02"
        ref.str_price = "4.796"

        self.assertIsNone(ref.adjust_factor())

    def test_recalculates_when_net_value_is_new(self):
        source = FakeSource(
            netvalue={(6, "2026-10-02"): 31098.7787, (8, "2026-10-02"): 6.705},
        )
        ref = QdiiReference("SZ161130", source=source, auto_estimate=False)
        ref.str_date = "2026-10-02"
        ref.str_price = "4.796"
        ref.get_cny_ref().str_date = "2026-10-01"

        factor = ref.adjust_factor()

        expected = 31098.7787 * 6.705 / 4.796
        self.assertIsNotNone(factor)
        self.assertAlmostEqual(factor, expected, places=6)
        self.assertAlmostEqual(ref.get_factor(), expected, places=6)

    def test_returns_none_when_est_value_missing(self):
        source = FakeSource(netvalue={(8, "2026-10-02"): 6.705})
        ref = QdiiReference("SZ161130", source=source, auto_estimate=False)
        ref.str_date = "2026-10-02"
        ref.str_price = "4.796"
        ref.get_cny_ref().str_date = "2026-10-01"

        self.assertIsNone(ref.adjust_factor())


class PairReferenceTest(unittest.TestCase):
    """``fundpair`` 的"我跟踪谁" —— 实时 EST 的**前置条件**。

    没有这一档，``FundReference.get_realtime_ref()`` 恒为 ``None``，
    ``QdiiReference`` 就不算实时估值，**页面 EST 表会少两列**（实测踩过）。
    """

    def test_symbol_without_pair_returns_none(self):
        """``SZ161130`` 在 ``fundpair`` 里没有"我跟踪谁"的行 → ``None``。"""
        ref = EstReference("SZ161130", source=FakeSource())
        self.assertIsNone(ref.get_pair_ref())

    def test_index_pairs_to_its_future(self):
        """``^NDX -> hf_NQ``：生产端参考数据表第 3 行就是这么来的。"""
        source = FakeSource(pairs={"^NDX": "hf_NQ"})
        ref = EstReference("^NDX", source=source)

        pair = ref.get_pair_ref()
        self.assertIsNotNone(pair)
        self.assertEqual(pair.get_symbol(), "hf_NQ")

    def test_dict_and_tuple_cursor_rows_both_work(self):
        """真驱动给 dict 行、有些测试给 tuple 行，两种都必须认。

        只认一种就会在换驱动时静默失效（返回 ``None``），而表现是
        "页面少两列"，不是报错 —— 所以两种都钉住。
        """
        for as_dict in (False, True):
            with self.subTest(as_dict=as_dict):
                source = FakeSource(pairs={"^NDX": "hf_NQ"}, pairs_as_dict=as_dict)
                pair = EstReference("^NDX", source=source).get_pair_ref()
                self.assertIsNotNone(pair)
                self.assertEqual(pair.get_symbol(), "hf_NQ")

    def test_pair_reference_is_cached(self):
        """第二次取配对不该再查库（惰性 + 缓存）。"""
        source = FakeSource(pairs={"^NDX": "hf_NQ"})
        ref = EstReference("^NDX", source=source)

        first = ref.get_pair_ref()
        cursors_after_first = len(source.connection.cursors)
        second = ref.get_pair_ref()

        self.assertIs(first, second)
        self.assertEqual(len(source.connection.cursors), cursors_after_first)

    def test_no_source_is_safe(self):
        """没有数据源时不抛异常，只是取不到配对。"""
        self.assertIsNone(EstReference("^NDX").get_pair_ref())

    def test_pair_reference_does_not_recurse(self):
        """``^NDX -> hf_NQ`` 的配对引用**自己不再找配对**，避免无限递归。

        PHP 靠 ``IsSinaFutureUS()`` 打断这个环；本版用更直白的办法 ——
        配对引用是 ``EstReference``，而 ``EstReference.get_pair_ref()``
        会用**自己的**符号再查一次。若 ``hf_NQ`` 也配了对自己，就会递归；
        这里确保它取不到就停。
        """
        source = FakeSource(pairs={"^NDX": "hf_NQ"})
        ref = EstReference("^NDX", source=source)
        pair = ref.get_pair_ref()

        # hf_NQ 自己不在 pairs 里 → 它的配对是 None，链在这里断掉
        self.assertIsNone(pair.get_pair_ref())


class AliasTest(unittest.TestCase):
    """PHP 原名别名（见 AGENTS.md）必须真的指向同一个实现。"""

    def test_method_aliases(self):
        ref = MysqlReference("SZ161130", source=FakeSource())
        self.assertEqual(ref.GetClose("2026-10-01"), ref.get_close("2026-10-01"))
        self.assertEqual(ref.GetNetValue("2026-10-01"), ref.get_net_value("2026-10-01"))
        self.assertEqual(ref.GetPosition(), ref.get_position())

        fund = QdiiReference("SZ161130", source=FakeSource(), auto_estimate=False)
        self.assertEqual(fund.GetQdiiValue(1.0, 1.0), fund.get_qdii_value(1.0, 1.0))
        self.assertEqual(fund.GetEstQuantity(100), fund.get_est_quantity(100))

    def test_function_alias(self):
        self.assertIs(refobj.GbToUtf8, gb_to_utf8)


class EstReferenceSelectionTest(unittest.TestCase):
    """``build_est_reference`` —— ``qdiiref.php:245`` 的"持仓型 / 配对型"选型。

    **演示库跑不到这个分支**：只有 ``SH513050`` 有持仓数据，而它不是任何 QDII
    基金的 est 标的。这两个用例是它唯一的覆盖 —— 等真有持仓型标的入库时，
    行为就已经是被测过的，不用再赌。
    """

    def test_holdings_type_gets_holdings_reference(self):
        #: ``QQQ`` 在 FakeSource 里是 4；给 36 行持仓 -> 判成持仓型
        ref = build_est_reference("QQQ", FakeSource(counts={4: 36}))
        self.assertEqual(type(ref).__name__, "HoldingsReference")

    def test_non_holdings_type_gets_est_reference(self):
        ref = build_est_reference("QQQ", FakeSource(counts={4: 0}))
        self.assertIsInstance(ref, EstReference)
        self.assertNotEqual(type(ref).__name__, "HoldingsReference")

    def test_count_holdings_reads_rows(self):
        self.assertEqual(count_holdings(FakeSource(counts={4: 36}), "QQQ"), 36)

    def test_count_holdings_unknown_symbol_is_zero(self):
        """符号不在 ``stock`` 表 -> 0，而不是抛异常。"""
        self.assertEqual(count_holdings(FakeSource(counts={4: 36}), "SPY"), 0)

    def test_count_holdings_without_connection_is_zero(self):
        """没有连接（纯内存数据源）-> 0。它只是选型判据，不能让构造失败。"""

        class NoConnection:
            def get_stock_id(self, _symbol):
                return 4

        self.assertEqual(count_holdings(NoConnection(), "QQQ"), 0)

    def test_qdii_reference_picks_holdings_when_present(self):
        """``QdiiReference`` 构造要走 :func:`build_est_reference` 而不是一律包
        ``EstReference`` —— 否则持仓口径永远到不了估值那一步。

        ``SZ161130`` 的 est 标的是 ``^NDX``（FakeSource 里 id=6），
        所以计数挂在 6 上。**挂在 QQQ(4) 上这个用例会安静地测不到东西** ——
        符号选错不报错，只是走不到目标分支（见 pitfalls #70）。
        """
        fund = QdiiReference("SZ161130", source=FakeSource(counts={6: 36}),
                             auto_estimate=False)
        est_ref = fund.get_est_ref()
        self.assertIsNotNone(est_ref, "est_symbol 没解析出来，用例的前提不成立")
        self.assertEqual(type(est_ref).__name__, "HoldingsReference")

    def test_qdii_reference_defaults_to_est_reference(self):
        fund = QdiiReference("SZ161130", source=FakeSource(counts={6: 0}),
                             auto_estimate=False)
        self.assertIsInstance(fund.get_est_ref(), EstReference)


if __name__ == "__main__":
    unittest.main()
