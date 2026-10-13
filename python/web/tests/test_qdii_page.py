"""QDII 分类页的测试（``woody/res/qdiicn.php`` → ``qdii_page_service.py``）。

**全部离线**：引用对象与数据源都是内存假实现，不联网、不连 MySQL。
分类逻辑用 ``mock.patch.object`` 打在 ``qdii_page_service`` 模块自己的全局名上
（项目踩坑记录 #39：补丁要打在**被调用的那个命名空间**，打错了会静默失效）。
"""

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

from web.services import qdii_page_service  # noqa: E402
from web.services.fundest_service import FundEstService  # noqa: E402
from web.services.qdii_page_service import (  # noqa: E402
    PAGE_DESCRIPTION,
    PAGE_TITLE,
    build_qdii_page,
    classify_symbols,
)
from web.services.reference_service import ReferenceService  # noqa: E402


class FakeRef:
    """:class:`MysqlReference` / :func:`build_qdii_reference` 的最小替身。

    分类只用 ``is_fund_a()`` 与 ``get_official_net_value()``；但路由测试会把
    它喂给**真** service，所以还要实现 ``ref_has_data()`` 那一族方法 ——
    ``ui.uicolumn.ref_has_data`` 默认取 ``False``，缺方法等于"没有数据"，
    整行会被两个 service 一起跳过（实测踩到：第一版 FakeRef 太薄，
    两张表都渲染不出来）。
    """

    def __init__(self, symbol, source=None, fund_a=True, official=None, has_data=True):
        self.symbol = symbol
        self.source = source
        self.stock_id = "1"
        self._fund_a = fund_a
        self._official = official
        self._has_data = has_data

    def is_fund_a(self):
        return self._fund_a

    def get_official_net_value(self):
        return self._official

    def get_fair_net_value(self):
        return None

    def get_realtime_net_value(self):
        return None

    def has_data(self):
        return self._has_data

    def get_symbol(self):
        return self.symbol

    def get_external_link(self):
        return '<a href="https://finance.sina.com.cn/fund/quotes/%s/bc.shtml">%s</a>' % (
            self.symbol[2:],
            self.symbol,
        )

    def get_stock_link(self):
        return '<a href="/fund/%s">%s</a>' % (self.symbol, self.symbol)

    def get_price_display(self, value=None):
        return "" if value is None else "%.3f" % float(value)

    def get_percentage_text(self, value=None):
        return "1.23%"

    def get_date(self):
        return "2026-10-12"

    def get_time_hm(self):
        return "15:00"

    def get_official_date(self):
        return "2026-10-12"


class BoomRef:
    """建引用对象就抛异常的替身，用来验证"跳过而不是整页挂"。"""

    def __init__(self, *args, **kwargs):
        raise RuntimeError("模拟引用对象构造失败")


class RecordingReferenceService:
    """记录 ``get_table`` 调用的替身。"""

    def __init__(self, table=None, boom=False):
        self.table = table
        self.boom = boom
        self.calls = []

    def get_table(self, refs, wide=False, admin=False, title=None):
        self.calls.append({"refs": list(refs), "wide": wide, "admin": admin, "title": title})
        if self.boom:
            raise RuntimeError("模拟参考数据服务失败")
        return self.table if self.table is not None else {"title": "参考数据", "columns": [], "rows": [], "count": 0}


class RecordingFundEstService:
    """记录 ``get_table`` 调用的替身。"""

    def __init__(self, table=None, boom=False):
        self.table = table
        self.boom = boom
        self.calls = []

    def get_table(self, refs, wide=False, title=None, sort=None):
        self.calls.append({"refs": list(refs), "wide": wide, "title": title, "sort": sort})
        if self.boom:
            raise RuntimeError("模拟估值服务失败")
        return self.table


REFERENCE_TABLE = {"title": "参考数据", "columns": [], "rows": ["row"], "count": 1}
EST_TABLE = {"id": "estimation", "title": "", "columns": [], "rows": ["row"], "count": 1}


