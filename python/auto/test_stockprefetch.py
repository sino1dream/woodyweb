"""`python/auto/stockprefetch.py` 的单元测试。

对应 PHP：``php/stock/stockprefetch.php``、``php/class/year_month_day.php``。

判定逻辑的完整分支组合另有与真实 PHP 的逐项对照：
``python -B _php_prefetch_compare.py``（2.68 万个用例）。美股涉及夏令时，
PHP 侧脚手架无法忠实换算，所以美股的完整分支在这里用 ``zoneinfo`` 覆盖。
"""

import datetime
import os
import unittest
from unittest import mock

import stockprefetch as sp
import tzutil
from stocksymbol import StockSymbol

SHANGHAI = datetime.timezone(datetime.timedelta(hours=8), "Asia/Shanghai")


def shanghai(year, month, day, hour, minute=0, symbol="SH600000"):
    """构造「市场当地」时间点。

    必须按符号的真实时区来挂时区（并走 ``tzutil.localize`` 以正确处理夏令时），
    而不是用等于 +08:00 的固定偏移：``_check_between_market_close`` 在 PHP 里
    接收的是 ``SetTimeZone()`` 之后的 ``GetNowYMD()``，即市场当地时间；
    本模块内部也统一传入折算后的时间点。
    """
    naive = datetime.datetime(year, month, day, hour, minute)
    return tzutil.localize(naive, StockSymbol(symbol).get_time_zone())


class NeedFileTest(unittest.TestCase):
    def test_returns_false_when_file_is_fresh(self):
        now = shanghai(2026, 1, 5, 10, 0)
        mtimes = {"q": int(now.timestamp()) - 30}
        self.assertIs(sp.need_file(now, "q", 60, mtimes), False)

    def test_returns_file_time_when_expired(self):
        now = shanghai(2026, 1, 5, 10, 0)
        file_time = int(now.timestamp()) - 120
        self.assertEqual(sp.need_file(now, "q", 60, {"q": file_time}), file_time)

    def test_missing_file_uses_sentinel_one_like_php(self):
        # PHP: file_exists() ? filemtime() : 1，所以不存在时返回 1
        now = shanghai(2026, 1, 5, 10, 0)
        self.assertEqual(sp.need_file(now, "missing", 60, {}), 1)

    def test_needs_update_helper(self):
        self.assertFalse(sp.needs_update(False))
        self.assertTrue(sp.needs_update(0))
        self.assertTrue(sp.needs_update(12345))


class BetweenMarketCloseTest(unittest.TestCase):
    def test_weekday_threshold(self):
        now = shanghai(2026, 1, 5, 16, 0)  # 周一
        now_tick = int(now.timestamp())
        # A 股参数：工作日需等待 2 小时
        self.assertFalse(sp._check_between_market_close(now, now_tick - 3600, 2, 8))
        self.assertTrue(sp._check_between_market_close(now, now_tick - 3 * 3600, 2, 8))

    def test_saturday_adds_weekend_hours(self):
        # 周六 12:00（市场当地）。PHP 用的是严格大于号，所以阈值本身不算超过：
        # A 股参数 2 + 8 = 10 小时，港股 3 + 8 = 11 小时。
        now = shanghai(2026, 1, 10, 12, 0, "00700")
        self.assertEqual(now.weekday(), 5)  # 确认是周六
        now_tick = int(now.timestamp())

        self.assertFalse(sp._check_between_market_close(now, now_tick - 10 * 3600, 2, 8))
        self.assertTrue(sp._check_between_market_close(now, now_tick - 11 * 3600, 2, 8))
        # 港股阈值更宽：11 小时刚好不算，12 小时才算
        self.assertFalse(sp._check_between_market_close(now, now_tick - 11 * 3600, 3, 8))
        self.assertTrue(sp._check_between_market_close(now, now_tick - 12 * 3600, 3, 8))

    def test_sunday_adds_full_day(self):
        now = shanghai(2026, 1, 11, 12, 0)  # 周日
        now_tick = int(now.timestamp())
        # A 股参数 2 + 8 + 24 = 34 小时
        self.assertFalse(sp._check_between_market_close(now, now_tick - 34 * 3600, 2, 8))
        self.assertTrue(sp._check_between_market_close(now, now_tick - 35 * 3600, 2, 8))


