# -*- coding: utf-8 -*-
"""分组列表的升降序（``/fund/qdiietf?sort=...&order=...``）。

2026-10-26 用户要求「表格所有栏目增加升降序选项」。这是**本地增强**，
不是迁移 —— PHP 的基金列表页没有排序（``?sort=symbol`` / ``?sort=premium``
是 ``/qdii`` 那一页估值表的参数）。

分两层测：

* 纯逻辑（``sort_value`` / ``sort_rows`` / ``build_column_defs``）——
  按手工构造的行字典钉排序口径，不连库；
* 路由（``/fund/qdiietf?sort=...``）—— 钉链接形状、箭头、非法参数不炸。
"""

from __future__ import annotations

import unittest

from web.app import create_app
from web.services.fund_list_service import (
    FUND_LIST_COLUMNS,
    SORT_ASC,
    SORT_DESC,
    FundListService,
    build_column_defs,
    sort_rows,
    sort_value,
)

from .test_web import make_source


def _row(label, **values):
    """造一行：字典键与 ``FUND_LIST_COLUMNS`` 的键一致。

    第一个位置参数是"这行的标签"，默认同时当符号用；要区分两者就传
    ``symbol=`` 覆盖（``_row("A", symbol="SZ000001")`` 表示标签 A、符号 SZ000001）。
    """
    row = {key: "" for key, _, _ in FUND_LIST_COLUMNS}
    row["symbol"] = label
    row.update(values)
    return row


def _symbols(rows):
    return [row["symbol"] for row in rows]


class SortValueTest(unittest.TestCase):
    """显示文本 -> 可比较值。"""

    def test_number_strips_percent_sign(self):
        self.assertEqual(17.9, sort_value("17.9%", "number"))
        self.assertEqual(-2.64, sort_value("-2.64%", "number"))

    def test_number_accepts_plain_and_zero(self):
        self.assertEqual(1.545, sort_value("1.545", "number"))
        # 涨跌幅为 0 时 format_change_percent 输出裸 "0"（没有 %）
        self.assertEqual(0.0, sort_value("0", "number"))

    def test_empty_and_junk_are_none(self):
        for text in ("", "   ", None, "N/A"):
            self.assertIsNone(sort_value(text, "number"), text)

    def test_text_kind_is_not_parsed_as_number(self):
        # 文本列不剥 %、也不转 float —— 否则 "000001" 之类会变成 1.0
        self.assertEqual("17.9%", sort_value("17.9%", "text"))
        self.assertEqual("000001", sort_value("000001", "text"))

    def test_dates_and_times_compare_as_text(self):
        """ISO 日期与定宽时间的字典序就是时间序，不必解析成 datetime。"""
        dates = ["2026-10-22", "2026-09-30", "2026-10-06"]
        self.assertEqual(
            ["2026-09-30", "2026-10-06", "2026-10-22"],
            sorted(dates, key=lambda d: sort_value(d, "date")),
        )
        times = ["13:05:00", "09:30:00", "15:00:00"]
        self.assertEqual(
            ["09:30:00", "13:05:00", "15:00:00"],
            sorted(times, key=lambda t: sort_value(t, "time")),
        )


