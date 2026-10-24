"""交易管理页（/transactions）的路由与写端点测试。

对应 PHP ``_mystocktransaction.php``（明细段落）+ ``_editstocktransaction.php``
（修改表单）+ ``_submittransaction.php``（写端点）。段落口径本身由
``test_transaction.py`` 覆盖，这里验证：

1. **接真库后的取数**：分组切换、``?symbol=`` 只看那一支、最新 10 条；
2. **表单默认值**：价格取最新收盘、数量取最近一笔（去负号）；
3. **四个 POST 写端点**：新增 / 删除 / 校准 / 清空备注都真的改库。

数据源用 ``transactions_source_factory`` 注入内存假库
（复用 ``test_submittransaction`` 的四表假库，多塞两张日频表）。
"""

import re
import sys
import unittest

sys.path.insert(0, r"F:\woody\woodyweb\python")

from web.app import create_app  # noqa: E402
from web.bootstrap import install_paths  # noqa: E402

install_paths()

from core.submittransaction import SUBMIT_NEW  # noqa: E402
from test_submittransaction import (  # noqa: E402
    GROUP_ID,
    ITEM_161125,
    ITEM_513100,
    STOCK_ID_161125,
    STOCK_ID_513100,
    FakeDB,
    _groups,
    _items,
    _stocks,
    _transactions,
)


class FakeSource(FakeDB):
    """``MySqlFundDataSource`` 的最小替身：假库 + 两个符号换算方法。"""

    def __init__(self):
        super().__init__(_groups(), _items(), _transactions(), _stocks())
        self.tables["stockhistory"] = [
            {"id": 1, "stock_id": STOCK_ID_161125, "date": "2026-10-23",
             "close": "1.680", "volume": "0", "adjclose": "1.680"},
            {"id": 2, "stock_id": STOCK_ID_513100, "date": "2026-10-23",
             "close": "2.399", "volume": "0", "adjclose": "2.399"},
        ]
        self.tables["netvaluehistory"] = [
            {"id": 1, "stock_id": STOCK_ID_161125, "date": "2026-10-20",
             "close": "1.6500"},
        ]

    # -- MySqlFundDataSource 的两个方法（路由直接用） ----------------------
    def get_stock_id(self, symbol):
        for row in self.tables["stock"]:
            if row["symbol"] == symbol:
                return str(row["id"])
        return None

    def get_stock_symbol(self, stock_id):
        for row in self.tables["stock"]:
            if str(row["id"]) == str(stock_id):
                return row["symbol"]
        return None


def _transaction_rows(html):
    match = re.search(r'class="data-table transaction-table".*?</table>', html,
                      re.S)
    if not match:
        return -1
    return match.group(0).count("<tr>") - 1


class TransactionsRouteTest(unittest.TestCase):
    def setUp(self):
        self.source = FakeSource()
        self.app = create_app(
            {"TESTING": True},
            transactions_source_factory=lambda: self.source)
        self.client = self.app.test_client()

    # -- 取数 ---------------------------------------------------------------
    def test_default_page_lists_real_records(self):
        body = self.client.get("/transactions").get_data(as_text=True)
        self.assertEqual(_transaction_rows(body), 2)      # 种子里两条

    def test_switch_group(self):
        body = self.client.get("/transactions?groupid=%d" % GROUP_ID
                               ).get_data(as_text=True)
        self.assertEqual(_transaction_rows(body), 2)

    def test_filter_by_symbol(self):
        """``?symbol=`` 只看那一支：SH513100 没有成交 → 0 行。"""
        body = self.client.get("/transactions?symbol=SH513100"
                               ).get_data(as_text=True)
        self.assertEqual(_transaction_rows(body), 0)
        body = self.client.get("/transactions?symbol=SZ161125"
                               ).get_data(as_text=True)
        self.assertEqual(_transaction_rows(body), 2)

    def test_unknown_group_is_empty(self):
        body = self.client.get("/transactions?groupid=999"
                               ).get_data(as_text=True)
        self.assertEqual(_transaction_rows(body), 0)

    # -- 表单 ---------------------------------------------------------------
    def test_form_defaults_from_real_data(self):
        """价格取最新收盘、数量取最近一笔（卖出 -500 → 500）。"""
        body = self.client.get("/transactions").get_data(as_text=True)
        self.assertIn('name="symbol"', body)
        self.assertIn('name="quantity"', body)
        self.assertIn('value="%s"' % SUBMIT_NEW, body)
        self.assertIn("2.399", body)      # 首个成员（SH513100）的最新收盘

    def test_edit_page(self):
        response = self.client.get("/transactions/edit?groupid=%d&edit=2"
                                   % GROUP_ID)
        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn('value="500"', body)        # -500 去负号
        self.assertIn('name="edit"', body)

    def test_edit_missing_record_redirects(self):
        response = self.client.get("/transactions/edit?groupid=%d&edit=999"
                                   % GROUP_ID)
        self.assertEqual(302, response.status_code)

    # -- 写端点（一律 POST） ------------------------------------------------
    def test_submit_new(self):
        response = self.client.post("/transactions/submit", data={
            "submit": SUBMIT_NEW, "symbol": ITEM_161125, "groupid": GROUP_ID,
            "quantity": "300", "price": "1.70", "type": "1",
        })
        self.assertEqual(302, response.status_code)
        self.assertEqual(len(self.source.transactions()), 3)
        row = self.source.transactions()[-1]
        self.assertEqual(row["quantity"], "300")
        # 成员预统计被回填：1000 - 500 + 300
        item = [r for r in self.source.items()
                if str(r["id"]) == str(ITEM_161125)][0]
        self.assertEqual(item["quantity"], 800)

    def test_delete_endpoint(self):
        response = self.client.post("/transactions/delete", data={
            "trans_id": 2, "groupid": GROUP_ID})
        self.assertEqual(302, response.status_code)
        self.assertEqual([r["id"] for r in self.source.transactions()], [1])

    def test_adjust_endpoint(self):
        response = self.client.post("/transactions/adjust", data={
            "trans_id": 1, "netvalue": "1.10", "groupid": GROUP_ID})
        self.assertEqual(302, response.status_code)
        row = [r for r in self.source.transactions() if r["id"] == 1][0]
        self.assertEqual(row["price"], "1.10")

    def test_empty_endpoint(self):
        response = self.client.post("/transactions/empty", data={
            "trans_id": 2, "groupid": GROUP_ID})
        self.assertEqual(302, response.status_code)
        row = [r for r in self.source.transactions() if r["id"] == 2][0]
        self.assertEqual(row["remark"], "")

    def test_get_write_endpoint_rejected(self):
        """写端点只认 POST（PHP 那几个 GET 链接不复活）。"""
        self.assertEqual(405,
                         self.client.get("/transactions/delete").status_code)
        self.assertEqual(405,
                         self.client.get("/transactions/submit").status_code)


if __name__ == "__main__":
    unittest.main()
