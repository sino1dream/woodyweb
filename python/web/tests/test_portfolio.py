"""投资组合段落的测试（``php/ui/portfolioparagraph.php`` → ``portfolio_service.py``）。

**全部离线**：交易与引用对象都是内存假实现，不连 MySQL、不发请求。

## 为什么这个文件的用例特别多

三件事只有单测能钉住：

1. **``static $fCny`` 的跨调用持久化** —— 换成显式累加器之后，
   "同一个实例跨表共享"与"每次新建"的差别必须在测试里看得见；
2. **行宽 2 / 6 / 7 三种** —— PHP 靠"少几格"表达"没有持仓"，
   补空格就丢了这个信息；
3. **``_getArbitrageTestStr()`` 的 ``while`` 赋值语义** —— ``break`` 时
   ``$record`` 是触发行；自然耗尽时最后一次赋值把 ``null`` 留在里面，
   于是组内没有正数量条目时函数返回空串。初版移植把耗尽路径误写成
   "取最后一行"，PHP 逐例对照抓出后修正（pitfalls 第 47 条）。
"""

import re
import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[3]
PYTHON_DIR = ROOT_DIR / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from web.bootstrap import install_paths  # noqa: E402

install_paths()

from core.stocktrans import MyStockTransaction  # noqa: E402
from sql.historytable import format_int_display  # noqa: E402
from web.services.portfolio_service import (  # noqa: E402
    ARBITRAGE_SYMBOLS,
    FX_SUSDCNH_SYMBOL,
    PORTFOLIO_PRICE_PREFIX,
    PORTFOLIO_TABLE_ID,
    PORTFOLIO_TEST_QUANTITY,
    PortfolioCnyTotal,
    build_portfolio_row,
    build_portfolio_table,
    build_merge_table,
    get_arbitrage_quantity,
    get_arbitrage_test_str,
    get_portfolio_test_val,
    merge_transactions_by_symbol,
    portfolio_columns,
    portfolio_title,
    sort_trans_by_symbol,
)
from ui.uicolumn import columns_to_dicts, table_column_get_total_width  # noqa: E402


class FakeRef:
    """``MyStockReference`` 的最小替身。

    ``price`` 用**字符串**（与 PHP 的 ``strPrice`` 一致），用来验证
    ``GetValue()`` 走的是 ``floatval()`` 而不是 ``float()``。
    """

    def __init__(self, symbol="SZ161125", price="1.2", position=0.0,
                 stock_id=None, name="", val="7.1", fund_a=False):
        self._symbol = symbol
        self._price = price
        self._position = position
        self._stock_id = stock_id or symbol
        self._name = name
        self._val = val

    def get_symbol(self):
        return self._symbol

    def get_stock_id(self):
        return self._stock_id

    def get_price(self):
        return self._price

    def get_position(self):
        return self._position

    def get_val(self):
        return self._val

    def get_price_display(self, disp=None, prev=None, precision=None):
        return "cost(%s)" % disp

    def get_percentage_text(self, divisor=None, dividend=None):
        return "12.5%"

    def is_symbol_a(self):
        return self._symbol.startswith(("SH", "SZ"))

    def is_symbol_h(self):
        return self._symbol.startswith("HK")


def _trans(symbol, shares, cost, price="1.2", position=0.0, stock_id=None,
           records=1, group_id="g1", name=""):
    """一笔已成交的交易。``records`` 用来测"没有记录就整行跳过"。"""
    ref = FakeRef(symbol=symbol, price=price, position=position,
                  stock_id=stock_id, name=name)
    trans = MyStockTransaction(ref, group_id)
    for _ in range(records):
        trans.AddTransaction(shares, cost)
    return trans


# ---------------------------------------------------------------------------
# 列与表头
# ---------------------------------------------------------------------------


class ColumnTest(unittest.TestCase):
    """七个列：顺序、宽度、颜色、总宽。"""

    def test_columns_match_php(self):
        columns = columns_to_dicts(portfolio_columns())
        self.assertEqual(
            [(c["text"], c["width"], c.get("color", "")) for c in columns],
            [
                ("代码", 80, "maroon"),
                ("盈亏", 105, "red"),
                ("持仓", 105, ""),
                ("数量", 100, "indigo"),
                ("平均价格", 70, "blue"),
                ("涨幅", 70, ""),
                ("测试", 110, ""),
            ],
        )

    def test_price_prefix_is_average(self):
        """价格列的前缀是 ``平均``，不是段落标题前缀 ``个股``。"""
        self.assertEqual(PORTFOLIO_PRICE_PREFIX, "平均")
        columns = columns_to_dicts(portfolio_columns())
        self.assertEqual(columns[4]["text"], "平均价格")

    def test_total_width_is_640(self):
        self.assertEqual(table_column_get_total_width(portfolio_columns()), 640)

    def test_title_and_id(self):
        self.assertEqual(portfolio_title(), "个股盈亏")
        self.assertEqual(PORTFOLIO_TABLE_ID, "myportfolio")


