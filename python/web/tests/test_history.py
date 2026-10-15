"""历史数据页面的路由测试。

对应 PHP 的 6 个历史数据页面（``php/ui/*paragraph.php``）。
用假连接喂数据，不依赖 MySQL，验证：

* 6 个 kind 都能渲染，表头与列数正确；
* 数据行按日期倒序；
* QDII 用**前一交易日**净值（基金溢价记录 / 净值价格比较的关键口径）；
* 分页参数 ``start`` / ``num`` 的语义（``num=0`` 是全部记录）；
* 未知 kind / 未知符号返回 404；
* JSON 接口与页面返回同一份数据。
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
from web.services.history_service import HistoryService, parse_paging  # noqa: E402


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []
        self.sql = ""
        self.params = ()

    def execute(self, sql, params=()):
        self.sql = sql
        self.params = params
        self.connection.queries.append((sql, params))
        self.rows = self.connection.dispatch(sql, params)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class FakeConnection:
    """只实现历史页面真正会发的那几种查询。"""

    def __init__(self, tables):
        self.queries = []
        self.tables = tables

    def cursor(self):
        return FakeCursor(self)

    def dispatch(self, sql, params):
        import re

        match = re.search(r"FROM\s+`([^`]+)`", sql)
        table = match.group(1) if match else ""
        rows = [dict(r) for r in self.tables.get(table, [])]

        if "COUNT(*)" in sql:
            stock_id = params[0] if params else None
            return [{"n": len([r for r in rows if str(r["stock_id"]) == str(stock_id)])}]

        if "WHERE" not in sql:
            return rows

        stock_id = params[0]
        rows = [r for r in rows if str(r["stock_id"]) == str(stock_id)]
        if "`date` = %s" in sql:
            rows = [r for r in rows if str(r["date"]) == str(params[1])][:1]
        elif "`date` < %s" in sql:
            rows = [r for r in rows if str(r["date"]) < str(params[1])]
            rows.sort(key=lambda r: str(r["date"]), reverse=True)
            rows = rows[:1]
        else:
            rows.sort(key=lambda r: str(r["date"]), reverse=True)
            limit = re.search(r"LIMIT (\d+)(?:, (\d+))?", sql)
            if limit:
                if limit.group(2) is not None:
                    start, count = int(limit.group(1)), int(limit.group(2))
                    rows = rows[start:start + count]
                else:
                    rows = rows[:int(limit.group(1))]
        return rows


# 假数据：日期刻意选 09-29 / 09-30 / 10-01 三天，方便验证"前一交易日"。
TABLES = {
    "stockhistory": [
        {"id": 1, "stock_id": "1", "date": "2026-09-30", "close": 4.796,
         "volume": 6369249, "adjclose": 4.796},
        {"id": 2, "stock_id": "1", "date": "2026-09-29", "close": 4.727,
         "volume": 5800000, "adjclose": 4.727},
    ],
    "netvaluehistory": [
        {"id": 1, "stock_id": "1", "date": "2026-10-01", "close": 4.5452},
        {"id": 2, "stock_id": "1", "date": "2026-09-29", "close": 4.5452},
        {"id": 10, "stock_id": "8", "date": "2026-10-01", "close": 6.705},
        {"id": 11, "stock_id": "8", "date": "2026-09-29", "close": 6.7411},
    ],
    "calibrationhistory": [
        {"id": 1, "stock_id": "1", "date": "2026-10-01", "close": 32.079543,
         "time": "00:12:34", "num": 1},
        {"id": 2, "stock_id": "1", "date": "2026-09-29", "close": 30.0,
         "time": "17:30:00", "num": 2},
        # 中间层标的（^GSPC）也有校准记录 —— 期货均线表前那张表的对象。
        # 演示库里它**同样有 profile 行**（position 默认 1.0），这正是当年
        # 谓词写错时多出「净值 / 对冲值」两列的原因。
        {"id": 3, "stock_id": "7", "date": "2026-10-01", "close": 1.006,
         "time": "04:03:00", "num": 1749},
    ],
    "shareshistory": [
        # 份额取 8050.00 万份：配合 stockhistory 里 2026-09-30 的成交量 6369249，
        # 换手 = 100 * 6369249 / (8050 * 10000) = 7.91%，与生产端一致。
        # （份额单位是万份，忘了乘 10000 会算出大 10000 倍的换手率。）
        {"id": 1, "stock_id": "1", "date": "2026-09-30", "close": 8050.00},
        {"id": 2, "stock_id": "1", "date": "2026-09-29", "close": 12240.48},
    ],
    "fundest": [],
}


def make_source():
    """与假连接配套的数据源：SZ161130 是 QDII（要回落到前一交易日净值）。"""
    return MemoryFundDataSource(
        stocks={"SZ161130": "1", "USCNY": "8", "^GSPC": "7"},
        names={"SZ161130": "易方达纳斯达克100LOF", "USCNY": "美元人民币",
               "^GSPC": "标普500指数"},
        profiles={
            "SZ161130": FundProfile(
                symbol="SZ161130",
                stock_id="1",
                position=0.95,
                official_date="2026-09-29",
                cny_est=6.705,
                is_lof=True,
                est_symbol="QQQ",
            ),
            #: 中间层标的在演示库里**也有** profile（position 默认 1.0）——
            #: 照实复现，否则测不出当年那个谓词 bug。
            "^GSPC": FundProfile(
                symbol="^GSPC",
                stock_id="7",
                position=1.0,
                official_date="2026-10-01",
                is_lof=True,
            ),
        },
        history_close={("1", "2026-09-30"): 4.796, ("1", "2026-09-29"): 4.727},
    )


class HistoryRouteTest(unittest.TestCase):
    def setUp(self):
        source = make_source()
        connection = FakeConnection(TABLES)
        self.app = create_app(
            {"TESTING": True},
            history_service_factory=lambda: HistoryService(source, connection),
        )
        self.client = self.app.test_client()

    # -- 基本可用性 --------------------------------------------------------
    def test_all_six_kinds_render(self):
        for kind in ("stockhistory", "fundhistory", "netvaluehistory",
                     "netvalueclose", "calibrationhistory", "shareshistory"):
            response = self.client.get("/history/%s/SZ161130" % kind)
            self.assertEqual(response.status_code, 200, "kind=%s" % kind)
            html = response.get_data(as_text=True)
            self.assertIn("易方达纳斯达克100LOF", html)

    def test_unknown_kind_is_404(self):
        response = self.client.get("/history/notakind/SZ161130")
        self.assertEqual(response.status_code, 404)

    def test_unknown_symbol_is_404(self):
        response = self.client.get("/history/stockhistory/NOPE")
        self.assertEqual(response.status_code, 404)

    def test_api_returns_same_rows_as_page(self):
        page = self.client.get("/history/calibrationhistory/SZ161130").get_data(as_text=True)
        data = self.client.get("/api/history/calibrationhistory/SZ161130").get_json()

        self.assertEqual(data["kind"], "calibrationhistory")
        self.assertEqual(len(data["rows"]), 2)
        # 日期倒序
        self.assertEqual(data["rows"][0]["cells"][0], "2026-10-01")
        self.assertIn("32.08", page)          # 校准值 3 位精度去尾零
        self.assertIn("17:30", page)          # GetHM 截到分

    # -- 列组成 -----------------------------------------------------------
    def test_stockhistory_columns(self):
        data = self.client.get("/api/history/stockhistory/SZ161130").get_json()
        labels = [c["text"] for c in data["columns"]]
        # 非期货 -> 复权价格而不是结算价
        self.assertEqual(labels, ["日期", "价格", "数量", "复权价格", "QQQ", "USCNY", "比价"])

    def test_calibrationhistory_columns_include_netvalue_and_hedge(self):
        data = self.client.get("/api/history/calibrationhistory/SZ161130").get_json()
        labels = [c["text"] for c in data["columns"]]
        self.assertEqual(labels, ["日期", "校准值", "时间", "次数", "净值", "对冲值"])

    def test_calibrationhistory_columns_for_middle_layer_symbol(self):
        """中间层标的（^GSPC）不是 A 股基金 → 只有 4 列。

        PHP 的谓词是 ``$ref->IsFundA()``（A 股 LOF/ETF），不是"有没有 profile"：
        演示库里 ^GSPC / ^NDX / QQQ / SPY 都有 profile 行，用 profile 当谓词会让
        期货均线表前那张校准表凭空多出「净值 / 对冲值」，而生产端
        ``sz161125cn.php`` 里 ^GSPC 那张只有「日期 校准值 时间 次数」。
        """
        data = self.client.get("/api/history/calibrationhistory/%5EGSPC").get_json()
        labels = [c["text"] for c in data["columns"]]
        self.assertEqual(labels, ["日期", "校准值", "时间", "次数"])
        # 记录本身要能取到（否则"4 列"是因为整段没数据）
        self.assertEqual(len(data["rows"]), 1)
        self.assertEqual(data["rows"][0]["cells"][0], "2026-10-01")

    def test_netvaluehistory_columns_avoid_duplicate_netvalue_label(self):
        """PHP 会给基金净值和中间层净值用同一个「净值」列名，这里必须区分。"""
        data = self.client.get("/api/history/netvaluehistory/SZ161130").get_json()
        labels = [c["text"] for c in data["columns"]]
        self.assertEqual(labels, ["日期", "净值", "涨幅", "USCNY", "涨幅", "标的净值", "涨幅", "仓位"])
        # 「净值」只应出现一次，避免读者分不清是基金还是标的的
        self.assertEqual(labels.count("净值"), 1)

    def test_shareshistory_columns(self):
        data = self.client.get("/api/history/shareshistory/SZ161130").get_json()
        labels = [c["text"] for c in data["columns"]]
        self.assertEqual(labels, ["日期", "份额(万)", "场内新增(万)", "数量", "换手", "新增换手"])

    # -- 业务口径 ---------------------------------------------------------
    def test_fundhistory_uses_previous_trading_day_netvalue(self):
        """QDII 的当天净值要等美股收盘后才出，所以用前一交易日净值。

        假数据：行情 2026-09-30、净值只有 09-29 与 10-01。
        若错误地用当天（09-30）净值，会取不到，页面就没有这一行。
        """
        data = self.client.get("/api/history/fundhistory/SZ161130").get_json()
        self.assertEqual(len(data["rows"]), 1)
        cells = data["rows"][0]["cells"]
        self.assertEqual(cells[0], "2026-09-30")          # 日期=行情日期
        self.assertEqual(cells[1][0], "4.796")            # 价格
        self.assertEqual(cells[2][0], "4.545")            # 净值（09-29 的）
        self.assertEqual(cells[3], "5.52%")               # 溢价 = 4.796/4.5452-1

    def test_premium_matches_production_value(self):
        """溢价 5.52% 与生产端 qdiicn.php 上 SZ161130 一致（已核对）。"""
        html = self.client.get("/history/fundhistory/SZ161130").get_data(as_text=True)
        self.assertIn("5.52%", html)

    def test_shareshistory_diffs_and_turnover(self):
        """份额差与换手率。

        换手口径：100 * volume / (shares万份 * 10000)，对应 PHP
        ``GetTurnoverDisplay()``（php/ui/stockcomm.php:70）。
        快照数据是自洽的（份额 80500 万份、成交 6369249），
        真实样本见 python/dbtools/_seed_shares_sample.py。
        """
        data = self.client.get("/api/history/shareshistory/SZ161130").get_json()
        cells = data["rows"][0]["cells"]
        self.assertEqual(cells[1], "8050")                # 份额(万)
        self.assertEqual(cells[2], "-4190.48")            # 场内新增 = 8050-12240.48
        self.assertEqual(cells[3], "6369249")             # 数量
        self.assertEqual(cells[4], "7.91%")               # 换手

    def test_netvalueclose_joins_price_with_previous_netvalue(self):
        data = self.client.get("/api/history/netvalueclose/SZ161130").get_json()
        labels = [c["text"] for c in data["columns"]]
        self.assertEqual(labels, ["日期", "价格", "净值", "溢价", "涨幅", "份额(万)", "换手"])
        self.assertEqual(len(data["rows"]), 1)
        cells = data["rows"][0]["cells"]
        self.assertEqual(cells[0], "2026-09-30")
        self.assertEqual(cells[1][0], "4.796")            # 价格
        self.assertEqual(cells[2][0], "4.545")            # 净值（前一交易日）
        self.assertEqual(cells[4], "1.46%")               # 涨幅 = 4.796/4.727-1

    # -- 分页 -------------------------------------------------------------
    def test_paging_default_limits_rows(self):
        data = self.client.get("/api/history/stockhistory/SZ161130").get_json()
        self.assertEqual(data["num"], 10)
        self.assertEqual(data["total"], 2)

    def test_paging_num_zero_means_all(self):
        data = self.client.get("/api/history/stockhistory/SZ161130?start=0&num=0").get_json()
        self.assertEqual(data["num"], 0)
        self.assertEqual(len(data["rows"]), 2)

    def test_paging_invalid_values_fall_back(self):
        data = self.client.get("/api/history/stockhistory/SZ161130?start=abc&num=-5").get_json()
        self.assertEqual((data["start"], data["num"]), (0, 10))

    def test_parse_paging_helper(self):
        class Args:
            def __init__(self, values):
                self.values = values

            def get(self, name):
                return self.values.get(name)

        self.assertEqual(parse_paging(Args({})), (0, 10))
        self.assertEqual(parse_paging(Args({"start": "10", "num": "20"})), (10, 20))
        self.assertEqual(parse_paging(Args({"num": "0"})), (0, 0))

    # -- 空数据 -----------------------------------------------------------
    def test_empty_table_renders_message_not_error(self):
        """没有数据的页面要给出提示，而不是报错或空白表格。"""
        html = self.client.get("/history/fundhistory/SZ161130").get_data(as_text=True)
        self.assertIn("溢价", html)
        # 造假数据源里 fundest 为空 -> 不显示官方EST列
        data = self.client.get("/api/history/fundhistory/SZ161130").get_json()
        labels = [c["text"] for c in data["columns"]]
        self.assertNotIn("官方EST", labels)

    def test_fundhistory_shows_official_est_time_and_error_when_recorded(self):
        """有 fundest 记录时，「官方EST / 时间 / 误差」三列要出来且算得对。

        对应 PHP ``fundhistoryparagraph.php:74-86`` 的列定义与
        ``_echoFundHistoryTableItem()`` 的取值：

        * 官方 EST 与净值比价上色；
        * 时间截到分（``GetHM()``）；
        * 误差 = ``GetPercentageDisplay($fNetValue, $fEstValue)``。

        **关键口径**：``fundest`` 是按**净值日期**查的
        （PHP 用的是同一个 ``$strDate``），实测把记录放在行情日期上会一行都查不到。
        测试替身的净值日期是 2026-09-29（QDII 的 net_date），
        所以记录也放在 09-29。
        """
        source = make_source()
        connection = FakeConnection(TABLES)
        # 在净值日期上放一条官方 EST：4.5452 与净值完全相同 -> 误差 0
        connection.tables = dict(TABLES)
        connection.tables["fundest"] = [
            {"id": 1, "stock_id": "1", "date": "2026-09-29", "close": 4.5452,
             "time": "04:00:00"},
        ]
        app = create_app(
            {"TESTING": True},
            history_service_factory=lambda: HistoryService(source, connection),
        )
        client = app.test_client()

        data = client.get("/api/history/fundhistory/SZ161130").get_json()
        labels = [c["text"] for c in data["columns"]]
        self.assertIn("官方EST", labels)
        self.assertIn("时间", labels)
        self.assertIn("误差", labels)

        cells = data["rows"][0]["cells"]
        # 日期 | 价格 | 净值 | 溢价 | 官方EST | 时间 | 误差
        self.assertEqual(cells[0], "2026-09-30")
        self.assertEqual(cells[2][0], "4.545")            # 净值（09-29）
        self.assertEqual(cells[4][0], "4.545")            # 官方 EST
        self.assertEqual(cells[5], "04:00")               # 时间截到分
        self.assertEqual(cells[6], "0")                   # 误差 0

    def test_fundhistory_error_column_flags_a_bad_estimate(self):
        """误差列要真的反映偏差，不是恒为 0。"""
        source = make_source()
        connection = FakeConnection(TABLES)
        connection.tables = dict(TABLES)
        # 故意放一个偏 10% 的估值：净值 4.5452 -> 5.0
        connection.tables["fundest"] = [
            {"id": 1, "stock_id": "1", "date": "2026-09-29", "close": 5.0,
             "time": "04:00:00"},
        ]
        app = create_app(
            {"TESTING": True},
            history_service_factory=lambda: HistoryService(source, connection),
        )
        client = app.test_client()

        cells = client.get("/api/history/fundhistory/SZ161130").get_json()["rows"][0]["cells"]
        # (5.0 / 4.5452 - 1) * 100 = 10.0055...% -> 两位小数去尾零 -> 10.01%
        self.assertEqual(cells[6], "10.01%")

    def test_fundhistory_est_of_zero_shows_no_est_cells(self):
        """官方 EST 为 ``"0"`` 时那三格不出现 —— PHP 的 ``== false`` 语义。

        PHP 的守卫是 ``if ($strEstValue = $arFundEst['close'])``
        （``fundhistoryparagraph.php:17``）：``""`` 与 ``"0"`` 都是**假值**。
        Python 里非空字符串 ``"0"`` 是**真值**，直接写 ``if estimate_close:``
        会给 0 值的 EST 摆出三格 —— 这是接详情页时做 PHP 逐例对照才发现的
        （``_php_fundhistory_check.php`` case.4），单测里没这条例就会一直绿。
        """
        source = make_source()
        connection = FakeConnection(TABLES)
        connection.tables = dict(TABLES)
        connection.tables["fundest"] = [
            {"id": 1, "stock_id": "1", "date": "2026-09-29", "close": "0",
             "time": "04:00:00"},
        ]
        app = create_app(
            {"TESTING": True},
            history_service_factory=lambda: HistoryService(source, connection),
        )
        client = app.test_client()

        data = client.get("/api/history/fundhistory/SZ161130").get_json()
        # 列仍然在（列集由 fundest.count 决定，不是由这一行的值决定），
        # 但这一行只到「溢价」为止
        self.assertIn("官方EST", [c["text"] for c in data["columns"]])
        self.assertEqual(len(data["rows"][0]["cells"]), 4)

    def test_fundhistory_last_column_is_price_when_est_has_no_netvalue(self):
        """第 8 列文案 = ``<est>价格`` —— est 没有净值记录时。

        对应 PHP ``RefGetTableColumnNetValue()``（``php/ui/stocktable.php:320``）
        的第三支（``TableColumnPrice``）。生产端 ``sz161125cn.php`` 上
        SZ161125 的 est 是 ^GSPC（``QdiiGetEstSymbol`` 映射到**指数**而非
        ETF，``qdiiref.php:26``），指数没有净值行，所以显示「^GSPC价格」。
        替身里 SZ161130 的 est 是 QQQ 且 QQQ 没有 stock 行 -> 同走这一支。
        """
        source = make_source()
        connection = FakeConnection(TABLES)
        connection.tables = dict(TABLES)
        connection.tables["fundest"] = [
            {"id": 1, "stock_id": "1", "date": "2026-09-29", "close": 4.5452,
             "time": "04:00:00"},
        ]
        app = create_app(
            {"TESTING": True},
            history_service_factory=lambda: HistoryService(source, connection),
        )
        client = app.test_client()

        labels = [c["text"] for c in
                  client.get("/api/history/fundhistory/SZ161130").get_json()["columns"]]
        self.assertEqual(labels, ["日期", "价格", "净值", "溢价",
                                  "官方EST", "时间", "误差", "QQQ价格"])

    def test_fundhistory_last_column_is_netvalue_when_est_has_records(self):
        """第 8 列文案 = ``<est>净值`` —— est 有净值记录时。

        对应 ``RefGetTableColumnNetValue()`` 第一支
        （``CountNetValue() > 0``，``mysqlref.php:31`` 数的是 netvaluehistory
        行数）。生产端 ``sz162411cn.php`` 上 SZ162411 的 est 是 XOP（ETF，
        有净值行），显示「XOP净值」。替身用 KWEB 组基金 SZ164906 复现。
        """
        tables = dict(TABLES)
        tables["stockhistory"] = TABLES["stockhistory"] + [
            {"id": 3, "stock_id": "9", "date": "2026-09-30", "close": 1.0,
             "volume": 1000, "adjclose": 1.0},
            # 09-29 那行是给 GetDatePrev() 用的：QDII 的净值日期取前一交易日
            {"id": 4, "stock_id": "9", "date": "2026-09-29", "close": 0.99,
             "volume": 900, "adjclose": 0.99},
        ]
        tables["netvaluehistory"] = TABLES["netvaluehistory"] + [
            # 基金自己 09-29 的净值（QDII 取前一交易日）
            {"id": 20, "stock_id": "9", "date": "2026-09-29", "close": 1.0},
            # est（KWEB）09-29 的净值 —— 第 8 列取的就是它
            {"id": 21, "stock_id": "10", "date": "2026-09-29", "close": 42.0},
        ]
        tables["fundest"] = [
            {"id": 1, "stock_id": "9", "date": "2026-09-29", "close": 1.0,
             "time": "04:00:00"},
        ]
        source = MemoryFundDataSource(
            stocks={"SZ164906": "9", "KWEB": "10"},
            names={"SZ164906": "中概互联网LOF", "KWEB": "中概互联网ETF"},
            profiles={
                "SZ164906": FundProfile(
                    symbol="SZ164906",
                    stock_id="9",
                    position=0.95,
                    official_date="2026-09-29",
                    cny_est=6.705,
                    is_lof=True,
                    est_symbol="KWEB",
                ),
            },
        )
        app = create_app(
            {"TESTING": True},
            history_service_factory=lambda: HistoryService(source, FakeConnection(tables)),
        )
        client = app.test_client()

        data = client.get("/api/history/fundhistory/SZ164906").get_json()
        labels = [c["text"] for c in data["columns"]]
        self.assertEqual(labels, ["日期", "价格", "净值", "溢价",
                                  "官方EST", "时间", "误差", "KWEB净值"])
        # 行内第 8 格 = est 在 EST 记录日期上的净值
        cells = data["rows"][0]["cells"]
        self.assertEqual(cells[7][0], "42")


class FakeCnyRef:
    """汇率引用替身：只有一个符号（``_net_value_refs`` 只要这个）。"""

    def __init__(self, symbol):
        self._symbol = symbol

    def get_symbol(self):
        return self._symbol


class FakeHoldingStockRef:
    """成分股引用替身：只有一个 stock_id。"""

    def __init__(self, stock_id):
        self._stock_id = stock_id

    def get_stock_id(self):
        return self._stock_id


class FakeHoldingsRef:
    """持仓型引用替身。

    ``_net_value_refs`` 靠 ``get_proportion_array`` 认它（duck-typing），
    ``qdii_mix_position`` 还要 ``get_holdings_ratio_array`` 与
    ``get_holdings_ref_array``。
    """

    def __init__(self, cny_symbol="USCNY", proportions=None, ratios=None, refs=None):
        self._cny = FakeCnyRef(cny_symbol)
        self.proportions = proportions
        self.ratios = ratios if ratios is not None else {}
        self.refs = refs if refs is not None else []

    def get_cny_ref(self):
        return self._cny

    def get_proportion_array(self, date, prev_date):
        return self.proportions

    def get_holdings_ratio_array(self):
        return self.ratios

    def get_holdings_ref_array(self):
        return self.refs


def _qdii_get_stock_position(proportion, prev, net_value, cny_prev, cny, f_input=None):
    from web.services.history_service import qdii_get_stock_position
    if f_input is None:
        return qdii_get_stock_position(proportion, prev, net_value, cny_prev, cny)
    return qdii_get_stock_position(proportion, prev, net_value, cny_prev, cny, f_input)


class QdiiGetStockPositionTest(unittest.TestCase):
    """``_QdiiGetStockPosition()``（netvaluehistoryparagraph.php:5）。

    这组数特意凑成"仓位 = 0.95"：prev=100 / net=95 → 净值涨跌 -5%，
    cny 从 7.0 到 6.63 → 汇率 -5.2857%，两者一除正是 0.9459 → "0.95"。
    """

    def test_normal_case_rounds_to_two_decimals(self):
        value = _qdii_get_stock_position(1.0, 100.0, 95.0, 7.0, 6.63)
        self.assertEqual(value, "0.95")

    def test_keeps_trailing_zeros(self):
        """PHP 直接 ``number_format($fVal, 2)`` —— 不削尾零。

        ``format_number_display`` 那套会把它变成 "1"，这里必须区分：
        下一行还要拿它和 ``number_format(GetPosition(), 2)`` 做**字符串**相等比较。
        """
        # 让 percent / est_percent 正好等于 1：两边涨跌相同
        value = _qdii_get_stock_position(1.0, 100.0, 95.0, 7.0, 6.65)
        self.assertEqual(value, "1.00")

    def test_small_move_is_skipped(self):
        """净值涨跌不超过 ``fInput``（默认 4%）就不算 —— 噪声比信号大。"""
        self.assertIsNone(_qdii_get_stock_position(1.0, 100.0, 98.0, 7.0, 6.63))

    def test_custom_threshold(self):
        self.assertIsNotNone(_qdii_get_stock_position(1.0, 100.0, 98.0, 7.0, 6.63, 1.0))

    def test_zero_proportion_does_not_raise(self):
        """PHP 这里是裸除法，proportion 为 0 会 DivisionByZeroError fatal。

        Python 侧返回 None（页面留空）—— 记在 README 的 known deviation 里。
        """
        self.assertIsNone(_qdii_get_stock_position(0.0, 100.0, 95.0, 7.0, 6.63))

    def test_zero_result_is_dropped(self):
        """``$fVal > MIN_FLOAT_VAL`` 不成立时 PHP 返回 false。"""
        # 汇率没动 → est_percent = 0 → 被 MIN_FLOAT_VAL 挡掉
        self.assertIsNone(_qdii_get_stock_position(1.0, 100.0, 95.0, 7.0, 7.0))

    def test_negative_result_is_dropped(self):
        """仓位不可能是负数，PHP 同样直接 false。"""
        self.assertIsNone(_qdii_get_stock_position(1.0, 95.0, 100.0, 7.0, 6.63))


class QdiiMixPositionTest(unittest.TestCase):
    """``_QdiiMixGetPosition()``（netvaluehistoryparagraph.php:23）。"""

    def _ref(self, proportions, ratios, refs):
        return FakeHoldingsRef(proportions=proportions, ratios=ratios, refs=refs)

    def test_weighted_total(self):
        """Σ[成分股涨跌 × 持仓比例 / 100] = 0.02×40% + 0.05×60% = 0.038。"""
        ref = self._ref(
            proportions=[0.02, 0.05],
            ratios={"11": 40.0, "12": 60.0},
            refs=[FakeHoldingStockRef("11"), FakeHoldingStockRef("12")],
        )
        from web.services.history_service import qdii_mix_position

        value = qdii_mix_position(ref, "2026-10-14", "2026-10-13", 100.0, 95.0, 7.0, 6.63)
        self.assertEqual(value, "0.05")

    def test_missing_proportion_returns_none(self):
        """``GetProportionArray()`` 有一只成分股缺数据就整段 false。"""
        ref = self._ref(proportions=None, ratios={"11": 100.0}, refs=[FakeHoldingStockRef("11")])
        from web.services.history_service import qdii_mix_position

        self.assertIsNone(
            qdii_mix_position(ref, "2026-10-14", "2026-10-13", 100.0, 95.0, 7.0, 6.63)
        )

    def test_pairs_by_stock_id_not_index(self):
        """刻意偏离 PHP 的一处：按 stock_id 回查比例，不靠 foreach 下标。

        ``_build_holding_refs`` 在"成分股符号查不到"时会跳过那只
        （PHP 不跳），于是 ``holdings_refs`` 比 ``holdings_ratio`` 短，
        按下标配对会把 A 股的比例安到港股头上。这里钉住"少了也对"。

        数字挑过：按 stock_id 得 "0.09"，按下标会得 "0.05"。
        """
        ref = self._ref(
            proportions=[0.5],                   # 只剩一只的比例
            ratios={"11": 10.0, "12": 90.0},     # 比例表还是两条
            refs=[FakeHoldingStockRef("12")],    # 但引用只剩 12 那只
        )
        from web.services.history_service import qdii_mix_position

        # total = 0.5 × 0.9 = 0.45（若按下标会取到 10%，得 0.05）
        value = qdii_mix_position(ref, "2026-10-14", "2026-10-13", 100.0, 95.0, 7.0, 6.63)
        self.assertEqual(value, "0.09")

    def test_int_and_str_keys_both_work(self):
        """CK4 踩过的坑：比例表的键是 int，``get_stock_id()`` 给 str。"""
        ref = self._ref(
            proportions=[0.05],
            ratios={11: 60.0},                   # int 键
            refs=[FakeHoldingStockRef("11")],    # str id
        )
        from web.services.history_service import qdii_mix_position

        self.assertIsNotNone(
            qdii_mix_position(ref, "2026-10-14", "2026-10-13", 100.0, 95.0, 7.0, 6.63)
        )


class NetValueRefsTest(unittest.TestCase):
    """``EchoNetValueHistoryParagraph()`` 的三支选路（``:157-172``）。"""

    def _service(self):
        return HistoryService(make_source(), FakeConnection(TABLES))

    def test_plain_symbol_gets_no_columns(self):
        """既不是 QDII 也不是 LofMix：只有日期/净值/涨幅三列。

        ``SH513050`` 正是这一类 —— 它在 ``holdings`` 表里有数据（所以走 CK4
        分支），但不在 ``QdiiGetSymbolArray()`` 也不在 ``GetLofMixSymbolArray()``。
        生产端那一页的净值记录表就是三列。
        """
        self.assertEqual(self._service()._net_value_refs("SH513050"), (None, None, None))

    def test_qdii_symbol_gets_cny_and_est(self):
        cny, est, holdings = self._service()._net_value_refs("SZ161130")
        self.assertEqual(cny, "USCNY")
        self.assertEqual(est, "^NDX")
        self.assertIsNone(holdings)

    def test_qdii_hk_symbol_uses_hkcny(self):
        cny, est, _holdings = self._service()._net_value_refs("SZ161124")
        self.assertEqual(cny, "HKCNY")
        self.assertEqual(est, "^HSI")

    def test_qdii_jp_and_eu_regions(self):
        service = self._service()
        self.assertEqual(service._net_value_refs("SH513800")[0], "JPCNY")
        self.assertEqual(service._net_value_refs("SH513080")[0], "EUCNY")

    def test_lof_mix_symbol_gets_cny_but_no_est(self):
        """第 2 支：``$est_ref = false``，仓位改走 ``_QdiiMixGetPosition``。"""
        ref = FakeHoldingsRef(cny_symbol="USCNY")
        cny, est, holdings = self._service()._net_value_refs("SZ161129", ref)
        self.assertEqual(cny, "USCNY")
        self.assertIsNone(est)
        self.assertIs(holdings, ref, "传进来的持仓引用应该被复用，不该重建")

    def test_lof_mix_symbol_without_holdings_has_no_cny(self):
        """持仓引用建不出来（或没有成分股）时 ``uscny_ref`` 是 None。

        PHP 那边 ``new CnyReference('USCNY')`` 那行是**注释掉的**
        （``holdingsref.php:49``），所以同样退化成三列。
        """
        cny, est, _holdings = self._service()._net_value_refs("SZ161129")
        self.assertEqual((cny, est), (None, None))

    def test_qdii_wins_over_lof_mix(self):
        """4 只符号同时在这两个数组里，PHP 先问 ``in_arrayQdii``。

        （生产端 2026-10-12 把那 8 只加进了 QDII 数组，本地 PHP 仓库还没有；
        Python 跟生产端，所以这里也走 QDII 支。）证据是汇率列来自
        ``QDII_REGIONS['us']`` 而不是持仓引用 —— 这个数据源没有持仓数据，
        走 LofMix 支会连汇率列都没有。
        """
        cny, est, _holdings = self._service()._net_value_refs("SZ160216")
        self.assertEqual(cny, "USCNY")
        # 持仓型 QDII 查不到中间层符号，est 是 None —— 于是仓位改走
        # _QdiiMixGetPosition（下面一条测试钉这个）
        self.assertIsNone(est)

    def test_qdii_without_est_still_passes_holdings_ref(self):
        """QDII 支里 ``est_ref`` 为 false 时，PHP 用**传进来的 $ref** 反算仓位。

        ``EchoNetValueItem()`` 的 ``else if ($cny_ref)`` 支不管前面走的是
        哪一支，都用同一个 ``$ref`` 调 ``_QdiiMixGetPosition()``。
        """
        holdings = FakeHoldingsRef(cny_symbol="USCNY")
        cny, est, passed = self._service()._net_value_refs("SZ160216", holdings)
        self.assertEqual(cny, "USCNY")
        self.assertIsNone(est)
        self.assertIs(passed, holdings)


class NetValueParagraphTest(unittest.TestCase):
    """``EchoNetValueHistoryParagraph()``（netvaluehistoryparagraph.php:138）。"""

    def _service(self, tables=None):
        return HistoryService(make_source(), FakeConnection(tables if tables is not None else TABLES))

    def test_no_net_value_returns_none(self):
        """PHP: ``if (($iTotal = $ref->CountNetValue()) == 0) return;``"""
        tables = dict(TABLES)
        tables["netvaluehistory"] = []
        self.assertIsNone(self._service(tables).net_value_paragraph("SZ161130", num=1))

    def test_unknown_symbol_returns_none(self):
        self.assertIsNone(self._service().net_value_paragraph("NOPE", num=1))

    def test_num_one_returns_latest_row_only(self):
        """CK4 传的是 ``false, 0, 1`` —— 只摆最新一天。"""
        table = self._service().net_value_paragraph("SZ161130", num=1)
        self.assertEqual(table["total"], 2)
        self.assertEqual(len(table["rows"]), 1)
        self.assertEqual(table["rows"][0].cells[0], "2026-10-01")

    def test_num_zero_returns_all_rows(self):
        table = self._service().net_value_paragraph("SZ161130", num=0)
        self.assertEqual(len(table["rows"]), 2)

    def test_plain_symbol_has_three_columns(self):
        """SH513050 走 else 支：日期/净值/涨幅，没有汇率也没有仓位。"""
        tables = dict(TABLES)
        tables["netvaluehistory"] = [
            {"id": 1, "stock_id": "20", "date": "2026-10-13", "close": 1.0115},
            {"id": 2, "stock_id": "20", "date": "2026-10-12", "close": 1.0139},
        ]
        source = MemoryFundDataSource(
            stocks={"SH513050": "20", "USCNY": "8"},
            names={"SH513050": "中概互联网ETF易方达"},
        )
        service = HistoryService(source, FakeConnection(tables))
        table = service.net_value_paragraph("SH513050", num=1)
        self.assertEqual([c["text"] for c in table["columns"]], ["日期", "净值", "涨幅"])
        # 生产端那一格是 "1.0115" —— NETVALUE_PRECISION(4)，不是默认的 3 位
        self.assertEqual(table["rows"][0].cells[1][0], "1.0115")
        self.assertEqual(table["rows"][0].cells[2], "-0.24%")

    def _position_tables(self):
        """SZ161130（QDII，est=^NDX）的两天数据，凑成"反算仓位 = 0.95"。

        净值 100 → 95（-5%），USCNY 7.0 → 6.63（-5.2857%），
        ^NDX 两天都是 100（比例 1.0）→ -5 / -5.2857 = 0.9459 → "0.95"。
        """
        return {
            "netvaluehistory": [
                {"id": 1, "stock_id": "1", "date": "2026-10-01", "close": 95.0},
                {"id": 2, "stock_id": "1", "date": "2026-09-29", "close": 100.0},
                {"id": 10, "stock_id": "8", "date": "2026-10-01", "close": 6.63},
                {"id": 11, "stock_id": "8", "date": "2026-09-29", "close": 7.0},
                {"id": 20, "stock_id": "30", "date": "2026-10-01", "close": 100.0},
                {"id": 21, "stock_id": "30", "date": "2026-09-29", "close": 100.0},
            ],
        }

    def _position_source(self, position):
        return MemoryFundDataSource(
            stocks={"SZ161130": "1", "^NDX": "30", "USCNY": "8"},
            names={"SZ161130": "易方达纳斯达克100LOF", "USCNY": "美元人民币"},
            profiles={
                "SZ161130": FundProfile(
                    symbol="SZ161130", stock_id="1", position=position,
                    official_date="2026-09-29", cny_est=6.705, is_lof=True,
                    est_symbol="^NDX",
                ),
            },
            positions={"1": position},
        )

    def test_position_row_is_marked_when_it_matches(self):
        """反算出的仓位与库里登记值一致时整行标黄（``EchoMatchTableColumn``）。"""
        service = HistoryService(self._position_source(0.95), FakeConnection(self._position_tables()))
        row = service.net_value_paragraph("SZ161130", num=1)["rows"][0]
        self.assertEqual(row.cells[-1], "0.95")
        self.assertTrue(row.match, "反算值 0.95 与登记仓位 0.95 一致，应该标黄")

    def test_position_row_not_marked_when_it_differs(self):
        service = HistoryService(self._position_source(0.5), FakeConnection(self._position_tables()))
        row = service.net_value_paragraph("SZ161130", num=1)["rows"][0]
        self.assertEqual(row.cells[-1], "0.95")
        self.assertFalse(row.match, "登记仓位是 0.50，不该标黄")


if __name__ == "__main__":
    unittest.main()
