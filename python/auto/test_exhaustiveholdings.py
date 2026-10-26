"""``core.exhaustiveholdings`` 的单测。

对照 PHP：``woody/res/exhaustiveholdingscn.php``。

替身（:class:`FakeRef` / :class:`FakeNetSql`）只实现穷举页真正用到的那几个
方法 —— 这样测试同时充当"页面到底依赖引用对象的哪些能力"的清单。
"""

import unittest

from core.exhaustiveholdings import (
    DEFAULT_EXHAUSTIVE_ROWS,
    UpdateCell,
    ExhaustiveHoldingsCalculator,
    confirm_update_question,
    encode_holdings_payload,
    holding_ratio_cell,
    number_display,
    parse_threshold,
    percentage_cell,
    position_cell,
    switch_date_pairs,
)

ITEM_ONE = 101
ITEM_TWO = 102


class FakeCnyRef:
    def __init__(self, stock_id=8):
        self._stock_id = stock_id

    def get_stock_id(self):
        return self._stock_id


class FakeNetSql:
    """``NetValueHistorySql`` 的子集。``closes`` 键是 ``(stock_id, date)``。"""

    def __init__(self, closes=None, dates=None):
        self.closes = dict(closes or {})
        self.dates = list(dates or [])

    def get_close(self, stock_id, date):
        return self.closes.get((stock_id, date))

    def get_proportion(self, stock_id, date, prev_date):
        now = self.closes.get((stock_id, date))
        prev = self.closes.get((stock_id, prev_date))
        if not now or not prev:
            return None
        return float(now) / float(prev)

    def get_switch_dates(self, stock_id):
        return list(self.dates)

    def get_all(self, stock_id):
        rows = [(date, value) for (sid, date), value in self.closes.items() if sid == stock_id]
        rows.sort(key=lambda item: item[0], reverse=True)
        return [{"date": date, "close": value} for date, value in rows]


class FakeRef:
    """``HoldingsReference`` 的子集。

    ``proportions`` 是 ``{(date, prev_date): [ratio, ...]}`` —— 穷举页只需要
    "这两天之间各成分股的涨跌比例"，不需要真实的行情串。
    """

    def __init__(self, ratios=None, position=0.95, proportions=None,
                 net_value_display="1.2345", est=None):
        self.ratios = dict(ratios or {ITEM_ONE: 50.0, ITEM_TWO: 50.0})
        self.position = position
        self.proportions = dict(proportions or {})
        self.net_value_display = net_value_display
        self.est = est if est is not None else {}
        self.cny_ref = FakeCnyRef()
        self.source = None
        self.holdings_date = None
        self.net_value_string = None

    # 引用对象接口
    def get_holdings_ratio_array(self):
        return dict(self.ratios)

    def get_position(self):
        return self.position

    def count_holdings(self):
        return len(self.ratios)

    def get_stock_id(self):
        return 1

    def get_cny_ref(self):
        return self.cny_ref

    def get_net_value_display(self, net_value):
        return self.net_value_display

    def get_percentage_text(self, divisor=None, dividend=None):
        if not divisor:
            return ""
        return "%.2f%%" % ((float(dividend) / float(divisor) - 1.0) * 100.0)

    def get_proportion_array(self, date, prev_date):
        return self.proportions.get((date, prev_date))

    # 两个会改状态的 setter（PHP 那边也是直接改 $this）
    def set_holdings_date(self, date):
        self.holdings_date = date

    def set_net_value_string(self, value):
        self.net_value_string = value

    def _est_net_value(self, date=None, realtime=False):
        return self.est.get(date, 1.0)