# ---------------------------------------------------------------------------
# 行宽：2 / 6 / 7
# ---------------------------------------------------------------------------


class RowWidthTest(unittest.TestCase):
    """PHP 靠"少几格"表达状态，不能补空格。"""

    def test_zero_records_skipped_entirely(self):
        """``GetTotalRecords() > 0`` 是守卫：没记录连符号都不显示。"""
        table = build_portfolio_table([_trans("KWEB", 100, 8000.0, records=0)])
        self.assertEqual(table["rows"], [])

    def test_zero_shares_gives_two_cells(self):
        """清仓后 ``iTotalShares == 0``：只剩首格与盈亏格。"""
        trans = _trans("KWEB", 0, 0.0, price="80.0")
        row = build_portfolio_row(trans)
        self.assertEqual(len(row.cells), 2)
        self.assertEqual(row.cells[0], "KWEB")

    def test_shares_without_test_symbol_gives_six_cells(self):
        """不在 ``switch`` 清单里的符号：六格，最后一格整格缺失。"""
        trans = _trans("SH513100", 100, 120.0)
        row = build_portfolio_row(trans)
        self.assertEqual(len(row.cells), 6)

    def test_shares_with_test_symbol_gives_seven_cells(self):
        trans = _trans("KWEB", 300, 24000.0, price="80.0")
        row = build_portfolio_row(trans)
        self.assertEqual(len(row.cells), 7)

    def test_change_cell_only_when_cost_positive(self):
        """``GetTotalCost() > 0.0`` 才有涨幅格内容，否则是空串。"""
        paid = _trans("KWEB", 100, 8000.0, price="80.0")
        self.assertEqual(build_portfolio_row(paid).cells[5], ("12.5%", "red"))

        free = _trans("KWEB", 100, 0.0, price="80.0")
        self.assertEqual(build_portfolio_row(free).cells[5], "")

    def test_cells_are_value_shares_cost_change(self):
        trans = _trans("SH600104", 100, 1200.0, price="13.5")
        row = build_portfolio_row(trans)
        # 市值 1350、持仓 100、均价 12
        self.assertEqual(row.cells[2], "1350")
        self.assertEqual(row.cells[3], "100")
        self.assertEqual(row.cells[4], "cost(12.0)")

    def test_first_cell_is_transaction_link(self):
        trans = _trans("SH513100", 100, 120.0, group_id="42")
        row = build_portfolio_row(trans)
        self.assertEqual(row.href,
                         "/woody/res/mystocktransactioncn.php?groupid=42&symbol=SH513100")
        self.assertEqual(row.cells[0], "SH513100")

    def test_title_is_stock_name(self):
        trans = _trans("SH513100", 100, 120.0, name="华宝油气")
        row = build_portfolio_row(trans, name_of=lambda symbol: "华宝油气")
        self.assertEqual(row.title, "华宝油气")

    def test_link_builder_overrides_default(self):
        trans = _trans("SH513100", 100, 120.0, group_id="42")
        row = build_portfolio_row(
            trans, link_builder=lambda group, symbol: ("/t/%s/%s" % (group, symbol), symbol))
        self.assertEqual(row.href, "/t/42/SH513100")


# ---------------------------------------------------------------------------
# static $fCny
# ---------------------------------------------------------------------------


