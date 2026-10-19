import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[3]
PYTHON_DIR = ROOT_DIR / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from web.app import create_app
from web.bootstrap import install_paths
from web.services.fund_list_service import FundListService
from web.services.fund_service import FundService

install_paths()

from core.stockdata import DailyRecord, FundProfile, MemoryFundDataSource  # noqa: E402

# 离线行情样本：A 股/基金 32 字段，date(30)/time(31) 落在正确位置
QUOTE_LINE_SZ162411 = (
    'var hq_str_sz162411="华宝油气,0.891,0.891,0.913,0.915,0.889,'
    '0.912,0.913,450669050,406234599.722,1495300,0.912,1017200,0.911,'
    '2230574,0.910,960700,0.909,717800,0.908,605171,0.913,1059900,0.914,'
    '1285002,0.915,163602,0.916,139500,0.917,2026-06-08,15:00:00,00";'
)


class FakeQuoteSource:
    """假行情源，实现 MarketQuoteSource 的最小接口（离线，不打网络）。"""

    name = "fake"

    def __init__(self, line):
        self.line = line

    def fetch_lines(self, symbols):
        return {symbol: self.line for symbol in symbols}


def make_quotes_updater():
    """构造一个带假行情源的实时行情更新器。"""
    from updater.quoteupdater import QuotesUpdater

    updater = QuotesUpdater(
        source=FakeQuoteSource(QUOTE_LINE_SZ162411),
        symbols_provider=lambda: [("SZ162411", "sz162411")],
    )
    updater.update()
    return updater


def make_source():
    """构造一个不依赖 MySQL 的基金数据源。

    覆盖三种情况，便于验证分组链接：

    * SZ159518：QDII-ETF（深圳 15 号段），原有用例使用；
    * SZ162411：QDII-LOF（深圳 16 号段，名称沿用「华宝油气」方便断言）；
    * SH513100：QDII-ETF（上海 51 号段）；
    * SH600000：普通 A 股，不属于任何海外基金分组。
    """
    return MemoryFundDataSource(
        stocks={"SZ159518": "1", "SZ162411": "2", "SH513100": "3", "SH600000": "4"},
        names={
            "SZ159518": "华宝油气",
            "SZ162411": "华宝油气",
            "SH513100": "国泰纳斯达克100ETF",
            "SH600000": "浦发银行",
        },
        profiles={
            "SZ159518": FundProfile(
                symbol="SZ159518",
                stock_id="1",
                position=0.8,
                official_date="2026-06-10",
                cny_est=7.1,
                cny_current=7.2,
                is_lof=True,
                est_symbol="XOP",
                est_netvalue=130.68,
            ),
            "SZ162411": FundProfile(
                symbol="SZ162411",
                stock_id="2",
                position=0.95,
                official_date="2026-06-10",
                cny_est=7.1,
                cny_current=7.2,
                is_lof=True,
                est_symbol="XOP",
                est_netvalue=130.68,
            ),
            "SH513100": FundProfile(
                symbol="SH513100",
                stock_id="3",
                position=0.98,
                official_date="2026-06-10",
                cny_est=7.1,
                cny_current=7.2,
                is_lof=False,
                est_symbol="QQQ",
                est_netvalue=560.0,
            ),
            "SH600000": FundProfile(
                symbol="SH600000",
                stock_id="4",
                position=1.0,
                official_date="2026-06-10",
                cny_est=7.1,
                cny_current=7.2,
                is_lof=False,
                est_symbol=None,
                est_netvalue=None,
            ),
        },
        calibration={
            "1": [DailyRecord("2026-06-10", 700.0)],
            "2": [DailyRecord("2026-06-10", 700.0)],
            "3": [DailyRecord("2026-06-10", 480.0)],
            "4": [DailyRecord("2026-06-10", 9.9)],
        },
        netvalue={
            ("1", "2026-06-10"): 1.2,
            ("2", "2026-06-10"): 1.2,
            ("3", "2026-06-10"): 1.5,
            ("4", "2026-06-10"): 9.9,
        },
        hedges={("SZ159518", None): 875.0, ("SZ162411", None): 875.0, ("SH513100", None): 1.0},
    )


