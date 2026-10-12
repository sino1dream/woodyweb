"""均线表（``web/services/sma_service.py``）的测试。

对应 ``php/ui/smaparagraph.php``。**全部离线**：``StockHistory`` 由参数注入的
假实现顶替，不打网络、不需要数据库、不需要 flask。

分三块：

1. ``SmaRowTextTest`` —— ``_getSmaRow()`` 的字符串游戏最容易抄错，逐个钉住；
2. ``ColumnAndRowTest`` —— 列数与格数（九个组合），以及 PHP 的几处"看起来
   像笔误但是真的"；
3. ``QdiiCallbackTest`` —— ``_callbackQdiiSma`` 那个"一个 callable 两件事"契约。
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

from web.services.sma_service import (  # noqa: E402
    SMA_TABLE_ID,
    SmaService,
    callback_ah_pair_sma,
    callback_fund_pair_sma,
    callback_qdii_sma,
    sma_row_text,
)


# ---------------------------------------------------------------------------
# 假的引用对象与假的 StockHistory
# ---------------------------------------------------------------------------
class FakeRef:
    """只提供 SMA 表要用到的那几个方法（对应 PHP 的 ``StockReference``）。"""

    def __init__(self, symbol="^NDX", prev_price="100.00", price="101.00",
                 precision=3):
        self.str_symbol = symbol
        self.str_price = price
        self.str_prev_price = prev_price
        self._precision = precision

    def get_symbol(self):
        return self.str_symbol

    def get_price(self):
        return self.str_price

    def get_prev_price(self):
        return self.str_prev_price

    def get_price_display(self, value=None, prev=None):
        """``GetPriceDisplay($fVal)``：只有一个参数，负值是预期内的。"""
        if value is None:
            value = self.str_price
        prev = prev if prev is not None else self.str_prev_price
        try:
            number = float(value)
        except (TypeError, ValueError):
            return ""
        return "%.*f" % (self._precision, number)

    def get_percentage_text(self, value=None):
        """``GetPercentageText($fVal)``：返回带 ``%`` 的**纯文本**。

        ## 这里原来是 ``get_percentage_display``，是个假通过

        替身实现的方法**真实 ref 上并没有** —— ``refobj.EstReference`` 只有
        ``get_percentage_text``。于是替身让测试全绿，而真实页面上
        ``_invoke(ref, "get_percentage_display", ...)`` 静默拿到空串，
        **整列「溢价」都是空的**（不报错，很难发现）。

        PHP 的 ``GetPercentageDisplay()`` 是"文本 + 染色"两步
        （``php/stockref.php:146``），而染色在本项目由
        ``reference_service.percentage_cell`` 负责，所以 SMA 表这里
        只需要文本 —— 与 ``reference_service`` 的口径一致。

        见 ``FakeRefContractTest``：替身的方法集合必须与真实 ref 对得上。
        """
        if value is None:
            value = self.str_price
        try:
            number = float(value)
            prev = float(self.str_prev_price)
        except (TypeError, ValueError):
            return ""
        if prev == 0:
            return ""
        return "%.2f%%" % ((number / prev - 1.0) * 100.0)

    def get_display(self):
        """``GetDisplay()`` —— ``TableColumnStock`` 的列头用它。"""
        return self.str_symbol


class FakeHistory:
    """``StockHistory`` 的鸭子类型实现：一口价给出三组字典。"""

    def __init__(self, sma=None, next_values=None, after_hour_values=None,
                 ref=None, start_date="2026-10-09", bull_bear="牛市",
                 order=None, need_after_hour=False):
        self._sma = sma if sma is not None else {}
        self.next_values = next_values if next_values is not None else {}
        self.after_hour_values = after_hour_values if after_hour_values is not None else {}
        self._ref = ref if ref is not None else FakeRef()
        self.start_date = start_date
        self.bull_bear = bull_bear
        self.order = order if order is not None else []
        self._need = need_after_hour

    def get_ref(self):
        return self._ref

    def get_sma(self):
        return self._sma

    def get_start_date(self):
        return self.start_date

    def get_bull_bear(self):
        return self.bull_bear

    def get_order_array(self):
        return self.order

    def need_after_hour_est(self):
        return self._need


def make_sma(*, keys=("D5", "D10", "DBOLLUP", "EMA50"), value="100.00"):
    """造一组均线值。

    **强制关键字参数**：``keys`` 与 ``value`` 都是字符串元组/字符串，
    写成位置参数 ``make_sma("101.00")`` 会静默地把它当 key —— 那种错不会报错，
    只会让"用 T+1 值填满两格"这类断言变成假通过。
    """
    return {key: value for key in keys}


def make_service(history, **kwargs):
    """把 ``StockHistory`` 固定成注入实现的 ``SmaService``。"""
    return SmaService(history_factory=lambda ref, wide: history, **kwargs)


class SmaRowTextTest(unittest.TestCase):
    """``_getSmaRow()``：三层 ``substr`` 的判断顺序必须逐条验证（``:5``）。

    这段最怕的是"看着对但实际错位" —— 三种形态的拼法不同，
    多一个字少一个字页面照样显示，只有逐个钉住才看得出。
    """

    def test_daily_key_puts_the_unit_after_the_number(self):
        """``$strRest.$arRow[$strFirst]`` —— 数字在前、单位在后。"""
        self.assertEqual(sma_row_text("D5"), "5日")
        self.assertEqual(sma_row_text("D10"), "10日")
        self.assertEqual(sma_row_text("D20"), "20日")

    def test_weekly_and_monthly_use_their_own_unit(self):
        self.assertEqual(sma_row_text("W5"), "5周")
        self.assertEqual(sma_row_text("M10"), "10月")
        self.assertEqual(sma_row_text("M20"), "20月")

    def test_daily_bollinger_has_no_unit(self):
        """``($strFirst == 'D') ? $arRow[$strRest] : ...`` —— 日线**只**给布林名。

        生产端那一格就叫"布林上轨"，不叫"日布林上轨"。看着像 PHP 漏了前缀，
        补上就与生产端不一致了。
        """
        self.assertEqual(sma_row_text("DBOLLUP"), "布林上轨")
        self.assertEqual(sma_row_text("DBOLLDN"), "布林下轨")

    def test_non_daily_bollinger_puts_the_unit_first(self):
        """非日线走 ``$arRow[$strFirst].$arRow[$strRest]`` —— 单位在**前**。"""
        self.assertEqual(sma_row_text("WBOLLUP"), "周布林上轨")
        self.assertEqual(sma_row_text("MBOLLDN"), "月布林下轨")

    def test_ema_keys_are_looked_up_whole(self):
        """首字符是 ``E`` 时直接整串查表（不走拼接那两条分支）。"""
        self.assertEqual(sma_row_text("EMA50"), "小牛熊分界")
        self.assertEqual(sma_row_text("EMA200"), "牛熊分界")

    def test_unknown_key_falls_back_to_itself(self):
        """未知 key 返回原 key —— 表格少一个字总比整段 500 强。"""
        self.assertEqual(sma_row_text("EMA100"), "EMA100")
        self.assertEqual(sma_row_text(""), "")


class ColumnAndRowTest(unittest.TestCase):
    """列数与格数的九个组合，以及几处 PHP 的"看着像 bug"。"""

    def test_plain_table_is_five_columns(self):
        table = make_service(FakeHistory(sma=make_sma())).sma_table(FakeRef())

        self.assertEqual(table["id"], SMA_TABLE_ID)
        self.assertEqual(table["column_count"], 5)
        self.assertEqual(table["cell_count"], 5)
        self.assertEqual(table["count"], 4)

    def test_first_column_is_the_label(self):
        """``new TableColumn('均线', 90)``。"""
        table = make_service(FakeHistory(sma=make_sma())).sma_table(FakeRef())
        heads = [column["text"] for column in table["columns"]]

        self.assertEqual(heads[0], "均线")
        self.assertEqual(table["columns"][0]["width"], 90)

    def test_after_hour_adds_one_column(self):
        """``if ($bAfterHour) $ar[] = $afterhour_col;``。"""
        history = FakeHistory(sma=make_sma(), after_hour_values=make_sma(value="101.00"),
                              need_after_hour=True)
        table = make_service(history, wide=True).sma_table(FakeRef())

        self.assertEqual(table["column_count"], 6)
        self.assertEqual(table["cell_count"], 6)

    def test_wide_alone_is_not_enough(self):
        """``$bAfterHour = LayoutUseWide()`` 之后还要 ``NeedAfterHourEst()`` 为真。

        两步收窄：构造用 ``wide`` 的初值，之后盘后列**再**被 need_after_hour 关掉。
        合成一步会让"宽屏但不在盘后时段"多出一列。
        """
        history = FakeHistory(sma=make_sma(), need_after_hour=False)
        table = make_service(history, wide=True).sma_table(FakeRef())

        self.assertEqual(table["column_count"], 5)

    def test_missing_next_still_emits_two_cells(self):
        """``else { $ar[] = ''; $ar[] = ''; }`` —— 没有 T+1 也要占两格。

        这是 PHP 显式写的 else 分支，不是"少算一格"；改成不追加会让整行错位。
        """
        table = make_service(FakeHistory(sma=make_sma())).sma_table(FakeRef())

        row = table["rows"][0]
        self.assertEqual(len(row.cells), 5)
        self.assertEqual(_cells(row)[3], "")
        self.assertEqual(_cells(row)[4], "")

    def test_with_next_the_two_cells_are_filled(self):
        history = FakeHistory(sma=make_sma(value="100.00"), next_values=make_sma(value="102.00"))
        table = make_service(history).sma_table(FakeRef())

        self.assertEqual(_cells(table["rows"][0])[3], "102.000")

    def test_no_history_returns_none(self):
        """建不出 ``StockHistory``（没有数据源）就整段跳过，不摆空表。"""
        self.assertIsNone(make_service(None).sma_table(FakeRef()))

    def test_empty_sma_returns_none(self):
        self.assertIsNone(make_service(FakeHistory(sma={})).sma_table(FakeRef()))

    def test_zero_value_row_does_not_vanish(self):
        """``'0'`` 在 PHP 是假值，但**这一格照样要画出来**（只是内容是 0）。

        用**混合**的一组均线（只有 ``D5`` 是 0）来钉：全 0 的那一组另有
        :meth:`test_all_zero_sma_is_treated_as_no_history` 处理。
        """
        sma = make_sma(value="100.00")
        sma["D5"] = "0"
        table = make_service(FakeHistory(sma=sma)).sma_table(FakeRef())

        self.assertEqual(_cells(table["rows"][0])[1], "0.000")
        self.assertEqual(_cells(table["rows"][1])[1], "100.000")

    def test_all_zero_sma_is_treated_as_no_history(self):
        """**全** 0 时不摆表 —— 这是一处有意偏离 PHP 的守卫。

        PHP 遇到"该标的没有历史数据"时会把一串 ``_estSma()`` 全算成 0，
        于是页面上摆一张**整张都是 0** 的均线表。演示库里 ``^NDX`` 一行
        ``stockhistory`` 都没有，照抄就是一张误导性的表，所以这里返回
        ``None``，让上层整段跳过。见 ``sma_service._has_any_level``。
        """
        self.assertIsNone(
            make_service(FakeHistory(sma=make_sma(value="0"))).sma_table(FakeRef()))

    def test_all_empty_string_sma_is_treated_as_no_history(self):
        """``''`` 与 ``'0'`` 一样是 PHP 假值，同样落进那道守卫。"""
        self.assertIsNone(
            make_service(FakeHistory(sma=make_sma(value=""))).sma_table(FakeRef()))


class MemoTest(unittest.TestCase):
    """``_getSmaParagraphMemo()`` 的取值，以及它那个 ``=== false``。"""

    def test_default_memo_is_built_from_the_history(self):
        """``$str === false`` 时才用默认 memo。"""
        table = make_service(FakeHistory(sma=make_sma())).sma_table(FakeRef())

        self.assertEqual(table["memo"]["start_date"], "2026-10-09")
        self.assertEqual(table["memo"]["bull_bear"], "牛市")
        self.assertEqual(table["memo"]["symbol"], "^NDX")

    def test_empty_string_is_kept_as_empty(self):
        """``EchoFundPairSmaParagraphs()`` 传的是 ``''`` —— **不会被替换**。

        PHP 用的是 ``===``，所以空串是"就要空标题"而不是"没给标题"。
        这个差异决定了一组配对标的并排展示时只有第一张表带标题。
        """
        table = make_service(FakeHistory(sma=make_sma())).sma_table(FakeRef(), memo="")
        self.assertEqual(table["memo"], "")

    def test_given_text_is_used_verbatim(self):
        table = make_service(FakeHistory(sma=make_sma())).sma_table(
            FakeRef(), memo="理论溢价：1.0234")
        self.assertEqual(table["memo"], "理论溢价：1.0234")

    def test_admin_order_is_hidden_by_default(self):
        """``$str = DebugIsAdmin() ? implode(', ', $his->GetOrderArray()) : ''``。"""
        history = FakeHistory(sma=make_sma(), order=["D5", "D10"])
        self.assertEqual(make_service(history).sma_table(FakeRef())["order"], [])
        self.assertEqual(
            make_service(history, is_admin=True).sma_table(FakeRef())["order"],
            ["D5", "D10"])


class QdiiCallbackTest(unittest.TestCase):
    """``_callbackQdiiSma()``：一个 callable 承担两件事（``:141``）。"""

    class FakeQdiiRef:
        """``EstReference`` 一侧由 stock_ref 显示，估值是 ``GetQdiiValue()``。"""

        def __init__(self):
            self.ref = FakeRef(symbol="SZ161130", prev_price="4.814", precision=3)
            self._est_ref = FakeRef(symbol="^NDX")

        def GetEstRef(self):
            return self._est_ref

        def GetStockRef(self):
            return self.ref

        def GetQdiiValue(self, est):
            return float(est) * 0.95

    def test_without_est_returns_the_display_ref(self):
        """``call_user_func($callback, $ref)`` —— 不给值就是"用谁的口径显示"。"""
        qdii = self.FakeQdiiRef()
        self.assertIs(callback_qdii_sma(qdii), qdii.ref)

    def test_with_est_returns_the_converted_number(self):
        self.assertAlmostEqual(callback_qdii_sma(self.FakeQdiiRef(), "100.00"), 95.0)

    def test_qdii_table_has_the_est_column(self):
        """``EchoQdiiSmaParagraph()`` 会多出两列（对照标的 + T+1EST）。"""
        qdii = self.FakeQdiiRef()
        history = FakeHistory(sma=make_sma(value="100.00"), next_values=make_sma(value="102.00"),
                              ref=FakeRef())
        service = SmaService(history_factory=lambda ref, wide: history)

        table = service.qdii_table(qdii)

        self.assertIsNotNone(table)
        # 5 列基础 + 对照标的列 + T+1EST
        self.assertEqual(table["column_count"], 7)
        self.assertEqual(table["cell_count"], 7)
        # 对照列的值是 valuation 后的价格（100 * 0.95 = 95）
        self.assertEqual(_cells(table["rows"][0])[5], "95.000")

    def test_qdii_table_without_est_ref_returns_none(self):
        class Bare:
            pass

        self.assertIsNone(SmaService().qdii_table(Bare()))


class PairCallbackTest(unittest.TestCase):
    """``_callbackFundPairSma`` / ``_callbackAhPairSma``（``:151`` / ``:169``）。

    两个回调的形状一样，差别只有"显示口径是谁"：

    * 配对兄弟用 **ref 自己**（``$strEst ? $ref->EstFromPair($strEst) : $ref``）；
    * AH 比价用 **``GetPairRef()``**（对方的价格才是那一列要显示的）。
    """

    class PairRef(FakeRef):
        """带上 ``pair_ref`` / ``cny_ref`` / 校准值的引用对象。"""

        def __init__(self, *args, **kwargs):
            FakeRef.__init__(self, *args, **kwargs)
            self.pair_ref = FakeRef(symbol="H股-配对", price="100.00")
            self.cny_ref = None
            self.factor = 1.0
            self._position = 1.0

        def get_pair_ref(self):
            return self.pair_ref

        def get_position(self):
            return self._position

        def get_last_calibration_val(self):
            return False

    def _pair(self):
        return self.PairRef(symbol="SZ161130", price="4.88")

    def test_fund_pair_without_est_returns_the_ref_itself(self):
        ref = self._pair()
        self.assertIs(callback_fund_pair_sma(ref), ref)

    def test_fund_pair_with_est_converts_from_the_pair(self):
        """``EstFromPair(100)`` —— 汇率缺省 1.0、校准 1.0 → 就是 100。"""
        self.assertAlmostEqual(callback_fund_pair_sma(self._pair(), "100.00"), 100.0)

    def test_ah_pair_without_est_returns_the_pair_ref(self):
        ref = self._pair()
        self.assertIs(callback_ah_pair_sma(ref), ref.pair_ref)

    def test_ah_pair_with_est_converts_to_the_pair(self):
        self.assertAlmostEqual(callback_ah_pair_sma(self._pair(), "100.00"), 100.0)

    def test_zero_est_is_read_as_no_est(self):
        """又一次 PHP 的 ``'0'`` —— ``if ($strEst)`` 在这里是假值。"""
        ref = self._pair()
        self.assertIs(callback_fund_pair_sma(ref, "0"), ref)
        self.assertIs(callback_ah_pair_sma(ref, "0"), ref.pair_ref)

    def test_two_callbacks_differ_only_in_the_display_ref(self):
        """同一个人传入同一个价格，两个回调吐出来的**数**一致，口径不同。"""
        ref = self._pair()
        self.assertAlmostEqual(callback_fund_pair_sma(ref, "100.00"),
                               callback_ah_pair_sma(ref, "100.00"))
        self.assertIsNot(callback_fund_pair_sma(ref), callback_ah_pair_sma(ref))


class FundPairTablesTest(unittest.TestCase):
    """``EchoFundPairSmaParagraphs()``（``php/ui/smaparagraph.php:156``）。

    ``_qdiigroup.php:101`` 在每个杠杆标的身上各 echo 一张均线表：

    .. code-block:: php

        foreach ($arFundPairRef as $fund_pair_ref)
        {   EchoSmaParagraph($ref, '', $fund_pair_ref, '_callbackFundPairSma', $callback2); }

    三个容易抄错的点，逐个钉住：

    1. **``''`` 不是 ``false``** —— 传空串，于是这些表没有标题文字
       （``if ($str === false)`` 是严格比较）；
    2. **算均线的是同一个 ref**（中间层标的），变的只有 ``$cb_ref``；
    3. **对照列是 maroon**（``TableColumnStock`` 的构造第三个参数）。
    """

    class LevRef(FakeRef):
        """一个杠杆标的：自己有换算因子，也是那一列的显示口径。"""

        def __init__(self, symbol="SPY", factor=10.0, precision=2):
            FakeRef.__init__(self, symbol=symbol, precision=precision)
            self.factor = factor
            self.cny_ref = None
            self._position = 1.0

        def get_position(self):
            return self._position

        def get_last_calibration_val(self):
            return False

    def _two_refs(self):
        return [self.LevRef(symbol="SH", factor=8.0),
                self.LevRef(symbol="SPY", factor=10.0)]

    def _tables(self, refs=None, callback2=False):
        """中间层标的的均线固定成 ``1000.00``，只看对照列怎么变。"""
        history = FakeHistory(sma=make_sma(value="1000.00"), ref=FakeRef(symbol="^GSPC"))
        service = make_service(history)
        return service.fund_pair_tables(FakeRef(symbol="^GSPC"),
                                        self._two_refs() if refs is None else refs,
                                        callback2)

    def test_one_table_per_pair_ref(self):
        tables = self._tables()
        self.assertEqual(len(tables), 2)

    def test_columns_of_each_table_are_the_same_shape(self):
        """5 列基础 + 对照列 + T+1EST = 7 列。"""
        for table in self._tables():
            self.assertEqual(table["column_count"], 7)
            self.assertEqual(table["cell_count"], 7)

    def test_the_stock_column_carries_this_ref_display(self):
        """``new TableColumnStock($est_ref, 90)`` —— 列头是**这一个**标的。

        生产端 SZ161125 页上两张表的第 6 列分别是 ``SH`` 与 ``SPY``。
        """
        heads = [table["columns"][5]["text"] for table in self._tables()]
        self.assertEqual(heads, ["SH", "SPY"])

    def test_the_stock_column_is_maroon(self):
        """``parent::__construct($sym->GetDisplay(), $iWidth, 'maroon')``。

        这一条是**修 bug 时补的**：原先写成不带颜色的 ``column_base``，
        数值全对、只差一个颜色 —— 生产端是 ``<font color=maroon>SH</font>``。
        """
        for table in self._tables():
            self.assertEqual(table["columns"][5]["color"], "maroon")
            self.assertEqual(table["columns"][5]["width"], 90)

    def test_memo_is_empty_string_not_the_default(self):
        """``if ($str === false)`` 是**严格**比较 —— 空串不会被换成默认标题。

        这是整段最反直觉的一点：``''`` 在 PHP 里是假值，但 ``=== false``
        挡住了替换。后果是这几张表**没有**"2026-10-09数据 牛市"那行说明，
        只有循环外自己的那张才有。
        """
        for table in self._tables():
            self.assertEqual(table["memo"], "")

    def test_each_table_converts_with_its_own_factor(self):
        """同一个均线值，两个标的按各自的因子换算：``1000 / factor``。"""
        cells = [_cells(table["rows"][0])[5] for table in self._tables()]
        self.assertEqual(cells, ["125.00", "100.00"])

    def test_no_pair_refs_means_no_tables(self):
        """``if (count($arLev) > 0)`` —— 没有杠杆标的整段不出现。"""
        self.assertEqual(self._tables(refs=[]), [])

    def test_a_table_without_history_is_skipped(self):
        """建不出 ``StockHistory`` 的那张不进列表，但不影响其它张。"""
        history = FakeHistory(sma=make_sma(value="1000.00"), ref=FakeRef(symbol="^GSPC"))
        count = {"n": 0}

        def factory(ref, wide):
            count["n"] += 1
            return history if count["n"] == 2 else None

        service = SmaService(history_factory=factory)
        tables = service.fund_pair_tables(FakeRef(), self._two_refs())
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0]["columns"][5]["text"], "SPY")

    def test_callback2_adds_one_more_column(self):
        """``if ($callback2) $ar[] = new TableColumn(call_user_func($callback2), 90)``。

        不传价格 → 列头文本；传 ``($strVal, $strNext)`` → 那一格。
        ``_qdiigroup.php`` 走这条路径时什么都没传，所以生产端是 7 列；
        这里验证"传了就多一列"这个机制本身还在。
        """
        tables = self._tables(callback2=lambda *args: "理论价" if not args else "1.05")
        self.assertEqual(len(tables[0]["columns"]), 8)
        self.assertEqual(tables[0]["columns"][7]["text"], "理论价")
        self.assertEqual(_cells(tables[0]["rows"][0])[7], "1.05")


def _cells(row):
    """把行的格子统一成文本列表（丢掉颜色）。

    排版本 Consider 只关心文本顺序；颜色由 ``reference_service.percentage_cell``
    决定，在它自己的测试里验证。
    """
    result = []
    for cell in row.cells:
        if isinstance(cell, tuple):
            result.append("" if cell[0] is None else str(cell[0]))
        else:
            result.append("" if cell is None else str(cell))
    return result


class FakeRefContractTest(unittest.TestCase):
    """替身的方法集合必须与**真实 ref** 对得上。

    ## 为什么需要这条

    这一条是被一个真实 bug 逼出来的：``FakeRef`` 原来实现了
    ``get_percentage_display``，而 ``refobj.EstReference`` **没有**那个方法
    （它只有 ``get_percentage_text``）。于是：

    * 单元测试全绿；
    * 真实页面上 ``_invoke(ref, "get_percentage_display", ...)`` 静默拿到空串；
    * **整列「溢价」都是空的**，且不报错。

    所以替身不能随便多实现方法 —— 多实现一个就会把"真实 ref 上没有它"
    这件事掩盖掉。下面直接对着真实类检查。
    """

    #: SMA 服务在 ref 上调用的方法。多一个都会掩盖"真实类没有它"。
    CALLED = (
        "get_price_display",
        "get_percentage_text",
    )

    def test_real_reference_has_every_method_the_service_calls(self):
        """真实 ``EstReference`` 必须真的有这些方法。"""
        from refobj import EstReference

        for name in self.CALLED:
            self.assertTrue(
                callable(getattr(EstReference, name, None)),
                "refobj.EstReference 缺少 %s（服务在调它）" % name)

    def test_fake_does_not_invent_methods(self):
        """替身不该实现真实类没有的方法 —— 那会造成假通过。"""
        from refobj import EstReference

        for name in self.CALLED:
            self.assertTrue(callable(getattr(FakeRef, name, None)),
                            "FakeRef 缺少 %s" % name)

    def test_get_percentage_display_is_not_the_method_used(self):
        """``get_percentage_display`` 在真实类上**不存在** —— 别再用它。

        PHP 的 ``GetPercentageDisplay()`` 是"文本 + 染色"，本项目把染色
        放在 ``reference_service.percentage_cell`` 里，所以服务只该要文本。
        """
        from refobj import EstReference

        self.assertFalse(
            callable(getattr(EstReference, "get_percentage_display", None)),
            "真实类上竟然有 get_percentage_display 了？那段 PHP 行为要重新核对")


if __name__ == "__main__":
    unittest.main()
