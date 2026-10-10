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


if __name__ == "__main__":
    unittest.main()
