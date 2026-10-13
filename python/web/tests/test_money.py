"""资金汇总段落的测试（``_stock.php`` / ``_stockaccount.php`` → ``money_service.py``）。

**全部离线**：分组、引用对象、邮箱都是内存假实现。

## 这个段落最需要钉住的三件事

1. **美元 / 港币行用 ``empty()`` 判** —— ``empty(0.0)`` 为真，
   于是"持有量与盈亏都为 0"时整行不出。写成 ``is not None`` 会把空行放出来；
2. **先折算再出行** —— ``_EchoMoneyGroupData()`` 第一行就
   ``ConvertCurrency()``，顺序反了会读到 ``None``；
3. **测试列只对"全部"这一行出现**，且按会员邮箱分两种算法，
   两个魔法数字照抄。
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

from core.stockgroup import MyStockGroup  # noqa: E402
from web.services.money_service import (  # noqa: E402
    ADMIN_EMAIL,
    ADMIN_PROFIT_BASE,
    DISP_ALL_CN,
    MIX_EMAIL,
    MONEY_TABLE_ID,
    SINGLE_CURRENCY_TEXT,
    build_money_group_rows,
    build_money_row,
    build_money_table,
    money_columns,
    money_title,
    money_title_link,
)
from ui.uicolumn import columns_to_dicts  # noqa: E402


class FakeRef:
    """引用对象替身：符号 + 价格 + 币种判定。"""

    def __init__(self, symbol, price='10.0', is_a=False, is_h=False):
        self._symbol = symbol
        self._price = price
        self._is_a = is_a
        self._is_h = is_h

    def get_symbol(self):
        return self._symbol

    def get_price(self):
        return self._price

    def is_symbol_a(self):
        return self._is_a

    def is_symbol_h(self):
        return self._is_h

    def is_sina_future_cn(self):
        return False


class FakeFxRef:
    """汇率引用替身：只有一个 ``GetPrice()``。"""

    def __init__(self, price='7.1'):
        self._price = price

    def get_price(self):
        return self._price


def _group(group_id='g1', cn_shares=100, cn_cost=900.0, cn_price='10.0',
           us_shares=0, us_cost=0.0, us_price='470.0',
           hk_shares=0, hk_cost=0.0, hk_price='40.0'):
    """造一个分组：三个币种各一笔（数量为 0 的那一笔不出行）。"""
    refs = []
    if cn_shares:
        refs.append(FakeRef('SH600104', cn_price, is_a=True))
    if us_shares:
        refs.append(FakeRef('SPY', us_price))
    if hk_shares:
        refs.append(FakeRef('HK00700', hk_price, is_h=True))
    group = MyStockGroup(group_id, refs)
    if cn_shares:
        group.set_value('SH600104', 1, cn_shares, cn_cost)
    if us_shares:
        group.set_value('SPY', 1, us_shares, us_cost)
    if hk_shares:
        group.set_value('HK00700', 1, hk_shares, hk_cost)
    return group


class ColumnsTest(unittest.TestCase):
    """``EchoMoneyParagraphs()`` 的六列与标题。"""

    def test_six_columns(self):
        """列头文本看 ``columns_to_dicts()`` 之后的结果 —— 前缀在那一步才拼上。"""
        columns = columns_to_dicts(money_columns())
        self.assertEqual(len(columns), 6)
        self.assertEqual([column['text'] for column in columns],
                         ['分组名称', '全部盈亏', '全部持仓',
                          '单一货币盈亏', '单一货币持仓', '测试'])

    def test_prefixes(self):
        """前两列带 ``全部``，中间两列带 ``单一货币``。"""
        columns = money_columns()
        self.assertEqual(columns[1].prefix, DISP_ALL_CN)
        self.assertEqual(columns[3].prefix, SINGLE_CURRENCY_TEXT)

    def test_title_is_plain_text(self):
        """``$profit_col->GetDisplay()`` 的文本部分 —— 加粗与链接归模板。"""
        self.assertEqual(money_title(), '盈亏')

    def test_title_link_points_to_group_page(self):
        self.assertIn('mystockgroupcn.php', money_title_link())
        self.assertIn('股票分组', money_title_link())


class MoneyRowTest(unittest.TestCase):
    """``_echoMoneyItem()``：格数不固定，4 / 5 / 6 都有可能。"""

    def test_normal_row_has_five_cells(self):
        row = build_money_row('分组甲', 1000.0, 100.0, 34370.0, 1520.0)
        self.assertEqual(row.cells, ['分组甲', '1520', '34370', '100', '1000'])

    def test_cell_order_is_profit_before_value(self):
        """``$ar[] = $strProfit; $ar[] = $strValue;`` —— 盈亏在市值之前。"""
        row = build_money_row('g', 1000.0, 100.0, 34370.0, 1520.0)
        self.assertEqual(row.cells[3], '100')
        self.assertEqual(row.cells[4], '1000')

    def test_convert_profit_comes_first(self):
        """第一格之后就是"折人民币盈亏"，比"折人民币市值"还靠前。"""
        row = build_money_row('g', 1000.0, 100.0, 34370.0, 1520.0)
        self.assertEqual(row.cells[1], '1520')


class MoneyRowEmptyBranchTest(unittest.TestCase):
    """"补空格子"那两个分支。

    PHP 8 下 ``GetNumberDisplay()`` 对任何输入都不返回空串（``0.0`` 产出
    ``<font color=grey>0</font>``），所以这两个分支在生产上是**死代码**。
    本版照抄结构，而 Python 的 ``format_number_display(None)`` 返回空串，
    于是退化路径（没折算过）能走进去 —— 测试把它钉住。
    """

    def test_all_none_gives_three_cells(self):
        row = build_money_row('g', None, None, None, None)
        self.assertEqual(row.cells, ['g', '', ''])

    def test_only_value_gives_five_cells_with_two_blanks(self):
        row = build_money_row('g', 100.0, None, None, None)
        self.assertEqual(row.cells, ['g', '', '', '', '100'])

    def test_value_and_profit_gives_five_cells(self):
        row = build_money_row('g', 100.0, 20.0, None, None)
        self.assertEqual(row.cells, ['g', '', '', '20', '100'])

    def test_convert_value_present_shortens_nothing(self):
        """有折人民币市值时不补空格子 —— 直接放它自己。"""
        row = build_money_row('g', 100.0, 20.0, 50.0, 5.0)
        self.assertEqual(row.cells, ['g', '5', '50', '20', '100'])


class TestColumnTest(unittest.TestCase):
    """测试列：只对"全部"行出现，按邮箱分两种算法。"""

    def test_admin_email_subtracts_base(self):
        row = build_money_row(DISP_ALL_CN, 1000.0, 100.0, 1000.0, 100.0,
                              member_email_of=lambda: ADMIN_EMAIL)
        self.assertEqual(row.cells[5], '%.2f' % (100.0 - ADMIN_PROFIT_BASE))

    def test_mix_email_halves_then_subtracts(self):
        row = build_money_row(DISP_ALL_CN, 1000.0, 100.0, 1000.0, 100.0,
                              member_email_of=lambda: MIX_EMAIL)
        # format_number_display 会去掉尾零，所以 '-50.00' 显示成 '-50'
        self.assertEqual(row.cells[5], '-50')

    def test_other_email_has_no_test_cell(self):
        row = build_money_row(DISP_ALL_CN, 1000.0, 100.0, 1000.0, 100.0,
                              member_email_of=lambda: 'someone@example.com')
        self.assertEqual(len(row.cells), 5)

    def test_not_logged_in_has_no_test_cell(self):
        """没注入邮箱时与"未登录"一致 —— 没有测试列。"""
        row = build_money_row(DISP_ALL_CN, 1000.0, 100.0, 1000.0, 100.0)
        self.assertEqual(len(row.cells), 5)

    def test_named_group_never_gets_test_cell(self):
        """只有 ``全部`` 那一行有测试列 —— 判断条件是 ``$strGroup == DISP_ALL_CN``。"""
        row = build_money_row('分组甲', 1000.0, 100.0, 1000.0, 100.0,
                              member_email_of=lambda: ADMIN_EMAIL)
        self.assertEqual(len(row.cells), 5)


class MoneyGroupRowsTest(unittest.TestCase):
    """``_EchoMoneyGroupData()``：一个分组 1~3 行。"""

    def test_cny_row_always_present(self):
        rows = build_money_group_rows(_group(cn_shares=100), '7.1', '0.9',
                                      group_link_of=lambda gid: '分组甲')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].cells[0], '分组甲')

    def test_usd_row_appears_when_non_zero(self):
        rows = build_money_group_rows(
            _group(cn_shares=100, us_shares=10, us_cost=4500.0), '7.1', '0.9',
            group_link_of=lambda gid: '分组甲')
        self.assertEqual(len(rows), 2)
        self.assertIn('$', rows[1].cells[0])

    def test_usd_row_hidden_when_zero(self):
        """``empty(0.0)`` 为真 —— 没买美股就没有那一行。"""
        group = _group(cn_shares=100)
        group.multi_amount.f_usd = 0.0
        group.multi_profit.f_usd = 0.0
        rows = build_money_group_rows(group, '7.1', '0.9')
        self.assertEqual(len(rows), 1)

    def test_usd_row_shown_when_only_profit_non_zero(self):
        """``||`` —— 持有量为 0 但盈亏非零时也出（卖光了但还有浮盈）。"""
        group = _group(cn_shares=100, us_shares=1, us_cost=4500.0)
        group.multi_amount.f_usd = 0.0       # 已清仓
        group.multi_profit.f_usd = 200.0     # 但盈亏留着
        rows = build_money_group_rows(group, '7.1', '0.9')
        self.assertEqual(len(rows), 2)

    def test_hkd_row_uses_column_text(self):
        """港币行的首格是 ``TableColumnHKD`` 的文本（``港币$``），不是链接。"""
        rows = build_money_group_rows(
            _group(cn_shares=100, hk_shares=10, hk_cost=380.0), '7.1', '0.9')
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1].cells[0], '港币$')

    def test_all_group_has_no_group_id(self):
        """``$strGroupId = $group->GetGroupId()`` 为假 → 首格是 ``全部``。"""
        group = _group(group_id=False, cn_shares=100)
        rows = build_money_group_rows(group, '7.1', '0.9')
        self.assertEqual(rows[0].cells[0], DISP_ALL_CN)

    def test_usd_row_of_all_group_is_plain_dollar(self):
        group = _group(group_id=False, cn_shares=100, us_shares=10, us_cost=4500.0)
        rows = build_money_group_rows(group, '7.1', '0.9')
        self.assertEqual(rows[1].cells[0], '$')

    def test_convert_happens_before_rows(self):
        """先 ``ConvertCurrency()`` 再出行 —— 顺序反了会读到 ``None``。"""
        group = _group(cn_shares=100)
        self.assertIsNone(group.multi_amount.f_convert_cny)   # 折算前是 None
        build_money_group_rows(group, '7.1', '0.9')
        self.assertIsNotNone(group.multi_amount.f_convert_cny)

    def test_convert_values(self):
        """三个数字按 PHP 的公式核一遍。

        A 股 100 股 @10（成本 900）：市值 1000、盈亏 100；
        美股 10 股 @470（成本 4500）：市值 4700、盈亏 200；
        汇率 7.1 / 0.9。
        """
        rows = build_money_group_rows(
            _group(cn_shares=100, cn_cost=900.0, us_shares=10, us_cost=4500.0),
            '7.1', '0.9')
        # 人民币行：折盈亏 100 + 200*7.1 = 1520；折市值 1000 + 4700*7.1 = 34370
        self.assertEqual(rows[0].cells[1], '1520')
        self.assertEqual(rows[0].cells[2], '34370')
        # 美元行：折盈亏 1520/7.1，折市值 34370/7.1（都拿**折人民币总数**反除）
        self.assertEqual(rows[1].cells[1], '%.2f' % (1520.0 / 7.1))
        self.assertEqual(rows[1].cells[2], '%.2f' % (34370.0 / 7.1))


class MoneyTableTest(unittest.TestCase):
    """``EchoMoneyParagraphs()``：遍历分组、取汇率。"""

    def test_rows_from_all_groups(self):
        table = build_money_table(
            [_group(group_id='g1', cn_shares=100),
             _group(group_id='g2', cn_shares=50, cn_cost=450.0)],
            group_link_of=lambda gid: '分组' + gid)
        self.assertEqual(len(table['rows']), 2)
        self.assertEqual(table['rows'][0].cells[0], '分组g1')
        self.assertEqual(table['rows'][1].cells[0], '分组g2')

    def test_rates_come_from_refs(self):
        """``$uscny_ref ? $uscny_ref->GetPrice() : false``。"""
        table = build_money_table([_group(cn_shares=100, us_shares=10, us_cost=4500.0)],
                                  usdcny_ref=FakeFxRef('7.1'))
        # 没给港币汇率 → 折港币是 0.0，港币行不出
        self.assertEqual(len(table['rows']), 2)
        self.assertEqual(table['rows'][1].cells[2], '%.2f' % (34370.0 / 7.1))

    def test_no_rates_still_renders_cny_row(self):
        table = build_money_table([_group(cn_shares=100)])
        self.assertEqual(len(table['rows']), 1)
        self.assertEqual(table['rows'][0].cells[2], '1000')

    def test_no_groups_gives_no_rows(self):
        table = build_money_table([])
        self.assertEqual(table['rows'], [])

    def test_table_id_and_columns(self):
        table = build_money_table([])
        self.assertEqual(table['id'], MONEY_TABLE_ID)
        self.assertEqual(len(table['columns']), 6)


if __name__ == "__main__":
    unittest.main()
