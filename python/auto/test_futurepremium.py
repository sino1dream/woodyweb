"""``auto/futurepremium.py`` 的单元测试。

对应 ``php/ui/smaparagraph.php:179`` 的 ``GetFutureInterestPremium()`` /
``RefGetFuturePremium()`` 与 ``:200`` 的 ``_callbackFutureSma()``。

钉的是三类"抄错也不报错"的地方：

1. **PHP 的 ``round()`` 是四舍五入，Python 内建是银行家舍入** ——
   ``0.125`` 上两边就分叉（``0.13`` vs ``0.12``）；
2. **天数不是整数** —— 跨夏令时切换时 ``GetTick()`` 的差是 ``68.0417`` 天，
   拿两个 ``date`` 直接相减会得到整 68；
3. **利率单位是百分点** —— ``futurepremium.close`` 存的是 ``5.00625``
   （表示 5.00625%），要 ``/ 100``。

与 PHP 的逐项对照在 ``_php_futurepremium_compare.py``（13 例 0 差异）。
"""

import datetime
import unittest

from futurepremium import (
    DAYS_IN_YEAR,
    DEFAULT_FUTURE_END_DATE,
    DEFAULT_FUTURE_RATE,
    future_sma_value,
    get_future_interest_premium,
    php_round,
    ref_get_future_premium,
)


class PhpRoundTest(unittest.TestCase):
    """``round()`` 的 PHP 语义：half away from zero。"""

    def test_half_goes_up_not_to_even(self):
        """PHP 给 0.13，Python 内建给 0.12 —— 这就是要分家的那一条。"""
        self.assertEqual(php_round(0.125, 2), 0.13)
        self.assertNotEqual(php_round(0.125, 2), round(0.125, 2))

    def test_integers_round_away_from_zero(self):
        self.assertEqual(php_round(2.5), 3.0)
        self.assertEqual(php_round(-2.5), -3.0)
        self.assertNotEqual(php_round(2.5), round(2.5))

    def test_negative_keeps_its_sign(self):
        self.assertEqual(php_round(-0.125, 2), -0.13)

    def test_zero_digits_gives_a_float(self):
        self.assertEqual(php_round(31137.575), 31138.0)


class InterestPremiumTest(unittest.TestCase):
    """``1.0 + $fRate * $iDay / 365.0``。"""

    def test_same_day_is_exactly_one(self):
        """没有剩余天数 → 溢价就是 1，即"不打折不加价"。

        注意跨 DST 时"今天对今天"仍是 0 天（同一个零点），所以这条稳。
        """
        value = get_future_interest_premium(
            0.05, "2026-10-11", now=datetime.datetime(2026, 10, 11))
        self.assertAlmostEqual(value, 1.0)

    def test_zero_rate_is_one(self):
        value = get_future_interest_premium(
            0.0, "2026-12-18", now=datetime.datetime(2026, 10, 11))
        self.assertAlmostEqual(value, 1.0)

    def test_expired_contract_gives_below_one(self):
        """到期日已过 → 天数为负 → 溢价小于 1。"""
        value = get_future_interest_premium(
            0.0500625, "2025-03-21", now=datetime.datetime(2026, 10, 11))
        self.assertLess(value, 1.0)

    def test_days_are_whole_when_no_dst_in_between(self):
        """7 月到 8 月都在夏令时内 → 天数是整数 31。"""
        value = get_future_interest_premium(
            0.05, "2026-08-01", now=datetime.datetime(2026, 7, 1))
        self.assertAlmostEqual((value - 1.0) * DAYS_IN_YEAR / 0.05, 31.0, places=6)

    def test_dst_switch_makes_the_day_count_fractional(self):
        """跨 11 月夏令时结束时，零点到零点**不是**整数天。

        PHP 的 ``GetTick()`` 是 ``mktime(0,0,0,...)``，所以天数是
        ``68.0417`` 而不是 68。用两个 ``date`` 直接相减会得到 68，
        与 PHP 差 0.04 天 —— 在 365 分之一的尺度上够让逐项对照失败。
        """
        value = get_future_interest_premium(
            0.0500625, "2026-12-18", now=datetime.datetime(2026, 10, 11))
        days = (value - 1.0) * DAYS_IN_YEAR / 0.0500625
        self.assertAlmostEqual(days, 68.04166666666667, places=6)
        self.assertNotAlmostEqual(days, 68.0, places=3)

    def test_accepts_a_date_object_and_a_string(self):
        """``$strEndDate`` 在 PHP 里是字符串；Python 侧两种都收。"""
        kwargs = {"now": datetime.datetime(2026, 10, 11)}
        self.assertAlmostEqual(
            get_future_interest_premium(0.05, "2026-11-10", **kwargs),
            get_future_interest_premium(0.05, datetime.date(2026, 11, 10), **kwargs))

    def test_defaults_match_php(self):
        """PHP 的两个默认参数（``0.0500625`` / ``'2025-03-21'``）。"""
        self.assertEqual(DEFAULT_FUTURE_RATE, 0.0500625)
        self.assertEqual(DEFAULT_FUTURE_END_DATE, "2025-03-21")


