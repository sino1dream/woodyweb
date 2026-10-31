"""分类页的测试（19 个 ``woody/res/*cn.php`` → ``category_page_service.py``）。

**全部离线**：引用对象是内存假实现，不联网、不连 MySQL。

这一批页面的"逻辑"只有三样东西，所以测试也围绕这三样：

1. **注册表**（``CATEGORY_PAGES``）—— 19 个键、各自的标题/说明/PHP 源文件，
   以及符号数组的**形状**（全是 A 股场内基金、无重复）；
2. **装配**（``build_category_page``）—— 两张表用谁的数据、缺服务时不炸；
3. **路由**（``/category/<name>`` 与 ``/qdii``）—— 200/404、排序参数、
   页尾导航不列自己、两个入口同一页。

"符号数组与 PHP 逐例一致"不在这里断言 —— 那需要对真 PHP 跑，见
``auto/_scratch/_php_category_compare.py`` 与 ``README_category_pages_migration.md``。
这里只守**不变量**，因为数组本身会随基金发行而增长，写死数量只会变成维护负担。
"""

import re
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT_DIR = Path(__file__).resolve().parents[3]
PYTHON_DIR = ROOT_DIR / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from web.app import create_app  # noqa: E402
from web.bootstrap import install_paths  # noqa: E402

install_paths()

from web.services import category_page_service  # noqa: E402
from core.stocksymbol import StockSymbol, get_all_symbol_array  # noqa: E402
from web.services.category_page_service import (  # noqa: E402
    CATEGORY_PAGES,
    CategoryNotFoundError,
    build_category_page,
    get_category,
    get_category_nav,
    get_category_names,
)
from web.services.fundest_service import FundEstService  # noqa: E402
from web.services.reference_service import ReferenceService  # noqa: E402
# 假对象与 qdii 分类页共用一套（同一个页面家族，没必要各写一份）
from web.tests.test_qdii_page import (  # noqa: E402
    EST_TABLE,
    REFERENCE_TABLE,
    FakeRef,
    RecordingFundEstService,
    RecordingReferenceService,
)


#: 19 个"分类页"（`qdiicn.php` 也在其中，它在 PHP 里与其它 18 个同构）。
#: 顺序 = ``_stocklink.php::GetCategoryArray()`` 的 switch 顺序（也是导航顺序）。
#: 2026-11-01 补上 ``lof`` / ``overnight`` 两个 —— 它们一直是同一套壳子
#: （``lofcn.php`` / ``overnightcn.php``），2026-10-30 那批漏在清单外。
FUND_CATEGORIES = [
    "biotech",
    "chinafuture",
    "chinaindex",
    "chinainternet",
    "commodity",
    "hangseng",
    "hshares",
    "hstech",
    "lof",
    "mscius50",
    "oilfund",
    "overnight",
    "qdii",
    "qdiieu",
    "qdiihk",
    "qdiijp",
    "qdiimix",
    "qqqfund",
    "spyfund",
]

#: 标题**不带**"基金净值计算工具"后缀的两个分类：PHP ``_getTitleStr()`` 里
#: 写的就是 ``'lof' => LOF_ALL_DISPLAY`` / ``'overnight' => OVERNIGHT_DISPLAY``，
#: 不像 biotech 那批拼了 ``$strTool``。照抄，别为了"看起来整齐"补上后缀。
TITLE_WITHOUT_TOOL_SUFFIX = {"lof", "overnight"}


#: A 股场内基金代码：SH/SZ + 6 位数字。分类页的数组必须全是这个形状 ——
#: 一旦混进美股/指数（``^NDX``、``ASHR``），PHP 那边就会去建 AB/AH/ADR
#: 配对引用并渲染那三个段落，而本页不渲染，行为就分叉了。
A_SHARE_FUND = re.compile(r"^(SH|SZ)\d{6}$")


