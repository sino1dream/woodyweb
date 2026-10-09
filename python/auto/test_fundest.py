"""``fundeststore`` / ``fundestcalc`` 的单元测试。

对应 PHP：
* ``php/sql/sqldailytime.php`` ``DailyTimeSql``（``fundest`` 表的读写接口）
* ``php/sql/sqlstocksymbol.php:117`` ``new DailyTimeSql('fundest')``
* ``php/stock.php`` ``StockUpdateEstResult()`` / ``StockCompareEstResult()``
* ``php/stock/qdiiref.php`` ``_QdiiReference::EstNetValue()``
* ``php/stock/fundpairref.php:252`` 的 ``20:55`` 闸门
"""

from __future__ import annotations

import datetime
import unittest

from fundestcalc import (
    ESTIMATE_ERROR_THRESHOLD,
    EstimateCalculator,
    EstRecord,
)
from fundeststore import FundEstStore, hour_minute_of


class HourMinuteTest(unittest.TestCase):
    """``GetHourMinute()``：把时刻转成 ``HHMM`` 整数。"""

    def test_plain_strings(self):
        self.assertEqual(hour_minute_of("20:54:00"), 2054)
        self.assertEqual(hour_minute_of("20:55:00"), 2055)
        self.assertEqual(hour_minute_of("17:30"), 1730)
        self.assertEqual(hour_minute_of("0900"), 900)

    def test_timedelta_from_mysql_time_column(self):
        # PyMySQL 把 TIME 列取成 timedelta：17:30:00 -> 63000 秒
        self.assertEqual(hour_minute_of(datetime.timedelta(seconds=63000)), 1730)
        # 跨天也要算对（否则会变成 "1 day, ..." 而比较失败）
        self.assertEqual(hour_minute_of(datetime.timedelta(days=1, seconds=3600)), 100)

    def test_time_and_datetime(self):
        self.assertEqual(hour_minute_of(datetime.time(20, 55, 30)), 2055)
        self.assertEqual(hour_minute_of(datetime.datetime(2026, 10, 1, 9, 15, 0)), 915)

    def test_invalid_returns_none(self):
        for value in (None, "", "abc", "99:99"):
            self.assertIsNone(hour_minute_of(value), repr(value))