class CnyTotalTest(unittest.TestCase):
    """``static $fCny`` 换成显式累加器之后的语义。"""

    def test_accumulates_qdii_value_times_position(self):
        trans = _trans("SZ161125", 1000, 1200.0, price="1.2", position=0.95)
        cny = PortfolioCnyTotal()
        build_portfolio_row(trans, cny=cny)
        # 市值 1200 × 仓位 0.95
        self.assertAlmostEqual(cny.value, 1140.0)

    def test_non_qdii_does_not_accumulate(self):
        trans = _trans("KWEB", 300, 24000.0, price="80.0", position=0.5)
        cny = PortfolioCnyTotal()
        build_portfolio_row(trans, cny=cny)
        self.assertEqual(cny.value, 0.0)

    def test_zero_shares_does_not_accumulate(self):
        """累加在 ``if ($iShares != 0)`` 里面，清仓的不加。"""
        trans = _trans("SZ161125", 0, 0.0, price="1.2", position=0.95)
        cny = PortfolioCnyTotal()
        build_portfolio_row(trans, cny=cny)
        self.assertEqual(cny.value, 0.0)

    def test_shared_instance_sees_previous_rows(self):
        """同一个实例跨行传递 —— 复刻 PHP 的静态持久化。"""
        cny = PortfolioCnyTotal()
        lof = _trans("SZ161125", 1000, 1200.0, price="1.2", position=0.95)
        forex = _trans(FX_SUSDCNH_SYMBOL, 100000, 710000.0, price="7.1")
        build_portfolio_row(lof, cny=cny)
        row = build_portfolio_row(forex, cny=cny)
        # (710000 + 1140) / 7.1 / 1000 = 100.157… → 100
        self.assertEqual(row.cells[6], "$100K")

    def test_fresh_instance_only_sees_itself(self):
        """不传 ``cny`` 时内部新建，``fx_susdcnh`` 只看到自己。"""
        forex = _trans(FX_SUSDCNH_SYMBOL, 100000, 710000.0, price="7.1")
        row = build_portfolio_row(forex)
        # 710000 / 7.1 / 1000 = 100
        self.assertEqual(row.cells[6], "$100K")

    def test_value_cell_uses_pre_accumulation_value(self):
        """市值格（索引 2）用的是加 ``$fCny`` **之前**的 ``$fVal``。"""
        cny = PortfolioCnyTotal(500.0)
        forex = _trans(FX_SUSDCNH_SYMBOL, 100000, 710000.0, price="7.1")
        row = build_portfolio_row(forex, cny=cny)
        self.assertEqual(row.cells[2], "710000")
        # 最后一格才加上累加值：(710000 + 500) / 7.1 / 1000 = 100.07 → 100
        self.assertEqual(row.cells[6], "$100K")

    def test_amount_is_negative_of_total(self):
        cny = PortfolioCnyTotal()
        forex = _trans(FX_SUSDCNH_SYMBOL, 100000, 710000.0, price="7.1")
        row = build_portfolio_row(forex, cny=cny)
        self.assertIn("overnightcnh=-710000", row.hrefs[6])

    def test_table_shares_one_instance_across_rows(self):
        """``build_portfolio_table`` 内部只建一个累加器，全表共享。"""
        trans_list = [
            _trans("SZ161125", 1000, 1200.0, price="1.2", position=0.95),
            _trans(FX_SUSDCNH_SYMBOL, 100000, 710000.0, price="7.1"),
        ]
        table = build_portfolio_table(trans_list)
        self.assertEqual(table["rows"][1].cells[6], "$100K")

    def test_caller_can_share_across_tables(self):
        """多张表传同一个实例 —— PHP 里静态变量正是这个行为。"""
        cny = PortfolioCnyTotal()
        first = build_portfolio_table(
            [_trans("SZ161125", 1000, 1200.0, price="1.2", position=0.95)], cny=cny)
        second = build_portfolio_table(
            [_trans(FX_SUSDCNH_SYMBOL, 100000, 710000.0, price="7.1")], cny=cny)
        self.assertEqual(first["rows"][0].cells[6], "1,000")
        self.assertEqual(second["rows"][0].cells[6], "$100K")

    def test_add_qdii_handles_junk(self):
        cny = PortfolioCnyTotal()
        cny.add_qdii("abc", "1.0")
        self.assertEqual(cny.value, 0.0)
        cny.add_qdii(None, None)
        self.assertEqual(cny.value, 0.0)


# ---------------------------------------------------------------------------
# 测试列的七个分支
# ---------------------------------------------------------------------------


class PortfolioTestValTest(unittest.TestCase):
    """``_getPortfolioTestVal()``：写死目标持仓的那三个符号。"""

    def test_known_symbols(self):
        self.assertEqual(get_portfolio_test_val(500, "KWEB"), 300)
        self.assertEqual(get_portfolio_test_val(13000, "SH600104"), 1000)
        self.assertEqual(get_portfolio_test_val(700, "TLT"), 200)

    def test_unknown_symbol_returns_shares(self):
        self.assertEqual(get_portfolio_test_val(1234, "SH513100"), 1234)
        self.assertEqual(get_portfolio_test_val(1234, ""), 1234)

    def test_negative_when_underweight(self):
        self.assertEqual(get_portfolio_test_val(100, "KWEB"), -100)


class ArbitrageQuantityTest(unittest.TestCase):
    """``GetArbitrageQuantity()``：折股份数，带千分位。"""

    def test_thousands_separator(self):
        self.assertEqual(get_arbitrage_quantity("SZ161125", "501", 1000.0), "1,000")
        self.assertEqual(get_arbitrage_quantity("SZ161125", "501", 1234567.0),
                         "1,234,567")

    def test_divides_by_hedge(self):
        self.assertEqual(
            get_arbitrage_quantity("SZ161125", "501", 1000.0,
                                   hedge_of=lambda s, i: 2.0), "500")

    def test_hedge_zero_falls_back_to_one(self):
        """PHP 里 ``GetStockHedge()`` 查不到回 1.0；这里补一层 0 保护。"""
        self.assertEqual(
            get_arbitrage_quantity("SZ161125", "501", 1000.0,
                                   hedge_of=lambda s, i: 0.0), "1,000")

    def test_no_hedge_callback_is_one(self):
        self.assertEqual(get_arbitrage_quantity("SZ161125", "501", 1000.0), "1,000")