class NumberDisplayTest(unittest.TestCase):
    def test_positive_is_black(self):
        self.assertEqual(number_display(12.34, 2), ("12.34", ""))

    def test_negative_is_red(self):
        """``number_format(-3.5, 0)`` = ``-4`` —— PHP 是 half **away from zero**，
        Python 的 ``'%.0f' % -3.5`` 给 ``-4`` 也是同一个结果，但 -2.5 会分叉
        （PHP 给 -3），所以下面另钉一例。"""
        self.assertEqual(number_display(-3.5, 0), ("-4", "red"))
        self.assertEqual(number_display(-2.5, 0), ("-3", "red"))

    def test_within_tolerance_is_grey(self):
        """``0.5 / 10^precision`` 之内算"零"，灰字。"""
        self.assertEqual(number_display(0.004, 2), ("0", "grey"))
        self.assertEqual(number_display(-0.004, 2), ("0", "grey"))

    def test_trailing_zeros_stripped(self):
        """``StockGetPriceDisplay`` 会 rtrim('0')，与 ``number_format`` 不同。"""
        self.assertEqual(number_display(50.0, 0), ("50", ""))
        self.assertEqual(number_display(1.0, 2), ("1", ""))

    def test_negative_zero_becomes_zero(self):
        self.assertEqual(number_display(-0.0004, 3), ("0", "grey"))


class HoldingRatioCellTest(unittest.TestCase):
    def test_match_keeps_true(self):
        cell, match = holding_ratio_cell(49.6, "50", True)
        self.assertEqual(cell, ("50", ""))
        self.assertTrue(match)

    def test_mismatch_turns_false(self):
        cell, match = holding_ratio_cell(62.4, "50", True)
        self.assertEqual(cell, ("62", ""))
        self.assertFalse(match)

    def test_negative_is_red(self):
        cell, match = holding_ratio_cell(-12.4, "0", True)
        self.assertEqual(cell, ("-12", "red"))


class PositionCellTest(unittest.TestCase):
    def test_normal_position(self):
        cell, match = position_cell(0.95, 0.9512, {}, True)
        self.assertEqual(cell, ("0.95", ""))
        self.assertTrue(match)

    def test_mismatch(self):
        cell, match = position_cell(0.95, 1.0, {}, True)
        self.assertEqual(cell, ("1.00", ""))
        self.assertFalse(match)

    def test_out_of_range_is_red(self):
        """区间外（解崩了）标红。

        同时 match 也会被打成 False —— 因为 ``number_format`` 与库里仓位不等
        这一条仍然生效（PHP 里两个判断是**并列**的，不是互斥）。
        """
        cell, match = position_cell(0.95, 0.1, {}, True)
        self.assertEqual(cell, ("0.10", "red"))
        self.assertFalse(match)

    def test_admin_link_only_when_mismatch(self):
        calls = []

        def href(ratios, position):
            calls.append((ratios, position))
            return "确认更新"

        cell, match = position_cell(0.95, 1.0, {"A": 50.0}, True, True, href)
        self.assertEqual(cell, ("确认更新", ""))
        #: 回调拿的是**未格式化的仓位**（float 1.0），不是文本 "1.00" ——
        #: 载荷要 ratio × position，先看文本再反解会丢精度。
        self.assertEqual(calls, [({"A": 50.0}, 1.0)])
        self.assertIsInstance(cell[0], UpdateCell)

        cell, _ = position_cell(0.95, 0.95, {}, True, True, href)
        self.assertEqual(cell, ("0.95", ""))
        self.assertEqual(len(calls), 1)

    def test_no_link_for_non_admin(self):
        cell, _ = position_cell(0.95, 1.0, {}, True, False, lambda r, p: "不该出现")
        self.assertEqual(cell, ("1.00", ""))
        self.assertNotIsInstance(cell[0], UpdateCell)


