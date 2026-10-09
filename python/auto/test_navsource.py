"""`navsource.py` 与 `navupdater.py` 的单元测试。

对应 PHP：``php/stock/yahoostock.php`` 的 ``YahooUpdateNetValue()`` /
``_yahooStockGetData()``。原实现抓 Yahoo（实测已全面 403），这里测的是
改用国内源之后的抓取与落库逻辑。

全部用假的净值源与内存 store 离线测试，不打网络。
"""

import datetime
import os
import unittest

import navsource
from navupdater import NavUpdater, is_nav_ready, nav_code_of
from quotestore import MemoryQuoteStore

# 真实响应样本（取自东方财富 / 新浪 / 深交所）
EASTMONEY_PAYLOAD = {
    "Data": {
        "LSJZList": [
            {"FSRQ": "2026-09-29", "DWJZ": "4.5452", "LJJZ": "4.5452", "JZZZL": "0.21"},
            {"FSRQ": "2026-09-28", "DWJZ": "4.5357", "LJJZ": "4.5357", "JZZZL": "-0.75"},
        ]
    }
}
SINA_TEXT = (
    'var hq_str_f_161130="易方达纳斯达克100ETF联接(QDII-LOF)A(人民币份额),'
    '4.5452,4.5452,4.5357,2026-09-29,3.89851";'
)
SZSE_PAYLOAD = [{
    "metadata": {"catalogid": "1785_child", "recordcount": 0},
    "data": [
        {"nav_date": "2026-09-28", "fund_code": "161130",
         "security_short_name": "纳斯达克100LOF", "nav_per_share": "4.5357"},
        {"nav_date": "2026-09-24", "fund_code": "161130",
         "security_short_name": "纳斯达克100LOF", "nav_per_share": "4.5696"},
    ],
}]


class FakeResponse:
    def __init__(self, payload=None, content=None, json_error=False):
        self._payload = payload
        self.content = content if content is not None else b""
        self._json_error = json_error

    def raise_for_status(self):
        return None

    def json(self):
        if self._json_error:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    """记录请求并按预置响应返回。"""

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append({"url": url, "headers": headers or {}})
        if self.error is not None:
            raise self.error
        return self.response


class NavSourceRegistryTest(unittest.TestCase):
    def test_sources_registered(self):
        names = navsource.list_nav_sources()
        for expected in ("eastmoney", "sina", "szse", "crosscheck"):
            self.assertIn(expected, names)

    def test_default_is_crosscheck(self):
        # 默认走"东财为主 + 新浪对照 + 差异告警"
        self.assertEqual(navsource.DEFAULT_NAV_SOURCE, "crosscheck")
        source = navsource.get_nav_source()
        self.assertEqual(source.name, "crosscheck")
        # 主源必须是东财（需求指定默认优先东财）
        self.assertEqual(source.primary.name, "eastmoney")
        self.assertEqual(source.secondary.name, "sina")

    def test_crosscheck_does_not_recurse(self):
        # 环境变量设成 crosscheck 时不能无限递归（内部必须退到单源）
        old = os.environ.get(navsource.ENV_SOURCE)
        os.environ[navsource.ENV_SOURCE] = "crosscheck"
        try:
            source = navsource.create_source("crosscheck")
            self.assertEqual(source.primary.name, "eastmoney")
        finally:
            if old is None:
                os.environ.pop(navsource.ENV_SOURCE, None)
            else:
                os.environ[navsource.ENV_SOURCE] = old

    def test_unknown_source_raises_with_available_list(self):
        with self.assertRaises(ValueError) as context:
            navsource.get_nav_source("no-such-source")
        self.assertIn("eastmoney", str(context.exception))

    def test_register_custom_source(self):
        # 这就是"换净值源"的最小示例
        navsource.register_nav_source("fake-nav", lambda **kwargs: FakeNavSource())
        try:
            self.assertIn("fake-nav", navsource.list_nav_sources())
        finally:
            navsource.registry._factories.pop("fake-nav", None)


