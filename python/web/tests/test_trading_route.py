"""基金详情页的「五档交易」接线测试（`web/routes/fund.py`）。

**单独成一个模块的原因**：它需要 Flask（``create_app``）。托管的 Python 3.13
没装 flask，而 ``test_trading.py`` 那 39 个用例是**不依赖 flask** 的、
两套解释器都要能跑。把路由测试混进去会让整个模块在 3.13 上变成 import error，
把一个"服务层可测"的属性白白丢掉。

**不联网**：引用对象由 ``trading_refs_builder`` 注入。
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

from web.services.trading_service import TradingService  # noqa: E402
from web.tests.test_trading import FakeRef, full_ref  # noqa: E402


class FundDetailRouteTest(unittest.TestCase):
    """基金详情页接线：注入假引用对象，**不打网络、不连数据库**。"""

    class FakeFundRef:
        """基金引用对象：盘口在 stock_ref 上，三档估值在自己身上。"""

        def __init__(self, symbol="SZ162411", official=None, fair=None):
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

    def setUp(self):
        from web.app import create_app
        from web.services.fund_service import FundService
        from web.services.trading_service import TradingService
        from web.tests.test_web import make_source

        self.refs = [self.FakeFundRef(official=4.545, fair=4.549)]
        self.app = create_app(
            {"TESTING": True},
            fund_service_factory=lambda: FundService(make_source()),
            trading_service_factory=lambda: TradingService(),
            trading_refs_builder=lambda symbols: self.refs,
        )
        self.client = self.app.test_client()

    def test_page_renders_the_trading_table(self):
        html = self.client.get("/fund/SZ162411").get_data(as_text=True)
        self.assertIn('class="data-table trading-table"', html)
        self.assertIn('id="trading"', html)
        for text in ("交易", "价格", "数量", "官方溢价", "参考溢价"):
            self.assertIn(text, html)

    def test_row_colors_survive_the_template(self):
        html = self.client.get("/fund/SZ162411").get_data(as_text=True)
        self.assertIn('class="bg-orange"', html)
        self.assertIn('class="bg-yellow"', html)

    def test_title_sentence_is_plain_text(self):
        """PHP 把外链 HTML 拼进句子；本版必须是纯文本，否则会被转义成一串标签。"""
        html = self.client.get("/fund/SZ162411").get_data(as_text=True)
        self.assertIn("SZ162411当前5档交易价格相对于EST4.545|4.549的溢价", html)
        self.assertIn("五档交易", html)

    def test_column_count_matches_cell_count_on_the_page(self):
        """表格的两种位数在这里应当相等 —— 有错位时这条先炸。"""
        table = TradingService().fund_table(self.refs[0])
        self.assertEqual(table["cell_count"], table["column_count"])

    def test_without_order_book_there_is_no_table(self):
        """没有挂单量就整段跳过，页面给一句说明而不是摆一张空盘口。"""
        self.refs = [self.FakeFundRef()]
        self.refs[0].ref = FakeRef()   # 五档全空
        html = self.client.get("/fund/SZ162411").get_data(as_text=True)
        self.assertNotIn("trading-table", html)
        self.assertIn("暂无五档盘口数据", html)


if __name__ == "__main__":
    unittest.main()
