"""季报持仓索引页（``/holdings/quarter``）与后台控制台入口的测试。

## 这一层为什么存在

生产端 ``editquarterreportcn.php`` 必须先知道 ``?symbol=`` 才能打开，
于是"哪些基金要按季报录持仓"只能靠管理员背符号。本版补一张索引：
从后台控制台点「修改季报持仓」→ 32 只持仓型 QDII 的代码清单 →
点代码进 ``/holdings/quarter/<symbol>`` 编辑页。

## 钉的事

1. 清单来自 ``qdiimix_holdings_symbols()``（32 只），**每个**符号都有详情链接，
   且代码是 6 位（``SZ161815`` → ``161815``）；
2. 后台那一项是**静态**链接，不带 ``__SYMBOL__`` / ``data-symbol-link``
   —— 否则前端会拿符号输入框去改写一个没有占位符的URL；
3. 库挂了（cursor 抛异常 / 连数据源都造不出来）页面仍然 200 并列全 32 个代码；
4. 名称与最新季报日期只是附加信息：查得到就显示，查不到留空。

假数据源按 SQL 特征分发，不连真库；与 ``test_quarter_report.py`` 的
``FakeDb`` 同一套路，但这里只需要两条 SELECT。
"""

from __future__ import annotations

import os
import re
import sys
import unittest

WEB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON_DIR = os.path.dirname(WEB_DIR)
for _path in (PYTHON_DIR, WEB_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from web.app import create_app  # noqa: E402
from web.bootstrap import install_paths  # noqa: E402

install_paths()

from updater.holdingsupdater import qdiimix_holdings_symbols  # noqa: E402


class FakeCursor:
    """只认索引页会发的两条 SELECT，别的一律返回空。"""

    def __init__(self, connection):
        self.connection = connection
        self._rows = []

    def execute(self, sql, params=()):
        text = " ".join(sql.split())
        if "FROM stock" in text:
            wanted = set(params)
            self._rows = [dict(row) for row in self.connection.stock_rows
                          if row["symbol"] in wanted]
        elif "FROM quarterreport" in text:
            self._rows = [dict(row) for row in self.connection.report_rows]
        else:
            self._rows = []

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def close(self):
        self.connection.cursor_closed = True


class FakeConnection:
    def __init__(self, stock_rows=(), report_rows=(), broken=False):
        self.stock_rows = list(stock_rows)
        self.report_rows = list(report_rows)
        self.broken = broken
        self.cursor_closed = False

    def cursor(self):
        if self.broken:
            raise RuntimeError("连接断了")
        return FakeCursor(self)

    def rollback(self):
        pass


class FakeSource:
    def __init__(self, connection):
        self.connection = connection


def _app(connection=None, source_broken=False):
    app = create_app({"TESTING": True})
    if source_broken:
        def factory():
            raise RuntimeError("数据源造不出来")
    else:
        factory = lambda: FakeSource(connection or FakeConnection())  # noqa: E731
    app.extensions["admin_source_factory"] = factory
    return app


def _codes(html):
    """表格里点得动的基金代码（首列 <strong> 里的 6 位数字）。"""
    return re.findall(r'<a href="/holdings/quarter/(?:SH|SZ)\d{6}">'
                      r'<strong>(\d{6})</strong></a>', html)


class IndexPageTest(unittest.TestCase):
    def setUp(self):
        self.client = _app().test_client()
        self.html = self.client.get("/holdings/quarter").get_data(as_text=True)

    def test_page_title_and_lead(self):
        self.assertIn("修改季报持仓", self.html)
        self.assertIn("_qdiimix.php", self.html)
        self.assertIn("qdiimix_holdings_symbols()", self.html)

    def test_all_32_targets_have_a_detail_link(self):
        targets = list(qdiimix_holdings_symbols())
        self.assertEqual(32, len(targets))
        for symbol in targets:
            self.assertIn('href="/holdings/quarter/%s"' % symbol, self.html)
        self.assertEqual(32, len(_codes(self.html)))

    def test_examples_from_the_request_are_six_digit_codes(self):
        self.assertIn("161815", _codes(self.html))
        self.assertIn("160216", _codes(self.html))
        self.assertIn("<code>SZ161815</code>", self.html)
        self.assertIn("<code>SZ160216</code>", self.html)

    def test_back_link_to_admin_console(self):
        self.assertIn('href="/admin"', self.html)

    def test_detail_link_resolves_to_the_edit_page(self):
        """点代码真的能进编辑页（不是 404，也不是索引页自己）。"""
        response = self.client.get("/holdings/quarter/SZ161815")
        self.assertEqual(200, response.status_code)
        self.assertIn("SZ161815 季报持仓", response.get_data(as_text=True))


class AdminConsoleLinkTest(unittest.TestCase):
    def test_quarter_chip_is_a_static_link_to_the_index(self):
        html = _app().test_client().get("/admin").get_data(as_text=True)
        self.assertIn('href="/holdings/quarter"', html)
        self.assertNotIn('data-symbol-href="/holdings/quarter"', html)
        self.assertNotIn('href="/holdings/quarter/__SYMBOL__"', html)

    def test_clicking_the_chip_lands_on_the_index(self):
        client = _app().test_client()
        html = client.get("/admin").get_data(as_text=True)
        match = re.search(r'<a class="option-chip" href="(/holdings/quarter)"', html)
        self.assertIsNotNone(match)
        response = client.get(match.group(1))
        self.assertEqual(200, response.status_code)
        self.assertIn("修改季报持仓", response.get_data(as_text=True))


class DatabaseGracefulDegradationTest(unittest.TestCase):
    def test_broken_cursor_still_lists_all_codes(self):
        """后台索引页是出问题时最需要能打开的页 —— 名称/日期查不到就算了。"""
        client = _app(FakeConnection(broken=True)).test_client()
        response = client.get("/holdings/quarter")
        self.assertEqual(200, response.status_code)
        self.assertEqual(32, len(_codes(response.get_data(as_text=True))))

    def test_broken_source_factory_still_lists_all_codes(self):
        client = _app(source_broken=True).test_client()
        response = client.get("/holdings/quarter")
        self.assertEqual(200, response.status_code)
        self.assertEqual(32, len(_codes(response.get_data(as_text=True))))

    def test_missing_stock_row_is_flagged(self):
        html = _app().test_client().get("/holdings/quarter").get_data(as_text=True)
        self.assertIn("stock 表里没有这个符号", html)

    def test_name_and_latest_date_are_shown_when_available(self):
        connection = FakeConnection(
            stock_rows=[{"id": 59, "symbol": "SZ161815", "name": "银华抗通胀主题"}],
            report_rows=[{"stock_id": "59", "latest": "2026-09-30", "cnt": 2}],
        )
        html = _app(connection).test_client().get(
            "/holdings/quarter").get_data(as_text=True)
        self.assertIn("银华抗通胀主题", html)
        self.assertIn("2026-09-30", html)
        self.assertIn(">2</td>", html)


if __name__ == "__main__":
    unittest.main()
