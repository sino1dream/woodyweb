"""站内链接的测试（``php/stocklink.php`` → ``ref/stocklink.py``）。

**全部离线**：只碰符号数组，不连数据库、不发请求。

## 为什么值得单测

``MysqlReference.GetStockLink`` 曾经被错别名成 ``get_external_link``，
于是估值表首格渲染出新浪外链，而生产端是站内详情页。这个错在基金详情页
上**看不出来**（只有一张表，没有外链并排），是分类页把两张表摆到一起、
用 ``_scratch/_prod_qdiipage_compare.py`` 逐格比 href 才抓出来的。
所以这里除了正常用例，还专门有一条"两个链接必须不同"的回归测试。
"""

import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
PYTHON_DIR = ROOT_DIR / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from web.bootstrap import install_paths  # noqa: E402

install_paths()

from ref.stocklink import (  # noqa: E402
    ALL_STOCK_DISPLAY,
    OVERNIGHT_CNH_DISPLAY,
    STOCK_GROUP_DISPLAY,
    STOCK_TRANSACTION_DISPLAY,
    get_delete_link,
    get_edit_link,
    get_exhaustive_holdings_link,
    get_fund_position_link,
    get_group_stock_link,
    get_holdings_link,
    get_my_stock_group_link,
    get_my_stock_link,
    get_on_click_link,
    get_overnight_cnh_link,
    get_php_link,
    get_stock_link,
    get_transaction_link,
    stock_get_all_transaction_link,
)


class _Ref:
    """``$ref`` 的最小替身 —— 只有一个 ``GetSymbol()``。"""

    def __init__(self, symbol):
        self._symbol = symbol

    def GetSymbol(self):
        return self._symbol


class GetPhpLinkTest(unittest.TestCase):
    """``GetPhpLink()``：路径 + 语言后缀 + 查询串。"""

    def test_chinese_page_gets_cn_suffix(self):
        self.assertEqual(
            '<a href="/woody/res/sh513100cn.php">SH513100</a>',
            get_php_link("/woody/res/sh513100", None, "SH513100"),
        )

    def test_english_page_has_no_cn_suffix(self):
        self.assertEqual(
            '<a href="/woody/res/sh513100.php">SH513100</a>',
            get_php_link("/woody/res/sh513100", None, "SH513100", chinese=False),
        )

    def test_query_is_appended(self):
        self.assertEqual(
            '<a href="/woody/res/mystockcn.php?symbol=SH513100">SH513100</a>',
            get_php_link("/woody/res/mystock", "symbol=SH513100", "SH513100"),
        )

    def test_empty_query_is_skipped(self):
        """PHP 是 ``if ($strQuery)`` —— 空串不加 ``?``。"""
        link = get_php_link("/woody/res/mystock", "", "全部股票代码")
        self.assertNotIn("?", link)

    def test_display_defaults_to_basename(self):
        """PHP 的 ``GetInternalLink()`` 在显示名为空时用 ``basename($strPath)``。"""
        self.assertEqual(
            '<a href="/woody/res/sh513100cn.php">sh513100</a>',
            get_php_link("/woody/res/sh513100"),
        )

    def test_internal_link_has_no_target_blank(self):
        """站内链接不带 ``target="_blank"`` —— 这是它与外链的唯一区别。"""
        self.assertNotIn("target=", get_php_link("/woody/res/sh513100", None, "SH513100"))


class GetGroupStockLinkTest(unittest.TestCase):
    """``GetGroupStockLink()``：在"全部"清单里的符号才有专属页面。"""

    def test_known_symbol_gets_own_page(self):
        # SH513100 在 QdiiGetSymbolArray() 里，于是 in_arrayAll() 为真
        self.assertEqual(
            '<a href="/woody/res/sh513100cn.php">SH513100</a>',
            get_group_stock_link("SH513100"),
        )

    def test_page_name_is_lowercase(self):
        """PHP 用 ``strtolower($strSymbol)`` 做页面名 —— 大小写不能搞反。"""
        self.assertIn("/woody/res/sz161125cn.php", get_group_stock_link("SZ161125"))

    def test_unknown_symbol_returns_none(self):
        """PHP 返回 ``false``；按项目约定映射成 ``None``，由调用方走下一支。"""
        self.assertIsNone(get_group_stock_link("SH600000"))

    def test_display_can_be_overridden(self):
        self.assertIn("纳指ETF国泰", get_group_stock_link("SH513100", "纳指ETF国泰"))


