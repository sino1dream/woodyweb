"""参考数据表的测试（`php/ui/referenceparagraph.php` → `reference_service.py`）。

**全部离线**：引用对象与数据源都是内存假实现，不联网、不连 MySQL。
"""

import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[3]
PYTHON_DIR = ROOT_DIR / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from web.app import create_app
from web.bootstrap import install_paths
from web.services.fund_list_service import FundListService
from web.services.fund_service import FundService
from web.services.reference_service import (
    REFERENCE_NAME_WIDTH,
    REFERENCE_STATE_NO_QUOTE,
    REFERENCE_STATE_NO_SYMBOL,
    REFERENCE_STATE_OK,
    REFERENCE_STATE_UNKNOWN,
    ReferenceService,
    _leverage_symbols,
    _realtime_symbol,
    build_stock_references,
    percentage_cell,
    quote_state_of,
    reference_symbols_for_fund,
)
from web.tests.test_web import FakeStatusService  # noqa: E402

install_paths()

from web.tests.test_web import make_source  # noqa: E402


class StoredQuote:
    """``quotestore.RecentQuote`` 形状（``refquote`` 只读这几个属性）。"""

    def __init__(self, price, prev_price, date, time, name):
        self.price = price
        self.prev_price = prev_price
        self.date = date
        self.time = time
        self.name = name


class StoredQuoteSource:
    """带 ``realtimequote`` 快照的假数据源。

    只实现 ``refobj.MysqlReference`` 真正会问到的那几个方法。
    数值取生产端 ``qdiicn.php`` 的 2026-10-09 那一行，方便逐格对照。
    """

    #: 与生产端一致的快照
    QUOTES = {
        "SZ161130": StoredQuote("4.884", "4.814", "2026-10-09", "15:00:00", "纳指LOF"),
    }
    NAMES = {"SZ161130": "易方达纳斯达克100LOF"}
    IDS = {"SZ161130": "1", "USCNY": "8"}

    def __init__(self):
        self.connection = None

    def get_stock_id(self, symbol):
        return self.IDS.get(symbol)

    def get_stock_name(self, symbol):
        return self.NAMES.get(symbol)

    def get_stored_quote(self, symbol):
        return self.QUOTES.get(symbol)

    def get_calibration_now(self, stock_id):
        return None

    def get_fund_profile(self, symbol):
        """``FundService`` 会问它要估值配置；参考数据表这条路不需要，返回 ``None``。"""
        return None

    def get_position(self, stock_id):
        return None

    def count_net_value(self, stock_id):
        return 0

    def get_latest_net_value_close(self, stock_id):
        return None

    def get_latest_net_value_date(self, stock_id):
        return None

    def get_latest_history_close(self, stock_id):
        return None



class FakeRef:
    """引用对象替身，只实现参考数据表会问到的那几个动作。"""

    def __init__(
        self,
        symbol="SZ161130",
        has_data=True,
        price="4.796",
        change="-1.23%",
        date="2026-10-02",
        time="15:00:00",
        session=None,
        extended=None,
    ):
        self.symbol = symbol
        self.has_data = has_data
        self._price = price
        self._change = change
        self._date = date
        self._time = time
        self._session = session
        self.extended_ref = extended

    def get_symbol(self):
        return self.symbol

    def get_external_link(self):
        return '<a href="https://example.test/%s">新浪</a>' % self.symbol

    def get_price_display(self):
        return self._price

    def get_percentage_text(self):
        return self._change

    def get_date(self):
        return self._date

    def get_time_hm(self):
        # 15:00:00 -> 15:00，对应 PHP GetTimeHM()
        return self._time[:5]

    def get_market_session(self):
        return self._session or ""

    def debug_link(self):
        return "debug.link/%s" % self.symbol


class FakeNameSource:
    def __init__(self, names=None):
        self.names = names or {}

    def get_stock_name(self, symbol):
        return self.names.get(symbol)


