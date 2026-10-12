"""`forexsource.py` / `forexupdater.py` 与名称核对的单元测试。

对应 PHP：``php/stock/chinamoney.php`` 的 ``GetChinaMoney()``、
``php/stock/cnyref.php`` 的 ``CnyReference``。

全部离线测试，不打网络。
"""

import os
import unittest

from source import forexsource
from source import navsource
from updater.forexupdater import ForexUpdater
from sql.quotestore import MemoryQuoteStore

# 真实响应样本（取自新浪，GB18030）
SINA_TEXT = (
    'var hq_str_fx_susdcny="03:00:00,6.7050000000,6.7072000000,6.7050000000,'
    '51.0000000000,6.7050000000,6.7072000000,6.7021000000,6.7050000000,'
    '在岸人民币,2026-10-01";'
)
# 裸代码布局：日期在最后一位（与 fx_ 代码不同）
SINA_TEXT_RAW = (
    'var hq_str_USDCNY="03:00:00,6.7050,6.7072,6.7085,51,6.7050,6.7072,6.7021,'
    '6.7050,美元人民币,2026-10-01";'
)
# 中国货币网（PHP chinamoney.php 的源）
CHINAMONEY_PAYLOAD = {
    "data": {"lastDate": "2026-09-30 9:15"},
    "records": [
        {"vrtEName": "USD/CNY", "price": "6.7351"},
        {"vrtEName": "100JPY/CNY", "price": "4.2727"},
        {"vrtEName": "HKD/CNY", "price": "0.85842"},
        {"vrtEName": "EUR/CNY", "price": "7.6215"},
    ],
}


class FakeResponse:
    def __init__(self, payload=None, content=None):
        self._payload = payload
        self.content = content if content is not None else b""
        self.status_code = 200
        self.text = ""

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append({"url": url, "headers": headers or {}})
        if self.error is not None:
            raise self.error
        return self.response


class StaleRateWarningTest(unittest.TestCase):
    """汇率回退过久必须留下警告 —— 这是 7.1 那个坑的防线。"""

    def test_warns_when_fallback_is_too_old(self):
        from core.stockdata import MAX_RATE_FALLBACK_DAYS, MySqlFundDataSource

        store = MySqlFundDataSource.__new__(MySqlFundDataSource)
        store.rate_warnings = []

        # 直接调用记录逻辑：问 2026-09-29，实际用的是 2026-01-02
        store._note_rate_age("2026-09-29", "2026-01-02", "SZ161130")

        self.assertEqual(len(store.rate_warnings), 1)
        self.assertIn("270 天", store.rate_warnings[0])
        self.assertIn("SZ161130", store.rate_warnings[0])
        self.assertEqual(MAX_RATE_FALLBACK_DAYS, 10)

    def test_no_warning_within_threshold(self):
        from core.stockdata import MySqlFundDataSource

        store = MySqlFundDataSource.__new__(MySqlFundDataSource)
        store.rate_warnings = []

        # 相差 3 天（周末 + 节假日级别）不该报警
        store._note_rate_age("2026-09-29", "2026-09-26", "SZ161130")

        self.assertEqual(store.rate_warnings, [])

    def test_bad_dates_are_ignored(self):
        from core.stockdata import MySqlFundDataSource

        store = MySqlFundDataSource.__new__(MySqlFundDataSource)
        store.rate_warnings = []
        store._note_rate_age("not-a-date", "2026-01-02", "X")
        self.assertEqual(store.rate_warnings, [])


class FakeForexSource(forexsource.ForexSource):
    """假汇率源。"""

    name = "fake"

    def __init__(self, record=None, error=None):
        self.record = record
        self.error = error
        self.calls = []

    def fetch_rate(self, symbol):
        self.calls.append(symbol)
        if self.error is not None:
            raise self.error
        return self.record