class ArbitrageTestStrTest(unittest.TestCase):
    """``_getArbitrageTestStr()``：KWEB 的跨标的对冲量。

    照抄了三处 PHP 的行为（``while ($record = fetch())`` 的语义，
    见 ``portfolio_service`` 模块 docstring 第 3 条）：

    * 只有数量**为正**才 ``break``，否则继续扫；
    * **break 时** ``$record`` 是触发行 —— 数量与 hedge 同源；
    * **耗尽时** while 的赋值把 ``null`` 留在 ``$record`` →
      ``if ($record)`` 为假 → 返回空串。

    公式：``arbitrage_quantity + (shares - target) * hedge``，
    其中 ``target`` 是 :data:`PORTFOLIO_TEST_QUANTITY` 里 KWEB 的 200。
    """

    ITEMS = [
        {"stock_id": "501", "quantity": "600"},
        {"stock_id": "502", "quantity": "300"},
        {"stock_id": "503", "quantity": "200"},
    ]

    def test_no_items_returns_empty(self):
        self.assertEqual(get_arbitrage_test_str(300, "g1", "502", "KWEB", items=[]), "")

    def test_skips_own_stock_id(self):
        """自己是 503：扫到 501 的 600 就 break，偏离 300-200=100。"""
        self.assertEqual(
            get_arbitrage_test_str(300, "g1", "503", "KWEB", items=self.ITEMS,
                                   hedge_of=lambda s, i: 1.0), "700")

    def test_break_record_is_used_for_hedge(self):
        """break 时 ``$record`` 就是触发那一条（502），与数量一致。"""
        # 自己是 501 → 扫 502 的 300 即 break；300 + 100 = 400
        self.assertEqual(
            get_arbitrage_test_str(300, "g1", "501", "KWEB", items=self.ITEMS,
                                   hedge_of=lambda s, i: 1.0), "400")

    def test_quantity_includes_deviation(self):
        """``arbitrage + (shares - 200) * hedge``。"""
        # 自己是 503 → arbitrage 600；偏离 100；hedge 2.0 → 600 + 200
        self.assertEqual(
            get_arbitrage_test_str(300, "g1", "503", "KWEB", items=self.ITEMS,
                                   hedge_of=lambda s, i: 2.0), "800")

    def test_no_positive_quantity_returns_empty(self):
        """全部非正时不 break，循环耗尽 —— ``$record`` 被 while 赋值成 null。

        PHP 逐例对照钉住：``items=[501:0, 503:-5]``、自己是 502 时
        ``_getArbitrageTestStr`` 返回空串，不是 "-5 + 100"。
        """
        items = [
            {"stock_id": "501", "quantity": "0"},
            {"stock_id": "503", "quantity": "-5"},
        ]
        # 自己是 502：501 → 0（不 break），503 → -5（不 break），耗尽 → null
        self.assertEqual(
            get_arbitrage_test_str(300, "g1", "502", "KWEB", items=items,
                                   hedge_of=lambda s, i: 1.0), "")

    def test_only_self_in_group_returns_empty(self):
        """组里唯一一条就是自己：跳过之后循环耗尽，PHP 回空串。

        初版误写成"取最后一行"，会用自己的 stock_id 去查 hedge ——
        ``_php_portfolio_compare.py`` 抓出来后钉在这里。
        """
        items = [
            {"stock_id": "502", "quantity": "300"},
        ]
        self.assertEqual(
            get_arbitrage_test_str(300, "g1", "502", "KWEB", items=items,
                                   hedge_of=lambda s, i: 1.0), "")