class FakeNavSource(navsource.NavSource):
    """假净值源，实现接口最小集合。"""

    name = "fake"

    def __init__(self, record=None, error=None, history=None):
        self.record = record
        self.error = error
        self.history = history
        self.calls = []

    def fetch_latest(self, code):
        self.calls.append(code)
        if self.error is not None:
            raise self.error
        return self.record

    def fetch_history(self, code, limit=30):
        return (self.history or [])[:limit]

    def describe(self):
        return {"name": self.name}


class EastmoneySourceTest(unittest.TestCase):
    def test_parses_lsjz_payload(self):
        session = FakeSession(FakeResponse(payload=EASTMONEY_PAYLOAD))
        source = navsource.EastmoneyNavSource(session=session)

        record = source.fetch_latest("161130")

        self.assertEqual(record["date"], "2026-09-29")
        self.assertEqual(record["nav"], "4.5452")
        self.assertEqual(record["acc_nav"], "4.5452")
        self.assertEqual(record["change_pct"], "0.21")
        self.assertEqual(record["source"], "eastmoney")

    def test_history_keeps_source_order(self):
        session = FakeSession(FakeResponse(payload=EASTMONEY_PAYLOAD))
        source = navsource.EastmoneyNavSource(session=session)

        rows = source.fetch_history("161130", limit=5)

        self.assertEqual([row["date"] for row in rows], ["2026-09-29", "2026-09-28"])

    def test_sends_referer(self):
        # 东财不带 Referer 会返回空，所以这个请求头必须有
        session = FakeSession(FakeResponse(payload=EASTMONEY_PAYLOAD))
        navsource.EastmoneyNavSource(session=session).fetch_latest("161130")

        self.assertEqual(session.calls[0]["headers"]["Referer"], navsource.EASTMONEY_REFERER)

    def test_returns_none_when_no_data(self):
        session = FakeSession(FakeResponse(payload={"Data": {"LSJZList": []}}))
        self.assertIsNone(navsource.EastmoneyNavSource(session=session).fetch_latest("161130"))

    def test_skips_blank_nav_entries(self):
        payload = {"Data": {"LSJZList": [
            {"FSRQ": "2026-09-29", "DWJZ": "", "LJJZ": ""},
            {"FSRQ": "2026-09-28", "DWJZ": "4.5357", "LJJZ": "4.5357"},
        ]}}
        session = FakeSession(FakeResponse(payload=payload))
        record = navsource.EastmoneyNavSource(session=session).fetch_latest("161130")

        self.assertEqual(record["date"], "2026-09-28")

    def test_network_error_returns_empty(self):
        session = FakeSession(error=RuntimeError("boom"))
        source = navsource.EastmoneyNavSource(session=session)

        self.assertIsNone(source.fetch_latest("161130"))
        self.assertEqual(source.fetch_history("161130"), [])


class SinaSourceTest(unittest.TestCase):
    def test_parses_gb18030_line(self):
        session = FakeSession(FakeResponse(content=SINA_TEXT.encode("gb18030")))
        source = navsource.SinaNavSource(session=session)

        record = source.fetch_latest("161130")

        self.assertEqual(record["date"], "2026-09-29")
        self.assertEqual(record["nav"], "4.5452")
        self.assertEqual(record["acc_nav"], "4.5452")
        self.assertEqual(record["prev_nav"], "4.5357")
        self.assertIn("易方达", record["name"])
        self.assertEqual(record["source"], "sina")

    def test_returns_none_for_unrelated_symbol(self):
        session = FakeSession(FakeResponse(content=SINA_TEXT.encode("gb18030")))
        self.assertIsNone(navsource.SinaNavSource(session=session).fetch_latest("999999"))

    def test_returns_none_when_nav_is_zero(self):
        text = 'var hq_str_f_000000="某基金,0.0000,0.0000,0.0000,2026-09-29,1";'
        session = FakeSession(FakeResponse(content=text.encode("gb18030")))
        self.assertIsNone(navsource.SinaNavSource(session=session).fetch_latest("000000"))


