"""基金详情页的「均线」接线测试（`web/routes/fund.py`）。

**单独成一个模块的原因**与 ``test_trading_route.py`` 一样：它需要 Flask，
而 ``test_sma.py`` 那 30 个用例是**不依赖 flask** 的、两套解释器都要能跑。

**不联网、不连库**：引用对象走 ``sma_refs_builder`` 注入，``StockHistory``
走 ``sma_history_factory`` —— 两个口子都是为此新增的。

钉的是三件事：

1. 均线表真的出现在页面上（列头「均线」、行标题「5日」）；
2. 期货那张表的**理论溢价**说明文字出现在页面上 —— 那是
   ``EchoFutureSmaParagraph`` 独有的，走 ``futurepremium`` 表；
3. 没有历史数据时整段跳过，页面上不出现空表。

``SmaConfigBackendTest`` 另钉一件事：**配置后端默认不落盘**
（``auto/smaconfig.py``，那三节设计取舍的开关就在这一处）。
"""

import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[3]
PYTHON_DIR = ROOT_DIR / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from web.bootstrap import install_paths  # noqa: E402

install_paths()

from web.tests.test_sma import FakeHistory, FakeRef, make_sma  # noqa: E402


def _squeezed(html: str) -> str:
    """压掉标签之间的空白 —— Jinja 会在标签之间塞缩进，
    直接 ``assertIn("<td>125.00</td>")`` 会因为缩进而失败。"""
    return re.sub(r"\s+", " ", html)


#: 对照列（``TableColumnStock``）的表头：``<span class="col-maroon">SH</span>``。
_STOCK_HEAD_RE = re.compile(r'<span class="col-maroon">([^<]+)</span>')


class FakeDailyRecord:
    """``stockdata.DailyRecord`` 的同形状替身（``.date`` / ``.close``）。"""

    def __init__(self, date, close):
        self.date = date
        self.close = close


class FakePremiumSource:
    """``futurepremium`` 的数据源替身：给 ``hf_NQ`` 一条固定利率。

    只实现 ``_get_daily_record`` 一个方法 —— ``futurepremium`` 模块就靠它读表，
    与 ``stockcalibration`` / ``netvalue`` 走的是同一个通用日记录口子。
    """

    def __init__(self, rate=5.00625, end_date="2026-12-18"):
        self.rate = rate
        self.end_date = end_date

    def _get_daily_record(self, table, stock_id, *args, **kwargs):
        if table != "futurepremium":
            return None
        return FakeDailyRecord(self.end_date, self.rate)


class FakeFutureRef(FakeRef):
    """期货引用对象替身：多一个 ``GetStockId()``。

    ``RefGetFuturePremium()`` 靠 ``$ref->GetStockId()`` 去 ``futurepremium``
    表里查利率 —— 光有符号查不到（表里存的是 ``stock_id``）。
    演示库里 ``hf_NQ`` 是 13 号。
    """

    def __init__(self, stock_id="13", **kwargs):
        super().__init__(**kwargs)
        self._stock_id = stock_id

    def get_stock_id(self):
        return self._stock_id


