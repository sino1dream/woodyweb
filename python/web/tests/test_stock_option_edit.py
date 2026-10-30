"""通用股票编辑页（``/stock/<symbol>/edit/<option>``）的 web 层测试。

对应 PHP：

* ``woody/res/edit*.php``（16 个 4 行壳子）→ ``php/_editstockoption.php``
* ``woody/res/php/_editstockoptionform.php``（表单 + 日期/值/说明三张 switch 表）
* ``woody/res/php/_submitstockoptions.php``（POST + ``_updateStockOption*`` 一族）

本文件验四件事：

1. **分派**：16 项都在 —— 14 项在本页可写（含会员级的"申购金额"），
   2 项（季报 / 持仓）跳到各自的专门页；
2. **默认值口径**：每项的日期与初始值照 ``_getStockOptionDate()`` /
   ``_getStockOptionVal()``；
3. **写入语义**：日频表的"空值（含 ``'0'``）→ 删、否则 WriteDaily"、
   EMA 的"先清空再回填"、AH/HA 的**删除方向**、配对表的方向语义、
   校准的"估算值 → 校准值"换算、分红/拆股/申购金额的连带影响；
4. **权限与错误**：非管理员 POST 403（会员级那一项除外）、不认识的选项 404、
   符号不在库时不摆表单、专门页那一项一律 302。

用内存 sqlite 假库（``tests/sqlitedb.py``）—— 理由写在那个模块的
docstring 里：写方法都是"先查后写"，只有真数据库才分得清"写了"与"没写"。
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

from web.app import create_app  # noqa: E402
from web.services.admin_auth import ADMIN_EMAIL, ENV_ADMIN_EMAIL  # noqa: E402
from web.services.stock_option_service import (  # noqa: E402
    STOCK_OPTIONS,
    StockOptionService,
    _intval,
    _is_zero_text,
    _php_empty,
    _php_is_numeric,
)
from web.tests.sqlitedb import FakeQuote, FakeSource, SqliteConnection  # noqa: E402


#: 假库里的符号：一只 LOF 基金（1）+ 一只美股指数（2）+ 一只 A 股（3）
#: + 一个 H 股（4）+ 一个 ADR（5）+ 一只境内指数 ETF（8）+ 一只商品期货基金（9）
#: + 中间价（7）。后面三个是 ``editcalibration`` 的分支要用的。
SZ161130 = 1
GSPC = 2
SH600030 = 3
HK06030 = 4
US_CRD = 5
NDX = 6
USCNY = 7
SH510300 = 8
SH518800 = 9

#: 会员 1 = 演示登录身份；会员 2 = "别人"，用来验会员级写入口只动自己那一行。
MEMBER_SELF = 1
MEMBER_OTHER = 2
#: 会员 1 名下"分组名 = 符号"的那个分组与组里这一行（``editstockamount`` 的靶子）。
SELF_GROUP_ID = 7
SELF_ITEM_ID = 7
OTHER_GROUP_ID = 8
OTHER_ITEM_ID = 8


def build_connection() -> SqliteConnection:
    """建一套够 14 个选项跑的假库。"""
    connection = SqliteConnection()
    connection.create_stock([
        (SZ161130, "SZ161130", "易方达纳斯达克100LOF"),
        (GSPC, "^GSPC", "标普500指数"),
        (SH600030, "SH600030", "中信证券"),
        (HK06030, "HK06030", "中信证券"),
        (US_CRD, "US_CRD", "Cree"),
        (NDX, "^NDX", "纳斯达克100指数"),
        (USCNY, "USCNY", "美元中间价"),
        (SH510300, "SH510300", "沪深300ETF"),
        (SH518800, "SH518800", "黄金ETF"),
    ])
    connection.create_history()
    connection.create_daily("netvaluehistory")
    connection.create_calibration()
    connection.create_daily("futurepremium")
    connection.create_daily("shareshistory")
    connection.create_daily("sharesdiff")
    connection.create_daily("stockema50")
    connection.create_daily("stockema200")
    connection.create_daily("stockdividend")
    connection.create_daily("stocksplit")
    connection.create_group_tables()
    connection.create_member([(MEMBER_SELF, ADMIN_EMAIL), (MEMBER_OTHER, "other@example.com")])
    connection.create_val("groupitemamount", column="num", sql_type="INTEGER NOT NULL")
    #: ``editstockamount`` 的靶子：会员 1 名下**分组名 = 符号**的那个分组
    #: （PHP 的"我的自选"路径，``SqlGetMyStockGroupItemId``）。会员 2 也有一行
    #: —— 用来验"只改自己名下那一行"。
    connection.execute(
        "INSERT INTO `stockgroup` (`id`, `member_id`, `groupname`) VALUES (?, ?, ?)",
        (SELF_GROUP_ID, MEMBER_SELF, "SZ161130"),
    )
    connection.execute(
        "INSERT INTO `stockgroup` (`id`, `member_id`, `groupname`) VALUES (?, ?, ?)",
        (OTHER_GROUP_ID, MEMBER_OTHER, "SZ161130"),
    )
    #: ``record = 0``（没有成交过）—— 拆股流水那一支会跳过它们，见
    #: ``_split_transaction_items`` 的 ``get_stock_id_array(True)``。
    for group_id, item_id in ((SELF_GROUP_ID, SELF_ITEM_ID),
                              (OTHER_GROUP_ID, OTHER_ITEM_ID)):
        connection.execute(
            "INSERT INTO `stockgroupitem` (`id`, `stockgroup_id`, `stock_id`, "
            "`quantity`, `cost`, `record`) VALUES (?, ?, ?, ?, ?, ?)",
            (item_id, group_id, SZ161130, 0, 0.0, 0),
        )
    for table in ("ahpair", "adrpair", "fundpair"):
        connection.create_pair(table)
    connection.create_val("fundposition")
    connection.create_val("lastcalibration")

    for date, close in (("2026-10-22", 4.70), ("2026-10-23", 4.75), ("2026-10-26", 4.769)):
        connection.insert_daily(
            "stockhistory", SZ161130, date, close,
            extra_columns="`volume`, `adjclose`", extra_values=(1000, close),
        )
    connection.insert_daily("netvaluehistory", SZ161130, "2026-10-26", 4.3784)
    connection.insert_daily("netvaluehistory", SZ161130, "2026-10-27", 4.4324)
    connection.insert_daily("calibrationhistory", SZ161130, "2026-10-26", 41.111,
                            extra_columns="`time`, `num`", extra_values=("10:00:00", 1))
    connection.insert_daily("shareshistory", SZ161130, "2026-10-23", 100.0)
    connection.insert_daily("shareshistory", SZ161130, "2026-10-26", 150.0)
    connection.insert_daily("stockema200", SZ161130, "2026-10-26", 4.512)
    connection.insert_daily("stockema50", SZ161130, "2026-10-26", 4.623)
    connection.execute(
        "INSERT INTO `fundposition` (`id`, `close`) VALUES (?, ?), (?, ?)",
        (SZ161130, 0.95, US_CRD, 100),
    )
    connection.execute(
        "INSERT INTO `fundpair` (`id`, `stock_id`) VALUES (?, ?)", (SZ161130, NDX)
    )

    # -- editcalibration 要用的数据 -----------------------------------------
    #: 配对标的（^NDX）的净值：``_getBestEstNetValue()`` 的第一级，
    #: 也是表单里显示给管理员的"对方值"。
    connection.insert_daily("netvaluehistory", NDX, "2026-10-26", 29402.51)
    connection.insert_daily("netvaluehistory", NDX, "2026-10-27", 29500.0)
    #: ^GSPC 只有行情没有净值 —— 用来验"退到该日收盘"那一级。
    connection.insert_daily(
        "stockhistory", GSPC, "2026-10-27", 7720.39,
        extra_columns="`volume`, `adjclose`", extra_values=(0, 7720.39),
    )
    #: 美元中间价（换算要用）+ 境内指数 ETF 自己的净值。
    connection.insert_daily("netvaluehistory", USCNY, "2026-10-26", 6.7165)
    connection.insert_daily("netvaluehistory", USCNY, "2026-10-27", 6.7147)
    #: 10-29 只有中间价、SZ161130 自己没有净值 —— 用来把"缺分母"与"缺中间价"
    #: 两件事分开验（两者的报错不同）。
    connection.insert_daily("netvaluehistory", USCNY, "2026-10-29", 6.7100)
    connection.insert_daily("netvaluehistory", SH510300, "2026-10-27", 3.5)
    #: 空值分支要跟着删的那一行（PHP ``LastCalibrationSql::DeleteById``）。
    connection.execute(
        "INSERT INTO `lastcalibration` (`id`, `close`) VALUES (?, ?)", (SZ161130, 41.111)
    )
    return connection


def build_service(connection: SqliteConnection, quote=None) -> StockOptionService:
    return StockOptionService(source=FakeSource(connection, quote=quote))


class StockOptionAppTest(unittest.TestCase):
    """带假服务工厂的 app：路由与模板全走真代码，只把数据层换成 sqlite。"""

    def setUp(self):
        self.connection = build_connection()
        self.service = build_service(
            self.connection, quote=FakeQuote(prev_price="4.769", date="2026-10-29")
        )
        self.app = create_app(
            {"TESTING": True, "SECRET_KEY": "test"},
            stock_option_service_factory=lambda: self.service,
        )
        self.client = self.app.test_client()
        self._saved_env = os.environ.pop(ENV_ADMIN_EMAIL, None)

    def tearDown(self):
        os.environ.pop(ENV_ADMIN_EMAIL, None)
        if self._saved_env is not None:
            os.environ[ENV_ADMIN_EMAIL] = self._saved_env

    # -- 索引页 -------------------------------------------------------------
    def test_index_lists_all_16_options(self):
        """16 项全在，顺序与 ``GetStockOptionArray()`` 一致。"""
        response = self.client.get("/stock/SZ161130/edit")
        self.assertEqual(response.status_code, 200)
        body = response.data.decode("utf-8")
        for spec in STOCK_OPTIONS:
            self.assertIn(spec.submit, body)
        #: 14 项在本页可编辑 + 2 项有专门页（季报 / 持仓）= 16，没有"未迁"
        self.assertIn("可在此页编辑（14 项）", body)
        self.assertIn("另有专门页（2 项）", body)
        self.assertNotIn("未迁（", body)

    def test_index_links_point_at_each_option(self):
        body = self.client.get("/stock/SZ161130/edit").data.decode("utf-8")
        self.assertIn("/stock/SZ161130/edit/editnetvalue", body)
        self.assertIn("/stock/SZ161130/edit/editcalibration", body)
        self.assertIn("/stock/SZ161130/edit/editstocksplit", body)
        #: 申购金额迁完之后也有链接了
        self.assertIn("editstockamount", body)

    def test_index_links_dedicated_pages(self):
        """季报 / 持仓不给 ``<option>`` 链接，直接指专门页。"""
        body = self.client.get("/stock/SZ161130/edit").data.decode("utf-8")
        self.assertIn("/holdings/quarter/SZ161130", body)
        self.assertIn("/holdings/exhaustive/SZ161130", body)
        self.assertNotIn('href="/stock/SZ161130/edit/editquarterreport"', body)
        self.assertNotIn('href="/stock/SZ161130/edit/editstockholdings"', body)

    # -- 未知选项 -----------------------------------------------------------
    def test_unknown_option_is_404(self):
        response = self.client.get("/stock/SZ161130/edit/nosuchoption")
        self.assertEqual(response.status_code, 404)

    # -- 符号不在库 ---------------------------------------------------------
    def test_symbol_not_in_stock_table_shows_notice_without_form(self):
        body = self.client.get("/stock/NOPE999/edit/editstock").data.decode("utf-8")
        self.assertIn("stock 表里没有符号 NOPE999", body)
        self.assertNotIn('<textarea', body)

    # -- 有专门页的两项：一律跳转 -------------------------------------------
    def test_dedicated_option_get_redirects(self):
        for option, target in (
            ("editquarterreport", "/holdings/quarter/SZ161130"),
            ("editstockholdings", "/holdings/exhaustive/SZ161130"),
        ):
            response = self.client.get("/stock/SZ161130/edit/" + option)
            self.assertEqual(response.status_code, 302, option)
            self.assertTrue(response.headers["Location"].endswith(target), option)

    def test_dedicated_option_post_redirects_and_writes_nothing(self):
        """POST 也弹到专门页：本页不接这一项的写（两处各写一份迟早跑偏）。"""
        before = len(self.connection.queries)
        response = self.client.post(
            "/stock/SZ161130/edit/editquarterreport",
            data={"date": "2026-10-26", "value": "100"},
        )
        self.assertEqual(response.status_code, 302)
        writes = [
            sql for sql, _ in self.connection.queries[before:]
            if not sql.strip().upper().startswith("SELECT")
        ]
        self.assertEqual(writes, [])


class StockOptionReadTest(unittest.TestCase):
    """默认值口径：``_getStockOptionDate()`` / ``_getStockOptionVal()``。"""

    def setUp(self):
        self.connection = build_connection()
        self.service = build_service(
            self.connection, quote=FakeQuote(prev_price="4.769", date="2026-10-29")
        )

    def form(self, option, symbol="SZ161130"):
        return self.service.get_form(symbol, option)

    def test_editstock_shows_stock_name(self):
        self.assertEqual(self.form("editstock")["value"], "易方达纳斯达克100LOF")
        self.assertFalse(self.form("editstock")["date_enabled"])

    def test_editnetvalue_date_and_value(self):
        """日期取净值表最新一天，值是那天的净值（去掉尾零）。"""
        form = self.form("editnetvalue")
        self.assertEqual(form["date"], "2026-10-27")
        self.assertEqual(form["value"], "4.4324")

    def test_editnetvalue_falls_back_to_history_date_when_no_netvalue(self):
        """净值表没有行时退到行情表最新一天，值留空（PHP 的 ``false`` → 空串）。"""
        form = self.form("editnetvalue", symbol="^GSPC")
        #: ^GSPC 在假库里没有净值行，也没有 stockhistory 行 → 退到今天
        self.assertEqual(form["value"], "")

    def test_editstockclose_uses_quote_prev_price(self):
        """``$ref->GetPrevPrice()`` 优先取快照昨收，日期取快照日之前最近交易日。"""
        form = self.form("editstockclose")
        self.assertEqual(form["value"], "4.769")
        self.assertEqual(form["date"], "2026-10-26")

    def test_editstockclose_falls_back_to_history_close(self):
        """没有快照时退到 ``stockhistory`` 最新一条收盘价。"""
        service = build_service(self.connection, quote=None)
        self.assertEqual(service.get_form("SZ161130", "editstockclose")["value"], "4.769")

    def test_editstockema_shows_both_emas(self):
        self.assertEqual(self.form("editstockema")["value"], "4.51/4.62")
        self.assertEqual(self.form("editstockema")["date"], "2026-10-26")

    def test_editstockema_shows_hint_when_missing(self):
        """任一侧缺失 → 显示格式提示串（PHP ``if ($str200 && $str50)``）。"""
        self.connection.execute("DELETE FROM `stockema50`")
        self.assertEqual(self.form("editstockema")["value"], "EMA200/50")

    def test_editsharesdiff_computes_diff_from_shareshistory(self):
        """``sharesdiff`` 没有行时，用份额序列相减（150 - 100）。"""
        form = self.form("editsharesdiff")
        self.assertEqual(form["date"], "2026-10-26")
        self.assertEqual(form["value"], "50")

    def test_editfund_shows_pair_and_position(self):
        """``INDEX*仓位``：配对取自 ``fundpair``，仓位取自 ``fundposition``。"""
        self.assertEqual(self.form("editfund")["value"], "^NDX*0.95")

    def test_editfund_hint_for_lof_without_pair(self):
        """没有配对也没有仓位时给 ``INDEX*0.95``（LOF）或 ``INDEX*1``（其余）。"""
        self.connection.execute("DELETE FROM `fundpair`")
        self.connection.execute("DELETE FROM `fundposition`")
        self.assertEqual(self.form("editfund")["value"], "INDEX*0.95")
        #: 美股指数不是 LOF → 1
        self.assertEqual(self.form("editfund", symbol="^GSPC")["value"], "INDEX*1")

    def test_editstockah_and_ha_hints(self):
        self.assertEqual(self.form("editstockah")["value"], "H")
        self.assertEqual(self.form("editstockha")["value"], "A")

    def test_editstockadr_hint(self):
        self.assertEqual(self.form("editstockadr")["value"], "ADR/100")

    def test_editstockadr_with_pair_and_ratio(self):
        """H 股 → ADR：``ADR/比例``，比例取自 ADR 自己的仓位行。"""
        self.connection.execute(
            "INSERT INTO `adrpair` (`id`, `stock_id`) VALUES (?, ?)", (US_CRD, HK06030)
        )
        self.assertEqual(self.form("editstockadr", symbol="HK06030")["value"], "US_CRD/100")


class StockOptionWriteTest(unittest.TestCase):
    """写入语义：逐项对照 ``_submitstockoptions.php`` 的写函数。"""

    def setUp(self):
        self.connection = build_connection()
        self.service = build_service(self.connection, quote=None)

    def submit(self, option, date=None, value=None, symbol="SZ161130"):
        return self.service.submit(symbol, option, date, value)

    # -- 日频表：净值 -------------------------------------------------------
    def test_netvalue_write_and_delete(self):
        result = self.submit("editnetvalue", "2026-10-28", "4.5")
        self.assertTrue(result["ok"])
        self.assertEqual(
            self.connection.single(
                "SELECT close FROM `netvaluehistory` WHERE stock_id = ? AND `date` = ?",
                (SZ161130, "2026-10-28"),
            )["close"],
            4.5,
        )
        result = self.submit("editnetvalue", "2026-10-28", "")
        self.assertTrue(result["ok"])
        self.assertIsNone(self.connection.single(
            "SELECT close FROM `netvaluehistory` WHERE stock_id = ? AND `date` = ?",
            (SZ161130, "2026-10-28"),
        ))

    def test_netvalue_zero_means_delete(self):
        """PHP ``empty('0')`` 为真 → 填 0 等于删除（pitfalls 里那条）。"""
        result = self.submit("editnetvalue", "2026-10-26", "0")
        self.assertTrue(result["ok"])
        self.assertIsNone(self.connection.single(
            "SELECT close FROM `netvaluehistory` WHERE stock_id = ? AND `date` = ?",
            (SZ161130, "2026-10-26"),
        ))

    def test_netvalue_unchanged_is_not_rewritten(self):
        result = self.submit("editnetvalue", "2026-10-26", "4.3784")
        self.assertTrue(result["ok"])
        self.assertIn("没有变化", result["message"])

    def test_daily_write_needs_date(self):
        result = self.submit("editnetvalue", "", "4.5")
        self.assertFalse(result["ok"])
        self.assertIn("日期", result["message"])

    # -- 收盘价 -------------------------------------------------------------
    def test_close_writes_close_and_adjclose(self):
        result = self.submit("editstockclose", "2026-10-23", "9.99")
        self.assertTrue(result["ok"])
        row = self.connection.single(
            "SELECT close, adjclose FROM `stockhistory` WHERE stock_id = ? AND `date` = ?",
            (SZ161130, "2026-10-23"),
        )
        self.assertEqual(row["close"], 9.99)
        self.assertEqual(row["adjclose"], 9.99)

    def test_close_never_inserts_missing_day(self):
        """``_updateStockHistoryClose`` 只在有记录时改，不新增（照抄 PHP）。"""
        result = self.submit("editstockclose", "2026-09-01", "9.99")
        self.assertFalse(result["ok"])
        self.assertIn("没有行情记录", result["message"])
        self.assertIsNone(self.connection.single(
            "SELECT close FROM `stockhistory` WHERE stock_id = ? AND `date` = ?",
            (SZ161130, "2026-09-01"),
        ))

    def test_close_rejects_empty_value(self):
        """PHP 会把空串写成 0（收盘价被清零）。本版挡住 —— 记在文档里。"""
        result = self.submit("editstockclose", "2026-10-23", "")
        self.assertFalse(result["ok"])

    # -- 股票名 -------------------------------------------------------------
    def test_stock_name_write(self):
        result = self.submit("editstock", None, "新名字")
        self.assertTrue(result["ok"])
        self.assertEqual(
            self.connection.single("SELECT name FROM `stock` WHERE id = ?", (SZ161130,))["name"],
            "新名字",
        )

    def test_stock_name_empty_is_refused(self):
        result = self.submit("editstock", None, "   ")
        self.assertFalse(result["ok"])

    # -- EMA ---------------------------------------------------------------
    def test_ema_clears_then_writes_both(self):
        self.submit("editstockema", "2026-10-27", "5.0/6.0")
        ema200 = self.connection.single(
            "SELECT close FROM `stockema200` WHERE stock_id = ?", (SZ161130,)
        )
        ema50 = self.connection.single(
            "SELECT close FROM `stockema50` WHERE stock_id = ?", (SZ161130,)
        )
        self.assertEqual(ema200["close"], 5.0)
        self.assertEqual(ema50["close"], 6.0)
        #: 旧的那一行（10-26）已被清掉 —— EMA 是递推量，不能新旧混排
        self.assertIsNone(self.connection.single(
            "SELECT close FROM `stockema200` WHERE stock_id = ? AND `date` = ?",
            (SZ161130, "2026-10-26"),
        ))

    def test_ema_without_slash_only_clears(self):
        """值里没有 ``/`` → 只清空不回填（等价于"删除 EMA"）。"""
        result = self.submit("editstockema", "2026-10-27", "")
        self.assertTrue(result["ok"])
        self.assertIn("已全部删除", result["message"])
        self.assertIsNone(self.connection.single(
            "SELECT close FROM `stockema200` WHERE stock_id = ?", (SZ161130,)
        ))

    def test_ema_placeholder_is_refused_without_deleting(self):
        """占位提示串 ``EMA200/50`` 里有 ``/``，PHP 会把它当数字写进库。

        PHP 的顺序是"先清空、再看有没有斜杠"，于是管理员**不动那一栏直接提交**
        会把 ``'EMA200'`` 写进 ``double`` 列（变 0）并删掉真 EMA。本版先校验：
        两段不是数字就一句错误、一个字节都不写 —— 原有 EMA 必须还在。
        """
        result = self.submit("editstockema", "2026-10-27", "EMA200/50")
        self.assertFalse(result["ok"])
        self.assertIn("两段都要是数字", result["message"])
        self.assertIsNotNone(self.connection.single(
            "SELECT close FROM `stockema200` WHERE stock_id = ?", (SZ161130,)
        ))

    # -- AH / HA -----------------------------------------------------------
    def test_ah_writes_pair_host_is_a_share(self):
        result = self.submit("editstockah", None, "HK06030", symbol="SH600030")
        self.assertTrue(result["ok"])
        self.assertEqual(
            self.connection.single("SELECT stock_id FROM `ahpair` WHERE id = ?", (SH600030,))["stock_id"],
            HK06030,
        )

    def test_ah_clear_deletes_by_host(self):
        self.connection.execute(
            "INSERT INTO `ahpair` (`id`, `stock_id`) VALUES (?, ?)", (SH600030, HK06030)
        )
        self.submit("editstockah", None, "", symbol="SH600030")
        self.assertIsNone(
            self.connection.single("SELECT id FROM `ahpair` WHERE id = ?", (SH600030,))
        )

    def test_ha_writes_pair_from_h_side(self):
        """H 股页填 A 股 → 写的是 ``ahpair(id=A, stock_id=H)``，不是反过来。"""
        result = self.submit("editstockha", None, "SH600030", symbol="HK06030")
        self.assertTrue(result["ok"])
        row = self.connection.single("SELECT stock_id FROM `ahpair` WHERE id = ?", (SH600030,))
        self.assertEqual(row["stock_id"], HK06030)

    def test_ha_clear_deletes_by_pair_side(self):
        """``DeleteByPairSymbol(H)`` —— 删的是"配对侧是 H"的行。

        与 ``editstockah`` 的删除方向**相反**，这一条搞反会删掉别的 A 股的配对。
        """
        self.connection.execute(
            "INSERT INTO `ahpair` (`id`, `stock_id`) VALUES (?, ?), (?, ?)",
            (SH600030, HK06030, HK06030, SH600030),
        )
        self.submit("editstockha", None, "", symbol="HK06030")
        self.assertIsNone(
            self.connection.single("SELECT id FROM `ahpair` WHERE stock_id = ?", (HK06030,))
        )
        #: 以 H 股为**本体**的那一行不受影响
        self.assertIsNotNone(
            self.connection.single("SELECT id FROM `ahpair` WHERE id = ?", (HK06030,))
        )

    # -- ADR ---------------------------------------------------------------
    def test_adr_writes_pair_and_ratio(self):
        """``adrpair`` 的本体是 ADR：填 ``US_CRD/2`` → ``id=US_CRD, stock_id=H``。"""
        result = self.submit("editstockadr", None, "US_CRD/2", symbol="HK06030")
        self.assertTrue(result["ok"])
        row = self.connection.single("SELECT stock_id FROM `adrpair` WHERE id = ?", (US_CRD,))
        self.assertEqual(row["stock_id"], HK06030)
        self.assertEqual(
            self.connection.single(
                "SELECT close FROM `fundposition` WHERE id = ?", (US_CRD,)
            )["close"],
            2.0,
        )

    def test_adr_ratio_one_does_not_touch_position(self):
        """``intval($strRatio) != 1`` 才写仓位 —— 填 1 时不动仓位。"""
        self.connection.execute(
            "UPDATE `fundposition` SET `close` = 7 WHERE id = ?", (US_CRD,)
        )
        self.submit("editstockadr", None, "US_CRD/1", symbol="HK06030")
        self.assertEqual(
            self.connection.single(
                "SELECT close FROM `fundposition` WHERE id = ?", (US_CRD,)
            )["close"],
            7.0,
        )

    def test_adr_ratio_zero_deletes_pair_and_position(self):
        self.connection.execute(
            "INSERT INTO `adrpair` (`id`, `stock_id`) VALUES (?, ?)", (US_CRD, HK06030)
        )
        result = self.submit("editstockadr", None, "US_CRD/0", symbol="HK06030")
        self.assertTrue(result["ok"])
        self.assertIsNone(
            self.connection.single("SELECT id FROM `adrpair` WHERE id = ?", (US_CRD,))
        )
        self.assertIsNone(
            self.connection.single("SELECT id FROM `fundposition` WHERE id = ?", (US_CRD,))
        )

    def test_adr_unknown_symbol_is_refused(self):
        result = self.submit("editstockadr", None, "NOPE/1", symbol="HK06030")
        self.assertFalse(result["ok"])
        self.assertIn("stock 表里没有", result["message"])

    # -- 基金配对 -----------------------------------------------------------
    def test_fund_writes_pair_and_position(self):
        self.connection.execute("DELETE FROM `fundpair`")
        result = self.submit("editfund", None, "^NDX*1.2")
        self.assertTrue(result["ok"])
        self.assertEqual(
            self.connection.single("SELECT stock_id FROM `fundpair` WHERE id = ?", (SZ161130,))["stock_id"],
            NDX,
        )
        self.assertEqual(
            self.connection.single("SELECT close FROM `fundposition` WHERE id = ?", (SZ161130,))["close"],
            1.2,
        )

    def test_fund_index_only_keeps_position(self):
        """只填 INDEX **不算**删除 —— 这是与 PHP 的唯一一处刻意偏离。

        PHP 里 ``$strPos === false`` 时 ``$strPos == '0'`` 为真，于是"只改配对"
        会把校准历史与配对一起删掉。本版按页面说明（``输入INDEX*0删除对应关系``）
        实现：要删必须显式带 ``*0``。
        """
        self.submit("editfund", None, "^NDX")
        self.assertIsNotNone(
            self.connection.single("SELECT id FROM `fundpair` WHERE id = ?", (SZ161130,))
        )
        self.assertEqual(
            self.connection.single("SELECT close FROM `fundposition` WHERE id = ?", (SZ161130,))["close"],
            0.95,
        )
        self.assertIsNotNone(
            self.connection.single(
                "SELECT id FROM `calibrationhistory` WHERE stock_id = ?", (SZ161130,)
            )
        )

    def test_fund_index_with_zero_clears_pair_calibration_and_position(self):
        result = self.submit("editfund", None, "^NDX*0")
        self.assertTrue(result["ok"])
        self.assertIsNone(
            self.connection.single("SELECT id FROM `fundpair` WHERE id = ?", (SZ161130,))
        )
        self.assertIsNone(
            self.connection.single(
                "SELECT id FROM `calibrationhistory` WHERE stock_id = ?", (SZ161130,)
            )
        )
        self.assertIsNone(
            self.connection.single("SELECT id FROM `fundposition` WHERE id = ?", (SZ161130,))
        )

    def test_fund_position_only(self):
        """只填小于 3 的数字 → 只改仓位（``$strIndex = false``）。"""
        result = self.submit("editfund", None, "0.5")
        self.assertTrue(result["ok"])
        self.assertEqual(
            self.connection.single("SELECT close FROM `fundposition` WHERE id = ?", (SZ161130,))["close"],
            0.5,
        )
        self.assertIsNotNone(
            self.connection.single("SELECT id FROM `fundpair` WHERE id = ?", (SZ161130,))
        )

    def test_fund_zero_position_clears_position_only(self):
        """填 ``0`` → ``$strIndex`` 为假，只删仓位，配对留着。"""
        self.submit("editfund", None, "0")
        self.assertIsNone(
            self.connection.single("SELECT id FROM `fundposition` WHERE id = ?", (SZ161130,))
        )
        self.assertIsNotNone(
            self.connection.single("SELECT id FROM `fundpair` WHERE id = ?", (SZ161130,))
        )


class StockOptionCalibrationTest(unittest.TestCase):
    """``editcalibration``：估算值 → 校准值的换算，以及"空值删两处"。

    对应 ``_getStockOptionCalibration()``（读）与 ``_updateStockOptionCalibration()``
    （写，``_submitstockoptions.php:340``）。
    """

    def setUp(self):
        self.connection = build_connection()
        self.service = build_service(
            self.connection, quote=FakeQuote(prev_price="4.769", date="2026-10-29")
        )

    def submit(self, date=None, value=None, symbol="SZ161130"):
        return self.service.submit(symbol, "editcalibration", date, value)

    def calibration(self, stock_id=SZ161130, date="2026-10-27"):
        return self.connection.single(
            "SELECT close, num FROM `calibrationhistory` WHERE stock_id = ? AND `date` = ?",
            (stock_id, date),
        )

    # -- 读初值：对方标的的值 -----------------------------------------------
    def test_read_shows_pair_net_value(self):
        """有 ``fundpair`` 时取**配对标的**的净值（这里是 ^NDX）。"""
        form = self.service.get_form("SZ161130", "editcalibration")
        self.assertEqual(form["date"], "2026-10-27")
        self.assertEqual(form["value"], "29500")

    def test_read_falls_back_to_history_close(self):
        """配对标的没有净值 → 退到该日收盘（``_getBestEstNetValue`` 第二级）。"""
        self.assertEqual(self.service._best_est_net_value("^GSPC", "2026-10-27"), "7720.39")

    def test_read_returns_empty_when_nothing_found(self):
        """三级都取不到 → 空串（PHP 那边 ``false`` 打印出来也是空）。"""
        self.assertEqual(self.service._best_est_net_value("^GSPC", "2026-10-01"), "")

    def test_read_without_any_est_shows_placeholder(self):
        """既没配对也不在 QDII 清单 → ``'对方净值'``（``STOCK_DISP_NETVALUE``）。"""
        self.assertEqual(self.service._read_calibration("SH600030", SH600030, "2026-10-27"),
                         "对方净值")

    # -- 写：换算 -----------------------------------------------------------
    def test_write_converts_estimate_to_calibration(self):
        """QDII：``估算值 × 中间价 ÷ 基金净值``（``QdiiGetCalibration``）。"""
        result = self.submit("2026-10-27", "29500")
        self.assertTrue(result["ok"], result["message"])
        expected = 29500.0 * 6.7147 / 4.4324
        self.assertAlmostEqual(self.calibration()["close"], expected, places=6)
        #: ``UpdateDaily`` / ``InsertDaily`` 都把 ``num`` 置 1（不是累加）。
        self.assertEqual(self.calibration()["num"], 1)

    def test_write_china_index_uses_cny_one(self):
        """``in_arrayChinaIndex`` → ``$strCNY = '1.0'``，不乘汇率。"""
        result = self.submit("2026-10-27", "3.8", symbol="SH510300")
        self.assertTrue(result["ok"], result["message"])
        self.assertAlmostEqual(
            self.calibration(SH510300)["close"], 3.8 / 3.5, places=6
        )

    def test_write_updates_existing_row(self):
        """已有同一天的行 → 覆盖（值变了才 UPDATE，``MIN_FLOAT_VAL``）。"""
        before = self.calibration(date="2026-10-26")
        self.assertEqual(before["close"], 41.111)
        result = self.submit("2026-10-26", "29402.51")
        self.assertTrue(result["ok"], result["message"])
        self.assertNotEqual(self.calibration(date="2026-10-26")["close"], 41.111)

    def test_write_same_value_reports_no_change(self):
        """算出来的值与库里相同 → 不发 UPDATE，提示"未写入"。"""
        self.submit("2026-10-26", "29402.51")
        again = self.submit("2026-10-26", "29402.51")
        self.assertTrue(again["ok"])
        self.assertIn("未写入", again["message"])

    # -- 写：空值删两处 -----------------------------------------------------
    def test_empty_deletes_calibration_and_last_calibration(self):
        """空值 → 删该日校准行 **和** ``lastcalibration``（PHP 两处都删）。"""
        self.assertIsNotNone(
            self.connection.single("SELECT id FROM `lastcalibration` WHERE id = ?", (SZ161130,))
        )
        result = self.submit("2026-10-26", "")
        self.assertTrue(result["ok"])
        self.assertIsNone(self.calibration(date="2026-10-26"))
        self.assertIsNone(
            self.connection.single("SELECT id FROM `lastcalibration` WHERE id = ?", (SZ161130,))
        )

    def test_zero_also_deletes(self):
        """``empty('0')`` 为真 —— 填 ``0`` 也是删除（pitfalls #123）。"""
        result = self.submit("2026-10-26", "0")
        self.assertTrue(result["ok"])
        self.assertIsNone(self.calibration(date="2026-10-26"))

    # -- 写：拒绝的几支 -----------------------------------------------------
    def test_missing_cny_refuses_and_writes_nothing(self):
        """中间价那天没有记录 → 拒绝（PHP 是静默 return，两边都不写）。"""
        result = self.submit("2026-10-28", "29500")
        self.assertFalse(result["ok"])
        self.assertIn("USCNY", result["message"])
        self.assertIsNone(self.calibration(date="2026-10-28"))

    def test_missing_net_value_refuses(self):
        """基金自己没有净值 → 没有分母，拒绝（PHP 会写出 INF → 列里变 0）。"""
        result = self.submit("2026-10-29", "29500")
        self.assertFalse(result["ok"])
        self.assertIn("没有净值记录", result["message"])

    def test_china_future_is_refused(self):
        """商品期货基金：PHP 自己也是 ``unhandled China future symbol``。"""
        result = self.submit("2026-10-27", "100", symbol="SH518800")
        self.assertFalse(result["ok"])
        self.assertIn("商品期货", result["message"])

    def test_unknown_symbol_class_is_refused(self):
        """既非境内指数也非 QDII → ``unhandled other symbol``。"""
        result = self.submit("2026-10-26", "100", symbol="SH600030")
        self.assertFalse(result["ok"])
        self.assertIn("unhandled", result["message"])

    def test_empty_date_is_refused(self):
        result = self.submit("", "100")
        self.assertFalse(result["ok"])
        self.assertIn("日期", result["message"])