class SzseSourceTest(unittest.TestCase):
    def test_parses_data_array_when_recordcount_is_zero(self):
        # 深交所这个接口 metadata.recordcount 恒为 0，不能据它判断有没有数据
        session = FakeSession(FakeResponse(payload=SZSE_PAYLOAD))
        source = navsource.SzseNavSource(session=session)

        record = source.fetch_latest("161130")

        self.assertEqual(record["date"], "2026-09-28")
        self.assertEqual(record["nav"], "4.5357")
        self.assertEqual(record["name"], "纳斯达克100LOF")

    def test_empty_array_returns_none(self):
        session = FakeSession(FakeResponse(payload=[{"metadata": {}, "data": []}]))
        self.assertIsNone(navsource.SzseNavSource(session=session).fetch_latest("513100"))

    def test_non_list_payload_returns_none(self):
        session = FakeSession(FakeResponse(payload={"error": "x"}))
        self.assertIsNone(navsource.SzseNavSource(session=session).fetch_latest("161130"))


class NavCodeTest(unittest.TestCase):
    def test_strips_market_prefix(self):
        self.assertEqual(nav_code_of("SZ161130"), "161130")
        self.assertEqual(nav_code_of("SH513100"), "513100")
        # 已经六位数字时原样返回
        self.assertEqual(nav_code_of("161130"), "161130")

    def test_rejects_non_china_codes(self):
        # QQQ / ^GSPC 这类中间层标的不是中国基金，不参与净值抓取
        for symbol in ("QQQ", "SPY", "^GSPC", "gb_qqq", ""):
            self.assertIsNone(nav_code_of(symbol), symbol)


class NormalizeTest(unittest.TestCase):
    """清洗函数：各源的脏数据都要能吃下去。"""

    def test_normalize_nav_accepts_plain_numbers(self):
        self.assertEqual(navsource.normalize_nav("4.5452"), 4.5452)
        self.assertEqual(navsource.normalize_nav(4.5452), 4.5452)
        # 带千分位/百分号也能解析
        self.assertEqual(navsource.normalize_nav("1,234.5"), 1234.5)

    def test_normalize_nav_rejects_blank_variants(self):
        for value in (None, "", "  ", "-", "--", "N/A", "null"):
            self.assertIsNone(navsource.normalize_nav(value), repr(value))

    def test_normalize_date_forms(self):
        # 三个源写法不同，都要归一到 ISO
        self.assertEqual(navsource.normalize_date("2026-09-29"), "2026-09-29")
        self.assertEqual(navsource.normalize_date("2026/09/29"), "2026-09-29")
        self.assertEqual(navsource.normalize_date("20260929"), "2026-09-29")
        self.assertIsNone(navsource.normalize_date(""))
        self.assertIsNone(navsource.normalize_date(None))


