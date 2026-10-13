"""``refobj.FundPairReference``（``php/stock/fundpairref.php:148-276``）单元测试。

钉住几件迁移易错的事：

1. **``calc_factor`` 的两个分支汇率方向相反** —— A 股 ``pair * cny / netvalue``、
   其它 ``pair / (netvalue * cny)``。它必须与 :meth:`est_from_pair` 严格互逆，
   否则校准值越算越偏而**看不出来**（两个方法都不报错，只是慢慢漂移）。
   本文件用往返组断言这一点。
2. **官方估值的三路回落**用 PHP 的 ``== false`` 松散口径：净值正好为 ``0``
   时也算"没取到"，要继续往下试 —— Python 里只判 ``None`` 会在那一刻算出除零。
3. **``get_fair_net_value`` 的控制流是穿透过来的**，不是两个独立的 ``if``：
   没有汇率引用时会**落到第二个判断**，而不是直接返回 ``None``。
4. **自动校准默认不写库**（``auto_write`` 默认关）—— 这是与 PHP 的刻意偏差，
   PHP 在构造函数里就写了。

**不联网、不连真库**：数据源、配对标的、汇率引用都是内存替身。
"""

from __future__ import annotations

import unittest

from ref.refobj import (
    FundPairReference,
    StockGetFundPairReference,
    _pair_val_is_false,
)

DATE = "2026-01-05"


# ---------------------------------------------------------------------------
# 替身
# ---------------------------------------------------------------------------
class FakeCursor:
    """假游标：认得的 SQL 才答，其余一律 ``None``。"""

    def execute(self, sql, params=None):
        self.sql = sql
        self.params = params

    def fetchone(self):
        return None

    def fetchall(self):
        return []

    def close(self):
        pass


class FakeConnection:
    def cursor(self):
        return FakeCursor()


class FakeSource:
    """内存数据源：够 ``load_calibration`` 走完就行（没有校准记录 → 因子回落 1.0）。"""

    STOCK = {"SZ161725": 1, "00700": 3, "USCNY": 11, "HKCNY": 12}

    def __init__(self, tables=None, calibration=None):
        self.tables = tables or {}
        self.calibration = calibration or {}
        self.connection = FakeConnection()

    def get_stock_id(self, symbol):
        return self.STOCK.get(symbol)

    def get_stock_name(self, symbol):
        return None

    def get_position(self, stock_id):
        return 1.0

    def get_calibration_now(self, stock_id):
        rec = self.calibration.get(stock_id)
        return _Rec(*rec) if rec else None

    def get_last_calibration(self, stock_id):
        return None

    def get_netvalue_close(self, stock_id, date):
        return None


class _Rec:
    def __init__(self, date, close):
        self.date = date
        self.close = close


class FakeCny:
    """够用的汇率引用：值、日期、某日收盘价都可控。"""

    def __init__(self, val=1.0, date=None, close=None):
        self._val = val
        self._date = date
        self._close = close

    def get_val(self, date=None):
        return self._val

    def get_date(self):
        return self._date

    def get_close(self, date):
        return self._close


class FakePairRef:
    """够用的配对标的：价格 / 日期 / 净值 / VWAP / 时刻都可控。"""

    def __init__(self, symbol="00700", price=100.0, date=DATE, netvalue=None,
                 vwap=None, hour_minute=1200, except_gold_cn=False, future_us=False):
        self._symbol = symbol
        self._price = price
        self._date = date
        self._netvalue = netvalue
        self.str_vwap = vwap
        self._hour_minute = hour_minute
        self._except_gold_cn = except_gold_cn
        self._future_us = future_us

    def get_symbol(self):
        return self._symbol

    def get_price(self):
        return self._price

    def get_val(self, date=None):
        return self._price

    def get_date(self):
        return self._date

    def get_net_value(self, date):
        return self._netvalue

    def get_hour_minute(self):
        return self._hour_minute

    def is_sina_future_except_gold_cn(self):
        return self._except_gold_cn

    def is_sina_future_us(self):
        return self._future_us


