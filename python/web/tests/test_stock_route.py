"""/stock 与 /api/stock 的接线测试（`web/routes/stock.py`）。

**单独成一个模块的原因**：它需要 Flask（``create_app`` / ``url_for``）。
托管的 Python 3.13 没装 flask，而 ``test_stock.py`` 那 29 个用例是
**不依赖 flask** 的。分开以后服务层在两套解释器上都能跑，
路由测试只在装了 flask 的环境跑。

**不连数据库**：``StockSql`` 接的是假连接。
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

from web.tests.test_stock import FakeConnection  # noqa: E402


class StockRouteTest(unittest.TestCase):
    """``/stock`` 接线：**不连数据库**，用假连接替掉 StockSql。"""

    def setUp(self):
        from stocksql import StockSql
        from web.app import create_app
        from web.services.stock_service import StockService

        connection = FakeConnection()
        self.app = create_app(
            {"TESTING": True},
            stock_service_factory=lambda: StockService(StockSql(connection)),
        )
        self.client = self.app.test_client()

    def test_page_renders_the_stock_table(self):
        html = self.client.get("/stock").get_data(as_text=True)
        self.assertIn('class="data-table stock-table"', html)
        self.assertIn('id="stock"', html)
        self.assertIn("代码", html)
        self.assertIn("名称", html)

    def test_first_cell_links_to_the_fund_page(self):
        html = self.client.get("/stock").get_data(as_text=True)
        self.assertIn('<a href="/fund/SZ161130">SZ161130</a>', html)

    def test_name_cell_links_to_xueqiu(self):
        html = self.client.get("/stock").get_data(as_text=True)
        self.assertIn("https://xueqiu.com/S/SZ161130", html)

    def test_paging_links_use_start_and_num(self):
        # 第 2 页（共 3 行、每页 1 行）：prev=0、next=2
        html = self.client.get("/stock?start=1&num=1").get_data(as_text=True)
        self.assertIn("start=0&amp;num=1", html)   # 上一页
        self.assertIn("start=2&amp;num=1", html)   # 下一页

    def test_a_page_beyond_the_first_gets_a_first_link(self):
        """``$iPrevStart != 0`` 才给"首页" —— 第二页的"上一页"已经是 0 了。

        只比对 ``<p class="paging">`` 那一块：站点导航里也有"首页"两个字。
        """
        second = _paging_block(self.client.get("/stock?start=1&num=1").get_data(as_text=True))
        third = _paging_block(self.client.get("/stock?start=2&num=1").get_data(as_text=True))
        self.assertNotIn("首页", second)
        self.assertIn("首页", third)

    def test_api_returns_rows_with_links_as_data(self):
        payload = self.client.get("/api/stock").get_json()
        self.assertEqual(payload["count"], 3)
        self.assertEqual(payload["rows"][0]["href"], "/fund/SZ161130")
        self.assertEqual(payload["rows"][0]["hrefs"]["1"],
                         "https://xueqiu.com/S/SZ161130")
        # cells 里不能有 HTML —— 拼 HTML 是模板的职责，放到数据里迟早被转义两次
        self.assertNotIn("<a", str(payload["rows"][0]["cells"]))

    def test_database_unavailable_page_still_opens(self):
        """取数失败时降级成空表，而不是整页 500。"""
        from stocksql import StockSql

        app = _app_with_failing_sql(StockSql)
        html = app.test_client().get("/stock").get_data(as_text=True)
        self.assertIn("stock 表里暂无符号", html)


def _paging_block(html: str) -> str:
    """取出翻页那一块，避免断言撞上站点导航里的同名词。"""
    import re

    match = re.search(r'<p class="paging">(.*?)</p>', html, re.S)
    return match.group(1) if match else ""


def _app_with_failing_sql(stock_sql_class):
    from web.app import create_app
    from web.services.stock_service import StockService

    class BoomConnection:
        def cursor(self):
            raise RuntimeError("no database")

    return create_app(
        {"TESTING": True},
        stock_service_factory=lambda: StockService(stock_sql_class(BoomConnection())),
    )


if __name__ == "__main__":
    unittest.main()
