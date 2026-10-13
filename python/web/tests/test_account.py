"""账户演示页的接线测试（``web/routes/account.py``）。

**单独成一个模块的原因**与 ``test_sma_route.py`` 一样：它需要 Flask，
而 ``test_transaction.py`` / ``test_money.py`` 那几十个用例是**不依赖
flask** 的。这里钉的是"服务层装配好的表真的到了页面上"：

1. 两张表（交易明细 / 资金汇总）都出现，且 ``id`` 与 PHP 的
   ``EchoTableParagraphBegin($ar, 'transaction'/'money', …)`` 一致；
2. 备注格的**五种形态**在页面上各就各位 —— 校准链接只在"含申购且净值
   不等于成交价"时出现；
3. 资金汇总三行的 ``empty()`` 闸门：分组 1 只有港币行、分组 2 只有
   美元行、"全部"三行都有；
4. 分组链接**没被转义**（``first_cell_is_html`` 那条口子），且指回本页。

**不连库、不联网**：演示数据是路由里写死的，汇率也是假引用对象给的。
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


def _squeezed(html: str) -> str:
    """压掉标签之间的空白 —— Jinja 会在标签之间塞缩进，
    直接 ``assertIn('<td>2026-10-11</td>')`` 会因为缩进而失败。"""
    return re.sub(r"\s+", " ", html)


def _table(html: str, css_class: str) -> str:
    """按 ``data-table`` 的类名抠出一张表，找不到给空串。"""
    match = re.search(
        r'<table class="data-table %s".*?</table>' % re.escape(css_class), html)
    return match.group(0) if match else ""


class AccountRouteTest(unittest.TestCase):
    """``/account`` 的端到端渲染。"""

    def setUp(self):
        from web.app import create_app

        self.app = create_app({"TESTING": True})
        self.client = self.app.test_client()

    def _html(self, query=""):
        return _squeezed(
            self.client.get("/account" + query).get_data(as_text=True))

    # -- 两张表都在 ----------------------------------------------------
    def test_both_paragraphs_reach_the_page(self):
        html = self._html()

        self.assertIn('class="data-table transaction-table"', html)
        self.assertIn('id="transaction"', html)
        self.assertIn('class="data-table money-table"', html)
        self.assertIn('id="money"', html)

    def test_the_transaction_columns_match_php(self):
        """七列：日期 / 代码 / 数量 / 价格 / 费用 / 备注 / 操作。"""
        table = _table(self._html(), "transaction-table")

        for text in ("日期", "代码", "数量", "价格", "费用", "备注", "操作"):
            self.assertIn(text, table)

    def test_the_money_columns_match_php(self):
        """六列，前两列带 ``全部`` 前缀、中间两列带 ``单一货币`` 前缀。"""
        table = _table(self._html(), "money-table")

        for text in ("分组名称", "全部盈亏", "全部持仓",
                     "单一货币盈亏", "单一货币持仓", "测试"):
            self.assertIn(text, table)

    def test_the_page_says_the_data_is_a_demo(self):
        """演示数据要写在页面上 —— 别让人拿它当行情看。"""
        html = self._html()

        self.assertIn("演示数据", html)
        self.assertIn("stocktransaction", html)

    # -- 备注格的五种形态 ----------------------------------------------
    def test_qdii_order_shows_the_calibration_link(self):
        """``SZ161125`` 第一笔：含``申购`` 且净值 ``2.0950`` ≠ 成交价 ``1.8000``。"""
        table = _table(self._html(), "transaction-table")

        self.assertIn('?adjust=201&netvalue=2.0950', table)
        self.assertIn("确认校准到净值: 2.0950？", table)

    def test_non_qdii_order_shows_the_calibration_link(self):
        """``TLT``：含``申购``，净值按**当日**取（非 QDII 走 ``GetClose``）。"""
        table = _table(self._html(), "transaction-table")

        self.assertIn('?adjust=202&netvalue=98.50', table)

    def test_order_with_a_matching_netvalue_has_no_calibration_link(self):
        """``SZ161125`` 第二笔：成交价与净值都是 ``2.0950``。

        这一条是 PHP 松散比较 ``$strNetValue != $strPrice`` 的现场 ——
        用 Python 的字符串比较（``'2.0950' != '2.0950'`` 为假）结果一样，
        但换成 ``'2.095'`` 对 ``'2.0950'`` 就会分道扬镳。
        """
        table = _table(self._html(), "transaction-table")

        self.assertIn('?empty=205', table)
        self.assertNotIn('?adjust=205', table)

    def test_remark_without_order_has_only_the_clear_link(self):
        """``KWEB`` 的``打新``：不含``申购``，只有清空链接。"""
        table = _table(self._html(), "transaction-table")

        self.assertIn('?empty=203', table)
        self.assertNotIn('?adjust=203', table)
        # 确认文字是"确认清空备注：打新？"—— 前缀那两个字别漏
        self.assertIn("确认清空备注：打新？", table)

    def test_empty_remark_has_no_link_at_all(self):
        """空备注：``strlen($strRemark) > 0`` 守卫整段跳过。"""
        table = _table(self._html(), "transaction-table")

        self.assertNotIn('?empty=204', table)
        self.assertNotIn('?adjust=204', table)
        # 删除链接不受备注影响（它认的是"日期 + 数量"）
        self.assertIn('?delete=204', table)

    def test_the_delete_question_carries_date_and_quantity(self):
        """删除确认里是 ``2026-10-13 200交易记录`` —— 同一天同数量会有两条。"""
        table = _table(self._html(), "transaction-table")

        self.assertIn("确认删除2026-10-13 200交易记录？", table)

    # -- 分组切换 ------------------------------------------------------
    def test_the_default_group_is_the_richer_one(self):
        """默认分组 2：五条记录，备注格的五种形态都在里面。"""
        table = _table(self._html(), "transaction-table")

        self.assertEqual(table.count("<tr> <td>2"), 5)

    def test_switching_group_changes_the_transaction_rows(self):
        """分组 1 只有两条（A 股与港股），且都没有校准链接。"""
        table = _table(self._html("?groupid=1"), "transaction-table")

        self.assertEqual(table.count("<tr> <td>2"), 2)
        self.assertNotIn("adjust=", table)

    def test_an_unknown_group_falls_back_to_the_default(self):
        """演示页没有"分组不存在"这一说，兜底到默认分组而不是摆张空表。"""
        table = _table(self._html("?groupid=99"), "transaction-table")

        self.assertEqual(table.count("<tr> <td>2"), 5)

    # -- 资金汇总 ------------------------------------------------------
    def test_the_money_rows_are_grouped_by_currency(self):
        """分组 1（人民币 + 港币）两行、分组 2（人民币 + 美元）两行、
        ``全部`` 三行 —— 共七行。"""
        table = _table(self._html(), "money-table")

        self.assertEqual(table.count("<tr> <td>"), 7)

    def test_the_group_link_is_not_escaped(self):
        """首格是分组链接：``first_cell_is_html`` 那条口子要开着。"""
        table = _table(self._html(), "money-table")

        self.assertIn('<a href="/account?groupid=1">A股与港股</a>', table)
        self.assertIn('<a href="/account?groupid=2">QDII与美股</a>', table)
        self.assertNotIn("&lt;a href=", table)

    def test_the_all_row_uses_the_literal_text(self):
        """``全部`` 分组没有 id —— 首格是字面量，不是链接。"""
        table = _table(self._html(), "money-table")

        self.assertIn("<td>全部</td>", table)

    def test_the_usd_row_uses_a_transaction_link(self):
        """分组 2 的美元行首格是 ``StockGetTransactionLink($groupId, false, '$')``。

        ``全部`` 那一行的美元行没有 group id，于是只剩一个裸的 ``$`` ——
        页面上两种形态都要在。
        """
        table = _table(self._html(), "money-table")

        self.assertIn('<a href="/woody/res/mystocktransactioncn.php?groupid=2">$</a>',
                      table)
        self.assertIn("<td>$</td>", table)

    def test_the_hkd_row_uses_the_hkd_column_text(self):
        """港币行的首格是 ``TableColumnHKD`` 的文本（``港币$``），不带链接。"""
        table = _table(self._html(), "money-table")

        self.assertEqual(table.count("<td>港币$</td>"), 2)

    def test_there_is_no_test_column_without_a_member_email(self):
        """没注入 ``member_email_of`` = 没登录 —— 测试列一格都不出。

        PHP 里那一列只对 ``全部`` 行出现，且按会员邮箱分两种算法。表头
        照渲染、行里少一格，是 PHP 的原样（``EchoTableColumn`` 有几格
        echo 几格）。
        """
        table = _table(self._html(), "money-table")

        self.assertIn("测试", table)
        self.assertNotIn("<td>1679328.22</td>", table)

    def test_the_converted_cny_total_is_the_same_across_rows(self):
        """三行的"全部持仓"指向同一个折人民币总数，只是单位不同。

        ``全部`` 行：``246900 + 59390*7.12 + 60000*0.92 = 724956.8``；
        折美元 ``724956.8/7.12 = 101819.78``、折港币
        ``724956.8/0.92 = 787996.52``。这三个数都出现在同一行里。
        """
        table = _table(self._html(), "money-table")

        for text in ("724956.8", "101819.78", "787996.52"):
            self.assertIn("<td>%s</td>" % text, table)


if __name__ == "__main__":
    unittest.main()
