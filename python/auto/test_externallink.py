"""``externallink`` 的单元测试。

对应 PHP：``GetLinkElement()`` / ``GetExternalLink()``（``php/httplink.php``）、
``GetSinaFundLink()``（``php/externallink.php:194``）。

## 为什么这个模块必须测

参考数据表的**首格**就是这个外链。为空会让整行少一格、后面所有列错位
（实测踩到：SZ161130 首格空白，价格跑到代码列）。
"""

from __future__ import annotations

import unittest

from externallink import (
    SINA_FINANCE_URL,
    get_external_link,
    get_link_element,
    get_sina_fund_link,
    get_sina_stock_link,
)


class FakeSym:
    """``StockSymbol`` 形状的替身。"""

    def __init__(self, symbol, fund_a=None, sina_fund=None):
        self._symbol = symbol
        self._fund_a = fund_a
        self._sina_fund = sina_fund

    def get_symbol(self):
        return self._symbol

    def is_fund_a(self):
        return self._fund_a

    def is_sina_fund(self):
        return self._sina_fund


class LinkElementTest(unittest.TestCase):
    def test_href_first_then_extra_attributes(self):
        """PHP ``GetLinkElement()`` 先放 ``href``，再并入额外属性。"""
        html = get_link_element("显示", "https://x.test", {"target": "_blank"})
        self.assertEqual(html, '<a href="https://x.test" target="_blank">显示</a>')

    def test_without_extra(self):
        self.assertEqual(get_link_element("A", "u"), '<a href="u">A</a>')

    def test_none_values_become_empty(self):
        self.assertEqual(get_link_element(None, None), '<a href="">None</a>'.replace("None", ""))


class ExternalLinkTest(unittest.TestCase):
    def test_display_defaults_to_url(self):
        """PHP ``$strDisplay ? $strDisplay : $strHttp``。"""
        html = get_external_link("https://x.test")
        self.assertIn(">https://x.test</a>", html)
        self.assertIn('target="_blank"', html)

    def test_display_overrides(self):
        html = get_external_link("https://x.test", "名字")
        self.assertIn(">名字</a>", html)


class SinaFundLinkTest(unittest.TestCase):
    def test_builds_production_url(self):
        """URL 形状必须与生产端一致（``qdiicn.php`` 里那些链接）。"""
        html = get_sina_fund_link(FakeSym("SZ161130", fund_a="161130"))
        self.assertEqual(
            html,
            '<a href="%s/fund/quotes/161130/bc.shtml" target="_blank">SZ161130</a>'
            % SINA_FINANCE_URL,
        )

    def test_falls_back_to_sina_fund_code(self):
        """``IsFundA()`` 取不到时退 ``IsSinaFund()``。"""
        html = get_sina_fund_link(FakeSym("SZ161130", fund_a=None, sina_fund="161130"))
        self.assertIn("/fund/quotes/161130/bc.shtml", html)

    def test_no_code_returns_plain_symbol(self):
        """**没有外链时退回纯符号文本** —— 保证首格非空、列不错位。"""
        html = get_sina_fund_link(FakeSym("QQQ", fund_a=None, sina_fund=None))
        self.assertEqual(html, "QQQ")
        self.assertNotIn("<a", html)

    def test_all_three_listed_funds_produce_a_link(self):
        """本页面上那三只基金都必须能生成外链（P0 的验收点之一）。"""
        for symbol, code in (("SZ161130", "161130"), ("SZ161125", "161125"),
                             ("SH513100", "513100")):
            html = get_sina_fund_link(FakeSym(symbol, fund_a=code))
            self.assertIn("/fund/quotes/%s/bc.shtml" % code, html, symbol)
            self.assertTrue(html.startswith("<a "), symbol)


class SinaStockLinkDispatchTest(unittest.TestCase):
    """``get_sina_stock_link()`` 的**类型分发**。

    href 全部对照生产端 ``sz161130cn.php`` 参考数据表的实际 HTML
    （2026-10-09 抓取）。参考数据表会把基金、美股指数、期货、外汇混在一张表里，
    所以按类型分派是必需的 —— 只认基金会让其余行首格为空、整行列错位。
    """

    #: 符号 -> 生产端 href
    PRODUCTION = {
        "SZ161130": "https://finance.sina.com.cn/fund/quotes/161130/bc.shtml",
        "^NDX": "https://stock.finance.sina.com.cn/usstock/quotes/.NDX.html",
        "hf_NQ": "https://finance.sina.com.cn/futures/quotes/NQ.shtml",
        "QQQ": "https://stock.finance.sina.com.cn/usstock/quotes/QQQ.html",
        "TQQQ": "https://stock.finance.sina.com.cn/usstock/quotes/TQQQ.html",
        "USDCNH": "https://finance.sina.com.cn/money/forex/hq/USDCNH.shtml",
        "USDCNY": "https://finance.sina.com.cn/money/forex/hq/USDCNY.shtml",
        "USCNY": "https://www.chinamoney.com.cn",
    }

    @staticmethod
    def _href(html: str) -> str:
        marker = 'href="'
        return html.split(marker)[1].split('"')[0] if marker in html else ""

    def test_all_eight_reference_rows_match_production(self):
        """参考数据表会出现的 8 类标的，href 必须与生产端逐字一致。"""
        for symbol, expected in sorted(self.PRODUCTION.items()):
            self.assertEqual(self._href(get_sina_stock_link(symbol)), expected, symbol)

    def test_middle_rate_goes_to_chinamoney_not_sina(self):
        """``USCNY``（中间价）指向中国货币网；``USDCNY``（即期）才是新浪外汇页。

        两者的区分点是 ``is_forex()``：中间价为真、即期为假（实测）。
        搞混了会给出一个 404 的新浪页面。
        """
        self.assertIn("chinamoney", get_sina_stock_link("USCNY"))
        self.assertIn("money/forex/hq/USDCNY", get_sina_stock_link("USDCNY"))

    def test_us_index_gets_a_dot_prefix(self):
        """``^NDX`` → ``.NDX``（PHP ``'.'.strtoupper(GetSinaIndexUS())``）。"""
        self.assertIn("/usstock/quotes/.NDX.html", get_sina_stock_link("^NDX"))
        self.assertIn(">^NDX</a>", get_sina_stock_link("^NDX"))

    def test_future_uses_display_name(self):
        """``hf_NQ`` 的 URL 与显示名都用去前缀的 ``NQ``。"""
        html = get_sina_stock_link("hf_NQ")
        self.assertIn("/futures/quotes/NQ.shtml", html)
        self.assertIn(">NQ</a>", html)

    def test_unknown_symbol_returns_plain_text(self):
        """认不出的符号退回纯文本 —— **首格必须有内容**，否则整行少一格。"""
        html = get_sina_stock_link("^ZZZUNKNOWN")
        self.assertTrue(html)
        self.assertNotIn("href=\"\"", html)


if __name__ == "__main__":
    unittest.main()
