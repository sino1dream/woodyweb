"""``stockhistory`` 数值核心的单元测试。

这些用例钉的是**PHP 的原意**（含看着像 bug 的地方），不是"我觉得应该怎样"。
配套的逐项 PHP 对照在 ``_php_sma_compare.py``（436 例）。
"""

from __future__ import annotations

import unittest

import stockhistory as sh


class EstSmaTest(unittest.TestCase):
    """``_estSma()`` —— 注意除数是 ``avg - 1``。"""

    def test_uses_avg_minus_one_as_both_count_and_divisor(self):
        """**最关键的一条**：元素个数与除数都是 ``avg - 1``。

        所以 ``est_sma(x, 5)`` 是"最近 4 个的均值"，不是 5 个。
        看着像 off-by-one，但它是 ``php/stockhis.php:28/38`` 的原意；
        改掉会让所有均线整体偏移。
        """
        quotes = [10.0, 20.0, 30.0, 40.0, 100.0, 200.0]
        # 前 4 个之和 100，除以 4
        self.assertAlmostEqual(sh.est_sma(quotes, 5), 25.0)
        # 若错写成 avg，会变成 100/5 = 20
        self.assertNotAlmostEqual(sh.est_sma(quotes, 5), 20.0)

    def test_newest_first_order(self):
        """数组是最新在前，所以取的是**开头**那几个。"""
        quotes = [1.0, 2.0, 3.0, 1000.0, 2000.0]
        self.assertAlmostEqual(sh.est_sma(quotes, 3), 1.5)      # (1+2)/2

    def test_flat_series_returns_the_same_value(self):
        self.assertAlmostEqual(sh.est_sma([50.0] * 32, 20), 50.0)

    def test_too_few_elements_still_divides_by_expected_count(self):
        """元素不足时 PHP 循环自然结束、**仍除以 ``iNum``**（等于把缺的当 0）。

        照抄这个行为：若改成"除以实际个数"，短序列（新上市标的）的均线会偏高。
        """
        # 只有 3 个元素，avg=20 → iNum=19，和 = 33.0
        self.assertAlmostEqual(sh.est_sma([12.5, 11.0, 9.5], 20), 33.0 / 19)

    def test_empty_series_is_zero(self):
        self.assertAlmostEqual(sh.est_sma([], 5), 0.0)

    def test_avg_one_raises_instead_of_dividing_by_zero(self):
        """``avg=1`` 会让 ``iNum=0``。PHP 8 抛 ``DivisionByZeroError``。

        本版显式抛 ``ValueError`` —— 不想让它变成 ``inf`` 或 ``NaN`` 悄悄流下去。
        """
        with self.assertRaises(ValueError):
            sh.est_sma([1.0, 2.0], 1)

    def test_returns_float_not_string(self):
        """数值层返回数值；字符串化留给显示层。"""
        self.assertIsInstance(sh.est_sma([1.0, 2.0, 3.0], 4), float)


class QuadraticEquationRootTest(unittest.TestCase):
    """``GetQuadraticEquationRoot()``。"""

    def test_two_real_roots(self):
        roots = sh.get_quadratic_equation_root(1.0, -3.0, 2.0)
        self.assertIsNotNone(roots)
        self.assertAlmostEqual(sorted(roots)[0], 1.0)
        self.assertAlmostEqual(sorted(roots)[1], 2.0)

    def test_plus_root_comes_first(self):
        """PHP 先返回 ``(-b + sqrt)/2a``，顺序有意义（对应上轨/下轨）。"""
        roots = sh.get_quadratic_equation_root(1.0, -3.0, 2.0)
        self.assertAlmostEqual(roots[0], 2.0)
        self.assertAlmostEqual(roots[1], 1.0)

    def test_zero_discriminant(self):
        roots = sh.get_quadratic_equation_root(1.0, 2.0, 1.0)
        self.assertIsNotNone(roots)
        self.assertAlmostEqual(roots[0], -1.0)
        self.assertAlmostEqual(roots[1], -1.0)

    def test_negative_discriminant_returns_none(self):
        """PHP 返回 ``false`` → 本版 ``None``（AGENTS.md 第 3 条）。"""
        self.assertIsNone(sh.get_quadratic_equation_root(1.0, 0.0, 1.0))

    def test_zero_a_raises(self):
        """不是二次方程。PHP 会抛除零，本版显式抛 ``ValueError``。"""
        with self.assertRaises(ValueError):
            sh.get_quadratic_equation_root(0.0, 1.0, 1.0)