def build_ref(symbol="SZ161725", source=None):
    """构造一个替身齐备的 ``FundPairReference``。

    ``FakeSource`` 的 ``fundpair`` 表留空 → ``get_pair_ref()`` 返回 ``None``，
    ``__init__`` 提前 return（于是**不做自动校准、不建 netvalue_ref**），
    随后把替身挂上去。单元测试要的是**控制**，不是真实数据。

    ``last_calibration_val`` 保持 ``None`` 是有意的：
    :func:`~calc.pairestimate.est_from_pair` 末位的仓位调整在
    ``old_value == value`` 时是恒等的，留空才能得到干净的可验算式
    （``pairref.py`` 的 ``est_from_pair`` 往返测试同理）。
    """
    ref = FundPairReference(symbol, source=source or FakeSource())
    ref.position = 1.0
    ref.factor = 1.0
    ref.last_calibration_val = None
    ref.str_price = "10.0"
    ref.str_date = DATE
    return ref


# ---------------------------------------------------------------------------
# CalcFactor —— 汇率方向与 est_from_pair 的互逆
# ---------------------------------------------------------------------------
class CalcFactorTest(unittest.TestCase):
    """``calc_factor``（``fundpairref.php:213``）。"""

    def test_a_share_multiplies_cny(self):
        """A 股：``factor = pair * cny / netvalue``。"""
        ref = build_ref("SZ161725")
        ref.cny_ref = FakeCny(7.1)
        # pair=71.0 / netvalue=10.0 = 7.1 → ×7.1
        self.assertAlmostEqual(ref.calc_factor(71.0, 10.0, DATE), 50.41)

    def test_non_a_divides_cny(self):
        """非 A 股：``factor = pair / (netvalue * cny)`` —— **与 A 股方向相反**。"""
        ref = build_ref("^NDX")
        ref.cny_ref = FakeCny(7.1)
        # pair=71.0 / netvalue=10.0 = 7.1 → ÷7.1
        self.assertAlmostEqual(ref.calc_factor(71.0, 10.0, DATE), 1.0)

    def test_two_branches_differ_only_by_cny_direction(self):
        """同一个 ref 走 A 股 / 非 A 两条路，结果相差 ``cny ** 2`` 倍。

        这条把"两个分支汇率方向相反"钉成可执行的断言 —— 也是
        ``calc_factor`` **不能**复用 ``futurescalib.FuturesCalibrator.calc_factor``
        （只有 A 股那一式）的理由。
        """
        cny = 2.0
        a_ref = build_ref("SZ161725")
        a_ref.cny_ref = FakeCny(cny)
        na_ref = build_ref("^NDX")
        na_ref.cny_ref = FakeCny(cny)
        up = a_ref.calc_factor(100.0, 10.0, DATE)
        down = na_ref.calc_factor(100.0, 10.0, DATE)
        self.assertAlmostEqual(up, 20.0)      # 10 * 2
        self.assertAlmostEqual(down, 5.0)     # 10 / 2
        self.assertAlmostEqual(up / down, cny * cny)

    def test_roundtrip_a_share(self):
        """A 股往返：``est_from_pair`` 算出来的值喂回 ``calc_factor`` 要还原因子。"""
        ref = build_ref("SZ161725")
        cny = 7.1
        ref.cny_ref = FakeCny(cny)
        ref.factor = 1.25
        est = ref.est_from_pair(80.0, cny)
        self.assertAlmostEqual(ref.calc_factor(80.0, est, DATE), 1.25)

    def test_roundtrip_non_a(self):
        """非 A 往返：同上。**这条最容易错** —— 写成同一个方向时它会偏 ``cny**2``。"""
        ref = build_ref("^NDX")
        cny = 7.1
        ref.cny_ref = FakeCny(cny)
        ref.factor = 1.25
        est = ref.est_from_pair(80.0, cny)
        self.assertAlmostEqual(ref.calc_factor(80.0, est, DATE), 1.25)

    def test_without_cny_ref_returns_plain_ratio(self):
        """没有汇率引用：纯比值。"""
        ref = build_ref("SZ161725")
        ref.cny_ref = None
        self.assertAlmostEqual(ref.calc_factor(80.0, 10.0, DATE), 8.0)

    def test_zero_cny_falls_back_to_plain_ratio(self):
        """``$fCny`` 为 0：PHP 会得到 ``INF``，本版退回不含汇率的比值。"""
        ref = build_ref("SZ161725")
        ref.cny_ref = FakeCny(0.0)
        self.assertAlmostEqual(ref.calc_factor(80.0, 10.0, DATE), 8.0)

    def test_zero_netvalue_returns_none(self):
        ref = build_ref("SZ161725")
        ref.cny_ref = FakeCny(7.1)
        self.assertIsNone(ref.calc_factor(80.0, 0.0, DATE))

    def test_empty_value_returns_none(self):
        ref = build_ref("SZ161725")
        self.assertIsNone(ref.calc_factor(None, 10.0, DATE))
        self.assertIsNone(ref.calc_factor(80.0, None, DATE))


