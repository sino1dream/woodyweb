"""页脚导航（``SymbolAccount::EchoLinks()``）的本地对应物测试。

对应 PHP ``woody/res/php/_stockaccount.php:126``：每个页面 ``EchoAll()``
末尾都调一次，所以它是**全站**的页脚。本地由 ``web/services/site_links.py``
供数据、``base.html`` 的 ``<footer>`` 渲染、``create_app`` 的 context
processor 注入。

两块验证：

1. **纯逻辑**（:func:`footer_sections`）：段的顺序、外链标记、当前分组
   不给自己挂链接、管理员段只在管理员时出现；
2. **渲染**：随便一页的 HTML 里必须真的有两段（本站点 / 生产端对照）。
"""

from __future__ import annotations

import os
import sys
import unittest

WEB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON_DIR = os.path.dirname(WEB_DIR)
for _path in (PYTHON_DIR, WEB_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from web.app import create_app  # noqa: E402
from web.services.site_links import (  # noqa: E402
    PRODUCTION_ROOT,
    STOCK_CATEGORY,
    STOCK_MENU,
    UTILITY_LINKS,
    footer_sections,
    local_links,
    production_links,
    safe_footer_sections,
)


def _url_for(endpoint, **values):
    """``url_for`` 的假实现：够验"调了哪个 endpoint、带了什么参数"。"""
    if values:
        suffix = "&".join("%s=%s" % item for item in sorted(values.items()))
        return "/%s?%s" % (endpoint, suffix)
    return "/%s" % endpoint


class ProductionLinksTest(unittest.TestCase):
    def test_every_link_is_external_and_absolute(self):
        links = production_links()
        self.assertTrue(links)
        for link in links:
            self.assertTrue(link["external"], link)
            self.assertTrue(link["url"].startswith(PRODUCTION_ROOT + "/"), link)

    def test_order_matches_php(self):
        """顺序照抄：工具页 → 账号工具 → 开发记录 → 菜单 → 分类。"""
        links = production_links()
        self.assertEqual([link["text"] for link in links[:len(UTILITY_LINKS)]],
                         [text for _, _, text in UTILITY_LINKS])
        self.assertEqual(links[len(UTILITY_LINKS)]["url"],
                         PRODUCTION_ROOT + "/account/simpletest.php")
        self.assertEqual(links[len(UTILITY_LINKS) + 1]["text"], "开发记录")
        menu_start = len(UTILITY_LINKS) + 2
        self.assertEqual([link["text"] for link in links[menu_start:menu_start + len(STOCK_MENU)]],
                         [text for _, text in STOCK_MENU])
        category_start = menu_start + len(STOCK_MENU)
        self.assertEqual(
            [link["text"] for link in links[category_start:category_start + len(STOCK_CATEGORY)]],
            [text for _, text in STOCK_CATEGORY])

    def test_migrated_utility_pages_are_not_external(self):
        """四个工具页迁完后，页脚不该再留生产端外链（那会成自指的"死链"）。

        ``lofcn`` / ``overnightcn`` 进分类页注册表（2026-11-01）、
        ``overnightcnh`` / ``autotractor`` 各有自己的路由。
        """
        urls = [link["url"] for link in production_links()]
        for page in ("lof.php", "overnight.php", "overnightcnh.php", "autotractor.php"):
            with self.subTest(page=page):
                self.assertFalse([url for url in urls if url.endswith("/" + page)])

    def test_link_count(self):
        """工具页（本地已全迁，见 ``UTILITY_LINKS``）+ 账号工具 + 开发记录 + 7 菜单 + 10 分类。"""
        self.assertEqual(
            len(production_links()),
            len(UTILITY_LINKS) + 1 + 1 + len(STOCK_MENU) + len(STOCK_CATEGORY),
        )

    def test_ahcompare_is_not_external_anymore(self):
        """迁完 ``/ahcompare`` 后，页脚不该再留一条生产端外链（会成死链）。"""
        self.assertFalse(
            [link for link in production_links() if "ahcompare" in link["url"]]
        )


class LocalLinksTest(unittest.TestCase):
    def test_all_local_links_are_internal(self):
        for link in local_links(_url_for):
            self.assertFalse(link["external"], link)
            self.assertTrue(link["url"].startswith("/"), link)

    def test_current_group_is_not_linked(self):
        """PHP ``unset($ar[$strItem])``：当前分组不给自己挂链接。"""
        urls = [link["url"] for link in local_links(_url_for, current_group="qdiilof")]
        self.assertNotIn("/fund.fund_group_page?group=qdiilof", urls)
        self.assertIn("/fund.fund_group_page?group=qdiietf", urls)

    def test_symbol_adds_detail_link(self):
        links = local_links(_url_for, symbol="SZ164701")
        self.assertIn("/stock.stock_detail_page?symbol=SZ164701",
                      [link["url"] for link in links])

    def test_admin_section_only_for_admin(self):
        """PHP 的管理员段在 ``if ($this->IsAdmin())`` 里。"""
        urls = [link["url"] for link in local_links(_url_for, is_admin=False)]
        self.assertNotIn("/admin.groups", urls)
        urls = [link["url"] for link in local_links(_url_for, is_admin=True)]
        self.assertIn("/admin.groups", urls)

    def test_ahcompare_points_to_local_page(self):
        urls = {link["text"]: link["url"] for link in local_links(_url_for)}
        self.assertEqual(urls["A股和H股对比"], "/compare.ahcompare_page")

    def test_group_title_has_no_trailing_space(self):
        texts = [link["text"] for link in local_links(_url_for)]
        for text in texts:
            self.assertEqual(text, text.strip(), repr(text))


class FooterSectionsTest(unittest.TestCase):
    def test_two_sections_in_order(self):
        sections = footer_sections(_url_for)
        self.assertEqual([section["title"] for section in sections],
                         ["本站点", "生产端对照（PHP 页面）"])

    def test_safe_wrapper_swallows_errors(self):
        """页脚拼错不能让整页 500 —— 兜住异常返回空列表。"""

        def boom(endpoint, **values):
            raise RuntimeError("endpoint 拼错了")

        self.assertEqual(safe_footer_sections(boom), [])


class RenderedFooterTest(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True})
        self.client = self.app.test_client()

    def test_page_contains_both_sections(self):
        html = self.client.get("/status").get_data(as_text=True)
        self.assertIn('class="footer-links"', html)
        self.assertIn("本站点", html)
        self.assertIn("生产端对照（PHP 页面）", html)

    def test_external_links_open_in_new_tab(self):
        """生产端那一段用绝对地址，且新窗口打开（``↗`` 后缀由模板给）。

        取 ``chinaindex.php`` 当样本 —— 它属于"七个股票菜单"，本地没有对应页，
        不会随某个工具页迁移而失效（``lof.php`` 就迁走了）。
        """
        html = self.client.get("/status").get_data(as_text=True)
        self.assertIn('target="_blank" rel="noopener noreferrer"', html)
        self.assertIn("palmmicro.com/woody/res/chinaindex.php", html)

    def test_admin_link_follows_identity(self):
        """页脚的管理员段在 ``is_admin()`` 为假时整条消失。

        **只验页脚**：页头导航（``base.html`` 的 ``site-nav``）里也有一个
        ``/admin/groups``，那是站点常驻入口、与身份无关，不能拿它当判据。
        """
        from unittest import mock

        def footer(html):
            start = html.find('<footer class="site-footer')
            return html[start:html.find("</footer>", start)]

        html = self.client.get("/status").get_data(as_text=True)
        self.assertIn('href="/admin/groups"', footer(html))
        with mock.patch.dict(os.environ,
                            {"PALMMICRO_ADMIN_EMAIL": "guest@example.com"}):
            html = self.client.get("/status").get_data(as_text=True)
        self.assertNotIn('href="/admin/groups"', footer(html))


if __name__ == "__main__":
    unittest.main()
