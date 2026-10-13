"""``core/stocktrans.py`` 的单元测试（对应 ``php/stocktrans.php``）。

重点盯三件事：

1. **手续费进成本不进数量** —— 这是 ``AddSqlTransaction()`` 的公式，
   漏掉 fees 会让均价与盈亏同时偏低；
2. **``GetAvgCost()`` 的除零保护** —— 空仓时必须回 0.0；
3. **``GetValue()`` 的 ``floatval`` 语义** —— ``GetPrice()`` 返回空串时
   PHP 得 0.0，Python 的 ``float('')`` 会抛异常。
"""

import unittest

from core.stocktrans import (
    MyStockTransaction,
    StockTransaction,
    add_sql_transaction,
    get_sql_transaction_date,
    number_display_cell,
)


class FakeRef:
    """``MyStockReference`` 的最小替身。

    只留本测试要用到的几个取值方法，且**同时提供 snake_case 与 camelCase**
    两种名字，用来验证 :func:`core.stocktrans._invoke` 的优先顺序。
    """

    def __init__(self, symbol='SH513100', price='1.234', stock_id='100'):
        self._symbol = symbol
        self._price = price
        self._stock_id = stock_id
        self.display_calls = []

    def get_symbol(self):
        return self._symbol

    def GetSymbol(self):
        return self._symbol

    def get_price(self):
        return self._price

    def GetPrice(self):
        return self._price

    def get_stock_id(self):
        return self._stock_id

    def get_price_display(self, disp=None, prev=None, precision=None):
        self.display_calls.append((disp, prev, precision))
        return 'disp(%s)' % disp

    def GetPriceDisplay(self, disp=None, prev=None, precision=None):
        return self.display_calls.append((disp, prev, precision))

    def is_symbol_a(self):
        return True

    def IsSymbolA(self):
        return True

    def is_symbol_h(self):
        return False

    def IsSymbolH(self):
        return False


class StockTransactionTest(unittest.TestCase):
    """基类：三个累加量与 ``Add`` / ``AddTransaction``。"""

    def test_constructor_defaults_to_zero(self):
        trans = StockTransaction()
        self.assertEqual(trans.total_shares, 0)
        self.assertEqual(trans.total_cost, 0.0)
        self.assertEqual(trans.total_records, 0)

    def test_add_transaction_accumulates(self):
        trans = StockTransaction()
        trans.AddTransaction(100, 123.4)
        trans.AddTransaction(-50, -61.7)
        self.assertEqual(trans.total_shares, 50)
        self.assertAlmostEqual(trans.total_cost, 61.7)
        self.assertEqual(trans.total_records, 2)

    def test_add_merges_totals_not_records(self):
        """``Add()`` 只并合计量，``total_records`` 只加 1。"""
        trans = StockTransaction()
        other = StockTransaction()
        other.AddTransaction(100, 123.4)
        other.AddTransaction(200, 246.8)
        trans.Add(other)
        self.assertEqual(trans.total_shares, 300)
        self.assertAlmostEqual(trans.total_cost, 370.2)
        self.assertEqual(trans.total_records, 1)

    def test_add_transaction_coerces_string_shares(self):
        """PHP 的 ``intval($record['quantity'])``：字符串数量要取整。"""
        trans = StockTransaction()
        trans.AddTransaction('100', '123.4')
        self.assertEqual(trans.total_shares, 100)
        self.assertIsInstance(trans.total_shares, int)

    def test_avg_cost_includes_fees(self):
        """均价 = (数量×价 + 手续费) / 数量，比成交价高一丁点。"""
        trans = StockTransaction()
        add_sql_transaction(trans, {'quantity': '100', 'price': '1.234', 'fees': '5.0'})
        add_sql_transaction(trans, {'quantity': '200', 'price': '1.5', 'fees': '3.0'})
        self.assertEqual(trans.total_shares, 300)
        self.assertAlmostEqual(trans.total_cost, 431.4)
        self.assertAlmostEqual(trans.GetAvgCost(), 1.438)
        self.assertEqual(trans.total_records, 2)

    def test_avg_cost_zero_when_flat(self):
        """空仓（全部卖出）时 ``iTotalShares == 0``，不能除零。"""
        trans = StockTransaction()
        trans.AddTransaction(100, 123.4)
        trans.AddTransaction(-100, -123.4)
        self.assertEqual(trans.total_shares, 0)
        self.assertEqual(trans.GetAvgCost(), 0.0)

    def test_set_value_resets(self):
        trans = StockTransaction()
        trans.AddTransaction(100, 123.4)
        trans.SetValue(3, 7, 8.5)
        self.assertEqual((trans.total_records, trans.total_shares, trans.total_cost),
                         (3, 7, 8.5))