class BollingerBandsTest(unittest.TestCase):
    """``_estBollingerBands()`` 与 ``_estNextBollingerBands()``。"""

    @staticmethod
    def _serie(length: int = 64) -> list:
        values = []
        value = 100.0
        for index in range(length):
            value += ((index % 7) - 3) * 0.37 + 0.021 * index
            values.append(round(value, 4))
        return values

    def test_flagship_values_match_php(self):
        """实测值对照 ``_php_sma_compare.py``（PHP 侧同一组数据）。

        钉住具体数字，因为这两式的系数抄错一个下标**不会崩**，
        只会让轨道慢慢偏 —— 页面上"看起来还是个布林带"。
        """
        bands = sh.est_bollinger_bands(self._serie(), 20)
        self.assertAlmostEqual(bands[0], 102.56813910146684, places=9)
        self.assertAlmostEqual(bands[1], 96.79712405642816, places=9)

        next_bands = sh.est_next_bollinger_bands(self._serie(), 20)
        self.assertAlmostEqual(next_bands[0], 102.91423119518971, places=9)
        self.assertAlmostEqual(next_bands[1], 96.22232436036587, places=9)

    def test_upper_band_is_above_lower_band(self):
        """两根的顺序不能反：``x1``（+sqrt）对应上轨。"""
        bands = sh.est_bollinger_bands(self._serie(), 20)
        self.assertGreater(bands[0], bands[1])

    def test_the_two_formulas_differ(self):
        """当日式与 T+1 式**必须给出不同结果**。

        相同就说明只迁了一式（或把 ``iAvg-1`` / ``iAvg-2`` 抄成同一个）。
        """
        bands = sh.est_bollinger_bands(self._serie(), 20)
        next_bands = sh.est_next_bollinger_bands(self._serie(), 20)
        self.assertNotAlmostEqual(bands[0], next_bands[0])
        self.assertNotAlmostEqual(bands[1], next_bands[1])

    def test_net_sigma_divisor_differs(self):
        """当日式 sigma 除以 2、T+1 式除以 4 —— 这是两条式子最易混的地方。"""
        import inspect

        today_source = inspect.getsource(sh.est_bollinger_bands)
        next_source = inspect.getsource(sh.est_next_bollinger_bands)
        self.assertIn("/ 2", today_source)
        self.assertIn("/ 4", next_source)

    def test_flat_series_gives_equal_bands(self):
        """常数序列的标准差为 0，上下轨应重合于常数本身。"""
        bands = sh.est_bollinger_bands([50.0] * 32, 20)
        self.assertAlmostEqual(bands[0], 50.0, places=6)
        self.assertAlmostEqual(bands[1], 50.0, places=6)


class DayOfWeekSemanticsTest(unittest.TestCase):
    """``GetDayOfWeek()`` 是 ``date('w')``（周日 = 0 … 周六 = 6），不是 ISO。"""

    def test_friday_is_five(self):
        # 2026-10-09 是周五
        self.assertEqual(sh._day_of_week("2026-10-09"), 5)

    def test_sunday_is_zero_and_saturday_is_six(self):
        self.assertEqual(sh._day_of_week("2026-10-11"), 0)      # 周日
        self.assertEqual(sh._day_of_week("2026-10-10"), 6)      # 周六

    def test_monday_is_one(self):
        """ISO 下周一也是 1，但周日会差一天 —— 所以不能换成 ``weekday()``。"""
        self.assertEqual(sh._day_of_week("2026-10-12"), 1)

    def test_accepts_date_objects_and_compact_text(self):
        import datetime

        expected = sh._day_of_week("2026-10-09")
        self.assertEqual(sh._day_of_week(datetime.date(2026, 10, 9)), expected)
        self.assertEqual(sh._day_of_week("20261009"), expected)


class WeekEndTest(unittest.TestCase):
    """``_isWeekEnd()``。"""

    def test_friday_without_next_day_is_week_end(self):
        self.assertTrue(sh.is_week_end("2026-10-09", None, "2026-10-09"))

    def test_thursday_without_next_day_is_not(self):
        self.assertFalse(sh.is_week_end("2026-10-08", None, "2026-10-09"))

    def test_crossing_weekend_is_detected_with_next_day(self):
        """数据倒序，"下一天"其实是时间上更早的一条。

        周一(1) 之后是上周五(5)：``1 >= 5`` 为假，所以周一不算周内最后一条。
        反过来周五(5) 之后是周四(4)：``5 >= 4`` 为真，周内最后一条。
        """
        self.assertTrue(sh.is_week_end("2026-10-09", "2026-10-08"))    # 周五 vs 周四
        self.assertFalse(sh.is_week_end("2026-10-12", "2026-10-09"))   # 周一 vs 周五

    def test_weekend_now_counts_everything_as_week_end(self):
        """现在是周末时，PHP 直接返回 true。"""
        self.assertTrue(sh.is_week_end("2026-10-07", None, "2026-10-11"))