class CategoryRegistryTest(unittest.TestCase):
    """注册表本身：键、标题、说明、PHP 源文件、符号数组。"""

    def test_registry_covers_all_pages(self):
        """19 个键，顺序与 PHP 分派表同序。"""
        self.assertEqual(FUND_CATEGORIES, get_category_names())

    def test_every_entry_has_the_five_fields(self):
        for name, definition in CATEGORY_PAGES.items():
            with self.subTest(category=name):
                for key in ("display", "title", "description", "symbols", "php"):
                    self.assertIn(key, definition)
                self.assertTrue(definition["display"])
                self.assertTrue(definition["description"])
                self.assertTrue(callable(definition["symbols"]))

    def test_titles_are_unique_and_match_the_php_pattern(self):
        """PHP ``_getTitleStr()``：多数 = 分组显示名 + '基金净值计算工具'。

        ``lof`` / ``overnight`` 是例外（只有显示名，没有后缀），见
        :data:`TITLE_WITHOUT_TOOL_SUFFIX` —— 两条分支都要钉住，
        否则"统一补后缀"会把它们改成 PHP 里不存在的标题。
        """
        titles = [item["title"] for item in CATEGORY_PAGES.values()]
        self.assertEqual(len(titles), len(set(titles)))
        for name, definition in CATEGORY_PAGES.items():
            with self.subTest(category=name):
                if name in TITLE_WITHOUT_TOOL_SUFFIX:
                    self.assertEqual(definition["display"], definition["title"])
                else:
                    self.assertTrue(definition["title"].endswith("基金净值计算工具"))
                self.assertTrue(definition["title"].startswith(definition["display"]))

    def test_php_page_files_exist(self):
        """``php`` 字段要能对回真的 4 行壳子，写错了就没法照原样核对。"""
        res_dir = ROOT_DIR / "woody" / "res"
        for name, definition in CATEGORY_PAGES.items():
            with self.subTest(category=name):
                self.assertTrue(
                    (res_dir / definition["php"]).is_file(),
                    "缺少 PHP 源文件：%s" % definition["php"],
                )

    def test_symbol_arrays_are_all_a_share_funds(self):
        """全是 A 股场内基金、且无重复。

        这两条一起说明"PHP 的 AB/AH/ADR 分支在本页一次都不会进"，
        也说明 ``+`` 拼数组没有把同一个符号拼两遍（PHP 的 ``array_merge``
        同样不去重，重复的后果是同一行显示两次）。
        """
        for name, definition in CATEGORY_PAGES.items():
            symbols = definition["symbols"]()
            with self.subTest(category=name):
                self.assertTrue(symbols)
                for symbol in symbols:
                    self.assertRegex(symbol, A_SHARE_FUND)
                self.assertEqual(len(symbols), len(set(symbols)), "符号重复：%s" % name)

    def test_merged_categories_keep_their_hardcoded_extras(self):
        """三个"拼出来的"分类，PHP 硬编码追加的那几只不能丢。

        这些是 ``_stocklink.php::GetCategoryArray()`` 里 ``array_merge`` 的第二、
        三个参数，不属于任何 ``stocksymbol`` 函数，最容易在重构时被"顺手整理"掉。
        """
        self.assertIn("SH513290", get_category("biotech")["symbols"]())
        self.assertIn("SZ164906", get_category("chinainternet")["symbols"]())
        # commodity = 商品期货 6 只 + 黄金 6 只，两边不重叠
        commodity = get_category("commodity")["symbols"]()
        self.assertEqual(12, len(commodity))
        # oilfund = XOP 组 + 场内原油 ETF + QDII 原油，三段各不相同
        oilfund = get_category("oilfund")["symbols"]()
        self.assertEqual(9, len(oilfund))

    def test_commodity_gold_order_follows_production(self):
        """黄金段的显示顺序按生产端（2026-10-30 抓取），成员仍来自源函数。

        本地 PHP 快照的成员与生产端一致、排列不同；行序是人工比对对象，
        所以这里把顺序钉住，防止有人"顺手"改回源函数顺序。
        """
        commodity = get_category("commodity")["symbols"]()
        self.assertEqual(6, len(commodity) - 6)          # 前 6 只是商品期货
        self.assertEqual(
            ["SZ160216", "SZ164701", "SZ160719", "SZ161116", "SZ165513", "SZ161815"],
            commodity[6:],
        )

    def test_overnight_gold_order_follows_production(self):
        """``overnight`` 的金块顺序也按生产端（2026-11-01 抓取）。

        与 :meth:`test_commodity_gold_order_follows_production` 是同一个坑：
        内嵌的 ``GetQdiiGoldSymbolArray()`` 在本地快照里的排列与生产端不同。
        这里只钉"黄金 6 只是不是按生产端序、且仍然连在一起"，别的位置不动
        —— 前后两段（XOP/原油与那 9 只 LOF）本来就一致。
        """
        symbols = get_category("overnight")["symbols"]()
        self.assertEqual(23, len(symbols))
        start = symbols.index("SZ160216")
        self.assertEqual(
            ["SZ160216", "SZ164701", "SZ160719", "SZ161116", "SZ165513", "SZ161815"],
            symbols[start:start + 6],
        )

    def test_lof_is_the_filtered_a_share_subset(self):
        """``lof`` 是**过滤**出来的（``IsLofA()``），不是某个现成数组。

        PHP 是 ``foreach (GetAllSymbolArray()) { if ($sym->IsLofA()) ... }``，
        所以它必须是 ``get_all_symbol_array()`` 的**保序子序列** —— 一旦有人
        改成排序或换成别的源，行序就和生产端（也是 ALL 的原序）对不上了。
        """
        lof = get_category("lof")["symbols"]()
        self.assertEqual(53, len(lof))
        self.assertEqual(
            [s for s in get_all_symbol_array() if s in set(lof)],
            lof,
        )
        for symbol in lof:
            with self.subTest(symbol=symbol):
                self.assertTrue(StockSymbol(symbol).is_lof_a())

    def test_unknown_category_raises(self):
        for name in ("", "nope", "hstechcn", "QDII"):
            with self.subTest(name=name):
                with self.assertRaises(CategoryNotFoundError):
                    get_category(name)

    def test_nav_lists_every_category_with_a_count(self):
        nav = get_category_nav()
        self.assertEqual(FUND_CATEGORIES, [item["name"] for item in nav])
        for item in nav:
            with self.subTest(category=item["name"]):
                self.assertEqual(len(get_category(item["name"])["symbols"]()), item["count"])
                # 导航显示短名，不是带"基金净值计算工具"的长标题
                self.assertNotIn("计算工具", item["display"])


