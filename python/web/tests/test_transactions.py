"""交易管理演示页（/transactions）的路由与演示数据过滤测试。

对应 PHP ``_mystocktransaction.php``：整组交易明细，带 ``?symbol=`` 时只看
那一支标的。段落口径本身由 ``test_transaction.py`` 覆盖，这里验证路由参数
（分组切换、按标的过滤）与 :func:`web.routes.account_demo_data
._demo_transaction_records` 的过滤。
"""

import re
import sys
import unittest

sys.path.insert(0, r"F:\woody\woodyweb\python")

from web.app import create_app  # noqa: E402
from web.routes.account_demo_data import (  # noqa: E402
    DEMO_GROUP_NAMES,
    _demo_transaction_records,
)


def _transaction_rows(html):
    match = re.search(r'class="data-table transaction-table".*?</table>"', html, re.S)
    if not match:
        # 退一步：直接数整页 transaction-table 里的 <tr>
        match = re.search(r'class="data-table transaction-table".*?</table>', html, re.S)
    if not match:
        return -1
    return match.group(0).count("<tr>") - 1


class TransactionsRouteTest(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True})
        self.client = self.app.test_client()

    def test_default_page(self):
        resp = self.client.get("/transactions")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("交易", body)
        # 默认分组 2 → 5 条
        self.assertEqual(_transaction_rows(body), 5)

    def test_switch_group(self):
        for gid in DEMO_GROUP_NAMES:
            resp = self.client.get("/transactions?groupid=" + gid)
            self.assertEqual(resp.status_code, 200)
            expected = len(_demo_transaction_records(gid))
            self.assertEqual(_transaction_rows(resp.get_data(as_text=True)), expected)

    def test_filter_by_symbol(self):
        # 分组 2 里 SZ161125 两次、TLT 一次、KWEB 两次
        self.assertEqual(
            _transaction_rows(
                self.client.get("/transactions?groupid=2&symbol=SZ161125")
                .get_data(as_text=True)),
            2)
        self.assertEqual(
            _transaction_rows(
                self.client.get("/transactions?groupid=2&symbol=TLT")
                .get_data(as_text=True)),
            1)
        self.assertEqual(
            _transaction_rows(
                self.client.get("/transactions?groupid=1&symbol=HK00700")
                .get_data(as_text=True)),
            1)

    def test_invalid_group_falls_back(self):
        resp = self.client.get("/transactions?groupid=999")
        self.assertEqual(resp.status_code, 200)
        # 回退到默认分组（2）→ 5 条
        self.assertEqual(_transaction_rows(resp.get_data(as_text=True)), 5)


class TransactionRecordsFilterTest(unittest.TestCase):
    def test_records_by_group(self):
        self.assertEqual(len(_demo_transaction_records("1")), 2)
        self.assertEqual(len(_demo_transaction_records("2")), 5)

    def test_records_by_symbol(self):
        self.assertEqual(len(_demo_transaction_records("2", "SZ161125")), 2)
        self.assertEqual(len(_demo_transaction_records("2", "TLT")), 1)
        self.assertEqual(len(_demo_transaction_records("2", "NOPE")), 0)

    def test_records_group_and_symbol(self):
        # 分组 1 没有 SZ161125
        self.assertEqual(len(_demo_transaction_records("1", "SZ161125")), 0)


if __name__ == "__main__":
    unittest.main()
