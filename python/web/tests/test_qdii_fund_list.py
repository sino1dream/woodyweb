"""详情页「基金指数对照表」的测试（`web/services/qdii_fund_list.py`）。

对应 `php/ui/fundlistparagraph.php:38 EchoFundListParagraph()` 与
`woody/res/php/_qdiigroup.php:41 _callbackFundListHedge()`（末列对冲值）。
**全部离线**：引用对象、数据源、链接构造器都由假对象顶替。

钉的是五件事：

1. **对冲值不自己算** —— 必须调到 `source.get_stock_hedge()`，
   而那边的公式是已迁移好的 `GetStockHedge()`；
2. **被对冲的"本基金"是 `$acct->GetRef()`**，不是行里的杠杆标的；
3. **「跟踪」列只有第一个出现的是链接**（PHP `static $arSymbol` 去重）；
4. **数字按 0 为基准染色**：负数红、绝对值小于容差是灰、正数不染色
   （`black` 在 PHP 里不包 `<font>`）；
5. **缺数据整段不出现**（PHP 的 `if (count($arLev) > 0)` 守卫）。

生产端 `sz161125cn.php` 实测的两行（用来核对形状）::

    SH   | ^GSPC | -1 | 245.591 | 2026-10-09 | -72
    SPY  | ^GSPC |  1 |  10.035 | 2026-10-09 | 1732
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

from web.services.qdii_fund_list import (  # noqa: E402
    DEFAULT_HEDGE,
    FUNDLIST_ID_SUFFIX,
    build_hedge_callback,
    callbackFundListHedge,
    fund_list_table,
    fund_reference_table,
)


class _Record:
    """`calibrationhistory` 一行：`GetDateNow()` 返回的就是它。"""

    def __init__(self, date="2026-10-09", close=0.0):
        self.date = date
        self.close = close


class FakeSource:
    """数据源替身：只实现这张表用到的四个方法。

    `get_stock_hedge` **记录调用参数**而不是真算：测试要钉的是"值有没有被
    以正确的姿势取到"，公式本身由 `auto/stockdata.py` 负责。
    """

    def __init__(self, hedges=None, names=None, dates=None, ids=None, last=None):
        self.hedges = hedges or {}
        self.names = names or {}
        self.dates = dates or {}
        self.ids = ids or {}
        #: ``lastcalibration`` 表：`stock_id -> close`。参考值那一格读它。
        self.last = {} if last is None else last
        self.hedge_calls = []
        self.last_calls = []

    def get_stock_hedge(self, symbol, stock_id, leverage_symbol=None):
        self.hedge_calls.append((symbol, stock_id, leverage_symbol))
        return self.hedges.get(leverage_symbol)

    def get_last_calibration(self, stock_id):
        """``LastCalibrationSql::ReadVal($strStockId, true)``（``sqlval.php:53``）。"""
        self.last_calls.append(stock_id)
        return self.last.get(stock_id)

    def get_stock_name(self, symbol):
        return self.names.get(symbol)

    def get_calibration_now(self, stock_id):
        date = self.dates.get(stock_id)
        return None if date is None else _Record(date)

    def get_stock_id(self, symbol):
        return self.ids.get(symbol)


class FakePairRef:
    """「跟踪」那一格的标的：只有符号与显示文本。"""

    def __init__(self, symbol="^GSPC", display=None):
        self._symbol = symbol
        self._display = symbol if display is None else display

    def get_symbol(self):
        return self._symbol

    def get_display(self):
        return self._display


class FakeRef:
    """杠杆标的替身：`FundPairReference` 在这张表里用到的那几样。

    `factor` 与 `position` 故意用**实例属性**而不是方法 —— 真类是
    `fFactor` / `fPosition` 字段配 `GetFactor()` 访问器；本模块两种都调得到
    （`_invoke` 蛇形优先），测试里用方法更接近真类。
    """

    def __init__(self, symbol="SPY", display=None, position=1.0, factor=10.035,
                 pair_ref=None, stock_id=None, price=None, precision=None):
        self._symbol = symbol
        self._display = symbol if display is None else display
        self._position = position
        self._factor = factor
        self._pair_ref = FakePairRef() if pair_ref is None else pair_ref
        self.stock_id = stock_id
        #: ``GetVal()``：参考值那一格的染色基准（``stockref.php:162``）
        self._price = price
        self._precision = precision

    def get_symbol(self):
        return self._symbol

    def get_display(self):
        return self._display

    def get_position(self):
        return self._position

    def get_factor(self):
        return self._factor

    def get_pair_ref(self):
        return self._pair_ref

    def get_val(self):
        return self._price

    def get_precision(self):
        return self._precision


class FakeFund:
    """被看的基金（PHP 的 `$acct->GetRef()`）。"""

    def __init__(self, symbol="SZ161125", stock_id="2"):
        self._symbol = symbol
        self.stock_id = stock_id

    def get_symbol(self):
        return self._symbol


def _link_builder(symbol, kind):
    """假链接构造器：把 kind 拼进 URL 里，测试才看得出指向哪一页。"""
    return "/%s/%s" % (kind, symbol)


class HedgeCallbackTest(unittest.TestCase):
    """`_callbackFundListHedge`：一个符号 → 对本基金的对冲值。"""

    def setUp(self):
        self.source = FakeSource(hedges={"SPY": 1732.4, "SH": -72.1})
        self.fund = FakeFund()

    def test_the_name_is_the_php_one(self):
        callback = build_hedge_callback(self.fund, self.source)
        self.assertEqual(callback.__name__, "_callbackFundListHedge")

    def test_camel_case_alias_points_at_the_same_function(self):
        self.assertIs(callbackFundListHedge, build_hedge_callback)

    def test_it_calls_the_already_migrated_formula(self):
        """对冲值必须来自 `get_stock_hedge`，本模块不得自己算第二份。"""
        callback = build_hedge_callback(self.fund, self.source)
        self.assertEqual(callback("SPY"), 1732.4)

    def test_the_subject_is_the_fund_not_the_row_symbol(self):
        """`GetStockHedge($ref->GetSymbol(), ...)` —— 第一、二个参数是**基金**。"""
        callback = build_hedge_callback(self.fund, self.source)
        callback("SPY")
        self.assertEqual(self.source.hedge_calls, [("SZ161125", "2", "SPY")])

    def test_stock_id_falls_back_to_a_database_lookup(self):
        source = FakeSource(ids={"SZ161125": 99})
        fund = FakeFund(stock_id=None)
        build_hedge_callback(fund, source)("SPY")
        self.assertEqual(source.hedge_calls[0][1], 99)

    def test_source_without_the_method_gives_the_php_default(self):
        """数据源没实现 `get_stock_hedge` 时退回 PHP 的 ``return 1.0``。"""
        callback = build_hedge_callback(self.fund, object())
        self.assertEqual(callback("SPY"), DEFAULT_HEDGE)
        self.assertEqual(DEFAULT_HEDGE, 1.0)

    def test_unknown_leverage_symbol_gives_the_php_default(self):
        callback = build_hedge_callback(self.fund, self.source)
        self.assertEqual(callback("ZZZ"), DEFAULT_HEDGE)

    def test_no_fund_symbol_means_no_callback(self):
        self.assertIsNone(build_hedge_callback(object(), self.source))


class TableGuardTest(unittest.TestCase):
    """PHP 的守卫：`if (count($arLev) > 0)`。"""

    def test_no_leverage_refs_means_no_table(self):
        self.assertIsNone(fund_list_table(FakeFund(), []))

    def test_none_instead_of_refs_means_no_table(self):
        self.assertIsNone(fund_list_table(FakeFund(), None))

    def test_no_fund_means_no_table(self):
        self.assertIsNone(fund_list_table(None, [FakeRef()]))

    def test_the_id_carries_the_symbol(self):
        table = fund_list_table(FakeFund(), [FakeRef()])
        self.assertEqual(table["id"], "SZ161125" + FUNDLIST_ID_SUFFIX)
        self.assertEqual(table["count"], 1)


class TableShapeTest(unittest.TestCase):
    """列头：六个，最后一列是对冲值（navy）。"""

    def setUp(self):
        self.table = fund_list_table(
            FakeFund(), [FakeRef("SH", position=-1.0, factor=245.591),
                         FakeRef("SPY", position=1.0, factor=10.035)],
        )

    def test_column_texts(self):
        self.assertEqual(
            [column["text"] for column in self.table["columns"]],
            ["代码", "跟踪代码", "仓位", "校准值", "日期", "对冲值"],
        )

    def test_only_the_hedge_column_is_colored(self):
        colors = [column["color"] for column in self.table["columns"]]
        self.assertEqual(colors[-1], "navy")
        self.assertEqual(colors[:2], ["maroon", "maroon"])

    def test_widths_match_the_production_page(self):
        self.assertEqual(
            [column["width"] for column in self.table["columns"]],
            [80, 80, 60, 140, 100, 80],
        )

    def test_one_row_per_leverage_ref(self):
        self.assertEqual(len(self.table["rows"]), 2)


class PairSymbolFallbackTest(unittest.TestCase):
    """「跟踪」列的兜底：引用对象说不出跟踪谁时用调用方给的。

    PHP 的 ``FundPairReference::GetPairRef()`` 自己知道（来自 ``fundpair`` 表），
    而本项目那个类**没迁**，引用对象答不上来 —— 真库上实测这一列是空的，
    所以调用方（路由）把"找它们时用的那个中间层符号"传进来。
    """

    class RefWithoutPair(FakeRef):
        def get_pair_ref(self):
            return None

    def setUp(self):
        self.ref = self.RefWithoutPair("QQQ")

    def test_the_caller_supplied_symbol_shows_up(self):
        table = fund_list_table(FakeFund(), [self.ref], pair_symbol="^NDX")
        self.assertEqual(table["rows"][0].cells[1], "^NDX")

    def test_it_is_still_deduplicated_for_the_link(self):
        table = fund_list_table(
            FakeFund(), [self.ref, self.RefWithoutPair("TQQQ")],
            link_builder=_link_builder, pair_symbol="^NDX",
        )
        self.assertEqual(table["rows"][0].hrefs.get(1), "/fund/^NDX")
        self.assertNotIn(1, table["rows"][1].hrefs)
        self.assertEqual(table["rows"][1].cells[1], "^NDX")

    def test_without_anything_the_cell_is_empty(self):
        table = fund_list_table(FakeFund(), [self.ref])
        self.assertEqual(table["rows"][0].cells[1], "")


class RowContentTest(unittest.TestCase):
    """一行六格：顺序、精度、染色、两个链接的去重。"""

    def setUp(self):
        self.source = FakeSource(
            hedges={"SH": -72.1, "SPY": 1732.4},
            names={"SH": "标普500指数ETF-ProShares做空", "SPY": "SPDR标普500 ETF"},
            dates={"sh": "2026-10-09", "spy": "2026-10-09"},
        )
        self.sh = FakeRef("SH", position=-1.0, factor=245.591, stock_id="sh")
        self.spy = FakeRef("SPY", position=1.0, factor=10.035, stock_id="spy")
        # 两行的「跟踪」都是 ^GSPC —— 只有第一次出现该给链接
        self.sh._pair_ref = FakePairRef("^GSPC")
        self.spy._pair_ref = FakePairRef("^GSPC")
        self.table = fund_list_table(
            FakeFund(),
            [self.sh, self.spy],
            source=self.source,
            link_builder=_link_builder,
        )
        self.first, self.second = self.table["rows"]

    def test_cell_order_and_text(self):
        """与生产端那行逐字对照：`SH | ^GSPC | -1 | 245.591 | 2026-10-09 | -72`。"""
        self.assertEqual(
            self.first.cells[:5], ["SH", "^GSPC", ("-1", "red"), "245.591",
                                   "2026-10-09"],
        )
        self.assertEqual(self.first.cells[5], ("-72", "red"))

    def test_hedge_of_the_second_row(self):
        self.assertEqual(self.second.cells[5], "1732")

    def test_positive_numbers_are_not_colored(self):
        """PHP 的 `black` 不包 `<font>`，等于纯文本。"""
        self.assertEqual(self.second.cells[2], "1")
        self.assertEqual(self.second.cells[3], "10.035")

    def test_negative_position_is_red(self):
        self.assertEqual(self.first.cells[2], ("-1", "red"))

    def test_tiny_values_are_grey_not_red(self):
        """容差是 `0.5 / 10**精度`：精度 0 时 0.3 落在容差内 → grey。"""
        source = FakeSource(hedges={"X": 0.3})
        table = fund_list_table(FakeFund(), [FakeRef("X")], source=source)
        self.assertEqual(table["rows"][0].cells[5], ("0", "grey"))

    def test_the_first_cell_links_to_calibration_history(self):
        self.assertEqual(self.first.href, "/calibrationhistory/SH")
        self.assertEqual(self.second.href, "/calibrationhistory/SPY")

    def test_the_pair_link_appears_only_once(self):
        """PHP `static $arSymbol`：第二行的 `^GSPC` 退化成纯文本。"""
        self.assertEqual(self.first.hrefs.get(1), "/fund/^GSPC")
        self.assertNotIn(1, self.second.hrefs)
        self.assertEqual(self.second.cells[1], "^GSPC")

    def test_the_stock_name_is_the_row_tooltip(self):
        self.assertEqual(self.first.title, "标普500指数ETF-ProShares做空")

    def test_without_a_link_builder_there_are_no_links(self):
        table = fund_list_table(FakeFund(), [self.sh, self.spy], source=self.source)
        self.assertEqual(table["rows"][0].href, "")
        self.assertEqual(table["rows"][0].hrefs, {})


class ReferenceTableTest(unittest.TestCase):
    """``EchoFundListParagraph($arRef)`` **不带回调**那版 —— 末列「参考值」。

    PHP 里它与带回调那版是**同一个函数**（``$callback = false`` 走 else 分支，
    ``fundlistparagraph.php:33``）：

    .. code-block:: php

        if ($strVal = $last_sql->ReadVal($strStockId, true))
            $ar[] = $ref->GetPriceDisplay(floatval($strVal));

    个股详情页（``_mystock.php:134``）走的就是这一版。
    """

    def setUp(self):
        self.source = FakeSource(last={"spy": 1.5234}, dates={"spy": "2026-10-09"})
        self.ref = FakeRef("SPY", position=1.0, factor=1.5234, stock_id="spy",
                           price=1.5, precision=3)

    def _table(self, **kwargs):
        return fund_reference_table([self.ref], source=self.source, **kwargs)

    def test_the_last_column_is_reference_not_hedge(self):
        """末列列名是「参考值」—— PHP 直接 ``new TableColumn('参考值')``。

        宽 80 是 ``TableColumn`` 的默认值，且**没有颜色**：PHP 那个基类
        ``TableColumn::__construct($strText = '', $iWidth = 80,
        $strColor = false, ...)`` 不传色就不包 ``<font>``。
        """
        column = self._table()["columns"][-1]
        self.assertEqual(column["text"], "参考值")
        self.assertEqual(column["width"], 80)
        self.assertEqual(column["color"], "")

    def test_the_value_comes_from_lastcalibration(self):
        """参考值读 ``lastcalibration`` 表，不是本行的「校准值」列。"""
        table = self._table()
        self.assertEqual(self.source.last_calls, ["spy"])
        self.assertEqual(table["rows"][0].cells[5], ("1.523", "red"))

    def test_the_color_baseline_is_the_refs_own_price(self):
        """染色基准是 ref 的现价（``GetPriceDisplay`` 单参），不是 0。

        与「仓位」/「校准值」两列（``GetNumberDisplay``，以 0 为基准）不是
        一套规则：这里 ref 现价 1.5、参考值 1.5234 → 涨 → 红；
        换成"以 0 为基准"也会是红，所以**再补一条跌的方向**才钉得住。
        """
        self.ref._price = 1.6
        self.assertEqual(self._table()["rows"][0].cells[5], ("1.523", "green"))

    def test_precision_comes_from_the_ref(self):
        """精度取 ``$ref->GetPrecision()``；ref 不给时用模块兜底的 3。"""
        self.source.last["spy"] = 1.23456
        self.ref._precision = 2
        self.assertEqual(self._table()["rows"][0].cells[5][0], "1.23")
        #: ref 不给精度时兜底 3 —— 多数符号的 ``GetPrecision()`` 就是 3
        self.ref._precision = None
        self.assertEqual(self._table()["rows"][0].cells[5][0], "1.235")

    def test_zero_value_is_left_blank(self):
        """库里存 ``0`` 时 PHP 的 ``if ($strVal = ...)`` 判为假 —— 那格不显示。

        PHP 存的是 ``strval(0.0)`` 即 ``"0"``，``"0"`` 是 PHP 里少数几个
        "字符串为假"的值之一。写成 ``if ($strVal != '')`` 就会显示 ``0.000``，
        与生产端对不上。
        """
        self.source.last["spy"] = 0.0
        self.assertEqual(self._table()["rows"][0].cells[5], "")

    def test_source_without_the_reader_leaves_the_cell_blank(self):
        """数据源没有 ``get_last_calibration`` 时留空，不编造一个值。"""
        table = fund_reference_table([self.ref], source=object())
        self.assertEqual(table["rows"][0].cells[5], "")

    def test_the_first_five_columns_match_the_hedge_version(self):
        """两版只有末列不同 —— 前五列与逐行组装是同一份实现。"""
        reference = self._table()["columns"][:5]
        hedge = fund_list_table(FakeFund(), [self.ref])["columns"][:5]
        self.assertEqual(reference, hedge)

    def test_shared_cells_match_the_hedge_version(self):
        """同一 ref 走两版，前五格必须逐格相同。"""
        reference = self._table()["rows"][0].cells[:5]
        hedge = fund_list_table(FakeFund(), [self.ref], source=self.source)
        self.assertEqual(reference, hedge["rows"][0].cells[:5])

    def test_no_refs_means_no_table(self):
        """PHP 的 ``if (count($arRef) > 0)``。"""
        self.assertIsNone(fund_reference_table([]))
        self.assertIsNone(fund_reference_table(None))

    def test_the_id_uses_the_first_refs_symbol(self):
        """这一版没有"被看的基金"那个参数，id 前缀取行里的第一个符号。"""
        self.assertEqual(self._table()["id"], "SPY" + FUNDLIST_ID_SUFFIX)

    def test_links_are_built_the_same_way(self):
        """两格链接（校准记录页 / 标的详情页）与带回调版同一套约定。"""
        table = self._table(link_builder=_link_builder)
        self.assertEqual(table["rows"][0].href, "/calibrationhistory/SPY")
        self.assertEqual(table["rows"][0].hrefs.get(1), "/fund/^GSPC")


if __name__ == "__main__":
    unittest.main()