class MonthEndTest(unittest.TestCase):
    """``_isMonthEnd()``。"""

    def test_month_change_with_next_day(self):
        """有"下一天"时只看月份是否相同。"""
        # 10-30 的"下一天"（时间更早的 10-29）同月
        self.assertFalse(sh.is_month_end("2026-10-30", "2026-10-29"))
        self.assertFalse(sh.is_month_end("2026-10-29", "2026-10-28"))

    def test_crossing_month_boundary(self):
        # 10-30 之后（时间更早）是 10-29 —— 同月
        self.assertFalse(sh.is_month_end("2026-10-30", "2026-10-29"))
        # 11-02 之后是 10-30 —— 跨月
        self.assertTrue(sh.is_month_end("2026-11-02", "2026-10-30"))

    def test_without_next_day_uses_today(self):
        """今天就是本日时，用"下一个工作日"比月份。"""
        # 2026-10-30 是周五，下一个工作日是 11-02 → 跨月
        self.assertTrue(sh.is_month_end("2026-10-30", None, "2026-10-30"))
        # 2026-10-29 是周四，下一个工作日是 10-30 → 同月
        self.assertFalse(sh.is_month_end("2026-10-29", None, "2026-10-29"))


class MaxMinTest(unittest.TestCase):
    """``MaxMin`` —— 复刻 PHP 的 ``== false`` 语义。"""

    def test_initial_values_are_false_not_zero(self):
        instance = sh.MaxMin()
        self.assertIs(instance.f_max, False)
        self.assertIs(instance.f_min, False)

    def test_init_takes_effect_only_once(self):
        """守卫是"两个都还是 ``false`` 才写" —— 第二次 ``Init`` 不生效。"""
        instance = sh.MaxMin()
        instance.init(0.0, 10000000.0)
        self.assertEqual(instance.f_max, 0.0)
        self.assertEqual(instance.f_min, 10000000.0)

        instance.init(5.0, 6.0)
        self.assertEqual(instance.f_max, 0.0, "第二次 Init 不该覆盖")
        self.assertEqual(instance.f_min, 10000000.0)

    def test_init_with_zero_does_not_reopen_the_guard(self):
        """``Init(0.0, 0.0)`` 之后两个字段都是 0，``== false`` 仍为真。

        所以再调一次 ``Init`` **会**生效 —— 这是 PHP 的怪行为，照抄。
        """
        instance = sh.MaxMin()
        instance.init(0.0, 0.0)
        instance.init(7.0, 8.0)
        self.assertEqual(instance.f_max, 7.0)
        self.assertEqual(instance.f_min, 8.0)

    def test_set_widens_the_range(self):
        instance = sh.MaxMin()
        instance.init(0.0, 10000000.0)
        for value in (-1.0, 5.0, 20000000.0, 3.0):
            instance.set(value)
        self.assertEqual(instance.f_max, 20000000.0)
        self.assertEqual(instance.f_min, -1.0)

    def test_fit_is_an_open_interval(self):
        instance = sh.MaxMin()
        instance.init(10.0, 0.0)
        self.assertTrue(instance.fit(5.0))
        self.assertFalse(instance.fit(0.0), "左端点不算在区间内")
        self.assertFalse(instance.fit(10.0), "右端点不算在区间内")
        self.assertFalse(instance.fit(-1.0))
        self.assertFalse(instance.fit(11.0))

    def test_php_aliases(self):
        instance = sh.MaxMin()
        instance.Init(1.0, 0.0)
        instance.Set(5.0)
        self.assertTrue(instance.Fit(3.0))