class FakeStatusService:
    """状态服务测试替身。"""

    def get_status(self):
        return {
            "database": {"ok": True, "version": "8.0"},
            "tables": {"stock": 1},
            "latest": {"stockhistory": "2026-09-28"},
            "migration": {"fundcalc.py": "已迁移"},
        }


class WebAppTest(unittest.TestCase):
    def setUp(self):
        source = make_source()
        self.app = create_app(
            {"TESTING": True},
            fund_service_factory=lambda: FundService(source),
            status_service_factory=lambda: FakeStatusService(),
            fund_list_service_factory=lambda: FundListService(source),
        )
        self.client = self.app.test_client()

    def test_index_page(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("基金估值", response.get_data(as_text=True))

    def test_index_redirects_to_fund_page(self):
        response = self.client.get("/?symbol=SZ159518")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/fund/SZ159518")

    def test_index_lists_fund_groups(self):
        response = self.client.get("/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('href="/fund/qdiilof"', html)
        self.assertIn('href="/fund/qdiietf"', html)
        self.assertIn("QDII-LOF 基金指数对照表", html)
        self.assertIn("QDII-ETF 基金指数对照表", html)

    def test_fund_api_returns_estimate(self):
        response = self.client.get("/api/fund/SZ159518")
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["symbol"], "SZ159518")
        self.assertEqual(data["name"], "华宝油气")
        self.assertAlmostEqual(data["estimate"], 1.3003748571428573)
        self.assertIn("calibration", data["config"])

    def test_fund_api_accepts_source_values(self):
        response = self.client.get("/api/fund/SZ159518?source=XOP=140&source=CNY=7.1")
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertAlmostEqual(data["estimate"], 1.376)

    def test_fund_page_contains_name_and_estimate(self):
        response = self.client.get("/fund/SZ159518")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("华宝油气", html)
        self.assertIn("1.300375", html)

    def test_unknown_fund_returns_json_404(self):
        response = self.client.get("/api/fund/UNKNOWN")
        data = response.get_json()

        self.assertEqual(response.status_code, 404)
        self.assertIn("未找到基金代码", data["error"])

    def test_unknown_fund_returns_html_404(self):
        response = self.client.get("/fund/UNKNOWN")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 404)
        self.assertIn("未找到基金代码", html)

    def test_invalid_source_returns_json_400(self):
        response = self.client.get("/api/fund/SZ159518?source=bad")
        data = response.get_json()

        self.assertEqual(response.status_code, 400)
        self.assertIn("Expected SYMBOL=VALUE", data["error"])

    def test_health(self):
        response = self.client.get("/health")
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["status"], "ok")

    def test_status_api(self):
        response = self.client.get("/api/status")
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(data["database"]["ok"])
        self.assertEqual(data["migration"]["fundcalc.py"], "已迁移")

    def test_status_page(self):
        response = self.client.get("/status")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("迁移状态", html)
        self.assertIn("fundcalc.py", html)


