"""基金详情页「基金指数对照表」的接线测试（`web/routes/fund.py`）。

**单独成一个模块的原因**：它需要 Flask（``create_app``），而
``test_qdii_fund_list.py`` 那 25 个用例是不依赖 flask 的、两套解释器都要能跑。

**不联网、不连数据库**：引用对象走 ``trading_refs_builder``，``$arLev`` 走
``fundlist_refs_builder``，数据源走 ``reference_service_factory``。

钉的是"服务算出来的东西真的到了页面上" —— 上一轮「校准记录」那次就是栽在这：
单元测试全绿、页面表头是空的，因为替身的列是**纯字符串**而真值是 dict。
所以这里直接断言渲染后的 HTML。
"""

import re
import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
PYTHON_DIR = ROOT_DIR / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from web.bootstrap import install_paths  # noqa: E402

install_paths()

from web.tests.test_qdii_fund_list import FakePairRef, FakeRef, FakeSource  # noqa: E402


def _squeezed(html: str) -> str:
    """压掉标签之间的空白 —— Jinja 会在标签之间塞缩进，
    直接 ``assertIn(">SH<")`` 会因为缩进而失败。"""
    return re.sub(r"\s+", " ", html)


class _Record:
    def __init__(self, date="2026-10-09", close=0.0):
        self.date = date
        self.close = close


class _EstRef:
    """中间层标的引用对象：只需要符号与显示文本。"""

    def __init__(self, symbol="^GSPC"):
        self._symbol = symbol

    def get_symbol(self):
        return self._symbol

    def get_display(self):
        return self._symbol


class _FundRef:
    """被看的基金（PHP ``$acct->GetRef()``）。"""

    def __init__(self, symbol="SZ162411", est_symbol="^GSPC", stock_id="2"):
        self._symbol = symbol
        self._est_ref = _EstRef(est_symbol)
        self.stock_id = stock_id

    def get_symbol(self):
        return self._symbol

    def get_est_ref(self):
        return self._est_ref


class _Service:
    """参考服务替身：路由只从它身上取 ``source``。"""

    def __init__(self, source):
        self.source = source


class FundListRouteTest(unittest.TestCase):
    """基金指数对照表在页面上的样子。"""

    def setUp(self):
        from web.app import create_app
        from web.services.fund_service import FundService
        from web.tests.test_web import make_source

        self.source = FakeSource(
            hedges={"SH": -72.1, "SPY": 1732.4},
            names={"SH": "标普500指数ETF-ProShares做空", "SPY": "SPDR标普500 ETF"},
            dates={"sh": "2026-10-09", "spy": "2026-10-09"},
        )
        self.leverage_refs = [
            FakeRef("SH", position=-1.0, factor=245.591, stock_id="sh"),
            FakeRef("SPY", position=1.0, factor=10.035, stock_id="spy"),
        ]
        for ref in self.leverage_refs:
            ref._pair_ref = FakePairRef("^GSPC")
        self.fund = _FundRef()
        self.app = create_app(
            {"TESTING": True},
            fund_service_factory=lambda: FundService(make_source()),
            trading_refs_builder=lambda symbols: [self.fund],
            reference_service_factory=lambda: _Service(self.source),
            fundlist_refs_builder=lambda est_symbol: self.leverage_refs,
        )
        self.client = self.app.test_client()

    def _html(self, symbol="SZ162411"):
        return _squeezed(self.client.get("/fund/" + symbol).get_data(as_text=True))

    def test_the_table_reaches_the_page(self):
        html = self._html()
        self.assertIn('class="data-table fundlist-table"', html)
        self.assertIn('id="SZ162411fundlist"', html)

    def test_the_two_characteristic_headers_are_rendered(self):
        """「跟踪代码」与「对冲值」—— 前者是表头**不能丢前缀**的证据。"""
        html = self._html()
        self.assertIn("跟踪代码", html)
        self.assertIn("对冲值", html)

    def test_the_negative_hedge_is_red_on_the_page(self):
        """生产端那行：``SH | ^GSPC | -1 | 245.591 | 2026-10-09 | -72``。"""
        html = self._html()
        self.assertIn('<td class="cell-red">-72</td>', html)
        self.assertIn('<td class="cell-red">-1</td>', html)
        self.assertIn("245.591", html)
        self.assertIn("2026-10-09", html)

    def test_the_positive_hedge_has_no_color(self):
        html = self._html()
        self.assertIn("<td>1732</td>", html)

    def test_the_calibration_history_link_is_on_the_first_cell(self):
        html = self._html()
        self.assertIn('<a href="/history/calibrationhistory/SH">SH</a>', html)

    def test_the_pair_link_appears_only_once(self):
        """PHP ``static $arSymbol`` 去重：第二行的 ``^GSPC`` 是纯文本。

        URL 里是 ``%5EGSPC`` —— Werkzeug 会把 ``^`` 百分号编码
        （生产端 PHP 直接写 ``symbol=^GSPC``，两种写法浏览器都一样解码）。
        """
        html = self._html()
        # 非首格的链接由宏补 ``target="_blank"``，所以只数 href
        self.assertEqual(html.count('href="/fund/%5EGSPC"'), 1)
        self.assertEqual(html.count("^GSPC"), 2)

    def test_the_group_link_is_in_the_note(self):
        html = self._html()
        self.assertIn("基金指数对照表", html)


class NoLeverageRefsTest(unittest.TestCase):
    """PHP 的守卫：``count($arLev) == 0`` 时整段不出现。"""

    def setUp(self):
        from web.app import create_app
        from web.services.fund_service import FundService
        from web.tests.test_web import make_source

        self.app = create_app(
            {"TESTING": True},
            fund_service_factory=lambda: FundService(make_source()),
            trading_refs_builder=lambda symbols: [_FundRef()],
            fundlist_refs_builder=lambda est_symbol: [],
        )
        self.client = self.app.test_client()

    def test_there_is_no_table_but_the_page_still_renders(self):
        response = self.client.get("/fund/SZ162411")
        self.assertEqual(response.status_code, 200)
        html = _squeezed(response.get_data(as_text=True))
        self.assertNotIn("fundlist-table", html)
        self.assertNotIn("对冲值", html)


if __name__ == "__main__":
    unittest.main()
