"""`python/auto/uicolumn.py` 的单元测试。

对应 PHP ``php/ui/table.php``、``stocktable.php``、``stockcomm.php``、
``stockdisp.php``。

这些用例**全部是纯数据断言**（文本 / 宽度 / 颜色名 / 排序结果），
不碰 Flask、不联网：列定义本来就该与渲染引擎无关。
"""

from __future__ import annotations

import unittest

from uicolumn import (
    COLUMN_PRESETS,
    HOLDINGS_NETVALUE_DIFF,
    POS_NETVALUE_DIFF,
    STOCK_DISP_EST,
    STOCK_DISP_NETVALUE,
    STOCK_DISP_OFFICIAL,
    STOCK_DISP_PREMIUM,
    STOCK_DISP_SYMBOL,
    TABLE_COMMON_DISPLAY,
    TableColumn,
    columns_to_dicts,
    column_change,
    column_date,
    column_est,
    column_group_name,
    column_symbol,
    is_table_common_display,
    ref_get_table_column_net_value,
    ref_has_data,
    ref_sort_by_numeric,
    ref_sort_by_symbol,
    table_column_get_last_width,
    table_column_get_official_est,
    table_column_get_total_width,
)


class FakeRef:
    """够用的引用对象替身。

    只实现列选择会问到的三个动作。``count_net_value`` 与
    ``is_sina_future_except_gold_cn`` 用构造参数控制，用来覆盖三条分支。
    """

    def __init__(self, symbol: str, count: int = 0, future: bool = False, has_data: bool = True):
        self.symbol = symbol
        self._count = count
        self._future = future
        self._has_data = has_data

    def get_symbol(self):
        return self.symbol

    def get_display(self):
        return self.symbol

    def count_net_value(self):
        return self._count

    def is_sina_future_except_gold_cn(self):
        return self._future

    def has_data(self):
        return self._has_data


class DisplayConstantTest(unittest.TestCase):
    """``stockdisp.php`` 那一份只有 define 的文件：值不能漂移。"""

    def test_display_constants(self):
        self.assertEqual(STOCK_DISP_SYMBOL, "代码")
        self.assertEqual(STOCK_DISP_EST, "EST")
        self.assertEqual(STOCK_DISP_NETVALUE, "净值")
        self.assertEqual(STOCK_DISP_OFFICIAL, "官方")
        self.assertEqual(STOCK_DISP_PREMIUM, "溢价")

    def test_thresholds(self):
        """反算仓位的触发阈值：净值类 4%、持仓型 1%。"""
        self.assertEqual(POS_NETVALUE_DIFF, 4.0)
        self.assertEqual(HOLDINGS_NETVALUE_DIFF, 1.0)


class TableColumnTest(unittest.TestCase):
    """``TableColumn`` 的四个方法与 PHP 同名的对应关系。"""

    def test_defaults_match_php_signature(self):
        """PHP 签名是 ``($strText='', $iWidth=80, $strColor=false, $strPrefix=false)``。"""
        column = TableColumn()
        self.assertEqual(column.text, "")
        self.assertEqual(column.width, 80)
        self.assertEqual(column.color, "")
        self.assertEqual(column.prefix, "")

    def test_prefix_is_prepended_not_appended(self):
        """PHP 是 ``$this->strText = $strPrefix.$this->strText`` —— 前缀在前。"""
        column = TableColumn(text="EST", prefix="官方")
        self.assertEqual(column.text_with_prefix, "官方EST")
        self.assertEqual(column.display, "官方EST")

    def test_display_is_plain_text_not_html(self):
        """`GetDisplay()` 在 PHP 返回 ``<b>文本</b>``；这里必须是纯文本。

        颜色也是数据而不是 ``<font color>``：这条盯着"列定义里不许出现 HTML"。
        """
        column = TableColumn(text="价格", color="blue")
        self.assertEqual(column.display, "价格")
        self.assertNotIn("<", column.display)
        self.assertEqual(column.color, "blue")

    def test_to_dict_shape_matches_web_services(self):
        """与 ``web/services/history_service._column()`` 的形状完全一致，
        这样两种写法可以在同一个 columns 列表里混用。
        """
        self.assertEqual(
            column_symbol().to_dict(),
            {"text": "代码", "color": "maroon", "width": 80},
        )

    def test_columns_to_dicts(self):
        self.assertEqual(
            columns_to_dicts([column_date(), column_change()]),
            [
                {"text": "日期", "color": "", "width": 100},
                {"text": "涨幅", "color": "", "width": 70},
            ],
        )

    def test_is_frozen(self):
        """``frozen=True``：列定义是常量预置，构造完不该还能改。"""

        with self.assertRaises(Exception):
            column_symbol().width = 999