class ClassifySymbolsTest(unittest.TestCase):
    """PHP ``_echoStockGroupArray()`` 的 foreach 分类规则。"""

    def _patch(self, ref_factory, qdii_factory):
        """把两个构造器换成假的，返回``(ref_patch, qdii_patch)``上下文。"""
        return (
            mock.patch.object(qdii_page_service, "MysqlReference", ref_factory),
            mock.patch.object(qdii_page_service, "build_qdii_reference", qdii_factory),
        )

    def test_fund_with_official_net_value_lands_in_both_tables(self):
        """有官方净值的 A 股基金：参考数据表与估值表都进。"""
        ref_patch, qdii_patch = self._patch(
            lambda symbol, source=None: FakeRef(symbol, source=source, fund_a=True),
            lambda symbol, source=None: FakeRef(symbol, source=source, fund_a=True, official=1.234),
        )
        with ref_patch, qdii_patch:
            refs, funds, skipped = classify_symbols(["SH513100"])

        self.assertEqual(1, len(refs))
        self.assertEqual(1, len(funds))
        self.assertEqual([], skipped)

    def test_fund_without_official_net_value_stays_out_of_est_table(self):
        """算不出官方净值：PHP 的守卫 ``if ($fund->GetOfficialNetValue())`` 不成立。

        但它**仍然进参考数据表** —— ``$arRef[] = $ref`` 在 IsFundA 分支之外。
        """
        ref_patch, qdii_patch = self._patch(
            lambda symbol, source=None: FakeRef(symbol, source=source, fund_a=True),
            lambda symbol, source=None: FakeRef(symbol, source=source, fund_a=True, official=None),
        )
        with ref_patch, qdii_patch:
            refs, funds, skipped = classify_symbols(["SH513100"])

        self.assertEqual(1, len(refs))
        self.assertEqual([], funds)

    def test_non_fund_a_symbol_never_builds_a_fund_reference(self):
        """非 A 股基金：PHP 会去建 AB/AH/ADR 配对引用，本页不渲染那些段落。

        所以这里连 ``build_qdii_reference`` 都不该被调到。
        """
        called = []

        def qdii_factory(symbol, source=None):
            called.append(symbol)
            return FakeRef(symbol, source=source, fund_a=False)

        ref_patch, qdii_patch = self._patch(
            lambda symbol, source=None: FakeRef(symbol, source=source, fund_a=False),
            qdii_factory,
        )
        with ref_patch, qdii_patch:
            refs, funds, skipped = classify_symbols(["^NDX"])

        self.assertEqual(1, len(refs))
        self.assertEqual([], funds)
        self.assertEqual([], called)

    def test_reference_failure_skips_only_that_symbol(self):
        """建普通引用对象失败：只跳过该符号，不影响后面的。"""

        def flaky(symbol, source=None):
            if symbol == "SH513100":
                raise RuntimeError("boom")
            return FakeRef(symbol, source=source, fund_a=True)

        ref_patch, qdii_patch = self._patch(flaky, lambda symbol, source=None: FakeRef(symbol))
        with ref_patch, qdii_patch:
            refs, funds, skipped = classify_symbols(["SH513100", "SH513500"])

        self.assertEqual(["SH513500"], [ref.symbol for ref in refs])
        self.assertEqual(["SH513100"], skipped)

    def test_fund_reference_failure_keeps_reference_row(self):
        """基金引用对象建失败：参考数据那一行仍然要（PHP 里两者互不牵连）。"""

        def flaky(symbol, source=None):
            if symbol == "SH513100":
                raise RuntimeError("boom")
            return FakeRef(symbol, source=source, official=1.0)

        ref_patch, qdii_patch = self._patch(
            lambda symbol, source=None: FakeRef(symbol, source=source, fund_a=True),
            flaky,
        )
        with ref_patch, qdii_patch:
            refs, funds, _ = classify_symbols(["SH513100"])

        self.assertEqual(1, len(refs))
        self.assertEqual([], funds)

    def test_all_symbols_reach_reference_table_even_wide_is_false(self):
        """``$bWide == false`` 时 PHP 的守卫恒真，所以**全部**符号都进参考表。

        生产端表1 与表2 行数相同（都是 41 行）正是这个原因；这个用例把
        "被归到基金段的符号不会从参考表消失"钉住。
        """
        ref_patch, qdii_patch = self._patch(
            lambda symbol, source=None: FakeRef(symbol, source=source, fund_a=True),
            lambda symbol, source=None: FakeRef(symbol, source=source, official=1.0),
        )
        with ref_patch, qdii_patch:
            refs, funds, _ = classify_symbols(["SH513100", "SH513500", "SZ161125"])

        self.assertEqual(3, len(refs))
        self.assertEqual(3, len(funds))


