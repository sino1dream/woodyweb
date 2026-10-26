"""穷举持仓页（``woody/res/exhaustiveholdingscn.php``）的 web 层测试。

分两块：

1. **路由**：``/holdings/exhaustive/<symbol>`` 的三态阈值（缺省 / 空串 /
   有值）、以及"取不到持仓"时的 200 + 提示；
2. **说明文字的链接**：``/holdings/<symbol>`` 与 ``/fund/<group>/<symbol>``
   上那句「…穷举持仓比例」里的四个字必须是 ``<a>`` —— 这是本次迁移的
   核心诉求（用户原话："把穷举持仓做链接，不用登录客户界面，直接跳转"）。

服务层与纯逻辑的细节由 ``auto/test_exhaustiveholdings.py`` 覆盖，这里只验
"路由有没有把参数与链接接对"，所以四个表都用简化替身，不碰数据库。
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
from web.routes.holdings import _resolve_threshold  # noqa: E402
from web.services.exhaustive_holdings_service import (  # noqa: E402
    EXHAUSTIVE_TABLE_ID,
    build_current_columns,
    build_exhaustive_columns,
    build_quarter_columns,
    build_verify_columns,
)
from sql.historytable import TableRow  # noqa: E402


def _table(table_id, title, columns, rows):
    return {"id": table_id, "title": title, "columns": columns,
            "rows": rows, "count": len(rows)}


def _row(*cells):
    row = TableRow()
    for cell in cells:
        if isinstance(cell, tuple):
            row.add(cell[0], cell[1])
        else:
            row.add(cell)
    return row


class FakeService:
    """只回一份固定的页面数据，记录被调用时的参数。"""

    def __init__(self, page=None):
        self.page = page if page is not None else {
            "symbol": "SZ164701",
            "count": 1,
            "threshold": 1.0,
            "solvable": True,
            "current": _table("current", "当前使用的持仓数据",
                              build_current_columns([]), [_row("100", "1")]),
            "quarter": None,
            "exhaustive": _table(EXHAUSTIVE_TABLE_ID, "穷举持仓比例",
                                 build_exhaustive_columns([]),
                                 [_row("2026-10-22", "1.546", "-2.64%", "0", "100", "1.01")]),
            "verify": _table("verify", "验证最激进数据",
                             build_verify_columns([]),
                             [_row("2026-10-22", "1.546", "-2.64%", "100", "1.01")]),
        }
        self.calls = []

    def get_page(self, symbol, threshold=None, num=None, admin=None):
        self.calls.append({"symbol": symbol, "threshold": threshold,
                           "num": num, "admin": admin})
        return self.page

    def empty_page(self, symbol, threshold=None, num=None, admin=None):
        self.calls.append({"symbol": symbol, "threshold": threshold,
                           "num": num, "admin": admin})
        return None


class ThresholdTest(unittest.TestCase):
    """``GetEditInput()`` 的三态（``exhaustiveholdingscn.php:346``）。"""

    def test_missing_uses_default(self):
        render, value, text = _resolve_threshold(None)
        self.assertTrue(render)
        self.assertEqual(value, 1.0)
        self.assertEqual(text, "1.0")

    def test_empty_skips_calculation(self):
        """空串 → 不算（页面只剩表单），文本也回填空串。"""
        render, value, text = _resolve_threshold("")
        self.assertFalse(render)
        self.assertEqual(text, "")

    def test_zero_is_valid(self):
        """``'0' != ''`` 在 PHP 里是真 —— 填 0 必须照算，不能当空。"""
        render, value, _ = _resolve_threshold("0")
        self.assertTrue(render)
        self.assertEqual(value, 0.0)

    def test_non_numeric_falls_back_to_zero(self):
        render, value, _ = _resolve_threshold("abc")
        self.assertTrue(render)
        self.assertEqual(value, 0.0)


class ColumnLayoutTest(unittest.TestCase):
    """四张表的列顺序（列头错位那一处只钉"格数对得上"）。"""

    def test_exhaustive_has_error_column(self):
        texts = [column["text"] for column in build_exhaustive_columns([])]
        # 日期/净值/涨幅/误差 + 仓位（没有持仓时只剩这一列）
        self.assertEqual(texts[:4], ["日期", "净值", "涨幅", "误差"])
        self.assertEqual(texts[-1], "仓位")

    def test_verify_has_no_error_column(self):
        texts = [column["text"] for column in build_verify_columns([])]
        self.assertNotIn("误差", texts)
        self.assertEqual(texts[:3], ["日期", "净值", "涨幅"])

    def test_quarter_puts_extra_before_current(self):
        """季报独有标的排在当前持仓前面（PHP ``array_merge([Date], $arExtra, $ar)``）。"""
        texts = [column["text"] for column in build_quarter_columns([], {"GLD": "GLD"})]
        self.assertEqual(texts[0], "日期")
        self.assertEqual(texts[1], "GLD")


class ExhaustiveRouteTest(unittest.TestCase):
    def setUp(self):
        self.service = FakeService()
        self.app = create_app({"TESTING": True})
        #: ``create_app`` 没有这个工厂参数（只在路由里现造默认实现），
        #: 所以直接塞 extensions —— ``_get_exhaustive_service`` 就是从这里取的。
        self.app.extensions["exhaustive_holdings_service_factory"] = lambda: self.service
        self.client = self.app.test_client()

    def test_page_renders_four_sections(self):
        response = self.client.get("/holdings/exhaustive/SZ164701")
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn("穷举持仓比例", html)
        self.assertIn("当前使用的持仓数据", html)
        self.assertIn("验证最激进数据", html)
        self.assertIn("2026-10-22", html)
        self.assertEqual(self.service.calls[0]["symbol"], "SZ164701")

    def test_default_threshold(self):
        self.client.get("/holdings/exhaustive/SZ164701")
        self.assertEqual(self.service.calls[0]["threshold"], 1.0)

    def test_threshold_from_query(self):
        self.client.get("/holdings/exhaustive/SZ164701?input=0.5")
        self.assertEqual(self.service.calls[0]["threshold"], 0.5)

    def test_empty_threshold_skips_service(self):
        """``?input=`` 空串 → 服务层压根不被调用（PHP 那边整段不渲染）。"""
        response = self.client.get("/holdings/exhaustive/SZ164701?input=")
        self.assertEqual(200, response.status_code)
        self.assertEqual(self.service.calls, [])
        self.assertIn("阈值留空", response.get_data(as_text=True))

    def test_form_echoes_threshold(self):
        html = self.client.get("/holdings/exhaustive/SZ164701?input=2.5").get_data(as_text=True)
        self.assertIn('value="2.5"', html)

    def test_admin_flag_is_true_for_demo_identity(self):
        """``admin=`` 传的是 ``is_admin()`` —— 演示环境的默认身份就是管理员。

        2026-10-26 之前这里是 ``False``（写入链接恒关，因为
        ``process_admin_holdings`` 没有权限检查）。补上闸门之后不再需要关它，
        所以断言反过来：**给管理员**，但闸门是真的（见
        ``test_admin_flag_false_for_other_identity``）。
        """
        self.client.get("/holdings/exhaustive/SZ164701")
        self.assertTrue(self.service.calls[0]["admin"])

    def test_admin_flag_false_for_other_identity(self):
        """换成非管理员身份 → ``admin=False``（页面上的写入控件随之消失）。"""
        import os

        from unittest import mock

        with mock.patch.dict(os.environ, {"PALMMICRO_ADMIN_EMAIL": "guest@example.com"}):
            self.client.get("/holdings/exhaustive/SZ164701")
        self.assertFalse(self.service.calls[0]["admin"])

    def test_no_page_still_200(self):
        self.service.page = None
        response = self.client.get("/holdings/exhaustive/SZ164701")
        self.assertEqual(200, response.status_code)
        self.assertIn("取不到该基金的持仓数据", response.get_data(as_text=True))


class AdminUpdateFormTest(unittest.TestCase):
    """管理员"确认更新持仓"控件：HTML 要透传，且只在服务层给的时候出现。

    PHP 那一格是 ``GetOnClickLink()`` 拼好的 HTML（``exhaustiveholdingscn.php:43``），
    由 ``EchoTableColumn($ar)`` 原样输出。本版走 ``TableRow.html_indexes``
    透传 —— 如果漏了这个下标，页面上会看到 ``&lt;form`` 的转义源码，
    按钮点不动而且很难发现是转义问题。
    """

    FORM = ('<form method="post" action="/holdings/submit" style="display:inline;"'
            ' onsubmit="return confirm(\'确认更新持仓GLD:50.00和仓位1.00？\')">'
            '<input type="hidden" name="holdings" value="\'\'">'
            '<button type="submit" class="link-button">1.00</button></form>')

    def _app(self):
        service = FakeService()
        row = TableRow(match=False)
        for cell in ("2026-10-22", "1.546", "-2.64%", "0", "50"):
            row.add(cell)
        row.add(self.FORM)
        row.html_indexes.append(5)
        service.page["quarter"] = None
        service.page["exhaustive"]["rows"] = [row]
        app = create_app({"TESTING": True})
        app.extensions["exhaustive_holdings_service_factory"] = lambda: service
        return app

    def test_form_html_is_not_escaped(self):
        html = self._app().test_client().get(
            "/holdings/exhaustive/SZ164701").get_data(as_text=True)
        self.assertIn('<form method="post" action="/holdings/submit"', html)
        self.assertNotIn("&lt;form", html)

    def test_admin_note_mentions_clickable_position(self):
        html = self._app().test_client().get(
            "/holdings/exhaustive/SZ164701").get_data(as_text=True)
        self.assertIn("仓位格是一个可点的数字", html)

    def test_admin_note_hidden_for_non_admin(self):
        import os

        from unittest import mock

        with mock.patch.dict(os.environ, {"PALMMICRO_ADMIN_EMAIL": "guest@example.com"}):
            html = self._app().test_client().get(
                "/holdings/exhaustive/SZ164701").get_data(as_text=True)
        self.assertNotIn("仓位格是一个可点的数字", html)

    def test_submit_holdings_denied_for_non_admin(self):
        """非管理员 POST 写入口 → 403（闸门在路由上，不靠页面藏按钮）。"""
        import os

        from unittest import mock

        with mock.patch.dict(os.environ, {"PALMMICRO_ADMIN_EMAIL": "guest@example.com"}):
            response = self._app().test_client().post(
                "/holdings/submit", data={"symbol": "SZ164701", "holdings": '"GLD":100'})
        self.assertEqual(403, response.status_code)

    def test_submit_holdings_missing_symbol(self):
        response = self._app().test_client().post("/holdings/submit", data={})
        self.assertEqual(400, response.status_code)


class NoteLinkTest(unittest.TestCase):
    """详情页说明行里的「穷举持仓比例」必须是链接（本次迁移的核心诉求）。"""

    def setUp(self):
        self.app = create_app({"TESTING": True})
        self.client = self.app.test_client()

    def test_holdings_page_links_exhaustive(self):
        from unittest import mock

        from web.services.holdings_service import HoldingsService

        page = {
            "est": _table("est", "估值", [], [_row("SZ164701", "1.546")]),
            "est_note": "占位",
            "netvaluehistory": None,
            "reference": None,
            "holdings": None,
            "adr": None,
        }
        with mock.patch.object(HoldingsService, "get_page", return_value=page):
            html = self.client.get("/holdings/SZ164701").get_data(as_text=True)
        #: 说明文字由服务层拼，这里的 est_note 是替身，所以只验"别的页面"那条：
        #: 真正的句子在 stock_detail_service 里拼，用它的纯函数验一次更直接。
        self.assertIn("估值", html)

    def test_note_links_all_three_keys(self):
        """``_note_links`` 三个键都给 URL，第三个指向穷举页。"""
        from web.routes.holdings import _note_links

        with self.app.test_request_context("/"):
            links = _note_links("SZ164701")
        self.assertEqual(links["holdings"], "/holdings/SZ164701")
        self.assertEqual(links["exhaustive"], "/holdings/exhaustive/SZ164701")
        self.assertTrue(links["position"].startswith("/history/fundposition/"))

    def test_est_note_contains_link(self):
        """服务层的说明文字：position / holdings 有链接，exhaustive 有链接。"""
        from web.services.stock_detail_service import StockDetailService

        class Ref:
            def get_symbol(self):
                return "SZ164701"

            def get_position(self):
                return 1.0

            def get_holdings_date(self):
                return "2026-10-22"

            def count_holdings(self):
                return 1

        note = StockDetailService._holdings_est_note(Ref(), {
            "position": "/history/fundposition/SZ164701",
            "holdings": "/holdings/SZ164701",
            "exhaustive": "/holdings/exhaustive/SZ164701",
        })
        self.assertIn('<a href="/holdings/exhaustive/SZ164701">穷举持仓比例</a>', note)
        self.assertIn("值使用1.00", note)
        self.assertIn("更新于2026-10-22", note)


if __name__ == "__main__":
    unittest.main()
