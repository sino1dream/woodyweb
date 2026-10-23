"""``ui/dateimage.py``（净值 / 仓位走势图）的单元测试。

对应 PHP ``php/dateimagefile.php`` + ``php/imagefile.php``。几何与文字标注
是纯函数部分，逐项钉住；PNG 生成只验"出得来、尺寸对"。
"""

import unittest

from ui import dateimage


def _chart(base, focus, symbol="SZ161125"):
    return dateimage.NetValuePositionChart(base, focus, symbol=symbol)


class GeometryTest(unittest.TestCase):
    """``PageImageFile`` 的坐标映射（照抄，含 intval 截断）。"""

    def setUp(self):
        #: 5 个点、净值 1.0~2.0 —— 手算得动的尺度。
        self.chart = _chart([("2026-10-15", 1.0), ("2026-10-16", 1.5),
                             ("2026-10-17", 2.0), ("2026-10-18", 1.2),
                             ("2026-10-19", 1.8)], [])
        self.chart.setup_base_scale()

    def test_get_pos_x_last_point_stays_before_right_edge(self):
        #: PHP：fMinX 恒 0、fMaxX = 行数，所以最后一点落在 (n-1)/n 处，
        #: 不到右边框（不是常规的 (n-1) 归一）—— 照抄。
        self.assertEqual(self.chart.get_pos_x(4, 100), int(100 * 4 / 5))

    def test_get_pos_y_top_is_zero(self):
        #: max 映射到 0（图顶）。**min_y 默认是 0.0 而不是 base 的最小值**
        #: —— PHP 的 ``$fMinY = 0.0`` 是类字段默认值，``setup_base_scale``
        #: 只设 max_y，所以净值 1.0（base 的最小值）落在半高而不是底部。
        self.assertEqual(self.chart.get_pos_y(2.0, 100), 0)
        self.assertEqual(self.chart.get_pos_y(1.0, 100), 50)

    def test_get_pos_returns_zero_when_range_below_min_float_val(self):
        #: |max-min| <= MIN_FLOAT_VAL 时给 0（PHP ``_getPos`` 的防守）。
        chart = _chart([("2026-10-15", 1.0), ("2026-10-16", 1.0)], [])
        chart.setup_base_scale()
        self.assertEqual(chart.get_pos_y(1.0, 100), 0)


class ScaleTest(unittest.TestCase):
    """双尺度切换（base 用 max(base)，focus 用 min/max(focus) 夹 0）。"""

    def test_setup_focus_scale_clamps_to_zero(self):
        chart = _chart([("2026-10-15", 1.0), ("2026-10-16", 1.1)],
                       [("2026-10-16", 0.95)])
        chart.setup_focus_scale()
        #: 仓位恒正 → min_y 夹成 0、max_y 保持 0.95。
        self.assertEqual(chart.min_y, 0.0)
        self.assertAlmostEqual(chart.max_y, 0.95, places=9)

    def test_reference_values_empty_when_position_positive(self):
        #: 仓位恒正 → fMinY 被夹成 0.0 → 0 与 -1 都不满足严格小于；
        #: 仓位不超过 1 → 1 也不画。三条参考线一条都不出现（照抄 PHP）。
        chart = _chart([("2026-10-15", 1.0), ("2026-10-16", 1.1)],
                       [("2026-10-16", 0.95)])
        chart.setup_focus_scale()
        self.assertEqual(chart.reference_values(), [])

    def test_reference_values_draw_zero_when_position_goes_negative(self):
        #: 只有出现负仓位时 0 才落进区间（理论情况，钉住判据本身）。
        chart = _chart([("2026-10-15", 1.0), ("2026-10-16", 1.1)],
                       [("2026-10-15", -0.5), ("2026-10-16", 0.95)])
        chart.setup_focus_scale()
        self.assertEqual(chart.reference_values(), [0.0])

    def test_reference_values_empty_without_focus(self):
        #: focus 空时 PHP 提前 return，根本不会调 DrawAxisX。
        chart = _chart([("2026-10-15", 1.0), ("2026-10-16", 1.1)], [])
        chart.setup_base_scale()
        self.assertEqual(chart.reference_values(), [])