class CrossCheckNavSourceTest(unittest.TestCase):
    """双源对比与告警（需求核心：东财为主、新浪对照、差异告警）。"""

    def _source(self, primary_record, secondary_record, **kwargs):
        alerts = []
        source = navsource.CrossCheckNavSource(
            primary=FakeNavSource(record=primary_record),
            secondary=FakeNavSource(record=secondary_record),
            alert=lambda level, message: alerts.append((level, message)),
            **kwargs
        )
        return source, alerts

    def test_returns_primary_when_both_agree(self):
        source, alerts = self._source(
            {"code": "161130", "date": "2026-09-29", "nav": "4.5452"},
            {"code": "161130", "date": "2026-09-29", "nav": "4.5452"},
        )

        record = source.fetch_latest("161130")

        # 返回值来自主源
        self.assertEqual(record["nav"], "4.5452")
        self.assertEqual(source.last_comparison["match"], True)
        # 完全一致时不该有告警
        self.assertEqual([level for level, _ in alerts if level in ("WARN", "ALERT")], [])

    def test_alerts_when_difference_exceeds_tolerance(self):
        source, alerts = self._source(
            {"code": "161130", "date": "2026-09-29", "nav": "4.5452"},
            {"code": "161130", "date": "2026-09-29", "nav": "4.6000"},
        )

        record = source.fetch_latest("161130")

        # 仍然返回主源（东财优先）
        self.assertEqual(record["nav"], "4.5452")
        self.assertEqual(source.last_comparison["match"], False)
        self.assertAlmostEqual(source.last_comparison["diff"], 0.0548, places=4)
        levels = [level for level, _ in alerts]
        self.assertIn("ALERT", levels)
        # 告警文本要能看出是哪只基金、差多少。
        # 注意净值已被清洗成数字再转字符串，所以 4.6000 会变成 4.6。
        message = next(text for level, text in alerts if level == "ALERT")
        self.assertIn("161130", message)
        self.assertIn("4.5452", message)
        self.assertIn("4.6", message)

    def test_small_difference_within_tolerance_is_not_alerted(self):
        # 容差 0.001 元，0.0005 的舍入差不算问题
        source, alerts = self._source(
            {"code": "161130", "date": "2026-09-29", "nav": "4.5452"},
            {"code": "161130", "date": "2026-09-29", "nav": "4.5457"},
        )

        source.fetch_latest("161130")

        self.assertEqual(source.last_comparison["match"], True)
        self.assertEqual([level for level, _ in alerts if level == "ALERT"], [])

    def test_date_mismatch_with_same_value_only_informs(self):
        # 日期不同但数值一致：只 INFO，不告警
        source, alerts = self._source(
            {"code": "161130", "date": "2026-09-29", "nav": "4.5452"},
            {"code": "161130", "date": "2026-09-28", "nav": "4.5452"},
        )

        source.fetch_latest("161130")

        self.assertEqual(source.last_comparison["match"], True)
        self.assertEqual([level for level, _ in alerts if level == "ALERT"], [])
        self.assertIn("INFO", [level for level, _ in alerts])

    def test_falls_back_to_secondary_when_primary_missing(self):
        source, alerts = self._source(
            None,
            {"code": "161130", "date": "2026-09-29", "nav": "4.5452"},
        )

        record = source.fetch_latest("161130")

        # 主源挂了不能断流，用对照源顶上并明确告警
        self.assertEqual(record["nav"], "4.5452")
        self.assertIn("WARN", [level for level, _ in alerts])

    def test_returns_none_when_both_missing(self):
        source, _alerts = self._source(None, None)
        self.assertIsNone(source.fetch_latest("161130"))

    def test_primary_exception_does_not_break(self):
        source = navsource.CrossCheckNavSource(
            primary=FakeNavSource(error=RuntimeError("boom")),
            secondary=FakeNavSource(record={"code": "161130", "date": "2026-09-29", "nav": "4.5"}),
            alert=lambda level, message: None,
        )

        record = source.fetch_latest("161130")

        self.assertEqual(record["nav"], "4.5")

    def test_no_secondary_disables_comparison(self):
        source = navsource.CrossCheckNavSource(
            primary=FakeNavSource(record={"code": "161130", "date": "2026-09-29", "nav": "4.5452"}),
            secondary=None,
            alert=lambda level, message: None,
        )

        record = source.fetch_latest("161130")

        self.assertEqual(record["nav"], "4.5452")
        self.assertFalse(source.last_comparison["comparable"])
        self.assertIn("未配置对照信源", source.last_comparison["reason"])

    def test_values_are_cleaned_before_comparison(self):
        # 主源给 '4.5452'，对照源给带空白的 ' 4.5452 ' —— 清洗后应当算一致
        source, _alerts = self._source(
            {"code": "161130", "date": "2026-09-29", "nav": "4.5452"},
            {"code": "161130", "date": "2026-09/29", "nav": " 4.5452 "},
        )

        record = source.fetch_latest("161130")

        self.assertEqual(source.last_comparison["match"], True)
        self.assertEqual(record["date"], "2026-09-29")

    def test_describe_reports_selection(self):
        source, _alerts = self._source(
            {"code": "161130", "date": "2026-09-29", "nav": "4.5452"},
            {"code": "161130", "date": "2026-09-29", "nav": "4.5452"},
        )
        info = source.describe()

        self.assertEqual(info["primary"], "fake")
        self.assertEqual(info["secondary"], "fake")
        self.assertEqual(info["tolerance"], navsource.DEFAULT_TOLERANCE)

    def test_tolerance_from_env(self):
        old = os.environ.get(navsource.ENV_TOLERANCE)
        os.environ[navsource.ENV_TOLERANCE] = "0.5"
        try:
            source = navsource.CrossCheckNavSource(
                primary=FakeNavSource(record={"code": "1", "date": "d", "nav": "1.0"}),
                secondary=FakeNavSource(record={"code": "1", "date": "d", "nav": "1.4"}),
                alert=lambda level, message: None,
            )
            # 容差 0.5 时 0.4 的差异不应告警
            source.fetch_latest("1")
            self.assertTrue(source.last_comparison["match"])
        finally:
            if old is None:
                os.environ.pop(navsource.ENV_TOLERANCE, None)
            else:
                os.environ[navsource.ENV_TOLERANCE] = old