class ColumnTest(unittest.TestCase):
    """列定义对照 PHP ``EchoReferenceParagraph()`` 那段"塞进去再 pop 掉"的组装。"""

    def test_six_columns_in_php_order(self):
        table = ReferenceService().get_table([FakeRef()])
        self.assertEqual(
            [column["text"] for column in table["columns"]],
            ["代码", "价格", "涨幅", "日期", "时间", "名称"],
        )

    def test_widths_match_php(self):
        """名称列被 PHP 换成 270 宽的那一个（默认 150 不够放全名）。"""
        table = ReferenceService().get_table([FakeRef()])
        widths = [column["width"] for column in table["columns"]]
        self.assertEqual(widths, [80, 70, 70, 100, 50, REFERENCE_NAME_WIDTH])
        self.assertEqual(REFERENCE_NAME_WIDTH, 270)

    def test_column_colors_come_from_uicolumn(self):
        """代码 maroon、价格 blue —— 这两条来自 stocktable.php 的预置。"""
        table = ReferenceService().get_table([FakeRef()])
        colors = [column["color"] for column in table["columns"]]
        self.assertEqual(colors, ["maroon", "blue", "", "", "", ""])

    def test_admin_appends_debug_column_with_remaining_width(self):
        """调试列吃剩余宽度（PHP ``TableColumnGetLastWidth``）。"""
        table = ReferenceService(layout_width=980).get_table([FakeRef()], admin=True)
        last = table["columns"][-1]
        self.assertEqual(last["text"], "调试数据")
        self.assertEqual(last["width"], 980 - (80 + 70 + 70 + 100 + 50 + 270))

    def test_admin_without_layout_width_is_zero(self):
        """没给布局宽度就算 0，不去猜屏幕宽度。"""
        table = ReferenceService().get_table([FakeRef()], admin=True)
        self.assertEqual(table["columns"][-1]["width"], 0)


class RowTest(unittest.TestCase):
    def test_row_order_and_cells(self):
        service = ReferenceService(FakeNameSource({"SZ161130": "易方达纳斯达克100LOF"}))
        row = service.get_table([FakeRef()])["rows"][0]

        # 首格外链 + 中间四格 + 名称
        self.assertEqual(row.cells[0], '<a href="https://example.test/SZ161130">新浪</a>')
        self.assertEqual(row.cells[1], "4.796")
        self.assertEqual(row.cells[2], ("-1.23%", "green"))
        self.assertEqual(row.cells[3], "2026-10-02")
        self.assertEqual(row.cells[4], "15:00")
        self.assertEqual(row.cells[5], "易方达纳斯达克100LOF")

    def test_without_quote_data_row_is_four_blanks(self):
        """**列数必须对齐**：没有行情时填四个空串而不是省略，
        否则整行会往左边串位。

        名称那一格则改成写"为什么没有" —— 见
        :class:`QuoteStateRowTest`，四格空格等于什么都不说。
        """
        row = ReferenceService().get_table([FakeRef(has_data=False)])["rows"][0]
        self.assertEqual(row.cells[1:5], ["", "", "", ""])
        self.assertEqual(row.cells[5], ("无行情", "gray"))
        self.assertEqual(len(row.cells), 6)

    def test_extended_row_moves_name_to_tooltip(self):
        """PHP 用 ``array_pop`` 把名称拎出来降级成首格 title，
        最后一格换成"盘前/盘后"说明。
        """
        extended = FakeRef(session="盘后交易")
        ref = FakeRef(extended=extended)
        service = ReferenceService(FakeNameSource({"SZ161130": "易方达LOF"}))

        rows = service.get_table([ref])["rows"]

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1].title, "易方达LOF")
        self.assertEqual(rows[1].cells[-1], ("盘后交易", "gray"))
        # 主行没有说明文字，名称照常显示在最后一格
        self.assertEqual(rows[0].title, "")
        self.assertEqual(rows[0].cells[-1], "易方达LOF")

    def test_none_refs_are_skipped(self):
        """PHP 的 ``_echoReferenceTableData`` 也是 ``if ($ref)`` 才渲染。"""
        self.assertEqual(ReferenceService().get_table([None, FakeRef(), None])["count"], 1)

    def test_missing_name_is_empty_string(self):
        row = ReferenceService(FakeNameSource()).get_table([FakeRef()])["rows"][0]
        self.assertEqual(row.cells[-1], "")

    def test_admin_row_has_extra_cell(self):
        row = ReferenceService(layout_width=980).get_table([FakeRef()], admin=True)["rows"][0]
        self.assertEqual(row.cells[-1], "debug.link/SZ161130")

    def test_name_lookup_failure_does_not_break_the_table(self):
        class Broken:
            def get_stock_name(self, symbol):
                raise RuntimeError("模拟数据库异常")

        row = ReferenceService(Broken()).get_table([FakeRef()])["rows"][0]
        self.assertEqual(row.cells[-1], "")
        self.assertEqual(len(row.cells), 6)


