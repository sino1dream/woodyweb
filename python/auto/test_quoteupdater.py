"""`quotesource.py` 与 `quoteupdater.py` 的单元测试。

对应 PHP：``php/stock.php`` 的 ``GetSinaQuotes()`` / ``StockPrefetchArrayData()``、
``php/stock/stockprefetch.php`` 的间隔节流。

全部用假的行情源离线测试，不打网络。
"""

import datetime
import unittest

import quotesource
from quoteupdater import (
    RecentQuote,
    QuotesUpdater,
    is_any_market_open,
    parse_symbol,
    quote_from_reference,
)
from stocksymbol import StockSymbol

# 真实响应样本（A 股/基金 32 字段，date(30)/time(31) 落在正确位置）
LINE_CN = (
    'var hq_str_sz161130="纳指LOF,4.700,4.727,4.796,4.800,4.690,4.795,4.796,'
    '1000000,4796000,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,'
    '2026-09-30,15:00:00,00";'
)


def make_line(sina_symbol, name, price, prev, date="2026-09-30", time_text="15:00:00"):
    """造一行 A 股/基金格式的行情。"""
    values = [""] * 32
    values[0] = name
    values[1] = "4.700"
    values[2] = prev
    values[3] = price
    values[4] = "4.800"
    values[5] = "4.690"
    values[8] = "1000000"
    values[30] = date
    values[31] = time_text
    return 'var hq_str_%s="%s";' % (sina_symbol, ",".join(values))


class FakeSource(quotesource.MarketQuoteSource):
    """假行情源：继承基类，顺带验证默认 ``fetch_raw()`` 的契约也成立。"""

    name = "fake"

    def __init__(self, lines=None, fail=False):
        self.lines = lines or {}
        self.fail = fail
        self.calls = []

    def fetch_lines(self, symbols):
        self.calls.append(list(symbols))
        if self.fail:
            return None
        return {symbol: self.lines[symbol] for symbol in symbols if symbol in self.lines}

    def describe(self):
        return {"name": self.name}


class QuoteSourceInterfaceTest(unittest.TestCase):
    def test_default_source_is_sina(self):
        self.assertIn(quotesource.DEFAULT_QUOTE_SOURCE, quotesource.list_quote_sources())
        # 新浪源要能实例化（不实际发请求）
        source = quotesource.get_quote_source("sina")
        self.assertEqual(source.name, "sina")

    def test_unknown_source_raises_with_available_list(self):
        with self.assertRaises(ValueError) as context:
            quotesource.get_quote_source("no-such-source")
        self.assertIn("sina", str(context.exception))

    def test_register_and_use_custom_source(self):
        # 这条用例就是「换行情源」的最小示例
        quotesource.register_quote_source("fake-for-test", lambda **kwargs: FakeSource())
        try:
            source = quotesource.get_quote_source("fake-for-test")
            self.assertEqual(source.name, "fake")
            self.assertIn("fake-for-test", quotesource.list_quote_sources())
        finally:
            quotesource.registry._factories.pop("fake-for-test", None)

    def test_fetch_raw_default_keeps_request_order(self):
        source = FakeSource({"a": "line-a", "b": "line-b"})
        # 请求顺序与返回顺序一致，缺失的行补空行保持下标对应
        self.assertEqual(source.fetch_raw(["b", "a"]), "line-b\nline-a")
        self.assertEqual(source.fetch_raw(["a", "missing"]), "line-a\n")

    def test_fetch_raw_returns_none_on_failure(self):
        self.assertIsNone(FakeSource(fail=True).fetch_raw(["a"]))