class FundGroupDefinitionTest(unittest.TestCase):
    """分组划分：按代码号段把 47 只分成 LOF 与 ETF 两组。"""

    def setUp(self):
        from calc.fundgroups import (
            get_overseas_fund_symbol_array,
            get_qdiietf_symbol_array,
            get_qdiilof_symbol_array,
            get_symbol_group,
        )

        self.lof = get_qdiilof_symbol_array()
        self.etf = get_qdiietf_symbol_array()
        self.all_symbols = get_overseas_fund_symbol_array()
        self.group_of = get_symbol_group

    def test_lof_membership_by_code_range(self):
        # 深圳 16 号段、上海 50 号段是 LOF
        for symbol in ("SZ160723", "SZ161130", "SZ161125", "SZ162411", "SZ165513", "SH501018", "SH501312"):
            self.assertIn(symbol, self.lof)

    def test_etf_membership_by_code_range(self):
        # 深圳 15 号段、上海 51 号段是 ETF
        for symbol in ("SZ159518", "SZ159941", "SH513100", "SH513500", "SH513300"):
            self.assertIn(symbol, self.etf)

    def test_groups_are_disjoint_and_cover_every_candidate(self):
        self.assertEqual(set(self.lof) & set(self.etf), set())
        self.assertEqual(len(self.lof) + len(self.etf), len(self.all_symbols))
        # 数字跟着生产端 qdiicn.php 走：QDII 组 2026-10-12 从 33 只扩到 41 只，
        # 多出的 8 只里 4 只是 ETF（qdiietf 21 → 25），另 4 只 LOF 本来就在
        # ``GetLofMixSymbolArray()`` 里、只是新进了 QDII 组（qdiilof 22 → 26）。
        # 详见 core/stocksymbol.py 的 qdii_get_symbol_array() docstring。
        self.assertEqual(len(self.all_symbols), 51)
        self.assertEqual(len(self.lof), 26)
        self.assertEqual(len(self.etf), 25)

    def test_symbol_group_lookup(self):
        self.assertEqual(self.group_of("SZ161130"), "qdiilof")
        self.assertEqual(self.group_of("SH513100"), "qdiietf")
        self.assertIsNone(self.group_of("SH600000"))

    def test_index_membership_helpers(self):
        from calc.fundgroups import in_array_qdiietf, in_array_qdiilof

        self.assertTrue(in_array_qdiilof("SZ162411"))
        self.assertFalse(in_array_qdiilof("SH513100"))
        self.assertTrue(in_array_qdiietf("SH513100"))
        self.assertFalse(in_array_qdiietf("SH600000"))


