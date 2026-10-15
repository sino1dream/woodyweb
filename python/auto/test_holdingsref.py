"""``ref/holdingsref.py`` 的单测（CK4-B）。

钉住几件迁移易错的事：

1. **``_est_net_value`` 的加权方向**：``Σ[比例 × 新价/旧价 / 汇率] ÷ Σ比例 - 1``，
   不是"比例 × 涨幅"。两者在比例之和恰为 100 时数值接近，所以**算错也看不出来**
   —— 持仓比例之和经常是 100.02（生产端 SH513050 就是这样），
   本文件用非 100 的比例组把它钉死。
2. **汇率调整除的是"成分股那一项"**，不是涨完之后再整体调一次
   （``holdingsref.php:332``）。整体那一次（``:339``）是**基金自身**的币种调整，
   两处用的系数不一定相同。
3. **旧价取 ``adjclose``**（复权价）而不是 close：持仓日到现在若有分红，
   用 close 会把除权当下跌。
4. **EST 日期取更早的那个市场**（``:97-101``）：港股比美股早收盘。
5. **三个净值的偏差补偿**：后两个净值要加回 ``official_est_adjust``；
   且 ``get_fair_net_value`` 在"不需要"时必须返回 ``None``（PHP 的 ``false``），
   因为渲染层靠它决定要不要画那一列。
6. **默认不写库**（``auto_write`` 关）—— 与 ``FundPairReference`` 同一条偏差。

**不联网、不连真库**：数据源与连接都是内存替身。
"""

from __future__ import annotations

import re
import unittest

from ref.holdingsref import (
    MIN_FLOAT_VAL,
    HoldingsReference,
    StockGetHoldingsReference,
    pick_est_date,
    ratio_of,
    ref_adjust_forex,
)

HOLDINGS_DATE = "2026-10-13"


