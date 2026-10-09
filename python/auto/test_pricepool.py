"""`python/auto/pricepool.py` 的单元测试。

对应 PHP ``php/ui/pricepoolparagraph.php``（136 行）里的
``PriceGoal`` / ``PricePool`` / ``_echoPricePoolParagraph()``。

全部是纯计数断言，不联网、不碰 Flask —— 这个段落本来就只是数数。

最重要的一组用例是 :class:`TransposeTest`：PHP 的 ``$bTranspose`` **只换标签
不换数据**，本版改成真转置，两者必须在测试里明确区分开。
"""

from __future__ import annotations

import unittest

from pricepool import (
    PRICE_POOL_CHANGE_LABELS,
    PRICE_POOL_PREMIUM_LABELS,
    PRICE_POOL_TABLE_ID,
    PRICE_POOL_TABLE_WIDTH,
    PriceGoal,
    PricePool,
    build_price_pool_columns,
    build_price_pool_rows,
    build_price_pool_table,
    price_pool_from_rows,
)

# 六个 (溢价, 涨跌) 样本。刻意做成**不对称**的：
# 溢价钱两笔、平价一笔、折价三笔，且折价里有涨有跌。
# 不对称是为了让「真转置」与「PHP 只换标签」的结果明显不同 ——
# 对称数据会让两者撞出相同的数字，从而掩盖那个 bug。
SAMPLES = (
    (0.01, 0.02),    # 溢价 + 涨
    (0.02, 0.03),    # 溢价 + 涨
    (-0.01, -0.05),  # 折价 + 跌
    (-0.02, -0.06),  # 折价 + 跌
    (-0.03, 0.04),   # 折价 + 涨
    (0.0, 0.01),     # 平价 + 涨
)


def _pool(samples=SAMPLES) -> PricePool:
    pool = PricePool()
    for value, compare in samples:
        pool.on_data(value, compare)
    return pool


class PriceGoalTest(unittest.TestCase):
    """``PriceGoal::AddData()`` 的三分支。"""

    def test_positive_is_higher(self):
        goal = PriceGoal()
        goal.add_data(0.5)
        self.assertEqual((goal.higher, goal.unchanged, goal.lower), (1, 0, 0))

    def test_negative_is_lower(self):
        goal = PriceGoal()
        goal.add_data(-0.5)
        self.assertEqual((goal.higher, goal.unchanged, goal.lower), (0, 0, 1))

    def test_zero_counts_as_unchanged(self):
        """PHP 的 ``empty(0.0)`` 为真 —— **0 是"不变"而不是"跌"**。

        写成 ``value is None`` 就会把 0 错判成跌，整列数字会偏。
        """
        goal = PriceGoal()
        goal.add_data(0.0)
        goal.add_data(0)
        goal.add_data(None)
        self.assertEqual((goal.higher, goal.unchanged, goal.lower), (0, 3, 0))

    def test_total_counts_every_call(self):
        """``iTotal`` 在 PHP 里是无条件加一的，不受分档影响。"""
        goal = PriceGoal()
        for value in (0.5, 0.0, -0.5):
            goal.add_data(value)
        self.assertEqual(goal.total, 3)

    def test_counts_order_matches_change_labels(self):
        """``counts()`` 的顺序必须是 (涨, 不变, 跌)，与列头标签对齐。"""
        goal = PriceGoal()
        goal.add_data(0.1)
        goal.add_data(0.0)
        goal.add_data(-0.1)
        self.assertEqual(goal.counts(), (1, 1, 1))
        self.assertEqual(len(goal.counts()), len(PRICE_POOL_CHANGE_LABELS))


class PricePoolTest(unittest.TestCase):
    """``PricePool::OnData()`` 的分桶。"""

    def test_positive_goes_to_h_goal(self):
        pool = PricePool()
        pool.on_data(0.01, 0.02)
        self.assertEqual(pool.h_goal.total, 1)
        self.assertEqual(pool.u_goal.total, 0)
        self.assertEqual(pool.l_goal.total, 0)

    def test_zero_premium_goes_to_u_goal(self):
        """溢价恰好为 0 是"平价"桶，不是折价桶（``empty()`` 语义）。"""
        pool = PricePool()
        pool.on_data(0.0, 0.02)
        self.assertEqual(pool.u_goal.total, 1)
        self.assertEqual(pool.l_goal.total, 0)

    def test_negative_goes_to_l_goal(self):
        pool = PricePool()
        pool.on_data(-0.01, 0.02)
        self.assertEqual(pool.l_goal.total, 1)

    def test_goals_order_matches_premium_labels(self):
        pool = _pool()
        self.assertEqual(len(pool.goals), len(PRICE_POOL_PREMIUM_LABELS))
        # h=溢价 u=平价 l=折价
        self.assertEqual([goal.total for goal in pool.goals], [2, 1, 3])

    def test_every_sample_is_counted_exactly_once(self):
        """三个桶的 total 之和必须等于样本数 —— 一个数都不许漏、也不许重复计。"""
        pool = _pool()
        self.assertEqual(sum(goal.total for goal in pool.goals), len(SAMPLES))
        self.assertEqual(
            sum(sum(goal.counts()) for goal in pool.goals), len(SAMPLES)
        )