class ColorAndOrderTest(unittest.TestCase):
    """``_getColorAndOrderArray()``。"""

    def test_daily_values_go_to_order_and_are_not_colored(self):
        colors, order = sh.get_color_and_order_array({"D5": "10.0", "D10": "20.0"})
        self.assertIsNone(colors["D5"])
        self.assertIsNone(colors["D10"])
        self.assertEqual(order, ["10.00", "20.00"])

    def test_weekly_inside_daily_range_is_silver(self):
        colors, _ = sh.get_color_and_order_array(
            {"D5": "10.0", "D10": "20.0", "W5": "15.0"})
        self.assertEqual(colors["W5"], "silver")

    def test_weekly_outside_daily_range_enters_order(self):
        colors, order = sh.get_color_and_order_array(
            {"D5": "10.0", "D10": "20.0", "W5": "100.0"})
        self.assertIsNone(colors["W5"])
        self.assertIn("100.00", order)

    def test_monthly_inside_daily_is_silver_inside_weekly_is_gray(self):
        """月线的三态：日区间内 → silver、周区间内 → gray、否则进标尺。"""
        colors, _ = sh.get_color_and_order_array({
            "D5": "10.0", "D10": "20.0",
            "W5": "100.0",                  # 落在日区间外 → 撑大周区间
            "M5": "15.0",                   # 日区间内 → silver
            "M10": "50.0",                  # 周区间(10..100)内 → gray
            "M20": "500.0",                 # 都外面 → 进标尺
        })
        self.assertEqual(colors["M5"], "silver")
        self.assertEqual(colors["M10"], "gray")
        self.assertIsNone(colors["M20"])

    def test_ema_inside_daily_is_silver_otherwise_yellow(self):
        colors, _ = sh.get_color_and_order_array(
            {"D5": "10.0", "D10": "20.0", "EMA50": "15.0", "EMA200": "90.0"})
        self.assertEqual(colors["EMA50"], "silver")
        self.assertEqual(colors["EMA200"], "yellow")

    def test_order_is_numerically_sorted_and_deduplicated(self):
        """``sort($arVal, SORT_NUMERIC)`` + ``array_unique``。

        必须是**数值**排序：字符串排序会把 "100.00" 排在 "20.00" 前面。
        """
        _, order = sh.get_color_and_order_array(
            {"D5": "100.0", "D10": "20.0", "M5": "300.0", "M10": "5.0", "M20": "20.0"})
        self.assertEqual(order, ["5.00", "20.00", "100.00", "300.00"])

    def test_order_values_are_two_decimal_strings(self):
        _, order = sh.get_color_and_order_array({"D5": "10.0"})
        self.assertEqual(order, ["10.00"])

    def test_without_daily_keys_it_does_not_crash(self):
        """没有 ``D`` 键时区间退化，EMA 全染 ``yellow`` —— 不能抛异常。

        PHP 那边 ``mm`` 只在遇到 ``D`` 键时才 ``Init(0.0, 10000000.0)``。
        字典若没有 ``D`` 键，``$mm->fMax/fMin`` 一直是初始的 ``false``，
        于是 ``Fit()`` 恒为假、EMA 全部落到 ``yellow`` 分支。

        **这与"调用方顺序对不对"无关**：只要没有 ``D`` 键就必然如此。
        实测 ``_php_sma_compare.py`` 的 ``only_ema`` 用例就是这个结果
        （``colors['EMA50'] == 'yellow'``），本版照抄。
        """
        colors, order = sh.get_color_and_order_array({"EMA50": "55.0", "EMA200": "44.0"})
        self.assertEqual(colors["EMA50"], "yellow")
        self.assertEqual(colors["EMA200"], "yellow")
        self.assertEqual(order, [])

    def test_every_key_gets_a_color_entry(self):
        """配色字典的键必须与输入一一对应（渲染层按 key 取色）。"""
        sma = {"D5": "1.0", "W5": "2.0", "M5": "3.0", "EMA50": "4.0"}
        colors, _ = sh.get_color_and_order_array(sma)
        self.assertEqual(set(colors), set(sma))


class AliasTest(unittest.TestCase):
    """PHP 原名别名（AGENTS.md 第 3 条）。"""

    def test_function_aliases(self):
        self.assertIs(sh._estSma, sh.est_sma)
        self.assertIs(sh.GetQuadraticEquationRoot, sh.get_quadratic_equation_root)
        self.assertIs(sh._estBollingerBands, sh.est_bollinger_bands)
        self.assertIs(sh._estNextBollingerBands, sh.est_next_bollinger_bands)
        self.assertIs(sh._isWeekEnd, sh.is_week_end)
        self.assertIs(sh._isMonthEnd, sh.is_month_end)

    def test_constants(self):
        self.assertEqual(sh.MAX_QUOTES_DAYS, 620)
        self.assertEqual(sh.BOLL_DAYS, 20)
        self.assertEqual(sh.SMA_SECTION, "SMA")
        self.assertEqual(sh.AI_NUM, (5, 10, 20))


class ModuleIsImportCleanTest(unittest.TestCase):
    """这个模块是纯计算的：import 它不该连数据库、不该读配置。"""

    def test_no_database_or_config_imports(self):
        import inspect

        source = inspect.getsource(sh)
        for forbidden in ("pymysql", "configparser", "open("):
            self.assertNotIn(forbidden, source,
                             "数值核心不该依赖 %s" % forbidden)


class _FakeReference:
    """最小 ref：只提供 ``StockHistory`` 用到的四个方法。"""

    def __init__(self, symbol="SZ161130", stock_id=1, price="4.884",
                 date="2026-10-09", time_zone="Asia/Shanghai"):
        self._symbol = symbol
        self._stock_id = stock_id
        self._price = price
        self._date = date
        self._time_zone = time_zone

    def get_symbol(self):
        return self._symbol

    def get_stock_id(self):
        return self._stock_id

    def get_price(self):
        return self._price

    def get_date(self):
        return self._date

    def get_time_zone(self):
        return self._time_zone