class ForexSourceRegistryTest(unittest.TestCase):
    def test_sources_registered(self):
        for expected in ("sina", "chinamoney", "crosscheck", "fixed"):
            self.assertIn(expected, forexsource.list_forex_sources())

    def test_default_is_crosscheck_with_sina_primary(self):
        # 默认：新浪为主（实时）+ 中国货币网对照
        self.assertEqual(forexsource.DEFAULT_FOREX_SOURCE, "crosscheck")
        source = forexsource.get_forex_source()
        self.assertEqual(source.primary.name, "sina")
        self.assertEqual(source.secondary.name, "chinamoney")

    def test_crosscheck_does_not_recurse(self):
        old = os.environ.get(forexsource.ENV_SOURCE)
        os.environ[forexsource.ENV_SOURCE] = "crosscheck"
        try:
            source = forexsource.get_forex_source()
            self.assertEqual(source.primary.name, "sina")
        finally:
            if old is None:
                os.environ.pop(forexsource.ENV_SOURCE, None)
            else:
                os.environ[forexsource.ENV_SOURCE] = old

    def test_unknown_source_raises(self):
        with self.assertRaises(ValueError):
            forexsource.get_forex_source("no-such-source")

    def test_forex_symbols(self):
        # 与 PHP 的 CnyReference 符号一致
        self.assertEqual(
            list(forexsource.FOREX_SYMBOLS),
            ["USCNY", "HKCNY", "EUCNY", "JPCNY"],
        )


class FixedForexSourceTest(unittest.TestCase):
    def test_returns_configured_rate(self):
        source = forexsource.FixedForexSource({"USCNY": 7.1}, date="2026-01-02")

        record = source.fetch_rate("USCNY")

        self.assertEqual(record["rate"], 7.1)
        self.assertEqual(record["date"], "2026-01-02")

    def test_unknown_symbol_returns_none(self):
        source = forexsource.FixedForexSource({"USCNY": 7.1})
        self.assertIsNone(source.fetch_rate("HKCNY"))


class SinaForexSourceTest(unittest.TestCase):
    def test_parses_fx_code_line(self):
        session = FakeSession(FakeResponse(content=SINA_TEXT.encode("gb18030")))
        source = forexsource.SinaForexSource(session=session)

        record = source.fetch_rate("USCNY")

        self.assertAlmostEqual(record["rate"], 6.705, places=4)
        self.assertEqual(record["date"], "2026-10-01")
        self.assertEqual(record["name"], "在岸人民币")
        self.assertEqual(record["source"], "sina")

    def test_parses_raw_code_line_date_at_end(self):
        # 裸代码 USDCNY 的日期在最后一位，位置与 fx_ 代码不同
        session = FakeSession(FakeResponse(content=SINA_TEXT_RAW.encode("gb18030")))
        source = forexsource.SinaForexSource(session=session)

        record = source.fetch_rate("USCNY")

        self.assertAlmostEqual(record["rate"], 6.705, places=4)
        self.assertEqual(record["date"], "2026-10-01")

    def test_sends_referer(self):
        session = FakeSession(FakeResponse(content=SINA_TEXT.encode("gb18030")))
        forexsource.SinaForexSource(session=session).fetch_rate("USCNY")

        self.assertIn("Referer", session.calls[0]["headers"])

    def test_network_error_returns_none(self):
        session = FakeSession(error=RuntimeError("boom"))
        self.assertIsNone(forexsource.SinaForexSource(session=session).fetch_rate("USCNY"))

    def test_missing_date_returns_none_not_garbage(self):
        """日期字段缺失时不能把数字当日期传出去（实测踩到过 0.0000）。"""
        text = 'var hq_str_USDCNY="03:00:00,6.70,6.70,6.70,51,6.70,6.70,6.70,6.7050,美元人民币";'
        session = FakeSession(FakeResponse(content=text.encode("gb18030")))
        record = forexsource.SinaForexSource(session=session).fetch_rate("USCNY")

        self.assertIsNone(record["date"])
        self.assertAlmostEqual(record["rate"], 6.705, places=4)


