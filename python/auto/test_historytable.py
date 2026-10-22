"""``historytable.py`` 的单元测试。

对应 PHP：
* 显示口径 —— ``php/stock.php``（``StockGetPriceDisplay`` / ``GetNumberDisplay`` /
  ``GetRatioDisplay``）与 ``php/ui/stockcomm.php``（``GetTurnoverDisplay``）；
* 取数语义 —— ``php/sql/sqldailyclose.php`` 与 ``php/sql/sqlstocksymbol.php``。

这些口径直接决定页面上显示的数字，所以逐条对照 PHP 行为验证。
"""

from __future__ import annotations

import datetime
import unittest

from sql.historytable import (
    CALIBRATION_HEDGE_MULTIPLIERS,
    MIN_FLOAT_VAL,
    TABLE_COMMON_DISPLAY,
    CalibrationSql,
    DailyCloseSql,
    FundEstSql,
    NetValueHistorySql,
    SharesHistorySql,
    StockHistorySql,
    TableRow,
    calibration_hedge_multiplier,
    format_hm,
    format_number_display,
    format_percentage_display,
    format_price_display,
    format_ratio_display,
    format_turnover_display,
    is_table_common_display,
    percentage_change,
    stock_calc_hedge,
    use_same_day_net_value,
)


class FakeCursor:
    """记录 SQL 并回放预置结果的假游标。"""

    def __init__(self, connection):
        self.connection = connection
        self.rows = []
        self.sql = ""
        self.params = ()

    def execute(self, sql, params=()):
        self.sql = sql
        self.params = params
        self.connection.queries.append((sql, params))
        self.rows = self.connection.dispatch(sql, params)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class FakeConnection:
    """假连接：按 SQL 特征分发。

    只实现 ``historytable`` 真正会发的那几种查询（COUNT / SELECT ... WHERE
    stock_id / ... AND date = / ... AND date <），够用且能验证生成的 SQL。
    """

    def __init__(self, rows_by_table=None, counts=None):
        self.queries = []
        self.rows_by_table = rows_by_table or {}
        self.counts = counts or {}

    def cursor(self):
        return FakeCursor(self)

    def dispatch(self, sql, params):
        table = self._table_of(sql)
        rows = list(self.rows_by_table.get(table, []))
        if "COUNT(*)" in sql:
            stock_id = params[0] if params else None
            return [{"n": self.counts.get(table, {}).get(stock_id, len(rows))}]
        if sql.strip().startswith("SELECT") and "WHERE" in sql:
            stock_id = params[0]
            rows = [r for r in rows if str(r.get("stock_id")) == str(stock_id)]
            if "`date` = %s" in sql:
                date = str(params[1])
                rows = [r for r in rows if str(r.get("date")) == date]
                rows = rows[:1]
            elif "`date` < %s" in sql:
                date = str(params[1])
                rows = [r for r in rows if str(r.get("date")) < date]
                rows.sort(key=lambda r: str(r["date"]), reverse=True)
                rows = rows[:1]
            else:
                rows.sort(key=lambda r: str(r["date"]), reverse=True)
                if "LIMIT" in sql:
                    import re

                    match = re.search(r"LIMIT (\d+)(?:, (\d+))?", sql)
                    if match:
                        if match.group(2) is not None:
                            start, count = int(match.group(1)), int(match.group(2))
                            rows = rows[start:start + count]
                        else:
                            rows = rows[:int(match.group(1))]
        return rows

    @staticmethod
    def _table_of(sql):
        import re

        match = re.search(r"FROM\s+`([^`]+)`", sql)
        return match.group(1) if match else ""