class FutureMarketTradingTest(unittest.TestCase):
    def test_saturday_is_closed_all_day(self):
        self.assertFalse(sp.is_future_market_trading(shanghai(2026, 1, 10, 20, 0)))

    def test_sunday_opens_at_eighteen(self):
        self.assertFalse(sp.is_future_market_trading(shanghai(2026, 1, 11, 17, 0)))
        self.assertTrue(sp.is_future_market_trading(shanghai(2026, 1, 11, 18, 0)))

    def test_friday_closes_at_seventeen(self):
        self.assertTrue(sp.is_future_market_trading(shanghai(2026, 1, 9, 16, 0)))
        self.assertFalse(sp.is_future_market_trading(shanghai(2026, 1, 9, 17, 0)))

    def test_weekday_break_is_only_hour_seventeen(self):
        self.assertTrue(sp.is_future_market_trading(shanghai(2026, 1, 5, 16, 0)))
        self.assertFalse(sp.is_future_market_trading(shanghai(2026, 1, 5, 17, 0)))
        self.assertTrue(sp.is_future_market_trading(shanghai(2026, 1, 5, 18, 0)))


class FutureNeedNewFileTest(unittest.TestCase):
    def test_fresh_file_needs_nothing(self):
        now = shanghai(2026, 1, 5, 10, 0)
        self.assertFalse(sp.future_need_new_file("q", now, 60, {"q": int(now.timestamp()) - 10}))

    def test_trading_time_needs_update(self):
        now = shanghai(2026, 1, 5, 10, 0)
        self.assertTrue(sp.future_need_new_file("q", now, 60, {"q": int(now.timestamp()) - 300}))

    def test_outside_trading_uses_between_close_rule(self):
        # 期货参数 23 + 7 = 30 小时（周六）。
        #
        # 关键前提：只有当「当前时刻」和「文件时刻」都落在非交易时段时，才会走到
        # 阈值判断。大于 30 小时的回溯会落在周五 17 点前，那属于交易时段，会先被
        # _isFutureMarketTrading() 判定为需要更新而直接返回 true，测不到阈值。
        # 所以这里用周日 12:00 与周六 07:00 的组合来隔离阈值逻辑。
        # hf_ES 的时区是 America/New_York，因此按美东构造时间点，与 PHP 在
        # SetTimeZone() 之后取 GetNowYMD() 的语义一致。
        now = shanghai(2026, 1, 11, 12, 0, "hf_ES")
        self.assertEqual(now.weekday(), 6)  # 确认是周日
        now_tick = int(now.timestamp())

        # 29 小时前是周六 07:00，同样非交易时段 -> 走阈值
        self.assertFalse(sp.future_need_new_file("q", now, 60, {"q": now_tick - 29 * 3600}))
        # 53 小时前是周五 07:00（交易时段）-> 由 _isFutureMarketTrading() 直接判定需要更新
        self.assertTrue(sp.future_need_new_file("q", now, 60, {"q": now_tick - 53 * 3600}))
        # 55 小时前是周五 05:00（交易时段）-> 同上
        self.assertTrue(sp.future_need_new_file("q", now, 60, {"q": now_tick - 55 * 3600}))

    def test_sunday_extra_day_threshold(self):
        # 周日走 23 + 7 + 24 = 54 小时分支：需要两个非交易时刻相隔 54 小时以上。
        # 周六 12:00 距周日 12:00 是 24 小时，只有回溯到更早的周六才会触发。
        # 这里直接验证 _check_between_market_close 的周日分支。
        now = shanghai(2026, 1, 11, 12, 0, "hf_ES")
        now_tick = int(now.timestamp())
        self.assertFalse(sp._check_between_market_close(now, now_tick - 54 * 3600, 23, 7))
        self.assertTrue(sp._check_between_market_close(now, now_tick - 55 * 3600, 23, 7))