class BuildCategoryPageTest(unittest.TestCase):
    """装配：标题/说明来自注册表，两张表来自服务，缺服务不炸。"""

    def _patch_constructors(self, official=1.5, fund_a=True):
        return (
            mock.patch.object(
                category_page_service,
                "MysqlReference",
                lambda symbol, source=None: FakeRef(symbol, source=source, fund_a=fund_a),
            ),
            mock.patch.object(
                category_page_service,
                "build_qdii_reference",
                lambda symbol, source=None: FakeRef(
                    symbol, source=source, fund_a=True, official=official
                ),
            ),
        )

    def test_title_and_description_come_from_the_registry(self):
        ref_patch, qdii_patch = self._patch_constructors()
        with ref_patch, qdii_patch:
            page = build_category_page(
                "hstech",
                reference_service=RecordingReferenceService(REFERENCE_TABLE),
                fundest_service=RecordingFundEstService(EST_TABLE),
            )
        definition = CATEGORY_PAGES["hstech"]
        self.assertEqual(definition["title"], page["title"])
        self.assertEqual(definition["description"], page["description"])
        self.assertEqual("hstechcn.php", page["php"])
        self.assertEqual("hstech", page["name"])

    def test_symbols_default_to_the_registry(self):
        """不传 ``symbols`` 时用注册表；传了就以参数为准（测试注入用）。"""
        ref_patch, qdii_patch = self._patch_constructors()
        with ref_patch, qdii_patch:
            page = build_category_page("biotech", symbols=["SZ159502"])
        self.assertEqual(1, page["counts"]["symbols"])

    def test_missing_services_still_return_a_page(self):
        """路由在数据库不可用时只注入 ``source=None`` 的服务，两张表都可能是空。"""
        ref_patch, qdii_patch = self._patch_constructors()
        with ref_patch, qdii_patch:
            page = build_category_page("hangseng", symbols=[])
        self.assertIsNone(page["reference"])
        self.assertIsNone(page["est"])
        self.assertEqual(0, page["counts"]["symbols"])


