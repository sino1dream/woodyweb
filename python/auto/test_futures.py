"""``futuresquotes`` / ``futurescalib`` 的单元测试。

对应 PHP：
* ``php/stock/stockref.php:447`` ``LoadSinaFutureData()`` —— 抓期货
* ``php/stock/stockref.php:413`` ``_onSinaFuture($ar)`` —— 字段映射
* ``php/stock/stockref.php:235`` ``CheckAdjustFactorTime()`` —— 时刻对齐才校准
* ``php/stock/fundpairref.php:174`` ``AverageCalibraion()`` —— 按平均写校准
* ``php/stock/qdiiref.php:161`` ``EstRealtimeNetValue()`` —— 实时 EST
"""

from __future__ import annotations

import datetime
import unittest

from calc.futurescalib import (
    FALLBACK_FUTURES,
    FALLBACK_PROXIES,
    FUTURES_VALUE_TRANSFORMS,
    FuturesCalibrator,
    FuturesPairStore,
    build_futures_calibrator,
    check_adjust_time,
)
from source.futuresquotes import (
    MIN_FIELD_COUNT,
    SinaFuturesClient,
    FuturesQuote,
    get_quote_symbol,
    is_futures_symbol,
    parse_futures_payload,
    parse_futures_text,
)
from core.stocksymbol import TIME_ZONE_SHANGHAI


#: 真实的 ``hq_str_hf_NQ`` 响应（2026-10-07 实测）。
LIVE_NQ = ('31365.390,,31349.500,31350.000,31521.000,31143.000,'
           '23:44:39,31483.250,31510.000,0,3,30,2026-10-07,纳斯达克100指数期货,0')
LIVE_ES = ('7837.818,,7839.750,7840.000,7884.500,7815.750,'
           '23:44:39,7874.000,7881.500,0,55,99,2026-10-07,标普500指数期货,0')


class FuturesSymbolTest(unittest.TestCase):
    """期货符号的识别与行情代码转换。"""

    def test_is_futures_symbol(self):
        for symbol in ("hf_NQ", "hf_ES", "hf_CL", "hf_NK"):
            self.assertTrue(is_futures_symbol(symbol), symbol)
        for symbol in ("QQQ", "SZ161130", "sh600000", "^NDX", ""):
            self.assertFalse(is_futures_symbol(symbol), symbol)

    def test_get_quote_symbol_keeps_bare_symbol_for_futures(self):
        """**这是本模块存在的首要原因。**

        ``StockSymbol.get_sina_symbol()`` 会给 ``hf_NQ`` 加 ``gb_`` 前缀
        （``gb_hf_nq``），新浪当成美股代码返回空 —— PHP 的
        ``GetSinaSymbol()`` 有同样的兜底，所以它的期货取数走的是
        ``DebugGetSinaFileName()``（文件名 = 裸符号）。
        """
        from core.stocksymbol import StockSymbol

        self.assertEqual(get_quote_symbol("hf_NQ"), "hf_NQ")
        self.assertEqual(get_quote_symbol("hf_ES"), "hf_ES")
        # 对照：直接调 get_sina_symbol 会得到错的前缀
        self.assertEqual(StockSymbol("hf_NQ").get_sina_symbol(), "gb_hf_nq")

    def test_get_quote_symbol_unchanged_for_normal_symbols(self):
        from core.stocksymbol import StockSymbol

        for symbol in ("SZ161130", "SH513100", "QQQ", "sh600000"):
            self.assertEqual(get_quote_symbol(symbol), StockSymbol(symbol).get_sina_symbol(), symbol)

    def test_get_quote_symbol_handles_empty(self):
        self.assertIsNone(get_quote_symbol(""))
        self.assertIsNone(get_quote_symbol(None))


