"""分组演示页（/group）的路由与演示数据测试。

主要验证两件事：路由把"单组"的渲染接对了（资金汇总**只有这一组**、没有
末尾的"全部"行），以及 :mod:`web.routes.account_demo_data` 的查询助手
过滤正确。交易 / 资金两个段落本身的口径由 ``test_transaction.py`` /
``test_money.py`` 已覆盖，这里不再重复。
"""

import re
import sys
import unittest

sys.path.insert(0, r"F:\woody\woodyweb\python")

from web.app import create_app  # noqa: E402
from web.routes.account_demo_data import (  # noqa: E402
    DEMO_GROUP_NAMES,
    _demo_single_group,
    _demo_transaction_records,
)


def _table_rows(html, table_class):
    """返回某个 data-table 内的数据行数（去掉表头那一行）。"""
    match = re.search(r'class="data-table %s".*?</table>' % table_class, html, re.S)
    if not match:
        return -1
    return match.group(0).count("<tr>") - 1


def _money_block(html):
    """返回资金汇总表的 HTML 块（不含说明文字）。"""
    match = re.search(r'class="data-table money-table".*?</table>', html, re.S)
    return match.group(0) if match else ""


class GroupRouteTest(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True})
        self.client = self.app.test_client()

    def test_default_page(self):
        resp = self.client.get("/group")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("分组", body)
        # 默认分组 1 = 人民币 + 港币 → 2 数据行
        self.assertEqual(_table_rows(body, "money-table"), 2)
        # 单组页面资金表不应出现"全部"行（只查资金表块，避开说明文字里的提及）
        self.assertNotIn(">全部<", _money_block(body))

    def test_switch_group(self):
        for gid in DEMO_GROUP_NAMES:
            resp = self.client.get("/group?groupid=" + gid)
            self.assertEqual(resp.status_code, 200)
            body = resp.get_data(as_text=True)
            # 每个分组都只有 2 数据行（人民币 + 该组持有的币种桶）
            self.assertEqual(_table_rows(body, "money-table"), 2)
            self.assertNotIn(">全部<", _money_block(body))

    def test_invalid_group_falls_back(self):
        resp = self.client.get("/group?groupid=999")
        self.assertEqual(resp.status_code, 200)
        # 回退到默认分组，仍正常出表，资金表无"全部"行
        self.assertNotIn(">全部<", _money_block(resp.get_data(as_text=True)))

    def test_transaction_table_present(self):
        resp = self.client.get("/group?groupid=2")
        body = resp.get_data(as_text=True)
        self.assertIn("transaction-table", body)
        # 分组 2 有 5 条交易
        self.assertEqual(_table_rows(body, "transaction-table"), 5)


class DemoDataHelperTest(unittest.TestCase):
    def test_single_group_returns_group_with_id(self):
        for gid in DEMO_GROUP_NAMES:
            group = _demo_single_group(gid)
            self.assertEqual(group.get_group_id(), gid)

    def test_transaction_records_by_group(self):
        self.assertEqual(len(_demo_transaction_records("1")), 2)
        self.assertEqual(len(_demo_transaction_records("2")), 5)

    def test_transaction_records_by_symbol(self):
        # 分组 2 里 SZ161125 出现两次（201 / 205）
        records = _demo_transaction_records("2", "SZ161125")
        self.assertEqual(len(records), 2)
        self.assertEqual({r["id"] for r in records}, {"201", "205"})
        # 不带 symbol 时返回整组
        self.assertEqual(len(_demo_transaction_records("2")), 5)
        # 不存在的分组返回空
        self.assertEqual(_demo_transaction_records("999"), [])


if __name__ == "__main__":
    unittest.main()
