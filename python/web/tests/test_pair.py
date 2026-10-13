"""`python/web/services/pair_service.py` 的单元测试。

对应 PHP ``php/ui/ahparagraph.php``（94 行）的 ``EchoAbParagraph`` /
``EchoAhParagraph`` / ``EchoAdrhParagraph``。

用假 ref，不打网络、不连数据库。重点钉住三件事：

1. 三种配对的**列集合**不同（``adr`` 多一列仓位、用美元而不是人民币）；
2. 行是**变长**的（有配对标的才追加、比价非 0 才追加、有仓位才追加）；
3. 超过 2 行才按比价排序。

最后一条 :class:`RowLengthTest` 记的是 PHP 的一个**真实错位 bug**：
``ab`` / ``ah`` 的行会比表头多一格。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[3]
PYTHON_DIR = ROOT_DIR / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from web.bootstrap import install_paths  # noqa: E402

install_paths()

from web.services.pair_service import (  # noqa: E402
    PAIR_KINDS,
    PAIR_SORT_THRESHOLD,
    PairService,
    build_pair_columns,
    build_pair_table,
)


class FakeRef:
    """够用的假 ref：只实现本段落会调到的方法。

    刻意**不**实现 ``get_pair_ref`` 以外的东西时用来测降级路径。
    这条路径在 CK2 之后仍然有意义：``MyPairReference`` 虽已迁，但
    ``FundPairReference`` 的期货自动校准（CK2b）与 ``HoldingsReference``
    还没迁，那些分支仍要靠 ``_invoke`` 返回 ``None`` 安静降级。
    """

    def __init__(
        self,
        symbol: str = "SZ162411",
        ratio: float = 1.0,
        pair=None,
        cny=None,
        stock_id: int = 1,
        has_data: bool = True,
        price: float = 4.796,
    ):
        self.symbol = symbol
        self.ratio = ratio
        self.pair = pair
        self.cny = cny
        self.stock_id = stock_id
        self.has_data = has_data
        self.price = price

    # -- 本段落用得到的 ---------------------------------------------------
    def get_symbol(self):
        return self.symbol

    def get_stock_id(self):
        return self.stock_id

    def get_my_stock_link(self):
        return '<a href="/mystock/%s">%s</a>' % (self.symbol, self.symbol)

    def get_pair_ref(self):
        return self.pair

    def get_price_ratio(self):
        return self.ratio

    def get_cny_ref(self):
        return self.cny

    def est_from_pair(self, pair_price=None, cny=None):
        if pair_price is None:
            return None
        return float(pair_price) * (float(cny) if cny else 1.0)

    def get_price_display(self, disp=None, prev=None, precision=None):
        return "%.3f" % (self.price if disp is None else float(disp))

    def get_price(self):
        return self.price

    # -- 宽屏那五格用得到的 -----------------------------------------------
    def has_data(self):
        return self.has_data

    def get_percentage_text(self):
        return "1.23%"

    def get_date(self):
        return "2026-10-02"

    def get_time_hm(self):
        return "15:00"


class FakeCny:
    def __init__(self, val=7.1):
        self.val = val

    def get_val(self):
        return self.val


def _paired(ratio: float = 1.0, symbol: str = "SZ162411", position: bool = True, **kwargs):
    """造一个带配对标的的 ref。"""
    pair = FakeRef(symbol="H" + symbol[2:], price=10.0)
    ref = FakeRef(symbol=symbol, ratio=ratio, pair=pair, **kwargs)
    return ref


class ColumnTest(unittest.TestCase):
    """三种配对的列集合。"""

    def test_ah_columns(self):
        columns = build_pair_columns("ah")
        self.assertEqual(
            [column["text"] for column in columns],
            ["代码", "H股代码", "H股价格", "H股人民币￥", "A/H比价"],
        )

    def test_ab_columns_use_b_shares(self):
        columns = build_pair_columns("ab")
        self.assertEqual(
            [column["text"] for column in columns],
            ["代码", "B股代码", "B股价格", "B股人民币￥", "A/B比价"],
        )

    def test_adr_columns_have_usd_and_position(self):
        """ADR 用美元计价，且**比另外两种多一列仓位**。"""
        columns = build_pair_columns("adr")
        self.assertEqual(
            [column["text"] for column in columns],
            ["代码", "H股代码", "H股价格", "H股美元$", "ADR/H比价", "仓位"],
        )
        self.assertEqual(len(columns), len(build_pair_columns("ah")) + 1)

    def test_wide_inserts_five_quote_columns(self):
        """``_getPairColumn($bWide)`` 在代码列之后插入行情五列。"""
        narrow = build_pair_columns("ah")
        wide = build_pair_columns("ah", wide=True)
        self.assertEqual(len(wide), len(narrow) + 5)
        self.assertEqual([c["text"] for c in wide[:6]], ["代码", "价格", "涨幅", "日期", "时间", "名称"])

    def test_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            build_pair_columns("xyz")

    def test_every_declared_kind_builds(self):
        for kind in PAIR_KINDS:
            self.assertTrue(build_pair_columns(kind))


class EmptyTest(unittest.TestCase):
    """``_echoPairParagraph`` 开头那句 ``if ($iCount == 0) return;``。"""

    def test_no_refs_returns_none(self):
        self.assertIsNone(PairService().get_table([]))

    def test_all_none_refs_returns_none(self):
        self.assertIsNone(PairService().get_table([None, None]))

    def test_none_refs_are_filtered_out(self):
        table = PairService().get_table([None, FakeRef(), None])
        self.assertEqual(table["count"], 1)


class RowTest(unittest.TestCase):
    """行的四段拼装。"""

    def test_without_pair_ref_row_is_just_the_link(self):
        """配对标的缺失时（``$pair_ref = false``）后面几段全部不输出。"""
        row = PairService().get_table([FakeRef()])["rows"][0]
        self.assertEqual(len(row.cells), 1)

    def test_full_row_has_six_cells(self):
        ref = _paired()
        table = PairService(position_reader=lambda stock_id: "0.95").get_table([ref])
        cells = table["rows"][0].cells
        self.assertEqual(len(cells), 6)
        # 本标的链接 / 配对链接 / 配对价格 / 反推估值 / 比价 / 仓位
        self.assertIn("SZ162411", cells[0])
        self.assertIn("H162411", cells[1])
        self.assertEqual(cells[2], "10.000")
        self.assertEqual(cells[3], "10.000")   # est = 10.0 * 1.0（无汇率回落 1.0）
        self.assertEqual(cells[4], ("1", "grey"))  # GetRatioDisplay(1.0) 平价
        self.assertEqual(cells[5], "0.95")

    def test_cny_is_multiplied_in(self):
        """``EstFromPair(配对价, 汇率)`` —— 汇率要真的参与计算。"""
        ref = _paired(cny=FakeCny(2.0))
        row = PairService().get_table([ref])["rows"][0]
        self.assertEqual(row.cells[3], "20.000")   # 10.0 * 2.0

    def test_zero_ratio_stops_before_ratio_cell(self):
        """``if ($fRatio = ...)`` —— 比价为 0 时比价格与仓位格都不输出。"""
        ref = _paired(ratio=0.0)
        row = PairService(position_reader=lambda stock_id: "0.95").get_table([ref])["rows"][0]
        self.assertEqual(len(row.cells), 4)
        self.assertNotIn("0.95", row.cells)

    def test_position_is_read_by_stock_id(self):
        seen = []

        def reader(stock_id):
            seen.append(stock_id)
            return "0.95"

        PairService(position_reader=reader).get_table([_paired(stock_id=42)])
        self.assertEqual(seen, [42])

    def test_position_is_trimmed(self):
        """PHP 是 ``rtrim0($strPos)`` —— 尾巴上的 0 要去掉。"""
        row = PairService(position_reader=lambda stock_id: "0.9500").get_table(
            [_paired()]
        )["rows"][0]
        self.assertEqual(row.cells[-1], "0.95")

    def test_without_position_reader_no_position_cell(self):
        row = PairService().get_table([_paired()])["rows"][0]
        self.assertEqual(len(row.cells), 5)

    def test_position_reader_failure_does_not_break_the_row(self):
        def reader(stock_id):
            raise RuntimeError("模拟数据库异常")

        row = PairService(position_reader=reader).get_table([_paired()])["rows"][0]
        self.assertEqual(len(row.cells), 5)


class WideTest(unittest.TestCase):
    """``$bWide`` 的两条分支。"""

    def test_wide_appends_five_cells_and_puts_name_last(self):
        class Source:
            def get_stock_name(self, symbol):
                return "华宝油气"

        row = PairService(Source()).get_table([_paired()], wide=True)["rows"][0]
        # 链接 + 行情四格 + 名称 + 配对三格 + 比价
        self.assertEqual(row.cells[1], "4.796")
        self.assertEqual(row.cells[2], ("1.23%", "red"))
        self.assertEqual(row.cells[3], "2026-10-02")
        self.assertEqual(row.cells[4], "15:00")
        self.assertEqual(row.cells[5], "华宝油气")
        # 链接(1) + 行情五格(5) + 配对三格(3) + 比价(1) = 10；
        # 没给 position_reader，所以没有仓位格
        self.assertEqual(len(row.cells), 10)

    def test_narrow_moves_name_to_first_cell_tooltip(self):
        """``RefEchoTableColumn()``：窄屏时名称不占列，挂到首格 ``title`` 上。"""
        class Source:
            def get_stock_name(self, symbol):
                return "华宝油气"

        row = PairService(Source()).get_table([_paired()])["rows"][0]
        self.assertEqual(row.title, "华宝油气")
        self.assertEqual(len(row.cells), 5)

    def test_wide_has_no_tooltip(self):
        """PHP 是 ``$bWide ? false : SqlGetStockName(...)`` —— 宽屏不挂 tooltip。"""
        class Source:
            def get_stock_name(self, symbol):
                return "华宝油气"

        row = PairService(Source()).get_table([_paired()], wide=True)["rows"][0]
        self.assertEqual(row.title, "")


class SortTest(unittest.TestCase):
    """``if ($iCount > 2)`` 才按比价排序。"""

    def test_two_refs_keep_their_order(self):
        """两行时不排序 —— 传进来的顺序就是页面上的顺序。"""
        refs = [_paired(ratio=0.9, symbol="SZ000001"), _paired(ratio=0.5, symbol="SZ000002")]
        rows = PairService().get_table(refs)["rows"]
        self.assertIn("SZ000001", rows[0].cells[0])
        self.assertIn("SZ000002", rows[1].cells[0])

    def test_three_refs_are_sorted_by_ratio(self):
        refs = [
            _paired(ratio=0.9, symbol="SZ000001"),
            _paired(ratio=0.5, symbol="SZ000002"),
            _paired(ratio=0.7, symbol="SZ000003"),
        ]
        rows = PairService().get_table(refs)["rows"]
        symbols = [row.cells[0] for row in rows]
        self.assertTrue(symbols[0].endswith("SZ000002</a>"))
        self.assertTrue(symbols[1].endswith("SZ000003</a>"))
        self.assertTrue(symbols[2].endswith("SZ000001</a>"))

    def test_threshold_is_two(self):
        """钉住 PHP 的 ``> 2``：改阈值属于行为变更，要在测试里显式改。"""
        self.assertEqual(PAIR_SORT_THRESHOLD, 2)


class DegradationTest(unittest.TestCase):
    """ref 上缺方法时的降级路径。

    CK2 之后 ``MyPairReference`` 已迁，但 ``FundPairReference`` 的期货自动校准
    （CK2b）与 ``HoldingsReference`` 仍未迁 —— 那些 ref 传进来时照样会缺方法，
    这组用例钉住的就是"缺方法不能抛异常、只能留空格子"这个契约。
    """

    def test_missing_methods_do_not_raise(self):
        class Bare:
            def get_symbol(self):
                return "SZ162411"

            def get_my_stock_link(self):
                return "link"

        row = PairService().get_table([Bare()])["rows"][0]
        self.assertEqual(row.cells, ["link"])

    def test_missing_est_from_pair_leaves_a_blank(self):
        """拿不到估值就留空，**不编造数字**。"""
        pair = FakeRef(symbol="H162411", price=10.0)

        class NoEst(FakeRef):
            def est_from_pair(self, pair_price=None, cny=None):
                raise AttributeError("未实现")

        row = PairService().get_table([NoEst(pair=pair)])["rows"][0]
        self.assertEqual(row.cells[3], "")

    def test_pair_price_missing_leaves_a_blank(self):
        class NoPrice:
            def get_my_stock_link(self):
                return "pair-link"

            def get_price(self):
                return None

        row = PairService().get_table([FakeRef(pair=NoPrice())])["rows"][0]
        # 本标的链接 / 配对链接 / 配对价格(空) / 估值(空) / 比价
        self.assertEqual(len(row.cells), 5)
        self.assertEqual(row.cells[2], "")
        self.assertEqual(row.cells[3], "")


class TableTest(unittest.TestCase):
    """整张表的形状。"""

    def test_id_and_kind(self):
        table = build_pair_table([_paired()], kind="ah")
        self.assertEqual(table["id"], "ahtable")
        self.assertEqual(table["kind"], "ah")

    def test_cell_count_is_the_longest_row(self):
        table = build_pair_table([_paired(), FakeRef()])
        self.assertEqual(table["count"], 2)
        self.assertEqual(table["cell_count"], 5)

    def test_title_defaults_to_empty(self):
        self.assertEqual(build_pair_table([_paired()])["title"], "")
        self.assertEqual(build_pair_table([_paired()], title="A/H比较")["title"], "A/H比较")


class RowLengthTest(unittest.TestCase):
    """⚠️ PHP 的错位 bug：``ab`` / ``ah`` 的行比表头**多一格**。

    ``_echoPairItem()`` 无论哪种配对都会在最后追加"仓位"格，但只有 ``adr``
    的列定义里有 ``TableColumnPosition``。于是：

    * ``adr``：6 列表头 / 6 格行 —— 对齐；
    * ``ab`` ``ah``：5 列表头 / 6 格行 —— **多出来的那格没有表头**。

    本版**照抄这个行为**（不砍掉那一格，因为那等于静默丢数据），
    并把 :class:`TableTest.test_cell_count_is_the_longest_row` 暴露出来让
    调用方能发现。这条用例的作用是：如果哪天有人"顺手"把列定义对齐了，
    这里会失败并提醒先看这一段。
    """

    def test_adr_is_aligned(self):
        table = build_pair_table(
            [_paired()], kind="adr", position_reader=lambda stock_id: "0.95"
        )
        self.assertEqual(len(table["columns"]), 6)
        self.assertEqual(table["cell_count"], 6)

    def test_ab_and_ah_are_one_cell_longer(self):
        for kind in ("ab", "ah"):
            table = build_pair_table(
                [_paired()], kind=kind, position_reader=lambda stock_id: "0.95"
            )
            self.assertEqual(len(table["columns"]), 5)
            self.assertEqual(table["cell_count"], 6)


if __name__ == "__main__":
    unittest.main()