class _FakeHistorySql:
    """假的历史表：按 PHP 语义返回**倒序**数据。

    ``rows`` 是 ``[(date, adjclose), ...]``，调用方按时间**从新到旧**给。
    """

    def __init__(self, rows, ema=None):
        self.rows = list(rows)
        self.ema = dict(ema or {})
        self.get_all_calls = []
        self.from_date_calls = []

    def get_all(self, stock_id, start=0, num=0):
        self.get_all_calls.append((stock_id, start, num))
        window = self.rows if num == 0 else self.rows[start:start + num]
        return [{"date": date, "adjclose": close} for date, close in window]

    def get_from_date(self, stock_id, date, num=0):
        """``date <= ?``（不是 ``>=``）—— 与 PHP ``_buildWhereFromDate`` 一致。

        这里刻意保留"容易写反"的注释：早期写成 ``>=`` 时只返回一行，
        均线全部退化成同一个价格且不报错。
        """
        self.from_date_calls.append((stock_id, date, num))
        kept = [(d, c) for d, c in self.rows if d <= str(date)]
        if num:
            kept = kept[:num]
        return [{"date": d, "adjclose": c} for d, c in kept]

    def get_close(self, stock_id, date):
        return self.ema.get(str(date))


class _FakeConnection:
    """假连接：让 ``StockHistorySql`` 能真的跑起来（游标假、结果真）。

    **刻意不 mock 掉 ``StockHistorySql``** —— 那样会把"SQL 拼错"这类问题
    一起屏蔽掉。这里只替换游标，让查询走真实代码路径。
    """

    def __init__(self, tables):
        #: ``{表名: {日期: close}}``
        self.tables = tables
        self.queries = []

    def cursor(self):
        return _FakeCursor(self)


class _FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.sql = ""
        self.params = ()
        self._rows = []

    def execute(self, sql, params=()):
        self.sql = sql
        self.params = params
        self.connection.queries.append((sql, params))
        self._rows = self._resolve(sql, params)

    def _resolve(self, sql, params):
        """只认两种查询：``SELECT `close` ... WHERE ... date = %s``。

        按表名从 ``tables`` 里取；参数是 ``(stock_id, date)``。
        """
        import re

        match = re.search(r"FROM `(\w+)`", sql)
        if not match:
            return []
        table = match.group(1)
        values = self.connection.tables.get(table, {})
        if "`close`" in sql and "date` = %s" in sql:
            date = str(params[1]) if len(params) > 1 else None
            if date in values:
                return [{"close": values[date]}]
            return []
        return []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass


class _FakeSource:
    """把 :class:`_FakeHistorySql` 挂成数据源。"""

    def __init__(self, history, time_zone="Asia/Shanghai", ema_tables=None):
        self.connection = _FakeConnection(dict(ema_tables or {}))
        self._history = history
        self._time_zone = time_zone

    def get_time_zone(self):
        return self._time_zone


#: 一段固定的历史：**从新到旧**，跨周末与跨月各一次
ROWS = [
    ("2026-10-09", 99.5),    # 周五
    ("2026-10-08", 99.0),
    ("2026-10-07", 98.5),
    ("2026-10-02", 98.0),    # 周五（上一周）
    ("2026-10-01", 97.5),
    ("2026-09-30", 97.0),    # 9 月最后一条 → 月线
    ("2026-09-29", 96.5),
    ("2026-09-28", 96.0),
    ("2026-09-25", 95.5),    # 周五（再上一周）
    ("2026-09-24", 95.0),
]


def _history(rows=None, ema=None):
    """建假历史表。

    **注意 ``ema`` 不参与历史行** —— EMA 走的是 ``stockema*`` 表（另一个
    连接游标），早期这里把 ``ema`` 误当成 ``rows`` 传进
    ``_FakeHistorySql``，导致历史只剩一条（``get_from_date`` 只回当天）。
    """
    return _FakeHistorySql(ROWS if rows is None else rows)


def _build(rows=None, ema=None, now=None, after_hour=False, config=None,
           price="4.884", date="2026-10-09", source_tz="Asia/Shanghai",
           history=None):
    import datetime

    sql = history if history is not None else _history(rows, ema)
    ref = _FakeReference(price=price, date=date)
    # EMA 走真实 StockHistorySql + 假游标；ema 字典按"日期 -> 值"给
    ema_tables = {
        "stockema50": dict(ema or {}),
        "stockema200": dict(ema or {}),
    }
    source = _FakeSource(sql, source_tz, ema_tables)
    return sh.StockHistory(
        ref,
        source=source,
        after_hour=after_hour,
        now=now if now is not None else datetime.datetime(2026, 10, 9, 17, 0),
        config=config,
        history_sql=sql,
    ), sql


