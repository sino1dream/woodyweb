"""``core/stockgroup.py`` 的单元测试（``php/stockgroup.php`` + ``multi_currency.php``）。

盯四件事：

1. **``Convert()`` 的除数守卫是"没汇率给 0.0"** —— 不是跳过赋值（那会留
   ``None``），也不是除零；
2. **``fx_susdcnh`` 归人民币桶** —— 它是汇率工具不是持仓，走美元分支会让
   资金表的美元列平白多一份"汇率本身"的市值；
3. **三个 ``f_convert_*`` 起步是 ``None``** —— PHP 的 ``var $fConvertCNY;``
   未赋值，"没折算过"与"折算出来是 0"必须能区分；
4. **``record <= 0`` 的记录不进表** —— 分组里预留了标的但没买过的人，
   不该在资金表里看到那一行。
"""

import unittest

from core.stockgroup import (
    FX_SUSDCNH_SYMBOL,
    MultiCurrency,
    MyStockGroup,
    StockGroup,
    _php_truthy,
)
from core.stocktrans import MyStockTransaction


class FakeRef:
    """引用对象替身：能指定币种判定与价格。

    ``future_cn`` 是国内期货（``IsSinaFutureCN``），与 A 股同桶。
    """

    def __init__(self, symbol='SH513100', price='1.0',
                 is_a=False, is_h=False, future_cn=False):
        self._symbol = symbol
        self._price = price
        self._is_a = is_a
        self._is_h = is_h
        self._future_cn = future_cn

    def get_symbol(self):
        return self._symbol

    def get_price(self):
        return self._price

    def is_symbol_a(self):
        return self._is_a

    def is_symbol_h(self):
        return self._is_h

    def is_sina_future_cn(self):
        return self._future_cn


def _trans(symbol, shares, cost, price='1.0', **kwargs):
    """造一笔已成交的交易：``shares`` 股、总成本 ``cost``、现价 ``price``。"""
    trans = MyStockTransaction(FakeRef(symbol, price, **kwargs), 'g1')
    trans.set_value(1, shares, cost)
    return trans


class PhpTruthyTest(unittest.TestCase):
    """``if ($strUSDCNY)`` 的 PHP 语义（汇率取不到时是空串）。"""

    def test_string_zero_is_false(self):
        """PHP 里 ``'0'`` 是假值 —— Python 的非空字符串都是真值。"""
        self.assertFalse(_php_truthy('0'))
        self.assertFalse(_php_truthy(''))
        self.assertFalse(_php_truthy(False))
        self.assertFalse(_php_truthy(None))

    def test_rate_string_is_true(self):
        self.assertTrue(_php_truthy('7.100'))
        self.assertTrue(_php_truthy(7.1))

    def test_zero_number_is_false(self):
        self.assertFalse(_php_truthy(0))
        self.assertFalse(_php_truthy(0.0))


class MultiCurrencyInitTest(unittest.TestCase):
    """构造只初始化三个原币桶。"""

    def test_original_buckets_start_at_zero(self):
        multi = MultiCurrency()
        self.assertEqual((multi.f_cny, multi.f_hkd, multi.f_usd), (0.0, 0.0, 0.0))

    def test_convert_buckets_start_at_none(self):
        """PHP 的 ``var $fConvertCNY;`` 未赋值 —— 照抄成 ``None``。"""
        multi = MultiCurrency()
        self.assertIsNone(multi.f_convert_cny)
        self.assertIsNone(multi.f_convert_usd)
        self.assertIsNone(multi.f_convert_hkd)


