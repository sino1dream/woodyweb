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

from futurescalib import (
    FALLBACK_FUTURES,
    FALLBACK_PROXIES,
    FuturesCalibrator,
    FuturesPairStore,
    build_futures_calibrator,
    check_adjust_time,
)
from futuresquotes import (
    MIN_FIELD_COUNT,
    SinaFuturesClient,
    FuturesQuote,
    get_quote_symbol,
    is_futures_symbol,
    parse_futures_payload,
    parse_futures_text,
)


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
        from stocksymbol import StockSymbol

        self.assertEqual(get_quote_symbol("hf_NQ"), "hf_NQ")
        self.assertEqual(get_quote_symbol("hf_ES"), "hf_ES")
        # 对照：直接调 get_sina_symbol 会得到错的前缀
        self.assertEqual(StockSymbol("hf_NQ").get_sina_symbol(), "gb_hf_nq")

    def test_get_quote_symbol_unchanged_for_normal_symbols(self):
        from stocksymbol import StockSymbol

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
        self.assertEqual(quote.time, "23:44:39")
        self.assertEqual(quote.prev_price, "31483.250")
        self.assertEqual(quote.open, "31510.000")
        self.assertEqual(quote.date, "2026-10-07")
        self.assertEqual(quote.name, "纳斯达克100指数期货")

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
        self.assertEqual(moment, datetime.datetime(2026, 10, 7, 23, 44, 39))

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
    """``CheckAdjustFactorTime()``：日期与 HH:MM 都相同才允许校准。"""

    def test_aligned(self):
        self.assertTrue(check_adjust_time(
            "2026-10-07", "23:44:39", "2026-10-07", "23:44:00"))

    def test_seconds_are_ignored(self):
        """PHP 用 ``GetHM()`` 截到分，秒级差异不算不一致。"""
        self.assertTrue(check_adjust_time(
            "2026-10-07", "23:44:01", "2026-10-07", "23:44:59"))

    def test_different_date_rejected(self):
        self.assertFalse(check_adjust_time(
            "2026-10-07", "23:44:39", "2026-10-06", "23:44:39"))

    def test_different_hour_minute_rejected(self):
        self.assertFalse(check_adjust_time(
            "2026-10-07", "23:44:39", "2026-10-07", "23:45:39"))

    def test_missing_values_rejected(self):
        self.assertFalse(check_adjust_time(None, "23:44:39", "2026-10-07", "23:44:39"))
        self.assertFalse(check_adjust_time("2026-10-07", None, "2026-10-07", "23:44:39"))
        self.assertFalse(check_adjust_time("2026-10-07", "23:44:39", "2026-10-07", None))


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
        from stockdata import DailyRecord

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
        future = FuturesQuote("hf_NQ", "hf_NQ", price="200.0", date="2026-10-07", time="23:44:00")
        index = FuturesQuote("^NDX", "^NDX", price="100.0", date="2026-10-07", time="23:44:30")
        factor = calibrator.calibrate("^NDX", future, index, 1.0, apply=True)

        self.assertAlmostEqual(factor, 2.0, places=9)
        rows = calibrator.connection.calibration
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stock_id"], 6)
        self.assertAlmostEqual(rows[0]["close"], 2.0, places=9)

    def test_calibrate_averages_on_second_write(self):
        """同一天第二次校准取**平均**（对应 PHP ``WriteDailyAverage()``）。"""
        calibrator = self._calibrator()
        first = FuturesQuote("hf_NQ", "hf_NQ", price="200.0", date="2026-10-07", time="23:44:00")
        second = FuturesQuote("hf_NQ", "hf_NQ", price="400.0", date="2026-10-07", time="23:44:00")
        index = FuturesQuote("^NDX", "^NDX", price="100.0", date="2026-10-07", time="23:44:00")

        calibrator.calibrate("^NDX", first, index, 1.0, apply=True)
        calibrator.calibrate("^NDX", second, index, 1.0, apply=True)

        row = calibrator.connection.calibration[0]
        # (1×2.0 + 4.0) / 2 = 3.0
        self.assertAlmostEqual(row["close"], 3.0, places=9)
        self.assertEqual(row["num"], 2)

    def test_calibrate_from_proxy_uses_middle_etf(self):
        """用中间层 ETF 反推指数 —— 不需要与指数时刻对齐。"""
        calibrator = self._calibrator()
        future = FuturesQuote("hf_NQ", "hf_NQ", price="31365.0", date="2026-10-07", time="23:44:00")
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
        """两个兜底表的键必须一致，免得只配了一半。"""
        self.assertEqual(sorted(FALLBACK_FUTURES), sorted(FALLBACK_PROXIES))
        self.assertEqual(FALLBACK_FUTURES["^NDX"], "hf_NQ")
        self.assertEqual(FALLBACK_FUTURES["^GSPC"], "hf_ES")
        self.assertEqual(FALLBACK_PROXIES["^NDX"], "QQQ")
        self.assertEqual(FALLBACK_PROXIES["^GSPC"], "SPY")