class RowTest(unittest.TestCase):
    """``transpose=False``（默认，等价 PHP 的 ``false``）：行=溢价轴。"""

    def test_rows_are_premium_axis(self):
        rows = build_price_pool_rows(_pool())
        self.assertEqual(rows[0], ["溢价", 2, 2, 0, 0])
        self.assertEqual(rows[1], ["平价", 1, 1, 0, 0])
        self.assertEqual(rows[2], ["折价", 3, 1, 0, 2])

    def test_second_cell_is_the_premium_bucket_total(self):
        """第二格"天数"是该溢价档的总天数，不是全表天数。"""
        rows = build_price_pool_rows(_pool())
        for index, row in enumerate(rows):
            self.assertEqual(row[1], _pool().goals[index].total)

    def test_row_labels_are_premium_labels(self):
        rows = build_price_pool_rows(_pool())
        self.assertEqual([row[0] for row in rows], list(PRICE_POOL_PREMIUM_LABELS))


class TransposeTest(unittest.TestCase):
    """``transpose=True``：本版真转置，与 PHP 的同名参数行为不同。"""

    def test_rows_become_change_axis(self):
        rows = build_price_pool_rows(_pool(), transpose=True)
        self.assertEqual([row[0] for row in rows], list(PRICE_POOL_CHANGE_LABELS))

    def test_transposed_matrix_is_the_real_transpose(self):
        """转置后的 cell(涨跌 i, 溢价 j) 必须等于转置前的 cell(溢价 j, 涨跌 i)。"""
        pool = _pool()
        plain = build_price_pool_rows(pool)
        turned = build_price_pool_rows(pool, transpose=True)

        for i in range(3):
            for j in range(3):
                # 跳过前两格（行标签 + 天数）
                self.assertEqual(turned[i][2 + j], plain[j][2 + i])

    def test_transposed_total_is_the_change_bucket_total(self):
        """转置后第二格是"该涨跌档的总天数"，而不是溢价档的。"""
        rows = build_price_pool_rows(_pool(), transpose=True)
        # 涨 4 笔（溢价2 + 平价1 + 折价1）、不变 0 笔、跌 2 笔
        self.assertEqual([row[1] for row in rows], [4, 0, 2])

    def test_grand_total_is_invariant_under_transpose(self):
        """转置不该改变样本总数 —— 转置漏/重一格时这条会先炸。"""
        pool = _pool()
        self.assertEqual(
            sum(row[1] for row in build_price_pool_rows(pool)), len(SAMPLES)
        )
        self.assertEqual(
            sum(row[1] for row in build_price_pool_rows(pool, transpose=True)),
            len(SAMPLES),
        )

    def test_legacy_labels_reproduce_the_php_bug(self):
        """``legacy_labels=True`` 复刻 PHP「只换标签不换数据」。

        PHP 默认 ``$bTranspose = true`` 走的就是这条路：行标写成涨跌轴，
        数据却还是溢价轴，于是 9 格里 6 格张冠李戴。这条用例把差异钉住 ——
        如果哪天 legacy 和真转置算出一样的数，说明这条用例的样本退化成对称了。
        """
        pool = _pool()
        legacy = build_price_pool_rows(pool, transpose=True, legacy_labels=True)
        correct = build_price_pool_rows(pool, transpose=True)

        self.assertNotEqual(legacy, correct)
        # legacy 的行标签是涨跌轴，数据却是溢价轴（对比 RowTest 的三行）
        self.assertEqual([row[0] for row in legacy], list(PRICE_POOL_CHANGE_LABELS))
        self.assertEqual(legacy[0], ["涨", 2, 2, 0, 0])     # 天数=溢价档的 2
        self.assertEqual(correct[0], ["涨", 4, 2, 1, 1])    # 天数=涨档的 4

    def test_legacy_without_transpose_equals_plain(self):
        """PHP 的 ``$bTranspose = false`` 是正确的，那时 legacy 与默认完全一致。"""
        pool = _pool()
        self.assertEqual(
            build_price_pool_rows(pool, transpose=False, legacy_labels=True),
            build_price_pool_rows(pool),
        )


