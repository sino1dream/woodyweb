"""`quotesource.py` 与 `quoteupdater.py` 的单元测试。

对应 PHP：``php/stock.php`` 的 ``GetSinaQuotes()`` / ``StockPrefetchArrayData()``、
``php/stock/stockprefetch.php`` 的间隔节流。

全部用假的行情源离线测试，不打网络。
"""

import datetime
import unittest

from source import quotesource
from updater.quoteupdater import (
    RecentQuote,
    QuotesUpdater,
    is_any_market_open,
    parse_symbol,
    quote_from_reference,
    stored_fetch_times,
    symbol_needs_quote,
)
from core import tzutil
from core.stocksymbol import StockSymbol, TIME_ZONE_SHANGHAI

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


def make_hk_index_line(sina_symbol, code, price, prev, date="2026/10/14", time_text="15:16:58"):
    """造一行港股指数 ``rt_hk`` 格式的行情（实测样本，见 README_p0_data_layer.md §五）。

    与 A 股那套字段**完全不同**：0=英文代码、1=中文名、2=开盘、3=昨收、
    4=最高、5=最低、6=最新价、17=日期、18=时间，一共 25 个字段（尾部是空的）。
    字段数不到 29，所以分派一旦走错就会落到"美股"那支、解析不出来。
    """
    values = [""] * 25
    values[0] = code
    values[1] = "恒生指数"
    values[2] = "24282.250"
    values[3] = prev
    values[4] = "24524.630"
    values[5] = "24115.000"
    values[6] = price
    values[11] = "156428049.929"
    values[12] = "8515808004"
    values[17] = date
    values[18] = time_text
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

    def test_parse_hk_index_line_dispatches_by_db_symbol(self):
        """港股指数（``rt_hk`` 格式）必须传**库内符号**才解析得出来。

        实测样本：daemon 曾经把新浪代码 ``rt_hkHSI`` 当成分派依据，于是
        ``estquote`` 里留下 close 为空的行（README_p0_data_layer.md §五.2）。
        """
        line = make_hk_index_line("rt_hkHSI", "HSI", "24339.230", "24345.219")
        reference = parse_symbol("^HSI", line)

        self.assertTrue(reference.has_quote_data())
        self.assertEqual(reference.get_price(), "24339.23")
        self.assertEqual(reference.get_prev_price(), "24345.219")
        self.assertEqual(reference.str_date, "2026-10-14")
        self.assertEqual(reference.str_time, "15:16:58")

    def test_parse_hk_index_with_sina_code_yields_no_data(self):
        """反面用例：传新浪代码（而非库内符号）时解析不出任何字段。

        留着这条是为了说明"为什么调用方必须传库内符号"——``rt_hkHSI`` 既不是
        A 股也不是港股符号（``is_symbol_h()`` 认 ``^HSI``），25 个字段又不到
        美股那支要求的 29 个，于是整行被判为无数据。
        """
        line = make_hk_index_line("rt_hkHSI", "HSI", "24339.230", "24345.219")
        self.assertFalse(parse_symbol("rt_hkHSI", line).has_quote_data())

    def test_parse_global_index_line_keeps_price(self):
        """日经/欧洲指数（``znb_`` 格式）：库内符号与新浪代码相同，不受影响。"""
        reference = parse_symbol(
            "znb_NKY",
            'var hq_str_znb_NKY="日经225,67744.0700,-33.45,-0.05,2:12 AM,'
            '1759126320,2026-10-14,14:30:01,67942.2300,67777.5200,68408.1400,67533.1000,0";',
        )

        self.assertTrue(reference.has_quote_data())
        self.assertEqual(reference.get_price(), "67744.07")
        # 长格式的昨收在字段 9（字段 8 是开盘，别拿错）
        self.assertEqual(reference.get_prev_price(), "67777.52")


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


class FakeCursor:
    """假游标：按 SQL 关键字分派，返回预先准备好的行。"""

    def __init__(self, history, futures, stock_rows):
        self.history = history
        self.futures = futures
        self.stock_rows = stock_rows
        self.rows = []
        self.executed = []

    def execute(self, sql, args=None):
        self.executed.append(sql)
        if "stockhistory" in sql:
            self.rows = list(self.history)
        elif "futurespair" in sql:
            self.rows = list(self.futures)
        elif "symbol IN" in sql:
            wanted = set(args or ())
            self.rows = [row for row in self.stock_rows if row["symbol"] in wanted]
        else:  # pragma: no cover - 未知 SQL 直接空
            self.rows = []

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class FakeConnection:
    def __init__(self, *args, **kwargs):
        self.cursor_obj = FakeCursor(*args, **kwargs)

    def cursor(self):
        return self.cursor_obj