class FundGroupRouteTest(unittest.TestCase):
    """分组列表与 /fund/<group>/<symbol> 路由。"""

    def setUp(self):
        source = make_source()
        self.app = create_app(
            {"TESTING": True},
            fund_service_factory=lambda: FundService(source),
            status_service_factory=lambda: FakeStatusService(),
            fund_list_service_factory=lambda: FundListService(source),
        )
        self.client = self.app.test_client()

    def test_lof_group_list_page_renders_table(self):
        response = self.client.get("/fund/qdiilof")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("QDII-LOF 基金指数对照表", html)
        # 表头对齐生产端 qdiicn.php 的两张表（2026-10-19 起 EST 七列接进本页，
        # 「校准值 / 校准日期」两列同时删除）：
        #   参考数据表  代码 | 价格 | 涨幅 | 日期 | 时间 | 名称
        #   估值表      代码 | 官方EST | EST日期 | 溢价 | 参考EST | 溢价 | 实时EST | 溢价
        # 两处"日期"在本页合并成一张表，所以必须拆成「EST日期」与「最新行情日期」，
        # 否则会把估值的日期误读成行情日期。
        for column in (
            "基金代码", "名称", "跟踪代码", "仓位",
            "基金净值", "基金净值日期",
            "最新行情日期", "行情更新时间", "最新价", "涨跌幅", "溢价率", "数据源",
            "官方EST", "EST日期", "溢价", "参考EST", "溢价", "实时EST", "溢价",
        ):
            self.assertIn("<th>%s</th>" % column, html)
        # 2026-10-19 删除的两列不应再出现
        self.assertNotIn("<th>校准值</th>", html)
        self.assertNotIn("<th>校准日期</th>", html)
        # 旧的列名不应再出现
        self.assertNotIn("<th>股票代码</th>", html)
        self.assertNotIn("<th>日期</th>", html)

    def test_etf_group_list_page_renders_table(self):
        response = self.client.get("/fund/qdiietf")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("QDII-ETF 基金指数对照表", html)
        self.assertIn("共 25 只", html)

    def test_lof_group_lists_only_lof_symbols(self):
        response = self.client.get("/fund/qdiilof")
        html = response.get_data(as_text=True)

        self.assertIn("共 26 只", html)
        # LOF：深圳 16 号段与上海 50 号段
        self.assertIn("SZ161130", html)
        self.assertIn("SZ161125", html)
        self.assertIn("SZ162411", html)
        self.assertIn("SH501018", html)
        # ETF 不能出现在 LOF 列表里
        self.assertNotIn("SH513100", html)
        self.assertNotIn("SZ159518", html)

    def test_etf_group_lists_only_etf_symbols(self):
        response = self.client.get("/fund/qdiietf")
        html = response.get_data(as_text=True)

        self.assertIn("SH513100", html)
        self.assertIn("SZ159941", html)
        self.assertIn("SZ159518", html)
        # LOF 不能出现在 ETF 列表里
        self.assertNotIn("SZ161130", html)
        self.assertNotIn("SZ161125", html)
        self.assertNotIn("SH501018", html)

    def test_etf_group_links_use_etf_grouped_path(self):
        response = self.client.get("/fund/qdiietf")
        html = response.get_data(as_text=True)

        self.assertIn('href="/fund/qdiietf/SH513100"', html)

    def test_grouped_fund_page_for_lof(self):
        # SZ162411 属于 qdiilof，且测试数据源里有它
        response = self.client.get("/fund/qdiilof/SZ162411")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("华宝油气", html)
        self.assertIn('href="/fund/qdiilof"', html)

    def test_grouped_fund_page_for_etf(self):
        response = self.client.get("/fund/qdiietf/SH513100")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('href="/fund/qdiietf"', html)

    def test_cross_group_link_redirects_to_real_group(self):
        # 把 ETF 符号挂在 LOF 分组下，应重定向到 ETF 分组
        response = self.client.get("/fund/qdiilof/SH513100")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/fund/qdiietf"))

    def test_plain_link_infers_group_by_code_range(self):
        # 旧链接也要能推断出分组，返回链接才能指对
        response = self.client.get("/fund/SZ162411")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('href="/fund/qdiilof"', html)

        response = self.client.get("/fund/SH513100")
        html = response.get_data(as_text=True)
        self.assertIn('href="/fund/qdiietf"', html)

    def test_out_of_group_symbol_has_no_group_back_link(self):
        # SH600000 不属于任何分组，不应显示「返回分组列表」
        response = self.client.get("/fund/SH600000")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("返回分组列表", html)

    def test_unknown_group_falls_back_to_fund_lookup(self):
        # /fund/<group> 与 /fund/<symbol> 重载，未知名必须仍按符号处理
        response = self.client.get("/fund/SZ162411")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("华宝油气", html)

    def test_unknown_symbol_still_404(self):
        response = self.client.get("/fund/NOTASYMBOL")
        self.assertEqual(response.status_code, 404)

    def test_api_routes_unaffected_by_group_routes(self):
        response = self.client.get("/api/fund/SZ162411")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["symbol"], "SZ162411")