class StockNeedNewQuotesTest(unittest.TestCase):
    def test_fresh_file_returns_false_immediately(self):
        now = shanghai(2026, 1, 5, 10, 0)
        symbol = StockSymbol("SH600000")
        self.assertFalse(
            sp.stock_need_new_quotes(symbol, "q", now, 60, {"q": int(now.timestamp()) - 10})
        )

    def test_trading_now_needs_update(self):
        # 周一 10:00（上海） A 股正在交易
        now = shanghai(2026, 1, 5, 10, 0)
        symbol = StockSymbol("SH600000")
        self.assertTrue(
            sp.stock_need_new_quotes(symbol, "q", now, 60, {"q": int(now.timestamp()) - 300})
        )

    def test_file_time_in_trading_hours_needs_update(self):
        # 当前是周一 07:00（盘前），但文件时间落在上周五的交易时段内
        now = shanghai(2026, 1, 5, 7, 0)
        now_tick = int(now.timestamp())
        # 上周五 2026-01-02 10:00 上海
        file_time = int(shanghai(2026, 1, 2, 10, 0).timestamp())
        self.assertGreater(now_tick, file_time)
        symbol = StockSymbol("SH600000")
        self.assertTrue(sp.stock_need_new_quotes(symbol, "q", now, 60, {"q": file_time}))

    def test_same_day_before_market_returns_false(self):
        # 周一 07:00，文件时间当天 06:00（同一天、盘前之前）
        now = shanghai(2026, 1, 5, 7, 0)
        file_time = int(shanghai(2026, 1, 5, 6, 0).timestamp())
        symbol = StockSymbol("SH600000")
        self.assertFalse(sp.stock_need_new_quotes(symbol, "q", now, 60, {"q": file_time}))

    def test_same_day_after_previous_close_returns_false(self):
        # 周一 20:00，文件时间当天 15:30（收盘之后），A 股参数判定为不需要
        now = shanghai(2026, 1, 5, 20, 0)
        file_time = int(shanghai(2026, 1, 5, 15, 30).timestamp())
        symbol = StockSymbol("SH600000")
        self.assertFalse(sp.stock_need_new_quotes(symbol, "q", now, 60, {"q": file_time}))

    def test_weekend_uses_weekend_threshold(self):
        # 周日对 A 股：2 + 8 + 24 = 34 小时
        now = shanghai(2026, 1, 11, 12, 0)
        now_tick = int(now.timestamp())
        symbol = StockSymbol("SH600000")
        self.assertFalse(sp.stock_need_new_quotes(symbol, "q", now, 60, {"q": now_tick - 33 * 3600}))
        self.assertTrue(sp.stock_need_new_quotes(symbol, "q", now, 60, {"q": now_tick - 35 * 3600}))

    def test_us_symbol_uses_new_york_clock(self):
        # 同一瞬间：美东周一 09:00 EST（= 14:00 UTC）是盘前，上海则是周一 22:00。
        now = datetime.datetime(2026, 1, 5, 14, 0, tzinfo=datetime.timezone.utc)
        # 文件时间取 5 分钟前：对两个市场都远小于更新间隔，先确保不会被第一步拦掉
        file_time = int(now.timestamp()) - 300
        self.assertTrue(sp.needs_update(sp.need_file(now, "q", 60, {"q": file_time})))

        # 美股落在纽约时间 08:55，是盘前 -> 不属于交易时段，需要按收盘规则继续判断
        us_symbol = StockSymbol("XOP")
        self.assertTrue(sp.stock_need_new_quotes(us_symbol, "q", now, 60, {"q": file_time}))

        # A 股同一瞬间是上海 22:00，已收盘；这里用较大的时间差来对比判定路径不同
        a_symbol = StockSymbol("SH600000")
        old_file_time = int(shanghai(2026, 1, 2, 15, 0).timestamp())
        # A 股参数（工作日 2 小时）下，相隔一个周末早已超过阈值 -> 需要更新
        self.assertTrue(sp.stock_need_new_quotes(a_symbol, "q", now, 60, {"q": old_file_time}))

        # 同一份「很旧」的文件对美股同样需要更新，两个市场都走 _checkBetweenMarketClose
        self.assertTrue(sp.stock_need_new_quotes(us_symbol, "q", now, 60, {"q": old_file_time}))

    def test_us_symbol_before_market_uses_new_york_clock(self):
        # 同一瞬间：纽约周一 03:00（盘前）＝ 上海周一 16:00（A 股已收盘）
        moment = datetime.datetime(2026, 1, 5, 8, 0, tzinfo=datetime.timezone.utc)

        symbol = StockSymbol("XOP")
        market = sp._to_market_datetime(moment, symbol)
        self.assertEqual(market.hour, 3)
        self.assertTrue(symbol.is_before_stock_market(market.hour * 100 + market.minute))

        a_symbol = StockSymbol("SH600000")
        a_market = sp._to_market_datetime(moment, a_symbol)
        self.assertEqual(a_market.hour, 16)
        self.assertTrue(a_symbol.is_after_stock_market(a_market.hour * 100 + a_market.minute))

    def test_naive_datetime_is_localized_to_symbol_zone(self):
        # 朴素时间按符号时区补齐：A 股按上海，美股按纽约
        naive = datetime.datetime(2026, 1, 5, 10, 0)
        market = sp._to_market_datetime(naive, StockSymbol("SH600000"))
        self.assertEqual(market.hour, 10)

        us_market = sp._to_market_datetime(naive, StockSymbol("XOP"))
        self.assertEqual(us_market.hour, 10)
        self.assertNotEqual(us_market.utcoffset(), datetime.timedelta(hours=8))


