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

    def test_without_est_ref_there_is_no_extra_column(self):
        """假的基金引用对象没有 ``GetEstRef()`` —— 此时退回通用五档表（四列）。

        这条是对别处的回归保护：**补列是可选的**，别把它的缺失变成"少一格错行"。
        """
        html = self.client.get("/fund/SZ162411").get_data(as_text=True)
        table = TradingService().fund_table(self.refs[0])

        self.assertNotIn("QQQ", html)
        self.assertEqual(table["cell_count"], table["column_count"])


class QdiiColumnRouteTest(unittest.TestCase):
    """五档表最右边那一列的接线（``_qdiigroup.php:96``）。

    提供的东西比上面那个类多一些：基金引用对象要有 ``GetEstRef()`` /
    ``GetEstValue()``，还要有一个能答 ``fundpair`` 的数据源 —— 该列要查
    "谁跟踪中间层标的"。数据源一律由假对象顶替，**不连数据库**。
    """

    class EstRef:
        def __init__(self, symbol="^NDX"):
            self._symbol = symbol

        def get_symbol(self):
            return self._symbol

        def get_display(self):
            return self._symbol

        def get_price_display(self, value=None, prev=None):
            return "%.3f" % float(value if value is not None else 0.0)

    class FundRef(FundDetailRouteTest.FakeFundRef):
        """比父类多两样：中间层标的 + 基金净值口径 → 中间层点位的换算。"""

        factor = 2459.0

        def get_est_ref(self):
            return QdiiColumnRouteTest.EstRef()

        def get_est_value(self, price=False):
            try:
                return float(price) * self.factor
            except (TypeError, ValueError):
                return 0.0

    def setUp(self):
        from web.app import create_app
        from web.services.fund_service import FundService
        from web.services.trading_service import TradingService
        from web.tests.test_qdii_trading_column import _FakeSource
        from web.tests.test_web import make_source

        self.refs = [self.FundRef(official=4.545)]
        self.source = _FakeSource({"QQQ": 4})
        self.app = create_app(
            {"TESTING": True},
            fund_service_factory=lambda: FundService(make_source()),
            trading_service_factory=lambda: TradingService(),
            trading_refs_builder=lambda symbols: self.refs,
            reference_service_factory=lambda: type(
                "FakeReferenceService", (), {"source": self.source})(),
        )
        self.client = self.app.test_client()

    def _html(self):
        return self.client.get("/fund/SZ162411").get_data(as_text=True)

    def test_the_column_header_reaches_the_page(self):
        """``^NDX|QQQ价格`` —— 中间层标的 + 杠杆标的 + "价格"后缀。"""
        self.assertIn("> ^NDX|QQQ价格 <", _squeezed(self._html()))

    def test_the_converted_price_reaches_the_page(self):
        """涨停那档 ``5.372``：中间层 ``5.372 * 2459``，杠杆标的再过 ``EstFromPair``。

        两格的小数位不同（3 位 vs 2 位）是**各引用对象自己的精度**决定的，
        顺便把"两个格子没共用同一个格式化口径"这条钉住。
        """
        self.assertIn(">13209.748|13209.75<", _squeezed(self._html()))

    def test_column_and_cell_counts_stay_aligned(self):
        """补列最容易出错的地方是"表头加了、某些行没加"。"""
        from web.services.qdii_trading_column import (
            PAIR_ESTIMATE_WIDTH,
            build_leverage_references,
            build_trading_column,
        )

        refs = build_leverage_references("^NDX", self.source)
        column = build_trading_column(self.refs[0], refs)
        table = TradingService().fund_table(self.refs[0], callback=column,
                                           callback_width=PAIR_ESTIMATE_WIDTH)

        self.assertEqual(table["cell_count"], table["column_count"])

    def test_the_wide_width_is_used_for_this_column(self):
        """这一列塞的是 ``^NDX|QQQ价格``，用默认那个短宽度会挤成一团。"""
        self.assertIn('style="width: 200px"', _squeezed(self._html()))


def _squeezed(html):
    """压掉标签之间的换行缩进，便于逐格断言。"""
    import re

    return re.sub(r"\s+", " ", html)


if __name__ == "__main__":
    unittest.main()