class RealtimeQuoteRouteTest(unittest.TestCase):
    """实时行情：/api/quotes 系列，以及列表页直读实时价。"""

    def setUp(self):
        source = make_source()
        self.updater = make_quotes_updater()
        self.app = create_app(
            {"TESTING": True},
            fund_service_factory=lambda: FundService(source),
            status_service_factory=lambda: FakeStatusService(),
            fund_list_service_factory=lambda: FundListService(source, quotes_provider=self.updater),
            updater_factory=lambda: self.updater,
        )
        self.client = self.app.test_client()

    def test_quotes_api_returns_cached_quotes(self):
        response = self.client.get("/api/quotes")
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["count"], 1)
        quote = data["quotes"][0]
        self.assertEqual(quote["symbol"], "SZ162411")
        self.assertEqual(quote["price"], "0.913")
        self.assertEqual(quote["prev_price"], "0.891")
        self.assertEqual(quote["source"], "fake")
        self.assertIn("age_seconds", quote)

    def test_quotes_api_reports_updater_state(self):
        response = self.client.get("/api/quotes")
        updater = response.get_json()["updater"]

        self.assertEqual(updater["source"]["name"], "fake")
        self.assertEqual(updater["cached"], 1)
        self.assertIn("refresh_on_request", updater)

    def test_quotes_api_source_endpoint(self):
        response = self.client.get("/api/quotes/source")
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertIn("sina", data["available_sources"])
        self.assertEqual(data["active"]["name"], "fake")

    def test_quotes_refresh_endpoint_fetches_again(self):
        response = self.client.post("/api/quotes/refresh")
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["updated"], 1)
        self.assertIsNone(data["error"])

    def test_fund_list_prefers_realtime_price(self):
        # 列表页的"最新价"应来自实时行情（0.913），并带出行情时间
        response = self.client.get("/fund/qdiilof")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("0.913", html)
        self.assertIn("15:00:00", html)
        # 溢价率按 实时价 / 净值 算出来了
        self.assertIn('class="premium ', html)

    def test_fund_list_columns_match_production_pages(self):
        """表头对齐生产端 qdiicn.php 的两张表（列名 + 口径）。

        生产端把「参考数据」和「估值」放在两张表里；本页合并成一张，
        因此两处同名的"日期"必须拆开：估值那份是 EST 日期
        （``GetOfficialDate()``，中间层行情的日期），行情那份是基金自己的
        行情日期。这个测试锁住这个区分，避免以后又被并回一列。
        """
        response = self.client.get("/fund/qdiilof")
        html = response.get_data(as_text=True)

        # 拆分后的两列各自独立存在
        self.assertIn('<th>EST日期</th>', html)
        self.assertIn('<th>最新行情日期</th>', html)
        # 不再有歧义的裸"日期"列
        self.assertNotIn('<th>日期</th>', html)
        # 生产端参考数据表的「涨幅」→ 本页「涨跌幅」
        self.assertIn('<th>涨跌幅</th>', html)
        # 估值表三档各两列，列名照抄 fundestparagraph.php 的 TableColumn*
        self.assertIn('<th>官方EST</th>', html)
        self.assertIn('<th>参考EST</th>', html)
        self.assertIn('<th>实时EST</th>', html)

    def test_fund_list_has_net_value_columns(self):
        """「基金净值」与「基金净值日期」两列（新增）。

        与「最新价 / 最新行情日期」是**两回事**：
        净值是官方公布的单位净值（一天一个数，T+1 才出），
        价格是盘中一直动的行情。溢价率拿净值算，所以两列必须同时可见。

        测试替身没有 ``get_latest_net_value_date``，日期会回退到行情日期链
        （``_read_latest_net_value_date`` 的容错分支），所以只断言两列表头与
        ``net-value`` 那一格有值。
        """
        response = self.client.get("/fund/qdiilof")
        html = response.get_data(as_text=True)

        self.assertIn('<th>基金净值</th>', html)
        self.assertIn('<th>基金净值日期</th>', html)
        self.assertIn('class="net-value"', html)
        self.assertIn('class="net-value-date"', html)

    def test_net_value_and_price_are_separate_columns(self):
        """净值列与价格列不能互相顶替（它们的数据来源不同）。

        替身里 SZ159518 的净值是 ``netvalue[("1","2026-06-10")] = 1.2``，
        价格是行情快照给的。若两列串了，净值那格会显示价格。
        """
        response = self.client.get("/fund/qdiilof")
        html = response.get_data(as_text=True)

        self.assertIn('<td class="net-value">1.2</td>', html)
        # 价格列仍在，且不是同一个数
        self.assertIn('class="price"', html)

    def test_fund_list_row_carries_quote_date_change_and_source(self):
        """行情日期 / 涨跌幅 / 数据源三列要有值，且涨跌幅按 PHP 口径算。

        测试替身：price=0.913、prev_price=0.891、date=2026-06-08、source=fake。
        涨跌幅 = (0.913/0.891 - 1) × 100 = 2.4691...% → 两位小数去尾零 → 2.47%
        """
        response = self.client.get("/fund/qdiilof")
        html = response.get_data(as_text=True)

        self.assertIn('<td class="quote-date">2026-06-08</td>', html)
        self.assertIn('<td class="change positive">2.47%</td>', html)
        self.assertIn('<td class="quote-source">fake</td>', html)

    def test_fund_list_keeps_est_date_separate_from_quote_date(self):
        """EST 日期与行情日期是两个 ``<td>``，不能互相覆盖。

        列名可以重复（三档都叫「溢价」），但 ``td`` 的 class 必须各就各位：
        ``est-date`` 是估值标的的行情日期，``quote-date`` 是基金自己的。
        这个测试锁住 class 不串味——真正有值的场景见
        ``FundListEstColumnsTest``（那里用假引用对象钉三档的取值/留空）。
        """
        response = self.client.get("/fund/qdiilof")
        html = response.get_data(as_text=True)

        self.assertIn('<td class="est-date">', html)
        self.assertIn('<td class="quote-date">2026-06-08</td>', html)
        self.assertIn('<td class="est-official">', html)
        self.assertIn('<td class="est-fair">', html)
        self.assertIn('<td class="est-realtime">', html)

    def test_fund_list_shows_tracking_symbol(self):
        """「跟踪代码」列取 est_symbol（估值用的中间层标的），对应生产端对照表。

        测试替身里 SZ162411 的估值标的是 XOP。
        """
        response = self.client.get("/fund/qdiilof")
        html = response.get_data(as_text=True)

        self.assertIn('<td class="est-symbol">XOP</td>', html)

    def test_fund_list_without_quotes_still_renders(self):
        # 没有实时行情时退化为库里的收盘价，页面不能报错
        source = make_source()
        app = create_app(
            {"TESTING": True},
            fund_service_factory=lambda: FundService(source),
            status_service_factory=lambda: FakeStatusService(),
            fund_list_service_factory=lambda: FundListService(source),
        )
        client = app.test_client()
        response = client.get("/fund/qdiilof")

        self.assertEqual(response.status_code, 200)
        self.assertIn("QDII-LOF 基金指数对照表", response.get_data(as_text=True))