class CategoryRouteTest(unittest.TestCase):
    """``/category/<name>`` 与 ``/qdii`` 两个入口的接线。"""

    def setUp(self):
        self._ref_patch = mock.patch.object(
            category_page_service,
            "MysqlReference",
            lambda symbol, source=None: FakeRef(symbol, source=source, fund_a=True),
        )
        self._qdii_patch = mock.patch.object(
            category_page_service,
            "build_qdii_reference",
            lambda symbol, source=None: FakeRef(symbol, source=source, official=1.5),
        )
        self._ref_patch.start()
        self._qdii_patch.start()
        self.addCleanup(self._ref_patch.stop)
        self.addCleanup(self._qdii_patch.stop)

        # 符号清单收窄到两只，免得 17 个数组 × 几十只基金拖慢测试
        self.app = create_app(
            {"TESTING": True},
            reference_service_factory=lambda: ReferenceService(source=None),
            fundest_service_factory=lambda: FundEstService(source=None),
        )
        self.client = self.app.test_client()

    def test_every_category_renders(self):
        for name in FUND_CATEGORIES:
            with self.subTest(category=name):
                response = self.client.get("/category/%s" % name)
                self.assertEqual(200, response.status_code)
                html = response.get_data(as_text=True)
                self.assertIn(CATEGORY_PAGES[name]["title"], html)
                self.assertIn(CATEGORY_PAGES[name]["description"], html)
                # 两张表用的是同一个宏，类名与 fund_detail.html 一致
                self.assertIn('class="data-table reference-table"', html)
                self.assertIn('class="data-table fund-est-table"', html)

    def test_unknown_category_is_404(self):
        for name in ("nope", "hstechcn", "QDII"):
            with self.subTest(name=name):
                self.assertEqual(404, self.client.get("/category/%s" % name).status_code)

    def test_qdii_short_path_and_category_path_are_the_same_page(self):
        short = self.client.get("/qdii").get_data(as_text=True)
        named = self.client.get("/category/qdii").get_data(as_text=True)
        for html in (short, named):
            self.assertIn(CATEGORY_PAGES["qdii"]["title"], html)

    def test_sort_links_point_back_to_the_serving_endpoint(self):
        """表头排序链接必须指回当前页 —— 两个入口共用一份模板，写死就会跳错页。"""
        named = self.client.get("/category/hstech").get_data(as_text=True)
        self.assertIn("/category/hstech?sort=symbol", named)
        self.assertIn("/category/hstech?sort=premium", named)

        short = self.client.get("/qdii").get_data(as_text=True)
        self.assertIn("/qdii?sort=symbol", short)

    def test_sort_query_is_accepted(self):
        for sort in ("symbol", "premium", "garbage"):
            with self.subTest(sort=sort):
                response = self.client.get("/category/qqqfund?sort=%s" % sort)
                self.assertEqual(200, response.status_code)

    def test_nav_lists_the_other_sixteen(self):
        html = self.client.get("/category/hstech").get_data(as_text=True)
        self.assertIn("其它分类", html)
        # 自己不列
        self.assertNotIn('href="/category/hstech"', html)
        # 其它都在（抽查三个，避免把 16 条硬编码进断言）
        for name in ("biotech", "hangseng", "qdii"):
            self.assertIn('href="/category/%s"' % name, html)

    def test_page_works_without_database(self):
        """库不可用时页面仍要能打开（分类阶段静默跳过缺失符号）。"""
        app = create_app(
            {"TESTING": True},
            reference_service_factory=lambda: ReferenceService(source=None),
            fundest_service_factory=lambda: FundEstService(source=None),
        )
        self.assertEqual(200, app.test_client().get("/category/hshares").status_code)


if __name__ == "__main__":
    unittest.main()