class StockOptionPermissionTest(unittest.TestCase):
    """权限闸门：非管理员 POST 必须 403 且一个字节都不写。"""

    def setUp(self):
        self.connection = build_connection()
        self.service = build_service(self.connection, quote=None)
        self.app = create_app(
            {"TESTING": True, "SECRET_KEY": "test"},
            stock_option_service_factory=lambda: self.service,
        )
        self.client = self.app.test_client()
        self._saved_env = os.environ.pop(ENV_ADMIN_EMAIL, None)

    def tearDown(self):
        os.environ.pop(ENV_ADMIN_EMAIL, None)
        if self._saved_env is not None:
            os.environ[ENV_ADMIN_EMAIL] = self._saved_env

    def test_non_admin_post_is_403(self):
        os.environ[ENV_ADMIN_EMAIL] = "someone@example.com"
        before = len(self.connection.queries)
        response = self.client.post(
            "/stock/SZ161130/edit/editnetvalue",
            data={"date": "2026-10-28", "value": "4.5"},
        )
        self.assertEqual(response.status_code, 403)
        writes = [
            sql for sql, _ in self.connection.queries[before:]
            if not sql.strip().upper().startswith("SELECT")
        ]
        self.assertEqual(writes, [])

    def test_non_admin_get_shows_readonly_form(self):
        os.environ[ENV_ADMIN_EMAIL] = "someone@example.com"
        body = self.client.get("/stock/SZ161130/edit/editnetvalue").data.decode("utf-8")
        self.assertIn("没有这一项的写权限", body)
        self.assertIn("readonly", body)
        self.assertIn("disabled", body)

    def test_admin_post_redirects_to_get(self):
        response = self.client.post(
            "/stock/SZ161130/edit/editnetvalue",
            data={"date": "2026-10-28", "value": "4.5"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/stock/SZ161130/edit/editnetvalue", response.headers["Location"])

    def test_failed_post_keeps_the_submitted_value(self):
        """写失败时不要 302 —— 把人留在页面上，填过的值原样回显。"""
        response = self.client.post(
            "/stock/SZ161130/edit/editstockclose",
            data={"date": "2026-09-01", "value": "9.99"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.data.decode("utf-8")
        self.assertIn("没有行情记录", body)
        self.assertIn("9.99", body)


class StockOptionDividendTest(unittest.TestCase):
    """``editstockdividend``：写一行分红，连带改校准值 / 净值 / 复权价。

    对应 ``_updateStockOptionDividend()``（``_submitstockoptions.php:301``）。
    假库里 ``SZ161130`` 在 10-26 有校准值 41.111、净值 4.3784，行情从 10-22 起。
    """

    def setUp(self):
        self.connection = build_connection()
        self.service = build_service(self.connection)
        #: 10-27 的行情由本类自己补：共享假库不加这条，否则 ``DATE_HISTORY``
        #: 的默认日期会从 10-26 变成 10-27，动到 ``editsharesdiff`` /
        #: ``editstockema`` 那批用例的期望值。
        self.connection.insert_daily(
            "stockhistory", SZ161130, "2026-10-27", 4.80,
            extra_columns="`volume`, `adjclose`", extra_values=(1200, 4.80),
        )

    def submit(self, date="2026-10-27", value="0.1", symbol="SZ161130"):
        return self.service.submit(symbol, "editstockdividend", date, value)

    def adjclose(self, date, stock_id=SZ161130):
        row = self.connection.single(
            "SELECT adjclose FROM `stockhistory` WHERE stock_id = ? AND `date` = ?",
            (stock_id, date))
        return row["adjclose"] if row else None

    def net_value(self, date, stock_id=SZ161130):
        row = self.connection.single(
            "SELECT close FROM `netvaluehistory` WHERE stock_id = ? AND `date` = ?",
            (stock_id, date))
        return row["close"] if row else None

    def calibration(self, date="2026-10-26", stock_id=SZ161130):
        row = self.connection.single(
            "SELECT close FROM `calibrationhistory` WHERE stock_id = ? AND `date` = ?",
            (stock_id, date))
        return row["close"] if row else None

    # -- 写：三段副作用 -----------------------------------------------------
    def test_write_recalculates_calibration_net_value_and_adjclose(self):
        """校准值 ``昨收×原净值÷新净值``；净值减 D；复权价减 D 但**当天不减**。"""
        result = self.submit()
        self.assertTrue(result["ok"], result["message"])
        #: ``41.111 × 4.3784 ÷ 4.2784``
        expected = 41.111 * 4.3784 / (4.3784 - 0.1)
        self.assertAlmostEqual(self.calibration(), expected, places=5)
        self.assertAlmostEqual(self.net_value("2026-10-26"), 4.2784, places=6)
        self.assertAlmostEqual(self.adjclose("2026-10-22"), 4.60, places=6)
        self.assertAlmostEqual(self.adjclose("2026-10-26"), 4.669, places=6)
        #: ``$record['date'] != $strYMD`` —— 当天已经是除权价，不再减一次。
        self.assertAlmostEqual(self.adjclose("2026-10-27"), 4.80, places=6)

    def test_write_records_the_dividend_row(self):
        self.submit()
        row = self.connection.single(
            "SELECT close FROM `stockdividend` WHERE stock_id = ? AND `date` = ?",
            (SZ161130, "2026-10-27"))
        self.assertAlmostEqual(row["close"], 0.1, places=6)

    def test_same_value_does_not_recalculate(self):
        """值没变（``MIN_FLOAT_VAL`` 内）→ ``WriteDaily`` 返回 false → 整段不跑。

        这一例靠"复权价没有减第二次"来验 —— 只看返回值是看不出来的
        （两次提交都报成功）。
        """
        self.submit()
        once = self.adjclose("2026-10-22")
        again = self.submit()
        self.assertTrue(again["ok"])
        self.assertIn("未写入", again["message"])
        self.assertAlmostEqual(self.adjclose("2026-10-22"), once, places=6)

    # -- 删：不回滚 ---------------------------------------------------------
    def test_empty_deletes_row_but_does_not_roll_back(self):
        """清空只删 ``stockdividend`` 那一行 —— 复权价与净值都不回来。"""
        self.submit()
        adjusted = self.adjclose("2026-10-22")
        result = self.submit(value="")
        self.assertTrue(result["ok"], result["message"])
        self.assertIsNone(self.connection.single(
            "SELECT close FROM `stockdividend` WHERE stock_id = ? AND `date` = ?",
            (SZ161130, "2026-10-27")))
        self.assertAlmostEqual(self.adjclose("2026-10-22"), adjusted, places=6)
        self.assertAlmostEqual(self.net_value("2026-10-26"), 4.2784, places=6)

    def test_zero_also_deletes(self):
        """``empty('0')`` 为真 → 填 0 也是**删**，不是写一条 0 分红。"""
        self.submit()
        result = self.submit(value="0")
        self.assertTrue(result["ok"], result["message"])
        self.assertIsNone(self.connection.single(
            "SELECT close FROM `stockdividend` WHERE stock_id = ? AND `date` = ?",
            (SZ161130, "2026-10-27")))

    # -- 拒绝 ---------------------------------------------------------------
    def test_missing_net_value_refuses_and_writes_nothing(self):
        """分母（净值）取不到 → 整笔不写（PHP 这里是 ``INF`` / 除零）。"""
        self.connection.execute(
            "DELETE FROM `netvaluehistory` WHERE stock_id = ? AND `date` = ?",
            (SZ161130, "2026-10-26"))
        result = self.submit()
        self.assertFalse(result["ok"])
        self.assertIsNone(self.connection.single(
            "SELECT close FROM `stockdividend` WHERE stock_id = ? AND `date` = ?",
            (SZ161130, "2026-10-27")))
        #: 复权价一行都没动
        self.assertAlmostEqual(self.adjclose("2026-10-22"), 4.70, places=6)

    def test_non_number_refuses(self):
        result = self.submit(value="abc")
        self.assertFalse(result["ok"])
        self.assertIn("数字", result["message"])

    def test_missing_date_refuses(self):
        self.assertFalse(self.submit(date="")["ok"])

    # -- QDII 分支 ----------------------------------------------------------
    def test_qdii_branch_multiplies_inverted_ratio_and_leaves_net_alone(self):
        """本标的没有校准行 → 遍历把它当 est 的基金，用**本标的**净值当分母。

        两支的公式**分子分母相反**（``昨收 × 新净值 ÷ 原净值``），是 PHP 原文
        的形状。另一处刻意偏离：不走 ``WriteDaily`` 写本标的净值 ——
        PHP 那一行用的是循环变量，会给本标的凭空插一条负净值。
        """
        before = self.net_value("2026-10-26", NDX)
        result = self.submit(symbol="^NDX")
        self.assertTrue(result["ok"], result["message"])
        expected = 41.111 * (29402.51 - 0.1) / 29402.51
        self.assertAlmostEqual(self.calibration(), expected, places=5)
        #: 本标的（^NDX）的净值**不该被改**
        self.assertAlmostEqual(self.net_value("2026-10-26", NDX), before, places=6)


class StockOptionSplitTest(unittest.TestCase):
    """``editstocksplit``：比例、四段副作用、以及"重复提交会再除一次"。

    对应 ``_updateStockOptionSplit()``（``_submitstockoptions.php:253``）。
    """

    def setUp(self):
        self.connection = build_connection()
        self.service = build_service(self.connection)
        self.connection.insert_daily(
            "stockhistory", SZ161130, "2026-10-27", 4.80,
            extra_columns="`volume`, `adjclose`", extra_values=(1200, 4.80),
        )
        #: 一个持有 100 股 + 一笔建仓流水的成员。成员表里的 100 股必须与流水
        #: 那笔对上，否则拆股后的回填会把 ``quantity`` 算成"只有新买的那部分"。
        self.connection.execute(
            "INSERT INTO `stockgroup` (`id`, `member_id`, `groupname`) VALUES (?, ?, ?)",
            (1, 1, "演示分组"),
        )
        self.connection.execute(
            "INSERT INTO `stockgroupitem` (`id`, `stockgroup_id`, `stock_id`, `quantity`, "
            "`cost`, `record`) VALUES (?, ?, ?, ?, ?, ?)",
            (1, 1, SZ161130, 100, 400.0, 1),
        )
        self.connection.execute(
            "INSERT INTO `stocktransaction` (`id`, `groupitem_id`, `quantity`, `price`, "
            "`fees`, `filled`, `remark`) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (1, 1, 100, 4.0, 0.0, "2026-10-20 10:00:00", "建仓"),
        )

    def submit(self, date="2026-10-27", value="10:1", symbol="SZ161130"):
        return self.service.submit(symbol, "editstocksplit", date, value)

    def adjclose(self, date, stock_id=SZ161130):
        row = self.connection.single(
            "SELECT adjclose FROM `stockhistory` WHERE stock_id = ? AND `date` = ?",
            (stock_id, date))
        return row["adjclose"] if row else None

    def split_row(self, date="2026-10-27", stock_id=SZ161130):
        return self.connection.single(
            "SELECT close FROM `stocksplit` WHERE stock_id = ? AND `date` = ?",
            (stock_id, date))

    def calibration(self, date="2026-10-26", stock_id=SZ161130):
        row = self.connection.single(
            "SELECT close FROM `calibrationhistory` WHERE stock_id = ? AND `date` = ?",
            (stock_id, date))
        return row["close"] if row else None

    # -- 写：比例与三段重算 -------------------------------------------------
    def test_write_divides_adjclose_including_same_day(self):
        result = self.submit()
        self.assertTrue(result["ok"], result["message"])
        self.assertAlmostEqual(self.split_row()["close"], 10.0, places=6)
        self.assertAlmostEqual(self.adjclose("2026-10-22"), 0.47, places=6)
        self.assertAlmostEqual(self.adjclose("2026-10-26"), 0.4769, places=6)
        #: 与分红相反：拆股**连当天一起**除（当天开盘就是除权价）。
        self.assertAlmostEqual(self.adjclose("2026-10-27"), 0.48, places=6)

    def test_write_multiplies_calibration(self):
        """本标的有校准行 → ``昨收 × 比例`` 写回 ``date_prev``。"""
        self.submit()
        self.assertAlmostEqual(self.calibration(), 411.11, places=4)

    def test_zero_zero_deletes_row_only(self):
        """``0:0`` 只删 ``stocksplit`` 那一行，复权价与校准值都不回滚。"""
        self.submit()
        adjusted = self.adjclose("2026-10-22")
        result = self.submit(value="0:0")
        self.assertTrue(result["ok"], result["message"])
        self.assertIsNone(self.split_row())
        self.assertAlmostEqual(self.adjclose("2026-10-22"), adjusted, places=6)
        self.assertAlmostEqual(self.calibration(), 411.11, places=4)

    def test_zero_zero_accepts_numeric_equivalents(self):
        """``$ar[0] == '0'`` 是 PHP 8 松散比较：``0.0:0`` 也算删除。"""
        self.submit()
        result = self.submit(value="0.0:0")
        self.assertTrue(result["ok"], result["message"])
        self.assertIsNone(self.split_row())

    # -- 拒绝（都在写之前）--------------------------------------------------
    def test_without_colon_refuses(self):
        result = self.submit(value="101")
        self.assertFalse(result["ok"])
        self.assertIn("冒号", result["message"])
        self.assertIsNone(self.split_row())

    def test_zero_denominator_refuses_before_inserting(self):
        """``10:0`` —— PHP 8 在算比例时就抛，但那时行**已经插进去了**。本版不插。"""
        result = self.submit(value="10:0")
        self.assertFalse(result["ok"])
        self.assertIsNone(self.split_row())

    def test_zero_numerator_refuses(self):
        """``0:10`` 比例是 0，后面"复权价 ÷ 比例"会除零。"""
        result = self.submit(value="0:10")
        self.assertFalse(result["ok"])
        self.assertIsNone(self.split_row())

    def test_missing_prev_close_refuses(self):
        """没有昨收价 → 流水按 0 价成交（PHP 是 ``floatval(false)``）→ 拒绝。"""
        result = self.submit(date="2026-10-02")
        self.assertFalse(result["ok"])
        self.assertIn("昨收", result["message"])
        self.assertIsNone(self.split_row(date="2026-10-02"))

    # -- 重复提交 -----------------------------------------------------------
    def test_second_submit_same_day_divides_again(self):
        """``InsertDaily`` 有行返回 false，但**重算在 if 外面** —— 会再除一次。

        这是照抄的行为（见 ``_write_stocksplit`` 的 docstring），用测试钉住，
        免得有人"顺手修好"以后与 PHP 对不上。
        """
        self.submit()
        again = self.submit()
        self.assertTrue(again["ok"])
        self.assertIn("当天已有拆股记录", again["message"])
        self.assertAlmostEqual(self.split_row()["close"], 10.0, places=6)
        self.assertAlmostEqual(self.adjclose("2026-10-22"), 0.047, places=6)
        self.assertAlmostEqual(self.calibration(), 4111.1, places=4)

    # -- 交易流水 -----------------------------------------------------------
    def test_split_inserts_two_transactions_and_rebuilds_item(self):
        """卖出全部 + 按新比例买回，再回填 ``quantity/cost/record``。"""
        self.submit()
        rows = self.connection.execute(
            "SELECT quantity, price FROM `stocktransaction` WHERE groupitem_id = ? "
            "ORDER BY id", (1,))
        #: 第一行是 setUp 里那笔建仓，后两行才是拆股补的
        self.assertEqual([(r["quantity"], round(r["price"], 4)) for r in rows],
                         [(100, 4.0), (-100, 4.769), (1000, 0.4769)])
        item = self.connection.single(
            "SELECT quantity, record FROM `stockgroupitem` WHERE id = ?", (1,))
        #: 100（原持仓）− 100（卖出）+ 1000（按 10:1 买回）
        self.assertEqual(item["quantity"], 1000)
        #: 原有 1 笔 + 新插 2 笔
        self.assertEqual(item["record"], 3)

    def test_member_without_transaction_is_skipped(self):
        """``GetStockIdArray(true)`` 跳过 ``record = 0`` 的成员 —— 一笔没成交不拆。"""
        self.connection.execute(
            "INSERT INTO `stockgroupitem` (`id`, `stockgroup_id`, `stock_id`, `quantity`, "
            "`cost`, `record`) VALUES (?, ?, ?, ?, ?, ?)",
            (2, 1, GSPC, 50, 100.0, 0),
        )
        self.connection.execute(
            "INSERT INTO `stockgroup` (`id`, `member_id`, `groupname`) VALUES (?, ?, ?)",
            (2, 1, "空成员组"),
        )
        self.connection.execute(
            "INSERT INTO `stockgroupitem` (`id`, `stockgroup_id`, `stock_id`, `quantity`, "
            "`cost`, `record`) VALUES (?, ?, ?, ?, ?, ?)",
            (3, 2, SZ161130, 10, 40.0, 0),
        )
        self.submit()
        #: 只给真正成交过的那个成员补了流水
        count = self.connection.single(
            "SELECT COUNT(*) AS n FROM `stocktransaction`")["n"]
        self.assertEqual(count, 3)


class PhpSemanticsTest(unittest.TestCase):
    """四个 PHP 口径助手各自的边界（写错会让删除/写入判断整体偏一位）。"""

    def test_php_empty(self):
        self.assertTrue(_php_empty(None))
        self.assertTrue(_php_empty(""))
        self.assertTrue(_php_empty("0"))
        #: '0.0' 是**非空**字符串 —— 与 ``== '0'`` 的数值语义不同
        self.assertFalse(_php_empty("0.0"))
        self.assertFalse(_php_empty("  "))

    def test_is_zero_text(self):
        self.assertTrue(_is_zero_text("0"))
        self.assertTrue(_is_zero_text("0.0"))
        self.assertTrue(_is_zero_text(0))
        self.assertFalse(_is_zero_text("abc"))
        self.assertFalse(_is_zero_text("0.1"))
        #: None 是"没填"，不是"填了 0"
        self.assertFalse(_is_zero_text(None))

    def test_intval_truncates(self):
        """``intval('1.5')`` 是 1 —— ADR 的比例判断靠这个截断。"""
        self.assertEqual(_intval("1.5"), 1)
        self.assertEqual(_intval("2"), 2)
        self.assertEqual(_intval("abc"), 0)
        self.assertEqual(_intval(None), 0)

    def test_php_is_numeric(self):
        """``is_numeric()``：十进制、可选正负号与指数，前后空白都允许。"""
        for text in ("0", "100000", "-5", "+5", "1.5", ".5", "5.", " 12 ", "1e3", "1.2E-3"):
            self.assertTrue(_php_is_numeric(text), text)
        for text in ("", " ", "abc", "1e", "0x1A", "1,000", "1 2", None):
            self.assertFalse(_php_is_numeric(text), text)


class StockOptionAmountTest(unittest.TestCase):
    """``editstockamount``：会员级写入口（``_updateFundPurchaseAmount``）。

    PHP 原文（``_submitstockoptions.php:70``）::

        $strMemberId = SqlGetIdByEmail($strEmail);
        if ($strMemberId && $strStockId && is_numeric($strVal))
        {   if ($strGroupItemId = SqlGetMyStockGroupItemId(...))
                (new GroupItemAmountSql())->WriteString($strGroupItemId, $strVal);
        }

    三件事与其它 15 项不同：**不要求管理员**、**没有日期**、
    **找不到分组项就什么都不做**（不是清空、不是报错）。这三条各有用例。
    """

    def setUp(self):
        self.connection = build_connection()
        self.service = build_service(self.connection, quote=None)
        self._saved_env = os.environ.pop(ENV_ADMIN_EMAIL, None)

    def tearDown(self):
        os.environ.pop(ENV_ADMIN_EMAIL, None)
        if self._saved_env is not None:
            os.environ[ENV_ADMIN_EMAIL] = self._saved_env

    def submit(self, value, symbol="SZ161130"):
        return self.service.submit(symbol, "editstockamount", "", value)

    def amount(self, item_id=SELF_ITEM_ID):
        row = self.connection.single(
            "SELECT num FROM `groupitemamount` WHERE id = ?", (item_id,))
        return row["num"] if row else None

    # -- 读 -----------------------------------------------------------------
    def test_form_defaults_to_100000(self):
        """``ReadAmount()`` 读不到回落 ``'100000'``（PHP 的默认申购金额）。"""
        form = self.service.get_form("SZ161130", "editstockamount")
        self.assertEqual(form["value"], "100000")
        self.assertFalse(form["date_enabled"])
        self.assertEqual(form["notice"], "")

    def test_form_shows_existing_amount(self):
        self.assertTrue(self.submit("50000")["ok"])
        form = self.service.get_form("SZ161130", "editstockamount")
        self.assertEqual(form["value"], "50000")

    def test_form_notice_when_no_group_item(self):
        """没有「分组名 = 符号」的分组项 → 空值 + 说明（PHP 填 ``出错了``）。"""
        self.connection.execute("DELETE FROM `stockgroupitem`")
        form = self.service.get_form("SZ161130", "editstockamount")
        self.assertEqual(form["value"], "")
        self.assertIn("分组名 = SZ161130", form["notice"])

    def test_form_notice_when_member_missing(self):
        self.connection.execute("DELETE FROM `member`")
        form = self.service.get_form("SZ161130", "editstockamount")
        self.assertIn("member 表里没有", form["notice"])
        self.assertIn(ADMIN_EMAIL, form["notice"])

    # -- 写 -----------------------------------------------------------------
    def test_write_sets_amount(self):
        result = self.submit("50000")
        self.assertTrue(result["ok"], result["message"])
        self.assertEqual(self.amount(), 50000)
        #: 只动自己那一行 —— 另一个会员的分组项原样
        self.assertIsNone(self.amount(OTHER_ITEM_ID))

    def test_write_zero_is_a_value_not_a_delete(self):
        """``'0'`` 在这一项是**合法值**（与日频表的 ``empty('0')`` 完全不同）。"""
        self.assertTrue(self.submit("0")["ok"])
        self.assertEqual(self.amount(), 0)
        self.assertIsNotNone(
            self.connection.single(
                "SELECT num FROM `groupitemamount` WHERE id = ?", (SELF_ITEM_ID,)))

    def test_write_non_numeric_is_refused(self):
        before = len(self.connection.queries)
        result = self.submit("abc")
        self.assertFalse(result["ok"])
        self.assertIn("数字", result["message"])
        writes = [
            sql for sql, _ in self.connection.queries[before:]
            if not sql.strip().upper().startswith("SELECT")
        ]
        self.assertEqual(writes, [])

    def test_write_empty_is_refused(self):
        """``is_numeric('')`` 为假 —— 空值进不了门（因此也删不掉记录）。"""
        result = self.submit("")
        self.assertFalse(result["ok"])
        self.assertIsNone(self.amount())

    def test_write_same_value_reports_no_change(self):
        """``WriteString`` 字符串比较：同值返回 False（不 UPDATE），页面要说清。"""
        self.submit("50000")
        again = self.submit("50000")
        self.assertTrue(again["ok"])
        self.assertIn("没有变化", again["message"])

    def test_write_without_group_item_writes_nothing(self):
        self.connection.execute("DELETE FROM `stockgroupitem`")
        before = len(self.connection.queries)
        result = self.submit("50000")
        self.assertFalse(result["ok"])
        self.assertIn("分组名 = SZ161130", result["message"])
        writes = [
            sql for sql, _ in self.connection.queries[before:]
            if not sql.strip().upper().startswith("SELECT")
        ]
        self.assertEqual(writes, [])

    def test_write_without_member_writes_nothing(self):
        self.connection.execute("DELETE FROM `member`")
        result = self.submit("50000")
        self.assertFalse(result["ok"])
        self.assertIn("member 表里没有", result["message"])
        self.assertIsNone(self.amount())

    def test_other_member_identity_touches_its_own_row(self):
        """换身份 → 写的是**那个**会员名下那一行（PHP 用 ``$_POST['login']``）。"""
        os.environ[ENV_ADMIN_EMAIL] = "other@example.com"
        result = self.submit("70000")
        self.assertTrue(result["ok"], result["message"])
        self.assertEqual(self.amount(OTHER_ITEM_ID), 70000)
        self.assertIsNone(self.amount(SELF_ITEM_ID))

    def test_non_admin_can_write_this_option(self):
        """会员级入口：非管理员也能提交（PHP 这一支没有 ``$bAdmin`` 判断）。"""
        os.environ[ENV_ADMIN_EMAIL] = "other@example.com"
        app = create_app(
            {"TESTING": True, "SECRET_KEY": "test"},
            stock_option_service_factory=lambda: self.service,
        )
        client = app.test_client()
        response = client.post(
            "/stock/SZ161130/edit/editstockamount",
            data={"date": "", "value": "70000"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.amount(OTHER_ITEM_ID), 70000)


if __name__ == "__main__":
    unittest.main()