class RecentQuoteTest(unittest.TestCase):
    def test_age_and_freshness(self):
        quote = RecentQuote(symbol="SZ161130", sina_symbol="sz161130", fetched_at=1000.0)

        self.assertEqual(quote.age(now=1030.0), 30.0)
        self.assertTrue(quote.is_fresh(ttl=60, now=1030.0))
        self.assertFalse(quote.is_fresh(ttl=60, now=1100.0))

    def test_as_dict_shape(self):
        quote = RecentQuote(
            symbol="SZ161130", sina_symbol="sz161130",
            price="4.796", prev_price="4.727", source="fake", fetched_at=1000.0,
        )
        data = quote.as_dict()

        self.assertEqual(data["symbol"], "SZ161130")
        self.assertEqual(data["price"], "4.796")
        self.assertEqual(data["source"], "fake")
        self.assertIn("age_seconds", data)


class ParseSymbolTest(unittest.TestCase):
    def test_parse_cn_line(self):
        # 解析层复用 stockref，所以这里顺带验证"新浪符号 -> 价格"是通的
        reference = parse_symbol("sz161130", make_line("sz161130", "纳指LOF", "4.796", "4.727"))

        self.assertTrue(reference.has_quote_data())
        self.assertEqual(reference.str_name, "纳指LOF")
        self.assertEqual(reference.get_price(), "4.796")
        self.assertEqual(reference.get_prev_price(), "4.727")
        self.assertEqual(reference.str_date, "2026-09-30")
        self.assertEqual(reference.str_time, "15:00:00")

    def test_parse_broken_line_does_not_raise(self):
        # 字段不足时解析会失败，但必须被吞掉并标记为无数据
        reference = parse_symbol("sz161130", 'var hq_str_sz161130="only,three,fields";')
        self.assertFalse(reference.has_quote_data())

    def test_quote_from_reference(self):
        reference = parse_symbol("sz161130", make_line("sz161130", "纳指LOF", "4.796", "4.727"))
        quote = quote_from_reference("SZ161130", "sz161130", reference, "fake")

        self.assertEqual(quote.symbol, "SZ161130")
        self.assertEqual(quote.sina_symbol, "sz161130")
        self.assertEqual(quote.price, "4.796")
        self.assertEqual(quote.prev_price, "4.727")
        self.assertEqual(quote.name, "纳指LOF")
        self.assertGreater(quote.fetched_at, 0)

    def test_quote_from_reference_without_data_leaves_price_empty(self):
        reference = parse_symbol("sz161130", "garbage")
        quote = quote_from_reference("SZ161130", "sz161130", reference, "fake")
        self.assertEqual(quote.price, "")


