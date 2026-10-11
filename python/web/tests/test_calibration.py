"""基金详情页「校准记录」表（`web/services/calibration_service.py`）的测试。

对应 `php/ui/calibrationhistoryparagraph.php`。**全部离线**：取数由
`history_factory` 注入的假对象顶替，不打网络、不需要数据库、不需要 flask。

钉的是四件事：

1. **复用而不是重写** —— 必须调到 `HistoryService` 的 `calibrationhistory`，
   那样 `IsFundA` 判列、对冲倍数表那一套才不会有两份实现；
2. **没有记录就返回 `None`** —— 这是相对 PHP 的偏离（PHP 会留一张空表头）；
3. DOM id 沿用 PHP 的 `<符号>calibrationhistory`；
4. 链接是可选项 —— 没传 link_builder 就不该出现在结构里。
"""

import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[3]
PYTHON_DIR = ROOT_DIR / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from web.bootstrap import install_paths  # noqa: E402

install_paths()

from web.services.calibration_service import (  # noqa: E402
    CALIBRATION_ID_SUFFIX,
    CALIBRATION_KIND,
    CALIBRATION_TITLE,
    DEFAULT_CALIBRATION_ROWS,
    CalibrationService,
)


class _Row:
    """``historytable.TableRow`` 的同形状替身（只要 ``cells``）。"""

    def __init__(self, *cells):
        self.cells = list(cells)
        self.background = ""
        self.title = ""
        self.href = None
        self.hrefs = {}


class FakeHistoryService:
    """``HistoryService`` 的替身：只实现 ``get_table``。

    ``records`` 是**还没分行**的原始记录，形状照 ``CalibrationSql.get_all()``
    的 dict 来（``date`` / ``close`` / ``time`` / ``num``），由本替身自行组析构；
    这样测试用例写起来贴近真实返回，也顺带确认服务层没有自己再造 `TableRow`。
    """

    def __init__(self, records=None, columns=None, total=None, fail=False):
        self.records = [] if records is None else records
        self.columns = list(CALIBRATION_COLUMNS) if columns is None else columns
        self.total = total
        self.fail = fail
        #: 每次调用的入参，用来钉"两个调用点都传 (0, 1)"
        self.calls = []

    def get_table(self, kind, symbol, start=0, num=0):
        self.calls.append((kind, symbol, start, num))
        if self.fail:
            raise RuntimeError("数据库连接断了")
        rows = [_Row(record["date"], record.get("close"), record.get("time"),
                     record.get("num")) for record in self.records]
        return {
            "kind": kind,
            "symbol": symbol,
            "columns": self.columns,
            "rows": rows,
            "total": len(rows) if self.total is None else self.total,
        }


def _column(text, color="", width=0):
    """``web.services.history_service._column`` 的同形状替身。

    **必须**是 dict 而不是纯字符串：`_data_table.html` 读的是
    ``column.text`` / ``column.color`` / ``column.width``。早先这里写成
    字符串，测试照样过，页面却渲染出一排空表头 —— 所以把形状钉在这儿。
    """
    return {"text": text, "color": color, "width": width}


#: 与 ``history_service._columns_calibrationhistory(False)`` 同形状：
#: 日期 / 校准值 / 时间 / 次数（宽度也照抄，改了这里会立刻测出来）。
CALIBRATION_COLUMNS = [
    _column("日期", "", 100),
    _column("校准值", "", 140),
    _column("时间", "", 60),
    _column("次数", "", 50),
]

#: 基金类符号在上面四列之后追加的两列（``IsFundA()`` 为真时才有）。
FUND_EXTRA_COLUMNS = [_column("净值", "olive", 90), _column("对冲值", "navy", 80)]


def make_service(records=None, columns=None, total=None, fail=False,
                 link_builder=None, **kwargs):
    """建一个服务，取数走假对象。"""
    history = FakeHistoryService(records=records, columns=columns,
                                 total=total, fail=fail)
    service = CalibrationService(history_factory=lambda: history,
                                 link_builder=link_builder, **kwargs)
    return service, history


def _record(date="2026-10-08", close="16513.43", time="21:03:00", num="1"):
    return {"date": date, "close": close, "time": time, "num": num}


class ReuseTest(unittest.TestCase):
    """必须走 ``HistoryService``，不再抄第二份取数。"""

    def test_reads_the_calibrationhistory_kind(self):
        service, history = make_service(records=[_record()])
        self.assertIsNotNone(service.table("SZ161125"))
        self.assertEqual(history.calls, [(CALIBRATION_KIND, "SZ161125", 0, 1)])

    def test_both_call_sites_ask_for_one_row(self):
        """PHP 两处都是 ``(0, 1)`` —— 只显示最近一条，不显示全部。"""
        service, history = make_service(records=[_record()])
        service.table("^GSPC")
        self.assertEqual(history.calls[0][2:], (0, DEFAULT_CALIBRATION_ROWS))

    def test_num_can_be_raised_by_the_caller(self):
        service, history = make_service(records=[_record()])
        service.table("^GSPC", 0, 5)
        self.assertEqual(history.calls[0][2:], (0, 5))

    def test_extra_columns_from_the_history_service_pass_through(self):
        """基金类符号多"净值 / 对冲值"两列 —— 判列在取数那一侧，这里不能重算。"""
        columns = CALIBRATION_COLUMNS + FUND_EXTRA_COLUMNS
        service, _ = make_service(records=[_record()], columns=columns)
        table = service.table("SZ161125")

        self.assertEqual(table["columns"], columns)
        self.assertEqual(len(table["columns"]), 6)

    def test_columns_carry_the_colors_the_macro_reads(self):
        """「净值」是 olive、「对冲值」是 navy —— 与 production 端的表头配色一致。"""
        columns = CALIBRATION_COLUMNS + FUND_EXTRA_COLUMNS
        service, _ = make_service(records=[_record()], columns=columns)
        table = service.table("SZ161125")

        self.assertEqual([column["color"] for column in table["columns"]],
                         ["", "", "", "", "olive", "navy"])