class GetMyStockLinkTest(unittest.TestCase):
    """``GetMyStockLink()``：没有专属页面时的兜底。"""

    def test_with_symbol(self):
        self.assertEqual(
            '<a href="/woody/res/mystockcn.php?symbol=SH600000">SH600000</a>',
            get_my_stock_link("SH600000"),
        )

    def test_without_symbol(self):
        """``GetMyStockLink()`` 无参时指向"全部股票代码"页。"""
        self.assertEqual(
            '<a href="/woody/res/mystockcn.php">%s</a>' % ALL_STOCK_DISPLAY,
            get_my_stock_link(),
        )


class GetStockLinkTest(unittest.TestCase):
    """``StockSymbol::GetStockLink()``：先专属页面，再 mystock 兜底。"""

    def test_prefers_group_page(self):
        self.assertEqual(
            '<a href="/woody/res/sh513100cn.php">SH513100</a>',
            get_stock_link("SH513100"),
        )

    def test_falls_back_to_mystock(self):
        self.assertEqual(
            '<a href="/woody/res/mystockcn.php?symbol=SH600000">SH600000</a>',
            get_stock_link("SH600000"),
        )

    def test_never_returns_empty(self):
        """首格为空会让整行少一格、后面所有列错位 —— 两条路都必须有内容。"""
        for symbol in ("SH513100", "SH600000", "^NDX", "QQQ", "hf_NQ", "USCNY"):
            self.assertTrue(get_stock_link(symbol), symbol)


class StockGetTransactionLinkTest(unittest.TestCase):
    """``StockGetTransactionLink()``：投资组合段落的首格。

    对应 ``php/stocklink.php:336``，查询串形如
    ``groupid=42&symbol=SH513100``。
    """

    def test_group_and_symbol_in_query(self):
        link = get_transaction_link('42', 'SH513100')
        self.assertIn('groupid=42&symbol=SH513100', link)
        self.assertIn('mystocktransactioncn.php', link)

    def test_symbol_optional(self):
        """``if ($strSymbol)`` 为假时查询串只有 groupid。"""
        link = get_transaction_link('42')
        self.assertIn('groupid=42', link)
        self.assertNotIn('symbol=', link)

    def test_display_defaults_to_symbol_display(self):
        """默认显示名取 ``StockSymbol::GetDisplay()``，即去前缀后的名字。

        ``hf_ES`` 是新浪美国期货，显示成 ``ES`` 而不是 ``hf_ES``。
        """
        self.assertIn('>ES</a>', get_transaction_link('42', 'hf_ES'))
        self.assertIn('>SH513100</a>', get_transaction_link('42', 'SH513100'))

    def test_display_can_be_overridden(self):
        link = get_transaction_link('42', 'SH513100', '华宝油气')
        self.assertIn('>华宝油气</a>', link)


class GetOvernightCnhLinkTest(unittest.TestCase):
    """``GetOvernightCnhLink()``：``fx_susdcnh`` 那一行的跳转。

    对应 ``php/stocklink.php:112``，查询串的**键就是页面名**
    （``overnightcnh=-123456``），不是 ``amount=``。
    """

    def test_default_display_and_amount(self):
        link = get_overnight_cnh_link()
        self.assertIn('overnightcnh=95000', link)
        self.assertIn('>%s</a>' % OVERNIGHT_CNH_DISPLAY, link)

    def test_amount_is_passed_through(self):
        link = get_overnight_cnh_link('$1234K', '-123456')
        self.assertIn('overnightcnh=-123456', link)
        self.assertIn('>$1234K</a>', link)