class NavReadyTest(unittest.TestCase):
    def test_before_and_after_1655(self):
        # 对应 PHP YahooUpdateNetValue() 里的 `if ($iHourMinute < 1655) return;`
        self.assertFalse(is_nav_ready(datetime.datetime(2026, 9, 30, 16, 54)))
        self.assertTrue(is_nav_ready(datetime.datetime(2026, 9, 30, 16, 55)))
        self.assertTrue(is_nav_ready(datetime.datetime(2026, 9, 30, 23, 0)))
        self.assertFalse(is_nav_ready(datetime.datetime(2026, 9, 30, 9, 30)))

    def test_custom_threshold(self):
        self.assertTrue(is_nav_ready(datetime.datetime(2026, 9, 30, 9, 30), hour_minute=900))


class NavUpdaterTest(unittest.TestCase):
    def _updater(self, record, now=None, store=None, **kwargs):
        return NavUpdater(
            source=FakeNavSource(record=record),
            store=store if store is not None else MemoryQuoteStore(),
            now_provider=lambda: now or datetime.datetime(2026, 10, 3, 20, 0),
            **kwargs
        )

    def test_writes_new_nav(self):
        store = MemoryQuoteStore()
        updater = self._updater({"date": "2026-09-29", "nav": "4.5452"}, store=store)

        updated = updater.update([("SZ161130", 1)])

        self.assertEqual(updated, 1)
        self.assertEqual(store.net_values[(1, "2026-09-29")], "4.5452")

    def test_skips_date_already_in_store(self):
        store = MemoryQuoteStore()
        store.write_net_value(1, "2026-09-29", "4.5000")
        updater = self._updater({"date": "2026-09-29", "nav": "4.5452"}, store=store)

        updated = updater.update([("SZ161130", 1)])

        self.assertEqual(updated, 0)
        # 已有值不应被覆盖
        self.assertEqual(store.net_values[(1, "2026-09-29")], "4.5000")

    def test_force_overwrites_existing(self):
        store = MemoryQuoteStore()
        store.write_net_value(1, "2026-09-29", "4.5000")
        updater = self._updater({"date": "2026-09-29", "nav": "4.5452"}, store=store)

        updated = updater.update([("SZ161130", 1)], force=True)

        self.assertEqual(updated, 1)
        self.assertEqual(store.net_values[(1, "2026-09-29")], "4.5452")

    def test_writes_yesterdays_nav_when_today_not_published(self):
        """关键回归：源返回的是**昨天**的净值时，必须能写进去。

        这是踩过的坑：早期实现拿"今天"去判断"是否已过公布时刻"，导致下午 3 点
        运行时（源给的是昨天净值）一条都写不进去。判断依据必须是源给出的日期。
        """
        store = MemoryQuoteStore()
        # 现在是 2026-09-30 的 15:00（未到 16:55），源给的是 09-29 的净值
        updater = self._updater(
            {"date": "2026-09-29", "nav": "4.5452"},
            now=datetime.datetime(2026, 9, 30, 15, 0),
            store=store,
        )

        updated = updater.update([("SZ161130", 1)])

        self.assertEqual(updated, 1)
        self.assertEqual(store.net_values[(1, "2026-09-29")], "4.5452")

    def test_skips_todays_nav_before_publish_time(self):
        # 源给的日期就是"今天"且还没到 16:55 → 应等一等（净值可能还在变）
        store = MemoryQuoteStore()
        updater = self._updater(
            {"date": "2026-09-30", "nav": "4.6000"},
            now=datetime.datetime(2026, 9, 30, 15, 0),
            store=store,
        )

        self.assertEqual(updater.update([("SZ161130", 1)]), 0)
        self.assertEqual(store.net_values, {})

    def test_source_without_record_is_reported(self):
        store = MemoryQuoteStore()
        updater = self._updater(None, store=store)

        self.assertEqual(updater.update([("SZ161130", 1)]), 0)
        self.assertIn("没有返回净值", updater.last_error)

    def test_source_exception_is_reported_not_raised(self):
        store = MemoryQuoteStore()
        updater = NavUpdater(
            source=FakeNavSource(error=RuntimeError("boom")),
            store=store,
            now_provider=lambda: datetime.datetime(2026, 10, 3, 20, 0),
        )

        self.assertEqual(updater.update([("SZ161130", 1)]), 0)
        self.assertIn("boom", updater.last_error)

    def test_non_fund_symbols_are_ignored(self):
        store = MemoryQuoteStore()
        updater = self._updater({"date": "2026-09-29", "nav": "1.0"}, store=store)

        # QQQ / ^GSPC 不是中国基金，不该走净值抓取
        self.assertEqual(updater.update([("QQQ", 4), ("^GSPC", 7)]), 0)

    def test_empty_targets_returns_zero(self):
        updater = self._updater({"date": "2026-09-29", "nav": "1.0"})
        self.assertEqual(updater.update([]), 0)

    def test_describe_reports_state(self):
        updater = self._updater({"date": "2026-09-29", "nav": "4.5452"})
        updater.update([("SZ161130", 1)])

        info = updater.describe()

        self.assertEqual(info["source"]["name"], "fake")
        self.assertEqual(info["update_hour_minute"], 1655)
        self.assertEqual(info["last_updated"], 1)