# ---------------------------------------------------------------------------
# 替身
# ---------------------------------------------------------------------------
class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self._rows: list = []

    def execute(self, sql, params=()):
        self._rows = self.connection.dispatch(sql, params)
        return len(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class FakeConnection:
    """认得 ``holdings`` / ``holdingsdate`` / ``stockhistory`` / ``fundpair`` 四张表。"""

    def __init__(self, holdings=None, holdingsdate=None, history=None, pairs=None):
        self.tables = {
            "holdings": [dict(r) for r in (holdings or [])],
            "holdingsdate": [dict(r) for r in (holdingsdate or [])],
            "stockhistory": [dict(r) for r in (history or [])],
        }
        self.pairs = pairs or {}
        self.written: list = []
        self.queries: list = []

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        pass

    def dispatch(self, sql, params):
        self.queries.append(sql)
        upper = sql.strip().upper()

        if upper.startswith("INSERT"):
            self.written.append((sql, params))
            return []

        if "COUNT(*)" in sql:
            table = re.search(r"FROM\s+`([^`]+)`", sql)
            rows = self.tables.get(table.group(1), []) if table else []
            return [{"n": len([r for r in rows if str(r["stock_id"]) == str(params[0])])}]

        if "fundpair" in sql:
            symbol = params[0]
            target = self.pairs.get(symbol)
            return [{"symbol": target}] if target else []

        table_match = re.search(r"FROM\s+`([^`]+)`", sql)
        table = table_match.group(1) if table_match else ""
        if table == "holdingsdate":
            rows = [r for r in self.tables["holdingsdate"] if str(r["id"]) == str(params[0])]
            return rows[:1]

        rows = [r for r in self.tables.get(table, []) if str(r["stock_id"]) == str(params[0])]
        if table == "holdings":
            rows = sorted(rows, key=lambda r: float(r["ratio"]), reverse=True)
            return rows
        if table == "stockhistory":
            if "`date` = %s" in sql:
                rows = [r for r in rows if str(r["date"]) == str(params[1])]
            elif "`date` < %s" in sql:
                rows = [r for r in rows if str(r["date"]) < str(params[1])]
                rows.sort(key=lambda r: str(r["date"]), reverse=True)
            if "LIMIT 1" in sql:
                rows = rows[:1]
        return rows


class FakeSource:
    """够 ``HoldingsReference`` 走完构造的内存数据源。"""

    #: **id 不能重复**：``NAMES`` 是按 id 反查符号的，重复会让某个成分股
    #: 查到别的符号（写测试时踩过一次：``fx_shkdcny`` 与 ``00700`` 都给 11，
    #: 反查拿到汇率符号，于是港股被判成美股）。
    STOCK = {
        "SH513050": 1,
        "00700": 11,
        "PDD": 12,
        #: A 股符号**必须带前缀**才被 ``is_symbol_a()`` 认出来（"600519" 不带
        #: 前缀会被当成"其它"，也就是美股） —— 库里存的就是带前缀的写法。
        "SH600519": 13,
        "USCNY": 8,
        "HKCNY": 10,
        "fx_susdcny": 9,
        "fx_shkdcny": 21,
    }
    NAMES = {v: k for k, v in STOCK.items()}

    def __init__(self, connection=None, netvalue=None, position=None):
        self.connection = connection or FakeConnection()
        self.netvalue = netvalue or {}
        self.position = position or {}
        self.names: dict = {}

    def get_stock_id(self, symbol):
        return self.STOCK.get(symbol)

    def get_stock_symbol(self, stock_id):
        return self.NAMES.get(int(stock_id)) if isinstance(stock_id, int) else self.NAMES.get(stock_id)

    def get_stock_name(self, symbol):
        return self.names.get(symbol)

    def get_netvalue_close(self, stock_id, date):
        return self.netvalue.get((stock_id, str(date)))

    def get_position(self, stock_id):
        return self.position.get(stock_id, 1.0)

    def get_history_close(self, stock_id, date):
        return None

    def get_history_adjclose(self, stock_id, date):
        return None

    def count_net_value(self, stock_id):
        return 2

    def get_calibration_now(self, stock_id):
        return None

    def get_last_calibration(self, stock_id):
        return None


class FakeRef:
    """成分股引用替身。

    只实现 ``HoldingsReference`` 会用到的那几个方法。真实成分股是
    ``MysqlReference``（普通股票）或 ``FundPairReference``（自己也是配对基金）。
    """

    def __init__(self, symbol, stock_id, price, date="2026-10-15",
                 kind="us", est_from_pair=None):
        self._symbol = symbol
        self._stock_id = stock_id
        self._price = price
        self._date = date
        self._kind = kind
        self._est = est_from_pair

    def get_symbol(self):
        return self._symbol

    def get_stock_id(self):
        return self._stock_id

    def get_price(self):
        return self._price

    def get_date(self):
        return self._date

    def est_from_pair(self, *_args, **_kwargs):
        return self._est

    def is_symbol_a(self):
        return self._kind == "a"

    def is_symbol_h(self):
        return self._kind == "hk"

    def is_etf_a(self):
        return False

    def is_fund_a(self):
        return False

    def get_position(self):
        return 1.0


class StrIdRef(FakeRef):
    """``get_stock_id()`` 返回**字符串**的 :class:`FakeRef`。

    真实的 ``MysqlReference`` 就是这样（``refobj.py`` 里 ``self.stock_id`` 是
    str），而 ``holdings`` 表取出来的键是 int —— 两者凑在一起才有
    :class:`RatioOfTest` 那个坑。
    """

    def get_stock_id(self):
        return str(self._stock_id)


class FakeCny:
    """汇率引用替身：``get_close`` / ``get_price`` / ``get_date`` 都可控。"""

    def __init__(self, price, date="2026-10-15", closes=None):
        self._price = price
        self._date = date
        self._closes = closes or {}

    def get_price(self):
        return self._price

    def get_date(self):
        return self._date

    def get_close(self, date):
        return self._closes.get(str(date), self._price)


def _ref(holdings=None, holdingsdate=None, history=None, netvalue=None, pairs=None):
    """造一个持仓引用：**绕开成分股引用对象的真实构造**。

    构造完把 ``holdings_refs`` / ``holdings_ratio`` / 净值 换成测试想要的值 ——
    本文件的算法测试要的是精确可控的输入，不是"能不能连库"。
    """
    connection = FakeConnection(holdings=holdings, holdingsdate=holdingsdate,
                                history=history, pairs=pairs)
    source = FakeSource(connection=connection, netvalue=netvalue)
    return HoldingsReference("SH513050", source=source), source, connection


class RefAdjustForexTest(unittest.TestCase):
    """``RefAdjustForex()``（``holdingsref.php:3``）。"""

    def test_a_share_is_one(self):
        self.assertEqual(ref_adjust_forex(FakeRef("600519", 13, 1.0, kind="a"), 1.0055, 1.1), 1.0)

    def test_hk_uses_hkd(self):
        self.assertEqual(ref_adjust_forex(FakeRef("00700", 11, 1.0, kind="hk"), 1.0055, 1.1), 1.0055)

    def test_us_uses_usd(self):
        self.assertEqual(ref_adjust_forex(FakeRef("PDD", 12, 1.0), 1.0055, 1.1), 1.1)


class LoadHoldingsTest(unittest.TestCase):
    """构造时的装载流程（``holdingsref.php:50-107``）。"""

    def _built(self, holdings, history=None, pairs=None, netvalue=None):
        connection = FakeConnection(
            holdings=holdings,
            holdingsdate=[{"id": 1, "date": HOLDINGS_DATE}],
            history=history or [],
            pairs=pairs,
        )
        #: ``netvalue or {...}`` 会在传 ``{}`` 时**静默取默认值** —— 这个测试
        #: 正要靠"净值缺失"那条路，所以必须显式判 ``None``。
        source = FakeSource(
            connection=connection,
            netvalue={(1, HOLDINGS_DATE): 1.0115} if netvalue is None else netvalue,
        )
        return HoldingsReference("SH513050", source=source), connection

    def test_reads_holdings_date_and_net_value(self):
        ref, _ = self._built([{"id": 1, "stock_id": 1, "holding_id": 11, "ratio": 30.68}])
        self.assertEqual(ref.get_holdings_date(), HOLDINGS_DATE)
        self.assertEqual(ref.get_net_value_string(), 1.0115)

    def test_without_holdings_date_nothing_is_loaded(self):
        """没有持仓日 → 退化成一只普通股票引用（持仓列表为空）。

        分支判据只看 ``holdings`` 表有没有行，不看这里成不成功，所以这种
        "半残"对象不会被拦住 —— 页面上的症状是"有持仓段落但没有估值数字"。
        """
        connection = FakeConnection(holdings=[{"id": 1, "stock_id": 1, "holding_id": 11, "ratio": 30.68}])
        source = FakeSource(connection=connection)
        ref = HoldingsReference("SH513050", source=source)
        self.assertIsNone(ref.get_holdings_date())
        self.assertEqual(ref.count_holdings(), 0)

    def test_missing_net_value_on_holdings_date(self):
        """持仓日没有净值 → PHP 只打 ``DebugString``，本版记在 ``missing_net_value`` 上。"""
        ref, _ = self._built([{"id": 1, "stock_id": 1, "holding_id": 11, "ratio": 30.68}],
                             netvalue={})
        self.assertEqual(ref.get_holdings_date(), HOLDINGS_DATE)
        self.assertTrue(getattr(ref, "missing_net_value", False))
        self.assertIsNone(ref.get_net_value_string())

    def test_ratio_is_split_by_market(self):
        """A 股 / 港股 / 美股三档分别累加（``:77-94``）。"""
        ref, _ = self._built([
            {"id": 1, "stock_id": 1, "holding_id": 13, "ratio": 5.0},    # A 股
            {"id": 2, "stock_id": 1, "holding_id": 11, "ratio": 30.68},  # 港股
            {"id": 3, "stock_id": 1, "holding_id": 12, "ratio": 7.31},   # 美股
        ])
        self.assertAlmostEqual(ref.ratio_cn, 5.0)
        self.assertAlmostEqual(ref.ratio_hk, 30.68)
        self.assertAlmostEqual(ref.ratio_us, 7.31)

    def test_ratio_display(self):
        """``GetHoldingsRatioDisplay()``（``:211``）：整数百分比 + rtrim。"""
        ref, _ = self._built([
            {"id": 1, "stock_id": 1, "holding_id": 11, "ratio": 30.68},
            {"id": 2, "stock_id": 1, "holding_id": 12, "ratio": 7.31},
        ])
        self.assertEqual(ref.get_holdings_ratio_display(), "港股31% 美股7%")

    def test_holdings_are_loaded_in_ratio_order(self):
        """``BuildOrderBy()`` 是 ``ratio DESC``，装载顺序跟着比例走。"""
        ref, _ = self._built([
            {"id": 1, "stock_id": 1, "holding_id": 13, "ratio": 5.0},
            {"id": 2, "stock_id": 1, "holding_id": 11, "ratio": 30.68},
            {"id": 3, "stock_id": 1, "holding_id": 12, "ratio": 7.31},
        ])
        self.assertEqual(ref.count_holdings(), 3)
        self.assertEqual([r.get_symbol() for r in ref.get_holdings_ref_array()],
                         ["00700", "PDD", "SH600519"])

    def test_holding_that_is_also_a_pair_fund_goes_realtime(self):
        """成分股自己也是配对基金 → 用 ``FundPairReference`` 并记进 realtime 列表。"""
        ref, _ = self._built(
            [{"id": 1, "stock_id": 1, "holding_id": 11, "ratio": 30.68}],
            pairs={"00700": "00700.hk"},
        )
        self.assertTrue(ref.use_realtime_est())


class PickEstDateTest(unittest.TestCase):
    """``pick_est_date()``（``:97-101``）：官方 EST 用哪个市场的日期。

    单独测是因为构造期要真连库建出成分股引用才拿得到日期，而这条选择逻辑
    本身不需要那些。
    """

    def test_us_missing_takes_hk(self):
        self.assertEqual(pick_est_date("2026-10-13", None), "2026-10-13")

    def test_hk_missing_takes_us(self):
        self.assertEqual(pick_est_date(None, "2026-10-14"), "2026-10-14")

    def test_hk_earlier_takes_hk(self):
        self.assertEqual(pick_est_date("2026-10-13", "2026-10-14"), "2026-10-13")

    def test_us_earlier_keeps_us(self):
        """美股日期更早时**不动** —— PHP 只在"港股更早 / 相等 / 美股缺失"时才换。"""
        self.assertEqual(pick_est_date("2026-10-14", "2026-10-13"), "2026-10-13")

    def test_equal_takes_hk(self):
        self.assertEqual(pick_est_date("2026-10-13", "2026-10-13"), "2026-10-13")

    def test_both_missing(self):
        self.assertIsNone(pick_est_date(None, None))


class RatioOfTest(unittest.TestCase):
    """``ratio_of()``：``holdings_ratio`` 的键与 ``get_stock_id()`` 类型不一致。

    **这是真实数据把我绊倒的那一个**（CK4-D 实测）：``$arHoldingsRatio`` 的键
    来自 ``SqlGetHoldingsArray()``，是 **int**；``$ref->GetStockId()`` 给的是
    **str**。PHP 里 ``$a['142']`` 和 ``$a[142]`` 是同一个键（数字字符串数组键
    自动转 int），永远命中；Python 的 dict 不认这层转换，比例**全取成 0**，
    于是 ``Σ比例 = 0`` → ``0 - 1 = -1`` → 估值整列变成 **0**，而且不报错。

    页面表现是"官方 EST / 参考 EST 全是 0，其它列都正常" —— 见到这个先查这里。
    """

    def test_int_key_with_str_stock_id(self):
        """真实形状：ratio 用 int 键，``get_stock_id()`` 返回 str。"""
        self.assertAlmostEqual(ratio_of({142: 30.68}, "142"), 30.68, places=9)

    def test_str_key_with_int_stock_id(self):
        """反过来也一样要命中。"""
        self.assertAlmostEqual(ratio_of({"142": 30.68}, 142), 30.68, places=9)

    def test_same_type_still_works(self):
        self.assertAlmostEqual(ratio_of({142: 30.68}, 142), 30.68, places=9)
        self.assertAlmostEqual(ratio_of({"PDD": 7.31}, "PDD"), 7.31, places=9)

    def test_missing_is_zero(self):
        self.assertEqual(ratio_of({142: 30.68}, 999), 0.0)
        self.assertEqual(ratio_of({142: 30.68}, "abc"), 0.0)


class EstNetValueTest(unittest.TestCase):
    """``_estNetValue()``（``:287``）的核心公式。"""

    def _prime(self, refs, ratios, net_value="1.0115", history=None):
        ref, _source, _connection = _ref(history=history)
        ref.net_value = net_value
        ref.holdings_date = HOLDINGS_DATE
        ref.holdings_refs = refs
        ref.holdings_ratio = ratios
        ref._holdings_date_history = {}
        return ref

    def test_weighted_formula(self):
        """比例之和 = 37.99（不是 100），用它钉住"除以 Σ比例"这一步。"""
        ref = self._prime(
            [
                FakeRef("00700", 11, "418", kind="hk"),
                FakeRef("PDD", 12, "81.45"),
            ],
            {11: 30.68, 12: 7.31},
            history=[
                {"id": 1, "stock_id": 11, "date": HOLDINGS_DATE,
                 "close": "425.2", "adjclose": "425.2", "volume": "0"},
                {"id": 2, "stock_id": 12, "date": HOLDINGS_DATE,
                 "close": "81.96", "adjclose": "81.96", "volume": "0"},
            ],
        )
        #: 汇率引用不存在 → 两个 adjust 都是 1.0
        total = 30.68 * (418.0 / 425.2) + 7.31 * (81.45 / 81.96)
        expected = 1.0115 * (1.0 + (total / 37.99 - 1.0))
        self.assertAlmostEqual(ref._est_net_value(), expected, places=9)
        #: 顺带钉住"不是比例×涨幅"的写法（两者差在归一化那一步）
        naive = 1.0115 * (1.0 + (total - 37.99))
        self.assertNotAlmostEqual(ref._est_net_value(), naive, places=3)

    def test_uses_adjclose_as_old_price(self):
        """旧价取 ``adjclose``（``:270``）；本例只给 adjclose，close 缺失也一样算。"""
        ref = self._prime(
            [FakeRef("00700", 11, "418", kind="hk")],
            {11: 30.68},
            history=[{"id": 1, "stock_id": 11, "date": HOLDINGS_DATE,
                      "close": "425.2", "adjclose": "400.0", "volume": "0"}],
        )
        #: 若误用 close（425.2）会得到另一个数
        with_adjclose = ref._est_net_value()
        expected = 1.0115 * (1.0 + (30.68 * (418.0 / 400.0) / 30.68 - 1.0))
        self.assertAlmostEqual(with_adjclose, expected, places=9)

    def test_holding_without_history_price_is_skipped(self):
        """成分股在持仓日没有价格 → 整项跳过（``:329`` 的 ``isset`` 判断）。

        **结果是 0 而不是原净值**：``Σ变化`` 为 0、``Σ比例`` 不为 0，
        于是 ``0 / Σ比例 - 1 = -1``，净值被乘成 0。这是 PHP 原样的行为
        （不是本迁移引入的），真实数据不会走到 —— 持仓页本身要显示"旧价格"，
        没有它页面早就是空的了。记在这里是为了排查时不至于误判成算法错。
        """
        ref = self._prime([FakeRef("00700", 11, "418", kind="hk")], {11: 30.68})
        self.assertAlmostEqual(ref._est_net_value(), 0.0, places=9)

    def test_fx_adjust_divides_each_holding(self):
        """汇率调整除在成分股那一项上（``:332``），不是最后整体除一次。

        ``SH513050`` 是 **A 股 ETF**，所以 ``date=None`` 时走
        ``_adjustToCNY(hkcny_ref, hkdcny_ref)`` 那一支（``:259``）——
        "官方中间价 vs 市场价"两个汇率之比，而不是同一个引用的两日比。
        这里把两样都给上，把这一支钉住。
        """
        ref = self._prime(
            [FakeRef("00700", 11, "418", kind="hk")],
            {11: 30.68},
            history=[{"id": 1, "stock_id": 11, "date": HOLDINGS_DATE,
                      "close": "425.2", "adjclose": "425.2", "volume": "0"}],
        )
        ref.hkcny_ref = FakeCny(0.9, closes={HOLDINGS_DATE: 0.91})
        ref.hkdcny_ref = FakeCny(0.89)
        adjust = 0.91 / 0.89
        expected = 1.0115 * (1.0 + ((30.68 * (418.0 / 425.2) / adjust) / 30.68 - 1.0))
        self.assertAlmostEqual(ref._est_net_value(), expected, places=9)

    def test_fx_adjust_uses_same_ref_for_non_etf(self):
        """非 ETF（或给了日期）时用**同一个**汇率引用的两日比（``:260`` / ``:254``）。

        给日期时 ``_adjustToCNY`` 的新值也取那天的收盘价，于是变成
        "持仓日汇率 / 指定日汇率"。
        """
        ref = self._prime(
            [FakeRef("00700", 11, "418", kind="hk")],
            {11: 30.68},
            history=[
                {"id": 1, "stock_id": 11, "date": HOLDINGS_DATE,
                 "close": "425.2", "adjclose": "425.2", "volume": "0"},
                {"id": 2, "stock_id": 11, "date": "2026-10-14",
                 "close": "420.0", "adjclose": "420.0", "volume": "0"},
            ],
        )
        ref.hkcny_ref = FakeCny(0.9, closes={HOLDINGS_DATE: 0.91, "2026-10-14": 0.905})
        adjust = 0.91 / 0.905
        expected = 1.0115 * (1.0 + ((30.68 * (420.0 / 425.2) / adjust) / 30.68 - 1.0))
        self.assertAlmostEqual(ref._est_net_value("2026-10-14"), expected, places=9)

    def test_realtime_uses_est_from_pair(self):
        """``realtime=True`` 时能对上配对的成分股用 ``EstFromPair()``（``:326``）。"""
        ref = self._prime(
            [FakeRef("00700", 11, "418", kind="hk", est_from_pair="500")],
            {11: 30.68},
            history=[{"id": 1, "stock_id": 11, "date": HOLDINGS_DATE,
                      "close": "425.2", "adjclose": "425.2", "volume": "0"}],
        )
        expected = 1.0115 * (1.0 + (30.68 * (500.0 / 425.2) / 30.68 - 1.0))
        self.assertAlmostEqual(ref._est_net_value(None, True), expected, places=9)

    def test_date_overrides_price_with_that_days_adjclose(self):
        """指定日期时用那天的 adjclose 代替当前价（算官方 EST 的路径）。"""
        ref = self._prime(
            [FakeRef("00700", 11, "418", kind="hk")],
            {11: 30.68},
            history=[
                {"id": 1, "stock_id": 11, "date": HOLDINGS_DATE,
                 "close": "425.2", "adjclose": "425.2", "volume": "0"},
                {"id": 2, "stock_id": 11, "date": "2026-10-14",
                 "close": "420.0", "adjclose": "420.0", "volume": "0"},
            ],
        )
        expected = 1.0115 * (1.0 + (30.68 * (420.0 / 425.2) / 30.68 - 1.0))
        self.assertAlmostEqual(ref._est_net_value("2026-10-14"), expected, places=9)

    def test_string_stock_id_still_finds_ratio(self):
        """``get_stock_id()`` 返回 str 而 ratio 用 int 键 —— 真实库就是这个形状。

        见 :class:`RatioOfTest`。**没有这条，页面估值整列归零还查不出原因**：
        单测里两边都是 int，一直命中，把坑盖住了。
        """
        ref = self._prime(
            [StrIdRef("00700", 11, "418", kind="hk")],
            {11: 30.68},
            history=[{"id": 1, "stock_id": 11, "date": HOLDINGS_DATE,
                      "close": "425.2", "adjclose": "425.2", "volume": "0"}],
        )
        expected = 1.0115 * (1.0 + (30.68 * (418.0 / 425.2) / 30.68 - 1.0))
        self.assertAlmostEqual(ref._est_net_value(), expected, places=9)
        #: 关键：不是 0
        self.assertNotAlmostEqual(ref._est_net_value(), 0.0, places=6)

    def test_zero_total_ratio_does_not_raise(self):
        """``Σ比例 <= MIN_FLOAT_VAL`` 时不归一化（``:337``）—— PHP 也不会除零。"""
        ref = self._prime([FakeRef("00700", 11, "418", kind="hk")], {11: 0.0})
        self.assertIsInstance(ref._est_net_value(), float)


class NetValueTest(unittest.TestCase):
    """三个净值与偏差补偿（``:378-438``）。"""

    def _prime(self, realtime=False, net_value="1.0115"):
        refs = [FakeRef("00700", 11, "418", kind="hk", est_from_pair="500")]
        history = [{"id": 1, "stock_id": 11, "date": HOLDINGS_DATE,
                    "close": "425.2", "adjclose": "425.2", "volume": "0"}]
        ref, source, connection = _ref(history=history)
        ref.net_value = net_value
        ref.holdings_date = HOLDINGS_DATE
        ref.holdings_refs = refs
        ref.holdings_ratio = {11: 30.68}
        ref._holdings_date_history = {}
        if realtime:
            ref.realtime_refs = refs
        return ref, source, connection

    def test_official_net_value_records_adjust(self):
        """``fOfficialEstAdjust`` = 当天真实净值 - 估算值（``:385-388``）。"""
        ref, source, _ = self._prime()
        source.netvalue[(1, "2026-10-14")] = 1.02
        ref.est_date = "2026-10-14"
        official = ref.get_official_net_value()
        self.assertAlmostEqual(ref.official_est_adjust, 1.02 - official, places=9)

    def test_fair_net_value_adds_adjust_back(self):
        """参考 EST = 当前价估的值 + 官方偏差（``:419-420``）。"""
        ref, source, _ = self._prime()
        source.netvalue[(1, "2026-10-14")] = 1.02
        ref.est_date = "2026-10-14"
        ref.get_official_net_value()
        fair = ref.get_fair_net_value()
        self.assertIsNotNone(fair)
        self.assertAlmostEqual(fair, ref._est_net_value() + ref.official_est_adjust, places=9)

    def test_realtime_net_value_needs_realtime_refs(self):
        """``UseRealtimeEst()`` 为假时实时 EST 是 ``None``（``:430``）。"""
        ref, _source, _ = self._prime(realtime=False)
        self.assertFalse(ref.use_realtime_est())
        self.assertIsNone(ref.get_realtime_net_value())

        ref2, _s2, _ = self._prime(realtime=True)
        self.assertTrue(ref2.use_realtime_est())
        self.assertIsNotNone(ref2.get_realtime_net_value())

    def test_auto_write_off_does_not_write(self):
        """默认不写 ``fundest``（与 PHP 的刻意偏差，PHP 无条件写）。"""
        ref, source, connection = self._prime()
        source.netvalue[(1, "2026-10-14")] = 1.02
        ref.est_date = "2026-10-14"
        ref.get_official_net_value()
        self.assertEqual(connection.written, [])


class StockGetHoldingsReferenceTest(unittest.TestCase):
    """``StockGetHoldingsReference()``（``_stock.php:133``）。"""

    def test_no_holdings_rows_returns_none(self):
        """判据只看 ``holdings`` 表有没有行 —— 没有就走后面的分支。"""
        source = FakeSource(connection=FakeConnection())
        self.assertIsNone(StockGetHoldingsReference("SH513050", source=source))

    def test_with_holdings_rows_returns_ref(self):
        connection = FakeConnection(
            holdings=[{"id": 1, "stock_id": 1, "holding_id": 11, "ratio": 30.68}],
            holdingsdate=[{"id": 1, "date": HOLDINGS_DATE}],
        )
        source = FakeSource(connection=connection,
                            netvalue={(1, HOLDINGS_DATE): 1.0115})
        ref = StockGetHoldingsReference("SH513050", source=source)
        self.assertIsInstance(ref, HoldingsReference)
        self.assertEqual(ref.count_holdings(), 1)

    def test_unknown_symbol_returns_none(self):
        source = FakeSource(connection=FakeConnection())
        self.assertIsNone(StockGetHoldingsReference("NOPE", source=source))


class ProportionArrayTest(unittest.TestCase):
    """``GetProportionArray()``（``:173``）。"""

    def _prime(self, history):
        ref, _source, _ = _ref(history=history)
        ref.holdings_refs = [FakeRef("00700", 11, "418", kind="hk")]
        ref.holdings_ratio = {11: 30.68}
        return ref

    def test_returns_none_when_a_day_is_missing(self):
        """任一成分股缺一天 → 整段放弃（PHP ``return false``）。"""
        ref = self._prime([{"id": 1, "stock_id": 11, "date": "2026-10-14",
                            "close": "420.0", "adjclose": "420.0", "volume": "0"}])
        self.assertIsNone(ref.get_proportion_array("2026-10-14", "2026-10-13"))

    def test_computes_ratio_between_two_days(self):
        ref = self._prime([
            {"id": 1, "stock_id": 11, "date": "2026-10-13", "close": "425.2",
             "adjclose": "425.2", "volume": "0"},
            {"id": 2, "stock_id": 11, "date": "2026-10-14", "close": "420.0",
             "adjclose": "420.0", "volume": "0"},
        ])
        array = ref.get_proportion_array("2026-10-14", "2026-10-13")
        self.assertIsNotNone(array)
        self.assertAlmostEqual(array[0], 420.0 / 425.2, places=9)


if __name__ == "__main__":
    unittest.main()