class PresetTest(unittest.TestCase):
    """30 个列预置的文本 / 宽度 / 颜色逐条对照 PHP。"""

    def test_est_columns_share_width_and_color(self):
        """官方 / 参考 / 实时 EST 都是 ``EST`` 加前缀，继承 80 与 magenta。"""
        for column in (column_est(), column_est("官方"), column_est("参考")):
            self.assertEqual(column.width, 80)
            self.assertEqual(column.color, "magenta")
        self.assertEqual(column_est("官方").text_with_prefix, "官方EST")
        self.assertEqual(column_est("参考").text_with_prefix, "参考EST")

    def test_official_est_helper(self):
        self.assertEqual(column_est(STOCK_DISP_OFFICIAL).text_with_prefix, "官方EST")
        self.assertEqual(table_column_get_official_est(), "官方EST")

    def test_group_name_is_a_prefix_concatenation(self):
        """`TableColumnGroupName` 调的是 ``parent::__construct('分组', 110)``，
        而 Name 的第一个参数是 ``$strPrefix``，所以文本是"分组名称"而不是"分组"。
        """
        column = column_group_name()
        self.assertEqual(column.text_with_prefix, "分组名称")
        self.assertEqual(column.width, 110)

    def test_date_column_width_is_100(self):
        self.assertEqual(column_date().width, 100)
        self.assertEqual(column_date("日期", chinese=False).text_with_prefix, "日期Date")

    def test_registry_covers_every_php_class(self):
        """``COLUMN_PRESETS`` 必须覆盖 ``stocktable.php`` 与 ``table.php`` 里
        的全部 34 个类，否则"按 PHP 类名反查"这个用途就漏了。
        """
        expected = [
            "TableColumn", "TableColumnDate", "TableColumnIP", "TableColumnTime",
            "TableColumnAmount", "TableColumnCalibration", "TableColumnChange",
            "TableColumnError", "TableColumnEst", "TableColumnOfficialEst",
            "TableColumnFairEst", "TableColumnRealtimeEst", "TableColumnHedge",
            "TableColumnHolding", "TableColumnName", "TableColumnGroupName",
            "TableColumnNetValue", "TableColumnPercentage", "TableColumnPosition",
            "TableColumnPremium", "TableColumnPrice", "TableColumnProfit",
            "TableColumnQuantity", "TableColumnRatio", "TableColumnRemark",
            "TableColumnSettlePrice", "TableColumnShare", "TableColumnStock",
            "TableColumnSymbol", "TableColumnTest", "TableColumnTurnover",
            "TableColumnHKD", "TableColumnRMB", "TableColumnUSD",
        ]
        self.assertEqual(sorted(COLUMN_PRESETS), sorted(expected))

    def test_every_preset_produces_a_column(self):
        """注册表里的每个工厂都能无参调用并产出 ``TableColumn``。

        ``TableColumnStock`` 需要显示文本，单独给。
        """
        for name, factory in COLUMN_PRESETS.items():
            if name == "TableColumnStock":
                continue
            column = factory()
            self.assertIsInstance(column, TableColumn, name)


class WidthTest(unittest.TestCase):
    """宽度辅助：最后一列吃剩余宽度。"""

    def test_total_width(self):
        columns = [column_date(), column_change(), column_symbol()]
        self.assertEqual(table_column_get_total_width(columns), 100 + 70 + 80)

    def test_last_width_takes_layout_width_explicitly(self):
        """PHP 是全局变量 ``LayoutGetDisplayWidth()``；这里改成显式入参。
        ``php/layout.php`` 属于未迁移的布局层，本层不去猜屏幕宽度。
        """
        columns = [column_date(), column_symbol()]
        self.assertEqual(table_column_get_last_width(columns, 980), 980 - 180)

    def test_empty_columns(self):
        self.assertEqual(table_column_get_total_width([]), 0)

    def test_is_table_common_display(self):
        """``start == 0`` 且 ``num <= 10`` 时不显示翻页。"""
        self.assertTrue(is_table_common_display(0, 10))
        self.assertFalse(is_table_common_display(0, 11))
        self.assertFalse(is_table_common_display(10, 5))
        self.assertEqual(TABLE_COMMON_DISPLAY, 10)