class FuturesParseTest(unittest.TestCase):
    """字段解析，逐项对照 PHP ``_onSinaFuture($ar)``。"""

    def test_field_mapping_matches_php(self):
        quote = parse_futures_payload("hf_NQ", LIVE_NQ)

        self.assertIsNotNone(quote)
        # PHP: strPrice=ar[0], strHigh=ar[4], strLow=ar[5], strTime=ar[6],
        #      strPrevPrice=ar[7], strOpen=ar[8], strDate=ar[12], strName=ar[13]
        self.assertEqual(quote.price, "31365.390")
        self.assertEqual(quote.high, "31521.000")
        self.assertEqual(quote.low, "31143.000")
        # 时间**不是**新浪原值：PHP 的 _onSinaFuture 会 _convertDateTimeToUS，
        # 即先按上海时间解读、再换到本符号时区（见下一条用例）。
        self.assertEqual(quote.time, "11:44:39")
        self.assertEqual(quote.prev_price, "31483.250")
        self.assertEqual(quote.open, "31510.000")
        self.assertEqual(quote.date, "2026-10-07")
        self.assertEqual(quote.name, "纳斯达克100指数期货")

    def test_time_is_converted_out_of_shanghai(self):
        """新浪给的``23:44:39``是上海时间，必须换到纽约（``11:44:39``）。

        曾经漏了这一步：期货存的是上海时刻、指数存的是纽约时刻，
        ``CheckAdjustFactorTime()`` 于是**永远不可能对齐**（还常常差一天），
        期货校准值一条都写不进去，实时 EST 只能回落中间层快照。
        """
        quote = parse_futures_payload("hf_NQ", LIVE_NQ)
        self.assertEqual(quote.date, "2026-10-07")
        self.assertEqual(quote.time, "11:44:39")
        # 跨日那一侧：上海 10-15 凌晨 -> 纽约还是 10-14
        late = LIVE_NQ.replace("23:44:39", "01:04:19").replace("2026-10-07", "2026-10-15")
        overnight = parse_futures_payload("hf_NQ", late)
        self.assertEqual(overnight.date, "2026-10-14")
        self.assertEqual(overnight.time, "13:04:19")

    def test_symbol_is_used_as_the_sina_symbol(self):
        quote = parse_futures_payload("hf_NQ", LIVE_NQ)
        self.assertEqual(quote.symbol, "hf_NQ")
        self.assertEqual(quote.sina_symbol, "hf_NQ")

    def test_too_few_fields_returns_none(self):
        """PHP 是 ``if (count($ar) >= 13)``；不足就当没数据。"""
        short = ",".join(["1"] * (MIN_FIELD_COUNT - 1))
        self.assertIsNone(parse_futures_payload("hf_NQ", short))
        just_enough = ",".join(["1"] * MIN_FIELD_COUNT)
        self.assertIsNotNone(parse_futures_payload("hf_NQ", just_enough))

    def test_fetched_datetime(self):
        quote = parse_futures_payload("hf_NQ", LIVE_NQ)
        moment = quote.fetched_datetime()
        # 已换算到纽约时区（见 test_time_is_converted_out_of_shanghai）
        self.assertEqual(moment, datetime.datetime(2026, 10, 7, 11, 44, 39))

    def test_fetched_datetime_missing_parts(self):
        self.assertIsNone(FuturesQuote("hf_NQ", "hf_NQ").fetched_datetime())
        self.assertIsNone(FuturesQuote("hf_NQ", "hf_NQ", date="2026-10-07").fetched_datetime())

    def test_parse_batch_response(self):
        text = (
            'var hq_str_hf_NQ="%s";\n'
            'var hq_str_hf_ES="%s";\n' % (LIVE_NQ, LIVE_ES)
        )
        quotes = parse_futures_text(text)

        self.assertEqual(sorted(quotes), ["hf_ES", "hf_NQ"])
        self.assertEqual(quotes["hf_ES"].price, "7837.818")
        self.assertEqual(quotes["hf_ES"].name, "标普500指数期货")


class FakeFuturesClient:
    """假的期货客户端：只回放预置行情。"""

    def __init__(self, quotes):
        self.quotes = quotes
        self.requested = []

    def fetch(self, symbols):
        self.requested.append(list(symbols))
        return dict((s, q) for s, q in self.quotes.items() if s in symbols)

    def fetch_one(self, symbol):
        return self.quotes.get(symbol)


class FuturesClientTest(unittest.TestCase):
    def test_client_requests_bare_symbols(self):
        """必须用裸符号请求 —— 这是实测确认过的。"""
        seen = {}

        def fetcher(symbols):
            seen["symbols"] = list(symbols)
            return 'var hq_str_hf_NQ="%s";' % LIVE_NQ

        client = SinaFuturesClient(fetcher=fetcher)
        quotes = client.fetch(["hf_NQ"])

        self.assertEqual(seen["symbols"], ["hf_NQ"])
        self.assertEqual(quotes["hf_NQ"].price, "31365.390")

    def test_client_returns_empty_for_no_symbols(self):
        client = SinaFuturesClient(fetcher=lambda symbols: "")
        self.assertEqual(client.fetch([]), {})
        self.assertIsNone(client.fetch_one("hf_NQ"))

    def test_client_decodes_bytes(self):
        # 复用 sinaquotes 的解码顺序：先 UTF-8 再 GB18030
        payload = ('var hq_str_hf_NQ="%s";' % LIVE_NQ).encode("utf-8")
        client = SinaFuturesClient(fetcher=lambda symbols: payload)
        self.assertEqual(client.fetch_one("hf_NQ").name, "纳斯达克100指数期货")