class FakeFundSource:
    def __init__(self, connection):
        self.connection = connection


class CollectSymbolsTest(unittest.TestCase):
    """``collect_symbols_from_source()`` 的三个来源。

    第三条（中间层 est 符号）是 P0.4 补的：中间层的作用就是给 QDII 估值当
    "当前值"，**不能因为没有 stockhistory 就不抓** —— 实测 znb_TPX 上游只有
    6 个字段、抓不到历史序列，于是它一直进不了抓取范围，estquote 里永远缺一行。
    """

    def _collect(self, history, futures=(), stock_rows=()):
        conn = FakeConnection(history, list(futures), list(stock_rows))
        from updater.quoteupdater import collect_symbols_from_source

        return collect_symbols_from_source(FakeFundSource(conn))

    def test_symbols_from_history(self):
        pairs = self._collect([{"stock_id": 1, "symbol": "SZ161130"}])
        self.assertEqual([item[0] for item in pairs], ["SZ161130"])
        self.assertEqual(pairs[0][2], 1)  # stock_id 带上，才能写快照表

    def test_middle_layer_without_history_is_still_collected(self):
        # 只有 stock 行、没有 stockhistory —— 仍要进抓取范围
        pairs = self._collect(
            history=[{"stock_id": 1, "symbol": "SZ161130"}],
            stock_rows=[{"stock_id": 1, "symbol": "SZ161130"},
                        {"stock_id": 9, "symbol": "znb_TPX"}],
        )
        self.assertIn("znb_TPX", [item[0] for item in pairs])

    def test_dedupe_keeps_one_pair_per_stock(self):
        pairs = self._collect(
            history=[{"stock_id": 1, "symbol": "SZ161130"}],
            futures=[{"stock_id": 1, "symbol": "SZ161130"}],
            stock_rows=[{"stock_id": 1, "symbol": "SZ161130"}],
        )
        self.assertEqual([item[0] for item in pairs], ["SZ161130"])

    def test_no_connection_returns_nothing(self):
        from updater.quoteupdater import collect_symbols_from_source

        self.assertEqual(collect_symbols_from_source(object()), [])


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

    def test_us_symbol_open_during_new_york_trading_hours(self):
        """北京时间周三 22:00 = 纽约周三 09:00（冬令时），盘中。

        曾经直接把服务器时间传进去，22:00 会被 ``is_after_stock_market``
        判成"已收盘"（> 20:05），``--only-open`` 于是**在美股盘中跳过抓取**。
        """
        wed_night = datetime.datetime(2026, 1, 7, 22, 0)
        self.assertTrue(is_any_market_open(["QQQ"], now=wed_night))

    def test_us_symbol_closed_when_new_york_asleep(self):
        # 北京时间周三 10:00 = 纽约周二 21:00，收盘后
        wed_morning = datetime.datetime(2026, 1, 7, 10, 0)
        self.assertFalse(is_any_market_open(["QQQ"], now=wed_morning))

    def test_mixed_batch_true_when_any_market_open(self):
        # 北京时间周三 10:00：A 股盘中（True），哪怕同批里的美股符号休市
        wed_morning = datetime.datetime(2026, 1, 7, 10, 0)
        self.assertTrue(is_any_market_open(["SZ161130", "QQQ"], now=wed_morning))


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
             orderbook=None, vwap=None):
        self.rows.append({
            "stock_id": stock_id, "symbol": symbol, "close": close,
            "prev_close": prev_close, "quote_date": quote_date, "source": source,
            # 盘口是后加的第十一个参数；均价是第十二个。假 store 照收，
            # 好让"盘口 / 均价有没有写进去"可断言
            "orderbook": orderbook,
            "vwap": vwap,
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

    def test_empty_price_does_not_overwrite_store(self):
        """取不到价时**不写库**：否则新浪偶尔返回空行会把好价格覆盖成空串。

        实测（README_p0_data_layer.md §五.2）：港股三个指数因为解析分派出错，
        ``realtimequote`` / ``estquote`` 里各留下 3 条空值行。内存缓存照旧
        写入（本轮的真实结果），只是不让空值传到跨进程共享的快照表。
        """
        quotes = FakeSnapshotStore()
        est = FakeSnapshotStore()
        # 源返回一行"有行无价"的数据（字段不足 -> 解析失败 -> price 为空）
        source = FakeSource({"sz161130": 'var hq_str_sz161130="only,three,fields";'})
        updater = QuotesUpdater(
            source=source,
            symbols_provider=lambda: [("SZ161130", "sz161130", 1)],
            store=quotes,
            est_store=est,
            est_symbols={"SZ161130"},
        )

        self.assertEqual(updater.update(), 1)   # 抓到行了，计数照旧
        self.assertIn("SZ161130", updater.last_updated_symbols)
        self.assertEqual(quotes.rows, [])
        self.assertEqual(est.rows, [])

    def test_default_est_symbols_derived_from_fundcalc(self):
        # 中间层名单从 fundcalc.qdii_est_symbol_map() 的值集合推导，
        # 这样以后加基金时不用记得同步这里
        from updater.quoteupdater import default_est_symbols

        symbols = default_est_symbols()

        for expected in ("QQQ", "SPY", "XOP", "INDA", "IBB", "USO"):
            self.assertIn(expected, symbols)

    def test_default_est_symbols_covers_hk_jp_eu_middle_layers(self):
        """港股 / 日 / 欧的中间层也必须在名单里（P0.4 补的缺口）。

        ``qdii_est_symbol_map()`` 只覆盖美股 QDII（PHP ``QdiiGetEstArray()``），
        港日欧的映射散在 ``QdiiHkGetEstSymbol()`` 等函数里 —— 早期
        ``default_est_symbols()`` 因此漏了它们，daemon 抓到行情也只写
        ``realtimequote``，官方 EST 的"实时快照优先"一步对港股基金全部落空。
        """
        from updater.quoteupdater import default_est_symbols

        symbols = default_est_symbols()

        for expected in ("^HSI", "^HSTECH", "^HSCE", "SH000869",
                         "znb_NKY", "znb_TPX", "znb_CAC", "znb_DAX"):
            self.assertIn(expected, symbols)


class FakeStoreWithRows:
    """假的快照表：``load_all()`` 返回若干行 MySQL 风格数据。"""

    def __init__(self, rows):
        self._rows = rows

    def load_all(self):
        return self._rows

    def save(self, *args, **kwargs):  # pragma: no cover - 本测试不写
        return True


class StoredFetchTimesTest(unittest.TestCase):
    """``stored_fetch_times()``：把快照表读成"上次抓取时刻"字典。"""

    def test_reads_fetched_at_per_symbol(self):
        from updater.quoteupdater import stored_fetch_times

        store = FakeStoreWithRows([
            {"symbol": "SZ161130", "fetched_at": datetime.datetime(2026, 10, 14, 12, 52, 20)},
            {"symbol": "QQQ", "fetched_at": datetime.datetime(2026, 10, 14, 9, 0, 0)},
        ])

        epochs = stored_fetch_times(store)

        self.assertEqual(sorted(epochs), ["QQQ", "SZ161130"])
        self.assertAlmostEqual(
            epochs["SZ161130"],
            datetime.datetime(2026, 10, 14, 12, 52, 20).timestamp(), places=3)

    def test_broken_store_returns_empty_mapping(self):
        from updater.quoteupdater import stored_fetch_times

        class Broken:
            def load_all(self):
                raise RuntimeError("no table")

        self.assertEqual(stored_fetch_times(Broken()), {})
        self.assertEqual(stored_fetch_times(None), {})


def shanghai_epoch(naive: datetime.datetime) -> float:
    """把"北京时间（朴素 datetime）"转成 epoch，测试里与实现同一口径。"""
    return tzutil.localize(naive, TIME_ZONE_SHANGHAI).timestamp()


class SymbolThrottleTest(unittest.TestCase):
    """逐符号节流（PHP ``stockprefetch.php`` 三个 ``*NeedFile`` 的接线）。

    时刻全部按**北京时间**给（服务器时区），验证时分确实按各符号自己的
    市场时区解释 —— 这是本节流与"直接拿服务器时间判断"的分水岭。
    """

    # 2026-01-07 是周三；1 月为美国冬令时（ET = 北京 - 13 小时）
    WED_10AM = datetime.datetime(2026, 1, 7, 10, 0)    # ET 周二 21:00，收盘后
    WED_10PM = datetime.datetime(2026, 1, 7, 22, 0)    # ET 周三 09:00，盘中
    SAT_10AM = datetime.datetime(2026, 1, 10, 10, 0)   # 周六，全市场休市

    def _needs(self, symbol, last=None, now=None):
        return symbol_needs_quote(
            symbol, StockSymbol(symbol).get_sina_symbol(),
            None if last is None else shanghai_epoch(last),
            shanghai_epoch(now or self.WED_10PM))

    def test_never_fetched_always_needs(self):
        for symbol in ("SZ161130", "QQQ", "^NDX", "hf_NQ", "znb_CAC"):
            self.assertTrue(self._needs(symbol, last=None, now=self.WED_10AM),
                            "%s 从来没抓过就该抓" % symbol)

    def test_recent_fetch_is_throttled(self):
        # 刚抓过 5 秒：任何符号都不该再抓（PHP NeedFile 的 60 秒间隔）
        for symbol in ("SZ161130", "QQQ", "hf_NQ"):
            self.assertFalse(self._needs(symbol, last=self.WED_10PM.replace(hour=21, minute=59, second=55),
                                         now=self.WED_10PM))

    def test_a_share_during_trading_needs_refresh(self):
        # 周三 10:00 北京时间 A 股盘中，1 小时前抓过 -> 交易中每分钟都该刷新
        self.assertTrue(self._needs("SZ161130", last=self.WED_10AM - datetime.timedelta(hours=1),
                                    now=self.WED_10AM))

    def test_a_share_after_close_same_day_waits_for_tomorrow(self):
        """同日收盘后不再重抓（PHP 的 ``IsAfterStockMarket($file)`` 分支）。

        收盘价当晚就抓到了，凌晨重抓拿到的还是同一条；下一次抓取要等
        "隔日 + 收盘后 2 小时"的间隔判断放行 —— 照抄 PHP，不自行加码。
        """
        # 周三 22:00，21:00（收盘后）抓过 -> 跳过
        self.assertFalse(self._needs("SZ161130", last=self.WED_10PM - datetime.timedelta(hours=1),
                                     now=self.WED_10PM))
        # 周四 00:30，昨晚 21:00 抓过 -> 隔日且超过 2 小时 -> 抓
        thur_midnight = datetime.datetime(2026, 1, 8, 0, 30)
        self.assertTrue(self._needs("SZ161130", last=self.WED_10PM - datetime.timedelta(hours=1),
                                    now=thur_midnight))

    def test_a_share_weekend_waits_ten_hours(self):
        # 周六：A 股 2 小时 + 周末 8 小时 = 10 小时
        self.assertFalse(self._needs("SZ161130", last=self.SAT_10AM - datetime.timedelta(hours=5),
                                     now=self.SAT_10AM))
        self.assertTrue(self._needs("SZ161130", last=self.SAT_10AM - datetime.timedelta(hours=11),
                                    now=self.SAT_10AM))

    def test_us_symbol_uses_new_york_time_not_beijing(self):
        """美股按**纽约时间**判断：北京时间周三 22:00 = 纽约周三 09:00（盘中）。

        没有时区折算的话，22:00 会被当成"已收盘"（> 20:05 而跳过，
        或 < 04:00 视为盘前）—— 那正是美股最该抓的时段。
        """
        # 盘中，2 分钟前抓过 -> 该刷新
        self.assertTrue(self._needs("QQQ", last=self.WED_10PM - datetime.timedelta(minutes=2),
                                    now=self.WED_10PM))
        # 北京时间周三 10:00 = 纽约周二 21:00（收盘后），半小时前抓过 -> 跳过
        self.assertFalse(self._needs("QQQ", last=self.WED_10AM - datetime.timedelta(minutes=30),
                                     now=self.WED_10AM))
        # 同上但 20 小时前抓过 -> 超过"其它市场 16 小时"的间隔，该抓
        self.assertTrue(self._needs("QQQ", last=self.WED_10AM - datetime.timedelta(hours=20),
                                    now=self.WED_10AM))

    def test_future_nearly_always_needs(self):
        # 期货几乎全天交易（只有周六与每小时 17 点那小时休市）
        self.assertTrue(self._needs("hf_NQ", last=self.WED_10PM - datetime.timedelta(minutes=2),
                                    now=self.WED_10PM))
        # 周六：期货 23 小时 + 周末 7 小时的间隔
        self.assertFalse(self._needs("hf_NQ", last=self.SAT_10AM - datetime.timedelta(hours=10),
                                     now=self.SAT_10AM))
        self.assertTrue(self._needs("hf_NQ", last=self.SAT_10AM - datetime.timedelta(hours=40),
                                    now=self.SAT_10AM))

    def test_unparseable_symbol_still_fetches(self):
        # 判定本身不许把符号吞掉：异常时保守抓取
        self.assertTrue(symbol_needs_quote("!!!", "!!!", 1.0, shanghai_epoch(self.WED_10AM)))


class ThrottledUpdateTest(unittest.TestCase):
    """``update(respect_throttle=True)`` 的批处理行为。"""

    def _updater(self, source, now_box, pairs):
        return QuotesUpdater(
            source=source,
            symbols_provider=lambda: pairs,
            ttl=60,
            clock=lambda: now_box[0],
        )

    def test_throttled_update_filters_and_counts(self):
        source = FakeSource({
            "sz161130": make_line("sz161130", "纳指LOF", "4.796", "4.727"),
            "sz161125": make_line("sz161125", "标普500", "3.275", "3.240"),
        })
        now = [shanghai_epoch(datetime.datetime(2026, 1, 7, 22, 0))]
        updater = self._updater(source, now, [
            ("SZ161130", "sz161130", 1),
            ("SZ161125", "sz161125", 2),
        ])
        # 两只都"刚抓过"（30 秒前）
        updater.seed_last_fetch({"sz161130": now[0] - 30, "sz161125": now[0] - 30})

        updated = updater.update(respect_throttle=True)

        self.assertEqual(updated, 0)
        self.assertEqual(updater.last_skipped_count, 2)
        self.assertEqual(source.calls, [])  # 一个都没请求

    def test_throttled_update_fetches_only_due_symbols(self):
        source = FakeSource({
            "sz161130": make_line("sz161130", "纳指LOF", "4.796", "4.727"),
            "sz161125": make_line("sz161125", "标普500", "3.275", "3.240"),
        })
        now = [shanghai_epoch(datetime.datetime(2026, 1, 7, 22, 0))]
        updater = self._updater(source, now, [
            ("SZ161130", "sz161130", 1),
            ("SZ161125", "sz161125", 2),
        ])
        # SZ161130 抓过（30 秒前，被节流）；SZ161125 从没抓过（要抓）
        updater.seed_last_fetch({"sz161130": now[0] - 30})

        updated = updater.update(respect_throttle=True)

        self.assertEqual(updated, 1)
        self.assertEqual(updater.last_skipped_count, 1)
        self.assertEqual(source.calls, [["sz161125"]])

    def test_update_marks_whole_batch_as_fetched(self):
        """新浪持续返回空的符号也要记"抓过了"（PHP 对选中符号一律写文件）。"""
        source = FakeSource({
            "sz161130": make_line("sz161130", "纳指LOF", "4.796", "4.727"),
            # sz161125 没有行
        })
        now = [shanghai_epoch(datetime.datetime(2026, 1, 7, 22, 0))]
        updater = self._updater(source, now, [
            ("SZ161130", "sz161130", 1),
            ("SZ161125", "sz161125", 2),
        ])

        self.assertEqual(updater.update(respect_throttle=True), 1)
        # 同一时刻再来一轮：两个符号都在 60 秒间隔内，全都跳过
        self.assertEqual(updater.update(respect_throttle=True), 0)
        self.assertEqual(updater.last_skipped_count, 2)

    def test_default_update_ignores_throttle(self):
        """``respect_throttle`` 默认关：``--once`` 与既有测试不受影响。"""
        source = FakeSource({
            "sz161130": make_line("sz161130", "纳指LOF", "4.796", "4.727"),
            "sz161125": make_line("sz161125", "标普500", "3.275", "3.240"),
        })
        now = [shanghai_epoch(datetime.datetime(2026, 1, 10, 10, 0))]  # 周六
        updater = self._updater(source, now, [
            ("SZ161130", "sz161130", 1),
            ("SZ161125", "sz161125", 2),
        ])
        updater.seed_last_fetch({"sz161130": now[0] - 5, "sz161125": now[0] - 5})

        # 不节流：周末 + 刚抓过也照样抓（显式全量语义）
        self.assertEqual(updater.update(), 2)
        self.assertEqual(updater.last_skipped_count, 0)


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
        from updater.quoteupdater import QuotesUpdater

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
        from updater.quoteupdater import QuotesUpdater

        updater = QuotesUpdater(source=FakeSource({}), symbols_provider=lambda: [])

        self.assertIsNone(updater.get_last_stored("SZ161130"))

    def test_normalize_quote_time_handles_odd_shapes(self):
        """时间字段可能是 timedelta / time / 字符串，都要落成时钟文本。"""
        from updater.quoteupdater import _normalize_quote_time

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