class NumberDisplayTest(unittest.TestCase):
    """数字格式化，对照 PHP ``StockGetPriceDisplay`` 的 number_format + rtrim。"""

    def test_trims_trailing_zeros_like_php(self):
        # PHP: number_format(1.500, 3) = '1.500' -> rtrim('0') = '1.5'
        self.assertEqual(format_number_display(1.5, 3), "1.5")
        self.assertEqual(format_number_display(1.0, 3), "1")
        self.assertEqual(format_number_display(1.234, 3), "1.234")

    def test_precision_zero_returns_integer_text(self):
        # PHP number_format(x, 0) 没有小数点
        self.assertEqual(format_number_display(1358.4, 0), "1358")
        self.assertEqual(format_number_display(1358.6, 0), "1359")

    def test_negative_zero_is_shown_as_zero(self):
        """PHP 的 ``number_format`` 对"四舍五入后落在负零"的值**不给负号**。

        实测（PHP 8.0.10）::

            number_format(-0.004, 2) = '0.00'      ← 没有 '-'
            number_format(-0.006, 2) = '-0.01'

        而 Python 的 ``'%.2f' % -0.004`` 是 ``'-0.00'``，rtrim 之后是 ``'-0'``。
        这一小段区间（``-0.005 < x < 0``）在页面上是"≈0 的手续费 / 盈亏"，
        显示成 ``-0`` 会让人以为算错了符号。
        是 ``_php_transaction_compare.py`` 逐例对照抓出来的。
        """
        self.assertEqual(format_number_display(-0.004), "0")
        self.assertEqual(format_number_display(-0.001), "0")
        self.assertEqual(format_number_display(-0.0), "0")
        # 真正跨过半位的值仍然带负号
        self.assertEqual(format_number_display(-0.006), "-0.01")
        self.assertEqual(format_number_display(-0.5), "-0.5")
        # 精度 0 那一支同样如此（number_format(-0.4) = '0'）
        self.assertEqual(format_number_display(-0.4, 0), "0")

    def test_ratio_display_uses_four_digits_by_default(self):
        # PHP GetRatioDisplay 精度 4
        self.assertEqual(format_ratio_display(1.23456789), "1.2346")
        self.assertEqual(format_ratio_display(1.0), "1")

    def test_empty_values_render_blank(self):
        # 缺数据要留白，不能显示 0 —— 0 会被误读成真实值
        for value in (None, ""):
            self.assertEqual(format_number_display(value), "")
            self.assertEqual(format_ratio_display(value), "")
            self.assertEqual(format_percentage_display(value, 1.0), "")
            self.assertEqual(format_turnover_display(value, 1.0), "")


class PriceDisplayTest(unittest.TestCase):
    """涨跌配色，对照 PHP ``StockGetPriceDisplay``（php/stock.php:91）。"""

    def test_up_is_red_down_is_green_flat_is_grey(self):
        # 容差 0.5/10^3 = 0.0005
        self.assertEqual(format_price_display(4.796, 4.727), ("4.796", "red"))
        self.assertEqual(format_price_display(3.240, 3.275), ("3.24", "green"))
        # 1.0004 与 1.0 的差 0.0004 < 0.0005 —— 视为持平
        self.assertEqual(format_price_display(1.0004, 1.0), ("1", "grey"))

    def test_tolerance_scales_with_precision(self):
        # precision=2 时容差 0.005：0.001 的差仍算持平
        self.assertEqual(format_price_display(1.001, 1.0, precision=2)[1], "grey")
        # 0.01 的差就超过容差，算涨
        self.assertEqual(format_price_display(1.01, 1.0, precision=2)[1], "red")

    def test_without_prev_there_is_no_color(self):
        text, color = format_price_display(4.796)
        self.assertEqual(text, "4.796")
        self.assertEqual(color, "black")