class CheckAdjustTimeTest(unittest.TestCase):
    """``CheckAdjustFactorTime()``：日期与 HH:MM 都相同才允许校准。

    PHP 里还有第三个条件 —— ``IsExtendedMarket()``：**只在常规时段
    9:30-16:00 校准**。用例里的时刻都取盘中（``11:44``），
    盘前盘后的拒绝由 :class:`SessionGateTest` 单独盯。
    """

    def test_aligned(self):
        self.assertTrue(check_adjust_time(
            "2026-10-07", "11:44:39", "2026-10-07", "11:44:00"))

    def test_seconds_are_ignored(self):
        """PHP 用 ``GetHM()`` 截到分，秒级差异不算不一致。"""
        self.assertTrue(check_adjust_time(
            "2026-10-07", "11:44:01", "2026-10-07", "11:44:59"))

    def test_different_date_rejected(self):
        self.assertFalse(check_adjust_time(
            "2026-10-07", "11:44:39", "2026-10-06", "11:44:39"))

    def test_different_hour_minute_rejected(self):
        self.assertFalse(check_adjust_time(
            "2026-10-07", "11:44:39", "2026-10-07", "11:45:39"))

    def test_missing_values_rejected(self):
        self.assertFalse(check_adjust_time(None, "11:44:39", "2026-10-07", "11:44:39"))
        self.assertFalse(check_adjust_time("2026-10-07", None, "2026-10-07", "11:44:39"))
        self.assertFalse(check_adjust_time("2026-10-07", "11:44:39", "2026-10-07", None))


class SessionGateTest(unittest.TestCase):
    """``IsExtendedMarket()``：盘前盘后不允许校准。

    PHP ``php/stock/stockref.php``：

    ```php
    $iVal = $this->GetHourMinute();
    if ($iVal < 930 || $iVal > 1600)    return true;   // 延长时段
    ```

    为什么必须有这条：``期货价 / 指数`` 只有在两个市场**同时开着常规盘**的
    那一瞬间才是干净的比值。盘前的期货价含隔夜跳空、指数却还停在昨收，
    算出来的比值会把跳空误当成基差。
    """

    def test_regular_session_allowed(self):
        self.assertTrue(check_adjust_time(
            "2026-10-14", "13:06:53", "2026-10-14", "13:06:00"))

    def test_pre_market_rejected(self):
        """纽约 03:42（盘前）：演示库里 hf_NQ 快照正是这个时刻。"""
        self.assertFalse(check_adjust_time(
            "2026-10-14", "03:42:07", "2026-10-14", "03:42:07"))

    def test_after_close_rejected(self):
        """纽约 17:16（收盘后）：^NDX 快照常常停在这里。"""
        self.assertFalse(check_adjust_time(
            "2026-10-13", "17:16:00", "2026-10-13", "17:16:00"))

    def test_boundaries_are_inclusive(self):
        self.assertTrue(check_adjust_time(
            "2026-10-14", "09:30:00", "2026-10-14", "09:30:59"))
        self.assertTrue(check_adjust_time(
            "2026-10-14", "16:00:00", "2026-10-14", "16:00:59"))


class TimeZoneAlignmentTest(unittest.TestCase):
    """时区不同时先换算到指数的时区再比（PHP 的 ``ConvertTick``）。"""

    def test_same_time_zone_compares_directly(self):
        self.assertTrue(check_adjust_time(
            "2026-10-14", "13:06:53", "2026-10-14", "13:06:00",
            "America/New_York", "America/New_York"))

    def test_shanghai_futures_align_after_conversion(self):
        """期货那侧还是上海时间时，换算过来能对上（先按上海解读再换纽约）。"""
        # 上海 2026-10-15 01:06 -> 纽约 2026-10-14 13:06
        self.assertTrue(check_adjust_time(
            "2026-10-15", "01:06:53", "2026-10-14", "13:06:00",
            TIME_ZONE_SHANGHAI, "America/New_York"))

    def test_without_conversion_the_same_pair_misses(self):
        """不给时区参数就当同时区比 —— 差一天，必然不等。"""
        self.assertFalse(check_adjust_time(
            "2026-10-15", "01:06:53", "2026-10-14", "13:06:00"))


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []

    def execute(self, sql, params=()):
        self.connection.queries.append((sql, params))
        self.rows = self.connection.dispatch(sql, params)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeConnection:
    """最小假连接：支持 futurespair / stock 查询与 calibrationhistory 读写。"""

    def __init__(self, stock=None, futurespair=None, calibration=None):
        self.queries = []
        self.stock = stock or {"^NDX": 6, "hf_NQ": 13, "QQQ": 4, "SPY": 5, "^GSPC": 7, "hf_ES": 14}
        self.by_id = dict((v, k) for k, v in self.stock.items())
        self.futurespair = futurespair if futurespair is not None else {6: 13, 7: 14}
        self.calibration = [dict(r) for r in (calibration or [])]
        self.commits = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def dispatch(self, sql, params):
        # **先把反引号剔掉**：真实 SQL 是 `FROM \`calibrationhistory\``，
        # 直接 `"FROM CALIBRATIONHISTORY" in upper` 永远匹配不上 ——
        # 这个坑让"第二次校准取平均"的用例误判成插入新行（实测踩过）。
        upper = sql.strip().upper().replace("`", "")
        if "FROM FUTURESPAIR" in upper:
            index_id = params[0]
            future_id = self.futurespair.get(index_id)
            return [{"stock_id": future_id}] if future_id is not None else []
        if "FROM STOCK WHERE ID" in upper:
            symbol = self.by_id.get(params[0])
            return [{"symbol": symbol}] if symbol else []
        if "FROM STOCK WHERE SYMBOL" in upper:
            stock_id = self.stock.get(params[0])
            return [{"id": stock_id}] if stock_id is not None else []
        if "INSERT INTO CALIBRATIONHISTORY" in upper:
            self.calibration.append({
                "id": len(self.calibration) + 1, "stock_id": params[0],
                "date": params[1], "close": params[2], "time": params[3], "num": 1,
            })
            return []
        if "UPDATE CALIBRATIONHISTORY" in upper:
            for row in self.calibration:
                if row["id"] == params[2]:
                    row["close"] = params[0]
                    row["num"] = params[1]
            return []
        if "FROM CALIBRATIONHISTORY" in upper:
            rows = [r for r in self.calibration if str(r["stock_id"]) == str(params[0])]
            # 带日期条件时按日期过滤（不要靠匹配 SQL 文本，太脆）
            if len(params) > 1:
                rows = [r for r in rows if str(r["date"]) == str(params[1])]
            return rows[:1]
        return []