class TestCellBranchTest(unittest.TestCase):
    """最后一格的七个符号分支与 default（不追加）。"""

    def test_hf_es_divides_by_five(self):
        trans = _trans("hf_ES", 100, 0.0, price="5000.0")
        row = build_portfolio_row(trans)
        self.assertEqual(row.cells[6], "20")

    def test_arbitrage_symbols_use_quantity(self):
        for symbol in ARBITRAGE_SYMBOLS:
            trans = _trans(symbol, 1000, 1200.0, price="1.2")
            row = build_portfolio_row(trans, hedge_of=lambda s, i: 1.0)
            self.assertEqual(row.cells[6], "1,000", symbol)

    def test_sh600104_and_tlt_use_portfolio_test_val(self):
        row = build_portfolio_row(_trans("SH600104", 13000, 1200.0))
        self.assertEqual(row.cells[6], "1000")
        row = build_portfolio_row(_trans("TLT", 700, 1200.0))
        self.assertEqual(row.cells[6], "200")

    def test_kweb_group_without_other_rows_is_empty(self):
        """组里只有自己：PHP 的循环耗尽后 ``$record`` 为 null，测试列是空串。"""
        trans = _trans("KWEB", 300, 24000.0, price="80.0", stock_id="502")
        row = build_portfolio_row(
            trans,
            items_of=lambda group: [{"stock_id": "502", "quantity": "300"}],
            hedge_of=lambda s, i: 1.0)
        self.assertEqual(row.cells[6], "")

    def test_kweb_without_items_is_empty(self):
        trans = _trans("KWEB", 300, 24000.0, price="80.0")
        row = build_portfolio_row(trans)
        self.assertEqual(row.cells[6], "")

    def test_fx_susdcnh_has_link(self):
        trans = _trans(FX_SUSDCNH_SYMBOL, 100000, 710000.0, price="7.1")
        row = build_portfolio_row(trans)
        self.assertEqual(row.cells[6], "$100K")
        self.assertIn("overnightcnh=-710000", row.hrefs[6])

    def test_fx_susdcnh_display_name_is_usdcnh(self):
        """``StockSymbol::GetDisplay()`` 把 ``fx_susdcnh`` 显示成 ``USDCNH``。"""
        trans = _trans(FX_SUSDCNH_SYMBOL, 100000, 710000.0, price="7.1")
        row = build_portfolio_row(trans)
        self.assertEqual(row.cells[0], "USDCNH")

    def test_unknown_symbol_has_no_test_cell(self):
        row = build_portfolio_row(_trans("SH513100", 100, 120.0))
        self.assertEqual(len(row.cells), 6)
        self.assertEqual(row.hrefs, {})


# ---------------------------------------------------------------------------
# 行序
# ---------------------------------------------------------------------------


class SortTransBySymbolTest(unittest.TestCase):
    """``_transSortBySymbol()``：A → H → US，每块内按符号。"""

    def test_blocks_are_concatenated(self):
        trans_list = [
            _trans("KWEB", 1, 1.0),
            _trans("SZ161125", 1, 1.0),
            _trans("HK00700", 1, 1.0),
            _trans("SH513100", 1, 1.0),
        ]
        ordered = sort_trans_by_symbol(trans_list)
        self.assertEqual([t.get_symbol() for t in ordered],
                         ["SH513100", "SZ161125", "HK00700", "KWEB"])

    def test_byte_order_puts_fx_last(self):
        """小写 ``f`` 的码位在大写之后，``fx_susdcnh`` 稳居美股块末尾。

        这保证 ``static $fCny`` 累加完成之后才被读。
        """
        trans_list = [
            _trans(FX_SUSDCNH_SYMBOL, 1, 1.0),
            _trans("KWEB", 1, 1.0),
            _trans("TLT", 1, 1.0),
            _trans("SPY", 1, 1.0),
        ]
        ordered = sort_trans_by_symbol(trans_list)
        self.assertEqual([t.get_symbol() for t in ordered],
                         ["KWEB", "SPY", "TLT", FX_SUSDCNH_SYMBOL])

    def test_qdii_rows_precede_fx(self):
        """QDII 全是 A 股，排在最前面的块里。"""
        trans_list = [
            _trans(FX_SUSDCNH_SYMBOL, 100000, 710000.0, price="7.1"),
            _trans("SZ161125", 1000, 1200.0, price="1.2", position=0.95),
            _trans("SZ162411", 1000, 1200.0, price="1.2", position=0.5),
        ]
        cny = PortfolioCnyTotal()
        table = build_portfolio_table(sort_trans_by_symbol(trans_list), cny=cny)
        symbols = [row.cells[0] for row in table["rows"]]
        self.assertEqual(symbols, ["SZ161125", "SZ162411", "USDCNH"])
        # 1200×0.95 + 1200×0.5 = 1740；(710000+1740)/7.1/1000 = 100.24 → 100
        self.assertEqual(table["rows"][2].cells[6], "$100K")

    def test_empty_list(self):
        self.assertEqual(sort_trans_by_symbol([]), [])
        self.assertEqual(sort_trans_by_symbol(None), [])


# ---------------------------------------------------------------------------
# 整张表
# ---------------------------------------------------------------------------


class BuildPortfolioTableTest(unittest.TestCase):
    """``EchoPortfolioParagraph()`` 的入口。"""

    def test_table_shape(self):
        table = build_portfolio_table([_trans("KWEB", 300, 24000.0, price="80.0")])
        self.assertEqual(table["id"], "myportfolio")
        self.assertEqual(table["title"], "个股盈亏")
        self.assertEqual(len(table["columns"]), 7)
        self.assertEqual(len(table["rows"]), 1)

    def test_none_and_empty(self):
        self.assertEqual(build_portfolio_table(None)["rows"], [])
        self.assertEqual(build_portfolio_table([])["rows"], [])

    def test_zero_record_rows_filtered(self):
        table = build_portfolio_table([
            _trans("KWEB", 300, 24000.0, price="80.0"),
            _trans("TLT", 100, 8000.0, price="80.0", records=0),
        ])
        self.assertEqual(len(table["rows"]), 1)