class PercentageTest(unittest.TestCase):
    """涨跌幅，复用 PHP ``StockGetPercentage``。"""

    def test_percentage_text_matches_php_rtrim(self):
        # (0.936 / 0.9338 - 1) * 100 = 0.2355...% -> '0.24%'
        self.assertEqual(format_percentage_display(0.9338, 0.936), "0.24%")
        # 整百分比去掉小数尾巴：1.80 -> '1.8%'
        self.assertEqual(format_percentage_display(3.24, 3.275), "1.08%")
        self.assertEqual(format_percentage_display(100.0, 101.8), "1.8%")

    def test_zero_change_displays_zero(self):
        self.assertEqual(format_percentage_display(1.0, 1.0), "0")

    def test_threshold_zeroes_below_half_of_last_digit(self):
        # PHP GetPercentageString()（stockref.php:129）的阈值归零：
        #   |涨幅% × 基准| < 50 / 10^precision  ->  '0'
        # 等价 |值差| < 0.5×10^-precision（涨幅% × 基准 = 值差 × 100）。
        # 三组实数取自 SZ161130「基金溢价记录」误差列（2026-10-20 对照生产端
        # sz161130cn.php 修掉的差异，精度 3 = 基金符号 GetPrecision()）：
        # 10-19 行 |change×prev| = 0.025 < 0.05 -> 归零
        self.assertEqual(format_percentage_display(4.3704, 4.37015, 3), "0")
        # 10-08 行 0.0308 < 0.05 -> 归零
        self.assertEqual(format_percentage_display(4.5515, 4.551192, 3), "0")
        # 10-13 行 0.0529 > 0.05 -> 阈外，保留 -0.01%
        self.assertEqual(format_percentage_display(4.586, 4.585471, 3), "-0.01%")

    def test_threshold_uses_the_ref_precision(self):
        # 同一组数值，精度决定阈值松紧：精度 2（50/10^2 = 0.5）连 10-13 行也归零。
        # 生产端上基金是 3 位（stocksymbol.php:994），所以那一行才显示 -0.01%。
        self.assertEqual(format_percentage_display(4.586, 4.585471, 3), "-0.01%")
        self.assertEqual(format_percentage_display(4.586, 4.585471, 2), "0")
        # 外汇精度 4（阈值 0.005）更松，0.025 的积不归零
        self.assertEqual(format_percentage_display(4.3704, 4.37015, 4), "-0.01%")

    def test_no_precision_keeps_legacy_behaviour(self):
        # precision=None 不做阈值判定（旧调用兼容）：10-19 行那种小误差照旧显示
        self.assertEqual(format_percentage_display(4.3704, 4.37015), "-0.01%")

    def test_zero_divisor_returns_none_not_zero_percent(self):
        # PHP 在 |divisor| <= MIN_FLOAT_VAL 时返回 0.0；
        # 这里返回 None 让页面留白 —— 显示 '0%' 会被误读成真的没涨跌
        self.assertIsNone(percentage_change(0.0, 1.0))
        self.assertIsNone(percentage_change(MIN_FLOAT_VAL / 2, 1.0))
        self.assertEqual(percentage_change(1.0, 1.1), 10.000000000000009)


class TurnoverTest(unittest.TestCase):
    """换手率，对照 PHP ``GetTurnoverDisplay``（php/ui/stockcomm.php:70）。"""

    def test_shares_unit_is_ten_thousand(self):
        # 作者文档里的真实数值：79553.18 万份，成交 62923045 股 -> 7.91%
        self.assertEqual(format_turnover_display(62923045, 79553.18), "7.91%")
        self.assertEqual(format_turnover_display(46053204, 83743.66), "5.5%")
        self.assertEqual(format_turnover_display(100639039, 93182.92), "10.8%")

    def test_missing_share_returns_blank(self):
        self.assertEqual(format_turnover_display(1000, 0), "")
        self.assertEqual(format_turnover_display(1000, None), "")

    def test_precision_zero_for_new_share_turnover(self):
        # PHP 新增换手用精度 0（fundshareparagraph.php:22）。
        # 换算：100 * volume / (shares * 10000)；shares 单位是万份。
        # volume=1000000、shares=1 万份 -> 100*1000000/10000 = 10000%
        self.assertEqual(format_turnover_display(1000000.0, 1.0, 0), "10000%")
        # 精度 0 时结果不含小数点
        self.assertNotIn(".", format_turnover_display(1000000.0, 1.0, 0))


class HmTest(unittest.TestCase):
    """``GetHM`` 取前 5 个字符，且要能处理 MySQL TIME 列的 timedelta。"""

    def test_plain_string(self):
        self.assertEqual(format_hm("17:30:00"), "17:30")
        self.assertEqual(format_hm("00:12:34"), "00:12")

    def test_timedelta_from_mysql_time_column(self):
        # PyMySQL 把 TIME 列取成 timedelta（实测 17:30:00 -> 63000 秒）
        self.assertEqual(format_hm(datetime.timedelta(seconds=63000)), "17:30")
        self.assertEqual(format_hm(datetime.timedelta(seconds=54000)), "15:00")

    def test_time_and_datetime_objects(self):
        self.assertEqual(format_hm(datetime.time(9, 15, 0)), "09:15")
        self.assertEqual(format_hm(datetime.datetime(2026, 10, 1, 9, 15, 30)), "09:15")

    def test_empty(self):
        self.assertEqual(format_hm(None), "")
        self.assertEqual(format_hm(""), "")