class FakeSource:
    """假数据源：给校准器提供 id / 校准值 / 快照价。"""

    def __init__(self, stock_id_map, calibrations, quotes):
        self._ids = stock_id_map
        self._calibrations = calibrations
        self._quotes = quotes
        self.connection = FakeConnection(stock=stock_id_map)

    def get_stock_id(self, symbol):
        return self._ids.get(symbol)

    def get_calibration_now(self, stock_id):
        from core.stockdata import DailyRecord

        value = self._calibrations.get(stock_id)
        return DailyRecord(date="2026-10-07", close=value) if value else None

    def get_stored_quote(self, symbol):
        return self._quotes.get(symbol)

    def get_latest_history_close(self, stock_id):
        return None


class FuturesPairStoreTest(unittest.TestCase):
    def test_get_future_symbol(self):
        store = FuturesPairStore(FakeConnection())
        self.assertEqual(store.get_future_id(6), 13)
        self.assertEqual(store.get_future_symbol(6), "hf_NQ")
        self.assertIsNone(store.get_future_symbol(999))
        self.assertIsNone(store.get_future_id(None))


class FuturesCalibratorTest(unittest.TestCase):
    def _calibrator(self):
        source = FakeSource(
            stock_id_map={"^NDX": 6, "hf_NQ": 13, "QQQ": 4},
            calibrations={4: 0.008945},
            quotes={"QQQ": FuturesQuote("QQQ", "gb_qqq", price="756.7999", date="2026-10-07")},
        )
        return FuturesCalibrator(source, connection=source.connection)

    def test_future_symbol_from_table(self):
        self.assertEqual(self._calibrator().future_symbol_of("^NDX"), "hf_NQ")

    def test_future_symbol_falls_back_to_builtin_map(self):
        """表里查不到时用内置映射 —— 避免少一行配置就整条静默失效。"""
        source = FakeSource(stock_id_map={"^NDX": 6}, calibrations={}, quotes={})
        calibrator = FuturesCalibrator(source, connection=source.connection)
        self.assertEqual(calibrator.future_symbol_of("^NDX"), FALLBACK_FUTURES["^NDX"])

    def test_calc_factor_matches_qdii_formula(self):
        # 校准值 = 期货价 × 汇率 / 指数价
        self.assertAlmostEqual(self._calibrator().calc_factor(31365.0, 31365.0, 1.0), 1.0, places=9)
        self.assertAlmostEqual(self._calibrator().calc_factor(200.0, 100.0, 1.0), 2.0, places=9)

    def test_calc_factor_rejects_bad_inputs(self):
        calibrator = self._calibrator()
        self.assertIsNone(calibrator.calc_factor(None, 100.0, 1.0))
        self.assertIsNone(calibrator.calc_factor(100.0, None, 1.0))
        self.assertIsNone(calibrator.calc_factor(100.0, "abc", 1.0))
        self.assertIsNone(calibrator.calc_factor(100.0, 0.0, 1.0))

    def test_implied_index_price(self):
        """``指数点位 = ETF价 / ETF校准值``（本项目实测出的恒等式）。"""
        implied = self._calibrator().implied_index_price("QQQ")
        self.assertAlmostEqual(implied, 756.7999 / 0.008945, places=4)

    def test_calibrate_requires_aligned_time(self):
        calibrator = self._calibrator()
        future = FuturesQuote("hf_NQ", "hf_NQ", price="31365.0", date="2026-10-07", time="23:44:00")
        index = FuturesQuote("^NDX", "^NDX", price="31365.0", date="2026-10-06", time="23:44:00")
        # 日期不同 -> 不校准
        self.assertIsNone(calibrator.calibrate("^NDX", future, index, 1.0))

    def test_calibrate_writes_on_alignment(self):
        calibrator = self._calibrator()
        future = FuturesQuote("hf_NQ", "hf_NQ", price="200.0", date="2026-10-07", time="11:44:00")
        index = FuturesQuote("^NDX", "^NDX", price="100.0", date="2026-10-07", time="11:44:30")
        factor = calibrator.calibrate("^NDX", future, index, 1.0, apply=True)

        self.assertAlmostEqual(factor, 2.0, places=9)
        rows = calibrator.connection.calibration
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stock_id"], 6)
        self.assertAlmostEqual(rows[0]["close"], 2.0, places=9)

    def test_calibrate_averages_on_second_write(self):
        """同一天第二次校准取**平均**（对应 PHP ``WriteDailyAverage()``）。"""
        calibrator = self._calibrator()
        first = FuturesQuote("hf_NQ", "hf_NQ", price="200.0", date="2026-10-07", time="11:44:00")
        second = FuturesQuote("hf_NQ", "hf_NQ", price="400.0", date="2026-10-07", time="11:44:00")
        index = FuturesQuote("^NDX", "^NDX", price="100.0", date="2026-10-07", time="11:44:00")

        calibrator.calibrate("^NDX", first, index, 1.0, apply=True)
        calibrator.calibrate("^NDX", second, index, 1.0, apply=True)

        row = calibrator.connection.calibration[0]
        # (1×2.0 + 4.0) / 2 = 3.0
        self.assertAlmostEqual(row["close"], 3.0, places=9)
        self.assertEqual(row["num"], 2)

    def test_calibrate_from_proxy_uses_middle_etf(self):
        """用中间层 ETF 反推指数 —— 不需要与指数时刻对齐。"""
        calibrator = self._calibrator()
        future = FuturesQuote("hf_NQ", "hf_NQ", price="31365.0", date="2026-10-07", time="11:44:00")
        factor = calibrator.calibrate_from_proxy("^NDX", future, apply=False)

        implied = 756.7999 / 0.008945
        self.assertAlmostEqual(factor, 31365.0 / implied, places=6)

    def test_calibrate_from_proxy_rejects_date_mismatch(self):
        calibrator = self._calibrator()
        future = FuturesQuote("hf_NQ", "hf_NQ", price="31365.0", date="2026-10-06", time="23:44:00")
        self.assertIsNone(calibrator.calibrate_from_proxy("^NDX", future, apply=False))

    def test_proxy_symbol_falls_back(self):
        """``fundpair`` 查不到时用内置映射（``^NDX`` -> ``QQQ``）。"""
        source = FakeSource(stock_id_map={"^NDX": 6}, calibrations={}, quotes={})
        calibrator = FuturesCalibrator(source, connection=source.connection)
        self.assertEqual(calibrator.proxy_symbol_of("^NDX"), FALLBACK_PROXIES["^NDX"])

    def test_fallback_maps_are_complete(self):
        """代理 ETF 的键必须是期货配对的**子集**，免得配了代理却没有期货。

        反向不成立：``^HSI`` / ``znb_NKY`` 有真实指数快照，不需要
        "ETF 反推点位"这条兜底路径（``FALLBACK_PROXIES`` 只有美股两对）。
        """
        self.assertTrue(set(FALLBACK_PROXIES) <= set(FALLBACK_FUTURES))
        self.assertEqual(FALLBACK_FUTURES["^NDX"], "hf_NQ")
        self.assertEqual(FALLBACK_FUTURES["^GSPC"], "hf_ES")
        self.assertEqual(FALLBACK_FUTURES["^HSI"], "hf_HSI")
        self.assertEqual(FALLBACK_FUTURES["znb_NKY"], "hf_NK")
        self.assertEqual(FALLBACK_PROXIES["^NDX"], "QQQ")
        self.assertEqual(FALLBACK_PROXIES["^GSPC"], "SPY")

    def test_hk_jp_futures_have_own_time_zone(self):
        """``hf_HSI`` / ``hf_NK`` 不能按 ``hf_`` 前缀一律算纽约。

        照抄 PHP ``IsSinaFutureUS()`` 会让新浪给的上海时间戳被换成前一天
        （上海 09:59 = 纽约前一日 20:59），日期直接错掉，校准永远对不齐。
        两者都刻意与配对的指数同区，``check_adjust_time`` 才能直接比 HH:MM。
        """
        from core.stocksymbol import StockSymbol

        self.assertEqual(StockSymbol("hf_HSI").get_time_zone(),
                         StockSymbol("^HSI").get_time_zone())
        self.assertEqual(StockSymbol("hf_NK").get_time_zone(),
                         StockSymbol("znb_NKY").get_time_zone())
        # 美股期货不受影响
        self.assertEqual(StockSymbol("hf_NQ").get_time_zone(), "America/New_York")