class UpdatePayloadTest(unittest.TestCase):
    """写回条载荷的三个口径（``exhaustiveholdingscn.php:36-43``）。"""

    SYMBOLS = {181: "GLD", 182: "SPY"}

    def test_payload_multiplies_ratio_by_position(self):
        """``number_format($fRatio * $fPos, 2)`` —— 比例 × 仓位，值带两位尾零。"""
        payload = encode_holdings_payload({181: 50.0}, 1.005, self.SYMBOLS.get)
        self.assertEqual(payload, '"GLD":"50.25"')

    def test_payload_uses_symbols_not_ids(self):
        payload = encode_holdings_payload({181: 50.0, 182: 50.0}, 1.0,
                                         self.SYMBOLS.get)
        self.assertEqual(payload, '"GLD":"50.00","SPY":"50.00"')

    def test_payload_falls_back_to_id_when_symbol_missing(self):
        """查不到符号时给 ``#<id>``，不静默少一格（少一格会让 fPos 变小）。"""
        payload = encode_holdings_payload({999: 100.0}, 1.0, self.SYMBOLS.get)
        self.assertEqual(payload, '"#999":"100.00"')

    def test_payload_empty(self):
        self.assertEqual(encode_holdings_payload({}, 1.0, self.SYMBOLS.get), "")

    def test_question_strips_double_quotes(self):
        """问句里去掉双引号（``GetOnClickLink`` 的 ``str_replace``）。"""
        question = confirm_update_question('"GLD":"50.00"', "1.01")
        self.assertEqual(question, "确认更新持仓GLD:50.00和仓位1.01？")
        self.assertNotIn('"', question)


class PercentageCellTest(unittest.TestCase):
    def test_rise_is_red_fall_is_green(self):
        self.assertEqual(percentage_cell("1.23%"), ("1.23%", "red"))
        self.assertEqual(percentage_cell("-1.23%"), ("-1.23%", "green"))

    def test_empty_is_gray(self):
        self.assertEqual(percentage_cell(""), ("", "gray"))


class SwitchDatePairsTest(unittest.TestCase):
    def test_pairs_each_switch_date_with_the_next(self):
        sql = FakeNetSql(
            closes={(1, "2026-10-24"): 1.30, (1, "2026-10-23"): 1.25,
                    (1, "2026-10-22"): 1.20, (1, "2026-10-21"): 1.20},
            dates=["2026-10-24", "2026-10-22"],
        )
        pairs = switch_date_pairs(sql, 1)
        # 24 号配它的下一个反转日 22 号；22 号后面没有反转日 → 不出行
        self.assertEqual(pairs, [("2026-10-24", 1.30, "2026-10-22")])

    def test_empty_dates(self):
        self.assertEqual(switch_date_pairs(FakeNetSql(), 1), [])


class CurrentRowTest(unittest.TestCase):
    def test_row_is_always_yellow(self):
        """``EchoMatchTableColumn($ar)`` 不传第二参 → 整行黄底。"""
        calculator = ExhaustiveHoldingsCalculator(FakeRef(), FakeNetSql(), 0.01)
        cells, background = calculator.current_row()
        self.assertEqual(background, "yellow")
        self.assertEqual(cells, [("50", ""), ("50", ""), ("0.95", "")])


