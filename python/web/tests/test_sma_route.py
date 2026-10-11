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
        import smaconfig
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
        import smaconfig
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