class TextLinesTest(unittest.TestCase):
    """四项标注（开始 / 结束 / 最大 / 最小值）。"""

    def setUp(self):
        #: 写入顺序是 date DESC（服务层按 GetAll 的顺序喂进来）。
        self.chart = _chart(
            [("2026-10-19", 1.8), ("2026-10-18", 1.2), ("2026-10-17", 2.0),
             ("2026-10-16", 1.5), ("2026-10-15", 1.0)],
            [("2026-10-19", 0.95), ("2026-10-17", 0.92), ("2026-10-15", 0.97)])

    def test_labels_use_chronological_order(self):
        #: PHP array_reverse 之后 key/current 取的是**最旧**那天，
        #: end 之后是最新那天 —— 顺序反了会张冠李戴。
        lines = dict(self.chart.text_lines())
        self.assertEqual(lines["开始日期"], "2026-10-15 0.97")
        self.assertEqual(lines["结束日期"], "2026-10-19 0.95")

    def test_max_and_min_dates(self):
        lines = dict(self.chart.text_lines())
        self.assertEqual(lines["最大值"], "2026-10-15 0.97")
        self.assertEqual(lines["最小值"], "2026-10-17 0.92")

    def test_min_uses_real_minimum_not_clamped_zero(self):
        #: 刻意偏离 PHP：最小值用真实值，不用夹 0 后的 fMinY（否则恒显示 0.00）。
        lines = dict(self.chart.text_lines())
        self.assertNotIn("0.00", lines["最小值"])

    def test_tied_maximum_takes_the_first_date(self):
        #: array_search 返回首个匹配键 → 并列取最旧那天。
        chart = _chart([("2026-10-19", 1.0), ("2026-10-18", 1.0)],
                       [("2026-10-19", 0.9), ("2026-10-18", 0.9)])
        lines = dict(chart.text_lines())
        self.assertEqual(lines["最大值"], "2026-10-18 0.90")

    def test_empty_focus_has_no_text_lines(self):
        chart = _chart([("2026-10-19", 1.0), ("2026-10-18", 1.0)], [])
        self.assertEqual(chart.text_lines(), [])


class DrawTest(unittest.TestCase):
    """``Draw()`` 的闸门与 PNG 产出。"""

    def test_base_fewer_than_two_points_returns_none(self):
        #: PHP：count($arBase) < 2 直接 false，页面不出现 <img>。
        self.assertIsNone(dateimage.draw_netvalue_position_chart(
            [("2026-10-19", 1.0)], [], symbol="SZ161125"))
        self.assertIsNone(dateimage.draw_netvalue_position_chart(
            [], [], symbol="SZ161125"))

    def test_render_png_returns_bytes_with_expected_size(self):
        png = dateimage.draw_netvalue_position_chart(
            [("2026-10-19", 1.8), ("2026-10-18", 1.2), ("2026-10-17", 2.0),
             ("2026-10-16", 1.5), ("2026-10-15", 1.0)],
            [("2026-10-19", 0.95), ("2026-10-17", 0.92)],
            symbol="SZ161125")
        self.assertIsNotNone(png)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        #: PNG 头里的 IHDR：宽 800、高 = int(800*360/640) = 450。
        width = int.from_bytes(png[16:20], "big")
        height = int.from_bytes(png[20:24], "big")
        self.assertEqual((width, height), (800, 450))

    def test_render_png_without_focus_still_draws_base(self):
        #: focus 空（一行仓位都没算出来）时 PHP 只画基准线就返回 true，
        #: 图照样给 —— 与"整页过不了闸门"不是一个概念。
        png = dateimage.draw_netvalue_position_chart(
            [("2026-10-19", 1.8), ("2026-10-18", 1.2)], [], symbol="SZ161125")
        self.assertIsNotNone(png)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")

    def test_picture_differs_when_focus_disappears(self):
        """同一份 base、focus 从有到无：红线/点/右轴刻度/图例项/标注全没了。

        不逐项去戳位图，用"两张 PNG 不同"兜住这条分支（渲染是确定性的）。
        """
        base = [("2026-10-19", 1.8), ("2026-10-18", 1.2), ("2026-10-17", 2.0)]
        with_focus = dateimage.draw_netvalue_position_chart(
            base, [("2026-10-19", 0.95), ("2026-10-17", 0.92)], symbol="SZ161125")
        without = dateimage.draw_netvalue_position_chart(base, [], symbol="SZ161125")
        self.assertIsNotNone(with_focus)
        self.assertIsNotNone(without)
        self.assertNotEqual(with_focus, without)


class NumberFormatTest(unittest.TestCase):
    """``number_format($fVal, 2)`` 保留尾零（与登记仓位做字符串比较的口径）。"""

    def test_keeps_trailing_zero(self):
        self.assertEqual(dateimage.number_format(1), "1.00")
        self.assertEqual(dateimage.number_format(0.9), "0.90")
        self.assertEqual(dateimage.number_format(-0.005), "-0.01")


if __name__ == "__main__":
    unittest.main()