class PercentageColorTest(unittest.TestCase):
    """涨跌幅染色：形状对照 ``php/stock/stockref.php:147``，颜色用全站统一的涨红跌绿。"""

    def test_negative_is_green(self):
        """**与 PHP 原文不同**：PHP 在这里给 ``red``（跌红），本实现给 ``green``。

        裁决见 :mod:`web.services.reference_service` 的模块 docstring ——
        跌红涨黑只在 ``GetPercentageDisplay()`` 一处出现，涨红跌绿才是
        ``StockGetPriceDisplay()`` 的主流口径。
        """
        self.assertEqual(percentage_cell("-1.23%"), ("-1.23%", "green"))

    def test_positive_is_red(self):
        """**与 PHP 原文不同**：PHP 在这里给 ``black``（涨黑），本实现给 ``red``。"""
        self.assertEqual(percentage_cell("1.23%"), ("1.23%", "red"))

    def test_zero_percent_counts_as_up(self):
        """``0.00%`` 不带 ``-``，按 PHP 的分支走"非下跌"那一路。

        与分组列表页 ``"negative" if change < 0 else "positive"`` 的判定一致：
        只有严格小于 0 才算跌。两处对 0 的处理必须相同，否则同一只基金
        在两个页面上会一红一绿。
        """
        self.assertEqual(percentage_cell("0.00%"), ("0.00%", "red"))

    def test_without_percent_sign_is_gray(self):
        """`GetPercentageText()` 返回空串（没有昨收可比）时染灰，这一条与 PHP 一致。"""
        self.assertEqual(percentage_cell(""), ("", "gray"))
        self.assertEqual(percentage_cell(None), ("", "gray"))

    def test_matches_fund_list_convention(self):
        """与分组列表页口径一致：跌是绿、涨是红。

        分组列表页（``fund_list_service``）用的是
        ``"negative" if change < 0 else "positive"``，CSS 里
        ``.negative`` 是绿、``.positive`` 是红。这里把"同一个跌幅
        必须得到同一个方向"钉住，防止有人把某一处单独改回去。
        """
        self.assertEqual(percentage_cell("-1.23%")[1], "green")
        self.assertEqual(percentage_cell("1.23%")[1], "red")
        # 两处对"跌"的判断必须是同一个方向
        for text in ("-1.23%", "-0.01%"):
            self.assertEqual(percentage_cell(text)[1], "green")
        for text in ("1.23%", "0.01%", "0.00%"):
            self.assertEqual(percentage_cell(text)[1], "red")