# ---------------------------------------------------------------------------
# 官方估值
# ---------------------------------------------------------------------------
class OfficialNetValueTest(unittest.TestCase):
    """``get_official_net_value``（``fundpairref.php:238``）的三路回落。"""

    def test_no_pair_ref_returns_none(self):
        ref = build_ref("SZ161725")
        self.assertIsNone(ref.get_official_net_value())

    def test_vwap_branch_wins_over_netvalue(self):
        """境外期货（黄金除外）先吃 ``str_vwap`` —— 有它就不用净值。"""
        ref = build_ref("SZ161725")
        ref.factor = 2.0
        ref.cny_ref = FakeCny(1.0)
        ref.pair_ref = FakePairRef(vwap="503.5", netvalue=100.0, price=101.0,
                                   except_gold_cn=True)
        # A 股：503.5 * 1.0 / 2.0
        self.assertAlmostEqual(ref.get_official_net_value(), 251.75)

    def test_empty_vwap_falls_back_to_netvalue(self):
        """VWAP 为空字符串 → 按 PHP ``== false`` 口径继续走净值那一路。"""
        ref = build_ref("SZ161725")
        ref.factor = 2.0
        ref.cny_ref = FakeCny(1.0)
        ref.pair_ref = FakePairRef(vwap="", netvalue=200.0, price=101.0,
                                   except_gold_cn=True)
        self.assertAlmostEqual(ref.get_official_net_value(), 100.0)

    def test_zero_netvalue_falls_back_to_price(self):
        """**净值为 0 也算没取到**（PHP 的 ``== false``），要落到配对标的现价。

        只判 ``None`` 的实现会在这一用例上直接算出 ``0`` 的结果并继续跑，
        不会报错 —— 所以必须有这条显式断言。
        """
        ref = build_ref("SZ161725")
        ref.factor = 2.0
        ref.cny_ref = FakeCny(1.0)
        ref.pair_ref = FakePairRef(netvalue=0.0, price=101.0)
        self.assertAlmostEqual(ref.get_official_net_value(), 50.5)

    def test_all_paths_empty_returns_none(self):
        ref = build_ref("SZ161725")
        ref.cny_ref = FakeCny(1.0)
        ref.pair_ref = FakePairRef(netvalue=None, price=None)
        self.assertIsNone(ref.get_official_net_value())

    def test_does_not_write_by_default(self):
        """``auto_write`` 默认关：``StockUpdateEstResult`` 那条写路径不应被走到。"""
        ref = build_ref("SZ161725")
        ref.factor = 1.0
        ref.cny_ref = FakeCny(1.0)
        ref.pair_ref = FakePairRef(netvalue=100.0, price=100.0, hour_minute=1200)
        self.assertAlmostEqual(ref.get_official_net_value(), 100.0)
        self.assertFalse(getattr(ref, "_auto_write", False))