class BuildQdiiPageTest(unittest.TestCase):
    """:func:`build_qdii_page` 的装配与服务调用。"""

    def _patch_constructors(self, official=1.5, fund_a=True):
        return (
            mock.patch.object(
                qdii_page_service,
                "MysqlReference",
                lambda symbol, source=None: FakeRef(symbol, source=source, fund_a=fund_a),
            ),
            mock.patch.object(
                qdii_page_service,
                "build_qdii_reference",
                lambda symbol, source=None: FakeRef(
                    symbol, source=source, fund_a=True, official=official
                ),
            ),
        )

    def test_both_tables_are_built(self):
        ref_patch, qdii_patch = self._patch_constructors()
        reference = RecordingReferenceService(table=REFERENCE_TABLE)
        fundest = RecordingFundEstService(table=EST_TABLE)
        with ref_patch, qdii_patch:
            page = build_qdii_page(
                source="fake-source",
                reference_service=reference,
                fundest_service=fundest,
                symbols=["SH513100", "SH513500"],
            )

        self.assertEqual(PAGE_TITLE, page["title"])
        self.assertEqual(PAGE_DESCRIPTION, page["description"])
        self.assertIs(REFERENCE_TABLE, page["reference"])
        self.assertIs(EST_TABLE, page["est"])
        self.assertEqual(
            {"symbols": 2, "reference": 2, "est": 2, "skipped": 0}, page["counts"]
        )
        # source 必须透传给构造器（引用对象靠它取名字/净值）
        self.assertEqual("fake-source", reference.calls[0]["refs"][0].source)
        self.assertEqual("fake-source", fundest.calls[0]["refs"][0].source)
        self.assertFalse(reference.calls[0]["wide"])
        self.assertFalse(fundest.calls[0]["wide"])

    def test_sort_is_forwarded_to_est_service_only(self):
        """``?sort=premium`` 只影响估值表 —— PHP 那边也只有它有排序分支。"""
        ref_patch, qdii_patch = self._patch_constructors()
        reference = RecordingReferenceService(table=REFERENCE_TABLE)
        fundest = RecordingFundEstService(table=EST_TABLE)
        with ref_patch, qdii_patch:
            build_qdii_page(
                reference_service=reference,
                fundest_service=fundest,
                symbols=["SH513100"],
                sort="premium",
            )

        self.assertEqual("premium", fundest.calls[0]["sort"])
        # 参考数据服务没有 sort 参数，不能凭空塞
        self.assertNotIn("sort", reference.calls[0])

    def test_empty_tables_become_none(self):
        """表一行都没有时对应键是 ``None``（模板据此整段跳过）。"""
        ref_patch, qdii_patch = self._patch_constructors()
        with ref_patch, qdii_patch:
            page = build_qdii_page(
                reference_service=RecordingReferenceService(table={"rows": [], "columns": []}),
                fundest_service=RecordingFundEstService(table=None),
                symbols=["SH513100"],
            )

        self.assertIsNone(page["reference"])
        self.assertIsNone(page["est"])

    def test_service_failure_does_not_break_the_page(self):
        """任何一张表炸掉都不该让整页 500。"""
        ref_patch, qdii_patch = self._patch_constructors()
        with ref_patch, qdii_patch:
            page = build_qdii_page(
                reference_service=RecordingReferenceService(boom=True),
                fundest_service=RecordingFundEstService(boom=True),
                symbols=["SH513100"],
            )

        self.assertIsNone(page["reference"])
        self.assertIsNone(page["est"])
        self.assertEqual(1, page["counts"]["reference"])

    def test_no_services_still_reports_counts(self):
        """没注入服务时也要能返回计数（模板的"共 N 只"要用）。"""
        page = build_qdii_page(symbols=["SH513100", "SH513500"])

        self.assertIsNone(page["reference"])
        self.assertIsNone(page["est"])
        self.assertEqual(2, page["counts"]["symbols"])

    def test_default_symbols_come_from_qdii_group(self):
        """默认符号清单就是 PHP 的 ``QdiiGetSymbolArray()``。"""
        from core.stocksymbol import qdii_get_symbol_array

        page = build_qdii_page(symbols=None)
        self.assertEqual(len(qdii_get_symbol_array()), page["counts"]["symbols"])

    def test_wide_flag_reaches_both_services(self):
        ref_patch, qdii_patch = self._patch_constructors()
        reference = RecordingReferenceService(table=REFERENCE_TABLE)
        fundest = RecordingFundEstService(table=EST_TABLE)
        with ref_patch, qdii_patch:
            build_qdii_page(
                reference_service=reference,
                fundest_service=fundest,
                symbols=["SH513100"],
                wide=True,
            )

        self.assertTrue(reference.calls[0]["wide"])
        self.assertTrue(fundest.calls[0]["wide"])