class FormatIntDisplayTest(unittest.TestCase):
    """``number_format($fVal)`` 单参数版：0 位小数 + 千分位。"""

    def test_thousands(self):
        self.assertEqual(format_int_display(1234567), "1,234,567")

    def test_negative_sign_before_digits(self):
        self.assertEqual(format_int_display(-1234567), "-1,234,567")

    def test_rounds_half_away_from_zero(self):
        """PHP 的 ``round`` 不是银行家舍入。"""
        self.assertEqual(format_int_display(2.5), "3")
        self.assertEqual(format_int_display(-2.5), "-3")

    def test_small_values(self):
        self.assertEqual(format_int_display(0.4), "0")
        self.assertEqual(format_int_display(999), "999")


# ---------------------------------------------------------------------------
# 合并段落（_myportfolio.php:85-119）
# ---------------------------------------------------------------------------


class MergeTransactionsTest(unittest.TestCase):
    """``_transEchoMergeParagraph()``：同符号跨分组合并。

    照抄两处 PHP 的不严谨：**连续假定**（非连续同符号会把上一个标的一起
    吞掉）与收尾那句泄漏出循环的 ``$strSymbol``。
    """

    def _merged(self, *specs):
        return merge_transactions_by_symbol(
            [_trans(symbol, shares, cost) for symbol, shares, cost in specs])

    def test_same_symbol_twice_merges(self):
        merge = self._merged(("KWEB", 100, 10.0), ("KWEB", 50, 5.0))
        self.assertEqual(list(merge), ["KWEB"])
        merged = merge["KWEB"]
        self.assertEqual(merged.get_total_shares(), 150)
        self.assertEqual(merged.get_total_cost(), 15.0)
        # Add() 每并一笔交易对象只让记录数 +1（不是加对方的记录数）
        self.assertEqual(merged.get_total_records(), 2)

    def test_same_symbol_three_times(self):
        merge = self._merged(("KWEB", 100, 10.0), ("KWEB", 50, 5.0),
                             ("KWEB", 25, 2.5))
        self.assertEqual(merge["KWEB"].get_total_shares(), 175)
        self.assertEqual(merge["KWEB"].get_total_records(), 3)

    def test_two_symbols_insertion_order(self):
        merge = self._merged(("KWEB", 100, 10.0), ("KWEB", 50, 5.0),
                             ("XOP", 30, 3.0), ("XOP", 70, 7.0))
        self.assertEqual(list(merge), ["KWEB", "XOP"])

    def test_single_occurrence_symbol_absent(self):
        """只出现一次的符号不进合并表 —— 每个符号只买过一份的人看不到这表。"""
        merge = self._merged(("KWEB", 100, 10.0), ("KWEB", 50, 5.0), ("SPY", 5, 0.5))
        self.assertEqual(list(merge), ["KWEB"])
        self.assertEqual(self._merged(("SPY", 5, 0.5), ("TLT", 3, 0.5)), {})

    def test_last_group_flush_uses_leaked_symbol(self):
        """末组收尾：PHP 用泄漏出循环的 ``$strSymbol``（最后一个符号）。

        连续前提下它恰好就是待结算组的符号 —— 碰巧对，但必须照抄。
        """
        merge = self._merged(("KWEB", 100, 10.0), ("KWEB", 50, 5.0),
                             ("XOP", 30, 3.0), ("XOP", 70, 7.0),
                             ("SPY", 5, 0.5))
        self.assertEqual(list(merge), ["KWEB", "XOP"])

    def test_non_consecutive_symbol_swallows_previous_group(self):
        """非连续同符号：第二个 KWEB 把 SPY 的合计量一并吞掉（照抄的 bug）。

        生产上不会发生（调用方传进来的已排序），但端口必须逐字复刻 ——
        PHP 逐例对照里也有一条钉住它。
        """
        merge = self._merged(("KWEB", 100, 10.0), ("SPY", 30, 3.0),
                             ("KWEB", 50, 5.0))
        self.assertEqual(list(merge), ["KWEB"])
        self.assertEqual(merge["KWEB"].get_total_shares(), 80)   # 30 + 50
        self.assertEqual(merge["KWEB"].get_total_records(), 2)
        self.assertNotIn("SPY", merge)

    def test_ref_less_trans_group_under_false(self):
        """ref 缺失时 ``GetSymbol()`` 回 ``False``，两个无引用交易并成一组。

        PHP 会把 false 铸成数组键 ``0``；端口保留 ``False`` 原值 ——
        该键只用于 ``switch`` 匹配，永不等于 KWEB/XOP，渲染无差别。
        """
        trans_list = []
        for shares, cost in ((100, 10.0), (50, 5.0)):
            trans = MyStockTransaction(None, "g1")
            trans.add_transaction(shares, cost)
            trans_list.append(trans)
        merge = merge_transactions_by_symbol(trans_list)
        self.assertEqual(list(merge), [False])
        self.assertEqual(merge[False].get_total_shares(), 150)
        self.assertEqual(merge[False].get_total_records(), 2)

    def test_empty_and_none(self):
        self.assertEqual(merge_transactions_by_symbol([]), {})
        self.assertEqual(merge_transactions_by_symbol(None), {})