class ChinaMoneyForexSourceTest(unittest.TestCase):
    def test_parses_rates(self):
        session = FakeSession(FakeResponse(payload=CHINAMONEY_PAYLOAD))
        source = forexsource.ChinaMoneyForexSource(session=session)

        self.assertAlmostEqual(source.fetch_rate("USCNY")["rate"], 6.7351, places=4)
        self.assertAlmostEqual(source.fetch_rate("HKCNY")["rate"], 0.85842, places=5)
        self.assertAlmostEqual(source.fetch_rate("EUCNY")["rate"], 7.6215, places=4)

    def test_100jpy_is_divided_by_100(self):
        # 中国货币网按 100JPY/CNY 报价，必须除 100 才是"每 1 日元"
        session = FakeSession(FakeResponse(payload=CHINAMONEY_PAYLOAD))
        record = forexsource.ChinaMoneyForexSource(session=session).fetch_rate("JPCNY")

        self.assertAlmostEqual(record["rate"], 0.042727, places=6)

    def test_date_strips_time_part(self):
        session = FakeSession(FakeResponse(payload=CHINAMONEY_PAYLOAD))
        record = forexsource.ChinaMoneyForexSource(session=session).fetch_rate("USCNY")

        self.assertEqual(record["date"], "2026-09-30")

    def test_payload_loaded_once(self):
        session = FakeSession(FakeResponse(payload=CHINAMONEY_PAYLOAD))
        source = forexsource.ChinaMoneyForexSource(session=session)

        source.fetch_all()

        # 整份 JSON 一次拿全，不应每个币种都请求
        self.assertEqual(len(session.calls), 1)

    def test_unknown_symbol_returns_none(self):
        session = FakeSession(FakeResponse(payload=CHINAMONEY_PAYLOAD))
        self.assertIsNone(forexsource.ChinaMoneyForexSource(session=session).fetch_rate("GBPCNY"))


class CrossCheckForexSourceTest(unittest.TestCase):
    """双源汇率对比与告警。"""

    def _source(self, primary_rate, secondary_rate, **kwargs):
        alerts = []
        source = forexsource.CrossCheckForexSource(
            primary=FakeForexSource({"symbol": "USCNY", "date": "2026-10-01", "rate": primary_rate}),
            secondary=FakeForexSource({"symbol": "USCNY", "date": "2026-09-30", "rate": secondary_rate}),
            alert=lambda level, message: alerts.append((level, message)),
            **kwargs
        )
        return source, alerts

    def test_returns_primary_rate(self):
        source, _alerts = self._source(6.705, 6.7351)

        record = source.fetch_rate("USCNY")

        # 主源（新浪）优先
        self.assertAlmostEqual(record["rate"], 6.705, places=4)
        self.assertTrue(source.last_comparison["match"])

    def test_alerts_when_relative_diff_exceeds_tolerance(self):
        # 相对差 1% 容差：6.705 vs 6.9 差 2.8%，应告警
        source, alerts = self._source(6.705, 6.9)

        source.fetch_rate("USCNY")

        self.assertFalse(source.last_comparison["match"])
        self.assertIn("ALERT", [level for level, _ in alerts])
        message = next(text for level, text in alerts if level == "ALERT")
        self.assertIn("USCNY", message)

    def test_small_diff_within_tolerance_no_alert(self):
        # 0.45% 的差异（实测新浪 vs 中国货币网就是这样）不算问题
        source, alerts = self._source(6.705, 6.7351)

        source.fetch_rate("USCNY")

        self.assertTrue(source.last_comparison["match"])
        self.assertEqual([level for level, _ in alerts if level == "ALERT"], [])

    def test_falls_back_to_secondary(self):
        alerts = []
        source = forexsource.CrossCheckForexSource(
            primary=FakeForexSource(None),
            secondary=FakeForexSource({"symbol": "USCNY", "date": "2026-09-30", "rate": 6.7351}),
            alert=lambda level, message: alerts.append((level, message)),
        )

        record = source.fetch_rate("USCNY")

        self.assertAlmostEqual(record["rate"], 6.7351, places=4)
        self.assertIn("WARN", [level for level, _ in alerts])

    def test_both_missing_returns_none(self):
        source = forexsource.CrossCheckForexSource(
            primary=FakeForexSource(None),
            secondary=FakeForexSource(None),
            alert=lambda level, message: None,
        )
        self.assertIsNone(source.fetch_rate("USCNY"))

    def test_primary_exception_does_not_break(self):
        source = forexsource.CrossCheckForexSource(
            primary=FakeForexSource(error=RuntimeError("boom")),
            secondary=FakeForexSource({"symbol": "USCNY", "date": "2026-09-30", "rate": 6.7}),
            alert=lambda level, message: None,
        )

        record = source.fetch_rate("USCNY")

        self.assertAlmostEqual(record["rate"], 6.7, places=4)