class CommonDisplayTest(unittest.TestCase):
    """``IsTableCommonDisplay``（php/ui/table.php:6）与 TABLE_COMMON_DISPLAY=10。"""

    def test_default_page_is_common_display(self):
        self.assertTrue(is_table_common_display(0, TABLE_COMMON_DISPLAY))
        self.assertTrue(is_table_common_display(0, 5))

    def test_shifted_or_larger_page_is_not_common(self):
        self.assertFalse(is_table_common_display(10, 10))
        self.assertFalse(is_table_common_display(0, 20))
        # num=0（全部记录）时 0 <= 10 成立，PHP 同样返回 true
        self.assertTrue(is_table_common_display(0, 0))


class TableRowTest(unittest.TestCase):
    def test_add_accepts_text_and_text_color_pairs(self):
        row = TableRow()
        row.add("2026-09-30").add(("4.796", "red")).add(None)
        self.assertEqual(row.cells[0], "2026-09-30")
        self.assertEqual(row.cells[1], ("4.796", "red"))
        self.assertEqual(row.cells[2], "")


class HedgeTest(unittest.TestCase):
    """``StockCalcHedge`` 与校准记录的倍数表。"""

    def test_hedge_is_calibration_over_position(self):
        self.assertAlmostEqual(stock_calc_hedge(1289.731, 0.9338), 1381.1645, places=3)

    def test_invalid_inputs_return_none(self):
        self.assertIsNone(stock_calc_hedge(None, 1.0))
        self.assertIsNone(stock_calc_hedge(100.0, None))
        self.assertIsNone(stock_calc_hedge(100.0, 0))

    def test_production_hedge_vectors(self):
        """用**生产端公开页面**的校准记录反证公式与仓位。

        生产端基金页（如 ``sz161130cn.php``）的「校准记录」表给出
        日期 / 校准值 / 时间 / 次数 / 净值 / 对冲值。对冲值就是
        ``StockCalcHedge() = 校准值 / 仓位`` 后取整（``format_number_display(x, 0)``）。

        这组向量还顺带验证了**仓位**：反推 ``校准值 / 对冲值`` 得到的仓位，
        必须与 ``StockSymbol::GetDefaultPosition()``（LOF 0.95 / 其余 1.0）一致。
        SH513100 是 ETF，生产用的是 1.0 —— 种子里曾错写 0.98，就是靠这组
        向量发现的。
        """
        # (符号, 校准值, 仓位, 生产页面显示的对冲值)
        vectors = (
            ("SZ161130", 44997.025, 0.95, "47365"),
            ("SZ161125", 16505.969, 0.95, "17375"),
            ("SH513100", 100358.446, 1.00, "100358"),
            ("SZ162411", 1289.731, 0.95, "1358"),
        )
        for symbol, calibration, position, expected in vectors:
            hedge = stock_calc_hedge(calibration, position)
            self.assertIsNotNone(hedge, symbol)
            self.assertEqual(format_number_display(hedge, 0), expected, symbol)

    def test_production_positions_match_get_default_position(self):
        """由生产对冲值反推的仓位，应与 GetDefaultPosition() 一致。

        公式：仓位 = 校准值 / 对冲值。
        """
        from core.stocksymbol import StockSymbol

        vectors = (
            ("SZ161130", 44997.025, 47365),
            ("SZ161125", 16505.969, 17375),
            ("SH513100", 100358.446, 100358),
            ("SZ162411", 1289.731, 1358),
        )
        for symbol, calibration, hedge in vectors:
            derived = calibration / hedge
            expected = StockSymbol(symbol).get_default_position()
            # 生产显示的对冲值取了整，所以只比到小数点后两位
            self.assertAlmostEqual(round(derived, 2), expected, places=2, msg=symbol)

    def test_multiplier_table_matches_php(self):
        # 对应 calibrationhistoryparagraph.php:45-65
        for symbol in ("SH518800", "SH518880", "SZ159934", "SZ159937"):
            self.assertEqual(calibration_hedge_multiplier(symbol), 1000)
        self.assertEqual(calibration_hedge_multiplier("SZ159985"), 10)
        self.assertEqual(calibration_hedge_multiplier("SZ161226"), 15)
        self.assertEqual(calibration_hedge_multiplier("SZ161130"), 1)
        self.assertEqual(sorted(CALIBRATION_HEDGE_MULTIPLIERS), sorted(
            ["SH518800", "SH518880", "SZ159934", "SZ159937", "SZ159985", "SZ161226"]))