class BuildMergeTableTest(unittest.TestCase):
    """``_echoMergeParagraph()``：三列合并表（代码 / 数量 / 测试）。"""

    def _merge(self, *specs):
        return merge_transactions_by_symbol(
            [_trans(symbol, shares, cost) for symbol, shares, cost in specs])

    def test_table_shape(self):
        table = build_merge_table(self._merge(("KWEB", 100, 10.0), ("KWEB", 50, 5.0)))
        self.assertEqual(table["id"], "merge")
        self.assertEqual(table["title"], "合并数量")
        self.assertEqual(len(table["columns"]), 3)
        self.assertEqual(len(table["rows"]), 1)

    def test_empty_merge_returns_none(self):
        """合并字典为空 → 整张表不渲染（PHP 的 ``count($arMerge) > 0`` 守卫）。"""
        self.assertIsNone(build_merge_table({}))
        self.assertIsNone(build_merge_table(self._merge(("SPY", 5, 0.5))))

    def test_kweb_row_has_three_cells(self):
        row = build_merge_table(
            self._merge(("KWEB", 100, 10.0), ("KWEB", 50, 5.0)))["rows"][0]
        self.assertEqual(row.cells, ["KWEB", "150", "650"])   # 150-200+700

    def test_xop_row_has_three_cells(self):
        row = build_merge_table(
            self._merge(("XOP", 30, 3.0), ("XOP", 70, 7.0)))["rows"][0]
        self.assertEqual(row.cells, ["XOP", "100", "100"])    # 100-00-00

    def test_other_symbol_has_two_cells(self):
        """KWEB/XOP 之外的符号只有两格 —— 照抄不补。"""
        row = build_merge_table(
            self._merge(("SPY", 30, 3.0), ("SPY", 70, 7.0)))["rows"][0]
        self.assertEqual(row.cells, ["SPY", "100"])

    def test_quantity_has_no_thousands_separator(self):
        """``strval()`` 不是 ``number_format()`` —— 不带千分位逗号。"""
        row = build_merge_table(
            self._merge(("SPY", 600000, 3.0), ("SPY", 700000, 7.0)))["rows"][0]
        self.assertEqual(row.cells[1], "1300000")

    def test_net_zero_rows_filtered_but_header_rendered(self):
        """合并后净额为 0：PHP 仍然渲染表头（``count`` 在过滤之前算）。"""
        table = build_merge_table(self._merge(("KWEB", 100, 10.0), ("KWEB", -100, -10.0)))
        self.assertIsNotNone(table)
        self.assertEqual(table["rows"], [])

    def test_first_cell_links_to_mystock_page(self):
        """首格是 mystock 链接，不是个股表首格那个交易明细链接。"""
        row = build_merge_table(
            self._merge(("KWEB", 100, 10.0), ("KWEB", 50, 5.0)))["rows"][0]
        self.assertEqual(row.href, "/woody/res/mystockcn.php?symbol=KWEB")
        self.assertEqual(row.cells[0], "KWEB")

    def test_name_fills_title(self):
        row = build_merge_table(
            self._merge(("KWEB", 100, 10.0), ("KWEB", 50, 5.0)),
            name_of=lambda symbol: "中概互联")["rows"][0]
        self.assertEqual(row.title, "中概互联")

    def test_ref_less_row_has_empty_link(self):
        """ref 缺失：``RefGetMyStockLink(false)`` 回空串，行里就是个空格子。"""
        trans_list = []
        for shares, cost in ((100, 10.0), (50, 5.0)):
            trans = MyStockTransaction(None, "g1")
            trans.add_transaction(shares, cost)
            trans_list.append(trans)
        table = build_merge_table(merge_transactions_by_symbol(trans_list))
        self.assertEqual(len(table["rows"]), 1)
        self.assertEqual(table["rows"][0].cells, ["", "150"])


# ---------------------------------------------------------------------------
# 第二个调用点（_stockaccount.php:207）：单分组原始数组
# ---------------------------------------------------------------------------