class ForexUpdaterTest(unittest.TestCase):
    def test_writes_rates_to_net_value_history(self):
        store = MemoryQuoteStore()
        store.connection = _FakeConnection({"USCNY": "8"})
        updater = ForexUpdater(
            source=FakeForexSource({"symbol": "USCNY", "date": "2026-10-01", "rate": 6.705}),
            store=store,
            symbols=["USCNY"],
        )

        updated = updater.update(apply=True)

        self.assertEqual(updated, 1)
        self.assertEqual(store.net_values[("8", "2026-10-01")], 6.705)

    def test_weekend_rate_is_accepted(self):
        """汇率周末也有报价，不该被"周末守卫"拦掉（实测把 HKCNY 等全拦了）。"""
        store = MemoryQuoteStore()
        store.connection = _FakeConnection({"HKCNY": "9"})
        updater = ForexUpdater(
            # 2026-10-03 是周六
            source=FakeForexSource({"symbol": "HKCNY", "date": "2026-10-03", "rate": 0.8544}),
            store=store,
            symbols=["HKCNY"],
        )

        self.assertEqual(updater.update(apply=True), 1)
        self.assertIn(("9", "2026-10-03"), store.net_values)

    def test_missing_stock_symbol_is_reported(self):
        store = MemoryQuoteStore()
        store.connection = _FakeConnection({})  # 库里没有该符号
        updater = ForexUpdater(
            source=FakeForexSource({"symbol": "USCNY", "date": "2026-10-01", "rate": 6.7}),
            store=store,
            symbols=["USCNY"],
        )

        self.assertEqual(updater.update(apply=True), 0)
        self.assertIn("stock 表里没有这个符号", updater.last_error)

    def test_source_without_rate_is_reported(self):
        store = MemoryQuoteStore()
        store.connection = _FakeConnection({"USCNY": "8"})
        updater = ForexUpdater(
            source=FakeForexSource(None), store=store, symbols=["USCNY"]
        )

        self.assertEqual(updater.update(apply=True), 0)
        self.assertIn("没有返回汇率", updater.last_error)

    def test_describe(self):
        store = MemoryQuoteStore()
        store.connection = _FakeConnection({"USCNY": "8"})
        updater = ForexUpdater(
            source=FakeForexSource({"symbol": "USCNY", "date": "2026-10-01", "rate": 6.7}),
            store=store, symbols=["USCNY"],
        )
        updater.update(apply=True)

        info = updater.describe()

        self.assertEqual(info["source"]["name"], "fake")
        self.assertEqual(info["last_updated"], 1)