class ColumnTest(unittest.TestCase):
    """五列表头。"""

    def test_widths_are_the_php_heredoc_widths(self):
        columns = build_price_pool_columns("SZ162411")
        self.assertEqual([column["width"] for column in columns], [150, 60, 120, 120, 120])
        self.assertEqual(sum(column["width"] for column in columns), PRICE_POOL_TABLE_WIDTH)

    def test_first_two_columns(self):
        columns = build_price_pool_columns("SZ162411")
        self.assertEqual(columns[0]["text"], "SZ162411交易")
        self.assertEqual(columns[1]["text"], "天数")

    def test_trailing_columns_default_to_change_axis(self):
        columns = build_price_pool_columns("SZ162411")
        self.assertEqual([column["text"] for column in columns[2:]], ["涨", "不变", "跌"])

    def test_trading_symbol_is_a_bare_prefix(self):
        """PHP 是 ``{$strTradingSymbol}{$arColumnEx[0]}`` —— **中间没有分隔符**。"""
        columns = build_price_pool_columns("SZ162411", "QQQ")
        self.assertEqual([column["text"] for column in columns[2:]], ["QQQ涨", "QQQ不变", "QQQ跌"])

    def test_premium_first_swaps_the_trailing_labels(self):
        columns = build_price_pool_columns("SZ162411", "", premium_first=True)
        self.assertEqual([column["text"] for column in columns[2:]], ["溢价", "平价", "折价"])

    def test_no_column_has_a_color(self):
        """这个段落 PHP 里就没给列上色（手写 ``<td class=c1>``），别加。"""
        for column in build_price_pool_columns("SZ162411"):
            self.assertEqual(column["color"], "")


class TableTest(unittest.TestCase):
    """一步到位的 :func:`build_price_pool_table`。"""

    def test_shape(self):
        table = build_price_pool_table(_pool(), "SZ162411", "QQQ")
        self.assertEqual(table["id"], PRICE_POOL_TABLE_ID)
        self.assertEqual(table["width"], PRICE_POOL_TABLE_WIDTH)
        self.assertEqual(len(table["columns"]), 5)
        self.assertEqual(len(table["rows"]), 3)
        for row in table["rows"]:
            self.assertEqual(len(row), len(table["columns"]))

    def test_transpose_switches_both_axes(self):
        """列头与行标签必须**同时**换 —— PHP 只换了一半，这就是那个 bug。"""
        table = build_price_pool_table(_pool(), "SZ162411", transpose=True)
        self.assertEqual([c["text"] for c in table["columns"][2:]], ["溢价", "平价", "折价"])
        self.assertEqual([row[0] for row in table["rows"]], ["涨", "不变", "跌"])

    def test_title_defaults_to_empty(self):
        self.assertEqual(build_price_pool_table(_pool(), "S")["title"], "")
        self.assertEqual(build_price_pool_table(_pool(), "S", title="价格分布")["title"], "价格分布")


class FromRowsTest(unittest.TestCase):
    """:func:`price_pool_from_rows` —— 两个调用方写盘的列序不一样。"""

    def test_default_order_is_value_then_compare(self):
        """``netvalueclosecn.php:12``：``OnData(arWord[1], arWord[2])`` 溢价在前。"""
        pool = price_pool_from_rows([(0.01, 0.02)])
        self.assertEqual(pool.h_goal.total, 1)

    def test_thanous_paradox_writes_compare_first(self):
        """``_thanousparadox.php:13``：``OnData(arWord[2], arWord[1])`` —— 涨跌写在第 1 列。

        所以它的 ``value_index=1``、``compare_index=0``。顺序写反的话
        溢价/涨跌会整体互换，出来的矩阵是转置过的，页面上看不出来但统计全错。
        """
        pool = price_pool_from_rows([(0.02, 0.01)], value_index=1, compare_index=0)
        self.assertEqual(pool.h_goal.total, 1)
        self.assertEqual(pool.h_goal.higher, 1)

    def test_short_rows_are_skipped(self):
        """列数不够的行跳过，而不是抛异常 —— 读 CSV 时末尾空行很常见。"""
        pool = price_pool_from_rows([(0.01,), (0.02, 0.03)])
        self.assertEqual(sum(goal.total for goal in pool.goals), 1)

    def test_unparsable_value_counts_as_zero(self):
        """``floatval('abc')`` 在 PHP 里也是 0，落到"平价"/"不变"。"""
        pool = price_pool_from_rows([("abc", "def")])
        self.assertEqual(pool.u_goal.total, 1)
        self.assertEqual(pool.u_goal.unchanged, 1)

    def test_matches_manual_feeding(self):
        """从行建池与逐条 ``on_data`` 必须得到同样的结果。"""
        self.assertEqual(
            [goal.counts() for goal in price_pool_from_rows(SAMPLES).goals],
            [goal.counts() for goal in _pool().goals],
        )


if __name__ == "__main__":
    unittest.main()