class ReferenceRouteTest(unittest.TestCase):
    """基金详情页接线：注入假引用对象，不打网络。"""

    def setUp(self):
        source = make_source()
        self.app = create_app(
            {"TESTING": True},
            fund_service_factory=lambda: FundService(source),
            reference_service_factory=lambda: ReferenceService(
                FakeNameSource({"SZ162411": "华宝油气"})
            ),
            reference_refs_builder=lambda symbols: [FakeRef(symbol) for symbol in symbols],
        )
        self.client = self.app.test_client()

    def test_fund_page_renders_reference_table(self):
        html = self.client.get("/fund/SZ162411").get_data(as_text=True)

        self.assertIn('class="data-table reference-table"', html)
        for text in ("代码", "价格", "涨幅", "日期", "时间", "名称"):
            self.assertIn(text, html)
        self.assertIn("华宝油气", html)
        self.assertIn("参考数据来自外部行情源", html)

    def test_fund_page_hides_table_when_there_are_no_rows(self):
        """没有任何行时整段跳过 —— 宁可少一块，也不摆一张空表。"""
        self.app.extensions["reference_refs_builder"] = lambda symbols: []
        html = self.client.get("/fund/SZ162411").get_data(as_text=True)

        self.assertNotIn("reference-table", html)

    def test_reference_failure_does_not_break_the_page(self):
        """参考数据取不到时，基金详情页必须照常可用。"""

        def boom(symbols):
            raise RuntimeError("模拟行情抓取失败")

        self.app.extensions["reference_refs_builder"] = boom
        response = self.client.get("/fund/SZ162411")

        self.assertEqual(response.status_code, 200)
        self.assertIn("估值配置", response.get_data(as_text=True))


class RealReferenceBuildingTest(unittest.TestCase):
    """`build_stock_references()` 的**跨层集成**测试。

    这层以前缺测试，结果详情页整张「参考数据」表是空的（只有名称），
    而单测全绿 —— 因为单测在 service 边界打桩，没有"真数据源 → 引用对象 →
    表格"这条链。这组用例专门补上它。

    ``build_stock_references()` 用真实 ``refobj.MysqlReference``：
    **构造期只读库快照、不联网**（见 ``auto/refquote.py``）。
    """

    def test_load_failure_degrades_to_blank_row(self):
        """没有数据源 → 读不到快照 → 四个数值格留空，但**列数仍对齐**。"""
        refs = build_stock_references(["SZ161130"])
        self.assertEqual(len(refs), 1)

        row = ReferenceService().get_table(refs)["rows"][0]
        self.assertEqual(row.cells[1:5], ["", "", "", ""])

    def test_snapshot_fills_the_row(self):
        """**有快照时四格必须有值**（P0 的验收点）。

        假数据源给出与生产端一致的行情（2026-10-09 的 SZ161130）。
        """
        source = StoredQuoteSource()
        refs = build_stock_references(["SZ161130"], source=source)

        self.assertEqual(len(refs), 1)
        ref = refs[0]
        self.assertTrue(ref.has_data, "读了快照就该有行情")
        self.assertEqual(ref.get_price(), "4.884")
        self.assertEqual(ref.get_date(), "2026-10-09")

    def test_snapshot_row_matches_production_cells(self):
        """逐格核对：代码 / 价格 / 涨幅 / 日期 / 时间 / 名称。

        对照生产端 ``qdiicn.php`` 的 SZ161130 那一行。名称列取自
        ``SqlGetStockName()``（库名），所以用假数据源里给的名字。
        """
        source = StoredQuoteSource()
        refs = build_stock_references(["SZ161130"], source=source)

        row = ReferenceService(source).get_table(refs)["rows"][0]

        # 首格是外链 HTML（模板按 first_cell_is_html 原样渲染）
        self.assertIn("161130", row.cells[0])
        self.assertIn("/fund/quotes/161130/bc.shtml", row.cells[0])
        self.assertEqual(row.cells[1], "4.884")
        # 涨幅格带颜色，是 (文本, 颜色类) 二元组；颜色属于渲染层
        change_text = row.cells[2] if isinstance(row.cells[2], str) else row.cells[2][0]
        self.assertEqual(change_text, "1.45%")
        self.assertEqual(row.cells[3], "2026-10-09")
        self.assertEqual(row.cells[4], "15:00")
        self.assertEqual(row.cells[5], "易方达纳斯达克100LOF")

    def test_full_stack_builds_six_filled_cells(self):
        """**跨层集成**：假数据源 → ``build_stock_references`` → ``ReferenceService``
        → 六个格子全部有值。

        这一层以前缺测试，所以"详情页整张表是空的"能一路绿灯溜过去：
        单测都在 service 边界打桩，没有"真数据源 → 引用对象 → 表格"这条链。

        **不走 HTTP**：``/fund/<symbol>`` 那条路由依赖真实 ``get_symbol_group()``
        （查库），在离线测试里会 404。页面渲染另由真实验收脚本覆盖
        （``dbtools/verify_pages.py`` 与 P0 对照）。
        """
        source = StoredQuoteSource()
        refs = build_stock_references(["SZ161130"], source=source)
        table = ReferenceService(source).get_table(refs)

        self.assertEqual(table["count"], 1)
        row = table["rows"][0]
        self.assertEqual(len(row.cells), 6, "参考数据表必须六格")

        change_text = row.cells[2] if isinstance(row.cells[2], str) else row.cells[2][0]
        self.assertEqual(
            [row.cells[0], row.cells[1], change_text, row.cells[3], row.cells[4], row.cells[5]],
            [
                row.cells[0],                      # 首格是外链 HTML，单独断言见下
                "4.884",
                "1.45%",
                "2026-10-09",
                "15:00",
                "易方达纳斯达克100LOF",
            ],
        )
        self.assertIn("/fund/quotes/161130/bc.shtml", row.cells[0])

    def test_construction_stays_offline(self):
        """**构造期不联网**：数据源没有快照时也不该去抓新浪。

        理由：``QdiiReference`` 构造里还会算实时 EST，
        构造期联网会让每个页面请求都慢一拍。
        """
        class OfflineCheck(StoredQuoteSource):
            def get_stored_quote(self, symbol):
                return None            # 快照没有

        source = OfflineCheck()
        refs = build_stock_references(["SZ161130"], source=source)

        self.assertFalse(refs[0].has_data, "没有快照就该留空，而不是联网")
        self.assertEqual(refs[0].quote_source, "none")