class NormalizeDateTest(unittest.TestCase):
    """日期清洗：不能把数字当日期（这是实测踩到的坑）。"""

    def test_valid_iso_date(self):
        self.assertEqual(forexsource._normalize_date("2026-10-01"), "2026-10-01")
        self.assertEqual(forexsource._normalize_date("2026/10/01"), "2026-10-01")
        # 中国货币网的 lastDate 带时间
        self.assertEqual(forexsource._normalize_date("2026-09-30 9:15"), "2026-09-30")

    def test_numbers_are_not_dates(self):
        for value in ("0.0000", "6.705", "", None, "51"):
            self.assertIsNone(forexsource._normalize_date(value), repr(value))

    def test_impossible_dates_rejected(self):
        self.assertIsNone(forexsource._normalize_date("2026-13-01"))
        self.assertIsNone(forexsource._normalize_date("2026-00-10"))


class _FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self._result = []

    def execute(self, sql, params=()):
        if sql.startswith("SELECT id FROM stock WHERE symbol"):
            value = self.connection.ids.get(str(params[0]))
            self._result = [{"id": value}] if value is not None else []
        else:
            self._result = []

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return self._result

    def close(self):
        pass


class _FakeConnection:
    def __init__(self, ids):
        self.ids = dict(ids)
        self.commits = 0

    def cursor(self):
        return _FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass


class ForexHistoryBackfillTest(unittest.TestCase):
    """历史回补：上游不稳定时必须优雅退化，不能抛异常。"""

    def test_unknown_symbol_returns_empty(self):
        # 只对已配置 secid 的符号回补
        self.assertEqual(forexsource.fetch_history_eastmoney("GBPCNY"), [])

    def test_unreachable_upstream_returns_empty(self):
        session = FakeSession(error=RuntimeError("ConnectionError"))
        self.assertEqual(
            forexsource.fetch_history_eastmoney("USCNY", session=session), [])

    def test_parses_klines(self):
        payload = {"data": {"klines": [
            "2026-09-28,6.7223,6.7126",
            "2026-09-29,6.7110,6.7079",
        ]}}
        session = FakeSession(FakeResponse(payload=payload))

        rows = forexsource.fetch_history_eastmoney("USCNY", session=session)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["date"], "2026-09-28")
        # 取收盘（第 3 个字段）
        self.assertAlmostEqual(rows[1]["rate"], 6.7079, places=4)

    def test_malformed_lines_are_skipped(self):
        payload = {"data": {"klines": ["garbage", "2026-09-29,6.71,6.70"]}}
        session = FakeSession(FakeResponse(payload=payload))

        rows = forexsource.fetch_history_eastmoney("USCNY", session=session)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], "2026-09-29")


class _ForexCursor:
    """按 SQL 前缀分派的假游标（够用即可）。"""

    def __init__(self, connection):
        self.connection = connection
        self._result = []

    def execute(self, sql, params=()):
        if sql.startswith("SELECT id FROM stock WHERE symbol"):
            value = self.connection.ids.get(str(params[0]))
            self._result = [{"id": value}] if value is not None else []
        elif sql.startswith("SELECT close FROM netvaluehistory WHERE stock_id = %s AND date = %s"):
            key = (str(params[0]), str(params[1]))
            self._result = [{"close": self.connection.rates[key]}] if key in self.connection.rates else []
        elif sql.startswith("SELECT date, close FROM netvaluehistory WHERE stock_id = %s AND date < %s"):
            stock_id, date = str(params[0]), str(params[1])
            rows = sorted(
                ({"date": key[1], "close": value}
                 for key, value in self.connection.rates.items()
                 if key[0] == stock_id and key[1] < date),
                key=lambda item: item["date"], reverse=True,
            )
            self._result = rows[:1]
        elif sql.startswith("SELECT date FROM calibrationhistory"):
            self._result = [{"date": self.connection.calibration_date}]
        elif sql.startswith("SELECT id, symbol FROM stock"):
            self._result = [{"id": key, "symbol": symbol}
                            for symbol, key in self.connection.ids.items()]
        elif sql.startswith("DELETE FROM netvaluehistory"):
            self.connection.rates.pop((str(params[0]), str(params[1])), None)
            self._result = []
        else:
            self._result = []

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return self._result

    def close(self):
        pass