class GetOnClickLinkTest(unittest.TestCase):
    """``GetOnClickLink()``（``php/httplink.php:26``）：确认弹窗链接。

    期望值由 PHP 实测得出（``php -r`` 载入 ``ui/htmlelement.php`` +
    ``httplink.php``，``UrlGetServer()`` 打桩成空串）。
    """

    def test_question_becomes_confirm_call(self):
        self.assertEqual(
            '<a href="/woody/res/submittransaction.php?empty=12" '
            'onclick="return confirm(\'确认清空备注：申购 3？\')">清空</a>',
            get_on_click_link("/woody/res/submittransaction.php?empty=12",
                              "确认清空备注：申购 3？", "清空"),
        )

    def test_double_quotes_are_removed_not_escaped(self):
        """PHP 用 ``str_replace('"', '', ...)`` 删引号 —— 不是转义。

        问句要塞进 ``onclick="..."`` 里，留着双引号会把 HTML 属性截断。
        """
        self.assertEqual(
            '<a href="/x" onclick="return confirm(\'say hi\')">d</a>',
            get_on_click_link("/x", 'say "hi"', "d"),
        )

    def test_extra_attribute_comes_after_href(self):
        """``GetLinkElement()`` 先放 ``href``，属性按数组顺序追加。"""
        link = get_on_click_link("/x", "q", "d")
        self.assertTrue(link.startswith('<a href="/x" onclick='))


class GetDeleteLinkTest(unittest.TestCase):
    """``GetDeleteLink()``：中文用全角问号，英文分支自带尾空格。"""

    def test_chinese_uses_fullwidth_question_mark(self):
        self.assertEqual(
            '<a href="/woody/res/submittransaction.php?delete=12" '
            'onclick="return confirm(\'确认删除2026-10-13 100交易记录？\')">删除</a>',
            get_delete_link("/woody/res/submittransaction.php?delete=12",
                            "2026-10-13 100交易记录"),
        )

    def test_english_uses_halfwidth_and_trailing_space(self):
        self.assertEqual(
            '<a href="/x.php?delete=1" '
            'onclick="return confirm(\'Confirm delete us str?\')">Delete</a>',
            get_delete_link("/x.php?delete=1", "cn", "us str", False),
        )

    def test_chinese_branch_ignores_us_text(self):
        """中文分支的问句只用 ``$strCn`` —— ``$strUs`` 是英文分支专用的。"""
        link = get_delete_link("/x.php?delete=1", "中文", "English")
        self.assertIn("确认删除中文？", link)
        self.assertNotIn("English", link)


class GetEditLinkTest(unittest.TestCase):
    """``GetEditLink()``：``edit=`` 查询串 + 中英文显示文本。"""

    def test_chinese(self):
        self.assertEqual(
            '<a href="/woody/res/editstocktransactioncn.php?edit=34">修改</a>',
            get_edit_link("/woody/res/editstocktransaction", "34"),
        )

    def test_english_page_has_no_cn_suffix(self):
        self.assertEqual(
            '<a href="/woody/res/editstocktransaction.php?edit=34">Edit</a>',
            get_edit_link("/woody/res/editstocktransaction", "34", False),
        )


class StockGetAllTransactionLinkTest(unittest.TestCase):
    """``StockGetAllTransactionLink()``（``php/stocklink.php:349``）。

    与 :func:`get_transaction_link` 的差别只有显示文本恒为 ``交易记录``。
    """

    def test_without_ref(self):
        self.assertEqual(
            '<a href="/woody/res/mystocktransactioncn.php?groupid=g7">交易记录</a>',
            stock_get_all_transaction_link("g7"),
        )

    def test_with_ref_appends_symbol(self):
        self.assertEqual(
            '<a href="/woody/res/mystocktransactioncn.php?groupid=g7'
            '&symbol=hf_ES">交易记录</a>',
            stock_get_all_transaction_link("g7", _Ref("hf_ES")),
        )

    def test_display_is_never_the_symbol(self):
        """显示文本恒为 ``交易记录`` —— 链接语境是"这一组全部"。"""
        link = stock_get_all_transaction_link("g7", _Ref("hf_ES"))
        self.assertIn(">%s</a>" % STOCK_TRANSACTION_DISPLAY, link)
        self.assertNotIn(">ES</a>", link)