class OfficialDateTest(unittest.TestCase):
    """``get_official_date``（``fundpairref.php:224``）。"""

    def test_no_pair_ref_returns_none(self):
        ref = build_ref("SZ161725")
        self.assertIsNone(ref.get_official_date())

    def test_uses_pair_date_when_cny_has_close(self):
        ref = build_ref("SZ161725")
        ref.pair_ref = FakePairRef(date="2026-01-05")
        ref.cny_ref = FakeCny(7.1, close=7.2)
        self.assertEqual(ref.get_official_date(), "2026-01-05")

    def test_no_cny_ref_uses_pair_date(self):
        ref = build_ref("SZ161725")
        ref.pair_ref = FakePairRef(date="2026-01-05")
        ref.cny_ref = None
        self.assertEqual(ref.get_official_date(), "2026-01-05")

    def test_missing_cny_close_falls_back_to_pair_date(self):
        """汇率那天没收盘 → 换 fundest 最新日期；**换不到就退回配对标的日期**。

        这比返回 ``None`` 好：官方 EST 那一列还画得出来，只是日期可能旧一点。
        """
        ref = build_ref("SZ161725")
        ref.pair_ref = FakePairRef(date="2026-01-05")
        ref.cny_ref = FakeCny(7.1, close=None)
        self.assertEqual(ref.get_official_date(), "2026-01-05")


# ---------------------------------------------------------------------------
# 参考 / 实时估值
# ---------------------------------------------------------------------------
class FairNetValueTest(unittest.TestCase):
    """``get_fair_net_value``（``fundpairref.php:256``）。

    控制流是**穿透过来的**：第一个 ``if`` 不满足时会落到第二个，不是各管一段。
    """

    def test_no_pair_ref_returns_none(self):
        ref = build_ref("SZ161725")
        self.assertIsNone(ref.get_fair_net_value())

    def test_cny_date_differs_returns_valuation(self):
        """有汇率引用 + 官方日期 ≠ 汇率日期 → 返回带汇率的估值。"""
        ref = build_ref("SZ161725")
        ref.factor = 2.0
        ref.cny_ref = FakeCny(4.0, date="2026-01-01")
        ref.pair_ref = FakePairRef(price=80.0, date=DATE)
        # A 股 est_from_pair(80.0, 4.0) = 80 * 4 / 2 = 160
        self.assertAlmostEqual(ref.get_fair_net_value(), 160.0)

    def test_future_pair_returns_valuation_without_cny(self):
        """穿透分支：日期相同，但配对是境外期货（非黄金）→ 用默认汇率估值。"""
        ref = build_ref("SZ161725")
        ref.factor = 2.0
        ref.cny_ref = FakeCny(4.0, date=DATE)
        ref.pair_ref = FakePairRef(price=80.0, date=DATE, except_gold_cn=True)
        # 走第二个 Return：EstFromPair(80.0)，cny 由 get_default_cny 兜底 = 4.0
        self.assertAlmostEqual(ref.get_fair_net_value(), 160.0)

    def test_same_date_and_plain_pair_returns_none(self):
        """日期相同、配对也不是期货 → PHP 返回 ``false``。"""
        ref = build_ref("SZ161725")
        ref.factor = 2.0
        ref.cny_ref = FakeCny(4.0, date=DATE)
        ref.pair_ref = FakePairRef(price=80.0, date=DATE)
        self.assertIsNone(ref.get_fair_net_value())