class ReferenceSymbolsTest(unittest.TestCase):
    """详情页参考数据表的**标的清单**（对应 PHP ``$arRef`` + ``$arLev`` 装配）。"""

    def test_first_symbol_is_always_the_fund_itself(self):
        self.assertEqual(reference_symbols_for_fund("SZ161130", None), ["SZ161130"])

    def test_appends_estimate_forex_and_middle_rate(self):
        """基金 → 估值标的 → 杠杆标的 → 即期外汇 → 中间价（生产端顺序）。"""
        source = StoredQuoteSource()
        symbols = reference_symbols_for_fund("SZ161130", source)

        self.assertEqual(symbols[0], "SZ161130")
        self.assertIn("^NDX", symbols, "估值标的必须进来")

    def test_deduplicates(self):
        source = StoredQuoteSource()
        symbols = reference_symbols_for_fund("SZ161130", source)
        self.assertEqual(len(symbols), len(set(symbols)), "标的不该重复")

    def test_without_source_returns_only_the_fund(self):
        """没有数据源时退化成一个标的，不抛异常。"""
        self.assertEqual(reference_symbols_for_fund("SZ162411"), ["SZ162411"])

    def test_cnh_comes_after_leverage_before_forex(self):
        """``_qdii.php:13``：``fx_susdcnh`` 是页面级恒定标的，不跟地区表走。

        它在 ``EchoAll()`` 里的位置是
        ``array_merge($arStockRef, [$oil_ref, $cnh_ref], GetForexRefArray())``
        —— 也就是**杠杆之后、外汇之前**。改回"只有地区外汇"时本测试应失败。
        """
        source = StoredQuoteSource()
        symbols = reference_symbols_for_fund("SZ161130", source)
        self.assertIn("fx_susdcnh", symbols)
        self.assertNotIn("hf_CL", symbols, "非 XOP 组不带原油")

    def test_oil_only_for_xop_group(self):
        """``_qdii.php:17-26``：只有 XOP 组（SZ162411 华宝油气）多带 ``hf_CL``。"""
        source = StoredQuoteSource()
        self.assertIn("hf_CL", reference_symbols_for_fund("SZ162411", source))