class GetMyStockGroupLinkTest(unittest.TestCase):
    """``GetMyStockGroupLink()``（``php/stocklink.php:329``）。

    PHP 走到 ``GetPageLink()``，后者在"目标就是当前页"时返回蓝色文本。
    按模块 docstring 的取舍 2，Python 不判当前页 —— 恒给链接。
    """

    def test_defaults_to_group_display(self):
        self.assertEqual(
            '<a href="/woody/res/mystockgroupcn.php">%s</a>' % STOCK_GROUP_DISPLAY,
            get_my_stock_group_link(),
        )

    def test_query_and_display(self):
        self.assertEqual(
            '<a href="/woody/res/mystockgroupcn.php?groupid=g7">分组甲</a>',
            get_my_stock_group_link("groupid=g7", "分组甲"),
        )


class StockLinkDiffersFromExternalLinkTest(unittest.TestCase):
    """回归测试：站内链接与外链必须是两个东西。

    这条钉的是那个真 bug —— ``GetStockLink`` 曾被别名成
    ``get_external_link``，估值表首格于是跳去新浪。详情页只有一张表，
    视觉上看不出错，所以用断言把差异固定住。
    """

    def test_reference_methods_differ(self):
        from ref.refobj import MysqlReference
        from ref.stockref import StockReference

        # 两个方法必须都存在，且不是同一个对象
        self.assertTrue(hasattr(MysqlReference, "get_stock_link"))
        self.assertTrue(hasattr(StockReference, "get_external_link"))
        self.assertIsNot(
            MysqlReference.__dict__.get("get_stock_link"),
            MysqlReference.__dict__.get("get_external_link"),
        )

    def test_rendered_links_differ(self):
        """真建一个引用对象，比两个首格。"""
        from ref.refobj import MysqlReference

        ref = MysqlReference("SH513100")
        self.assertNotEqual(ref.get_stock_link(), ref.get_external_link())
        self.assertIn("/woody/res/sh513100cn.php", ref.get_stock_link())
        self.assertIn("finance.sina.com.cn", ref.get_external_link())


class HoldingsLinkTest(unittest.TestCase):
    """CK4 持仓分支那三个链接（``php/stocklink.php:144`` / ``:232`` / ``:244``）。

    三个页面（持仓 / 仓位估算 / 穷举持仓）在本站点还没有，链接暂时不可达，
    但**形状必须与 PHP 一致** —— 逐行对照脚本比的就是 href。
    """

    def test_holdings_link(self):
        self.assertEqual(
            '<a href="/woody/res/holdingscn.php?symbol=SH513050">基金持仓</a>',
            get_holdings_link("SH513050"),
        )

    def test_holdings_link_display_defaults_to_symbol(self):
        """``($strDisplay ? $strDisplay : $strSymbol)`` —— 没给文字就用符号。"""
        self.assertEqual(
            '<a href="/woody/res/holdingscn.php?symbol=SH513050">SH513050</a>',
            get_holdings_link("SH513050", display=None),
        )

    def test_exhaustive_holdings_link(self):
        self.assertEqual(
            '<a href="/woody/res/exhaustiveholdingscn.php?symbol=SH513050">穷举持仓比例</a>',
            get_exhaustive_holdings_link("SH513050"),
        )

    def test_fund_position_link(self):
        self.assertEqual(
            '<a href="/woody/res/fundpositioncn.php?symbol=SH513050">仓位估算</a>',
            get_fund_position_link("SH513050"),
        )

    def test_fund_position_link_display_symbol(self):
        """``$bDisplaySymbol`` 为真时链接文字用符号（``:234``）。"""
        self.assertEqual(
            '<a href="/woody/res/fundpositioncn.php?symbol=SH513050">SH513050</a>',
            get_fund_position_link("SH513050", display_symbol=True),
        )

    def test_english_pages_drop_cn(self):
        """英文页不加 ``cn``（``chinese=False``）—— 三个链接都一样。"""
        self.assertIn("/woody/res/holdings.php?", get_holdings_link("SH513050", chinese=False))
        self.assertIn(
            "/woody/res/fundposition.php?", get_fund_position_link("SH513050", chinese=False)
        )


if __name__ == "__main__":
    unittest.main()