class MultiCurrencyConvertTest(unittest.TestCase):
    """``Convert()``：三桶折成"折人民币"总数，再反除回各币种。"""

    def _multi(self):
        multi = MultiCurrency()
        multi.f_cny = 100.0
        multi.f_usd = 10.0
        multi.f_hkd = 20.0
        return multi

    def test_both_rates(self):
        multi = self._multi()
        multi.convert('7.1', '0.9')
        # 折人民币 = 100 + 10*7.1 + 20*0.9 = 189
        self.assertAlmostEqual(multi.f_convert_cny, 189.0)
        # 折美元/折港币都拿**折人民币总数**反除，不是本币
        self.assertAlmostEqual(multi.f_convert_usd, 189.0 / 7.1)
        self.assertAlmostEqual(multi.f_convert_hkd, 189.0 / 0.9)

    def test_no_rates_gives_zero_not_none(self):
        """没汇率时**直接给 0.0** —— 不是留着 None，也不是除零。"""
        multi = self._multi()
        multi.convert()
        self.assertAlmostEqual(multi.f_convert_cny, 100.0)   # 只有人民币桶
        self.assertEqual(multi.f_convert_usd, 0.0)
        self.assertEqual(multi.f_convert_hkd, 0.0)

    def test_false_rate_is_treated_as_missing(self):
        """``if ($strUSDCNY)`` 为假时不加、也不除。"""
        multi = self._multi()
        multi.convert(False, False)
        self.assertAlmostEqual(multi.f_convert_cny, 100.0)
        self.assertEqual(multi.f_convert_usd, 0.0)

    def test_zero_string_rate_is_false(self):
        """``'0'`` 在 PHP 里是假值 —— 用 ``if (rate)`` 会漏，必须走真假判断。"""
        multi = self._multi()
        multi.convert('0', '0')
        self.assertAlmostEqual(multi.f_convert_cny, 100.0)
        self.assertEqual(multi.f_convert_usd, 0.0)

    def test_only_usd_rate(self):
        multi = self._multi()
        multi.convert('7.1', False)
        # 港币桶加了也不折：189 里没有 HKD 那 18
        self.assertAlmostEqual(multi.f_convert_cny, 100.0 + 10.0 * 7.1)
        self.assertEqual(multi.f_convert_hkd, 0.0)

    def test_convert_is_idempotent(self):
        """``Convert()`` 每次都从原始桶重算 —— 连调两次结果不变。

        这一点很重要：``_EchoMoneyGroupData()`` 对每个分组各调一次，
        若折算结果会累积，第二次调用起数字就翻倍了。
        """
        multi = self._multi()
        multi.convert('7.1', '0.9')
        first = multi.f_convert_cny
        multi.convert('7.1', '0.9')
        self.assertAlmostEqual(multi.f_convert_cny, first)


class StockGroupBucketTest(unittest.TestCase):
    """``OnStockTransaction()`` 的分桶：CNY / HKD / USD 三选一。"""

    def _bucket(self, symbol, **kwargs):
        group = StockGroup()
        group.on_stock_transaction(_trans(symbol, 100, 900.0, '10.0', **kwargs))
        return (group.multi_amount.f_cny, group.multi_amount.f_hkd,
                group.multi_amount.f_usd)

    def test_symbol_a_goes_to_cny(self):
        self.assertEqual(self._bucket('SH600104', is_a=True), (1000.0, 0.0, 0.0))

    def test_sina_future_cn_goes_to_cny(self):
        self.assertEqual(self._bucket('hf_ES', future_cn=True), (1000.0, 0.0, 0.0))

    def test_fx_susdcnh_goes_to_cny(self):
        """这是最容易搞错的一支 —— 它看着像美元，其实按人民币记。"""
        self.assertEqual(self._bucket(FX_SUSDCNH_SYMBOL), (1000.0, 0.0, 0.0))

    def test_symbol_h_goes_to_hkd(self):
        self.assertEqual(self._bucket('SH600104', is_h=True), (0.0, 1000.0, 0.0))

    def test_us_symbol_goes_to_usd(self):
        self.assertEqual(self._bucket('KWEB'), (0.0, 0.0, 1000.0))

    def test_profit_is_accumulated_separately(self):
        """市值与盈亏走**两个**累加器 —— 汇率不该被算成盈亏。"""
        group = StockGroup()
        group.on_stock_transaction(_trans('KWEB', 100, 900.0, '10.0'))
        self.assertAlmostEqual(group.multi_amount.f_usd, 1000.0)
        self.assertAlmostEqual(group.multi_profit.f_usd, 100.0)
        self.assertAlmostEqual(group.multi_profit.f_cny, 0.0)

    def test_multiple_transactions_accumulate(self):
        group = StockGroup()
        group.on_stock_transaction(_trans('KWEB', 100, 900.0, '10.0'))
        group.on_stock_transaction(_trans('SPY', 10, 4500.0, '470.0'))
        self.assertAlmostEqual(group.multi_amount.f_usd, 1000.0 + 4700.0)


class ConvertCurrencyTest(unittest.TestCase):
    """``ConvertCurrency()``：市值与盈亏各折一次。"""

    def test_both_accumulators_are_converted(self):
        group = StockGroup()
        group.on_stock_transaction(_trans('KWEB', 100, 900.0, '10.0'))
        group.convert_currency('7.1', '0.9')
        self.assertAlmostEqual(group.multi_amount.f_convert_cny, 1000.0 * 7.1)
        self.assertAlmostEqual(group.multi_profit.f_convert_cny, 100.0 * 7.1)