class FakePairCursor:
    """内存版 ``fundpair`` 查询。

    只回答两条 SQL，用 join 的方向区分（顺便验证了**这两条 SQL 确实不一样** ——
    万一有人复制粘贴漏改一个 ``s.id`` / ``f.id``，识别不出来就会直接让测试失败，
    而不是静默返回错误的结果）。

    ``row_factory`` 用来模拟不同游标类型：pymysql 的 ``DictCursor`` 给 dict，
    普通 cursor 给 tuple，两种都得认。
    """

    def __init__(self, rows, stock_ids, row_factory=None):
        self.rows = rows                    # [(id, stock_id), …]
        self.stock_ids = stock_ids
        self.row_factory = row_factory or (lambda name: {"symbol": name})
        self.executed = []
        self._result = []
        self.closed = False

    def execute(self, sql, params=()):
        self.executed.append((sql, params))
        (target_symbol,) = params
        target_id = self.stock_ids.get(target_symbol)
        if "s.id = f.id" in sql and "f.stock_id =" in sql:
            ids = [row[0] for row in self.rows if row[1] == target_id]   # 谁跟踪我
        elif "s.id = f.stock_id" in sql and "f.id =" in sql:
            ids = [row[1] for row in self.rows if row[0] == target_id]   # 我跟踪谁
        else:
            raise AssertionError("无法识别的 fundpair 查询：%s" % sql)
        names = {value: key for key, value in self.stock_ids.items()}
        self._result = [self.row_factory(names[i]) for i in ids if i in names]

    def fetchall(self):
        return list(self._result)

    def fetchone(self):
        return self._result[0] if self._result else None

    def close(self):
        self.closed = True


class FakePairConnection:
    def __init__(self, cursor):
        self.cursor_obj = cursor

    def cursor(self):
        return self.cursor_obj


class PairSource:
    """只提供 ``fundpair`` 查询的数据源替身。"""

    def __init__(self, rows, stock_ids, row_factory=None):
        self.cursor_obj = FakePairCursor(rows, stock_ids, row_factory)
        self.connection = FakePairConnection(self.cursor_obj)


