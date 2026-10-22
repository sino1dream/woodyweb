"""仓位估算页（``fundpositioncn.php``）的测试。

这一页与「净值记录」（``netvaluehistory``）共用同一个 ``EchoNetValueItem()``，
唯一的区别是**每一行的"上期"取上一个净值拐点**（:meth:`get_switch_dates`），
而不是上一交易日。所以本文件的重点不是重测行里的数字，而是：

* **行选择**：哪些日期出行、每行的 prev 是谁；
* **行数截断**：``$iTotal == $iNum`` 数的是候选行、不是"写出了仓位的行"；
* **阈值**：没过 ``input`` 的行仍然整行显示，只是仓位格空着；
* 混合型（``in_arrayLofMix``）走 ``_QdiiMixGetPosition``。

数字刻意选成能手算的：汇率取常数 7.0，于是
``仓位 = 净值涨跌% / 标的涨跌%``（汇率变动被抹平成 0），
期望值可以逐个口算核对，不必依赖另一份实现。
"""

from __future__ import annotations

import os
import sys
import unittest

WEB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON_DIR = os.path.dirname(WEB_DIR)
for _path in (PYTHON_DIR, WEB_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from app import create_app  # noqa: E402
from core.stockdata import FundProfile, MemoryFundDataSource  # noqa: E402
from ui.uicolumn import POS_NETVALUE_DIFF  # noqa: E402
from web.services.history_service import HistoryService  # noqa: E402
from web.tests.test_history import FakeConnection  # noqa: E402


# SZ161130 的净值：10-19(1.10) → 10-16(1.05) 极小 → 10-15(1.20) 极大
# → 10-14(1.15) 极小 → 10-13(1.30) 未闭合的尾巴
SWITCH_DATES = ["2026-10-19", "2026-10-16", "2026-10-15", "2026-10-14"]

TABLES = {
    "netvaluehistory": [
        {"id": 1, "stock_id": "1", "date": "2026-10-19", "close": 1.10},
        # **非拐点交易日**：必须留着。少了它，fundposition 与 netvaluehistory
        # 对同一天的"上期"会碰巧一样（两个"上一个"恰巧是同一天），
        # 于是测不出本页最核心的那点差别。真数据里这种日子是绝大多数。
        {"id": 11, "stock_id": "1", "date": "2026-10-17", "close": 1.08},
        {"id": 2, "stock_id": "1", "date": "2026-10-16", "close": 1.05},
        {"id": 3, "stock_id": "1", "date": "2026-10-15", "close": 1.20},
        {"id": 4, "stock_id": "1", "date": "2026-10-14", "close": 1.15},
        {"id": 5, "stock_id": "1", "date": "2026-10-13", "close": 1.30},
        # 汇率恒为 7.0 —— 抹平汇率成分，仓位就等于"净值涨跌% ÷ 标的涨跌%"
        {"id": 6, "stock_id": "8", "date": "2026-10-19", "close": 7.0},
        {"id": 12, "stock_id": "8", "date": "2026-10-17", "close": 7.0},
        {"id": 7, "stock_id": "8", "date": "2026-10-16", "close": 7.0},
        {"id": 8, "stock_id": "8", "date": "2026-10-15", "close": 7.0},
        {"id": 9, "stock_id": "8", "date": "2026-10-14", "close": 7.0},
        {"id": 10, "stock_id": "8", "date": "2026-10-13", "close": 7.0},
    ],
    "stockhistory": [
        {"id": 1, "stock_id": "1", "date": "2026-10-19", "close": 1.10,
         "volume": 100, "adjclose": 1.10},
    ],
    "fundest": [],
}

#: ^NDX 的收盘价。数值刻意取成整百/整十，配合上面那条注释里的手算期望值。
EST_CLOSE = {
    ("7", "2026-10-19"): 100.0,
    ("7", "2026-10-17"): 95.0,
    ("7", "2026-10-16"): 90.0,
    ("7", "2026-10-15"): 120.0,
    ("7", "2026-10-14"): 100.0,
}


def make_source(position: float = 0.95) -> MemoryFundDataSource:
    """SZ161130 是 QDII（us 区），est 是 ``^NDX``、汇率是 ``USCNY``。"""
    return MemoryFundDataSource(
        stocks={"SZ161130": "1", "USCNY": "8", "^NDX": "7"},
        names={"SZ161130": "易方达纳斯达克100LOF", "USCNY": "美元人民币",
               "^NDX": "纳斯达克100指数"},
        profiles={
            "SZ161130": FundProfile(
                symbol="SZ161130", stock_id="1", position=position,
                official_date="2026-10-19", cny_est=7.0, is_lof=True,
                est_symbol="^NDX",
            ),
        },
        positions={"1": position},
        history_close=EST_CLOSE,
    )


def make_service(position: float = 0.95) -> HistoryService:
    return HistoryService(make_source(position), FakeConnection(TABLES))


def cell_text(cell) -> str:
    """单元格可能是 ``(文本, 颜色)`` 元组；只取文本便于断言。"""
    return cell[0] if isinstance(cell, tuple) else str(cell)


class RowSelectionTest(unittest.TestCase):
    """``_echoFundPositionData()`` 的行选择（``fundpositioncn.php:14-38``）。"""

    def test_switch_dates_of_the_fixture(self):
        """先钉住拐点本身：后面的行断言都建立在它之上。"""
        from sql.historytable import NetValueHistorySql

        netvalue = NetValueHistorySql(FakeConnection(TABLES))
        self.assertEqual([str(d) for d in netvalue.get_switch_dates("1")], SWITCH_DATES)

    def test_rows_pair_each_switch_with_the_older_one(self):
        table = make_service().get_table("fundposition", "SZ161130")
        dates = [cell_text(row.cells[0]) for row in table["rows"]]
        #: 最后一个拐点（10-14）**没有**更早的拐点可配对，PHP 在这里 break，
        #: 所以它不是一行；尾巴上的 10-13 更不会出现。
        self.assertEqual(dates, ["2026-10-19", "2026-10-16", "2026-10-15"])

    def test_prev_date_is_the_older_switch_not_the_previous_trading_day(self):
        """与 netvaluehistory 的分水岭：这里的"上期"隔着好几个交易日。

        同一天 10-19 在两页上的涨幅不同就是证据：本页的上期是上一个**拐点**
        10-16（净值 1.05，+4.76%），净值记录页的上期是**上一交易日**
        10-17（净值 1.08，+1.85%）。中间隔着 10-17 这个非拐点交易日。
        """
        service = make_service()
        here = service.get_table("fundposition", "SZ161130")["rows"][0]
        there = service.get_table("netvaluehistory", "SZ161130")["rows"][0]
        self.assertEqual(cell_text(here.cells[0]), "2026-10-19")
        self.assertEqual(cell_text(there.cells[0]), "2026-10-19")
        self.assertTrue(cell_text(here.cells[2]).startswith("4.76"), cell_text(here.cells[2]))
        self.assertTrue(cell_text(there.cells[2]).startswith("1.85"), cell_text(there.cells[2]))

    def test_columns_are_the_same_as_netvaluehistory(self):
        """列组成复用 ``GetNetValueTableColumn()``，两页必须一致。"""
        service = make_service()
        position = service.get_table("fundposition", "SZ161130")["columns"]
        netvalue_history = service.get_table("netvaluehistory", "SZ161130")["columns"]
        self.assertEqual(position, netvalue_history)
        self.assertEqual([c["text"] for c in position],
                         ["日期", "净值", "涨幅", "USCNY", "涨幅", "标的净值", "涨幅", "仓位"])

    def test_empty_netvalue_table_gives_no_rows(self):
        service = HistoryService(make_source(), FakeConnection({"netvaluehistory": [],
                                                                "fundest": []}))
        table = service.get_table("fundposition", "SZ161130")
        self.assertEqual(table["rows"], [])
        self.assertEqual(table["total"], 0)


class RowLimitTest(unittest.TestCase):
    """``if ($iTotal == $iNum) break`` —— 数的是候选行。"""

    def test_default_page_size_shows_all_candidates(self):
        table = make_service().get_table("fundposition", "SZ161130")
        self.assertEqual(len(table["rows"]), 3)

    def test_num_caps_the_candidate_rows_not_the_records(self):
        """``num=2`` 只出两行，哪怕净值表有 5 条记录。

        换成 SQL LIMIT 就会变成"截前 2 条原始记录"（那时只会命中 10-19 一行），
        语义完全不同 —— 这条用例就是挡这个退化。
        """
        table = make_service().get_table("fundposition", "SZ161130", 0, 2)
        self.assertEqual([cell_text(r.cells[0]) for r in table["rows"]],
                         ["2026-10-19", "2026-10-16"])

    def test_num_zero_means_all(self):
        table = make_service().get_table("fundposition", "SZ161130", 0, 0)
        self.assertEqual(len(table["rows"]), 3)

    def test_total_is_the_record_count(self):
        """``total`` 给模板算"总数"，所以它数的是**记录**不是行数。

        6 条净值记录（含 10-17 那个非拐点日）里只有 3 行会显示出来。
        """
        table = make_service().get_table("fundposition", "SZ161130")
        self.assertEqual(table["total"], 6)


class ThresholdTest(unittest.TestCase):
    """``?input=`` 阈值（``EchoEditInputForm`` → ``$fInput``）。"""

    def test_position_formula_with_flat_fx(self):
        """汇率恒定时 仓位 = 净值涨跌% ÷ 标的涨跌%，三个值都是手算的。"""
        rows = make_service().get_table("fundposition", "SZ161130")["rows"]
        got = [cell_text(row.cells[-1]) for row in rows]
        # 10-19:  4.7619% / 11.1111% = 0.4285…
        # 10-16: -12.5%   / -25%      = 0.50
        # 10-15:  4.3478% / 20%       = 0.2173…
        self.assertEqual(got, ["0.43", "0.50", "0.22"])

    def test_below_threshold_keeps_the_row_but_blanks_the_position(self):
        """**没过阈值的行照样整行显示**（PHP 结尾无条件 ``EchoMatchTableColumn``）。

        ``input=5`` 下只有 -12.5% 那一行过关。把整行丢掉是这里最容易犯的错：
        ``$bWritten`` 只管 CSV 写不写仓位列，不管表上有没有这一行。
        """
        rows = make_service().get_table("fundposition", "SZ161130", f_input=5.0)["rows"]
        self.assertEqual([cell_text(r.cells[0]) for r in rows],
                         ["2026-10-19", "2026-10-16", "2026-10-15"])
        self.assertEqual([cell_text(r.cells[-1]) for r in rows], ["", "0.50", ""])

    def test_looser_threshold_lets_more_rows_through(self):
        rows = make_service().get_table("fundposition", "SZ161130", f_input=1.0)["rows"]
        self.assertEqual([cell_text(r.cells[-1]) for r in rows], ["0.43", "0.50", "0.22"])

    def test_default_matches_php_constant(self):
        with_input = make_service().get_table("fundposition", "SZ161130",
                                              f_input=POS_NETVALUE_DIFF)["rows"]
        without = make_service().get_table("fundposition", "SZ161130")["rows"]
        self.assertEqual([r.cells for r in with_input], [r.cells for r in without])

    def test_match_flag_compares_against_the_stored_position(self):
        """反算值与库里登记仓位一致时整行标黄（``EchoMatchTableColumn``）。"""
        rows = make_service(position=0.22).get_table("fundposition", "SZ161130")["rows"]
        self.assertEqual([bool(r.match) for r in rows], [False, False, True])


class FormulaNoteTest(unittest.TestCase):
    """页面下方的「算法说明」：公式 + 步骤 + 闸门 + 一行代入示例。

    PHP 没有这一段（生产端页面上仓位凭空出现，没法验算），本版新增。
    公式常量与 :func:`qdii_get_stock_position` 必须同步改，所以这里把
    常量本身也钉住 —— 改了实现忘了改说明，测试会红。
    """

    def test_formula_text_matches_the_php_source(self):
        """``仓位 = 净值涨跌% ÷ 标的涨跌%`` —— ``netvaluehistoryparagraph.php:13``。

        顺序**不能**写反：PHP 是 ``$fPercent / $fEstPercent``（净值 ÷ 标的），
        写反了 QDII 的仓位会变成 1.05 这种大于 1 的数。
        """
        note = make_service().get_table("fundposition", "SZ161130")["formula"]
        self.assertEqual(note["formula"], "仓位 = 净值涨跌% ÷ 标的涨跌%")
        self.assertEqual(note["net_percent"], "净值涨跌% = StockGetPercentage(上期净值, 净值)")
        self.assertIn("(dividend ÷ divisor − 1) × 100", note["percentage_def"])
        self.assertEqual(len(note["steps"]), 5)
        self.assertEqual(len(note["gates"]), 3)

    def test_sample_is_the_first_row_that_got_a_position(self):
        """示例取第一行算出仓位的行，不是第一行。"""
        note = make_service().get_table("fundposition", "SZ161130")["formula"]
        sample = note["sample"]
        self.assertEqual(sample["date"], "2026-10-19")
        #: 本页的"上期"是上一个**拐点**（10-16），不是上一交易日（10-17）
        self.assertEqual(sample["prev_date"], "2026-10-16")
        self.assertTrue(sample["net_percent"].startswith("4.76"), sample["net_percent"])
        #: 汇率恒 7.0 → 标的涨跌% 就是 ^NDX 的涨跌 100/90 − 1 = 11.11%
        self.assertTrue(sample["est_percent"].startswith("11.11"), sample["est_percent"])
        self.assertEqual(sample["position"], "0.43")
        self.assertEqual(sample["est_symbol"], "^NDX")
        self.assertEqual(sample["cny_symbol"], "USCNY")
        self.assertFalse(sample["mixed"])

    def test_sample_values_are_plain_text(self):
        """``format_price_display`` 返回 ``(文本, 颜色)``；说明段只取文本。

        忘了剥颜色，页面上会打出 ``('4.6176', 'black')`` 这种元组字符串。
        """
        sample = make_service().get_table("fundposition", "SZ161130")["formula"]["sample"]
        for key in ("net", "prev_net", "est", "est_prev", "cny", "cny_prev"):
            self.assertNotIsInstance(sample[key], tuple)
            self.assertNotIn("(", sample[key])
            self.assertNotIn("black", sample[key])

    def test_formula_survives_a_page_without_any_position(self):
        """阈值大到没有一行过闸门：公式照给、示例为 None。

        默认 4% 下 SZ161125 就是整页都过不了闸门 —— 那时如果把公式也藏掉，
        页面看着像坏了。
        """
        note = make_service().get_table("fundposition", "SZ161130", f_input=99.0)["formula"]
        self.assertIsNotNone(note)
        self.assertIsNone(note["sample"])

    def test_only_this_kind_has_a_formula(self):
        """其余历史页是"读库显示"，没有反算，也就不该有说明段。"""
        service = make_service()
        self.assertNotIn("formula", service.get_table("netvaluehistory", "SZ161130"))
        self.assertNotIn("formula", service.get_table("stockhistory", "SZ161130"))


class PagingTest(unittest.TestCase):
    """这一页在 PHP 侧没有翻页（``$acct->GetNum()`` 只截行数）。"""

    def test_paged_flag_is_off(self):
        self.assertFalse(make_service().get_table("fundposition", "SZ161130")["paged"])

    def test_other_kinds_keep_paging(self):
        self.assertTrue(make_service().get_table("netvaluehistory", "SZ161130")["paged"])


class RouteTest(unittest.TestCase):
    def setUp(self):
        source = make_source()
        self.app = create_app(
            {"TESTING": True},
            history_service_factory=lambda: HistoryService(source, FakeConnection(TABLES)),
        )
        self.client = self.app.test_client()

    def test_page_renders(self):
        response = self.client.get("/history/fundposition/SZ161130")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("仓位估算", html)
        self.assertIn("2026-10-19", html)

    def test_threshold_input_form_is_rendered(self):
        """对应 PHP 的 ``EchoEditInputForm``（默认显示 POS_NETVALUE_DIFF）。"""
        response = self.client.get("/history/fundposition/SZ161130")
        html = response.get_data(as_text=True)
        self.assertIn('name="input"', html)
        self.assertIn('value="4.0"', html)

    def test_input_query_param_feeds_the_threshold(self):
        """``?input=5`` 只有 -12.5% 那一行出仓位数字。"""
        response = self.client.get("/history/fundposition/SZ161130?input=5")
        html = response.get_data(as_text=True)
        self.assertIn("0.50", html)
        self.assertNotIn("0.43", html)

    def test_bad_input_falls_back_to_default(self):
        response = self.client.get("/history/fundposition/SZ161130?input=abc")
        self.assertEqual(response.status_code, 200)
        self.assertIn("0.43", response.get_data(as_text=True))

    def test_page_shows_a_record_count_but_paging_block_is_absent(self):
        """净值记录页摆得出翻页区块（含总数），本页连区块都没有。

        不直接断言"下一页"三个字：fixture 只有 6 条记录而默认一页 10 行，
        ``next_start`` 本来就是空的，其它页也渲染不出那个链接。
        """
        html = self.client.get("/history/fundposition/SZ161130").get_data(as_text=True)
        self.assertNotIn('class="paging"', html)
        other = self.client.get("/history/netvaluehistory/SZ161130").get_data(as_text=True)
        self.assertIn('class="paging"', other)

    def test_formula_block_is_rendered(self):
        """页面上要能看见公式与代入示例（本版新增，生产端没有这一段）。"""
        html = self.client.get("/history/fundposition/SZ161130").get_data(as_text=True)
        self.assertIn("算法说明", html)
        self.assertIn("StockGetPercentage", html)
        self.assertIn("代入示例", html)
        #: 示例那一行的两个百分比（净值涨跌 / 标的涨跌）与仓位
        self.assertIn("4.76", html)
        self.assertIn("11.11", html)
        self.assertIn("0.43", html)

    def test_no_sample_when_nothing_passes_the_gate(self):
        """没有一行算出仓位时：公式还在、「代入示例」那张表不出现。"""
        html = self.client.get("/history/fundposition/SZ161130?input=99").get_data(as_text=True)
        self.assertIn("算法说明", html)
        self.assertNotIn("代入示例", html)

    def test_api_returns_the_same_rows(self):
        api = self.client.get("/api/history/fundposition/SZ161130")
        self.assertEqual(api.status_code, 200)
        payload = api.get_json()
        self.assertEqual([row["cells"][0][0]
                          if isinstance(row["cells"][0], list) else row["cells"][0]
                          for row in payload["rows"]],
                         ["2026-10-19", "2026-10-16", "2026-10-15"])


if __name__ == "__main__":
    unittest.main()