class FakeQdiiRef(FakeRef):
    """QDII 引用对象替身。

    ``EchoQdiiSmaParagraph`` / ``EchoFutureSmaParagraph`` 两个入口都从它身上
    取**两个**引用，顺序不能搞反：

    * ``GetEstRef()`` —— 中间层标的（``^NDX``），**被算均线**的那个；
    * ``GetRealtimeRef()`` —— 配对期货（``hf_NQ``），``futurepremium`` 按它查利率，
      同时当 ``cb_ref``（回调列用它换算）。

    均线数值本身由注入的 ``FakeHistory`` 给，所以这两个引用只要有符号和价格
    就够跑通纹理。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._est_ref = FakeRef(symbol="^NDX", prev_price="31000.00",
                                price="31137.575", precision=3)
        self._realtime_ref = FakeFutureRef(symbol="hf_NQ", prev_price="31100.00",
                                           price="31200.00", precision=2)

    def get_est_ref(self):
        return self._est_ref

    def get_realtime_ref(self):
        return self._realtime_ref


class FundDetailSmaRouteTest(unittest.TestCase):
    """均线表接线：注入假的 ``StockHistory`` 与假引用对象。"""

    def setUp(self):
        from web.app import create_app
        from web.services.fund_service import FundService
        from web.services.sma_service import SmaService
        from web.tests.test_web import make_source

        self.history = FakeHistory(sma=make_sma(), ref=FakeQdiiRef())
        self.refs = [FakeQdiiRef()]
        self.app = create_app(
            {"TESTING": True},
            fund_service_factory=lambda: FundService(make_source()),
            sma_service_factory=lambda: SmaService(
                history_factory=lambda ref, wide: self.history),
            sma_refs_builder=lambda symbols: self.refs,
            reference_service_factory=lambda: type(
                "FakeReferenceService", (), {"source": FakePremiumSource()})(),
        )
        self.client = self.app.test_client()

    def test_page_renders_the_sma_table(self):
        html = self.client.get("/fund/SZ162411").get_data(as_text=True)

        self.assertIn('class="data-table sma-table"', html)
        self.assertIn('id="smatable"', html)
        for text in ("均线", "5日", "10日", "布林上轨", "小牛熊分界"):
            self.assertIn(text, html)

    def test_memo_start_date_reaches_the_page(self):
        """``_getSmaParagraphMemo()`` 的起始日期要出现在表上方那句话里。"""
        html = self.client.get("/fund/SZ162411").get_data(as_text=True)

        self.assertIn("2026-10-09数据", html)
        self.assertIn("牛市", html)

    def test_future_table_shows_the_premium_note(self):
        """「理论溢价：…」是期货那张表独有的说明，走 ``futurepremium`` 表。"""
        html = self.client.get("/fund/SZ162411").get_data(as_text=True)

        self.assertIn('class="data-table sma-future-table"', html)
        self.assertIn("理论溢价：", html)

    def test_without_history_there_is_no_table(self):
        """没有历史数据就整段跳过 —— 不摆一张全 0 的表。"""
        self.history = FakeHistory(sma=make_sma(value="0"), ref=FakeQdiiRef())
        html = self.client.get("/fund/SZ162411").get_data(as_text=True)

        self.assertNotIn("sma-table", html)
        self.assertNotIn("sma-future-table", html)
        self.assertNotIn("理论溢价", html)

    def test_without_the_factory_there_is_no_table(self):
        """没注入 ``sma_service_factory`` 时也不能崩（默认工厂会去连库）。"""
        from web.app import create_app
        from web.services.fund_service import FundService
        from web.tests.test_web import make_source

        app = create_app(
            {"TESTING": True},
            fund_service_factory=lambda: FundService(make_source()),
            sma_service_factory=None,
            sma_refs_builder=lambda symbols: [],
        )
        html = app.test_client().get("/fund/SZ162411").get_data(as_text=True)

        self.assertNotIn("sma-table", html)


class _LevRef(FakeRef):
    """一个杠杆标的：自己带换算因子，也是那一列的显示口径。

    三个方法缺一不可 —— ``est_from_pair`` 要用 ``factor``（校准值）、
    ``get_position``（仓位）、``get_last_calibration_val``（上次校准值）。
    少任何一个都会走 PHP 的退化分支，算出一个**看着合理但没有来源**的数。
    """

    def __init__(self, symbol="SPY", factor=10.0, precision=2):
        FakeRef.__init__(self, symbol=symbol, precision=precision)
        self.factor = factor
        self.cny_ref = None
        self._position = 1.0

    def get_position(self):
        return self._position

    def get_last_calibration_val(self):
        return False


class _EstRef(FakeRef):
    def __init__(self):
        FakeRef.__init__(self, symbol="^GSPC", prev_price="1000.00",
                         price="1000.00", precision=3)

    def get_display(self):
        return "^GSPC"


class _FundRef(FakeRef):
    """被看的基金：``$ref->GetEstRef()`` 给出中间层标的。

    ``GetStockRef()`` 也不能少 —— ``_callbackQdiiSma`` 无参数时返回它，
    那一列的**表头**就是它的显示名。少了它，页面上会出现一个空的 maroon 列
    （不报错，但那一列整列空白，很难看出是缺方法还是缺数据）。
    """

    def __init__(self):
        FakeRef.__init__(self, symbol="SZ162411")
        self._est_ref = _EstRef()
        self._stock_ref = FakeRef(symbol="SZ162411", precision=3)

    def get_est_ref(self):
        return self._est_ref

    def get_stock_ref(self):
        return self._stock_ref


class _Service:
    def __init__(self, source=None):
        self.source = source


class _EmptyHistory:
    """历史服务替身：所有 kind 都返回空行，让历史类段落整段隐藏。

    CK1 之后基金详情页多了 stockhistory / netvalueclose 两段（走真实历史服务，
    依赖演示库数据），本测试只验 SMA 列顺序，不该被库里的历史数据干扰，
    所以把历史服务掐掉，保持页面只含本测试控制的表。
    """

    def get_table(self, kind, symbol, start=0, num=10):
        return {"title": kind, "columns": [], "rows": []}


class FundPairSmaRouteTest(unittest.TestCase):
    """``EchoFundPairSmaParagraphs()`` 的接线：每个杠杆标的各一张表。

    与 ``FundDetailSmaRouteTest`` 的区别是**那张表有一张还是好几张**：
    这里断言页面上有两张、顺序与 ``$arLev`` 一致、各自的第 6 列不同。
    """

    def setUp(self):
        from web.app import create_app
        from web.services.fund_service import FundService
        from web.services.sma_service import SmaService
        from web.tests.test_web import make_source

        self.history = FakeHistory(sma=make_sma(value="1000.00"),
                                   next_values=make_sma(value="1010.00"),
                                   ref=_EstRef())
        self.leverage_refs = [_LevRef(symbol="SH", factor=8.0),
                              _LevRef(symbol="SPY", factor=10.0)]
        self.app = create_app(
            {"TESTING": True},
            fund_service_factory=lambda: FundService(make_source()),
            sma_service_factory=lambda: SmaService(
                history_factory=lambda ref, wide: self.history),
            sma_refs_builder=lambda symbols: [_FundRef()],
            fundlist_refs_builder=lambda est_symbol: self.leverage_refs,
            reference_service_factory=lambda: _Service(None),
            history_service_factory=lambda: _EmptyHistory(),
        )
        self.client = self.app.test_client()

    def _html(self):
        return _squeezed(self.client.get("/fund/SZ162411").get_data(as_text=True))

    def test_there_is_one_table_per_leverage_ref(self):
        html = self._html()
        self.assertEqual(html.count('class="data-table sma-pair-table"'), 2)

    def test_the_stock_column_headers_reach_the_page_in_order(self):
        """页面上三张表的对照列依次是 ``SZ162411`` → ``SH`` → ``SPY``。

        与生产端 ``sz161125cn.php`` 的形状一致（那里是 ``SZ161125`` → ``SH``
        → ``SPY`` → ``ES``）：``EchoQdiiSmaParagraph`` 一张，
        ``EchoFundPairSmaParagraphs`` 按 ``$arLev`` 的顺序再来两张。
        """
        html = self._html()
        self.assertEqual([m.group(1) for m in _STOCK_HEAD_RE.finditer(html)],
                         ["SZ162411", "SH", "SPY"])

    def test_the_stock_column_is_rendered_maroon(self):
        """``TableColumnStock`` 的 maroon —— 宏把它渲染成 ``col-maroon``。

        这一条是**修 bug 时补的**：原先服务层漏了颜色，数值全对、
        页面上只有颜色不对 —— 最难发现的那一类偏差。
        """
        html = self._html()
        self.assertIn('<span class="col-maroon">SH</span>', html)
        self.assertIn('<span class="col-maroon">SPY</span>', html)

    def test_each_table_converts_with_its_own_factor(self):
        """同一个均线值 ``1000.00``：``/8 = 125.00``、``/10 = 100.00``。"""
        html = self._html()
        self.assertIn("<td>125.00</td>", html)
        self.assertIn("<td>100.00</td>", html)

    def test_the_tables_have_no_memo_line(self):
        """PHP 传 ``''`` 而不是 ``false`` —— 这些表**没有**"日期数据 牛市"。

        页面上那行说明只属于循环外自己那张（``EchoQdiiSmaParagraph``）。
        """
        html = self._html()
        self.assertLess(html.count("2026-10-09数据"), 2)

    def test_no_leverage_refs_means_no_pair_tables(self):
        """``if (count($arLev) > 0)``：没有杠杆标的，这两张表都不出现。"""
        from web.app import create_app
        from web.services.fund_service import FundService
        from web.services.sma_service import SmaService
        from web.tests.test_web import make_source

        app = create_app(
            {"TESTING": True},
            fund_service_factory=lambda: FundService(make_source()),
            sma_service_factory=lambda: SmaService(
                history_factory=lambda ref, wide: self.history),
            sma_refs_builder=lambda symbols: [_FundRef()],
            fundlist_refs_builder=lambda est_symbol: [],
            reference_service_factory=lambda: _Service(None),
        )
        html = _squeezed(app.test_client().get("/fund/SZ162411").get_data(as_text=True))

        # QDII 那张（sma-table）还在 —— 它不受 count($arLev) 约束
        self.assertIn("sma-table", html)
        self.assertNotIn("sma-pair-table", html)
        # 两张配对表的换算值一个都不该出现
        self.assertNotIn("125.00", html)
        self.assertNotIn("<td>100.00</td>", html)


class SmaConfigBackendTest(unittest.TestCase):
    """配置存放处的开关：**不开就不落盘**（``auto/smaconfig.py``）。

    PHP 的 ``_configSMA()`` 每次都 ``save_data()`` 回写工作区文件。直译过来
    就是"渲染一个页面写一次磁盘"，所以本版把它做成显式开关，默认关闭。
    这里钉的是**开关本身**，文件读写的细节在 ``auto/test_smaconfig.py``。
    """

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="sma-route-cfg-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self._saved = os.environ.get("PALMMICRO_SMA_CACHE_DIR")
        self.addCleanup(self._restore)
        os.environ.pop("PALMMICRO_SMA_CACHE_DIR", None)

    def _restore(self):
        if self._saved is None:
            os.environ.pop("PALMMICRO_SMA_CACHE_DIR", None)
        else:
            os.environ["PALMMICRO_SMA_CACHE_DIR"] = self._saved

    def test_default_factory_uses_memory(self):
        """没设目录 → 内存实现，一个字节都不写。"""
        from web.services.sma_service import default_sma_service_factory

        service = default_sma_service_factory()
        store = service._config_factory("^NDX")
        self.assertEqual(type(store).__name__, "ConfigSmaStore")
        self.assertFalse(hasattr(store, "path"), "内存实现不该有路径")

    def test_default_factory_uses_files_once_the_dir_is_set(self):
        """设了 ``PALMMICRO_SMA_CACHE_DIR`` → 走文件后端，路径按 PHP 的规则拼。"""
        from web.services.sma_service import default_sma_service_factory

        os.environ["PALMMICRO_SMA_CACHE_DIR"] = self.root
        service = default_sma_service_factory()
        store = service._config_factory("^NDX")

        self.assertEqual(type(store).__name__, "ConfigSmaFileStore")
        self.assertEqual(store.path,
                         os.path.join(self.root, "config", "_ndx.txt"))

    def test_the_switch_is_read_per_request_not_at_import_time(self):
        """环境变量每次构造才读 —— 改了不用重启进程，测试也能来回切。"""
        from web.services.sma_service import default_sma_service_factory

        service = default_sma_service_factory()
        self.assertEqual(type(service._config_factory("^NDX")).__name__,
                         "ConfigSmaStore")

        os.environ["PALMMICRO_SMA_CACHE_DIR"] = self.root
        self.assertEqual(type(service._config_factory("^NDX")).__name__,
                         "ConfigSmaFileStore")

    def test_config_reaches_stockhistory(self):
        """``SmaService`` 把存放处交给 ``StockHistory`` —— 接线别断在半路。"""
        from sql import smaconfig
        from web.services import sma_service as module
        from web.services.sma_service import SmaService

        factory = smaconfig.file_store_factory(self.root)
        service = SmaService(config_factory=factory)

        seen = {}
        original = module.StockHistory

        def recorder(ref, after_hour=False, config=None, **kwargs):
            seen["config"] = config
            return original(ref, after_hour=after_hour, config=config, **kwargs)

        module.StockHistory = recorder
        try:
            service._build_history(FakeQdiiRef())
        finally:
            module.StockHistory = original

        self.assertIsNotNone(seen.get("config"))
        self.assertEqual(seen["config"].path,
                         os.path.join(self.root, "config", "_ndx.txt"))

    def test_custom_history_factory_owns_the_config(self):
        """注入了 ``history_factory`` 时服务**不再插手** config —— 构造归调用方。"""
        from sql import smaconfig
        from web.services import sma_service as module
        from web.services.sma_service import SmaService

        service = SmaService(history_factory=lambda ref, wide: FakeHistory(
            sma=make_sma(), ref=FakeQdiiRef()),
            config_factory=smaconfig.file_store_factory(self.root))

        seen = {}
        original = module.StockHistory

        def recorder(*args, **kwargs):
            seen["called"] = True
            return original(*args, **kwargs)

        module.StockHistory = recorder
        try:
            service._build_history(FakeQdiiRef())
        finally:
            module.StockHistory = original

        self.assertNotIn("called", seen, "自定义工厂接管后不该再造 StockHistory")

    def test_broken_config_factory_falls_back_to_memory(self):
        """找存放处失败不该让整段 500 —— 退回内存即可。"""
        from web.services.sma_service import SmaService

        def boom(ref):
            raise RuntimeError("磁盘满了")

        service = SmaService(config_factory=boom)
        self.assertIsNone(service._config_of(FakeQdiiRef()))

    def test_rendering_the_page_writes_nothing(self):
        """端到端：默认配置下渲染一整页，**临时目录里一个文件都没有**。

        这条是给"将来有人顺手把 root 默认值改成 CWD"准备的警报。
        刻意把 CWD 也切到临时目录 —— 那样一来无论猜哪个目录都会留下痕迹。
        """
        from web.app import create_app
        from web.services.fund_service import FundService
        from web.services.sma_service import SmaService
        from web.tests.test_web import make_source

        app = create_app(
            {"TESTING": True},
            fund_service_factory=lambda: FundService(make_source()),
            sma_service_factory=lambda: SmaService(
                history_factory=lambda ref, wide: FakeHistory(
                    sma=make_sma(), ref=FakeQdiiRef())),
            sma_refs_builder=lambda symbols: [FakeQdiiRef()],
        )

        cwd = os.getcwd()
        os.chdir(self.root)
        try:
            html = app.test_client().get("/fund/SZ162411").get_data(as_text=True)
        finally:
            os.chdir(cwd)

        self.assertIn("sma-table", html, "先确认确实渲染了均线表")
        self.assertEqual(os.listdir(self.root), [])


if __name__ == "__main__":
    unittest.main()