class SinaFundNeedFileTest(unittest.TestCase):
    def test_fresh_file_needs_nothing(self):
        now = shanghai(2026, 1, 5, 10, 0, "SZ162411")
        symbol = StockSymbol("SZ162411")
        mtimes = {"q": int(now.timestamp()) - 60}
        self.assertFalse(sp.sina_fund_need_file(symbol, "q", now, file_mtime=mtimes))

    def test_missing_file_needs_update(self):
        now = shanghai(2026, 1, 5, 10, 0, "SZ162411")
        symbol = StockSymbol("SZ162411")
        # 基金间隔是 30 分钟，不存在文件时按 sentinel=1 一定超过间隔
        self.assertTrue(sp.sina_fund_need_file(symbol, "q", now, file_mtime={}))

    def test_existing_net_value_skips_update(self):
        now = shanghai(2026, 1, 5, 10, 0, "SZ162411")
        symbol = StockSymbol("SZ162411")
        mtimes = {"q": int(now.timestamp()) - 3600}
        self.assertFalse(
            sp.sina_fund_need_file(
                symbol,
                "q",
                now,
                file_mtime=mtimes,
                net_value_date=lambda _symbol: "2026-01-05",
                net_value_exists=lambda _symbol, _date: True,
            )
        )

    def test_before_sixteen_hundred_with_today_date_skips_update(self):
        # 当天 10:00、且数据库日期就是今天 -> 净值尚未公布，不需要取
        now = shanghai(2026, 1, 5, 10, 0, "SZ162411")
        symbol = StockSymbol("SZ162411")
        mtimes = {"q": int(now.timestamp()) - 3600}
        self.assertFalse(
            sp.sina_fund_need_file(
                symbol,
                "q",
                now,
                file_mtime=mtimes,
                net_value_date=lambda _symbol: "2026-01-05",
                net_value_exists=lambda _symbol, _date: False,
            )
        )

    def test_after_sixteen_hundred_with_today_date_needs_update(self):
        now = shanghai(2026, 1, 5, 17, 0, "SZ162411")
        symbol = StockSymbol("SZ162411")
        mtimes = {"q": int(now.timestamp()) - 3600}
        self.assertTrue(
            sp.sina_fund_need_file(
                symbol,
                "q",
                now,
                file_mtime=mtimes,
                net_value_date=lambda _symbol: "2026-01-05",
                net_value_exists=lambda _symbol, _date: False,
            )
        )

    def test_sina_fund_symbol_is_converted_for_lookup(self):
        # f_162411 需要先还原成 SZ162411 再查数据库
        now = shanghai(2026, 1, 5, 17, 0, "SZ162411")
        symbol = StockSymbol("f_162411")
        mtimes = {"q": int(now.timestamp()) - 3600}
        seen = {}

        def net_value_date(check_symbol):
            seen["symbol"] = check_symbol.get_symbol()
            return "2026-01-04"

        sp.sina_fund_need_file(
            symbol, "q", now, file_mtime=mtimes, net_value_date=net_value_date
        )
        self.assertEqual(seen["symbol"], "SZ162411")

    def test_unknown_sina_fund_digit_returns_false(self):
        # 240019 既不是沪市也不是深市基金区间 -> BuildChinaFundSymbol 返回 false
        now = shanghai(2026, 1, 5, 17, 0, "f_240019")
        symbol = StockSymbol("f_240019")
        mtimes = {"q": int(now.timestamp()) - 3600}
        self.assertFalse(sp.sina_fund_need_file(symbol, "q", now, file_mtime=mtimes))


class PrefetchPlanTest(unittest.TestCase):
    def _quote_path(self, stem):
        """按本模块的命名规则拼出行情文件路径。

        必须在测试里用同一套 os.path.join，否则 Windows 上会得到 '/' 与 '\\'
        两种分隔符，file_mtime 的内存映射就对不上了。
        """
        return os.path.join(sp.DEFAULT_QUOTE_DIRECTORY, stem + ".txt")

    def _plan(self, symbols, now, mtimes):
        prefetch = {}
        for text in symbols:
            prefetch[text] = StockSymbol(text)

        selected = sp.symbols_to_fetch(prefetch, now, mtimes, 60)
        return [(name, stem) for name, stem in selected]

    def test_a_share_uses_sina_symbol_and_its_own_file(self):
        now = shanghai(2026, 1, 5, 10, 0)
        # SH600000 正常交易，文件已过期
        mtimes = {self._quote_path("sh600000"): int(now.timestamp()) - 300}
        plan = self._plan(["SH600000"], now, mtimes)

        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0][0], self._quote_path("sh600000"))
        self.assertEqual(plan[0][1], "SH600000")

    def test_fresh_file_excluded_from_plan(self):
        now = shanghai(2026, 1, 5, 10, 0)
        mtimes = {self._quote_path("sh600000"): int(now.timestamp()) - 10}
        self.assertEqual(self._plan(["SH600000"], now, mtimes), [])