class UseSameDayNetValueTest(unittest.TestCase):
    """``UseSameDayNetValue``（php/stock.php:381）。"""

    def test_qdii_uses_previous_day(self):
        # QDII 的当天净值要等美股收盘后才出，所以配对时用前一日
        self.assertFalse(use_same_day_net_value("SZ161130", True, True, False))

    def test_hk_mix_uses_same_day(self):
        self.assertTrue(use_same_day_net_value("SZ164906", False, True, True))

    def test_non_hk_mix_uses_previous_day(self):
        self.assertFalse(use_same_day_net_value("SH513050", False, True, False))

    def test_ordinary_symbol_uses_same_day(self):
        self.assertTrue(use_same_day_net_value("SH600000", False, False, False))


class DailyCloseSqlTest(unittest.TestCase):
    """取数语义，对照 PHP ``DailyCloseSql`` / ``_SqlBuildLimit``。"""

    def _rows(self):
        return [
            {"id": 1, "stock_id": "1", "date": "2026-09-30", "close": 4.796},
            {"id": 2, "stock_id": "1", "date": "2026-09-29", "close": 4.727},
            {"id": 3, "stock_id": "1", "date": "2026-09-28", "close": 4.700},
        ]

    def _table(self):
        connection = FakeConnection({"stockhistory": self._rows()})
        return StockHistorySql(connection), connection

    def test_orders_by_date_desc(self):
        table, connection = self._table()
        rows = table.get_all("1")
        self.assertEqual([r["date"] for r in rows],
                         ["2026-09-30", "2026-09-29", "2026-09-28"])
        self.assertIn("ORDER BY `date` DESC", connection.queries[-1][0])

    def test_default_columns_include_stock_id(self):
        # 行处理要用 stock_id 去查上一交易日/成交量，漏了会 KeyError
        table, connection = self._table()
        table.get_all("1")
        self.assertIn("`stock_id`", connection.queries[-1][0])

    def test_limit_semantics_match_php(self):
        table, connection = self._table()
        # start=0, num=2 -> LIMIT 2
        table.get_all("1", 0, 2)
        self.assertIn("LIMIT 2", connection.queries[-1][0])
        # start=1, num=2 -> LIMIT 1, 2
        table.get_all("1", 1, 2)
        self.assertIn("LIMIT 1, 2", connection.queries[-1][0])
        # num=0 且 start=0 -> 不加 limit（全部记录）
        table.get_all("1", 0, 0)
        self.assertNotIn("LIMIT", connection.queries[-1][0])

    def test_get_close_prev_uses_strictly_less_than(self):
        table, connection = self._table()
        # "上一个交易日"必须是 date < 而不是 <=，否则会返回当天
        self.assertEqual(table.get_close_prev("1", "2026-09-30"), 4.727)
        self.assertIn("`date` < %s", connection.queries[-1][0])

    def test_get_date_prev(self):
        table, _ = self._table()
        self.assertEqual(table.get_date_prev("1", "2026-09-30"), "2026-09-29")
        self.assertIsNone(table.get_date_prev("1", "2026-09-28"))

    def test_count(self):
        connection = FakeConnection({"stockhistory": self._rows()},
                                    counts={"stockhistory": {"1": 3}})
        table = StockHistorySql(connection)
        self.assertEqual(table.count("1"), 3)