class IgnoreCurrentTradingDataTest(unittest.TestCase):
    """``_ignoreCurrentTradingData()`` —— 收盘前不能用今天的数据。"""

    def test_today_before_close_is_ignored(self):
        self.assertTrue(sh.ignore_current_trading_data(
            "2026-10-09", "2026-10-09 16:30"))

    def test_today_after_close_is_usable(self):
        self.assertFalse(sh.ignore_current_trading_data(
            "2026-10-09", "2026-10-09 17:00"))

    def test_older_date_is_always_usable(self):
        """不是今天就跟收盘时间无关。"""
        self.assertFalse(sh.ignore_current_trading_data(
            "2026-10-08", "2026-10-09 10:00"))

    def test_boundary_is_hour_16_inclusive(self):
        """``IsStockTradingHourEnd()`` 是 ``GetHour() <= 16`` → 16:59 仍算未收盘。

        照抄这个边界：16 点整与 16:30 都返回"未收盘"，17 点才算。
        """
        self.assertFalse(sh.is_stock_trading_hour_end("2026-10-09 16:00"))
        self.assertFalse(sh.is_stock_trading_hour_end("2026-10-09 16:59"))
        self.assertTrue(sh.is_stock_trading_hour_end("2026-10-09 17:00"))

    def test_accepts_datetime(self):
        import datetime

        self.assertFalse(sh.is_stock_trading_hour_end(
            datetime.datetime(2026, 10, 9, 15, 0)))
        self.assertTrue(sh.is_stock_trading_hour_end(
            datetime.datetime(2026, 10, 9, 18, 0)))


class CalcStartDateTest(unittest.TestCase):
    """``_calcStartDate()`` —— 为什么取最近 **2** 条。"""

    def test_returns_newest_date_after_close(self):
        instance, sql = _build()
        self.assertEqual(instance.get_start_date(), "2026-10-09")
        self.assertEqual(sql.get_all_calls, [(1, 0, 2)], "必须只取 2 条")

    def test_skips_today_before_close_and_falls_back(self):
        """市场还没收盘（当地 10 点）时，最新那条要被跳过、起点落到上一交易日。

        这就是 PHP 取 **2** 条而不是 1 条的原因。
        """
        import datetime

        instance, _ = _build(now=datetime.datetime(2026, 10, 9, 10, 0))
        self.assertEqual(instance.get_start_date(), "2026-10-08")

    def test_no_history_returns_none(self):
        instance, _ = _build(rows=[])
        self.assertIsNone(instance.get_start_date())

    def test_php_alias(self):
        instance, _ = _build()
        self.assertEqual(instance._calcStartDate(), instance.get_start_date())


class DayWeekMonthSlicingTest(unittest.TestCase):
    """``_getDayWeekMonthData()`` —— 倒序遍历 + 两条判据的配合。"""

    def test_daily_series_is_newest_first(self):
        instance, _ = _build()
        daily, _, _ = instance.get_day_week_month_data()
        self.assertEqual(daily[0], 99.5, "最新在前")
        self.assertEqual(daily[-1], 95.0)
        self.assertEqual(len(daily), len(ROWS))

    def test_weekly_picks_week_ends(self):
        """2026-10-09 / 10-02 / 09-25 是各周最后一条（都是周五）。"""
        instance, _ = _build()
        _, weekly, _ = instance.get_day_week_month_data()
        self.assertEqual(weekly, [99.5, 98.0, 95.5])

    def test_monthly_picks_month_ends(self):
        """跨月处取 09-30（9 月最后一条）；最新那条由"今天"分支决定。

        第一条（10-09，周五）没有"下一天"，走 ``_isWeekEnd`` 的当前时间分支，
        在 10-09 当天看它是周内最后一条；但 **10 月还没结束**，所以不进月线。
        """
        instance, _ = _build()
        _, _, monthly = instance.get_day_week_month_data()
        self.assertEqual(monthly, [97.0])

    def test_from_date_is_called_with_start_date_and_620(self):
        """``_configSMA()`` 期间已经算过一次，所以这里断言"至少一次且参数正确"。"""
        instance, sql = _build()
        instance.get_day_week_month_data()
        self.assertTrue(sql.from_date_calls, "必须查过历史")
        for call in sql.from_date_calls:
            self.assertEqual(call, (1, "2026-10-09", sh.MAX_QUOTES_DAYS),
                             "必须带 start_date 与 MAX_QUOTES_DAYS")

    def test_without_start_date_series_is_empty(self):
        instance, _ = _build(rows=[])
        daily, weekly, monthly = instance.get_day_week_month_data()
        self.assertEqual((daily, weekly, monthly), ([], [], []))