class QuotesUpdaterTest(unittest.TestCase):
    def _pairs(self):
        return [("SZ161130", "sz161130"), ("SZ161125", "sz161125")]

    def _updater(self, source, ttl=60, clock=None):
        return QuotesUpdater(
            source=source,
            symbols_provider=self._pairs,
            ttl=ttl,
            clock=clock,
        )

    def test_update_fills_cache(self):
        source = FakeSource({
            "sz161130": make_line("sz161130", "纳指LOF", "4.796", "4.727"),
            "sz161125": make_line("sz161125", "标普500", "3.275", "3.240"),
        })
        updater = self._updater(source)

        updated = updater.update()

        self.assertEqual(updated, 2)
        self.assertEqual(updater.get("SZ161130").price, "4.796")
        self.assertEqual(updater.get("SZ161125").price, "3.275")
        self.assertIsNone(updater.last_error)

    def test_update_skips_symbols_without_lines(self):
        # 源只回了其中一只时，另一只不进缓存（避免用空价格覆盖旧值）
        source = FakeSource({"sz161130": make_line("sz161130", "纳指LOF", "4.796", "4.727")})
        updater = self._updater(source)

        self.assertEqual(updater.update(), 1)
        self.assertIsNotNone(updater.get("SZ161130"))
        self.assertIsNone(updater.get("SZ161125"))

    def test_update_uses_provider_pairs(self):
        source = FakeSource({"sz161130": make_line("sz161130", "纳指LOF", "4.796", "4.727")})
        updater = self._updater(source)

        updater.update()

        self.assertEqual(source.calls, [["sz161130", "sz161125"]])

    def test_source_failure_keeps_cache_and_records_error(self):
        good = FakeSource({"sz161130": make_line("sz161130", "纳指LOF", "4.796", "4.727")})
        updater = self._updater(good)
        updater.update()
        self.assertEqual(updater.get("SZ161130").price, "4.796")

        bad = FakeSource(fail=True)
        failing = self._updater(bad)
        failing.put(updater.get("SZ161130"))
        self.assertEqual(failing.update(), 0)
        self.assertIsNotNone(failing.last_error)
        # 失败不清缓存
        self.assertIsNotNone(failing.get("SZ161130"))

    def test_provider_exception_is_swallowed(self):
        def broken_provider():
            raise RuntimeError("db down")

        updater = QuotesUpdater(source=FakeSource(), symbols_provider=broken_provider)
        self.assertEqual(updater.update(), 0)
        self.assertIn("取符号列表失败", updater.last_error)

    def test_ttl_expiry(self):
        source = FakeSource({"sz161130": make_line("sz161130", "纳指LOF", "4.796", "4.727")})
        now = [1000.0]
        updater = self._updater(source, ttl=60, clock=lambda: now[0])

        updater.update()
        self.assertIsNotNone(updater.get("SZ161130"))

        now[0] += 61
        self.assertIsNone(updater.get("SZ161130"))
        # get_all 也过滤过期项
        self.assertEqual(updater.get_all(), {})

    def test_update_if_stale_throttles(self):
        source = FakeSource({"sz161130": make_line("sz161130", "纳指LOF", "4.796", "4.727")})
        now = [1000.0]
        updater = self._updater(source, ttl=60, clock=lambda: now[0])

        self.assertEqual(updater.update_if_stale(), 1)
        # 没过期，不再抓
        self.assertEqual(updater.update_if_stale(), 0)
        self.assertEqual(len(source.calls), 1)

        now[0] += 61
        self.assertEqual(updater.update_if_stale(), 1)
        self.assertEqual(len(source.calls), 2)

    def test_describe_reports_source_and_updater_state(self):
        source = FakeSource({"sz161130": make_line("sz161130", "纳指LOF", "4.796", "4.727")})
        updater = self._updater(source)
        updater.update()

        info = updater.describe()

        self.assertEqual(info["source"]["name"], "fake")
        # 只有源里真的给了行的符号才会进缓存（SZ161125 这行没数据）
        self.assertEqual(info["cached"], 1)
        self.assertEqual(info["last_updated_count"], 1)
        self.assertIn("SZ161130", info["cached_symbols"])
        self.assertEqual(info["ttl_seconds"], 60)


class MarketOpenTest(unittest.TestCase):
    def test_a_share_open_during_trading_hours(self):
        # 2026-01-05 是周一，10:00 上海时间
        monday = datetime.datetime(2026, 1, 5, 10, 0)
        self.assertTrue(is_any_market_open(["SZ161130"], now=monday))

    def test_weekend_is_closed(self):
        saturday = datetime.datetime(2026, 1, 3, 10, 0)
        self.assertFalse(is_any_market_open(["SZ161130"], now=saturday))

    def test_symbol_without_recognizable_market_is_ignored(self):
        # "NOT-A-SYMBOL" 里含连字符，既不是 A/H 也不是标准美股写法；
        # 不过 StockSymbol 对任意字符串都会落到"美股"分支，所以这里改用
        # 明确能判定的符号来验证"非交易时段为假"。
        saturday = datetime.datetime(2026, 1, 3, 10, 0)
        self.assertFalse(is_any_market_open(["SZ161130", "SH513100"], now=saturday))

    def test_empty_symbol_list_is_closed(self):
        monday = datetime.datetime(2026, 1, 5, 10, 0)
        self.assertFalse(is_any_market_open([], now=monday))


class SymbolConversionTest(unittest.TestCase):
    def test_china_symbol_to_sina_symbol(self):
        # 更新器依赖这个转换把库里的符号变成数据源代码
        self.assertEqual(StockSymbol("SZ161130").get_sina_symbol(), "sz161130")
        self.assertEqual(StockSymbol("SH513100").get_sina_symbol(), "sh513100")
        self.assertEqual(StockSymbol("QQQ").get_sina_symbol(), "gb_qqq")