class FakeQdiiRef:
    """:class:`~ref.refobj.QdiiReference` 的测试替身。

    只实现 ``FundListService._build_est()`` 真正会调到的六个方法。
    三档各自带「估值值 / 显示文本 / 溢价文本」，默认全给齐
    （数值取生产端 qdiicn.php 的 SH513100 那一行）；
    把某一档设成 ``None`` 就模拟 PHP 的 ``GetXxxNetValue()`` 返回 false。
    """

    def __init__(self, official=1.989, official_date="2026-10-19",
                 official_disp="1.989", official_premium="17.9%",
                 fair=1.977, fair_disp="1.977", fair_premium="18.6%",
                 realtime=1.9771, realtime_disp="1.977", realtime_premium="18.62%"):
        self._official = official
        self._official_date = official_date
        self._official_disp = official_disp
        self._official_premium = official_premium
        self._fair = fair
        self._fair_disp = fair_disp
        self._fair_premium = fair_premium
        self._realtime = realtime
        self._realtime_disp = realtime_disp
        self._realtime_premium = realtime_premium

    def get_official_net_value(self):
        return self._official

    def get_official_date(self):
        return self._official_date

    def get_fair_net_value(self):
        return self._fair

    def get_realtime_net_value(self):
        return self._realtime

    def get_price_display(self, disp=None, prev=None, precision=None):
        for value, text in (
            (self._official, self._official_disp),
            (self._fair, self._fair_disp),
            (self._realtime, self._realtime_disp),
        ):
            if value is not None and disp == value:
                return text
        return ""

    def get_percentage_text(self, divisor=None, dividend=None):
        for value, text in (
            (self._official, self._official_premium),
            (self._fair, self._fair_premium),
            (self._realtime, self._realtime_premium),
        ):
            if value is not None and divisor == value:
                return text
        return ""