class StockHistorySqlTest(unittest.TestCase):
    def _table(self):
        rows = [{"id": 1, "stock_id": "1", "date": "2026-09-30", "close": 4.796,
                 "volume": 6369249, "adjclose": 4.796},
                {"id": 2, "stock_id": "1", "date": "2026-09-29", "close": 4.727,
                 "volume": 5800000, "adjclose": 4.727}]
        return StockHistorySql(FakeConnection({"stockhistory": rows}))

    def test_volume_returns_string_zero_when_missing(self):
        # PHP GetVolume 取不到时返回 '0'，不是 false/None
        table = self._table()
        self.assertEqual(table.get_volume("1", "2020-01-01"), "0")
        self.assertEqual(table.get_volume("1", "2026-09-30"), 6369249)

    def test_adjclose_falls_back_to_previous_day(self):
        # 港股休市时用最近一条复权价（PHP 原注释 when hongkong market on holiday）
        table = self._table()
        self.assertEqual(table.get_adjclose("1", "2026-09-30"), 4.796)
        self.assertEqual(table.get_adjclose("1", "2026-09-30", strict=False), 4.796)
        # 非严格模式下 2026-10-01 没有记录 -> 回落到 2026-09-30
        self.assertEqual(table.get_adjclose("1", "2026-10-01"), 4.796)

    def test_adjclose_strict_does_not_fall_back(self):
        table = self._table()
        self.assertIsNone(table.get_adjclose("1", "2026-10-01", strict=True))


class TimeTableTest(unittest.TestCase):
    def test_calibration_columns_include_num(self):
        connection = FakeConnection({})
        plain = FundEstSql(connection)
        calibration = CalibrationSql(connection)
        # columns 是裸列名元组（拼 SQL 时才加反引号）
        self.assertNotIn("num", plain.columns)
        self.assertIn("num", calibration.columns)
        # 两者都要有 time 列（显示时要 GetHM）
        self.assertIn("time", plain.columns)
        self.assertIn("time", calibration.columns)
        # 都必须带上 stock_id，否则行处理拿不到 key
        self.assertIn("stock_id", plain.columns)
        self.assertIn("stock_id", calibration.columns)

    def test_shares_and_netvalue_tables_share_daily_shape(self):
        # PHP 里 SharesHistorySql 与 NetValueHistorySql 都继承 DailyCloseSql
        for cls, table in ((SharesHistorySql, "shareshistory"),
                           (NetValueHistorySql, "netvaluehistory")):
            instance = cls(FakeConnection({}))
            self.assertEqual(instance.table, table)
            self.assertEqual(instance.columns, ("id", "stock_id", "date", "close"))