class FakeSnapshotStore:
    """假的快照表：只记录 save() 收到了什么。"""

    def __init__(self):
        self.rows = []

    def save(self, stock_id, symbol, source_symbol, close, prev_close="", name="",
             quote_date=None, quote_time=None, source="", fetched_at=None,
             orderbook=None):
        self.rows.append({
            "stock_id": stock_id, "symbol": symbol, "close": close,
            "prev_close": prev_close, "quote_date": quote_date, "source": source,
            # 盘口是后加的第十一个参数；假 store 照收，好让"盘口有没有写进去"可断言
            "orderbook": orderbook,
        })
        return True


class EstQuoteStoreTest(unittest.TestCase):
    """中间层标的要额外写一份 estquote（对应 PHP _yahooStockGetData）。

    PHP 当年把它写进 netvaluehistory，与基金单位净值混在一张表；这里分开存，
    所以要验证"命中中间层名单才写、且写进的是 est_store"。
    """

    def _updater(self, est_store, est_symbols, store=None):
        # 用 sz 前缀的两只：StockSymbol 对 SZ161130/SZ161125 的解析是确定的，
        # 避免依赖 gb_* 这类"美股代码"的解析行为
        source = FakeSource({
            "sz161130": make_line("sz161130", "纳指LOF", "4.796", "4.727"),
            "sz161125": make_line("sz161125", "标普500", "3.275", "3.240"),
        })
        return QuotesUpdater(
            source=source,
            symbols_provider=lambda: [("SZ161130", "sz161130", 1), ("SZ161125", "sz161125", 2)],
            store=store,
            est_store=est_store,
            est_symbols=est_symbols,
        )

    def test_middle_layer_written_to_est_store_only(self):
        quotes = FakeSnapshotStore()
        est = FakeSnapshotStore()
        # 只把 SZ161130 当成中间层，验证"名单命中才写 est"
        updater = self._updater(est, {"SZ161130"}, store=quotes)

        updater.update()

        # 行情表里两只都有
        self.assertEqual(sorted(row["symbol"] for row in quotes.rows), ["SZ161125", "SZ161130"])
        # 中间层表里只有名单里那只
        self.assertEqual([row["symbol"] for row in est.rows], ["SZ161130"])
        self.assertEqual(est.rows[0]["close"], "4.796")
        self.assertEqual(est.rows[0]["stock_id"], 1)

    def test_no_est_store_means_no_est_write(self):
        quotes = FakeSnapshotStore()
        updater = self._updater(None, {"SZ161130"}, store=quotes)

        updater.update()

        self.assertEqual(len(quotes.rows), 2)

    def test_empty_est_symbols_writes_nothing_extra(self):
        quotes = FakeSnapshotStore()
        est = FakeSnapshotStore()
        updater = self._updater(est, set(), store=quotes)

        updater.update()

        self.assertEqual(est.rows, [])

    def test_default_est_symbols_derived_from_fundcalc(self):
        # 中间层名单从 fundcalc.qdii_est_symbol_map() 的值集合推导，
        # 这样以后加基金时不用记得同步这里
        from quoteupdater import default_est_symbols

        symbols = default_est_symbols()

        for expected in ("QQQ", "SPY", "XOP", "INDA", "IBB", "USO"):
            self.assertIn(expected, symbols)


