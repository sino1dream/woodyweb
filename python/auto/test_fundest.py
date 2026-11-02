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
import os
import unittest

from calc.fundestcalc import (
    ESTIMATE_ERROR_THRESHOLD,
    EstimateCalculator,
    EstRecord,
)
from sql.fundeststore import FundEstStore, hour_minute_of


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
        from cli.fundestcli import _time_text

        self.assertEqual(_time_text(datetime.timedelta(seconds=2406)), "00:40:06")
        self.assertEqual(_time_text(datetime.timedelta(seconds=63000)), "17:30:00")

    def test_strings_pass_through(self):
        from cli.fundestcli import _time_text

        self.assertEqual(_time_text("04:00:00"), "04:00:00")
        self.assertEqual(_time_text("04:00:00.123456"), "04:00:00")

    def test_missing_shows_dash(self):
        from cli.fundestcli import _time_text

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

    def __init__(self, fundest=None, netvalue=None, holdings=None):
        self.queries = []
        self.tables = {
            "fundest": [dict(r) for r in (fundest or [])],
            "netvaluehistory": [dict(r) for r in (netvalue or [])],
            #: ``holdings`` 只用来答 ``SqlCountHoldings``（有没有持仓明细）
            "holdings": [dict(r) for r in (holdings or [])],
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
            from core.stockdata import DailyRecord

            dates = [d for (sid, d) in self.netvalue if sid == stock_id]
            if not dates:
                return None
            date = max(dates)
            return DailyRecord(date=date, close=self.netvalue[(stock_id, date)])

        def get_calibration_now(self, stock_id):
            from core.stockdata import DailyRecord

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

        def get_netvalue_close(self, stock_id, date):
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
            # 中间层符号 = profile.est_symbol（^NDX，**不做杠杆替代**，
            # 见 fundestcalc._est_symbol 修偏记录）；QQQ 保留仅为兼容。
            self._ids = {"SZ161130": "1", "^NDX": "4", "QQQ": "4", "USCNY": "8"}

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
            from core.stockdata import DailyRecord

            if stock_id == "1":
                return DailyRecord(date=self._nav_date, close=self.NAV)
            if stock_id == "4":
                return DailyRecord(date=self._mid_date, close=self._mid_net_value)
            if stock_id == "8":
                return DailyRecord(date=self._cny_date, close=self._cny_current)
            return None

        def get_calibration_now(self, stock_id):
            from core.stockdata import DailyRecord

            value = self._calibrations.get(stock_id)
            # 校准日期要与 ``CAL_DATE`` 一致 —— ``_build()`` 按它去查校准时汇率
            return DailyRecord(date=self.CAL_DATE, close=value) if value else None

        def get_last_calibration(self, stock_id):
            return self._calibrations.get(stock_id)

        def get_latest_net_value_date(self, stock_id):
            record = self.get_latest_net_value(stock_id)
            return record.date if record else None

        def get_netvalue_close(self, stock_id, date):
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
            from core.stockdata import DailyRecord

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
        ``cny_cal`` 标定出来的，那样会造出自相矛盾的输入。

        正确算法（``cny_cal = 6.705``、``est_cal = 749.58``、基金净值 4.5452）：

        ```text
        contribution = nv_f × cal_f / cny        # 除的是**当前**汇率（(A) 式）
        hedge        = contribution × (est / est_cal) × (cny / cny_cal)
        est_f        = (1 - 0.95) × nv_f + 0.95 × hedge × cny / cal_f
                     = (1 - 0.95) × nv_f + 0.95 × nv_f × (est / est_cal) × (cny / cny_cal)
        ```

        中间层价不变（749.58）、当前汇率从 6.705 降到 6.60 时：

        ```text
        est_f = 4.4776  （相对净值 -1.49%，与汇率跌幅 1:1）
        ```

        **汇率只出现一次**正是关键：``_qdii_value()`` 曾经的实现把 contribution
        除了 ``cny_cal``，汇率被算两遍，人民币贬 1.57% 会让估值涨 2.95%
        （实测 4.4110）—— 而 QDII 基金的人民币净值只该随汇率动一次
        （``README_name_and_forex.md`` §2.4① 记的是同一个坑，只是那边在
        ``_attach_hedge_value()`` 里，这边在 EST 路径上又犯了一次）。

        再用**同一个**数据源调 `compute_official`：官方 EST 用的是**中间层日期
        那条汇率**（``cny_on_mid_date = 6.705``），所以得到 4.5452 而不是 4.4776。
        这正是两者分界的地方。
        """

        source = self.Source(mid_date="2026-10-02", cny_date="2026-10-01",
                             cny_current=6.60, cny_on_mid_date=6.705)
        calculator = self._calculator(source)

        fair = calculator.compute_fair("SZ161130")
        official = calculator.compute_official("SZ161130")

        self.assertIsNotNone(fair)
        self.assertAlmostEqual(fair.value, 4.4776, places=3)
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
        参考 EST（当日价 749.58、当前汇率 6.60）        = 4.4776
        实时 EST（实时价 780.00、当前汇率 6.60）        = 4.6501
        ```

        参考 / 实时相对净值分别是 -1.49% / +2.31%：汇率那一项与跌幅 1:1
        （-1.57%），实时价高出当日价 4.06% 再叠上来 —— 两个因子各算一次。
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
        self.assertAlmostEqual(fair.value, 4.4776, places=3)
        self.assertAlmostEqual(realtime.value, 4.6501, places=3)
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


class ProductionScaleTest(unittest.TestCase):
    """**生产尺度**下的回归：中间层校准值 41.58 不再把 EST 放大 4648 倍。

    ## 这个用例为什么必须存在

    ``ThreeEstTest`` 用的假数据源是**旧种子尺度**：基金校准值 32.08、
    中间层校准值 0.008945（= 汇率/报价）。``_qdii_value()`` 曾经的实现用恒等式
    ``est_quote_ref = cny_ref / cal_m`` 反推中间层基准价，恰好只在这个尺度上成立。

    2026-10-13 按生产端口径重算校准值后（见 ``_calibration_recount.py``）：

    * 基金（SH513100）: ``cal_f = 100390.448365``
      （= ^NDX 30883.148 × 6.7035 / 2.0622，口径二，生产端验算过）；
    * 中间层（QQQ）: ``cal_m = 41.579312``（= ^NDX / QQQ 净值，口径一）。

    恒等式把基准价算成 ``6.7035 / 41.579 = 0.161``（应约 742），比值放大
    **4648 倍**，``/qdii`` 与 ``/fund`` 页官方 EST 显示成 9625（应约 2.05）。
    页面不报错、只是数字离谱 —— 所以要有用例把这个尺度钉住。

    数据全部取自演示库实测（``auto/_scratch/_est_scale_probe.py`` 的输出）。
    """

    class Source(ThreeEstTest.Source):
        """演示库 SH513100 的真实尺度；仓位按生产端该页的 1.00。"""

        CAL_FUND = 100390.448365      # = ^NDX 30883.148 * 6.7035 / 2.0622
        CAL_MIDDLE = 41.579312        # = ^NDX / QQQ 净值
        NAV = 2.0622
        CAL_DATE = "2026-10-09"
        CAL_CNY = 6.7035
        #: 中间层在校准日**之前最近一条**行情价（QQQ 2026-10-01 收盘价）。
        #: 校准日 10-09 当天 QQQ 在 stockhistory 里没有行（只到 10-01）。
        CAL_PREV_QUOTE = 742.03

        def __init__(self, **kwargs):
            kwargs.setdefault("mid_date", "2026-10-07")
            kwargs.setdefault("mid_net_value", 749.41308)   # QQQ 10-07 净值
            kwargs.setdefault("day_quote", 742.03)
            kwargs.setdefault("realtime_quote", 749.58)
            kwargs.setdefault("cny_date", "2026-10-12")
            kwargs.setdefault("cny_current", 6.6925)
            # 10-07 没有汇率报价 -> 官方 EST 退到最新汇率（与演示库一致）
            kwargs.setdefault("cny_on_mid_date", None)
            kwargs.setdefault("nav_date", "2026-10-08")
            kwargs.setdefault("cny_on_cal_date", 6.7035)
            super().__init__(**kwargs)

        def get_position(self, stock_id):
            # 生产端 SH513100 页面：「仓位估算值使用 1.00」
            return 1.0

        def get_netvalue_close(self, stock_id, date):
            # 中间层 QQQ 在 mid_date 上有净值行（父类只伺候 USCNY）
            if stock_id == "4" and str(date) == str(self._mid_date):
                return self._mid_net_value
            return super().get_netvalue_close(stock_id, date)

        def get_history_close(self, stock_id, date):
            # QQQ 的 stockhistory 只到 10-01，mid_date(10-07) 与校准日(10-09) 都没行
            return None

        def get_prev_history_close(self, stock_id, date):
            return self.CAL_PREV_QUOTE if stock_id == "4" else None

    def _calculator(self, source=None):
        source = source or self.Source()
        return EstimateCalculator(source, FundEstStore(source.connection),
                                  now_provider=lambda: "2026-10-14 04:00:00")

    def test_official_est_stays_at_nav_scale(self):
        """官方 EST 必须留在净值量级（≈2.08），而不是 9625。"""
        calculator = self._calculator()
        source = calculator.source

        record = calculator.compute_official("SZ161130")

        self.assertIsNotNone(record)
        # 独立复算（PHP 式子）：nv * (est / est_cal) * (cny / cny_cal)
        #   est      = QQQ 10-07 净值 749.41308
        #   est_cal  = QQQ 10-01 收盘价 742.03（校准日 10-09 当天没有行，退昨收）
        #   cny      = 6.6925（10-07 没有汇率报价，退最新）
        #   cny_cal  = 6.7035（10-09）
        expected = (source.NAV
                    * (749.41308 / source.CAL_PREV_QUOTE)
                    * (6.6925 / source.CAL_CNY))
        self.assertAlmostEqual(record.value, expected, places=6)
        # 兜底护栏：旧恒等式在这里会给出约 9625（放大 4648 倍）
        self.assertLess(record.value, 10.0 * source.NAV)
        self.assertGreater(record.value, 0.1 * source.NAV)

    def test_fair_est_also_stays_at_nav_scale(self):
        """参考 / 实时 EST 同样不能爆炸（它们与官方 EST 只差分子）。"""
        calculator = self._calculator()

        for name in ("compute_fair", "compute_realtime"):
            record = getattr(calculator, name)("SZ161130")
            self.assertIsNotNone(record, name)
            self.assertLess(record.value, 10.0 * self.Source.NAV, name)
            self.assertGreater(record.value, 0.1 * self.Source.NAV, name)

    def test_fx_move_scales_the_est_one_to_one(self):
        """汇率相对变化在估值里**只出现一次**。

        仓位 1.00 时 ``est_f = nv * (est / est_cal) * (cny / cny_cal)``，
        所以只改汇率时，估值应当**按同一个比例**动。曾经的实现把贡献值除了
        ``cny_cal``，汇率被算两遍，贬 1.38% 会让估值动 2.72%。
        """
        high = self._calculator(self.Source(cny_current=6.6925)).compute_fair("SZ161130")
        low = self._calculator(self.Source(cny_current=6.60)).compute_fair("SZ161130")

        self.assertIsNotNone(high)
        self.assertIsNotNone(low)
        # 估值比例 == 汇率比例（1:1），不是它的平方
        self.assertAlmostEqual(low.value / high.value, 6.60 / 6.6925, places=9)
        self.assertNotAlmostEqual(low.value / high.value, (6.60 / 6.6925) ** 2, places=6)


class InterfaceNameTest(unittest.TestCase):
    """数据源方法名漂移的护栏。

    ## 为什么需要它

    ``EstimateCalculator`` 全靠 ``getattr(self.source, "名字")`` 取数，名字写错
    不会报错，只会**静默走兜底分支**。本项目真踩过：

    ``_net_value()`` 查的是 ``get_net_value_close``（多一个下划线），而接口里的
    方法是 ``get_netvalue_close``（``netvalue`` 连写）。真实数据源上永远取不到，
    于是**净值表从来没被读过** —— 中间层净值恒 ``None``、校准时汇率恒 ``None``
    （``cny/cny_cal`` 被约成 1，汇率相对变化整项消失）。测试替身照着错名定义，
    所以单测全绿，问题只在连真实库时才现形。

    这个用例把 ``fundestcalc`` 里出现过的每个 ``getattr`` 名字与
    ``MemoryFundDataSource`` / ``FundDataSource`` 对一遍，写错名字立刻红。
    """

    #: fundestcalc.py 的路径（相对 python/auto）
    MODULE = "calc/fundestcalc.py"

    #: 只供测试注入的钩子，两个真实现上都没有（``quote_time_provider`` 的替代）。
    OPTIONAL_HOOKS = {"get_quote_time"}

    def test_every_getattr_name_resolves_on_a_real_source(self):
        """每个 ``getattr`` 名字至少要在一个真实现上存在。

        ``getattr`` 取数的写法允许"可选方法"（某个实现没有就静默走兜底），
        但**两边都没有**的名字一定是写错了。本项目真踩过：
        ``_net_value()`` 查 ``get_net_value_close``（多一个下划线），两个实现上
        都不存在，于是**净值表从来没被读过** —— 中间层净值恒 ``None``、
        校准时汇率恒 ``None``（``cny/cny_cal`` 被约成 1）。而单测全绿，
        因为测试替身照着错名定义了一份。
        """
        import re

        from core import stockdata

        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), self.MODULE)
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        names = set(re.findall(r'getattr\(self\.source,\s*"([a-z_]+)"', source))
        self.assertTrue(names, "没抓到任何 getattr 名字，正则或路径过期了")

        for name in sorted(names - self.OPTIONAL_HOOKS):
            on_memory = hasattr(stockdata.MemoryFundDataSource, name)
            on_mysql = hasattr(stockdata.MySqlFundDataSource, name)
            self.assertTrue(on_memory or on_mysql,
                            "方法名 %r 在两个真实现上都不存在（fundestcalc 在按它取数，"
                            "大概率是写错了名字）" % name)

    def test_netvalue_close_is_the_interface_spelling(self):
        """接口真名是 ``get_netvalue_close``；``getattr`` 里不允许再出现错名。"""
        import re

        from core import stockdata

        self.assertTrue(hasattr(stockdata.FundDataSource, "get_netvalue_close"))
        self.assertFalse(hasattr(stockdata.FundDataSource, "get_net_value_close"))

        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), self.MODULE)
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        # 只查 **getattr 调用**里的名字：docstring 里提一句错名是为了记录历史，
        # 不算违规；真违规的是还在按错名取数。
        looked_up = set(re.findall(r'getattr\(self\.source,\s*"([a-z_]+)"', source))
        self.assertNotIn("get_net_value_close", looked_up)
        self.assertIn("get_netvalue_close", looked_up)


class HoldingsEstValueTest(unittest.TestCase):
    """``_holdings_est_value`` —— ``qdiiref.php:114`` 的持仓分支。

    **演示库跑不到这条分支**：只有 ``SH513050`` 有持仓数据，而它不是任何 QDII
    基金的 est 标的（est 标的是 ``QQQ`` / ``^NDX`` 那一层）。所以这三个用例是
    它唯一的覆盖 —— 等真有持仓型标的入库时，行为已经是被测过的。
    """

    class Source:
        """只实现 ``count_holdings`` 用得到的两样：``connection`` 与 id 查表。"""

        def __init__(self, connection=None, ids=None):
            self.connection = connection
            self._ids = ids or {"QQQ": "4"}

        def get_stock_id(self, symbol):
            return self._ids.get(symbol)

    def _calc(self, holdings=None):
        connection = FakeConnection(holdings=holdings)
        source = self.Source(connection)
        return EstimateCalculator(source, FundEstStore(connection),
                                  now_provider="04:00")

    def test_symbol_without_holdings_returns_none(self):
        """没有持仓明细 -> ``None``，让调用方继续走"行情价"那条分支。"""
        calc = self._calc(holdings=[])
        self.assertIsNone(calc._holdings_est_value("QQQ", "2026-10-14"))

    def test_without_connection_returns_none(self):
        """纯内存数据源（没有连接）-> ``None``，不抛异常。"""
        source = self.Source(connection=None)
        calc = EstimateCalculator(source, FundEstStore(FakeConnection()),
                                  now_provider="04:00")
        self.assertIsNone(calc._holdings_est_value("QQQ", "2026-10-14"))

    def test_holdings_symbol_returns_est_net_value(self):
        """持仓型 -> 返回 ``$est_ref->_estNetValue($strDate)``，且带上了日期。"""
        import ref.holdingsref

        original = ref.holdingsref.HoldingsReference
        box = {}

        class FakeRef:
            def __init__(self, symbol, source=None):
                box["ref"] = self
                self.symbol = symbol
                self.source = source
                self.calls = []

            def _est_net_value(self, date=None, realtime=False):
                self.calls.append(date)
                return 1.0263

        ref.holdingsref.HoldingsReference = FakeRef
        try:
            calc = self._calc(holdings=[
                {"id": 1, "stock_id": "4", "holding_id": 5, "ratio": 30.0},
            ])
            value = calc._holdings_est_value("QQQ", "2026-10-14")
        finally:
            ref.holdingsref.HoldingsReference = original

        self.assertEqual(value, 1.0263)
        #: 官方 EST 传的是**那天**的日期，不是 None（``_getFairEstVal`` 才不传）
        self.assertEqual(box["ref"].calls, ["2026-10-14"])


class MiddleDateOrderTest(unittest.TestCase):
    """``_latest_date()`` 必须**行情优先**，不能净值优先。

    ## 这条守护的是一整串真实症状（2026-10-20 实测 SZ161125）

    中间层 ``SPY`` 的行情快照已经到 ``2026-10-19``，但它的**净值**只到
    ``2026-10-16``。旧实现先取净值表，于是：

    * 页面「EST日期」显示 ``10-16``，生产端是 ``10-19``
      （PHP 用 ``$est_ref->GetDate()``，那是**行情**日期）；
    * ``est_value``（取 ``mid_date`` 的净值）与 ``est_quote_ref``
      （校准日的净值，恰好也是 10-16）撞成同一个值 → 比值退化成 1.0 →
      **官方 EST 恒等于基金净值**（3.1206），而生产端是 3.132。

    修好后官方 EST 3.1326，与生产端 3.132 一致。
    """

    class _Quote:
        def __init__(self, price):
            self.price = price

    class Source:
        """最小数据源：只需回答"各表最新日期"与"即期快照"。"""

        def __init__(self, quote_date=None, history_date=None, netvalue_date=None,
                     spot=None):
            self.quote_date = quote_date
            self.history_date = history_date
            self.netvalue_date = netvalue_date
            self.spot = spot

        def get_stored_quote_date(self, symbol):
            return self.quote_date

        def get_latest_history_date(self, stock_id):
            return self.history_date

        def get_latest_net_value_date(self, stock_id):
            return self.netvalue_date

        def get_stored_quote(self, symbol):
            return MiddleDateOrderTest._Quote(self.spot) if self.spot else None

        def get_stock_id(self, symbol):
            return "20" if symbol == "fx_susdcny" else None

        def get_latest_net_value_close(self, stock_id):
            return self.spot

    @staticmethod
    def _calc(**kwargs):
        from calc.fundestcalc import EstimateCalculator

        return EstimateCalculator(MiddleDateOrderTest.Source(**kwargs), store=None)

    def test_snapshot_date_wins_over_netvalue(self):
        """快照日期（10-19）必须压过净值日期（10-16）—— 本次修复的核心。"""
        calc = self._calc(quote_date="2026-10-19", history_date="2026-10-16",
                          netvalue_date="2026-10-16")
        self.assertEqual(calc._latest_date("5", "SPY"), "2026-10-19")

    def test_history_beats_netvalue_without_snapshot(self):
        """没有快照时行情表优先于净值表（同样是为了对齐 ``GetDate()``）。"""
        calc = self._calc(quote_date=None, history_date="2026-10-16",
                          netvalue_date="2026-10-16")
        self.assertEqual(calc._latest_date("5", "SPY"), "2026-10-16")

    def test_netvalue_is_the_last_resort(self):
        calc = self._calc(quote_date=None, history_date=None,
                          netvalue_date="2026-10-16")
        self.assertEqual(calc._latest_date("5", "SPY"), "2026-10-16")

    def test_returns_none_when_nothing_known(self):
        calc = self._calc()
        self.assertIsNone(calc._latest_date("5", "SPY"))

    def test_symbol_is_optional(self):
        """老调用方只给 stock_id 时跳过快照那一步，不应抛异常。"""
        calc = self._calc(quote_date="2026-10-19", history_date="2026-10-16")
        self.assertEqual(calc._latest_date("5"), "2026-10-16")


class FairCnyTest(unittest.TestCase):
    """``_fair_cny()``：**ETF 用即期、LOF 用中间价**（``GetForexRef()``）。

    PHP 原注释的理由：**ETF 可以按盘中汇率实时申赎，LOF 只能按当天中间价**。
    两者差约 0.6%（``USCNY`` 6.72 vs ``fx_susdcny`` 6.679），
    正好是之前参考 EST 对不上生产端的那 0.6%。
    """

    @staticmethod
    def _calc(spot=None):
        from calc.fundestcalc import EstimateCalculator

        return EstimateCalculator(MiddleDateOrderTest.Source(spot=spot), store=None)

    def test_lof_uses_middle_rate(self):
        """LOF（SZ161125）用中间价 —— 即期那 6.679 不该被用上。"""
        calc = self._calc(spot=6.679)
        self.assertEqual(calc._fair_cny("SZ161125", 6.72), 6.72)

    def test_etf_uses_spot_rate(self):
        """ETF（SH513100）用即期 —— 这正是参考 EST 偏低 0.6% 的来源。"""
        calc = self._calc(spot=6.679)
        self.assertEqual(calc._fair_cny("SH513100", 6.72), 6.679)

    def test_etf_falls_back_to_middle_without_spot(self):
        """即期取不到时退回中间价（对应 PHP ``GetPrice()`` 兜底）。"""
        calc = self._calc(spot=None)
        self.assertEqual(calc._fair_cny("SH513100", 6.72), 6.72)

    def test_lof_never_reads_spot(self):
        """LOF 分支不该碰即期表 —— 用即期会算错。"""
        calc = self._calc(spot=6.679)
        self.assertNotEqual(calc._fair_cny("SZ162411", 6.72), 6.679)

    # -- 地区分派 ---------------------------------------------------------
    class _SpotSource:
        """按符号回答即期价的假源 —— 用来验"哪个地区配哪个即期"。

        只实现 ``_spot_cny()`` 用到的两个方法（``get_stock_id`` /
        ``get_stored_quote``），不装全套。
        """

        class _Quote:
            def __init__(self, price):
                self.price = price

        def __init__(self, prices):
            self.prices = dict(prices)

        def get_stock_id(self, symbol):
            return symbol if symbol in self.prices else None

        def get_stored_quote(self, symbol):
            price = self.prices.get(symbol)
            return self._Quote(price) if price is not None else None

    def _hk_calc(self, prices):
        from calc.fundestcalc import EstimateCalculator

        return EstimateCalculator(self._SpotSource(prices), store=None)

    def test_spot_symbol_follows_the_region(self):
        """中间价 -> 即期的对应关系来自 ``QDII_REGIONS``，不是写死美元。"""
        calc = self._calc()
        self.assertEqual(calc._spot_symbol_of("USCNY"), "fx_susdcny")
        self.assertEqual(calc._spot_symbol_of("HKCNY"), "fx_shkdcny")
        self.assertEqual(calc._spot_symbol_of("JPCNY"), "fx_sjpycny")
        self.assertEqual(calc._spot_symbol_of("EUCNY"), "fx_seurcny")
        self.assertIsNone(calc._spot_symbol_of("XXXX"))

    def test_hk_etf_uses_hk_spot(self):
        """港股 ETF 的参考 EST 该用港币即期 —— 配 ``fx_susdcny`` 差一个汇率。"""
        calc = self._hk_calc({"fx_susdcny": 6.679, "fx_shkdcny": 0.8562})
        self.assertEqual(calc._fair_cny("SZ159920", 0.85594, "HKCNY"), 0.8562)

    def test_default_cny_symbol_stays_usd(self):
        """不传 ``cny_symbol`` 时仍走美元 —— 美股链路的行为不能变。"""
        calc = self._calc(spot=6.679)
        self.assertEqual(calc._fair_cny("SH513100", 6.72), 6.679)


class ExplainTest(unittest.TestCase):
    """``EstimateCalculator.explain()`` —— 页面「算法说明」那一段的数据源。

    ## 为什么值得单独钉住

    那段说明是**写给用户核对数值**的。它一旦与实现不一致（公式写错、
    或者标错哪个数是哪一档用的），危害比没有说明更大 —— 用户会按错误的
    公式去验算，然后得出"实现是对的"这个错误结论。

    所以这里最重要的一条是 :meth:`test_values_match_documented_formula`：
    **按说明里那行公式手算，必须等于实现给出的 EST**。

    替身复用 :class:`ThreeEstTest.Source`（三个 EST 的输入各不相同 ——
    输入相同的话三档会算出同一个值，测不出差别）。
    """

    def _calculator(self, source):
        return EstimateCalculator(source, FundEstStore(source.connection),
                                  now_provider=lambda: "2026-10-06 04:00:00")

    def test_three_tiers_named_in_order(self):
        raw = self._calculator(ThreeEstTest.Source()).explain("SZ161130")

        self.assertIsNotNone(raw)
        self.assertEqual([t["name"] for t in raw["tiers"]],
                         ["官方EST", "参考EST", "实时EST"])

    def test_values_match_documented_formula(self):
        """页面上写给用户看的公式，必须就是实现用的那条。

        ``EST = (1 − p) × nv + p × nv × (est ÷ est_cal) × (cny ÷ cny_cal)``

        三个 EST 共用 nv / est_cal / cny_cal，差别只在 (est, cny) 这一对 ——
        这条测试同时钉住"三档同源"这件事。
        """
        raw = self._calculator(ThreeEstTest.Source()).explain("SZ161130")
        position = raw["position"]
        netvalue = raw["netvalue"]
        est_cal = raw["est_cal"]
        cny_cal = raw["cny_cal"]
        self.assertIsNotNone(est_cal)
        self.assertIsNotNone(cny_cal)

        checked = 0
        for tier in raw["tiers"]:
            if tier["est"] is None or tier["cny"] is None or tier["value"] is None:
                continue
            expected = ((1.0 - position) * netvalue
                        + position * netvalue
                        * (tier["est"] / est_cal) * (tier["cny"] / cny_cal))
            self.assertAlmostEqual(tier["value"], expected, places=6,
                                   msg="%s 与页面公式不符" % tier["name"])
            checked += 1
        self.assertGreaterEqual(checked, 2, "至少官方与实时两档要能验算")

    def test_official_and_realtime_differ_on_the_underlying_price(self):
        """官方用中间层当日价、实时用实时快照 —— 输入不同，值就该不同。"""
        raw = self._calculator(ThreeEstTest.Source()).explain("SZ161130")
        official = raw["tiers"][0]
        realtime = raw["tiers"][2]

        self.assertNotEqual(official["est"], realtime["est"])
        self.assertNotEqual(official["value"], realtime["value"])

    def test_est_cal_date_is_the_date_actually_used(self):
        """基准价的标签日期是**实际取到的那天**，不是"想取的那天"。

        ``_est_quote_ref()`` 有四级兜底；若标签写的是校准日而实际取的是
        中间层最新日，用户按标签去查那天的行情会对不上 —— 说明反而害人。
        """
        source = ThreeEstTest.Source()
        raw = self._calculator(source).explain("SZ161130")

        # 校准日 2026-09-29 那天中间层没有数据，实际取的是中间层最新日
        self.assertEqual(str(raw["est_cal_date"]), str(source._mid_date))

    def test_returns_none_without_any_rate(self):
        """汇率完全取不到时整段不显示（页面据此跳过说明块）。"""

        class NoCny(ThreeEstTest.Source):
            def get_latest_net_value(self, stock_id):
                return None if stock_id == "8" else super().get_latest_net_value(stock_id)

        self.assertIsNone(self._calculator(NoCny()).explain("SZ161130"))


class EstSymbolNoLeverageSubstitutionTest(unittest.TestCase):
    """``_est_symbol()`` 必须返回基金档案的原始 est 符号，**不做杠杆替代**。

    回归钉子（2026-10-20 修偏）：旧实现拿 ``get_leverage_hedge_symbol()``
    把 ``^NDX`` 换成 ``QQQ`` 再取数，与 PHP ``QdiiGetEstSymbol()``
    （``qdiiref.php:243`` → ``FundPairReference('^NDX')``）不一致。
    生产端 SZ161130 官方 EST 4.418 只有 ^NDX 模型能复现，QQQ 模型给 4.420。
    """

    def test_returns_the_index_symbol_not_the_leverage_etf(self):
        from core.stockdata import get_leverage_hedge_symbol

        source = ThreeEstTest.Source()
        calculator = EstimateCalculator(source, FundEstStore(source.connection),
                                        now_provider=lambda: "2026-10-06 04:00:00")

        # 前提：SZ161130 的杠杆替代确实是 QQQ；档案里 est_symbol 是 ^NDX
        self.assertEqual(get_leverage_hedge_symbol("SZ161130"), "QQQ")
        self.assertEqual(calculator._est_symbol("SZ161130"), "^NDX")

    def test_resolve_uses_the_index_id(self):
        source = ThreeEstTest.Source()
        calculator = EstimateCalculator(source, FundEstStore(source.connection),
                                        now_provider=lambda: "2026-10-06 04:00:00")

        ctx = calculator._resolve("SZ161130")
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx["est_symbol"], "^NDX")
        self.assertEqual(ctx["est_id"], "4")

    def test_profile_without_est_symbol_falls_back_to_pair_symbol(self):
        class PairProfile(ThreeEstTest.Source):
            def get_fund_profile(self, symbol):
                class P:
                    stock_id = "1"
                    position = 0.95
                    est_symbol = None
                    pair_symbol = "^GSPC"

                return P() if symbol == "SZ161130" else None

        source = PairProfile()
        calculator = EstimateCalculator(source, FundEstStore(source.connection),
                                        now_provider=lambda: "2026-10-06 04:00:00")
        self.assertEqual(calculator._est_symbol("SZ161130"), "^GSPC")


if __name__ == "__main__":
    unittest.main()