class RealtimeNetValueTest(unittest.TestCase):
    """``get_realtime_net_value``（``fundpairref.php:267``）。"""

    def test_none_without_callback(self):
        """**没有回调就没有实时估值** —— 这一列整列不该出现。"""
        ref = build_ref("SZ161725")
        self.assertIsNone(ref.get_realtime_net_value())

    def test_uses_callback(self):
        ref = build_ref("SZ161725")
        ref.factor = 2.0
        ref.cny_ref = FakeCny(4.0)
        ref.realtime_callback = lambda: 80.0
        self.assertAlmostEqual(ref.get_realtime_net_value(), 160.0)

    def test_falsy_callback_result_still_returns_value(self):
        """回调返回 0 时 PHP 照样算（此处 ``GetVal`` 无 ``== false`` 判断）。"""
        ref = build_ref("SZ161725")
        ref.factor = 2.0
        ref.cny_ref = FakeCny(4.0)
        ref.realtime_callback = lambda: 0.0
        self.assertAlmostEqual(ref.get_realtime_net_value(), 0.0)


# ---------------------------------------------------------------------------
# netvalue_ref / 工厂 / 松散比较
# ---------------------------------------------------------------------------
class NetValueRefTest(unittest.TestCase):
    """``get_net_value_ref``（``fundpairref.php:208``）。"""

    def test_falls_back_to_self(self):
        """``$this->netvalue_ref ? $this->netvalue_ref : $this`` —— 没有就返回自己。"""
        ref = build_ref("SZ161725")
        ref.netvalue_ref = None
        self.assertIs(ref.get_net_value_ref(), ref)

    def test_returns_netvalue_ref_when_set(self):
        ref = build_ref("SZ161725")
        other = build_ref("00700")
        ref.netvalue_ref = other
        self.assertIs(ref.get_net_value_ref(), other)


class FactoryTest(unittest.TestCase):
    """``StockGetFundPairReference``（``woody/res/php/_stock.php:142``）。"""

    def test_returns_none_without_pair_record(self):
        """门禁 ``SqlGetFundPair`` 不过 → ``None``（PHP 的 ``false``）。"""
        self.assertIsNone(
            StockGetFundPairReference("SZ161725", source=FakeSource()))

    def test_returns_none_without_source(self):
        self.assertIsNone(StockGetFundPairReference("SZ161725", source=None))


class PairValIsFalseTest(unittest.TestCase):
    """``_pair_val_is_false``：PHP ``== false`` 的松散比较。"""

    def test_zero_and_empty_are_false(self):
        for value in (None, False, "", 0, 0.0, "0"):
            self.assertTrue(_pair_val_is_false(value), repr(value))

    def test_nonzero_is_not_false(self):
        for value in (1, 0.5, "0.5", "7.1"):
            self.assertFalse(_pair_val_is_false(value), repr(value))

    def test_string_zero_dot_zero_is_not_false(self):
        """``"0.0"`` 在 PHP 里是 **truthy** —— 不能用 ``float(...) == 0`` 近似。

        用近似写法时这个用例不会被满足，而真实数据里一旦出现 ``"0.0"``
        就会落到与 PHP 不同的分支（少回落一路，可能算出 0）。
        """
        self.assertFalse(_pair_val_is_false("0.0"))
        self.assertFalse(_pair_val_is_false("0.00"))


class AutoWriteTest(unittest.TestCase):
    """``auto_write`` 开关：这是本版**与 PHP 的刻意偏差**。"""

    def test_check_adjust_time_returns_false_when_disabled(self):
        """关着的时候闸门再松也不校准 —— PHP 是无条件校准的。"""
        ref = build_ref("SZ161725")
        ref.pair_ref = FakePairRef(future_us=True)
        self.assertFalse(ref._check_adjust_time(ref.pair_ref, ref))

    def test_realtime_callback_is_stored(self):
        """回调是构造函数第二个参数（``$callback``），记录下来给实时估值用。"""
        marker = lambda: 1.0  # noqa: E731
        source = FakeSource()
        ref = build_ref("SZ161725", source=source)
        ref.realtime_callback = marker
        self.assertIs(ref.realtime_callback, marker)


if __name__ == "__main__":
    unittest.main()