class NetValueColumnTest(unittest.TestCase):
    """``RefGetTableColumnNetValue()``：三条分支，顺序有意义。"""

    def test_fund_shows_net_value(self):
        column = ref_get_table_column_net_value(FakeRef("SZ161130", count=3))
        self.assertEqual(column.text_with_prefix, "SZ161130净值")
        self.assertEqual(column.color, "olive")

    def test_future_shows_settle_price(self):
        """新浪期货（黄金除外）没有净值概念，显示"结算价"。"""
        column = ref_get_table_column_net_value(FakeRef("hf_CL", future=True))
        self.assertEqual(column.text_with_prefix, "hf_CL结算价")
        self.assertEqual(column.width, 90)

    def test_others_show_price(self):
        column = ref_get_table_column_net_value(FakeRef("QQQ"))
        self.assertEqual(column.text_with_prefix, "QQQ价格")
        self.assertEqual(column.width, 90)
        self.assertEqual(column.color, "blue")

    def test_net_value_wins_over_future(self):
        """既是基金又是期货时以净值优先 —— PHP 的 if / elseif 顺序。"""
        column = ref_get_table_column_net_value(FakeRef("X", count=1, future=True))
        self.assertEqual(column.text_with_prefix, "X净值")

    def test_camel_case_alias_objects_are_supported(self):
        """引用对象保留了 PHP 原名别名，两种写法都要能选中分支。"""

        class CamelRef:
            def GetSymbol(self):
                return "USCNY"

            def GetDisplay(self):
                return "USCNY"

            def CountNetValue(self):
                return 0

            def IsSinaFutureExceptGoldCN(self):
                return False

        self.assertEqual(
            ref_get_table_column_net_value(CamelRef()).text_with_prefix, "USCNY价格"
        )


class RefHelperTest(unittest.TestCase):
    """``stockcomm.php`` 的三个 ref 辅助。"""

    def test_ref_has_data_handles_none(self):
        """PHP 的 ``RefHasData`` 第一件事就是 ``if ($ref)`` —— 允许传空。"""
        self.assertFalse(ref_has_data(None))
        self.assertTrue(ref_has_data(FakeRef("QQQ")))
        self.assertFalse(ref_has_data(FakeRef("QQQ", has_data=False)))

    def test_sort_by_numeric_is_ascending(self):
        refs = [FakeRef(symbol) for symbol in ("C", "A", "B")]
        values = {"C": 3.0, "A": 1.0, "B": 2.0}
        self.assertEqual(
            [ref.get_symbol() for ref in ref_sort_by_numeric(refs, lambda ref: values[ref.get_symbol()])],
            ["A", "B", "C"],
        )

    def test_sort_by_numeric_keeps_duplicates(self):
        """**与 PHP 不同**：PHP 的 ``RefSortByNumeric`` 用 symbol 做键，
        同符号会被静默覆盖。这里保留重复项 —— 排序函数悄悄丢行会让
        "少了两行"变成查不出来的 bug。
        """
        refs = [FakeRef("A"), FakeRef("A"), FakeRef("B")]
        sorted_refs = ref_sort_by_numeric(refs, lambda ref: 0.0)
        self.assertEqual(len(sorted_refs), 3)

    def test_sort_by_numeric_is_stable(self):
        """同值保持原顺序（PHP 的 ``asort`` 不保证稳定）。"""
        refs = [FakeRef("C"), FakeRef("A"), FakeRef("B")]
        self.assertEqual(
            [ref.get_symbol() for ref in ref_sort_by_numeric(refs, lambda ref: 1.0)],
            ["C", "A", "B"],
        )

    def test_sort_by_numeric_tolerates_missing_values(self):
        """取不到数值的按 0 处理，不让页面挂。"""
        refs = [FakeRef("B"), FakeRef("A")]
        self.assertEqual(
            [ref.get_symbol() for ref in ref_sort_by_numeric(refs, lambda ref: None)],
            ["B", "A"],
        )

    def test_sort_by_symbol_dedupes_and_keeps_first(self):
        """``RefSortBySymbol`` **本来就要去重**（PHP 用 ksort + 同键跳过），
        同符号保留**第一个**。
        """
        first, second = FakeRef("B"), FakeRef("B")
        last = FakeRef("A")
        refs = [FakeRef("C"), first, second, last]

        sorted_refs = ref_sort_by_symbol(refs)

        self.assertEqual([ref.get_symbol() for ref in sorted_refs], ["A", "B", "C"])
        self.assertIs(sorted_refs[1], first)
        self.assertNotIn(second, sorted_refs)

    def test_sort_by_symbol_order(self):
        refs = [FakeRef("SZ161130"), FakeRef("SH513100"), FakeRef("QQQ")]
        self.assertEqual(
            [ref.get_symbol() for ref in ref_sort_by_symbol(refs)],
            ["QQQ", "SH513100", "SZ161130"],
        )


if __name__ == "__main__":
    unittest.main()