class MyStockTransactionTest(unittest.TestCase):
    """子类：挂上 ref 之后的市值 / 盈亏 / 显示。"""

    def test_symbol_false_without_ref(self):
        trans = MyStockTransaction(None)
        self.assertIs(trans.GetSymbol(), False)

    def test_value_is_shares_times_price(self):
        trans = MyStockTransaction(FakeRef(price='1.234'))
        trans.AddTransaction(100, 123.4)
        self.assertAlmostEqual(trans.GetValue(), 123.4)

    def test_value_with_empty_price_is_zero(self):
        """``GetPrice()`` 返回空串时走 ``floatval('') == 0.0``，不抛异常。"""
        trans = MyStockTransaction(FakeRef(price=''))
        trans.AddTransaction(100, 123.4)
        self.assertEqual(trans.GetValue(), 0.0)

    def test_value_negative_for_short(self):
        """做空时数量为负，市值与盈亏跟着变负。"""
        trans = MyStockTransaction(FakeRef(price='2.0'))
        trans.AddTransaction(-100, -200.0)
        self.assertAlmostEqual(trans.GetValue(), -200.0)

    def test_profit_subtracts_cost(self):
        trans = MyStockTransaction(FakeRef(price='1.5'))
        add_sql_transaction(trans, {'quantity': '100', 'price': '1.234', 'fees': '5.0'})
        # 市值 150 - 成本 128.4
        self.assertAlmostEqual(trans.GetProfit(), 21.6)

    def test_avg_cost_display_passes_avg_only(self):
        """只传一个参数，让 PHP 的 ``$fPrev`` 落到当前价上。"""
        ref = FakeRef(price='1.5')
        trans = MyStockTransaction(ref)
        add_sql_transaction(trans, {'quantity': '100', 'price': '1.234', 'fees': '5.0'})
        self.assertEqual(trans.GetAvgCostDisplay(), 'disp(1.284)')
        self.assertEqual(ref.display_calls, [(1.284, None, None)])

    def test_avg_cost_display_empty_without_ref(self):
        self.assertEqual(MyStockTransaction(None).GetAvgCostDisplay(), '')

    def test_symbol_classification(self):
        self.assertTrue(MyStockTransaction(FakeRef()).IsSymbolA())
        self.assertFalse(MyStockTransaction(FakeRef()).IsSymbolH())


class AddSqlTransactionTest(unittest.TestCase):
    """``AddSqlTransaction()``：记录字典的三个字段。"""

    def test_missing_fields_treated_as_zero(self):
        trans = StockTransaction()
        add_sql_transaction(trans, {})
        self.assertEqual(trans.total_shares, 0)
        self.assertEqual(trans.total_cost, 0.0)
        self.assertEqual(trans.total_records, 1)

    def test_fractional_quantity_truncates(self):
        """PHP ``intval()`` 向零取整，不是四舍五入。"""
        trans = StockTransaction()
        add_sql_transaction(trans, {'quantity': '100.9', 'price': '1.0', 'fees': '0'})
        self.assertEqual(trans.total_shares, 100)


class GetSqlTransactionDateTest(unittest.TestCase):
    """``GetSqlTransactionDate()``：取 ``filled`` 里第一个空格前的部分。"""

    def test_splits_on_first_space(self):
        self.assertEqual(
            get_sql_transaction_date({'filled': '2026-10-13 09:30:00'}), '2026-10-13')

    def test_no_space_returns_none(self):
        """PHP 的 ``strstr`` 找不到空格回 false，按 AGENTS.md 映射为 None。"""
        self.assertIsNone(get_sql_transaction_date({'filled': '2026-10-13'}))

    def test_missing_filled_returns_none(self):
        self.assertIsNone(get_sql_transaction_date({}))
        self.assertIsNone(get_sql_transaction_date({'filled': ''}))

    def test_leading_space_returns_empty_string(self):
        """第一个空格就在开头：PHP 返回**真空串**（不是 false），必须与上面区分。

        ``' 2026-10-13 …'`` 的第一个空格之前是空串 —— 这是"日期为空"，
        而上面那些是"没有日期"。PHP 用 false vs '' 区分，端口用 None vs ''。
        """
        self.assertEqual(
            get_sql_transaction_date({'filled': ' 2026-10-13 09:30:00'}), '')


class NumberDisplayCellTest(unittest.TestCase):
    """``GetNumberDisplay()`` 的单元格形式与涨红跌绿约定。"""

    def test_positive_is_red(self):
        self.assertEqual(number_display_cell(12.5), ('12.5', 'red'))

    def test_negative_is_green(self):
        self.assertEqual(number_display_cell(-12.5), ('-12.5', 'green'))

    def test_zero_is_grey(self):
        self.assertEqual(number_display_cell(0.0), ('0', 'grey'))

    def test_trailing_zeros_trimmed(self):
        self.assertEqual(number_display_cell(12.500), ('12.5', 'red'))

    def test_empty_value_empty_cell(self):
        self.assertEqual(number_display_cell(None), ('', ''))
