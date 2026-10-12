"""五档交易表那一列「换算价」的测试（`web/services/qdii_trading_column.py`）。

对应 ``woody/res/php/_qdiigroup.php:5`` 的 ``_tradingUserDefined()``。
**全部离线**：基金引用对象与杠杆标的都由假对象顶替，不打网络、不需要数据库、
不需要 flask。

钉的是五件事：

1. **一个 callable 干两件事** —— 不传参给表头、传价格给格子；
2. **表头形状** —— ``^GSPC|SH|SPY价格``，**``价格`` 前面没有分隔符**；
3. **PHP 的 ``'0'`` 陷阱** —— ``if ($strVal)`` 里 ``'0'`` 是假值，
    Python 的 ``if price:`` 会判错，必须体现在测试里；
4. **换算顺序** —— 先 ``GetEstValue`` 再逐个 ``EstFromPair``，
    中间层那格在最前面；
5. **``EstFromPair`` 不能自己实现第二份** —— 必须调到 :mod:`pairestimate`。
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

from calc import pairestimate  # noqa: E402
from web.services.qdii_trading_column import (  # noqa: E402
    LEVERAGE_SEPARATOR,
    PRICE_COLUMN_TEXT,
    build_leverage_references,
    build_trading_column,
    column_header,
    estimate_cell,
)


class FakeRef:
    """标的引用对象替身：只要 ``get_display`` / ``get_price_display``。

    ``get_price_display`` 的行为照 ``stockref.StockReference``：只传一个参数时
    返回格式化后的文本，第二个参数（对比基准）缺省。这里用 ``%.3f`` 固定格式，
    是为了让断言能写出精确字符串。
    """

    def __init__(self, symbol="^GSPC", display=None, price="8000.00",
                 precision=3):
        self._symbol = symbol
        self._display = symbol if display is None else display
        self._price = price
        self._precision = precision

    def get_symbol(self):
        return self._symbol

    def get_display(self):
        return self._display

    def get_price_display(self, value=None, prev=None):
        try:
            return "%.*f" % (self._precision, float(value))
        except (TypeError, ValueError):
            return ""


class FakeFund:
    """基金引用对象替身：``GetEstRef`` / ``GetEstValue``。

    ``GetEstValue`` 是 ``GetQdiiValue`` 的逆，本处不给真公式 ——
    按一个可读的换算系数折起来即可，测试要钉的是"值有没有被传下去"。
    """

    def __init__(self, est_ref=None, factor=2459.0):
        self._est_ref = FakeRef() if est_ref is None else est_ref
        self._factor = factor

    def get_est_ref(self):
        return self._est_ref

    def get_est_value(self, price=False):
        try:
            return float(price) * self._factor
        except (TypeError, ValueError):
            return 0.0


class FakeLeverageRef(FakeRef):
    """杠杆标的替身：自带 ``EstFromPair`` 需要的 factor / position。

    **刻意不实现** ``get_est_value`` —— 换算只走 ``pairestimate.est_from_pair``，
    没有第二个入口。``factor`` 取自样例配对关系：``SPY`` ≈ ``^GSPC`` / 10。
    """

    def __init__(self, symbol="SPY", factor=10.03, position=1.0, **kwargs):
        super().__init__(symbol=symbol, **kwargs)
        self.factor = factor
        self.ratio = position

    def get_position(self):
        return self.ratio


def make_column(leverage=(), fund=None):
    """建一个回调；``leverage`` 给符号列表时就生成对应数量的杠杆标的。"""
    refs = [FakeLeverageRef(symbol=symbol) for symbol in leverage]
    return build_trading_column(fund or FakeFund(), refs), refs


class TwoCallTypesTest(unittest.TestCase):
    """PHP 的 ``call_user_func($callback)`` 与 ``call_user_func($callback, $str)``。"""

    def setUp(self):
        self.column, _ = make_column(leverage=("SPY",))

    def test_no_argument_gives_the_header(self):
        self.assertEqual(self.column(), "^GSPC|SPY价格")

    def test_with_a_price_gives_the_cell(self):
        cell = self.column("3.318")

        self.assertNotIn(PRICE_COLUMN_TEXT, cell)
        self.assertEqual(len(cell.split(LEVERAGE_SEPARATOR)), 2)

    def test_the_callable_is_named_after_the_php_function(self):
        """``tracing`` 时一眼能认出是哪个 PHP 函数。"""
        self.assertEqual(self.column.__name__, "_tradingUserDefined")

    def test_php_alias_exists(self):
        """按 AGENTS.md 第 3 条保留 camelCase 别名。"""
        from web.services.qdii_trading_column import tradingUserDefined

        self.assertIs(tradingUserDefined, build_trading_column)


class HeaderShapeTest(unittest.TestCase):
    """表头：``TableColumnGetStock(est) . '|'.lev … . TableColumnGetPrice()``。"""

    def test_without_leverage_it_is_just_the_est_symbol(self):
        self.assertEqual(column_header(FakeRef()), "^GSPC价格")

    def test_one_leverage_gets_one_separator(self):
        self.assertEqual(
            column_header(FakeRef(), [FakeRef(symbol="SPY")]), "^GSPC|SPY价格")

    def test_three_parts_match_the_production_page(self):
        """生产端 ``sz161125cn.php`` 实测：``^GSPC|SH|SPY价格``。"""
        refs = [FakeRef(symbol="SH"), FakeRef(symbol="SPY")]
        self.assertEqual(column_header(FakeRef(), refs), "^GSPC|SH|SPY价格")

    def test_no_separator_before_the_price_word(self):
        """``价格`` 是紧跟着写的 —— 顺手补成 ``…|SPY|价格`` 就与生产端不符了。"""
        self.assertNotIn("|" + PRICE_COLUMN_TEXT,
                         column_header(FakeRef(), [FakeRef(symbol="SPY")]))

    def test_display_name_is_used_not_the_raw_symbol(self):
        """``GetDisplay()`` 而不是 ``GetSymbol()`` —— 期货/外汇会显示另一套字。

        PHP 那边是 ``$sym->GetDisplay()``（``stocktable.php:256``）。
        """
        ref = FakeRef(symbol="hf_NQ", display="纳指100期货")
        self.assertEqual(column_header(ref), "纳指100期货价格")

    def test_missing_display_falls_back_to_the_symbol(self):
        ref = FakeRef(symbol="^NDX")
        ref.get_display = lambda: ""
        self.assertEqual(column_header(ref), "^NDX价格")


class CellShapeTest(unittest.TestCase):
    """格子：``GetEstValue(price)`` → 中间层一格 + 每个杠杆标的各一格。"""

    def test_est_value_comes_from_the_fund(self):
        column, _ = make_column(leverage=())
        self.assertEqual(column("3.318"), "8158.962")

    def test_leverage_columns_are_appended_in_order(self):
        """格子顺序跟着传入顺序走 —— 换一下两个标的的因子就能看出来。"""
        refs = [FakeLeverageRef(symbol="SH", factor=1.0),
                FakeLeverageRef(symbol="SPY", factor=10.03)]
        cell = build_trading_column(FakeFund(), refs)("3.318")

        # SH 的因子是 1（不换算），SPY 是 10.03（缩放一位）
        self.assertEqual(cell, "8158.962|8158.962|813.456")

    def test_est_from_pair_is_the_only_formula(self):
        """顶掉本模块**拿到的那个名字**，结果必须跟着变 ——
        证明这一列不复述公式，而是把活交给 :mod:`pairestimate`。
        """
        column, _ = make_column(leverage=("SPY",))
        module = sys.modules["web.services.qdii_trading_column"]
        original = module.est_from_pair
        module.est_from_pair = lambda ref, value=None, cny=False: 42.0
        try:
            cell = column("3.318")
        finally:
            module.est_from_pair = original

        self.assertEqual(cell, "8158.962|42.000")

    def test_the_leverage_value_follows_the_pair_factor(self):
        """非 A 股走 ``(价 / 汇) / 因子``：``8158.962 / 10.03`` ≈ ``813.456``。

        真对这个数是必要的 —— 换成"差不多就行"的断言，万一有人把
        ``EstFromPair`` 换成本地实现也测不出来。
        """
        column, _ = make_column(leverage=("SPY",))
        self.assertEqual(column("3.318"), "8158.962|813.456")

    def test_each_part_uses_its_own_precision(self):
        """中间层 3 位、杠杆标的 2 位 —— 各自 ``GetPriceDisplay``。"""
        lev = FakeLeverageRef(symbol="SPY", precision=2)
        cell = build_trading_column(FakeFund(), [lev])("3.318")

        self.assertEqual(cell, "8158.962|813.46")

    def test_cell_has_no_html_tags(self):
        """**有意偏离 PHP**：PHP 在这里产出 ``<font color=red>…</font>``，
        本版只给纯文本（理由见模块 docstring「已知偏差」）。
        """
        cell = make_column(leverage=("SPY",))[0]("3.318")

        self.assertNotIn("<", cell)


class ZeroPriceTest(unittest.TestCase):
    """PHP ``'0'`` 是假值 —— 这条测的是最容易翻车的那个分支。"""

    def setUp(self):
        self.column, _ = make_column(leverage=("SPY",))

    def test_string_zero_asks_for_the_header_not_a_cell(self):
        """``if ($strVal)`` 对 ``'0'`` 为假 → 落到表头分支。

        如果写成 ``if price:``，Python 会判真、返回一格价格 —— 页面上就是
        "某一行的表头位置出现一个数字"。
        """
        self.assertEqual(self.column("0"), "^GSPC|SPY价格")

    def test_empty_string_asks_for_the_header(self):
        self.assertEqual(self.column(""), "^GSPC|SPY价格")

    def test_none_and_false_ask_for_the_header(self):
        self.assertEqual(self.column(None), "^GSPC|SPY价格")
        self.assertEqual(self.column(False), "^GSPC|SPY价格")

    def test_zero_point_zero_still_returns_a_cell(self):
        """``'0.0'`` 在 PHP 里是真值（不等于 ``'0'``），这里同样当成价格处理。"""
        cell = self.column("0.0")

        self.assertNotIn(PRICE_COLUMN_TEXT, cell)


class NoEstRefTest(unittest.TestCase):
    """没有中间层标的就**不补这一列**，而不是补一列空数据。"""

    def test_fund_without_est_ref_gives_no_callback(self):
        class Plain:
            def get_symbol(self):
                return "SZ162411"

        self.assertIsNone(build_trading_column(Plain()))

    def test_none_est_ref_gives_no_callback(self):
        fund = FakeFund()
        fund.get_est_ref = lambda: None
        self.assertIsNone(build_trading_column(fund))

    def test_empty_leverage_list_is_still_usable(self):
        """``fundpair`` 里查不到记录是常态 —— 那一列只有中间层标的那一格。"""
        column, _ = make_column(leverage=())
        self.assertEqual(column(), "^GSPC价格")
        self.assertEqual(column("3.318"), "8158.962")


class LeverageReferenceBuildTest(unittest.TestCase):
    """``build_leverage_references``：``_qdiigroup.php:69-82`` 的 ``$arLev``。"""

    def test_no_est_symbol_means_no_references(self):
        self.assertEqual(build_leverage_references("", object()), [])

    def test_no_source_means_no_references(self):
        self.assertEqual(build_leverage_references("^GSPC", None), [])

    def test_no_pair_rows_means_no_references(self):
        self.assertEqual(build_leverage_references("^GSPC", _FakeSource({})), [])


class PairCalibrationTest(unittest.TestCase):
    """``MyPairReference::LoadCalibration()`` 的最小等价物。

    验证点：**因子与混合基准怎么落到 ref 上**，因为 ``EstFromPair`` 只读这两个
    属性，缺了不会报错、只会算错。
    """

    def setUp(self):
        self.source = _FakeSource({"SPY": 5}, calibration=_Record(10.03, "2026-10-07"))

    def test_factor_comes_from_the_calibration_record(self):
        refs = build_leverage_references("^GSPC", self.source)

        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].factor, 10.03)

    def test_last_calibration_prefers_the_lastcalibration_table(self):
        self.source.last_calibration = 9.87
        refs = build_leverage_references("^GSPC", self.source)

        self.assertEqual(refs[0].last_calibration_val, 9.87)

    def test_missing_last_calibration_falls_back_to_the_netvalue_of_that_date(self):
        """PHP 的 ``SqlGetNetValueByDate($stock_id, $record['date'])``。"""
        self.source.last_calibration = None
        self.source.netvalue = 812.89
        refs = build_leverage_references("^GSPC", self.source)

        self.assertEqual(refs[0].last_calibration_val, 812.89)

    def test_without_a_calibration_record_the_factor_stays_untouched(self):
        """没有校准记录时交给 ``pairestimate`` 退化（``1 / position``），
        本模块**不替它做决定** —— 那样会变成两份实现。
        """
        self.source.calibration = None
        refs = build_leverage_references("^GSPC", self.source)

        self.assertEqual(refs[0].factor, 1.0)  # MysqlReference 的默认值


class _Record:
    """``stockdata.DailyRecord`` 的同形状替身（只要 ``close`` / ``date``）。"""

    def __init__(self, close, date):
        self.close = close
        self.date = date


class _FakeSource:
    """数据源替身：只实现本模块用到的四个方法加一条 ``fundpair`` 查询。

    ``_fundpair_query`` 认的是 ``source.connection.cursor()``，
    所以这里连游标一起替掉 —— 不连数据库就跑完这条路径。
    """

    class _Cursor:
        def __init__(self, rows):
            self._rows = rows

        def execute(self, sql, params=()):
            self._rows = [row for row in self._rows]

        def fetchall(self):
            return list(self._rows)

        def close(self):
            pass

    class _Connection:
        def __init__(self, rows):
            self._rows = rows

        def cursor(self):
            return _FakeSource._Cursor(self._rows)

    def __init__(self, pairs, calibration=None):
        rows = [{"symbol": symbol} for symbol in pairs]
        self._pairs = pairs
        self.connection = _FakeSource._Connection(rows)
        self.calibration = calibration
        self.last_calibration = None
        self.netvalue = None

    # -- refobj / reference_service 会用到的 --------------------------------
    def get_stock_id(self, symbol):
        return self._pairs.get(symbol)

    def get_position(self, stock_id):
        return 1.0

    def get_calibration_now(self, stock_id):
        return self.calibration

    def get_last_calibration(self, stock_id):
        return self.last_calibration

    def get_netvalue_close(self, stock_id, date):
        return self.netvalue


if __name__ == "__main__":
    unittest.main()