class RawGroupArrayTest(unittest.TestCase):
    """``EchoPortfolioParagraph($group->GetStockTransactionArray())``。

    ``_stockaccount.php`` 的 ``EchoStockTransaction()`` 传的是**单个分组
    自己的交易数组**（``stockgroupitem`` 的 DB 顺序），不过 A/H/US 排序 ——
    排序是 ``_myportfolio.php`` 那个调用方额外做的事。服务层因此保持
    agnostic：给什么顺序就渲染什么顺序。两个调用点的差别只在入参，
    不在服务（这也是它能被复用的原因）。
    """

    def test_renders_in_given_order(self):
        table = build_portfolio_table([
            _trans("TLT", 100, 8000.0, price="80.0"),
            _trans("KWEB", 300, 24000.0, price="80.0"),
        ])
        self.assertEqual([row.cells[0] for row in table["rows"]], ["TLT", "KWEB"])

    def test_merge_consumes_same_array(self):
        """合并段落与个股段落消费同一批交易（PHP 的 ``_echoPortfolio``）。"""
        trans_list = [_trans("KWEB", 100, 10.0), _trans("KWEB", 50, 5.0)]
        table = build_merge_table(merge_transactions_by_symbol(trans_list))
        self.assertEqual(table["rows"][0].cells, ["KWEB", "150", "650"])


# ---------------------------------------------------------------------------
# 路由（需要 flask，所以单独放在本文件末尾）
# ---------------------------------------------------------------------------


def _squeezed(html: str) -> str:
    """压掉标签之间的空白 —— Jinja 会在标签之间塞缩进。"""
    return re.sub(r"\s+", " ", html)


class PortfolioRouteTest(unittest.TestCase):
    """``/portfolio`` 页面的接线测试。

    用的是 ``web/routes/portfolio.py`` 里那份**写死的演示数据**，
    所以不连库、不联网。钉的是四件事：

    1. 页面 200，七列列头齐全；
    2. 三种行宽都在（2 / 6 / 7）—— 特别是清仓的那行只有两格；
    3. ``fx_susdcnh`` 那一格是美元折算 + 跳转链接；
    4. 页面明确标注"演示数据"（真实页面要登录，账号体系暂缓）。
    """

    @classmethod
    def setUpClass(cls):
        from web.app import create_app

        cls.app = create_app({"TESTING": True})
        cls.client = cls.app.test_client()
        cls.html = _squeezed(cls.client.get("/portfolio").get_data(as_text=True))
        cls.table = re.search(
            r'<table class="data-table portfolio-table".*?</table>', cls.html, re.S
        ).group(0)

    def test_status_ok(self):
        response = self.client.get("/portfolio")
        self.assertEqual(response.status_code, 200)

    def test_headers(self):
        for header in ("代码", "盈亏", "持仓", "数量", "平均价格", "涨幅", "测试"):
            self.assertIn(header, self.table)

    def test_row_widths(self):
        """2 / 6 / 7 三种行宽都要出现。"""
        widths = set()
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", self.table, re.S)[1:]:
            widths.add(len(re.findall(r"<td[^>]*>", row)))
        self.assertEqual(widths, {2, 6, 7})

    def test_cleared_position_has_two_cells(self):
        """清仓的 QQQ：只剩代码与盈亏两格。"""
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", self.table, re.S)
        for row in rows:
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
            if cells and "QQQ" in cells[0]:
                self.assertEqual(len(cells), 2)
                return
        self.fail("页面里没有 QQQ 行")

    def test_fx_row_has_dollar_and_link(self):
        """``fx_susdcnh``：美元折算文本 + 跳往 overnightcnh 的链接。"""
        self.assertRegex(self.table, r"\$\d+K")
        self.assertIn("overnightcnh=", self.table)

    def test_demo_banner_present(self):
        """必须让看页面的人知道这是演示数据。"""
        self.assertIn("演示数据", self.html)

    def test_merge_table_rendered(self):
        """合并段落：KWEB / SPY 各出现在两个分组里，于是并成一行。

        表头照渲染（PHP 的 ``count($arMerge) > 0`` 守卫），KWEB 的
        末列是 ``300-200+700=800``，SPY 合计 100 且只有两格。
        """
        self.assertIn("合并数量", self.html)
        match = re.search(
            r'<table class="data-table merge-table".*?</table>', self.html, re.S)
        self.assertIsNotNone(match, "页面上没有合并表")
        table = match.group(0)
        self.assertIn("代码", table)
        self.assertIn("800", table)
        # KWEB 三格行、SPY 两格行 —— 同一张表里两种行宽
        widths = set()
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S)[1:]:
            widths.add(len(re.findall(r"<td[^>]*>", row)))
        self.assertEqual(widths, {2, 3})

    def test_nav_entry(self):
        self.assertIn('href="/portfolio"', self.html)


if __name__ == "__main__":
    unittest.main()
