"""基金详情页接入 stockhistory + netvalueclose + fundhistory 三段（CK1 / CK6）。

对应 ``php/ui/stockhistoryparagraph.php``、``netvaluecloseparagraph.php``
与 ``fundhistoryparagraph.php``。三段都复用 ``history_service`` 的同名 kind，
与 ``/history/*`` 页面同源。**离线**：history 服务由假工厂注入，不给数据库压力。

测试重点：
* 有数据时三段都出现（标题 + 表容器类名都命中）；
* 无数据时整段隐藏且不让页面 500（与 PHP ``CountNetValue()==0`` / 空结果守卫一致）；
* **CK6 起 netvalueclose 摆的不再是基金自己**：``_qdiigroup.php:106-110`` 摆的是
  中间层标的 + 各杠杆标的（SZ162411 的 est 是 XOP，演示库 fundpair 里没有跟踪
  XOP 的行，所以只有 XOP 一张表）。
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
    """假历史服务：按 ``with_data`` 决定三个 kind 有没有行。

    ``history_table`` 宏只读 ``columns`` / ``rows`` / ``symbol`` / ``title``
    （Jinja 把 dict 键当属性解析），``_build_*`` 用 ``table.get("rows")``
    （dict 方法），所以返回普通 dict 即可，不必实现真正的 ``TableRow``。

    netvalueclose 在 CK6 之后按 **est + 杠杆符号** 取表（见路由
    ``_build_netvalue_close``），假服务不区分符号、一律给同一张表。
    """

    #: kind -> (标题, 容器 class 关键字)
    KINDS = {
        "stockhistory": "历史价格",
        "netvalueclose": "净值价格比较",
        "fundhistory": "基金溢价记录",
    }

    def __init__(self, with_data=True):
        self.with_data = with_data

    def get_table(self, kind, symbol, start=0, num=10):
        if kind in self.KINDS:
            title = self.KINDS[kind]
            if self.with_data:
                return {
                    "kind": kind,
                    "symbol": symbol,
                    "title": title,
                    "columns": [{"text": "日期"}, {"text": "价格"}],
                    "rows": [{"cells": ["2026-09-30", "4.796"], "match": False}],
                }
            return {"title": title, "symbol": symbol, "columns": [{"text": "日期"}], "rows": []}
        # 其它 kind（shareshistory 等）一律空行，避免份额表干扰断言
        return {"title": kind, "symbol": symbol, "columns": [], "rows": []}


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
        self.assertIn('class="data-table history-table stockhistory-table"', html)
        self.assertIn('class="data-table history-table fundhistory-table"', html)
        self.assertIn("历史价格", html)
        self.assertIn("基金溢价记录", html)
        # 表容器类名唯一，确认两套渲染都接到了；"查看全部"链接指向 /history 同源页
        self.assertIn('href="/history/stockhistory/SZ162411"', html)
        self.assertIn('href="/history/fundhistory/SZ162411"', html)

    def test_fundest_memo_shows_position(self):
        """CK6：估值表上方摆仓位估算说明（PHP ``_getFundPositionStr``）。

        生产端 sz161125cn.php 上这句话以「仓位估算值使用0.95。」开头，
        后面还有「实时EST … 关联程度按照100%估算。」—— 替身引用没有
        est / 实时对象，只出第一句。
        """
        html = self.client.get("/fund/SZ162411").get_data(as_text=True)
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html))
        self.assertIn("仓位估算值使用0.95。", text)

    def test_fundest_memo_links_the_position_page(self):
        """CK6：说明行的「仓位估算」四字是**链接**，指向仓位估算页。

        PHP ``fundestparagraph.php:135-152`` 里那四个字就是
        ``GetFundPositionLink()`` 的锚文本（指向 ``fundpositioncn.php``）；
        本版链 ``/history/fundposition/<symbol>``。

        断言锚文本精确等于这四个字 —— 若哪天把整句（含 ``值使用0.95。``）
        一起包进 ``<a>``，本测试立刻失败。
        """
        html = self.client.get("/fund/SZ162411").get_data(as_text=True)
        self.assertIn('href="/history/fundposition/SZ162411"', html)
        self.assertIn(">仓位估算</a>", html)
        #: 底部那个独立「查看仓位估算」段落已挪走，不留两份入口
        self.assertNotIn("查看仓位估算", html)

    def test_netvalueclose_shows_est_symbol_not_fund_itself(self):
        """CK6：净值价格比较摆 **est + 杠杆**，不再只摆基金自己。

        PHP ``_qdiigroup.php:106-110``：先 est_ref（SZ162411 的是 XOP），
        再 ``$arLev``（fundpair 里跟踪 XOP 的符号，演示库为
        SH513350/SZ159518/SZ162411 —— 基金自己恰好在杠杆组里，所以它的表
        也会出现，但那是 fundpair 数据决定的，不是 CK1 的"摆自己"语义）。

        断言 est 符号的表存在且排在第一个：改回 CK1 语义（只摆基金自己）
        时 XOP 的表会消失，本测试立刻失败。
        """
        html = self.client.get("/fund/SZ162411").get_data(as_text=True)
        self.assertIn('class="data-table history-table netvalueclose-table"', html)
        self.assertIn('href="/history/netvalueclose/XOP"', html)
        self.assertLess(html.find("XOP的净值价格比较"),
                        html.find("SH513350的净值价格比较"))

    def test_history_sections_hidden_without_data(self):
        self.app.extensions["history_service_factory"] = (
            lambda: FakeHistoryService(with_data=False)
        )
        response = self.client.get("/fund/SZ162411")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertNotIn("stockhistory-table", html)
        self.assertNotIn("netvalueclose-table", html)
        self.assertNotIn("fundhistory-table", html)


if __name__ == "__main__":
    unittest.main()