class NameCompatibilityTest(unittest.TestCase):
    """名称核对：这是为了抓住"SZ161130 被写成标普信息科技"那类错误。

    真实案例（库里曾经就是这样）：
        库里   SZ161130 -> 易方达标普信息科技LOF          ← 错的，那是 161128 的名字
        权威   161130   -> 易方达纳斯达克100ETF联接(QDII-LOF)A(人民币份额)
    """

    def test_correct_short_name_is_compatible_with_full_name(self):
        from navupdater import _name_compatible

        # 库里存简洁短名、权威源给全称 —— 应当判为相容
        self.assertTrue(_name_compatible(
            "易方达纳斯达克100LOF",
            "易方达纳斯达克100ETF联接(QDII-LOF)A(人民币份额)",
        ))
        self.assertTrue(_name_compatible(
            "国泰纳斯达克100ETF",
            "国泰纳斯达克100(QDII-ETF)",
        ))
        self.assertTrue(_name_compatible(
            "易方达标普500LOF",
            "易方达标普500指数(QDII-LOF)A(人民币份额)",
        ))

    def test_wrong_fund_name_is_incompatible(self):
        from navupdater import _name_compatible

        # 这就是库里原来的错误：161130 被安上了 161128 的名字
        self.assertFalse(_name_compatible(
            "易方达标普信息科技LOF",
            "易方达纳斯达克100ETF联接(QDII-LOF)A(人民币份额)",
        ))

    def test_missing_side_does_not_conclude(self):
        from navupdater import _name_compatible

        # 缺一侧时不下定论，避免误报
        self.assertTrue(_name_compatible(None, "某基金"))
        self.assertTrue(_name_compatible("某基金", None))

    def test_name_key_strips_noise(self):
        from navupdater import _name_key

        self.assertEqual(_name_key("国泰纳斯达克100(QDII-ETF)"), "国泰纳斯达克100")
        self.assertEqual(_name_key("易方达纳斯达克100LOF"), "易方达纳斯达克100")