class ExhaustiveRowTest(unittest.TestCase):
    def _calculator(self, threshold=0.01, **kwargs):
        ref = FakeRef(proportions={
            ("2026-10-24", "2026-10-22"): [1.02, 1.01],
            ("2026-10-22", "2026-10-21"): [1.01, 1.03],
            ("2026-10-21", "2026-10-20"): [0.99, 0.98],
        }, **kwargs)
        sql = FakeNetSql(
            closes={(1, "2026-10-24"): 1.30, (1, "2026-10-22"): 1.25,
                    (1, "2026-10-21"): 1.20, (1, "2026-10-20"): 1.17,
                    (8, "2026-10-24"): 6.72, (8, "2026-10-22"): 6.70,
                    (8, "2026-10-21"): 6.71, (8, "2026-10-20"): 6.69},
            dates=["2026-10-24", "2026-10-22", "2026-10-21"],
        )
        return ExhaustiveHoldingsCalculator(ref, sql, threshold), ref

    def test_below_threshold_yields_three_cells(self):
        """净值涨跌不到阈值 → 只出日期/净值/涨幅三格（PHP 就少这几格）。"""
        calculator, _ = self._calculator(threshold=0.5)
        cells, match = calculator.exhaustive_row("2026-10-24", 1.30, "2026-10-22")
        self.assertEqual(len(cells), 3)
        self.assertFalse(match)

    def test_first_rows_have_five_cells_until_solution(self):
        calculator, ref = self._calculator()
        cells, _ = calculator.exhaustive_row("2026-10-24", 1.30, "2026-10-22")
        # 日期 + 净值 + 涨幅 + 估值涨幅 = 4 格（方程还不够 2 组，没解出比例）
        self.assertEqual(len(cells), 4)
        # 副作用：持仓日与净值串被改成前一日的
        self.assertEqual(ref.holdings_date, "2026-10-22")
        self.assertEqual(float(ref.net_value_string), 1.25)

    def test_second_row_produces_ratios_and_position(self):
        calculator, _ = self._calculator()
        calculator.exhaustive_row("2026-10-24", 1.30, "2026-10-22")
        cells, _ = calculator.exhaustive_row("2026-10-22", 1.25, "2026-10-21")
        # 4 格（日期/净值/涨幅/估值涨幅）+ 2 个持仓 + 仓位 = 7 格
        self.assertEqual(len(cells), 7)
        # 假数据的比例是编的，解出来的仓位没有经济含义（可能落在 0.25–1.25 之外
        # 而被标红）—— 这里只钉格式：两位小数、`number_format` 保留尾零。
        self.assertRegex(cells[-1][0], r"^-?\d+\.\d\d$")


class VerifyRowTest(unittest.TestCase):
    def _calculator(self):
        ref = FakeRef(proportions={
            ("2026-10-24", "2026-10-22"): [1.02, 1.01],
            ("2026-10-22", "2026-10-21"): [1.01, 1.03],
        })
        sql = FakeNetSql(
            closes={(1, "2026-10-24"): 1.30, (1, "2026-10-22"): 1.25,
                    (1, "2026-10-21"): 1.20,
                    (8, "2026-10-24"): 6.72, (8, "2026-10-22"): 6.70,
                    (8, "2026-10-21"): 6.71},
            dates=["2026-10-24", "2026-10-22", "2026-10-21"],
        )
        return ExhaustiveHoldingsCalculator(ref, sql, 0.01)

    def test_done_only_after_enough_equations(self):
        calculator = self._calculator()
        _, _, done = calculator.verify_row("2026-10-24", 1.30, "2026-10-22")
        self.assertFalse(done)
        cells, _, done = calculator.verify_row("2026-10-22", 1.25, "2026-10-21")
        self.assertTrue(done)
        # 日期 + 净值 + 涨幅 + 2 个持仓 + 仓位 = 6 格
        # （验证表**没有**「误差」列，所以比穷举表少一格）
        self.assertEqual(len(cells), 6)

    def test_never_done_twice(self):
        """``count($arEq) == $iCount`` 严格等于 —— 解过一次就再进不去。"""
        calculator = self._calculator()
        calculator.verify_row("2026-10-24", 1.30, "2026-10-22")
        calculator.verify_row("2026-10-22", 1.25, "2026-10-21")
        # 第二次再喂同一组日期（模拟 while 循环里重复命中）
        _, _, done = calculator.verify_row("2026-10-21", 1.20, "2026-10-20")
        self.assertFalse(done)


class ParseThresholdTest(unittest.TestCase):
    def test_default_constant(self):
        self.assertEqual(DEFAULT_EXHAUSTIVE_ROWS, 25)

    def test_parse(self):
        self.assertEqual(parse_threshold("1.0"), 1.0)
        self.assertEqual(parse_threshold(""), 0.0)
        self.assertEqual(parse_threshold(None), 0.0)


if __name__ == "__main__":
    unittest.main()
