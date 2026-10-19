"""基金详情页「校准记录」的接线测试（`web/routes/fund.py`）。

**单独成模块**的原因与 `test_sma_route.py` 一样：它需要 Flask，
而 `test_calibration.py` 那 19 个用例是不依赖 flask 的。

**不联网、不连库**：取数走注入的假 `HistoryService`。

钉的是三件事：

1. 校准表出现在页面上，DOM id 是 PHP 那套 `<符号>calibrationhistory`；
2. 没有记录时整段不出现（宁可不画，也不留一张只有表头的空表）；
3. 期货均线表之前那张校准表，对象是**中间层标的**而不是基金 ——
   两个调用点传入的 ref 不一样，搞混了表和列都不对。
"""

import re
import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[3]
PYTHON_DIR = ROOT_DIR / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from web.bootstrap import install_paths  # noqa: E402

install_paths()

from web.tests.test_calibration import (  # noqa: E402
    CALIBRATION_COLUMNS,
    FUND_EXTRA_COLUMNS,
    FakeHistoryService,
    _record,
    make_service,
)
from web.tests.test_sma import FakeHistory, make_sma  # noqa: E402
from web.tests.test_sma_route import (  # noqa: E402
    FakePremiumSource,
    FakeQdiiRef,
    _force_sma_visible,
)


def _squeezed(html):
    """压掉标签之间的换行缩进 —— Jinja 自带缩进，挨个匹配缩进会写得很难看。"""
    return re.sub(r"\s+", " ", html)


class FundDetailCalibrationRouteTest(unittest.TestCase):
    """校准记录接线：注入假的取数服务。"""

    def setUp(self):
        from web.app import create_app
        from web.services.fund_service import FundService
        from web.tests.test_web import make_source

        # SZ162411 是场内基金 → IsFundA() 为真，多「净值 / 对冲值」两列
        self.history = FakeHistoryService(
            records=[_record()],
            columns=CALIBRATION_COLUMNS + FUND_EXTRA_COLUMNS)
        self.app = create_app(
            {"TESTING": True},
            fund_service_factory=lambda: FundService(make_source()),
            calibration_service_factory=lambda: self._calibration_service(),
        )
        self.client = self.app.test_client()

    def _calibration_service(self):
        from web.services.calibration_service import CalibrationService

        return CalibrationService(history_factory=lambda: self.history,
                                 link_builder=self._link)

    @staticmethod
    def _link(symbol, page):
        return "/history/%s/%s" % (page, symbol)

    def test_page_renders_the_calibration_table(self):
        html = _squeezed(self.client.get("/fund/SZ162411").get_data(as_text=True))

        self.assertIn('class="data-table calibration-table"', html)
        self.assertIn('id="SZ162411calibrationhistory"', html)
        # 表头必须真的把文字画出来：早先替身的 columns 写成了字符串，
        # 模板读 column.text 拿到空 —— 这条断言专治那种"测试过、页面空"
        for text in ("日期", "校准值", "时间", "次数"):
            self.assertIn("> %s <" % text, html)
        for spans in ('<span class="col-olive">净值</span>',
                      '<span class="col-navy">对冲值</span>'):
            self.assertIn(spans, html)

    def test_row_values_reach_the_page(self):
        html = self.client.get("/fund/SZ162411").get_data(as_text=True)

        for text in ("2026-10-08", "16513.43", "21:03"):
            self.assertIn(text, html)

    def test_link_to_the_full_history_page(self):
        """PHP 的 ``GetCalibrationHistoryLink()`` —— 只显示一条，其余另有页面。"""
        html = self.client.get("/fund/SZ162411").get_data(as_text=True)

        self.assertIn('href="/history/calibrationhistory/SZ162411"', html)

    def test_without_records_the_block_is_absent(self):
        """有意偏离 PHP：没有记录就整段跳过，不留空表头。"""
        self.history = FakeHistoryService(records=[])
        html = self.client.get("/fund/SZ162411").get_data(as_text=True)

        self.assertNotIn("calibration-table", html)
        self.assertNotIn("校准记录", html)

    def test_without_the_factory_there_is_no_table(self):
        """没注入工厂也不能崩。"""
        from web.app import create_app
        from web.services.fund_service import FundService
        from web.tests.test_web import make_source

        app = create_app(
            {"TESTING": True},
            fund_service_factory=lambda: FundService(make_source()),
            calibration_service_factory=None,
        )
        app.extensions["calibration_service_factory"] = lambda: None
        html = app.test_client().get("/fund/SZ162411").get_data(as_text=True)

        self.assertNotIn("calibration-table", html)

    def test_a_broken_history_service_does_not_break_the_page(self):
        self.history = FakeHistoryService(fail=True)
        response = self.client.get("/fund/SZ162411")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("calibration-table", response.get_data(as_text=True))