class RealtimeSourceValueTest(unittest.TestCase):
    """``realtime_source_value()`` 的**量纲换算** —— 结果停在**指数点位空间**。

    对应 PHP ``_QdiiReference::EstRealtimeNetValue()``
    （``qdiiref.php:172-175``）→ ``FundPairReference::EstFromPair()``
    （``fundpairref.php:86-91``）：

    ```text
    指数点位 = 期货价 / cal_futures        （cal_futures = 期货 / 指数 ≈ 1.007）
    ```

    到这里就停：``est_cal``（``fundestcalc._est_quote_ref``）在**指数空间**
    （^NDX 的净值 / 收盘价），``est / est_cal`` 才是"标的相对变化"。
    PHP 外层 ``GetQdiiValue()`` 再 ``× 汇率 / 基金校准值`` 换成净值。

    ## 这里修过一次 10 倍的错（2026-10-20）

    旧实现换算完指数点位又除了一次 ``cal_m``（指数 / ETF 净值 ≈ 41.11），
    把值压到 ETF 价空间（7700 / 10.03 = 767），``est / est_cal`` 退化成
    "ETF 价 / 指数点位" ≈ 0.0998 —— 实时 EST 从 3.12 掉到 0.45、
    溢价 620%（对照 sz161125cn.php 实测）。
    ``test_guard_rejects_extra_cal_m_division`` 钉住尺子能抓住这个变体。
    """

    #: 一组自洽的数：^NDX 30807.93 / QQQ净值 749.3773 = 41.111（= cal_m，生产口径）
    INDEX_PRICE = 30807.93
    FUTURE_PRICE = 31369.43
    CAL_FUTURES = FUTURE_PRICE / INDEX_PRICE      # 期货 / 指数
    INDEX_QUOTE = 30807.93                         # ^NDX 实时快照价（量级保险的尺子）

    class Source:
        """最小数据源：``^NDX``(6) 有期货校准值与实时快照，``QQQ``(4) 占位。

        三个常量从**外层测试类**取 —— 嵌套类的类体**看不到**外层类的命名空间，
        直接写 ``CAL_FUTURES`` 只能拿到自己类体里那份，曾经这里就残留了一份
        旧口径的副本（0.370904 / 0.008945），于是"常量改了、测试用的还是旧的"，
        表现得像是换算写错了。现在统一走 ``RealtimeSourceValueTest`` 全名。

        子类想改口径覆盖同名类属性即可，``None`` 表示用默认。
        """

        CAL_FUTURES = None
        INDEX_QUOTE = None

        def __init__(self):
            outer = RealtimeSourceValueTest
            self._ids = {"^NDX": 6, "hf_NQ": 13, "QQQ": 4}
            self._calibrations = {
                6: self.CAL_FUTURES or outer.CAL_FUTURES,
            }
            self._quote = self.INDEX_QUOTE or outer.INDEX_QUOTE
            self.connection = FakeConnection(stock=self._ids)

        def get_stock_id(self, symbol):
            return self._ids.get(symbol)

        def get_calibration_now(self, stock_id):
            from core.stockdata import DailyRecord

            value = self._calibrations.get(stock_id)
            return DailyRecord(date="2026-10-07", close=value) if value else None

        def get_stored_quote(self, symbol):
            if symbol == "^NDX":
                return FuturesQuote("^NDX", "gb_ndx", price=str(self._quote), date="2026-10-07")
            return None

        def get_latest_history_close(self, stock_id):
            return None

    def _calibrator(self):
        source = self.Source()
        return FuturesCalibrator(source, connection=source.connection)

    def test_value_lands_in_the_index_space(self):
        """换算结果应当与"指数实时快照价"同量级（而不是 ETF 价的七百多）。"""
        calibrator = self._calibrator()
        quote = FuturesQuote("hf_NQ", "hf_NQ", price=str(self.FUTURE_PRICE),
                             date="2026-10-07", time="11:48:00")

        value = calibrator.realtime_source_value("^NDX", quote)

        self.assertIsNotNone(value)
        # 换算后应当非常接近 ^NDX 的快照价（指数点位空间）
        self.assertAlmostEqual(value, self.INDEX_QUOTE, delta=1.0)
        # 量级检查：必须是指数点位的三万，不能是 ETF 价的七百多
        self.assertGreater(value, 10000)

    def test_matches_formula(self):
        calibrator = self._calibrator()
        quote = FuturesQuote("hf_NQ", "hf_NQ", price=str(self.FUTURE_PRICE))
        expected = self.FUTURE_PRICE / self.CAL_FUTURES
        self.assertAlmostEqual(
            calibrator.realtime_source_value("^NDX", quote), expected, places=9)
        self.assertAlmostEqual(expected, self.INDEX_PRICE, places=6)

    def test_guard_rejects_extra_cal_m_division(self):
        """量级对不上时宁可返回 None（让调用方回落中间层快照）。

        盯的是 2026-10-20 那次 10 倍事故：换算完指数点位**又除了 cal_m**
        （≈41.11），值掉到 ETF 价空间（767 对指数 7690，偏离 90%）。
        这里把 ^NDX 的期货校准值污染成 ``cal_futures × cal_m``
        （等价位地把值压进 ETF 空间），尺子必须拒收。
        """
        class PollutedSource(self.Source):
            CAL_MIDDLE = 30807.93 / 749.3773                  # 指数 / ETF 净值
            CAL_FUTURES = RealtimeSourceValueTest.CAL_FUTURES * CAL_MIDDLE

        source = PollutedSource()
        calibrator = FuturesCalibrator(source, connection=source.connection)
        quote = FuturesQuote("hf_NQ", "hf_NQ", price=str(self.FUTURE_PRICE))
        self.assertIsNone(calibrator.realtime_source_value("^NDX", quote))

    def test_without_reference_price_no_guard_but_value_returned(self):
        """拿不到指数参考价时没有尺子 —— 放行，不把"查不到"变成"算不出来"。"""
        class NoQuote(self.Source):
            def get_stored_quote(self, symbol):
                return None

        source = NoQuote()
        calibrator = FuturesCalibrator(source, connection=source.connection)
        quote = FuturesQuote("hf_NQ", "hf_NQ", price=str(self.FUTURE_PRICE))
        self.assertIsNotNone(calibrator.realtime_source_value("^NDX", quote))

    def test_returns_none_without_futures_calibration(self):
        class NoCalibration(self.Source):
            def get_calibration_now(self, stock_id):
                from core.stockdata import DailyRecord

                return None

        source = NoCalibration()
        calibrator = FuturesCalibrator(source, connection=source.connection)
        quote = FuturesQuote("hf_NQ", "hf_NQ", price=str(self.FUTURE_PRICE))
        self.assertIsNone(calibrator.realtime_source_value("^NDX", quote))

    def test_returns_none_without_quote(self):
        calibrator = self._calibrator()
        self.assertIsNone(calibrator.realtime_source_value("^NDX", None))

    def test_returns_none_for_bad_price(self):
        calibrator = self._calibrator()
        quote = FuturesQuote("hf_NQ", "hf_NQ", price="abc")
        self.assertIsNone(calibrator.realtime_source_value("^NDX", quote))