class SortRowsTest(unittest.TestCase):
    def setUp(self):
        self.rows = [
            _row("A", net_value="1.545", premium="17.9%", name="甲"),
            _row("B", net_value="", premium="-3.2%", name="乙"),
            _row("C", net_value="2.5", premium="", name="丙"),
        ]

    def test_desc_puts_biggest_first(self):
        self.assertEqual(
            ["C", "A", "B"],
            _symbols(sort_rows(self.rows, "net_value", SORT_DESC)),
        )

    def test_asc_puts_smallest_first(self):
        self.assertEqual(
            ["A", "C", "B"],
            _symbols(sort_rows(self.rows, "net_value", SORT_ASC)),
        )

    def test_empty_values_always_last(self):
        """空值恒排最后 —— 缺净值的基金不该因为升序就霸占第一屏。"""
        for order in (SORT_ASC, SORT_DESC):
            self.assertEqual("B", _symbols(sort_rows(self.rows, "net_value", order))[-1])

    def test_negative_percent_sorts_below_zero(self):
        rows = [
            _row("X", change="0.44%"),
            _row("Y", change="-2.64%"),
            _row("Z", change="1.8%"),
        ]
        self.assertEqual(
            ["Y", "X", "Z"], _symbols(sort_rows(rows, "change", SORT_ASC)))
        self.assertEqual(
            ["Z", "X", "Y"], _symbols(sort_rows(rows, "change", SORT_DESC)))

    def test_text_column_sorts_lexicographically(self):
        rows = [_row(s, symbol=s) for s in ("SZ000003", "SH513100", "SZ000001")]
        self.assertEqual(
            ["SH513100", "SZ000001", "SZ000003"],
            _symbols(sort_rows(rows, "symbol", SORT_ASC)),
        )

    def test_ties_keep_original_order(self):
        """相等值保持原顺序（Python 排序稳定）—— 于是看到的是分组定义里的顺序。"""
        rows = [_row("P", position="1"), _row("Q", position="1.00"),
                _row("R", position="0.5")]
        self.assertEqual(
            ["P", "Q", "R"], _symbols(sort_rows(rows, "position", SORT_DESC)))

    def test_unknown_key_returns_rows_untouched(self):
        self.assertEqual(_symbols(self.rows), _symbols(sort_rows(self.rows, "nope")))

    def test_does_not_mutate_input(self):
        original = _symbols(self.rows)
        sort_rows(self.rows, "net_value", SORT_DESC)
        self.assertEqual(original, _symbols(self.rows))


class ColumnDefsTest(unittest.TestCase):
    def test_every_column_has_a_link(self):
        defs = build_column_defs()
        self.assertEqual(len(FUND_LIST_COLUMNS), len(defs))
        self.assertEqual(
            [label for _, label, _ in FUND_LIST_COLUMNS],
            [item["label"] for item in defs],
        )

    def test_number_column_first_click_is_desc(self):
        """数值列先给降序（先看最大的），文本/日期先给升序。"""
        by_key = {item["key"]: item for item in build_column_defs()}
        self.assertEqual(SORT_DESC, by_key["net_value"]["next_order"])
        self.assertEqual(SORT_DESC, by_key["change"]["next_order"])
        self.assertEqual(SORT_ASC, by_key["symbol"]["next_order"])
        self.assertEqual(SORT_ASC, by_key["net_value_date"]["next_order"])

    def test_sorted_column_toggles(self):
        asc = {item["key"]: item for item in build_column_defs("net_value", SORT_ASC)}
        self.assertTrue(asc["net_value"]["is_sorted"])
        self.assertEqual(SORT_DESC, asc["net_value"]["next_order"])
        self.assertEqual("▲", asc["net_value"]["mark"])

        desc = {item["key"]: item for item in build_column_defs("net_value", SORT_DESC)}
        self.assertEqual(SORT_ASC, desc["net_value"]["next_order"])
        self.assertEqual("▼", desc["net_value"]["mark"])

    def test_unsorted_columns_have_no_mark(self):
        defs = build_column_defs("net_value", SORT_DESC)
        marks = [item["mark"] for item in defs if item["key"] != "net_value"]
        self.assertEqual([""] * len(marks), marks)


class GroupSortTest(unittest.TestCase):
    """``FundListService.get_group(sort=..., order=...)`` 的参数清洗。"""

    def setUp(self):
        self.service = FundListService(make_source(), auto_calibrate=False)

    def test_default_order_is_empty(self):
        output = self.service.get_group("qdiietf")
        self.assertEqual("", output["sort_key"])
        self.assertEqual("", output["sort_order"])
        self.assertEqual("", output["sort_label"])
        self.assertEqual("", output["sort_order_text"])

    def test_unknown_key_is_ignored(self):
        """URL 是用户可编的：不认识的列键当没传，不抛异常。"""
        output = self.service.get_group("qdiietf", sort="'; DROP TABLE stock; --")
        self.assertEqual("", output["sort_key"])
        self.assertEqual(
            [row["symbol"] for row in self.service.get_group("qdiietf")["rows"]],
            [row["symbol"] for row in output["rows"]],
        )

    def test_unknown_order_falls_back_to_type_default(self):
        output = self.service.get_group("qdiietf", sort="net_value", order="sideways")
        self.assertEqual(SORT_DESC, output["sort_order"])
        self.assertEqual("降序", output["sort_order_text"])

    def test_sort_label_and_text(self):
        output = self.service.get_group("qdiietf", sort="price", order="asc")
        self.assertEqual("最新价", output["sort_label"])
        self.assertEqual("升序", output["sort_order_text"])