class SwitchDatesTest(unittest.TestCase):
    """``DailyCloseSql.get_switch_dates()`` —— PHP ``GetSwitchDates()``。

    期望值取自 real demo 库的 ``netvaluehistory``，并已用
    ``_scratch/_php_switchdates_check.php`` 跑真实 PHP 逐条对照过
    （SZ161125 / SZ161130 / SH513100 / SZ164906 四个符号 0 差异）。
    """

    #: ``date DESC``：``get_all()`` 的排序，也就是 PHP 喂给状态机的顺序
    SZ161125_DESC = [
        ("2026-10-19", "3.1319"), ("2026-10-16", "3.1206"), ("2026-10-15", "3.1548"),
        ("2026-10-14", "3.1448"), ("2026-10-13", "3.1803"), ("2026-10-12", "3.1817"),
        ("2026-10-09", "3.1843"), ("2026-10-08", "3.1679"), ("2026-10-01", "3.1328"),
        ("2026-09-30", "3.1229"), ("2026-09-29", "3.1328"), ("2026-09-28", "3.1374"),
        ("2026-09-24", "3.1494"), ("2026-09-23", "3.1494"), ("2026-09-22", "3.1718"),
        ("2026-09-21", "3.1731"), ("2026-09-18", "3.1302"), ("2026-09-17", "3.128"),
        ("2026-09-16", "3.0967"), ("2026-09-15", "3.1117"), ("2026-09-14", "3.1259"),
        ("2026-09-11", "3.1424"), ("2026-09-10", "3.1179"), ("2026-09-09", "3.1353"),
    ]

    def _table(self, rows):
        connection = FakeConnection({"netvaluehistory": [
            {"id": i, "stock_id": "1", "date": date, "close": close}
            for i, (date, close) in enumerate(rows)
        ]})
        return NetValueHistorySql(connection)

    def test_matches_php_on_real_netvalue_series(self):
        self.assertEqual(
            self._table(self.SZ161125_DESC).get_switch_dates("1"),
            ["2026-10-19", "2026-10-16", "2026-10-15", "2026-10-14", "2026-10-09",
             "2026-09-30", "2026-09-21", "2026-09-16", "2026-09-11", "2026-09-10"],
        )

    def test_records_are_local_min_or_max_alternately(self):
        """首个以外的成员必须是局部极值，且极小/极大严格交替。

        这条是对"为什么记的是上一条而不是当前条"的回归保护：
        记错一条，整个序列会整体平移一天，页面上每个波段的端点都错，
        但因为仍然是日期、长度也一样，肉眼很难发现。
        """
        closes = {date: float(close) for date, close in self.SZ161125_DESC}
        order = [date for date, _ in self.SZ161125_DESC]          # DESC
        dates = self._table(self.SZ161125_DESC).get_switch_dates("1")
        kinds = []
        for date in dates[1:]:
            index = order.index(date)
            newer = closes[order[index - 1]]
            older = closes[order[index + 1]]
            middle = closes[date]
            self.assertTrue(
                middle < min(newer, older) or middle > max(newer, older),
                "%s 不是极值（前 %s 后 %s 自己是 %s）" % (date, newer, older, middle),
            )
            kinds.append("min" if middle < min(newer, older) else "max")
        self.assertEqual(kinds[0], "min")
        self.assertTrue(all(a != b for a, b in zip(kinds, kinds[1:])),
                        "极值类型没有交替：%s" % kinds)

    def test_flat_segment_does_not_flip_the_trend(self):
        """严格不等号：相邻两天相等时**不**触发反转判定。

        PHP 的 ``if ($fOld < $fCur)`` / ``if ($fOld > $fCur)`` 都是严格比较，
        所以平台段被"看成还在延续原趋势"，直到真正越过平台那一天才判定，
        于是拐点落到平台上**更旧**那条（这里 10-15 而不是 10-16）。
        净值 09-23/09-24 同为 3.1494 就是这种情形。
        """
        plateau = [("2026-10-19", "1.20"), ("2026-10-16", "1.10"),
                   ("2026-10-15", "1.10"), ("2026-10-14", "1.15")]
        without = [("2026-10-19", "1.20"), ("2026-10-16", "1.10"),
                   ("2026-10-14", "1.15")]
        self.assertEqual(self._table(plateau).get_switch_dates("1"),
                         ["2026-10-19", "2026-10-15"])
        self.assertEqual(self._table(without).get_switch_dates("1"),
                         ["2026-10-19", "2026-10-16"])

    def test_single_record_returns_only_itself(self):
        """PHP 里 ``$bSecond`` 在只有一条记录时永远为真，走不到方向判断。"""
        self.assertEqual(self._table([("2026-10-19", "3.1319")]).get_switch_dates("1"),
                         ["2026-10-19"])

    def test_empty_table_returns_empty_list(self):
        self.assertEqual(self._table([]).get_switch_dates("1"), [])

    def test_unparsable_close_follows_php_floatval(self):
        """``close`` 为空串时照抄 PHP 的 ``floatval`` → **0.0**，不是跳过。

        PHP 原文是 ``floatval($record['close'])``，空串得到 0.0，于是净值缺失的
        那天会被当成一个假深谷 —— 生产端同样如此。这里刻意保持一致而不做
        "修正"：一旦改成跳过，拐点序列就与 PHP 错位，而页面上的波段是按下标配对的。
        """
        rows = [("2026-10-19", "3.10"), ("2026-10-16", ""), ("2026-10-15", "3.00")]
        self.assertEqual(self._table(rows).get_switch_dates("1"),
                         ["2026-10-19", "2026-10-16"])


if __name__ == "__main__":
    unittest.main()