class SilverTransformTest(unittest.TestCase):
    """``nf_AG0 ← hf_SI``：**要单位换算**的那一族。

    PHP ``woody/res/php/_chinafuture.php:6-13``（只挂在 ``SZ161226`` 上）::

        return 1000.0 * $realtime_ref->GetVal() * $cnh_ref->GetVal() / 31.1035;

    与 ``^NDX ← hf_NQ`` 的区别：那条两端同量纲，换算只有 ``÷ cal``；
    这条是**美元/盎司 → 元/千克**，汇率与单位塞不进 ``cal``
    （``cal`` 是校准历史里的死数，汇率一动它就得重算）。

    下面这组数取自生产端 2026-11-05 的实测页（``sz161226cn.php``）：
    ``SI 53.29 / USDCNH 6.6677 / AG0 12898``，页面实时 EST 1.285。
    """

    FUTURE_PRICE = 53.29
    FX = 6.6677
    AG_PRICE = 12898.0
    EXPECTED = FUTURE_PRICE * FX * 1000.0 / 31.1035      # ≈ 11423.9 元/千克

    #: ``_calibrator()`` 的"没传"标记（``None`` 在这里是**有意义的输入**：
    #: "这条取不到"，不能兼作默认值）
    _UNSET = object()

    class Source:
        """只提供三样东西：符号 id、两条快照（汇率 / AG0）、没有净值。

        ``fx`` / ``ag`` 传 ``None`` 表示"这一条取不到"，用来验网关。
        """

        def __init__(self, fx=None, ag=None):
            self._ids = {"nf_AG0": 287, "hf_SI": 301, "fx_susdcnh": 180}
            self.fx = fx
            self.ag = ag

        def get_stock_id(self, symbol):
            return self._ids.get(symbol)

        def get_stored_quote(self, symbol):
            if symbol == "fx_susdcnh" and self.fx is not None:
                return FuturesQuote(symbol, symbol, price=str(self.fx))
            if symbol == "nf_AG0" and self.ag is not None:
                return FuturesQuote(symbol, symbol, price=str(self.ag))
            return None

        def get_latest_net_value(self, stock_id):
            return None

    def _calibrator(self, fx=_UNSET, ag=_UNSET):
        source = self.Source(
            fx=self.FX if fx is self._UNSET else fx,
            ag=self.AG_PRICE if ag is self._UNSET else ag,
        )
        return FuturesCalibrator(source, connection=None)

    def test_future_symbol_does_not_need_the_table(self):
        """``futurespair`` 少一行也要能配到 —— 参数就写在换算表里。"""
        self.assertEqual(self._calibrator().future_symbol_of("nf_AG0"), "hf_SI")
        self.assertEqual(FUTURES_VALUE_TRANSFORMS["nf_AG0"].future_symbol, "hf_SI")

    def test_value_is_converted_to_yuan_per_kg(self):
        """换算结果必须落在**沪银**的量级（一万上下），不是 53 美元。"""
        quote = FuturesQuote("hf_SI", "hf_SI", price=str(self.FUTURE_PRICE))
        value = self._calibrator().realtime_source_value("nf_AG0", quote)

        self.assertIsNotNone(value)
        self.assertAlmostEqual(value, self.EXPECTED, places=6)
        self.assertGreater(value, 10000)

    def test_missing_fx_is_none_not_one(self):
        """缺汇率时**不能**按 1.0 算 —— 漏乘 6.7 倍的值照样能显示出来。

        这里的构造办法是把汇率快照与净值都拿掉：``Source(fx=None)``
        两条取数路径都返回 ``None``。
        """
        quote = FuturesQuote("hf_SI", "hf_SI", price=str(self.FUTURE_PRICE))
        self.assertIsNone(self._calibrator(fx=None).realtime_source_value("nf_AG0", quote))

    def test_scale_guard_still_applies(self):
        """换算完离 AG0 快照价太远（这里 10 倍）就拒收，让调用方回落快照。"""
        quote = FuturesQuote("hf_SI", "hf_SI", price=str(self.FUTURE_PRICE))
        self.assertIsNone(self._calibrator(ag=1289.0).realtime_source_value("nf_AG0", quote))

    def test_label_only_for_transformed_pairs(self):
        """「算法说明」只给需要换算的那一族加算式，别给美股也加上。"""
        calibrator = self._calibrator()
        self.assertIn("31.1035", calibrator.value_transform_label("nf_AG0"))
        self.assertIsNone(calibrator.value_transform_label("^NDX"))


