"""``/woody/res/*.php`` 旧地址兼容层的测试。

这一层的存在理由：生产端的地址就是文件名，迁到 Flask 之后全部 404。
2026-11-05 用户按生产端的习惯敲 ``/woody/res/sz161226cn.php`` 撞到的就是这件事。

钉四件事：

1. **每标的页**靠文件名里的符号 → ``/fund/<SYMBOL>``（131 个同构文件，一条正则）；
2. **分类页**从 ``CATEGORY_PAGES`` 反推、**edit 族**从 ``STOCK_OPTIONS`` 生成 ——
   两处都不另抄清单，加第 20 个分类 / 第 17 项编辑时这里自动跟上；
3. **不猜**：没有对应页就 404 + 原因，不往相近路径跳；
4. **所有映射目标都得有路由** —— 这条防的是「映射写对了但本地没这一页」，
   比逐个手测 196 个地址可靠。
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

from web.app import create_app  # noqa: E402
from web.services import legacy_urls  # noqa: E402
from web.services.category_page_service import CATEGORY_PAGES  # noqa: E402
from web.services.stock_option_service import STOCK_OPTIONS  # noqa: E402


class ResolveTest(unittest.TestCase):
    """映射函数本身（不起 Flask）。"""

    def test_symbol_page_goes_to_fund_detail(self):
        """``sz161226cn.php`` 的符号编在文件名里，本地统一是 ``/fund/<symbol>``。"""
        self.assertEqual("/fund/SZ161226", legacy_urls.resolve("sz161226cn.php"))
        self.assertEqual("/fund/SH518800", legacy_urls.resolve("sh518800cn.php"))

    def test_symbol_page_case_is_normalized(self):
        """文件名全是小写，本地符号是大写 —— 不归一就查不到标的。"""
        self.assertEqual("/fund/SZ161226", legacy_urls.resolve("Sz161226cn.php"))

    def test_every_category_page_maps_to_its_registry_key(self):
        """分类页从注册表的 ``php`` 字段反推，19 个一个不漏。"""
        for name, spec in CATEGORY_PAGES.items():
            php = spec["php"]
            self.assertEqual("/category/%s" % name, legacy_urls.resolve(php), php)

    def test_every_edit_option_maps_to_its_key(self):
        """16 项 ``edit*.php`` 全部有去处；季报/持仓两项落专门页。"""
        special = {
            "editquarterreport": "/holdings/quarter/SZ161226",
            "editstockholdings": "/holdings/exhaustive/SZ161226",
        }
        for spec in STOCK_OPTIONS:
            page = "%scn.php" % spec.key
            expected = special.get(spec.key, "/stock/SZ161226/edit/%s" % spec.key)
            self.assertEqual(expected, legacy_urls.resolve(page, "SZ161226"), page)

    def test_page_needing_symbol_raises_instead_of_guessing(self):
        with self.assertRaises(legacy_urls.SymbolRequired) as caught:
            legacy_urls.resolve("editnetvaluecn.php")
        self.assertEqual("editnetvaluecn.php", caught.exception.page)

    def test_symbol_is_normalized_for_paths_too(self):
        """``?symbol=nf_au0`` 落库路径必须是大写 ``nf_AU0``（新浪只认大写）。"""
        self.assertEqual("/stock/nf_AU0/delete",
                         legacy_urls.resolve("deletesymbol.php", "nf_au0"))

    def test_unknown_page_returns_none(self):
        self.assertIsNone(legacy_urls.resolve("nosuchpage.php"))
        # 不是 .php 的一律不当旧地址处理
        self.assertIsNone(legacy_urls.resolve("style.css"))

    def test_known_missing_pages_carry_a_reason(self):
        for page in ("uploadfile.php", "debugcn.php", "cateyescn.php"):
            self.assertTrue(legacy_urls.missing_reason(page), page)

    def test_normalize_symbol_shapes(self):
        cases = {
            "sz161226": "SZ161226",
            "SH518800": "SH518800",
            "nf_au0": "nf_AU0",
            "hf_nq": "hf_NQ",
            "gb_aapl": "gb_AAPL",
            "^NDX": "^NDX",
            "": "",
        }
        for raw, expected in cases.items():
            self.assertEqual(expected, legacy_urls.normalize_symbol(raw), raw)


class RoutesTest(unittest.TestCase):
    """路由行为。"""

    def setUp(self):
        self.client = create_app({"TESTING": True}).test_client()

    def test_symbol_page_redirects(self):
        response = self.client.get("/woody/res/sz161226cn.php")
        self.assertEqual(302, response.status_code)
        self.assertEqual("/fund/SZ161226", response.headers["Location"])

    def test_category_page_redirects(self):
        response = self.client.get("/woody/res/chinafuturecn.php")
        self.assertEqual(302, response.status_code)
        self.assertTrue(response.headers["Location"].endswith("/category/chinafuture"))

    def test_symbol_query_is_moved_into_the_path(self):
        """``?symbol=`` 是 PHP 的写法，跳过去之后不能再把参数带一遍。"""
        response = self.client.get("/woody/res/editnetvaluecn.php?symbol=sz161226")
        self.assertEqual(302, response.status_code)
        self.assertEqual("/stock/SZ161226/edit/editnetvalue", response.headers["Location"])

    def test_other_query_parameters_are_forwarded(self):
        """工具页靠查询串预填金额，丢掉参数等于丢功能。"""
        response = self.client.get("/woody/res/overnightcnhcn.php?overnightcnh=100000")
        self.assertEqual(302, response.status_code)
        self.assertEqual("/overnightcnh?overnightcnh=100000", response.headers["Location"])

    def test_missing_symbol_renders_the_form(self):
        response = self.client.get("/woody/res/editnetvaluecn.php")
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn("这一页需要符号", html)
        self.assertIn('name="symbol"', html)

    def test_unknown_page_is_404_with_a_reason(self):
        response = self.client.get("/woody/res/uploadfile.php")
        self.assertEqual(404, response.status_code)
        self.assertIn("本地没有这一页", response.get_data(as_text=True))
        # 已知未迁的页面要写清为什么，不要笼统一句"不认识"
        self.assertIn("stockhistory", response.get_data(as_text=True))

    def test_every_mapped_target_matches_a_real_route(self):
        """映射表里的目标必须有路由 —— 防"映射写对了但本地没这一页"。"""
        adapter = create_app({"TESTING": True}).url_map.bind("localhost")
        pages = set(legacy_urls.FIXED_PAGES) | set(legacy_urls.SYMBOL_ROUTES)
        pages |= set(legacy_urls._option_pages())
        pages |= {spec["php"] for spec in CATEGORY_PAGES.values()}
        pages |= set(legacy_urls.KNOWN_MISSING)

        for page in sorted(pages):
            try:
                target = legacy_urls.resolve(page, "SZ161226")
            except legacy_urls.SymbolRequired:  # pragma: no cover - 已给了符号
                self.fail("%s 不该还需要符号" % page)
            if target is None:
                # 只有 KNOWN_MISSING 里的页面允许没有目标
                self.assertIn(page, legacy_urls.KNOWN_MISSING, page)
                continue
            try:
                adapter.match(target.split("?")[0])
            except Exception as exc:  # pragma: no cover - 失败时打出来
                self.fail("%s -> %s 没有路由：%s" % (page, target, exc))

    def test_every_real_php_page_is_accounted_for(self):
        """拿生产端那 206 个文件名逐个过一遍：要么有目标，要么写清了原因。

        上面那条只走"表里已有的页面"，漏掉的是"表里压根没有的" ——
        2026-11-05 就有两个（``btbond.php`` / ``cateyes.php``）无目标也无原因，
        用户拿到的是一句笼统的"不认识"。这条钉住覆盖率。
        """
        root = ROOT_DIR / "woody" / "res"
        if not root.is_dir():  # pragma: no cover - 只在本仓库内跑
            self.skipTest("没有 woody/res 目录")

        unexplained = []
        for path in sorted(root.glob("*.php")):
            page = path.name
            try:
                target = legacy_urls.resolve(page)
            except legacy_urls.SymbolRequired:
                continue          # 要符号才跳，属于有对应页
            if target is None and not legacy_urls.missing_reason(page):
                unexplained.append(page)

        self.assertEqual([], unexplained, "这些页面没有对应页，也没写原因：%s" % unexplained)


if __name__ == "__main__":
    unittest.main()