class ConfigSmaTest(unittest.TestCase):
    """``_configSMA()`` 与缓存 —— 关键是**不写文件**。"""

    def test_keys_match_php_layout(self):
        instance, _ = _build()
        expected = {
            "D5", "D10", "D20", "DBOLLUP", "DBOLLDN",
            "W5", "W10", "W20", "WBOLLUP", "WBOLLDN",
            "M5", "M10", "M20", "MBOLLUP", "MBOLLDN",
            "EMA50", "EMA200",
        }
        self.assertEqual(set(instance.get_sma()), expected)

    def test_next_values_are_stored_for_daily(self):
        """``_cfg_set_SMAs`` 会给每个周期再存一个 ``i-1`` 的 T+1 值。"""
        instance, _ = _build()
        self.assertIsNotNone(instance.next_values["D5"])
        # T+1 用的是 est_sma(quotes, 4)
        daily, _, _ = instance.get_day_week_month_data()
        self.assertAlmostEqual(instance.next_values["D5"], sh.est_sma(daily, 4))

    def test_ema_next_and_after_hour_are_none(self):
        """EMA 不做 T+1 推算（PHP ``arNext['EMA50'] = false``）。"""
        instance, _ = _build(ema={"2026-10-09": 101.5})
        self.assertIsNone(instance.next_values["EMA50"])
        self.assertIsNone(instance.after_hour_values["EMA50"])

    def test_ema_read_by_exact_date(self):
        """``GetClose()`` 是按 ``start_date`` **精确取**，不是取最近一条。"""
        instance, _ = _build(ema={"2026-10-08": 100.0})
        self.assertIsNone(instance.get_sma()["EMA50"], "start_date 那天没有就为空")

        instance, _ = _build(ema={"2026-10-09": 101.5})
        self.assertAlmostEqual(instance.get_sma()["EMA50"], 101.5)

    def test_second_build_with_same_start_date_reads_cache(self):
        """第二次构造时配置里的 ``Date`` 相同 → 走读缓存分支。

        实测手法：先用一份历史建好（写入配置），再改掉历史数据用**同一份配置**
        建第二次 —— 若走了缓存，SMA 应保持第一次的值。
        """
        config = sh.ConfigSmaStore()
        first, _ = _build(config=config)
        first_d5 = first.get_sma()["D5"]

        # 换一份完全不同的历史 + 同样的 start_date
        other = [("2026-10-09", 1.0), ("2026-10-08", 2.0), ("2026-10-07", 3.0),
                 ("2026-10-06", 4.0), ("2026-10-05", 5.0)]
        second, _ = _build(rows=other, config=config)

        self.assertEqual(config.read_var(sh.SMA_SECTION, "Date"), "2026-10-09")
        self.assertAlmostEqual(second.get_sma()["D5"], first_d5,
                               msg="start_date 未变时应读缓存、不重算")

    def test_config_defaults_to_memory(self):
        """默认配置是纯内存实现，不能碰文件系统。"""
        instance, _ = _build()
        self.assertIsInstance(instance.config, sh.ConfigSmaStore)
        self.assertTrue(instance.config.snapshot())

    def test_save_data_is_a_noop(self):
        """``save_data()`` 保留是为了对齐 PHP 调用点，内存实现不该做任何事。"""
        store = sh.ConfigSmaStore()
        store.set_var("SMA", "D5", "1.0")
        self.assertIsNone(store.save_data())
        self.assertEqual(store.read_var("SMA", "D5"), "1.0")

    def test_group_exists(self):
        store = sh.ConfigSmaStore()
        self.assertFalse(store.group_exists("SMA"))
        store.set_var("SMA", "Date", "2026-10-09")
        self.assertTrue(store.group_exists("SMA"))


class NeedAfterHourEstTest(unittest.TestCase):
    """``NeedAfterHourEst()`` —— 两个条件缺一不可。"""

    def test_after_1520_with_stale_start_date(self):
        import datetime

        instance, _ = _build(now=datetime.datetime(2026, 10, 9, 15, 30))
        self.assertTrue(instance.need_after_hour_est())

    def test_before_1520_is_false(self):
        import datetime

        instance, _ = _build(now=datetime.datetime(2026, 10, 9, 15, 10))
        self.assertFalse(instance.need_after_hour_est())

    def test_boundary_is_1520_exclusive(self):
        """``GetHourMinute() > 1520`` —— 15:20 整为假，15:21 为真。"""
        import datetime

        instance, _ = _build(now=datetime.datetime(2026, 10, 9, 15, 20))
        self.assertFalse(instance.need_after_hour_est())
        instance, _ = _build(now=datetime.datetime(2026, 10, 9, 15, 21))
        self.assertTrue(instance.need_after_hour_est())

    def test_start_date_equal_to_ref_date_is_false(self):
        """起始日就是最新行情日 → 今天的数据已在序列里，不需要盘后补。"""
        import datetime

        instance, _ = _build(now=datetime.datetime(2026, 10, 9, 17, 0),
                             date="2026-10-09")
        self.assertEqual(instance.get_start_date(), "2026-10-09")
        self.assertFalse(instance.need_after_hour_est())