class QdiiPageRouteTest(unittest.TestCase):
    """``/qdii`` 路由接线：注入假构造器与假服务，不打网络。"""

    def setUp(self):
        self._ref_patch = mock.patch.object(
            qdii_page_service,
            "MysqlReference",
            lambda symbol, source=None: FakeRef(symbol, source=source, fund_a=True),
        )
        self._qdii_patch = mock.patch.object(
            qdii_page_service,
            "build_qdii_reference",
            lambda symbol, source=None: FakeRef(symbol, source=source, official=1.5),
        )
        self._ref_patch.start()
        self._qdii_patch.start()
        self.addCleanup(self._ref_patch.stop)
        self.addCleanup(self._qdii_patch.stop)

        self.app = create_app(
            {"TESTING": True},
            reference_service_factory=lambda: ReferenceService(
                _NameOnlySource({"SH513100": "纳指ETF国泰", "SH513500": "标普500ETF博时"})
            ),
            fundest_service_factory=lambda: FundEstService(source=None),
        )
        self.client = self.app.test_client()

    def test_page_renders_both_tables(self):
        response = self.client.get("/qdii")
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)

        self.assertIn(PAGE_TITLE, html)
        self.assertIn(PAGE_DESCRIPTION, html)
        # 两张表各自的容器类名（与 fund_detail.html 用的是同一个宏）
        self.assertIn('class="data-table reference-table"', html)
        self.assertIn('class="data-table fund-est-table"', html)
        # 表1 的六列
        for text in ("代码", "价格", "涨幅", "日期", "时间", "名称"):
            self.assertIn(text, html)
        # 假名字源给的中文名
        self.assertIn("纳指ETF国泰", html)
        self.assertIn("标普500ETF博时", html)

    def test_page_works_without_database(self):
        """数据库不可用时页面仍要能打开（分类阶段静默跳过缺失符号）。"""
        app = create_app(
            {"TESTING": True},
            reference_service_factory=lambda: ReferenceService(source=None),
            fundest_service_factory=lambda: FundEstService(source=None),
        )
        response = app.test_client().get("/qdii")
        self.assertEqual(200, response.status_code)

    def test_sort_query_is_accepted(self):
        """``?sort=premium`` / ``?sort=symbol`` 都是合法入口，不该 404/500。"""
        for sort in ("symbol", "premium", "garbage"):
            response = self.client.get(f"/qdii?sort={sort}")
            self.assertEqual(200, response.status_code, f"sort={sort}")

    def test_page_links_to_other_groups(self):
        """页尾的「其它分组」导航要与首页同一套分组定义。"""
        html = self.client.get("/qdii").get_data(as_text=True)
        self.assertIn("其它分组", html)
        self.assertIn("QDII-LOF 基金指数对照表", html)


class _NameOnlySource:
    """只有 ``get_stock_name`` 的假数据源。"""

    def __init__(self, names):
        self._names = names

    def get_stock_name(self, symbol):
        return self._names.get(symbol)


if __name__ == "__main__":
    unittest.main()
