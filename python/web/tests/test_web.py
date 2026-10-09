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

from stockdata import DailyRecord, FundProfile, MemoryFundDataSource  # noqa: E402

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
    from quoteupdater import QuotesUpdater

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
        from fundgroups import (
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
        self.assertEqual(len(self.all_symbols), 47)
        self.assertEqual(len(self.lof), 26)
        self.assertEqual(len(self.etf), 21)

    def test_symbol_group_lookup(self):
        self.assertEqual(self.group_of("SZ161130"), "qdiilof")
        self.assertEqual(self.group_of("SH513100"), "qdiietf")
        self.assertIsNone(self.group_of("SH600000"))

    def test_index_membership_helpers(self):
        from fundgroups import in_array_qdiietf, in_array_qdiilof

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
        # 表头对齐生产端 qdiicn.php / fundlistcn.php：
        #   参考数据表  代码 | 价格 | 涨幅 | 日期 | 时间 | 名称
        #   对照表      代码 | 跟踪代码 | 仓位 | 校准值 | 日期 | 参考值
        # 两处"日期"在本页合并成一张表，所以必须拆成「校准日期」与「最新行情日期」，
        # 否则会把校准值的日期误读成行情日期。
        for column in (
            "基金代码", "名称", "跟踪代码", "仓位", "校准值", "校准日期",
            "最新行情日期", "行情更新时间", "最新价", "涨跌幅", "溢价率", "数据源",
        ):
            self.assertIn("<th>%s</th>" % column, html)
        # 旧的列名不应再出现
        self.assertNotIn("<th>股票代码</th>", html)
        self.assertNotIn("<th>日期</th>", html)

    def test_etf_group_list_page_renders_table(self):
        response = self.client.get("/fund/qdiietf")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("QDII-ETF 基金指数对照表", html)
        self.assertIn("共 21 只", html)

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
        """表头对齐生产端 qdiicn.php 与 fundlistcn.php（列名 + 口径）。

        生产端把「参考数据」和「基金指数对照表」放在两张表里；本页合并成一张，
        因此两处同名的"日期"必须拆开：校准值那份是 calibrationhistory 的日期，
        行情那份是行情的日期。这个测试锁住这个区分，避免以后又被并回一列。
        """
        response = self.client.get("/fund/qdiilof")
        html = response.get_data(as_text=True)

        # 拆分后的两列各自独立存在
        self.assertIn('<th>校准日期</th>', html)
        self.assertIn('<th>最新行情日期</th>', html)
        # 不再有歧义的裸"日期"列
        self.assertNotIn('<th>日期</th>', html)
        # 生产端参考数据表的「涨幅」→ 本页「涨跌幅」
        self.assertIn('<th>涨跌幅</th>', html)

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

    def test_fund_list_keeps_calibration_date_separate_from_quote_date(self):
        """校准日期与行情日期是两列，不能互相覆盖。

        测试替身的校准值是 2026-06-10（见 make_source），行情日期是 2026-06-08；
        两者都不为空且不同，正好用来验证没有被合并成一列。
        """
        response = self.client.get("/fund/qdiilof")
        html = response.get_data(as_text=True)

        self.assertIn('<td class="calibration-date">2026-06-10</td>', html)
        self.assertIn('<td class="quote-date">2026-06-08</td>', html)

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


if __name__ == "__main__":
    unittest.main()