class EstCalibrationRouteTest(unittest.TestCase):
    """第二个调用点：``smaparagraph.php:218`` —— 对象是**中间层标的**。

    这张表只在期货均线表**之前**、且有期货溢价时才出现，所以要先把
    ``future_table`` 跑出来（注入假的期权利率源）。
    """

    def setUp(self):
        from web.app import create_app
        from web.services.fund_service import FundService
        from web.services.sma_service import SmaService
        from web.tests.test_web import make_source

        # 这张表在 PHP 里嵌套于 EchoFutureSmaParagraph（smaparagraph.php:218），
        # 2026-10-19 起随均线段一起默认隐藏；置 1 后接线不变，钉子继续有效。
        _force_sma_visible(self)
        self.history = FakeHistoryService(
            records=[_record(date="2026-10-09", close="1.006", time="04:02:00",
                             num="2083")],
            columns=CALIBRATION_COLUMNS,
        )
        self.records = list(self.history.records)
        self.app = create_app(
            {"TESTING": True},
            fund_service_factory=lambda: FundService(make_source()),
            sma_service_factory=lambda: SmaService(
                history_factory=lambda ref, wide: FakeHistory(
                    sma=make_sma(), ref=FakeQdiiRef())),
            sma_refs_builder=lambda symbols: [FakeQdiiRef()],
            reference_service_factory=lambda: type(
                "FakeReferenceService", (), {"source": FakePremiumSource()})(),
            calibration_service_factory=lambda: self._calibration_service(),
        )
        self.client = self.app.test_client()

    def _calibration_service(self):
        from web.services.calibration_service import CalibrationService

        return CalibrationService(history_factory=lambda: self.history)

    def test_the_queried_symbol_is_the_est_not_the_fund(self):
        """``^GSPC`` 而不是 ``SZ162411`` —— 传错对象，取到的是另一张表。"""
        html = self.client.get("/fund/SZ162411").get_data(as_text=True)

        # 页面上有两张校准表（基金自身 + 中间层标的），至少得有一张
        self.assertTrue(html.count("calibration-table") >= 1)

        # 走了 SmaService 那条路径之后，查询的符号应当是 ^NDX（FakeQdiiRef 的 EST）
        symbols = [call[1] for call in self.history.calls]
        self.assertIn("^NDX", symbols)

    def test_est_table_has_four_columns_only(self):
        """中间层标的不是 A 股基金 → 没有「净值 / 对冲值」两列。

        与生产端 ``^GSPC`` 那张校准记录一致（``日期 校准值 时间 次数``）。
        """
        html = _squeezed(self.client.get("/fund/SZ162411").get_data(as_text=True))

        self.assertIn('id="^NDXcalibrationhistory"', html)
        self.assertIn("calibration-est-table", html)
        # 四列表头都在，且不掺「净值 / 对冲值」——那两列是 IsFundA() 才有的
        for text in ("日期", "校准值", "时间", "次数"):
            self.assertIn("> %s <" % text, html)
        self.assertNotIn("col-navy", html)


if __name__ == "__main__":
    unittest.main()
