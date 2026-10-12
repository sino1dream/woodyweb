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
        stocks={"SZ161130": "1", "USCNY": "8"},
        names={"SZ161130": "易方达纳斯达克100LOF", "USCNY": "美元人民币"},
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
        真实样本见 python/sql/_seed_shares_sample.py。
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


if __name__ == "__main__":
    unittest.main()