class _ForexConnection:
    def __init__(self, ids, rates=None, calibration_date="2026-09-29"):
        self.ids = dict(ids)
        self.rates = dict(rates or {})
        self.calibration_date = calibration_date

    def cursor(self):
        return _ForexCursor(self)

    def commit(self):
        pass

    def rollback(self):
        pass


class ForexGapAndPurgeTest(unittest.TestCase):
    """给官方日期补汇率、清掉手工占位值。"""

    def _updater(self, rates, ids=None, calibration_date="2026-09-29"):
        store = MemoryQuoteStore()
        store.connection = _ForexConnection(
            ids or {"USCNY": "8", "SZ161130": "1"}, rates, calibration_date)
        return ForexUpdater(
            source=FakeForexSource({"symbol": "USCNY", "date": "2026-10-01", "rate": 6.705}),
            store=store,
            symbols=["USCNY"],
        ), store

    def test_fills_official_date_from_nearby_trading_day(self):
        # 官方日期 2026-09-29 没有汇率，但 09-28 有真实值且相差 1 天 → 应补上
        updater, store = self._updater({("8", "2026-09-28"): 6.7126})

        fills = updater.fill_official_dates(apply=True)

        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0]["date"], "2026-09-29")
        self.assertEqual(fills[0]["source_date"], "2026-09-28")
        self.assertEqual(fills[0]["gap_days"], 1)
        self.assertIn(("8", "2026-09-29"), store.net_values)

    def test_does_not_fill_when_gap_too_large(self):
        # 只有 9 个月前的值 → 不该拿它当"官方日期的汇率"（这正是 7.1 那个坑）
        updater, store = self._updater({("8", "2026-01-02"): 7.1})

        fills = updater.fill_official_dates(apply=True)

        self.assertEqual(fills, [])
        self.assertNotIn(("8", "2026-09-29"), store.net_values)

    def test_skips_when_date_already_has_rate(self):
        updater, _store = self._updater({("8", "2026-09-29"): 6.7079})
        self.assertEqual(updater.fill_official_dates(apply=True), [])

    def test_purge_fake_removes_seed_placeholder(self):
        updater, store = self._updater({("8", "2026-01-02"): 7.1})

        removed = updater.purge_fake_rates(apply=True)

        self.assertEqual(len(removed), 1)
        self.assertEqual(removed[0]["symbol"], "USCNY")
        self.assertEqual(removed[0]["rate"], 7.1)
        self.assertNotIn(("8", "2026-01-02"), store.connection.rates)

    def test_purge_fake_noop_when_absent(self):
        updater, _store = self._updater({("8", "2026-10-01"): 6.705})
        self.assertEqual(updater.purge_fake_rates(apply=True), [])


