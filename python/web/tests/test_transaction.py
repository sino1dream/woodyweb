"""交易明细段落的测试（``php/ui/transactionparagraph.php`` → ``transaction_service.py``）。

**全部离线**：引用对象、净值查询、记录都是内存假实现。

## 这个段落最需要钉住的三件事

1. **操作格在"只读且非管理员"时是 ``' '``** —— 一个空格，不是空串。
   PHP 的 ``$strEdit.' '.$strDelete`` 两边都清空后留下的就是它；
   补成空串会让那一列在页面上少一个占位。
2. **``$strNetValue != $strPrice`` 是 PHP 松散比较** —— 两边都是数据库里
   的字符串，``'1.2340'`` 与 ``'1.234'`` **数值相等**（PHP 判不等为假）。
   Python 的字符串比较会判成"变了"，于是每一行都多出一个校准链接。
3. **QDII 取上一交易日的净值** —— ``GetClosePrev`` 而不是 ``GetClose``，
   因为 QDII 净值当天收盘后才公布。
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

from web.services.transaction_service import (  # noqa: E402
    EDIT_TRANSACTION_PAGE,
    OPERATION_CELL_INDEX,
    REMARK_CELL_INDEX,
    CLEAR_REMARK_TEXT,
    SUBMIT_TRANSACTION_PAGE,
    TABLE_COMMON_DISPLAY,
    TRANSACTION_TABLE_ID,
    _php_ne,
    _php_str,
    build_transaction_row,
    build_transaction_rows,
    build_transaction_table,
    transaction_columns,
)


class FakeRef:
    """引用对象替身：只留本段落用到的五个方法。

    ``get_price_display`` 按 PHP 的 ``GetPriceDisplay($disp, $prev, $precision)``
    签名拼字符串，于是**精度**这一路能被断言到。
    """

    def __init__(self, symbol='KWEB', stock_id='11', display=None,
                 is_fund=False, precision=2):
        self._symbol = symbol
        self._stock_id = stock_id
        self._display = display
        self._is_fund = is_fund
        self._precision = precision

    def get_symbol(self):
        return self._symbol

    def get_stock_id(self):
        return self._stock_id

    def get_display(self):
        return self._display if self._display else self._symbol

    def is_fund_a(self):
        return self._is_fund

    def get_precision(self):
        return self._precision

    def get_price_display(self, disp=None, prev=None, precision=None):
        digits = precision if precision is not None else self._precision
        return ("%%.%df" % digits) % disp


def _record(**kwargs):
    record = {'id': '34', 'filled': '2026-10-13 09:30:00', 'quantity': '100',
              'price': '1.2340', 'fees': '5.00', 'remark': ''}
    record.update(kwargs)
    return record


class ColumnsTest(unittest.TestCase):
    """``EchoTransactionParagraph()`` 的七列。"""

    def test_seven_columns(self):
        columns = transaction_columns()
        self.assertEqual(len(columns), 7)
        self.assertEqual([column.text for column in columns],
                         ['日期', '代码', '数量', '价格', '费用', '备注', '操作'])

    def test_fees_column_width_is_sixty(self):
        """``new TableColumn('费用', 60)`` —— 无名列，宽度写死。"""
        self.assertEqual(transaction_columns()[4].width, 60)

    def test_operation_column_uses_default_width(self):
        """``new TableColumn('操作')`` 没给宽度 —— 走构造函数默认的 80。"""
        self.assertEqual(transaction_columns()[6].width, 80)


class RowBasicTest(unittest.TestCase):
    """前五列：日期 / 代码 / 数量 / 价格 / 费用。"""

    def test_first_five_cells(self):
        row = build_transaction_row(FakeRef(display='ES'), _record())
        self.assertEqual(row.cells[0], '2026-10-13')
        self.assertEqual(row.cells[1], 'ES')          # GetDisplay()，不是符号
        self.assertEqual(row.cells[2], '100')         # 数量原样，不格式化
        self.assertEqual(row.cells[3], '1.23')
        self.assertEqual(row.cells[4], '5')

    def test_price_precision_uses_get_precision(self):
        row = build_transaction_row(FakeRef(precision=3), _record(price='1.2345'))
        self.assertEqual(row.cells[3], '1.234')

    def test_fund_a_forces_four_digits(self):
        """``IsFundA() ? 4 : GetPrecision()`` —— A 股基金固定四位。"""
        row = build_transaction_row(FakeRef(is_fund=True, precision=3),
                                    _record(price='1.23456'))
        self.assertEqual(row.cells[3], '1.2346')

    def test_missing_space_in_filled_gives_empty(self):
        """``GetSqlTransactionDate()`` 找不到空格时回 ``None``（PHP 的 false），
        ``TableRow.add()`` 再把它归一成 ``''`` —— 与 PHP echo false 一致。
        """
        row = build_transaction_row(FakeRef(), _record(filled='2026-10-13'))
        self.assertEqual(row.cells[0], '')

    def test_html_cells_are_marked(self):
        """备注格与操作格是 HTML，模板按 ``html_indexes`` 不转义。"""
        row = build_transaction_row(FakeRef(), _record())
        self.assertEqual(row.html_indexes, [REMARK_CELL_INDEX, OPERATION_CELL_INDEX])


class RemarkCellTest(unittest.TestCase):
    """备注格：清空链接 + 原文 + 校准链接。"""

    def test_read_only_returns_plain_remark(self):
        """只读时整段跳过 —— 别人的分组里连清空链接都不该出现。"""
        row = build_transaction_row(FakeRef(), _record(remark='申购 3'),
                                    read_only=True)
        self.assertEqual(row.cells[5], '申购 3')

    def test_empty_remark_has_no_clear_link(self):
        row = build_transaction_row(FakeRef(), _record(remark=''))
        self.assertEqual(row.cells[5], '')

    def test_remark_without_order_keyword_has_only_clear_link(self):
        """``strpos($strRemark, '申购')`` 为假时不查净值、不补校准链接。"""
        row = build_transaction_row(FakeRef(), _record(remark='打新'))
        self.assertIn(CLEAR_REMARK_TEXT, row.cells[5])
        self.assertNotIn('adjust=', row.cells[5])
        self.assertIn('打新', row.cells[5])

    def test_clear_link_wraps_remark_in_confirm_text(self):
        """问句是 ``'确认清空'.'备注'.'：'.$strRemark.'？'``。

        **确认**两个字属于问句、不属于动作名 —— 初版移植把它漏了
        （写成 ``'清空备注：…'``），单测也照着错的写，是 PHP 逐例对照
        ``_php_transaction_compare.py`` 抓出来的。
        """
        row = build_transaction_row(FakeRef(), _record(remark='打新'))
        self.assertIn("confirm('确认清空备注：打新？')", row.cells[5])
        self.assertIn('>清空</a>', row.cells[5])

    def test_clear_link_comes_before_remark(self):
        """PHP 是 ``GetOnClickLink(...).$strRemark`` —— 链接在前。"""
        row = build_transaction_row(FakeRef(), _record(remark='打新'))
        self.assertTrue(row.cells[5].startswith('<a href='))
        self.assertTrue(row.cells[5].endswith('打新'))


class CalibrationLinkTest(unittest.TestCase):
    """校准链接：只有"含申购"且"净值与价格不同"时才出现。"""

    def _row(self, symbol='KWEB', price='1.2340', net_value='1.2340', **kwargs):
        calls = []

        def net_value_of(stock_id, date, prev):
            calls.append((stock_id, date, prev))
            return net_value

        row = build_transaction_row(FakeRef(symbol=symbol),
                                    _record(remark='申购 3', price=price),
                                    net_value_of=net_value_of, **kwargs)
        return row, calls

    def test_qdii_symbol_uses_previous_day(self):
        """QDII 净值收盘后才公布 —— 取上一交易日（``GetClosePrev``）。"""
        _, calls = self._row(symbol='SZ161125')
        self.assertEqual(calls, [('11', '2026-10-13', True)])

    def test_ordinary_symbol_uses_same_day(self):
        _, calls = self._row(symbol='SH600104')
        self.assertEqual(calls, [('11', '2026-10-13', False)])

    def test_numerically_equal_value_has_no_link(self):
        """``'1.2340' != '1.234'`` 在 PHP 里是**假** —— 数值相等。"""
        row, _ = self._row(price='1.234', net_value='1.2340')
        self.assertNotIn('adjust=', row.cells[5])

    def test_different_value_gets_link(self):
        row, _ = self._row(price='1.234', net_value='1.300')
        self.assertIn('adjust=34&netvalue=1.300', row.cells[5])
        self.assertIn("confirm('确认校准到净值: 1.300？')", row.cells[5])

    def test_missing_net_value_still_gets_link_with_empty_param(self):
        """PHP 查不到净值时 ``$strNetValue`` 为 false，照样拼链接、参数为空。

        ``false != '1.234'`` 为真，于是链接照出 —— 只是 ``netvalue=`` 后面
        是空串。注意 Python 的 ``str(None)`` 会得 ``'None'``，必须走
        :func:`_php_str`。
        """
        row, _ = self._row(price='1.234', net_value=None)
        self.assertIn('adjust=34&netvalue=', row.cells[5])
        self.assertNotIn('None', row.cells[5])
        self.assertIn("confirm('确认校准到净值: ？')", row.cells[5])

    def test_without_net_value_lookup_no_calibration_link(self):
        """没注入净值查询时**不补**校准链接 —— 与查不到数据不是一回事。"""
        row = build_transaction_row(FakeRef(), _record(remark='申购 3'))
        self.assertNotIn('adjust=', row.cells[5])


class OperationCellTest(unittest.TestCase):
    """操作格：``$strEdit.' '.$strDelete``。"""

    def test_read_only_and_not_admin_gives_single_space(self):
        """两边都清空后 PHP 留下的是**一个空格**，不是空串。"""
        row = build_transaction_row(FakeRef(), _record(), read_only=True, admin=False)
        self.assertEqual(row.cells[6], ' ')

    def test_read_only_admin_keeps_delete(self):
        """只读时管理员**仍能看到**删除链接（前缀一个空格）。"""
        row = build_transaction_row(FakeRef(), _record(), read_only=True, admin=True)
        self.assertTrue(row.cells[6].startswith(' <a href='))
        self.assertIn('删除', row.cells[6])

    def test_owner_sees_edit_and_delete(self):
        row = build_transaction_row(FakeRef(), _record(), read_only=False)
        self.assertIn(EDIT_TRANSACTION_PAGE, row.cells[6])
        self.assertIn('修改', row.cells[6])
        self.assertIn('删除', row.cells[6])

    def test_delete_confirm_text_identifies_the_record(self):
        """确认文字是"日期 + 空格 + 数量 + 交易记录"。

        同一天同数量会有两条记录，靠它区分删的是哪一条。
        """
        row = build_transaction_row(FakeRef(), _record(), read_only=False)
        self.assertIn("confirm('确认删除2026-10-13 100交易记录？')", row.cells[6])

    def test_delete_url_carries_record_id(self):
        row = build_transaction_row(FakeRef(), _record(id='34'), read_only=False)
        self.assertIn('%s?delete=34' % SUBMIT_TRANSACTION_PAGE, row.cells[6])


class PhpNeTest(unittest.TestCase):
    """PHP 8 的 ``!=``（实测值，见 ``_php_ne`` 的 docstring）。"""

    def test_numeric_strings_compare_numerically(self):
        self.assertFalse(_php_ne('1.234', '1.2340'))
        self.assertFalse(_php_ne('0', '0.0'))
        self.assertFalse(_php_ne(' 1.234 ', '1.234'))

    def test_null_equals_empty_string(self):
        self.assertFalse(_php_ne(None, ''))
        self.assertFalse(_php_ne(None, None))

    def test_null_differs_from_number(self):
        self.assertTrue(_php_ne(None, '1.234'))
        self.assertTrue(_php_ne(None, '0'))

    def test_non_numeric_falls_back_to_string(self):
        self.assertFalse(_php_ne('abc', 'abc'))
        self.assertTrue(_php_ne('abc', 'abd'))

    def test_python_string_compare_would_differ(self):
        """这正是必须自己实现的理由：Python 的字符串比较会判成"变了"。"""
        self.assertTrue('1.234' != '1.2340')          # Python
        self.assertFalse(_php_ne('1.234', '1.2340'))  # PHP


class PhpStrTest(unittest.TestCase):
    """``'netvalue='.null`` 在 PHP 里是 ``'netvalue='``。"""

    def test_none_and_false_become_empty(self):
        self.assertEqual(_php_str(None), '')
        self.assertEqual(_php_str(False), '')

    def test_values_are_passthrough(self):
        self.assertEqual(_php_str('1.234'), '1.234')
        self.assertEqual(_php_str(34), '34')


class RowsTest(unittest.TestCase):
    """``_echoTransactionTableData()`` 的两个分支与引用对象缓存。"""

    def test_single_ref_is_shared_by_all_rows(self):
        ref = FakeRef()
        rows = build_transaction_rows([_record()], ref=ref)
        self.assertEqual(len(rows), 1)

    def test_ref_cache_reuses_one_object_per_group_item(self):
        """同一个 ``groupitem_id`` 共用一份引用对象 —— 否则同一列会

        出现不同时刻的价格（引用对象构造时装填行情）。
        """
        built = []

        def ref_builder(symbol):
            built.append(symbol)
            return FakeRef(symbol)

        records = [
            {'id': '1', 'groupitem_id': 'g1', 'stock_id': '11', 'quantity': '1',
             'price': '1.0', 'fees': '0', 'remark': '', 'filled': '2026-10-13 09:30:00'},
            {'id': '2', 'groupitem_id': 'g1', 'stock_id': '11', 'quantity': '2',
             'price': '1.0', 'fees': '0', 'remark': '', 'filled': '2026-10-14 09:30:00'},
            {'id': '3', 'groupitem_id': 'g2', 'stock_id': '12', 'quantity': '3',
             'price': '1.0', 'fees': '0', 'remark': '', 'filled': '2026-10-15 09:30:00'},
        ]
        build_transaction_rows(records, ref_builder=ref_builder)
        self.assertEqual(built, ['11', '12'])   # g1 只建一次

    def test_symbol_of_is_used_to_build_ref(self):
        built = []

        def ref_builder(symbol):
            built.append(symbol)
            return FakeRef(symbol)

        records = [{'id': '1', 'groupitem_id': 'g1', 'stock_id': '11',
                    'quantity': '1', 'price': '1.0', 'fees': '0', 'remark': '',
                    'filled': '2026-10-13 09:30:00'}]
        build_transaction_rows(records, ref_builder=ref_builder,
                               symbol_of=lambda stock_id: 'KWEB')
        self.assertEqual(built, ['KWEB'])

    def test_empty_records_give_no_rows(self):
        self.assertEqual(build_transaction_rows([], ref=FakeRef()), [])


class TableTest(unittest.TestCase):
    """``EchoTransactionParagraph()``：``$bAll`` 决定标题与页脚。"""

    def test_all_mode_returns_paging_and_no_title_link(self):
        table = build_transaction_table([_record()], ref=FakeRef(), b_all=True,
                                        start=0, num=100, total=1)
        self.assertEqual(table['id'], TRANSACTION_TABLE_ID)
        self.assertIsNotNone(table['paging'])
        self.assertIsNone(table['title_link'])
        self.assertEqual(len(table['rows']), 1)

    def test_short_mode_slices_to_common_display(self):
        """``$bAll`` 为假时只显示 ``TABLE_COMMON_DISPLAY``（10）条。"""
        records = [_record(id=str(i)) for i in range(25)]
        table = build_transaction_table(records, ref=FakeRef(), b_all=False,
                                        group_id='g7')
        self.assertEqual(len(table['rows']), TABLE_COMMON_DISPLAY)
        self.assertIsNone(table['paging'])

    def test_short_mode_title_is_all_transaction_link(self):
        table = build_transaction_table([_record()], ref=FakeRef(), b_all=False,
                                        group_id='g7')
        self.assertIn('mystocktransactioncn.php?groupid=g7', table['title_link'])
        self.assertIn('交易记录', table['title_link'])

    def test_columns_are_dicts(self):
        """``columns_to_dicts()`` 之后模板才拿得到 ``text`` / ``width``。"""
        table = build_transaction_table([], ref=FakeRef())
        self.assertEqual(len(table['columns']), 7)
        self.assertIn('text', table['columns'][0])


if __name__ == "__main__":
    unittest.main()