class NoRecordMeansNoTableTest(unittest.TestCase):
    """有意偏离 PHP：没有记录就整段跳过，不留一张空表头。"""

    def test_no_rows_returns_none(self):
        service, _ = make_service(records=[])
        self.assertIsNone(service.table("SZ161125"))

    def test_missing_db_is_none_not_500(self):
        """校准记录是可选装饰 —— 取不到不该让整页挂掉。"""
        service, _ = make_service(fail=True)
        self.assertIsNone(service.table("SZ161125"))

    def test_factory_returning_none_is_none(self):
        service = CalibrationService(history_factory=lambda: None)
        self.assertIsNone(service.table("SZ161125"))

    def test_empty_symbol_does_not_even_query(self):
        service, history = make_service(records=[_record()])
        self.assertIsNone(service.table(""))
        self.assertEqual(history.calls, [])


class ShapeTest(unittest.TestCase):
    """返回结构与 ``_data_table.html`` 的契约。"""

    def setUp(self):
        self.service, _ = make_service(records=[_record()])
        self.table = self.service.table("SZ161125")

    def test_id_matches_php(self):
        """PHP 拼的是 ``$strSymbol.'calibrationhistory'``。"""
        self.assertEqual(self.table["id"], "SZ161125" + CALIBRATION_ID_SUFFIX)

    def test_id_follows_the_est_symbol_at_the_other_call_site(self):
        """``smaparagraph.php:218`` 传的是中间层标的，id 跟着它变。"""
        self.assertEqual(self.service.table("^GSPC")["id"],
                         "^GSPC" + CALIBRATION_ID_SUFFIX)

    def test_required_keys_for_the_macro(self):
        for key in ("id", "columns", "rows", "count", "symbol"):
            self.assertIn(key, self.table)

    def test_count_is_rows_not_total(self):
        """``count`` 是**这一页**多少行；库存总数另走 ``total``。

        两个字段分开是因为页面上只画一条，说明文字却要写"共 N 条"。
        """
        service, _ = make_service(records=[_record()], total=42)
        table = service.table("SZ161125")
        self.assertEqual(table["count"], 1)
        self.assertEqual(table["total"], 42)

    def test_three_records_give_three_rows(self):
        records = [_record(date="2026-10-09"), _record(date="2026-10-08"),
                   _record(date="2026-10-07")]
        service, _ = make_service(records=records)
        table = service.table("^GSPC", 0, 3)

        self.assertEqual(table["count"], 3)
        self.assertEqual(table["rows"][0].cells[0], "2026-10-09")

    def test_title_is_present(self):
        self.assertEqual(self.table["title"], CALIBRATION_TITLE)


class LinkTest(unittest.TestCase):
    """完整记录的链接：URL 要 ``url_for``，所以由外层注入。"""

    def test_no_link_builder_means_no_link(self):
        service, _ = make_service(records=[_record()])
        self.assertIsNone(service.table("SZ161125")["link"])

    def test_link_is_built_from_the_symbol_and_page_name(self):
        seen = []

        def builder(symbol, page):
            seen.append((symbol, page))
            return "/history/%s/%s" % (page, symbol)

        service, _ = make_service(records=[_record()], link_builder=builder)
        link = service.table("SZ161125")["link"]

        self.assertEqual(seen, [("SZ161125", CALIBRATION_KIND)])
        self.assertEqual(link, {"text": "校准记录",
                                "url": "/history/calibrationhistory/SZ161125"})

    def test_builder_returning_nothing_means_no_link(self):
        service, _ = make_service(records=[_record()],
                                  link_builder=lambda symbol, page: None)
        self.assertIsNone(service.table("SZ161125")["link"])


class DefaultFactoryTest(unittest.TestCase):
    """默认工厂：不给链接构造器，也不在导入时就连库。"""

    def test_default_factory_has_no_link_builder(self):
        from web.services.calibration_service import (
            default_calibration_service_factory,
        )

        service = default_calibration_service_factory()
        self.assertIsNone(service.link_builder)

    def test_php_alias_exists(self):
        """按 AGENTS.md 的约定保留 PHP 原名。"""
        self.assertTrue(hasattr(CalibrationService, "EchoCalibrationHistoryParagraph"))


if __name__ == "__main__":
    unittest.main()
