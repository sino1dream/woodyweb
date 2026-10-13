"""基金详情页接入 stockhistory + netvalueclose 两段（CK1）。

对应 ``php/ui/stockhistoryparagraph.php`` 与 ``php/ui/netvaluecloseparagraph.php``。
两段都复用 ``history_service`` 的 ``stockhistory`` / ``netvalueclose`` kind，
与 ``/history/*`` 页面同源。**离线**：history 服务由假工厂注入，不给数据库压力。

测试重点：
* 有数据时两段都出现（标题 + 表容器类名都命中）；
* 无数据时整段隐藏且不让页面 500（与 PHP ``CountNetValue()==0`` / 空结果守卫一致）。
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


class FakeFundRef:
    """基金引用对象：盘口在 stock_ref 上，三档估值在自己身上。"""

    def __init__(self, symbol="SZ162411", official=None, fair=None):
        from web.tests.test_trading import full_ref

        self._symbol = symbol
        self._official = official
        self._fair = fair
        self.ref = full_ref(symbol=symbol)

    def get_stock_ref(self):
        return self.ref

    def get_official_net_value(self):
        return self._official

    def get_fair_net_value(self):
        return self._fair


class FakeHistoryService:
    """假历史服务：按 ``with_data`` 决定 stockhistory / netvalueclose 有没有行。

    ``history_table`` 宏只读 ``columns`` / ``rows``（Jinja 把 dict 键当属性解析），
    ``_build_*`` 用 ``table.get("rows")``（dict 方法），所以返回普通 dict 即可，
    不必实现真正的 ``TableRow``。
    """

    def __init__(self, with_data=True):
        self.with_data = with_data

    def get_table(self, kind, symbol, start=0, num=10):
        if kind in ("stockhistory", "netvalueclose"):
            title = "历史价格" if kind == "stockhistory" else "净值价格比较"
            if self.with_data:
                return {
                    "title": title,
                    "columns": [{"text": "日期"}, {"text": "价格"}],
                    "rows": [{"cells": ["2026-09-30", "4.796"], "match": False}],
                }
            return {"title": title, "columns": [{"text": "日期"}], "rows": []}
        # 其它 kind（shareshistory 等）一律空行，避免份额表干扰断言
        return {"title": kind, "columns": [], "rows": []}


class FundDetailHistoryTest(unittest.TestCase):
    """基金详情页接线：注入假引用对象与假历史服务，不打网络、不连库。"""

    def setUp(self):
        from web.app import create_app
        from web.services.fund_service import FundService
        from web.services.trading_service import TradingService
        from web.tests.test_web import make_source

        self.refs = [FakeFundRef(official=4.545, fair=4.549)]
        self.app = create_app(
            {"TESTING": True},
            fund_service_factory=lambda: FundService(make_source()),
            trading_service_factory=lambda: TradingService(),
            trading_refs_builder=lambda symbols: self.refs,
            history_service_factory=lambda: FakeHistoryService(with_data=True),
        )
        self.client = self.app.test_client()

    def test_history_sections_show_when_data_present(self):
        html = self.client.get("/fund/SZ162411").get_data(as_text=True)
        self.assertIn('class="data-table history-table netvalueclose-table"', html)
        self.assertIn('class="data-table history-table stockhistory-table"', html)
        self.assertIn("历史价格", html)
        self.assertIn("净值价格比较", html)
        # 表容器类名唯一，确认两套渲染都接到了；"查看全部"链接指向 /history 同源页
        self.assertIn('href="/history/stockhistory/SZ162411"', html)
        self.assertIn('href="/history/netvalueclose/SZ162411"', html)

    def test_history_sections_hidden_without_data(self):
        self.app.extensions["history_service_factory"] = (
            lambda: FakeHistoryService(with_data=False)
        )
        response = self.client.get("/fund/SZ162411")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertNotIn("stockhistory-table", html)
        self.assertNotIn("netvalueclose-table", html)


if __name__ == "__main__":
    unittest.main()