class RouteTest(unittest.TestCase):
    def setUp(self):
        source = make_source()
        self.app = create_app(
            {"TESTING": True},
            fund_list_service_factory=lambda: FundListService(source,
                                                              auto_calibrate=False),
        )
        self.client = self.app.test_client()

    def _symbols_in_order(self, html):
        """按出现顺序取行首的符号链接 —— 表里第一列就是符号。"""
        import re

        return re.findall(r'<td class="symbol">\s*<a href="[^"]+">(\w+)</a>', html)

    def test_headers_link_to_both_orders(self):
        html = self.client.get("/fund/qdiietf").get_data(as_text=True)
        self.assertIn("/fund/qdiietf?sort=symbol&amp;order=asc", html)
        self.assertIn("/fund/qdiietf?sort=net_value&amp;order=desc", html)

    def test_sorted_page_marks_the_column(self):
        html = self.client.get(
            "/fund/qdiietf?sort=net_value&order=desc").get_data(as_text=True)
        self.assertIn('class="sortable sorted"', html)
        self.assertIn("▼", html)
        self.assertIn("当前：按「基金净值」降序排列", html)

    def test_sorted_column_link_toggles_to_asc(self):
        html = self.client.get(
            "/fund/qdiietf?sort=net_value&order=desc").get_data(as_text=True)
        self.assertIn("sort=net_value&amp;order=asc", html)

    def test_sorting_changes_row_order(self):
        plain = self.client.get("/fund/qdiietf").get_data(as_text=True)
        asc = self.client.get(
            "/fund/qdiietf?sort=symbol&order=asc").get_data(as_text=True)
        desc = self.client.get(
            "/fund/qdiietf?sort=symbol&order=desc").get_data(as_text=True)

        plain_rows = self._symbols_in_order(plain)
        self.assertTrue(plain_rows, "解析不到行首符号，断言会变成空转")
        self.assertEqual(sorted(plain_rows), self._symbols_in_order(asc))
        self.assertEqual(
            sorted(plain_rows, reverse=True), self._symbols_in_order(desc))

    def test_bogus_params_still_render(self):
        for query in ("?sort=bogus", "?sort=net_value&order=bogus", "?sort="):
            response = self.client.get("/fund/qdiietf" + query)
            self.assertEqual(200, response.status_code, query)
            self.assertIn("共 25 只", response.get_data(as_text=True))

    def test_reset_link_disappears_without_sort(self):
        with_sort = self.client.get(
            "/fund/qdiietf?sort=symbol").get_data(as_text=True)
        self.assertIn("恢复默认顺序", with_sort)
        plain = self.client.get("/fund/qdiietf").get_data(as_text=True)
        self.assertNotIn("恢复默认顺序", plain)


class LayoutRegressionTest(unittest.TestCase):
    """``main_class`` 的 block 别再漏到 <head> 里（2026-10-26 顺手修的）。

    写在 head 的 block 会把 "wide" 渲染成 <head> 里的一段文本，浏览器遇到
    它提前结束 head，把 "wide" 当 body 首行显示出来。
    """

    def setUp(self):
        source = make_source()
        self.app = create_app(
            {"TESTING": True},
            fund_list_service_factory=lambda: FundListService(source,
                                                              auto_calibrate=False),
        )
        self.client = self.app.test_client()

    def test_head_has_no_stray_text(self):
        html = self.client.get("/fund/qdiietf").get_data(as_text=True)
        head = html.split("</head>")[0]
        self.assertNotIn("wide", head)
        self.assertIn('<main class="site-main wide">', html)

    def test_narrow_page_has_empty_main_class(self):
        html = self.client.get("/status").get_data(as_text=True)
        self.assertIn('<main class="site-main ">', html)


if __name__ == "__main__":
    unittest.main()