class RealtimeSourceValueTest(unittest.TestCase):
    """``realtime_source_value()`` 的**量纲换算**。

    期货校准值 ``cal_futures = 期货 / 指数``，中间层校准值 ``cal_m = ETF / 指数``，
    两者分母都是指数，所以：

    ```text
    ETF价 = 期货价 × cal_m / cal_futures
    ```

    **不换算的后果**：直接把期货价当中间层值用，实时 EST 会大 100 倍
    （实测 487.68 而非 4.59）—— 因为期货价是指数点位（三万多），
    而中间层值在 ``cal_m`` 空间里（七百多）。这个 bug 在本轮真的踩到了。
    """

    #: 2026-10-07 实测值
    FUTURE_PRICE = 31369.43
    CAL_FUTURES = 0.370904
    CAL_MIDDLE = 0.008945
    QQQ_QUOTE = 756.7999

    class Source:
        def __init__(self):
            self._ids = {"^NDX": 6, "hf_NQ": 13, "QQQ": 4}
            self._calibrations = {6: self.CAL_FUTURES, 4: self.CAL_MIDDLE}
            self.connection = FakeConnection(stock=self._ids)

        def get_stock_id(self, symbol):
            return self._ids.get(symbol)

        def get_calibration_now(self, stock_id):
            from stockdata import DailyRecord

            value = self._calibrations.get(stock_id)
            return DailyRecord(date="2026-10-07", close=value) if value else None

        def get_stored_quote(self, symbol):
            if symbol == "QQQ":
                return FuturesQuote("QQQ", "gb_qqq", price=str(self.QQQ_QUOTE), date="2026-10-07")
            return None

        def get_latest_history_close(self, stock_id):
            return None

        CAL_FUTURES = 0.370904
        CAL_MIDDLE = 0.008945
        QQQ_QUOTE = 756.7999

    def _calibrator(self):
        source = self.Source()
        return FuturesCalibrator(source, connection=source.connection)

    def test_value_lands_in_the_middle_layer_space(self):
        """换算结果应当与"中间层快照价"同量级（而不是指数点位的三万）。"""
        calibrator = self._calibrator()
        quote = FuturesQuote("hf_NQ", "hf_NQ", price=str(self.FUTURE_PRICE),
                             date="2026-10-07", time="11:48:00")

        value = calibrator.realtime_source_value("^NDX", quote)

        self.assertIsNotNone(value)
        # 换算后应当非常接近 QQQ 的快照价
        self.assertAlmostEqual(value, self.QQQ_QUOTE, delta=1.0)
        # 量级检查：绝不能是三万这种指数点位
        self.assertLess(value, 10000)

    def test_matches_formula(self):
        calibrator = self._calibrator()
        quote = FuturesQuote("hf_NQ", "hf_NQ", price=str(self.FUTURE_PRICE))
        expected = self.FUTURE_PRICE * self.CAL_MIDDLE / self.CAL_FUTURES
        self.assertAlmostEqual(
            calibrator.realtime_source_value("^NDX", quote), expected, places=9)

    def test_returns_none_without_futures_calibration(self):
        class NoCalibration(self.Source):
            def get_calibration_now(self, stock_id):
                from stockdata import DailyRecord

                # 只有中间层有校准值，指数没有 -> 无法换算
                if stock_id == 4:
                    return DailyRecord(date="2026-10-07", close=self.CAL_MIDDLE)
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


if __name__ == "__main__":
    unittest.main()