class CalibratablePairsTest(unittest.TestCase):
    """``futurescli`` 只给"两端同量纲"的配对刷新快照、试着校准。

    换算族（``nf_AG0 ← hf_SI``）必须排除，两条理由：

    1. 两端量纲不同（美元/盎司 vs 元/千克），"期货 ÷ 现货"没有意义。
       PHP 的 ``AverageCalibraion()`` 也只对 ``IsSinaFutureUS()`` 触发
       （``fundpairref.php:160``），国内期货那一支走净值日线的 ``DailyCalibration()``。
    2. 刷新快照会抹掉 ``vwap`` / ``orderbook`` —— ``RealtimeQuoteStore.save``
       是整行 UPSERT，不给这两列就写空值，而 ``realtimequote.vwap`` 正是
       商品期货基金「官方 EST」取均价的来源，抹掉后官方 EST 静默退回结算价。
    """

    PAIRS = {"^NDX": "hf_NQ", "nf_AG0": "hf_SI", "GLD": "hf_GC"}

    def _calibratable(self):
        from cli.futurescli import _calibratable_pairs

        return _calibratable_pairs(self.PAIRS)

    def test_transform_pair_is_excluded(self):
        pairs = self._calibratable()
        self.assertNotIn("nf_AG0", pairs)
        self.assertIn("^NDX", pairs, "同量纲的那几对要留下来")
        self.assertIn("GLD", pairs)

    def test_kept_pairs_never_appear_in_the_transform_table(self):
        for index_symbol in self._calibratable():
            self.assertNotIn(index_symbol, FUTURES_VALUE_TRANSFORMS)

    def test_does_not_mutate_the_input(self):
        """``--show`` 还要能列出这一对，别把源字典就地删了。"""
        self._calibratable()
        self.assertIn("nf_AG0", self.PAIRS)


if __name__ == "__main__":
    unittest.main()
