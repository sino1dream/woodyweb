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
    get_group_stock_link,
    get_my_stock_link,
    get_overnight_cnh_link,
    get_php_link,
    get_stock_link,
    get_transaction_link,
)


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


if __name__ == "__main__":
    unittest.main()