class FundPairQueryTest(unittest.TestCase):
    """``fundpair`` 的两个相反读法：实时标的（我跟踪谁）与杠杆标的（谁跟踪我）。

    对应 PHP ``GetPairSymbol()`` 与 ``GetSymbolArray()``，
    详见 :func:`web.services.reference_service._realtime_symbol` 的对照说明。
    """

    #: id → 符号。数字取生产库，方便和生产端那张 8 行表对照
    STOCK_IDS = {"SZ161130": 1, "QQQ": 4, "^NDX": 6, "hf_NQ": 13, "TQQQ": 99}
    #: (id, stock_id)。注意 ``^NDX → hf_NQ`` 与 ``QQQ/TQQQ → ^NDX`` **同时存在**，
    #: 正好能验证两个方向不会串
    ROWS = [(1, 4), (6, 13), (4, 6), (99, 6)]

    def test_realtime_symbol_is_what_the_est_tracks(self):
        """实时标的 = "我跟踪谁"：``^NDX`` 配到纳指期货 ``hf_NQ``。"""
        source = PairSource(self.ROWS, self.STOCK_IDS)
        self.assertEqual(_realtime_symbol("^NDX", source), "hf_NQ")

    def test_leverage_symbols_are_those_tracking_the_est(self):
        """杠杆标的 = "谁跟踪我"：跟踪 ``^NDX`` 的是 ``QQQ`` 与 ``TQQQ``。"""
        source = PairSource(self.ROWS, self.STOCK_IDS)
        self.assertEqual(_leverage_symbols("^NDX", source), ["QQQ", "TQQQ"])

    def test_the_two_directions_do_not_bleed_into_each_other(self):
        """同一份数据上两个方向必须给出**不同**答案。

        这条是专门防"复制粘贴改漏一个别名"的：把 ``_LEVERAGE_SQL`` 抄成
        ``_PAIR_SYMBOL_SQL`` 之后页面照样出得来、行数也一样，只是参考数据
        从第 3 行开始全错位。
        """
        source = PairSource(self.ROWS, self.STOCK_IDS)

        self.assertEqual(_realtime_symbol("^NDX", source), "hf_NQ")
        self.assertEqual(_leverage_symbols("^NDX", source), ["QQQ", "TQQQ"])
        self.assertNotIn("hf_NQ", _leverage_symbols("^NDX", source))
        # 反向看同一个符号：QQQ 跟踪的是 ^NDX，而不是被 hf_NQ 跟踪
        self.assertEqual(_realtime_symbol("QQQ", source), "^NDX")
        self.assertEqual(_leverage_symbols("QQQ", source), ["SZ161130"])

    def test_unknown_symbol_is_empty(self):
        """连 ``stock`` 表里都没有的符号，两个方向都查不出东西。"""
        source = PairSource(self.ROWS, self.STOCK_IDS)
        self.assertIsNone(_realtime_symbol("NOPE", source))
        self.assertEqual(_leverage_symbols("NOPE", source), [])

    def test_without_est_symbol_does_not_query(self):
        source = PairSource(self.ROWS, self.STOCK_IDS)
        self.assertEqual(_leverage_symbols(None, source), [])
        self.assertIsNone(_realtime_symbol("", source))
        self.assertEqual(source.cursor_obj.executed, [], "没有估值标的不该查库")

    def test_without_connection_is_empty(self):
        class NoConnection:
            connection = None

        self.assertEqual(_leverage_symbols("^NDX", NoConnection()), [])
        self.assertIsNone(_realtime_symbol("^NDX", NoConnection()))

    def test_database_error_degrades_to_empty(self):
        """**查不出配对数据不该让详情页挂掉** —— 参考表只是多几行的事。"""

        class BoomCursor:
            closed = False

            def execute(self, sql, params=()):
                raise RuntimeError("模拟数据库异常")

            def fetchall(self):
                raise AssertionError("execute 已经抛了，不该再取数")

            def close(self):
                self.closed = True

        cursor = BoomCursor()
        source = PairSource.__new__(PairSource)
        source.cursor_obj = cursor
        source.connection = FakePairConnection(cursor)

        self.assertEqual(_leverage_symbols("^NDX", source), [])
        self.assertIsNone(_realtime_symbol("^NDX", source))
        self.assertTrue(cursor.closed, "就算抛异常，游标也必须关掉")

    def test_cursor_is_closed_on_success(self):
        source = PairSource(self.ROWS, self.STOCK_IDS)
        _leverage_symbols("^NDX", source)
        self.assertTrue(source.cursor_obj.closed)

    def test_tuple_rows_are_accepted(self):
        """pymysql 的**普通** cursor 返回 tuple 而不是 dict。

        只认 dict 的话换游标类型会让参考表静默少几行，比报错还难查。
        """
        source = PairSource(self.ROWS, self.STOCK_IDS, row_factory=lambda name: (name,))
        self.assertEqual(_leverage_symbols("^NDX", source), ["QQQ", "TQQQ"])

    def test_rows_without_symbol_are_skipped(self):
        source = PairSource(self.ROWS, self.STOCK_IDS, row_factory=lambda name: {"symbol": None})
        self.assertEqual(_leverage_symbols("^NDX", source), [])

    def test_realtime_symbol_sits_between_est_and_leverage(self):
        """生产端顺序：基金 → 估值标的 → **实时标的** → 杠杆标的 → …"""
        import web.services.reference_service as rs

        source = PairSource(self.ROWS, self.STOCK_IDS)
        original = rs._fund_reference_symbols
        rs._fund_reference_symbols = lambda symbol, src: ("^NDX", None, None)
        try:
            symbols = rs.reference_symbols_for_fund("SZ161130", source)
        finally:
            rs._fund_reference_symbols = original

        # 末尾那个 ``fx_susdcnh`` 是 ``_qdii.php:13`` 写死的页面级标的
        # （杠杆之后、外汇之前），跟这里桩掉的 ``_fund_reference_symbols`` 无关
        self.assertEqual(symbols, ["SZ161130", "^NDX", "hf_NQ", "QQQ", "TQQQ",
                                   "fx_susdcnh"])