class RefGetFuturePremiumTest(unittest.TestCase):
    """读 ``futurepremium`` 表那一段。"""

    class Source:
        """模仿 ``stockdata`` 的 ``_get_daily_record('futurepremium', stock_id)``。"""

        def __init__(self, close=None, date=None):
            self._close = close
            self._date = date
            self.calls = []

        def _get_daily_record(self, table, stock_id, extra_where=None, extra_params=()):
            self.calls.append((table, stock_id))
            if self._close is None:
                return None
            return Record(self._date, self._close)

    class Ref:
        def __init__(self, stock_id=7):
            self._stock_id = stock_id

        def get_stock_id(self):
            return self._stock_id

    class Record:
        pass

    def test_rate_is_stored_in_percentage_points(self):
        """``floatval($strClose) / 100.0`` —— 表里存 5.00625 表示 5.00625%。"""
        source = self.Source(close=5.00625, date="2026-12-18")
        value = ref_get_future_premium(source, self.Ref(),
                                       now=datetime.datetime(2026, 10, 11))
        # 参考值来自 _php_futurepremium_check.php 的用例 0
        self.assertAlmostEqual(value, 1.009332427226, places=9)

    def test_missing_row_returns_false(self):
        """``return false`` —— 调用方是 ``if ($fPremium = …)``。

        返回 ``False`` 而不是 ``0.0``，是为了让"没有这条记录"与
        "利率正好是 0"在 Python 侧能分开。
        """
        self.assertIs(ref_get_future_premium(self.Source(), self.Ref()), False)

    def test_table_missing_does_not_raise(self):
        """旧库没有 ``futurepremium`` 表时不能让页面 500 —— 跳过那一段即可。"""
        class NoTable:
            def _get_daily_record(self, table, stock_id, extra_where=None, extra_params=()):
                raise Exception("Table 'stock_data.futurepremium' doesn't exist")

        self.assertIs(ref_get_future_premium(NoTable(), self.Ref()), False)

    def test_ref_without_stock_id_returns_false(self):
        class Bare:
            pass

        self.assertIs(ref_get_future_premium(self.Source(close=5.0, date="2026-12-18"),
                                             Bare()), False)

    def test_reads_the_daily_table_by_its_real_name(self):
        source = self.Source(close=5.0, date="2026-12-18")
        ref_get_future_premium(source, self.Ref())
        self.assertEqual(source.calls[0], ("futurepremium", 7))


class FutureSmaValueTest(unittest.TestCase):
    """``_callbackFutureSma()`` 的两步舍入（``round(4*$f)/4`` 再 ``round(...,2)``）。"""

    def test_rounds_to_the_quarter_tick_first(self):
        """外盘指数期货的最小变动单位是 0.25 点 —— 理论价要落在步长上。"""
        # 31137.575 * 1.01 = 31448.95075 → round(4*x)/4 = 31449.0
        self.assertEqual(future_sma_value(None, 1.01, 31137.575), 31449.0)

    def test_keeps_two_decimals(self):
        # 7866.45 * 1.02 = 8023.779 → /4 取整 → 8023.75
        self.assertEqual(future_sma_value(None, 1.02, 7866.45), 8023.75)

    def test_premium_one_leaves_the_price_alone(self):
        self.assertEqual(future_sma_value(None, 1.0, 100.0), 100.0)

    def test_zero_premium_returns_none(self):
        """``if ($fPremium = …)`` —— 0 是假值，整段不渲染。"""
        self.assertIsNone(future_sma_value(None, 0.0, 100.0))
        self.assertIsNone(future_sma_value(None, False, 100.0))

    def test_missing_est_returns_none(self):
        self.assertIsNone(future_sma_value(None, 1.01, ""))
        self.assertIsNone(future_sma_value(None, 1.01, None))


class Record:
    """``stockdata.DailyRecord`` 的最小形状。"""

    def __init__(self, date, close):
        self.date = date
        self.close = close


RefGetFuturePremiumTest.Record = Record

if __name__ == "__main__":
    unittest.main()