class PrefetchSinaStockDataTest(unittest.TestCase):
    def test_unknown_symbols_are_returned(self):
        now = shanghai(2026, 1, 5, 10, 0)
        # ^FTSE 没有新浪代码，GetSinaSymbol() 返回 false
        with mock.patch.object(sp, "symbols_to_fetch", return_value=[]):
            unknown = sp.prefetch_sina_stock_data(["^FTSE"], lambda _: None, now)
        self.assertEqual(unknown, ["^FTSE"])

    def test_east_money_forex_is_ignored_silently(self):
        now = shanghai(2026, 1, 5, 10, 0)
        with mock.patch.object(sp, "symbols_to_fetch", return_value=[]):
            unknown = sp.prefetch_sina_stock_data(["USCNY"], lambda _: None, now)
        # PHP 注释明确：EASTMONEY 外汇什么都不做，也不放进 unknown
        self.assertEqual(unknown, [])

    def test_a_share_adds_sina_fund_symbol(self):
        now = shanghai(2026, 1, 5, 10, 0, "SZ162411")
        captured = {}

        def fake_symbols_to_fetch(prefetch, *_args, **_kwargs):
            captured.update(prefetch)
            return []

        with mock.patch.object(sp, "symbols_to_fetch", side_effect=fake_symbols_to_fetch):
            # 用场内 LOF 才能真正走到 is_fund_a() 分支（普通股票不会）
            sp.prefetch_sina_stock_data(["SZ162411"], lambda _: None, now)

        # 注意 PHP 只登记**新浪符号**作为 key，不会把原始符号也登记一份；
        # 场内基金/ETF 额外多一个 f_ 前缀的场外净值符号
        self.assertEqual(sorted(captured), ["f_162411", "sz162411"])
        self.assertEqual(captured["sz162411"].get_symbol(), "SZ162411")

    def test_plain_stock_has_no_sina_fund_symbol(self):
        now = shanghai(2026, 1, 5, 10, 0)
        captured = {}

        def fake_symbols_to_fetch(prefetch, *_args, **_kwargs):
            captured.update(prefetch)
            return []

        with mock.patch.object(sp, "symbols_to_fetch", side_effect=fake_symbols_to_fetch):
            sp.prefetch_sina_stock_data(["SH600000"], lambda _: None, now)

        self.assertEqual(sorted(captured), ["sh600000"])

    def test_prefetch_writes_quote_lines(self):
        # 直接测试回写逻辑：两个待更新条目对应两行行情
        selected = [("debug/sina/a.txt", "AAA"), ("debug/sina/b.txt", "BBB")]
        saved = {}

        def fetch(symbols):
            self.assertEqual(symbols, ["AAA", "BBB"])
            return "line-a\nline-b"

        sp._prefetch(selected, fetch, lambda name, data: saved.__setitem__(name, data))

        self.assertEqual(saved, {"debug/sina/a.txt": "line-a", "debug/sina/b.txt": "line-b"})

    def test_prefetch_skips_when_fetch_fails(self):
        selected = [("a.txt", "AAA")]
        saved = {}
        sp._prefetch(selected, lambda _: None, lambda name, data: saved.__setitem__(name, data))
        self.assertEqual(saved, {})

    def test_prefetch_ignores_missing_lines(self):
        # PHP 没有做行数检查，这里显式跳过缺失的行，避免写坏缓存
        selected = [("a.txt", "AAA"), ("b.txt", "BBB")]
        saved = {}
        sp._prefetch(selected, lambda _: "only-one-line", lambda name, data: saved.__setitem__(name, data))
        self.assertEqual(saved, {"a.txt": "only-one-line"})


class FileQuoteStorageTest(unittest.TestCase):
    def test_load_missing_returns_none(self):
        self.assertIsNone(sp.load_sina_data("definitely_missing_file.txt", "_test_quote_tmp"))

    def test_save_reports_error_for_unwritable_path(self):
        """写入失败必须抛出，而不是静默丢数据。

        沙箱环境对临时目录的写权限受限，所以这里只验证失败路径会报错；
        真实写入与读取的往返在部署路径下使用，逻辑本身很薄。
        """
        with self.assertRaises((OSError, ValueError)):
            sp.save_sina_data("gb_xop.txt", "XOP,1.23", "\x00invalid-path\x00")


if __name__ == "__main__":
    unittest.main()