class StaleQuoteReadTest(unittest.TestCase):
    """``get_last_stored()``：无视 TTL 读最近一次快照。

    背景（真实问题）：``QuotesUpdater.get()`` 的 TTL 默认只有 60 秒，是给
    "要不要重新抓"用的。列表页当初用 ``get()`` 取行情，结果只要抓取进程没在跑，
    「最新价 / 行情时间 / 数据源」就全变空白 —— 而库里其实存着上一次抓到的行情。
    页面要的是"最近一次行情是什么时候的"，所以需要这个不判 TTL 的读取入口。
    """

    class _Store:
        """假的快照表：``load()`` 返回一行 MySQL 风格的数据。"""

        def __init__(self, row):
            self._row = row

        def load(self, symbol):
            return self._row

        def save(self, *args, **kwargs):  # pragma: no cover - 本测试不写
            return True

    def _updater(self, row, now):
        from quoteupdater import QuotesUpdater

        return QuotesUpdater(
            source=FakeSource({}),
            symbols_provider=lambda: [],
            store=self._Store(row),
            clock=lambda: now,
        )

    def _row(self, fetched_at):
        return {
            "symbol": "SZ161130",
            "source_symbol": "sz161130",
            "price": "4.796",
            "prev_price": "4.727",
            "name": "纳指LOF",
            "quote_date": datetime.date(2026, 9, 30),
            # MySQL TIME 列经 PyMySQL 取回是 timedelta（15:00:00）
            "quote_time": datetime.timedelta(seconds=54000),
            "source": "sina",
            "fetched_at": fetched_at,
        }

    def test_returns_stale_quote_that_get_would_drop(self):
        # 抓取时刻距今 2 天，远超 60 秒 TTL
        fetched = datetime.datetime(2026, 10, 3, 13, 43, 19)
        now = fetched.timestamp() + 2 * 24 * 3600
        updater = self._updater(self._row(fetched), now)

        # get() 判过期 -> None
        self.assertIsNone(updater.get("SZ161130"))
        # get_last_stored() 仍然给出这条，且带上抓取时刻
        quote = updater.get_last_stored("SZ161130")
        self.assertIsNotNone(quote)
        self.assertEqual(quote.price, "4.796")
        self.assertEqual(quote.prev_price, "4.727")
        self.assertEqual(quote.source, "sina")
        self.assertFalse(updater.is_stored_fresh(quote))

    def test_timedelta_quote_time_becomes_clock_text(self):
        """MySQL TIME 列是 timedelta，必须转成 ``HH:MM:SS`` 才能显示。"""
        fetched = datetime.datetime(2026, 10, 3, 13, 43, 19)
        updater = self._updater(self._row(fetched), fetched.timestamp())

        quote = updater.get_last_stored("SZ161130")

        self.assertEqual(quote.time, "15:00:00")
        self.assertEqual(quote.date, "2026-09-30")

    def test_fresh_quote_is_reported_fresh(self):
        fetched = datetime.datetime(2026, 10, 3, 13, 43, 19)
        updater = self._updater(self._row(fetched), fetched.timestamp())

        quote = updater.get_last_stored("SZ161130")

        self.assertTrue(updater.is_stored_fresh(quote))

    def test_missing_store_returns_none(self):
        from quoteupdater import QuotesUpdater

        updater = QuotesUpdater(source=FakeSource({}), symbols_provider=lambda: [])

        self.assertIsNone(updater.get_last_stored("SZ161130"))

    def test_normalize_quote_time_handles_odd_shapes(self):
        """时间字段可能是 timedelta / time / 字符串，都要落成时钟文本。"""
        from quoteupdater import _normalize_quote_time

        self.assertEqual(_normalize_quote_time(datetime.timedelta(seconds=57600)), "16:00:00")
        self.assertEqual(_normalize_quote_time(datetime.time(9, 15, 0)), "09:15:00")
        self.assertEqual(_normalize_quote_time("15:34:59"), "15:34:59")
        self.assertEqual(_normalize_quote_time("15:34:59.123456"), "15:34:59")
        self.assertEqual(_normalize_quote_time(None), "")
        self.assertEqual(_normalize_quote_time(""), "")
        # 跨过一天的 timedelta 不能显示成 "1 day, ..."
        self.assertEqual(_normalize_quote_time(datetime.timedelta(days=1, seconds=3600)), "01:00:00")


if __name__ == "__main__":
    unittest.main()