class FundListEstColumnsTest(unittest.TestCase):
    """分组页 EST 七列（2026-10-19 接）——对应 PHP ``_echoFundEstTableItem()``。

    这里**不连库、不建真引用对象**：把 ``ref.refobj.build_qdii_reference``
    换成返回 :class:`FakeQdiiRef` 的桩，直接钉「三档估值怎么变成七列」——
    包括两档可能没有、没有时留空（不补位）这两条 PHP 语义。
    """

    def setUp(self):
        self.service = FundListService(make_source(), auto_calibrate=False)

    def _row(self, ref):
        from unittest import mock

        with mock.patch("ref.refobj.build_qdii_reference", return_value=ref):
            return self.service._build_row("SH513100")

    def test_three_tiers_fill_all_seven_columns(self):
        row = self._row(FakeQdiiRef())

        self.assertEqual(row["official_est"], "1.989")
        self.assertEqual(row["est_date"], "2026-10-19")
        self.assertEqual(row["official_premium"], "17.9%")
        self.assertEqual(row["official_premium_class"], "positive")
        self.assertEqual(row["fair_est"], "1.977")
        self.assertEqual(row["fair_premium"], "18.6%")
        self.assertEqual(row["fair_premium_class"], "positive")
        self.assertEqual(row["realtime_est"], "1.977")
        self.assertEqual(row["realtime_premium"], "18.62%")
        self.assertEqual(row["realtime_premium_class"], "positive")

    def test_without_realtime_leaves_two_cells_blank(self):
        """没有实时 EST 的两格留空 —— 用户点名要的行为，也是 PHP 的 if 语义。"""
        row = self._row(FakeQdiiRef(realtime=None))

        self.assertEqual(row["official_est"], "1.989")
        self.assertEqual(row["fair_est"], "1.977")
        self.assertEqual(row["realtime_est"], "")
        self.assertEqual(row["realtime_premium"], "")
        self.assertEqual(row["realtime_premium_class"], "")

    def test_without_fair_leaves_two_cells_blank(self):
        row = self._row(FakeQdiiRef(fair=None))

        self.assertEqual(row["official_est"], "1.989")
        self.assertEqual(row["fair_est"], "")
        self.assertEqual(row["fair_premium"], "")
        # 实时那两格不受影响
        self.assertEqual(row["realtime_est"], "1.977")

    def test_without_official_est_leaves_everything_blank(self):
        """PHP 的守卫 ``if ($fund->GetOfficialNetValue())``：官方估值没有的
        基金不进 EST 表 —— 这里七列全空（行还在，行情表里有它）。"""
        row = self._row(FakeQdiiRef(official=None))

        for key in ("official_est", "est_date", "official_premium",
                    "fair_est", "fair_premium",
                    "realtime_est", "realtime_premium"):
            self.assertEqual(row[key], "", key)

    def test_builder_failure_leaves_everything_blank(self):
        """建引用对象抛异常时七列全空 —— 列表页不能因一只基金算错估值就 500。"""
        from unittest import mock

        with mock.patch("ref.refobj.build_qdii_reference", side_effect=RuntimeError("boom")):
            row = self.service._build_row("SH513100")

        self.assertEqual(row["official_est"], "")
        self.assertEqual(row["realtime_premium"], "")

    def test_negative_premium_is_green_positive_is_red(self):
        """涨红跌绿（全站统一口径）：-0.61% → negative，0.57% → positive。"""
        row = self._row(FakeQdiiRef(
            official=0.867, official_disp="0.867", official_premium="-0.61%",
            fair=None, realtime=None,
        ))

        self.assertEqual(row["official_premium"], "-0.61%")
        self.assertEqual(row["official_premium_class"], "negative")

        row = self._row(FakeQdiiRef(
            official=0.917, official_disp="0.917", official_premium="0.57%",
            fair=None, realtime=None,
        ))
        self.assertEqual(row["official_premium_class"], "positive")

    def test_zero_premium_without_percent_sign_has_no_color(self):
        """``GetPercentageText()`` 归零时返回 ``"0"``（不带 %）——不上色。"""
        row = self._row(FakeQdiiRef(
            official=1.0, official_disp="1", official_premium="0",
            fair=None, realtime=None,
        ))

        self.assertEqual(row["official_premium"], "0")
        self.assertEqual(row["official_premium_class"], "")


if __name__ == "__main__":
    unittest.main()