class AfterHourTest(unittest.TestCase):
    """``after_hour=True`` 时的 ``_onTest()``。"""

    def test_prepends_current_price(self):
        """盘后估值要把**当天现价**并进序列再算。

        现价 4.884 在时间上晚于全部历史，所以接在最前面 —— 这样 D5 是
        "现价 + 最近 3 个历史"的均值（``avg-1 = 4`` 个）。

        取 ``date="2026-10-10"`` 让"最新行情日"晚于起始日，
        从而触发 ``NeedAfterHourEst()`` 的第二个条件。
        """
        import datetime

        instance, _ = _build(after_hour=True, price="4.884", date="2026-10-10",
                             now=datetime.datetime(2026, 10, 10, 16, 0))
        self.assertTrue(instance.need_after_hour_est())
        daily, _, _ = instance.get_day_week_month_data()
        # 显式再取一次（_onTest 只在构造里跑），确认现价确实被并入
        self.assertEqual(daily[0], 99.5, "未开 _prepend_price 时不含现价")
        instance._prepend_price = True
        try:
            daily2, _, _ = instance.get_day_week_month_data()
        finally:
            instance._prepend_price = False
        self.assertAlmostEqual(daily2[0], 4.884, msg="现价必须排在最前面")

    def test_needs_after_hour_when_start_date_is_older(self):
        import datetime

        instance, _ = _build(after_hour=True, price="4.884", date="2026-10-10",
                             now=datetime.datetime(2026, 10, 10, 16, 0))
        self.assertTrue(instance.need_after_hour_est())
        self.assertTrue(instance.after_hour_values.get("D5") is not None)

    def test_after_hour_values_include_both_bollinger_bands(self):
        import datetime

        instance, _ = _build(after_hour=True, price="4.884", date="2026-10-10",
                             now=datetime.datetime(2026, 10, 10, 16, 0))
        for key in ("DBOLLUP", "DBOLLDN", "WBOLLUP", "MBOLLUP"):
            self.assertIn(key, instance.after_hour_values)


class BullBearTest(unittest.TestCase):
    """``GetBullBear()``。"""

    def test_bull_when_ema50_above_ema200(self):
        instance, _ = _build(ema={"2026-10-09": 100.0})
        # ema 表里 50 与 200 用同一份假数据不好区分，这里直接改字段
        instance.sma["EMA50"] = 100.0
        instance.sma["EMA200"] = 90.0
        self.assertEqual(instance.get_bull_bear(), ("bull", "牛市"))

    def test_bear_when_ema50_below_ema200(self):
        instance, _ = _build()
        instance.sma["EMA50"] = 80.0
        instance.sma["EMA200"] = 95.0
        self.assertEqual(instance.get_bull_bear(), ("bear", "熊市"))

    def test_none_without_ema50(self):
        instance, _ = _build()
        instance.sma.pop("EMA50", None)
        self.assertIsNone(instance.get_bull_bear())

    def test_labels_are_chinese(self):
        """文案是"牛市 / 熊市"，对应 PHP ``GetFontElement('牛市')``。"""
        instance, _ = _build()
        instance.sma["EMA50"] = 1.0
        instance.sma["EMA200"] = 0.0
        self.assertEqual(instance.get_bull_bear()[1], "牛市")


class TimeZoneTest(unittest.TestCase):
    """``market_now()`` —— 不动进程时区。"""

    def test_localizes_to_market_time_zone(self):
        """用 ``tzutil`` 折算，而不是 PHP 那样改进程默认时区。

        ``stocksymbol.set_time_zone()`` 需要 ``time.tzset()``，Windows 上没有、
        会直接抛错，所以本项目必须走这条显式折算的路。
        """
        import datetime

        instance, _ = _build(now=datetime.datetime(2026, 10, 9, 17, 0),
                             source_tz="America/New_York")
        moment = instance.market_now()
        self.assertIsNotNone(moment.tzinfo)

    def test_naive_now_is_interpreted_as_market_local(self):
        import datetime

        instance, _ = _build(now=datetime.datetime(2026, 10, 9, 10, 0),
                             source_tz="Asia/Shanghai")
        self.assertEqual(instance.market_now().hour, 10)


class ColorIntegrationTest(unittest.TestCase):
    """``StockHistory`` 建完就应该有配色与纵轴标尺。"""

    def test_colors_cover_every_sma_key(self):
        instance, _ = _build(ema={"2026-10-09": 99.8})
        self.assertEqual(set(instance.colors), set(instance.get_sma()))

    def test_order_array_is_a_list_of_strings(self):
        instance, _ = _build()
        order = instance.get_order_array()
        self.assertIsInstance(order, list)
        for item in order:
            self.assertIsInstance(item, str)

    def test_get_color_returns_none_for_unknown_key(self):
        instance, _ = _build()
        self.assertIsNone(instance.get_color("NOT_A_KEY"))

    def test_aliases(self):
        instance, _ = _build()
        self.assertEqual(instance.GetStartDate(), instance.get_start_date())
        self.assertIs(instance.GetSMA(), instance.get_sma())
        self.assertIs(instance.GetRef(), instance.stock_ref)


if __name__ == "__main__":
    unittest.main()