class SyncNamesTest(unittest.TestCase):
    """名称同步：用权威源核对（并可选改写）stock.name。"""

    def _updater(self, authoritative):
        store = MemoryQuoteStore()
        # 直接构造一个带 connection 的最小假库，走 sync_names 的 SQL 分支
        store.connection = FakeConnection({"1": "易方达标普信息科技LOF"})
        return NavUpdater(
            source=FakeNavSource(record={"code": "161130", "date": "2026-09-29",
                                         "nav": "4.5452", "name": authoritative}),
            store=store,
        ), store.connection

    def test_reports_mismatch_without_writing(self):
        updater, connection = self._updater("易方达纳斯达克100ETF联接(QDII-LOF)A(人民币份额)")

        results = updater.sync_names([("SZ161130", "1")], apply=False)

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["mismatch"])
        self.assertFalse(results[0]["changed"])
        self.assertEqual(results[0]["current"], "易方达标普信息科技LOF")
        # 只报告时不能改库
        self.assertEqual(connection.updates, [])

    def test_apply_updates_to_authoritative_name(self):
        updater, connection = self._updater("易方达纳斯达克100ETF联接(QDII-LOF)A(人民币份额)")

        results = updater.sync_names([("SZ161130", "1")], apply=True)

        self.assertTrue(results[0]["changed"])
        self.assertEqual(len(connection.updates), 1)
        self.assertEqual(connection.updates[0][0], "易方达纳斯达克100ETF联接(QDII-LOF)A(人民币份额)")

    def test_compatible_name_is_left_alone(self):
        updater, connection = self._updater("易方达纳斯达克100ETF联接(QDII-LOF)A(人民币份额)")
        connection.names["1"] = "易方达纳斯达克100LOF"  # 已经是对的

        results = updater.sync_names([("SZ161130", "1")], apply=True)

        self.assertFalse(results[0]["mismatch"])
        self.assertEqual(connection.updates, [])

    def test_source_without_name_does_not_wipe(self):
        updater, connection = self._updater(None)

        results = updater.sync_names([("SZ161130", "1")], apply=True)

        self.assertFalse(results[0]["mismatch"])
        self.assertEqual(connection.updates, [])


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self._result = []

    def execute(self, sql, params=()):
        if sql.startswith("SELECT name FROM stock"):
            name = self.connection.names.get(str(params[0]))
            self._result = [{"name": name}] if name is not None else []
        elif sql.startswith("UPDATE stock SET name"):
            self.connection.updates.append((params[0], params[1]))
            self.connection.names[str(params[1])] = params[0]
            self._result = []
        elif sql.startswith("SELECT id, symbol FROM stock"):
            self._result = [{"id": key, "symbol": "SZ161130"} for key in self.connection.names]
        else:
            self._result = []

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return self._result

    def close(self):
        pass


class FakeConnection:
    def __init__(self, names):
        self.names = dict(names)
        self.updates = []

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        pass


if __name__ == "__main__":
    unittest.main()