class QuoteStateTest(unittest.TestCase):
    """「库里没有」与「没抓到行情」必须在页面上看得出区别。

    PHP 那边两者长得一模一样（中间四格全空），实测演示库的 ``^NDX`` 与
    ``USDCNY`` 正好各占一种：前者 stock 表有、快照没抓到；后者 stock 表里没有。
    """


class QuoteStateSource:
    """能回答"这个符号在库里有没有"的数据源替身。"""

    def __init__(self, ids, names=None):
        self.ids = ids
        self.names = names or {}

    def get_stock_id(self, symbol):
        return self.ids.get(symbol)

    def get_stock_name(self, symbol):
        return self.names.get(symbol)


class QuoteStateClassificationTest(QuoteStateTest):
    def test_has_data_is_ok(self):
        self.assertEqual(quote_state_of(None, FakeRef()), REFERENCE_STATE_OK)

    def test_symbol_in_stock_but_no_snapshot_is_no_quote(self):
        """引用对象构造时已经拿到 ``stock_id`` —— 说明库里有这一行。"""
        ref = FakeRef(has_data=False)
        ref.stock_id = "6"
        self.assertEqual(quote_state_of(None, ref), REFERENCE_STATE_NO_QUOTE)

    def test_symbol_missing_from_stock_is_no_symbol(self):
        ref = FakeRef(symbol="USDCNY", has_data=False)
        source = QuoteStateSource({"SZ161130": "1", "USCNY": "8"})
        self.assertEqual(quote_state_of(source, ref), REFERENCE_STATE_NO_SYMBOL)

    def test_without_source_cannot_tell(self):
        """没有数据源可问时不下结论 —— 说"库里没有"是撒谎。"""
        self.assertEqual(quote_state_of(None, FakeRef(has_data=False)), REFERENCE_STATE_UNKNOWN)

    def test_lookup_failure_cannot_tell(self):
        class Broken:
            def get_stock_id(self, symbol):
                raise RuntimeError("模拟数据库异常")

        ref = FakeRef(has_data=False)
        self.assertEqual(quote_state_of(Broken(), ref), REFERENCE_STATE_UNKNOWN)


class QuoteStateRowTest(QuoteStateTest):
    """名字那一格是唯一的解释机会 —— 中间四格必须留空以防列错位。"""

    def test_note_is_appended_after_the_name(self):
        source = QuoteStateSource({"^NDX": "6"}, {"^NDX": "纳斯达克100指数"})
        row = ReferenceService(source).get_table([FakeRef(symbol="^NDX", has_data=False)])["rows"][0]

        self.assertEqual(row.cells[1:5], ["", "", "", ""], "中间四格仍然是空串")
        self.assertEqual(row.cells[5], ("纳斯达克100指数（无行情快照）", "gray"))

    def test_note_alone_when_there_is_no_name(self):
        ref = FakeRef(symbol="USDCNY", has_data=False)
        row = ReferenceService(QuoteStateSource({})).get_table([ref])["rows"][0]

        self.assertEqual(row.cells[5], ("库里无此符号", "gray"))

    def test_no_note_when_there_is_data(self):
        source = QuoteStateSource({"SZ161130": "1"}, {"SZ161130": "易方达"})
        row = ReferenceService(source).get_table([FakeRef()])["rows"][0]

        self.assertEqual(row.cells[5], "易方达")

    def test_the_two_states_look_different(self):
        """这一条就是整个改动的验收点：两种成因不能显示成同一个样子。"""
        source = QuoteStateSource({"^NDX": "6"}, {"^NDX": "纳斯达克100指数"})

        no_quote = ReferenceService(source).get_table([FakeRef(symbol="^NDX", has_data=False)])["rows"][0]
        no_symbol = ReferenceService(source).get_table([FakeRef(symbol="USDCNY", has_data=False)])["rows"][0]

        self.assertNotEqual(no_quote.cells[5], no_symbol.cells[5])
        self.assertIn("无行情快照", no_quote.cells[5][0])
        self.assertIn("库里无此符号", no_symbol.cells[5][0])


if __name__ == "__main__":
    unittest.main()