class TimeTextTest(unittest.TestCase):
    """``fundestcli._time_text()``：把 TIME 列显示成 ``HH:MM:SS``。

    实测问题：直接 ``%s`` 一个 ``timedelta(seconds=2406)`` 会得到 ``'0:40:06'``，
    小时少了前导零。CLI 与页面都必须先规范化。
    """

    def test_timedelta_gets_zero_padded(self):
        from fundestcli import _time_text

        self.assertEqual(_time_text(datetime.timedelta(seconds=2406)), "00:40:06")
        self.assertEqual(_time_text(datetime.timedelta(seconds=63000)), "17:30:00")

    def test_strings_pass_through(self):
        from fundestcli import _time_text

        self.assertEqual(_time_text("04:00:00"), "04:00:00")
        self.assertEqual(_time_text("04:00:00.123456"), "04:00:00")

    def test_missing_shows_dash(self):
        from fundestcli import _time_text

        self.assertEqual(_time_text(None), "-")
        self.assertEqual(_time_text(""), "-")


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []
        self.sql = ""
        self.params = ()
        self.rowcount = 0

    def execute(self, sql, params=()):
        self.sql = sql
        self.params = params
        self.connection.queries.append((sql, params))
        self.rows = self.connection.dispatch(sql, params)
        self.rowcount = len(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class FakeConnection:
    """最小可用的假连接：自带 ``fundest`` 与 ``netvaluehistory`` 两张表。

    **两张表分开存**（``self.tables`` 每次都是新 dict）。之前把外部传入的
    ``netvalue`` 列表直接存进 ``self.tables``，与真实净值表共用同一个 dict，
    导致不同测试之间互相污染 —— 那是测试写法的错误，不是被测代码的问题。
    """

    def __init__(self, fundest=None, netvalue=None):
        self.queries = []
        self.tables = {
            "fundest": [dict(r) for r in (fundest or [])],
            "netvaluehistory": [dict(r) for r in (netvalue or [])],
        }
        self.commits = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def dispatch(self, sql, params):
        import re

        upper = sql.strip().upper()
        table_match = re.search(r"FROM\s+`([^`]+)`", sql) or re.search(r"INTO\s+`([^`]+)`", sql)
        table = table_match.group(1) if table_match else ""
        rows = self.tables.get(table, [])

        if upper.startswith("INSERT"):
            # (stock_id, date, close, time)
            rows.append({
                "id": len(rows) + 1,
                "stock_id": params[0],
                "date": params[1],
                "close": params[2],
                "time": params[3],
            })
            return []

        if "COUNT(*)" in sql:
            stock_id = params[0] if params else None
            return [{"n": len([r for r in rows if str(r["stock_id"]) == str(stock_id)])}]

        if "WHERE" not in sql:
            return sorted(rows, key=lambda r: str(r["date"]), reverse=True)

        stock_id = params[0]
        rows = [r for r in rows if str(r["stock_id"]) == str(stock_id)]
        if "`date` = %s" in sql:
            rows = [r for r in rows if str(r["date"]) == str(params[1])][:1]
        elif "`date` < %s" in sql:
            rows = [r for r in rows if str(r["date"]) < str(params[1])]
        rows.sort(key=lambda r: (str(r["date"]), str(r.get("time") or "")), reverse=True)
        if "LIMIT 1" in sql:
            rows = rows[:1]
        return rows


class FundEstStoreTest(unittest.TestCase):
    def _store(self, fundest=None, netvalue=None):
        connection = FakeConnection(fundest=fundest, netvalue=netvalue)
        return FundEstStore(connection), connection

    def test_write_then_read_back(self):
        store, connection = self._store()
        written = store.write("1", "2026-09-30", 4.545, time="04:00:00")

        self.assertTrue(written)
        self.assertEqual(connection.commits, 1)
        self.assertEqual(store.get_close("1", "2026-09-30"), 4.545)
        self.assertEqual(store.count("1"), 1)

    def test_write_does_not_overwrite_existing(self):
        """已有记录就不动 —— 这一列的价值在"第一次算出来的那个数"。"""
        store, _ = self._store(fundest=[
            {"id": 1, "stock_id": "1", "date": "2026-09-30", "close": 4.545, "time": "04:00:00"},
        ])

        self.assertFalse(store.write("1", "2026-09-30", 9.999))
        self.assertEqual(store.get_close("1", "2026-09-30"), 4.545)

    def test_rejects_incomplete_input(self):
        store, _ = self._store()
        self.assertFalse(store.write(None, "2026-09-30", 1.0))
        self.assertFalse(store.write("1", None, 1.0))
        self.assertFalse(store.write("1", "2026-09-30", None))
        self.assertFalse(store.write("1", "2026-09-30", ""))

    def test_get_latest_returns_newest_by_date(self):
        store, _ = self._store(fundest=[
            {"id": 1, "stock_id": "1", "date": "2026-09-28", "close": 4.57, "time": "04:06:00"},
            {"id": 2, "stock_id": "1", "date": "2026-09-30", "close": 4.545, "time": "04:00:00"},
            {"id": 3, "stock_id": "1", "date": "2026-09-29", "close": 4.535, "time": "04:04:00"},
        ])

        self.assertEqual(store.get_latest("1")["date"], "2026-09-30")
        self.assertEqual(store.get_latest_date("1"), "2026-09-30")
        self.assertIsNone(store.get_latest("999"))

    def test_write_if_ready_skips_when_real_netvalue_exists(self):
        """对应 PHP "Only update when net value is NOT ready"。"""
        store, _ = self._store(netvalue=[
            {"id": 1, "stock_id": "1", "date": "2026-09-30", "close": 4.5452},
        ])

        self.assertFalse(store.write_if_ready("1", "2026-09-30", 4.545))
        self.assertEqual(store.count("1"), 0)

    def test_write_if_ready_writes_when_netvalue_missing(self):
        store, _ = self._store(netvalue=[
            {"id": 1, "stock_id": "1", "date": "2026-09-29", "close": 4.5452},
        ])

        self.assertTrue(store.write_if_ready("1", "2026-09-30", 4.545, time="04:00:00"))
        self.assertEqual(store.count("1"), 1)

    def test_gate_hhmm_blocks_late_writes(self):
        """对应 fundpairref.php:252 的 ``GetHourMinute() < 2055``。"""
        store, _ = self._store()

        # 20:54 可以写
        self.assertTrue(store.write_if_ready("1", "2026-09-30", 4.545,
                                             time="20:54:00", gate_hhmm=2055))
        # 20:55 起不写（另一只符号，避免被"已有不覆盖"挡住而误判）
        self.assertFalse(store.write_if_ready("2", "2026-09-30", 4.545,
                                              time="20:55:00", gate_hhmm=2055))
        self.assertFalse(store.write_if_ready("3", "2026-09-30", 4.545,
                                              time="21:30:00", gate_hhmm=2055))

    def test_gate_none_skips_time_check(self):
        store, _ = self._store()
        self.assertTrue(store.write_if_ready("1", "2026-09-30", 4.545,
                                             time="23:59:00", gate_hhmm=None))


class EstimateCalculatorTest(unittest.TestCase):
    """官方 EST 的计算与记录。

    用假数据源喂一条与生产一致的链路：
        中间层 QQQ 净值 1.0（本项目归一化口径）、校准值 32.079543、
        仓位 0.95、基金净值 4.5452 → 官方 EST 应为 4.5452。
    """

    class Source:
        """假数据源：只实现官方 EST 计算真正需要的那几个接口。

        ``netvalue`` / ``history`` 的键是 ``(stock_id, date)``。
        ``calibrations`` 的键是 ``stock_id``，值是校准值。
        """

        CALIBRATION = 32.079543
        #: 中间层 QQQ 的校准值（归一化口径下的真实值）。
        #: 它决定"贡献值" hedge_value = est_m × cal_m，进而决定官方 EST。
        MIDDLE_CALIBRATION = 0.008945009205154886

        def __init__(self, netvalue_history, history, quote_time="04:00:00",
                     calibrations=None):
            self.netvalue = netvalue_history or {}
            self.history = history or {}
            self.calibrations = calibrations if calibrations is not None else {
                "1": self.CALIBRATION,      # SZ161130 基金层校准值
                "4": self.MIDDLE_CALIBRATION,  # QQQ 中间层校准值
            }
            self._quote_time = quote_time
            # 假连接里的 netvaluehistory 要与 self.netvalue **保持一致** ——
            # `write_if_ready()` 的"当天真实净值是否已到"就是查这张表。
            # 不同步的话闸门测不出来（实测踩过）。
            self.connection = FakeConnection(netvalue=[
                {"id": i + 1, "stock_id": sid, "date": date, "close": close}
                for i, ((sid, date), close) in enumerate(sorted(self.netvalue.items()))
            ])
            self._ids = {"SZ161130": "1", "QQQ": "4", "USCNY": "8"}

        def get_stock_id(self, symbol):
            return self._ids.get(symbol)

        def get_fund_profile(self, symbol):
            if symbol != "SZ161130":
                return None

            class P:
                stock_id = "1"
                position = 0.95
                est_symbol = "QQQ"
                pair_symbol = None

            return P()

        def get_position(self, stock_id):
            return 0.95

        def get_latest_net_value(self, stock_id):
            from stockdata import DailyRecord

            dates = [d for (sid, d) in self.netvalue if sid == stock_id]
            if not dates:
                return None
            date = max(dates)
            return DailyRecord(date=date, close=self.netvalue[(stock_id, date)])

        def get_calibration_now(self, stock_id):
            from stockdata import DailyRecord

            value = self.calibrations.get(stock_id)
            if value is None:
                return None
            dates = [d for (sid, d) in self.netvalue if sid == stock_id]
            return DailyRecord(date=max(dates) if dates else "2026-09-29", close=value)

        def get_last_calibration(self, stock_id):
            return self.calibrations.get(stock_id)

        def get_latest_net_value_date(self, stock_id):
            dates = [d for (sid, d) in self.netvalue if sid == stock_id]
            return max(dates) if dates else None

        def get_latest_history_date(self, stock_id):
            dates = [d for (sid, d) in self.history if sid == stock_id]
            return max(dates) if dates else None

        def get_net_value_close(self, stock_id, date):
            return self.netvalue.get((stock_id, date))

        def get_history_close(self, stock_id, date):
            return self.history.get((stock_id, date))

        def get_prev_history_close(self, stock_id, date):
            dates = [d for (sid, d) in self.history if sid == stock_id and d < date]
            return self.history.get((stock_id, max(dates))) if dates else None

        def get_quote_time(self, symbol):
            return self._quote_time

        def get_latest_history_close(self, stock_id):
            dates = [d for (sid, d) in self.history if sid == stock_id]
            return self.history[(stock_id, max(dates))] if dates else None

    def _calculator(self, source=None, now=None, fresh_fundest=False):
        """构造计算器。

        ``fresh_fundest=True`` 时清空假连接里的 ``fundest`` 行
        （需要"表里已有旧记录"的场景才用得上）。**默认不清空** ——
        清空会连 ``netvaluehistory`` 一起重置（``FakeConnection`` 一次建两张表），
        导致"当天真实净值是否已到"那条闸门失效（实测踩过）。
        """
        source = source or self.Source(
            netvalue_history={("1", "2026-09-29"): 4.5452, ("4", "2026-09-30"): 1.0,
                              ("8", "2026-09-30"): 6.705},
            history={("4", "2026-09-30"): 749.58},
        )
        if fresh_fundest:
            source.connection.tables["fundest"] = []
        store = FundEstStore(source.connection)
        return EstimateCalculator(source, store, now_provider=now), store

    def test_hour_minute_gate_default_is_2055(self):
        self.assertEqual(FundEstStore.DEFAULT_GATE_HHMM, 2055)

    def test_error_threshold_matches_php(self):
        # PHP StockCompareEstResult: if (abs($fPercentage) > 1.0)
        self.assertEqual(ESTIMATE_ERROR_THRESHOLD, 1.0)

    def test_est_record_shape(self):
        record = EstRecord("2026-09-30", 4.545, "04:00:00")
        self.assertEqual(record.as_dict()["date"], "2026-09-30")
        self.assertEqual(record.as_dict()["value"], 4.545)
        self.assertEqual(record.date, "2026-09-30")
        self.assertIsNone(EstRecord("2026-09-30", 4.545).time)

    def test_compare_with_net_value_below_threshold_does_not_warn(self):
        calculator, store = self._calculator()
        store.write("1", "2026-09-30", 4.545, time="04:00:00")

        # 真实 4.5452 vs 估算 4.545 -> 偏差 -0.0044%，远小于 1%
        change = calculator.compare_with_net_value("SZ161130", 4.5452, "2026-09-30")

        self.assertIsNotNone(change)
        self.assertLess(abs(change), 1.0)
        self.assertEqual(calculator.warnings, [])

    def test_compare_with_net_value_above_threshold_warns(self):
        """误差超过 1% 要告警，对应 PHP 的 ``trigger_error()``。"""
        calculator, store = self._calculator()
        store.write("1", "2026-09-30", 5.0, time="04:00:00")

        # 真实 4.5452 vs 估算 5.0 -> +10% 左右
        change = calculator.compare_with_net_value("SZ161130", 4.5452, "2026-09-30")

        self.assertGreater(abs(change), 1.0)
        self.assertEqual(len(calculator.warnings), 1)
        self.assertIn("SZ161130", calculator.warnings[0])
        self.assertIn("误差", calculator.warnings[0])

    def test_compare_returns_none_without_record(self):
        calculator, _ = self._calculator()
        self.assertIsNone(calculator.compare_with_net_value("SZ161130", 4.5452, "2026-09-30"))

    def test_compare_returns_none_for_invalid_input(self):
        calculator, store = self._calculator()
        store.write("1", "2026-09-30", 4.545, time="04:00:00")
        self.assertIsNone(calculator.compare_with_net_value("SZ161130", None, "2026-09-30"))
        self.assertIsNone(calculator.compare_with_net_value("SZ161130", 4.5452, None))
        self.assertIsNone(calculator.compare_with_net_value("NOPE", 4.5452, "2026-09-30"))

    def test_scan_errors_walks_recorded_dates(self):
        calculator, store = self._calculator()
        # 用底层 write()（不走"净值未到"闸门），直接造两条已记录的官方 EST
        store.write("1", "2026-09-29", 4.5452, time="04:00:00")
        store.write("1", "2026-09-28", 9.9, time="04:00:00")
        # scan_errors 是从 netvaluehistory 取真实净值比对的，所以两天的净值都要在
        calculator.source.netvalue[("1", "2026-09-29")] = 4.5452
        calculator.source.netvalue[("1", "2026-09-28")] = 4.57
        calculator.source.connection.tables["netvaluehistory"].extend([
            {"id": 90, "stock_id": "1", "date": "2026-09-29", "close": 4.5452},
            {"id": 91, "stock_id": "1", "date": "2026-09-28", "close": 4.57},
        ])

        rows = calculator.scan_errors(["SZ161130"])

        # 09-29 误差极小不告警；09-28 误差超阈值会告警
        self.assertEqual(len(calculator.warnings), 1)
        self.assertIn("2026-09-28", calculator.warnings[0])
        # 两条都会返回（返回的是全部比对结果，不只告警的）
        self.assertEqual(sorted(r[1] for r in rows), ["2026-09-28", "2026-09-29"])

    def test_record_official_writes_when_netvalue_missing_for_that_date(self):
        calculator, store = self._calculator(now=lambda: "2026-10-06 04:00:00")
        # 净值表里只有 09-29；中间层 QQQ 的最新日期是 09-30
        written = calculator.record_official("SZ161130")

        self.assertTrue(written)
        self.assertEqual(store.count("1"), 1)

    def test_record_official_skips_date_that_already_has_nav(self):
        source = self.Source(
            netvalue_history={("1", "2026-09-30"): 4.5452, ("4", "2026-09-30"): 1.0,
                              ("8", "2026-09-30"): 6.705},
            history={("4", "2026-09-30"): 749.58},
        )
        calculator, store = self._calculator(source=source)

        # 09-30 已有真实净值 -> 不该写
        self.assertFalse(calculator.record_official("SZ161130"))
        self.assertEqual(store.count("1"), 0)

    def test_record_official_respects_strict_gate(self):
        """严格复刻 PHP 时传 gate_hhmm=2055，20:55 之后不写。"""
        source = self.Source(
            netvalue_history={("1", "2026-09-29"): 4.5452, ("4", "2026-09-30"): 1.0,
                              ("8", "2026-09-30"): 6.705},
            history={("4", "2026-09-30"): 749.58},
            quote_time="21:30:00",
        )
        calculator, store = self._calculator(source=source)

        self.assertFalse(calculator.record_official("SZ161130", gate_hhmm=2055))
        self.assertEqual(store.count("1"), 0)

    def test_record_official_all_reports_per_symbol(self):
        calculator, _ = self._calculator()
        result = calculator.record_official_all(["SZ161130", "NOPE"])
        self.assertIn("SZ161130", result)
        self.assertFalse(result["NOPE"])


class ThreeEstTest(unittest.TestCase):
    """三个 EST（官方 / 参考 / 实时）的区分。

    三者的关系（作者原话见 skill 第 1.4 节）：

    * **官方 EST** —— 对应 ETF 的**净值**（``_getOfficialEstVal``），用中间层日期那条汇率；
    * **参考 EST** —— 中间层**当日行情** + **当前汇率**（``GetFairNetValue``）；
    * **实时 EST** —— **参考 EST + 实时快照**（``GetRealtimeNetValue``）。

    ## 为什么必须用"输入各不相同的"测试替身

    真实演示库里三个 EST 会算出**完全相同**的值，因为：

    * 中间层当日的行情收盘价（09-30 的 749.58）恰好等于实时快照价；
    * 汇率的"最新值"（10-01）恰好就是中间层日期用的那条。

    输入相同、输出必然相同 —— 这**不能**证明实现正确。
    所以这里给三个不同的输入，验证它们确实会分开。
    """

    class Source:
        """可以分别指定"当日行情价 / 实时快照价 / 汇率日期与取值"的假数据源。"""

        CAL_FUND = 32.079543
        CAL_MIDDLE = 0.008945009205154886
        NAV = 4.5452
        #: 校准日期与校准时用的汇率（``cny_ref``）。
        #: ``_build()`` 会按它去查 ``USCNY``，所以假数据源必须提供这一天的汇率，
        #: 否则 ``cny_ref`` 会回落到"当前汇率"，参考 EST 就算不对。
        CAL_DATE = "2026-09-29"
        CAL_CNY = 6.705

        def __init__(self, mid_date="2026-10-02", mid_net_value=1.0,
                     day_quote=749.58, realtime_quote=756.20,
                     cny_date="2026-10-01", cny_current=6.705,
                     cny_on_mid_date=None, nav_date="2026-10-01",
                     cny_on_cal_date=None):
            self._ids = {"SZ161130": "1", "QQQ": "4", "USCNY": "8"}
            self._mid_date = mid_date
            self._mid_net_value = mid_net_value
            self._day_quote = day_quote
            self._realtime_quote = realtime_quote
            self._cny_date = cny_date
            self._cny_current = cny_current
            self._cny_on_mid_date = cny_on_mid_date
            self._cny_on_cal_date = cny_on_cal_date
            self._nav_date = nav_date
            self.connection = FakeConnection(
                netvalue=[
                    {"id": 1, "stock_id": "1", "date": nav_date, "close": self.NAV},
                    {"id": 2, "stock_id": "4", "date": mid_date, "close": mid_net_value},
                    {"id": 3, "stock_id": "8", "date": cny_date, "close": cny_current},
                ]
            )
            self._calibrations = {"1": self.CAL_FUND, "4": self.CAL_MIDDLE}

        # -- 数据源接口 ---------------------------------------------------
        def get_stock_id(self, symbol):
            return self._ids.get(symbol)

        def get_fund_profile(self, symbol):
            if symbol != "SZ161130":
                return None

            class P:
                stock_id = "1"
                position = 0.95
                est_symbol = "^NDX"
                pair_symbol = None

            return P()

        def get_position(self, stock_id):
            return 1.0 if stock_id == "4" else 0.95

        def get_latest_net_value(self, stock_id):
            from stockdata import DailyRecord

            if stock_id == "1":
                return DailyRecord(date=self._nav_date, close=self.NAV)
            if stock_id == "4":
                return DailyRecord(date=self._mid_date, close=self._mid_net_value)
            if stock_id == "8":
                return DailyRecord(date=self._cny_date, close=self._cny_current)
            return None

        def get_calibration_now(self, stock_id):
            from stockdata import DailyRecord

            value = self._calibrations.get(stock_id)
            # 校准日期要与 ``CAL_DATE`` 一致 —— ``_build()`` 按它去查校准时汇率
            return DailyRecord(date=self.CAL_DATE, close=value) if value else None

        def get_last_calibration(self, stock_id):
            return self._calibrations.get(stock_id)

        def get_latest_net_value_date(self, stock_id):
            record = self.get_latest_net_value(stock_id)
            return record.date if record else None

        def get_net_value_close(self, stock_id, date):
            if stock_id == "8":
                if str(date) == str(self.CAL_DATE):
                    # 校准时用的汇率（cny_ref）：``_build()`` 会来问这一天
                    return self._cny_on_cal_date if self._cny_on_cal_date is not None else self.CAL_CNY
                if str(date) == str(self._mid_date) and self._cny_on_mid_date is not None:
                    return self._cny_on_mid_date
                if str(date) == str(self._cny_date):
                    return self._cny_current
                return None
            return None

        def get_history_close(self, stock_id, date):
            if stock_id == "4" and str(date) == str(self._mid_date):
                return self._day_quote
            return None

        def get_latest_history_close(self, stock_id):
            return self._day_quote if stock_id == "4" else None

        def get_latest_history_date(self, stock_id):
            return self._mid_date if stock_id == "4" else None

        def get_est_quote(self, stock_id):
            from stockdata import DailyRecord

            if stock_id == "4":
                return DailyRecord(date=self._mid_date, close=self._realtime_quote)
            return None

        def get_quote_time(self, symbol):
            return "04:00:00"

    def _calculator(self, source):
        return EstimateCalculator(source, FundEstStore(source.connection),
                                  now_provider=lambda: "2026-10-06 04:00:00")

    def test_official_est_equals_fund_nav(self):
        """官方 EST 应当等于基金单位净值（本项目的校准口径保证这一点）。"""
        calculator = self._calculator(self.Source())

        record = calculator.compute_official("SZ161130")

        self.assertIsNotNone(record)
        self.assertAlmostEqual(record.value, self.Source.NAV, places=6)

    def test_fair_est_is_none_when_rate_date_matches_middle_date(self):
        """汇率日期与中间层日期一致时，参考 EST **按定义为空**。

        对应 ``GetFairNetValue()`` 最后那个 ``return false``：
        两者一致时参考 EST 与官方 EST 相同、没有信息量，PHP 就不显示它。
        """
        calculator = self._calculator(
            self.Source(mid_date="2026-10-02", cny_date="2026-10-02", cny_on_mid_date=6.705)
        )

        self.assertIsNone(calculator.compute_fair("SZ161130"))

    def test_fair_est_appears_when_rate_is_stale(self):
        """汇率日期落后于中间层日期时，参考 EST 才出现。"""
        calculator = self._calculator(
            self.Source(mid_date="2026-10-02", cny_date="2026-10-01",
                        cny_current=6.705, cny_on_mid_date=None)
        )

        record = calculator.compute_fair("SZ161130")

        self.assertIsNotNone(record)
        self.assertAlmostEqual(record.value, self.Source.NAV, places=6)

    def test_fair_est_moves_with_the_current_rate(self):
        """参考 EST 要用**当前**汇率 —— 这正是它与官方 EST 的差别。

        ## 预期值怎么来的（重要）

        不能"固定中间层价、只改汇率"来测方向：``cal_f`` 本身是按
        ``cny_ref`` 标定出来的，那样会造出自相矛盾的输入。

        正确算法（``cny_ref = 6.705``、``est_ref = 749.58``、基金净值 4.5452）：

        ```text
        hedge  = (nv_f × cal_f / cny_ref) × (est / est_ref) × (cny / cny_ref)
        est_f  = (1 - 0.95) × nv_f + 0.95 × hedge × cny / cal_f
        ```

        中间层价不变（749.58）、当前汇率从 6.705 降到 6.60 时：

        ```text
        est_f = 4.4110  （相对净值 -2.95%）
        ```

        再用**同一个**数据源调 `compute_official`：官方 EST 用的是**中间层日期
        那条汇率**（``cny_on_mid_date = 6.705``），所以得到 4.5452 而不是 4.4110。
        这正是两者分界的地方。
        """

        source = self.Source(mid_date="2026-10-02", cny_date="2026-10-01",
                             cny_current=6.60, cny_on_mid_date=6.705)
        calculator = self._calculator(source)

        fair = calculator.compute_fair("SZ161130")
        official = calculator.compute_official("SZ161130")

        self.assertIsNotNone(fair)
        self.assertAlmostEqual(fair.value, 4.4110, places=3)
        self.assertIsNotNone(official)
        self.assertAlmostEqual(official.value, self.Source.NAV, places=6)
        # 参考 EST ≠ 官方 EST —— 差别正来自"当前汇率"那一条
        self.assertNotAlmostEqual(fair.value, official.value, places=3)

    def test_fair_est_ignores_realtime_quote(self):
        """参考 EST 用**当日行情价**；实时快照价变了它不该动。"""
        base = dict(mid_date="2026-10-02", day_quote=749.58, cny_date="2026-10-01",
                    cny_on_mid_date=None)
        low = self._calculator(self.Source(realtime_quote=700.0, **base)).compute_fair("SZ161130")
        high = self._calculator(self.Source(realtime_quote=800.0, **base)).compute_fair("SZ161130")

        self.assertIsNotNone(low)
        self.assertAlmostEqual(low.value, high.value, places=9)

    def test_realtime_est_uses_the_realtime_snapshot(self):
        """实时 EST 用**实时快照价** —— 它变了实时 EST 必须跟着变。

        这一条正是参考 EST 与实时 EST 的分界线。
        """
        base = dict(mid_date="2026-10-02", day_quote=749.58, cny_date="2026-10-01",
                    cny_on_mid_date=None)
        low = self._calculator(self.Source(realtime_quote=700.0, **base)).compute_realtime("SZ161130")
        high = self._calculator(self.Source(realtime_quote=800.0, **base)).compute_realtime("SZ161130")

        self.assertIsNotNone(low)
        self.assertIsNotNone(high)
        self.assertLess(low.value, high.value)

    def test_three_est_differ_when_inputs_differ(self):
        """三个输入都不同时，三个 EST 应当互不相同。

        演示库的数据恰好让三者相同（当日价 = 实时价、汇率最新 = 中间层日期那条），
        所以这个用例专门构造不同输入来证明它们**确实会分开**。

        期望方向（中间层价越高 → 估值越高；实时价 780 > 当日价 749.58）：

        ```text
        官方 EST（中间层净值 1.0、中间层日期汇率 6.705） = 4.5452
        参考 EST（当日价 749.58、当前汇率 6.60）        = 4.4110
        实时 EST（实时价 780.00、当前汇率 6.60）        = 4.5808
        ```
        """

        calculator = self._calculator(
            self.Source(mid_date="2026-10-02", day_quote=749.58, realtime_quote=780.0,
                        cny_date="2026-10-01", cny_current=6.60, cny_on_mid_date=6.705)
        )

        official = calculator.compute_official("SZ161130")
        fair = calculator.compute_fair("SZ161130")
        realtime = calculator.compute_realtime("SZ161130")

        self.assertIsNotNone(official)
        self.assertIsNotNone(fair)
        self.assertIsNotNone(realtime)
        self.assertAlmostEqual(official.value, self.Source.NAV, places=6)
        self.assertAlmostEqual(fair.value, 4.4110, places=3)
        self.assertAlmostEqual(realtime.value, 4.5808, places=3)
        # 三者互不相同
        self.assertNotAlmostEqual(fair.value, realtime.value, places=3)
        self.assertNotAlmostEqual(official.value, fair.value, places=3)
        # 实时价高于当日价 -> 实时 EST 高于参考 EST
        self.assertGreater(realtime.value, fair.value)

    def test_realtime_est_is_none_without_snapshot(self):
        """中间层没有实时快照时返回 None —— 不编造。"""

        class NoSnapshot(self.Source):
            def get_est_quote(self, stock_id):
                return None

            def get_latest_history_close(self, stock_id):
                return None

        calculator = self._calculator(NoSnapshot())
        self.assertIsNone(calculator.compute_realtime("SZ161130"))


if __name__ == "__main__":
    unittest.main()