class MyStockGroupTest(unittest.TestCase):
    """``MyStockGroup``：交易集合、``SetValue``、查库循环的外提。"""

    def test_refs_become_empty_transactions(self):
        group = MyStockGroup('g1', [FakeRef('KWEB'), FakeRef('SPY')])
        self.assertEqual(len(group.get_stock_transaction_array()), 2)
        self.assertEqual(group.get_total_records(), 0)

    def test_set_value_fills_and_buckets(self):
        group = MyStockGroup('g1', [FakeRef('KWEB', '10.0')])
        group.set_value('KWEB', 3, 100, 900.0)
        trans = group.get_stock_transaction_by_symbol('KWEB')
        self.assertEqual(trans.get_total_records(), 3)
        self.assertEqual(trans.get_total_shares(), 100)
        self.assertAlmostEqual(group.multi_amount.f_usd, 1000.0)

    def test_set_value_unknown_symbol_is_skipped_without_ref_builder(self):
        """``_checkSymbol()`` 要现建引用对象；没给 ``ref_builder`` 就跳过。

        与 PHP 建不出引用对象时的表现一致：那笔交易不出现在表里。
        """
        group = MyStockGroup('g1')
        group.set_value('KWEB', 1, 100, 900.0)
        self.assertIsNone(group.get_stock_transaction_by_symbol('KWEB'))
        self.assertEqual(group.get_total_records(), 0)

    def test_set_value_unknown_symbol_uses_ref_builder(self):
        group = MyStockGroup('g1', ref_builder=lambda symbol: FakeRef(symbol, '10.0'))
        group.set_value('KWEB', 1, 100, 900.0)
        self.assertIsNotNone(group.get_stock_transaction_by_symbol('KWEB'))

    def test_records_are_loaded(self):
        records = [
            {'stock_id': '11', 'record': '2', 'quantity': '100', 'cost': '900.0'},
            {'stock_id': '12', 'record': '1', 'quantity': '50', 'cost': '400.0'},
        ]
        group = MyStockGroup('g1',
                             [FakeRef('KWEB', '10.0'), FakeRef('SPY', '470.0')],
                             records=records,
                             symbol_of=lambda stock_id: ('KWEB' if stock_id == '11'
                                                         else 'SPY'))
        self.assertEqual(group.get_total_records(), 3)
        self.assertAlmostEqual(group.multi_amount.f_usd, 1000.0 + 50 * 470.0)

    def test_record_zero_is_skipped(self):
        """``intval($record['record']) > 0`` —— 预留了但没买过的不出现。"""
        records = [{'stock_id': '11', 'record': '0', 'quantity': '100', 'cost': '900.0'}]
        group = MyStockGroup('g1', [FakeRef('KWEB', '10.0')], records=records,
                             symbol_of=lambda stock_id: 'KWEB')
        self.assertEqual(group.get_total_records(), 0)
        self.assertAlmostEqual(group.multi_amount.f_usd, 0.0)

    def test_negative_record_is_skipped(self):
        records = [{'stock_id': '11', 'record': '-1', 'quantity': '100', 'cost': '900.0'}]
        group = MyStockGroup('g1', [FakeRef('KWEB', '10.0')], records=records,
                             symbol_of=lambda stock_id: 'KWEB')
        self.assertEqual(group.get_total_records(), 0)

    def test_missing_record_field_defaults_to_zero(self):
        records = [{'stock_id': '11', 'quantity': '100', 'cost': '900.0'}]
        group = MyStockGroup('g1', [FakeRef('KWEB', '10.0')], records=records,
                             symbol_of=lambda stock_id: 'KWEB')
        self.assertEqual(group.get_total_records(), 0)

    def test_group_id_defaults_to_false(self):
        """``var $strGroupId = false`` —— "全部"分组没有 id。"""
        self.assertFalse(MyStockGroup().get_group_id())

    def test_get_stock_transaction_by_symbol_miss(self):
        group = MyStockGroup('g1', [FakeRef('KWEB')])
        self.assertIsNone(group.get_stock_transaction_by_symbol('SPY'))


class CamelCaseAliasTest(unittest.TestCase):
    """PHP 原名别名（见 AGENTS.md：方法保留 camelCase 别名）。"""

    def test_method_aliases_exist(self):
        self.assertTrue(hasattr(MultiCurrency, 'Convert'))
        for name in ('GetGroupId', 'OnStockTransaction', 'ConvertCurrency',
                     'SetValue', 'GetTotalRecords',
                     'GetStockTransactionArray', 'GetStockTransactionBySymbol'):
            self.assertTrue(hasattr(MyStockGroup, name), name)

    def test_alias_calls_same_function(self):
        group = MyStockGroup('g1', [FakeRef('KWEB', '10.0')])
        group.SetValue('KWEB', 1, 100, 900.0)
        self.assertEqual(group.GetTotalRecords(), 1)


if __name__ == "__main__":
    unittest.main()