class SafeForexSourceTest(unittest.TestCase):
    """国家外汇管理局历史中间价 —— 汇率历史序列的正解。

    接口来自 AKShare 的 ``currency_boc_safe()``（本机装不上 akshare，
    所以读源码后用 requests 自行实现）。下面用真实响应的表格片段。
    """

    # 真实表头与数据行（取自 safe.gov.cn 实际响应，裁剪到需要的列）
    TABLE = (
        "<table><tr>"
        "<th>日期</th><th>美元</th><th>欧元</th><th>日元</th><th>港元</th>"
        "</tr>"
        "<tr><td>2026-09-30</td><td>673.51</td><td>762.15</td><td>4.2727</td><td>85.842</td></tr>"
        "<tr><td>2026-09-29</td><td>674.11</td><td>764.94</td><td>4.275</td><td>85.935</td></tr>"
        "<tr><td>2026-09-28</td><td>673.99</td><td>765.64</td><td>4.2685</td><td>85.923</td></tr>"
        "</table>"
    )

    class _Response:
        def __init__(self, text):
            self.text = text
            self.encoding = "utf-8"

    class _Session:
        def __init__(self, text):
            self.text = text
            self.calls = []

        def post(self, url, data=None, headers=None, timeout=None):
            self.calls.append({"url": url, "data": data, "headers": headers or {}})
            return SafeForexSourceTest._Response(self.text)

    def test_parses_and_divides_by_100(self):
        session = self._Session(self.TABLE)
        rows = forexsource.fetch_history_safe(
            "USCNY", "2026-09-25", "2026-10-03", session=session)

        self.assertEqual(len(rows), 3)
        by_date = {row["date"]: row["rate"] for row in rows}
        # 674.11 / 100 = 6.7411 —— 正是基金官方日期那天的值
        self.assertAlmostEqual(by_date["2026-09-29"], 6.7411, places=4)
        self.assertAlmostEqual(by_date["2026-09-30"], 6.7351, places=4)

    def test_jpy_and_hkd_and_eur(self):
        session = self._Session(self.TABLE)

        jpy = {row["date"]: row["rate"] for row in forexsource.fetch_history_safe(
            "JPCNY", "2026-09-25", "2026-10-03", session=session)}
        self.assertAlmostEqual(jpy["2026-09-29"], 0.04275, places=6)

        hkd = {row["date"]: row["rate"] for row in forexsource.fetch_history_safe(
            "HKCNY", "2026-09-25", "2026-10-03", session=session)}
        self.assertAlmostEqual(hkd["2026-09-29"], 0.85935, places=5)

        eur = {row["date"]: row["rate"] for row in forexsource.fetch_history_safe(
            "EUCNY", "2026-09-25", "2026-10-03", session=session)}
        self.assertAlmostEqual(eur["2026-09-29"], 7.6494, places=4)

    def test_rows_are_sorted_by_date(self):
        # 接口在区间较大时行序不保证，解析后必须按日期升序
        shuffled = (
            "<table><tr><th>日期</th><th>美元</th></tr>"
            "<tr><td>2026-09-29</td><td>674.11</td></tr>"
            "<tr><td>2026-09-25</td><td>671.00</td></tr>"
            "<tr><td>2026-09-30</td><td>673.51</td></tr>"
            "</table>"
        )
        rows = forexsource.fetch_history_safe(
            "USCNY", "2026-09-25", "2026-09-30", session=self._Session(shuffled))

        self.assertEqual([row["date"] for row in rows],
                         ["2026-09-25", "2026-09-29", "2026-09-30"])

    def test_unknown_symbol_returns_empty(self):
        session = self._Session(self.TABLE)
        self.assertEqual(
            forexsource.fetch_history_safe("GBPCNY", "2026-09-25", "2026-10-03", session=session), [])

    def test_network_error_returns_empty(self):
        class _Broken:
            def post(self, *args, **kwargs):
                raise RuntimeError("boom")

        self.assertEqual(
            forexsource.fetch_history_safe("USCNY", "2026-09-25", "2026-10-03", session=_Broken()), [])

    def test_source_reuses_one_request_for_all_symbols(self):
        session = self._Session(self.TABLE)
        source = forexsource.SafeForexSource(
            session=session, start_date="2026-09-25", end_date="2026-10-03")

        for symbol in forexsource.FOREX_SYMBOLS:
            source.fetch_history(symbol)

        # 整张表一次取回，四个币种共享；且请求参数带上了区间
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(session.calls[0]["data"]["startDate"], "2026-09-25")

    def test_fetch_rate_returns_latest(self):
        session = self._Session(self.TABLE)
        source = forexsource.SafeForexSource(
            session=session, start_date="2026-09-25", end_date="2026-10-03")

        record = source.fetch_rate("USCNY")

        self.assertEqual(record["date"], "2026-09-30")
        self.assertAlmostEqual(record["rate"], 6.7351, places=4)
        self.assertEqual(record["source"], "safe")

    def test_safe_is_registered(self):
        self.assertIn("safe", forexsource.list_forex_sources())


if __name__ == "__main__":
    unittest.main()
