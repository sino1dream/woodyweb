"""``web/services/etf_dividend_service.py`` 的单测。

不打网络、不连数据库：``StockDividendSql`` / ``StockHistorySql`` 换成假对象
（``mock.patch.object``），引用对象用手写的 :class:`FakeRef`。

钉住四件事：

1. **两道闸门的顺序** —— 建不出 ref / ``stock_id`` 为空 → 整页无表；
   ``CountNetValue() == 0`` → 有标题与链接但**连表头都没有**；
2. **行装配** —— 当天取不到收盘价就整行跳过；「分红」过 ``rtrim0``、
   「价格」原样（``double(10,3)`` 的 ``'741.470'`` 不能被削成 ``741.47``）；
3. **比例** —— ``number_format(x, 2)`` 固定两位；
4. **外链分派** —— A 股走新浪六位数字路径、H 股走港股路径、其余
   （美股）**不给链接**（Yahoo 那一支已去掉）。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT_DIR = Path(__file__).resolve().parents[3]
PYTHON_DIR = ROOT_DIR / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from web.bootstrap import install_paths  # noqa: E402

install_paths()

from web.services import etf_dividend_service  # noqa: E402
from web.services.etf_dividend_service import (  # noqa: E402
    ETF_DIVIDEND_DISPLAY,
    build_etf_dividend,
    sina_dividend_link,
)


class FakeSource:
    """只为让服务层认为"有连接"；真 SQL 已经被 patch 掉了。"""

    def __init__(self, connection=None):
        self.connection = connection


class FakeRef:
    """够用的引用对象：服务只调 ``get_stock_id`` / ``count_net_value``。"""

    def __init__(self, stock_id=4, net_value_count=7):
        self.stock_id = stock_id
        self.net_value_count = net_value_count

    def get_stock_id(self):
        return self.stock_id

    def count_net_value(self):
        return self.net_value_count


class FakeDividendSql:
    """``StockDividendSql`` 的替身：按 id 返回预置行（已按 date DESC）。"""

    rows: list = []

    def __init__(self, connection):
        self.connection = connection

    def get_all(self, stock_id, start=0, num=0):
        return list(self.rows)


class FakeHistorySql:
    """``StockHistorySql`` 的替身：日期 → 收盘价字符串。"""

    closes: dict = {}

    def __init__(self, connection):
        self.connection = connection

    def get_close_text(self, stock_id, date):
        return self.closes.get(str(date))


def _patched(dividend_rows, closes):
    FakeDividendSql.rows = dividend_rows
    FakeHistorySql.closes = closes
    return (
        mock.patch.object(etf_dividend_service, "StockDividendSql", FakeDividendSql),
        mock.patch.object(etf_dividend_service, "StockHistorySql", FakeHistorySql),
    )


class SinaLinkTest(unittest.TestCase):
    """``GetStockDividendUrl()`` 的剩余两派（Yahoo 那派已去掉）。"""

    def test_a_share_uses_six_digit_path(self):
        link = sina_dividend_link("SZ161125")
        self.assertEqual(link["text"], "Sina" + ETF_DIVIDEND_DISPLAY)
        self.assertEqual(
            link["url"],
            "https://vip.stock.finance.sina.com.cn/corp/go.php/vISSUE_ShareBonus/stockid/161125.phtml",
        )

    def test_a_share_etf_also_gets_the_link(self):
        """``IsSymbolA()`` 只看六位数字，不区分股票与 ETF/LOF —— 照抄。"""
        self.assertIsNotNone(sina_dividend_link("SH513100"))

    def test_h_share_uses_full_symbol(self):
        #: H 股在 palmmicro 的符号体系里就是**五位纯数字**（`00700`），
        #: 不是 `HK00700` —— `IsSymbolH()` 看的就是"首字符 0 + 五位数字"。
        link = sina_dividend_link("00700")
        self.assertEqual(
            link["url"],
            "https://stock.finance.sina.com.cn/hkstock/dividends/00700.html",
        )

    def test_us_symbol_has_no_link(self):
        """美股不给外链：Yahoo 那个域名对大陆返回服务停止公告。"""
        self.assertIsNone(sina_dividend_link("QQQ"))


class GateTest(unittest.TestCase):
    """两道闸门。"""

    def test_no_ref_returns_none(self):
        self.assertIsNone(build_etf_dividend("QQQ", source=FakeSource(), ref=None))

    def test_missing_stock_id_returns_none(self):
        """``stock`` 表里没有这个符号 → 与"建不出引用对象"同一后果。"""
        self.assertIsNone(
            build_etf_dividend("NOSUCH", source=FakeSource(), ref=FakeRef(stock_id=None))
        )

    def test_no_net_value_leaves_no_table(self):
        page = build_etf_dividend(
            "SH600030", source=FakeSource(), ref=FakeRef(net_value_count=0)
        )
        self.assertIsNotNone(page)
        self.assertIsNone(page["table"])
        self.assertEqual(page["title"], "SH600030" + ETF_DIVIDEND_DISPLAY)

    def test_no_connection_leaves_no_table(self):
        """有净值但借不到连接：页面照出，表没有（PHP 那边查询失败同理）。"""
        page = build_etf_dividend(
            "QQQ", source=FakeSource(connection=None), ref=FakeRef()
        )
        self.assertIsNotNone(page)
        self.assertIsNone(page["table"])


class RowTest(unittest.TestCase):
    """行装配与列口径。"""

    def _build(self, dividend_rows, closes, symbol="QQQ"):
        p1, p2 = _patched(dividend_rows, closes)
        with p1, p2:
            return build_etf_dividend(symbol, source=FakeSource(object()), ref=FakeRef())

    def test_rows_keep_three_decimals_on_price(self):
        """价格是 ``double(10,3)``：``'741.470'`` 不能变成 ``'741.47'``。"""
        page = self._build(
            [{"date": "2026-09-21", "close": 0.751474}],
            {"2026-09-21": "741.470"},
        )
        cells = page["table"]["rows"][0].cells
        self.assertEqual(cells[1], "0.751474")
        self.assertEqual(cells[2], "741.470")

    def test_dividend_is_rtrim0_but_price_is_not(self):
        page = self._build(
            [{"date": "2026-09-03", "close": 0.5}],
            {"2026-09-03": "717.670"},
        )
        cells = page["table"]["rows"][0].cells
        self.assertEqual(cells[1], "0.5")
        self.assertEqual(cells[2], "717.670")

    def test_ratio_is_two_decimals(self):
        page = self._build(
            [{"date": "2026-09-21", "close": 0.751474}],
            {"2026-09-21": "741.470"},
        )
        # 0.751474 / 741.470 * 100 = 0.10134… → number_format(..., 2)
        self.assertEqual(page["table"]["rows"][0].cells[3], "0.10")

    def test_missing_close_skips_the_whole_row(self):
        page = self._build(
            [
                {"date": "2026-09-21", "close": 0.751474},
                {"date": "2020-01-01", "close": 0.3},
            ],
            {"2026-09-21": "741.470"},
        )
        rows = page["table"]["rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].cells[0], "2026-09-21")

    def test_zero_price_row_is_skipped(self):
        """刻意偏离：PHP 8 在价格 0 时 ``1/0`` 抛错、整页 500；这里跳过该行。"""
        page = self._build(
            [{"date": "2026-09-21", "close": 0.5}],
            {"2026-09-21": "0.000"},
        )
        self.assertEqual(page["table"]["rows"], [])

    def test_empty_close_text_is_skipped(self):
        """空串在 PHP 里是假值 —— 与 ``None`` 一样整行不出现。"""
        page = self._build(
            [{"date": "2026-09-21", "close": 0.5}],
            {"2026-09-21": ""},
        )
        self.assertEqual(page["table"]["rows"], [])

    def test_table_id_and_columns(self):
        page = self._build(
            [{"date": "2026-09-21", "close": 0.751474}],
            {"2026-09-21": "741.470"},
        )
        table = page["table"]
        self.assertEqual(table["id"], "QQQetfdividend")
        self.assertEqual([c["text"] for c in table["columns"]], ["日期", "分红", "价格", "比例(%)"])

    def test_dividend_table_absent_still_returns_page(self):
        """``stockdividend`` 表不存在（本地 dev）不能抛到路由层换成 500。"""

        class BoomSql:
            def __init__(self, connection):
                pass

            def get_all(self, stock_id, start=0, num=0):
                raise RuntimeError("Table 'stock_data.stockdividend' doesn't exist")

        with mock.patch.object(etf_dividend_service, "StockDividendSql", BoomSql), \
                mock.patch.object(etf_dividend_service, "StockHistorySql", FakeHistorySql):
            page = build_etf_dividend("QQQ", source=FakeSource(object()), ref=FakeRef())
        self.assertIsNotNone(page)
        self.assertIsNotNone(page["table"])
        self.assertEqual(page["table"]["rows"], [])


class RouteTest(unittest.TestCase):
    """路由层：页面能开，三种状态各自出对文案。"""

    def _client(self, ref):
        from web.app import create_app

        class FakeReferenceService:
            def __init__(self):
                self.source = None

        app = create_app(
            {"TESTING": True},
            reference_service_factory=lambda: FakeReferenceService(),
            reference_refs_builder=lambda symbols: [] if ref is None else [ref],
        )
        return app.test_client()

    def test_ref_missing_shows_empty_state(self):
        response = self._client(None).get("/stock/NOSUCH/etfdividend")
        self.assertEqual(response.status_code, 200)
        self.assertIn("没有这个符号的参考数据", response.get_data(as_text=True))

    def test_no_net_value_skips_table(self):
        client = self._client(FakeRef(net_value_count=0))
        body = client.get("/stock/SH600030/etfdividend").get_data(as_text=True)
        self.assertIn("没有净值记录", body)
        self.assertNotIn("etf-dividend-table", body)

    def test_sina_link_rendered_for_a_share(self):
        client = self._client(FakeRef(net_value_count=3))
        body = client.get("/stock/SZ161125/etfdividend").get_data(as_text=True)
        self.assertIn("stockid/161125.phtml", body)

    def test_title_and_history_links(self):
        client = self._client(FakeRef(net_value_count=3))
        body = client.get("/stock/QQQ/etfdividend").get_data(as_text=True)
        self.assertIn("QQQ" + ETF_DIVIDEND_DISPLAY, body)
        self.assertIn("/history/stockhistory/QQQ", body)
        self.assertIn("/history/netvalueclose/QQQ", body)


if __name__ == "__main__":
    unittest.main()
