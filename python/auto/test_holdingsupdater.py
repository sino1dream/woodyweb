"""``updater/holdingsupdater.py`` 与 ``updater/etfholdings.py`` 的单测。

覆盖两条写入路径的**编排层**：

* ``_updateStockHoldings``（``woody/res/php/_qdiimix.php:46``）的四道闸门；
* ``UpdateStockOptionHoldings`` / ``StockOptionDecodeHoldings``
  （``woody/res/php/_updateholdings.php``）的写序与解码口径；
* 深市 PCF 的一次端到端解析（合成样本，不走网络）。

PHP 逐例对照在 ``_scratch/_php_etfholdings_compare.py``（真实样本）；
这里只测**编排与边界**，解析细节以那边的结论为准。

假连接是一个 SQL 子集解释器（:class:`FakeConnection`）—— 持仓链路要打
五张表，逐个方法分派的假库会随实现演进而失效，按 ``WHERE`` / ``ORDER BY``
解释则与真实 SQL 形状对齐。
"""

from __future__ import annotations

import re
import unittest

from updater.etfholdings import SzseHoldingsFile
from updater.holdingsupdater import (
    debug_decode,
    process_admin_holdings,
    stock_option_decode_holdings,
    update_stock_option_holdings,
    update_stock_holdings,
)


# ---------------------------------------------------------------------------
# 假连接
# ---------------------------------------------------------------------------
#: 条件列：``DailyCloseSql`` 的 SQL 里 ``stock_id = %s`` **不带反引号**
#: （``date`` 带），所以反引号是可选的。
_WHERE_COL = re.compile(r"`?(\w+)`?\s*=\s*%s")
_ORDER_BY = re.compile(r"ORDER BY\s+(.*?)(?:\s+LIMIT\s+(\d+))?$", re.I)
_LIMIT = re.compile(r"\s+LIMIT\s+(\d+)\s*$", re.I)


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self._rows = []
        self.lastrowid = 0
        self.rowcount = 0

    def execute(self, sql, params=()):
        self._rows = self.connection.dispatch(sql, tuple(params))
        self.lastrowid = len(self._rows) + 1
        self.rowcount = len(self._rows)
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
    """支持持仓链路用到的 SQL 子集。

    认识四类语句：``SELECT``（``WHERE col = %s`` 若干、``ORDER BY col DESC``
    若干、``LIMIT n``）、``INSERT INTO``（按列清单拼行）、
    ``UPDATE ... SET col = %s WHERE ...``、``DELETE FROM``。
    """

    def __init__(self, **tables):
        self.queries = []
        self.commits = 0
        self.next_id = 100
        self.tables = dict((name, [dict(r) for r in rows])
                           for name, rows in tables.items())

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rows(self, table):
        return self.tables.setdefault(table, [])

    def dispatch(self, sql, params):
        self.queries.append(sql)
        statement = " ".join(sql.split())
        upper = statement.upper()

        table_match = re.search(r"(?:FROM|INTO|UPDATE|TABLE)\s+`(\w+)`", statement, re.I)
        table = table_match.group(1) if table_match else None

        if upper.startswith("INSERT"):
            return self._insert(statement, table, params)
        if upper.startswith("DELETE"):
            return self._delete(table, params)
        if upper.startswith("UPDATE"):
            return self._update(statement, table, params)
        return self._select(table, statement, params)

    # -- 各类语句 -----------------------------------------------------------
    def _select(self, table, statement, params):
        rows = self.rows(table)
        where_part = statement.split(" WHERE ", 1)[1] if " WHERE " in statement else ""
        order_part = ""
        limit = None

        order_match = _ORDER_BY.search(where_part)
        if order_match:
            order_part = order_match.group(1)
            if order_match.group(2):
                limit = int(order_match.group(2))
            where_part = where_part[:order_match.start()].strip()
        else:
            limit_match = _LIMIT.search(where_part)
            if limit_match:
                limit = int(limit_match.group(1))
                where_part = where_part[:limit_match.start()].strip()

        columns = _WHERE_COL.findall(where_part)
        values = list(params[:len(columns)])
        rows = [r for r in rows
                if all(str(r.get(col)) == str(val) for col, val in zip(columns, values))]

        for spec in reversed([s.strip() for s in order_part.split(",") if s.strip()]):
            match = re.match(r"`?(\w+)`?\s+(ASC|DESC)", spec, re.I)
            if match:
                column, direction = match.group(1), match.group(2).upper()
                rows.sort(key=lambda r: str(r.get(column)), reverse=(direction == "DESC"))
        if limit is not None:
            rows = rows[:limit]
        return [dict(r) for r in rows]

    def _insert(self, statement, table, params):
        column_match = re.search(r"\(([^)]*)\)", statement)
        columns = [c.strip().strip("`") for c in column_match.group(1).split(",")]
        row = dict(zip(columns, params))
        if "id" not in row or row.get("id") is None:
            row["id"] = self.next_id
            self.next_id += 1
        rows = self.rows(table)
        #: ``ON DUPLICATE KEY UPDATE``（``WriteDate`` 的 upsert 语义）：
        #: 主键已存在就地改，不追加第二行。
        if "ON DUPLICATE KEY UPDATE" in statement.upper():
            key = "id" if "id" in row else None
            for existing in rows:
                if key and str(existing.get(key)) == str(row.get(key)):
                    existing.update(row)
                    return []
        rows.append(row)
        return []

    def _update(self, statement, table, params):
        set_part = statement.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
        set_columns = [c.strip().strip("`") for c in set_part.split(",")]
        where_part = statement.split(" WHERE ", 1)[1] if " WHERE " in statement else ""
        where_columns = _WHERE_COL.findall(where_part)
        set_count = len(set_columns)
        set_values = list(params[:set_count])
        where_values = list(params[set_count:set_count + len(where_columns)])
        for row in self.rows(table):
            if all(str(row.get(col)) == str(val) for col, val in zip(where_columns, where_values)):
                for column, value in zip(set_columns, set_values):
                    row[column] = value
        return []

    def _delete(self, table, params):
        rows = self.rows(table)
        column = params and "stock_id"
        kept = [r for r in rows if not (params and str(r.get(column)) == str(params[0]))]
        self.tables[table] = kept
        return []


class FakeSource:
    def __init__(self, connection):
        self.connection = connection


class FakeStockSql:
    """只有 ``get_id`` 的假 stock 表（解码路径只需要它）。"""

    def __init__(self, ids=None):
        self.ids = dict(ids or {})

    def get_id(self, symbol):
        return self.ids.get(symbol)


class FakeRef:
    """``HoldingsReference`` 的形状替身（只留编排层要调的方法）。"""

    def __init__(self, symbol="SZ164701", stock_id="9003", official_date=None,
                 check_holdings_date=True, quote_date="2026-10-23", hour_minute=1000,
                 shanghai_etf=False, shenzhen_etf=False, source=None):
        self._symbol = symbol
        self._stock_id = stock_id
        self._official_date = official_date
        self._check = check_holdings_date
        self._quote_date = quote_date
        self._hour_minute = hour_minute
        self._shanghai = shanghai_etf
        self._shenzhen = shenzhen_etf
        self.source = source

    def get_symbol(self):
        return self._symbol

    def get_stock_id(self):
        return self._stock_id

    def get_official_date(self):
        return self._official_date

    def check_holdings_date(self, date):
        return self._check

    def get_date(self):
        return self._quote_date

    def get_hour_minute(self):
        return self._hour_minute

    def is_shanghai_etf(self):
        return self._shanghai

    def is_shenzhen_etf(self):
        return self._shenzhen


def _netvalue_rows(stock_id, date, close=1.5):
    return [{"id": 1, "stock_id": stock_id, "date": date, "close": close}]


def _make_source(netvalue_date=None, holdings_date=None, fundest_date=None):
    """按"净值日 / 持仓日 / est 日"三件套造假连接。"""
    tables = {
        "holdingsdate": ([{"id": "9003", "date": holdings_date}] if holdings_date else []),
        "fundest": ([{"id": 1, "stock_id": "9003", "date": fundest_date,
                      "time": "09:30:00", "close": 1.4}] if fundest_date else []),
    }
    if netvalue_date is not None:
        tables["netvaluehistory"] = _netvalue_rows("9003", netvalue_date)
    return FakeSource(FakeConnection(**tables))


# ---------------------------------------------------------------------------
# StockOptionDecodeHoldings
# ---------------------------------------------------------------------------
class StockOptionDecodeHoldingsTest(unittest.TestCase):
    """对应 PHP ``StockOptionDecodeHoldings``（``_updateholdings.php:3``）。"""

    def _decode(self, text):
        return stock_option_decode_holdings(text, stock_sql=FakeStockSql({
            "GLD": "2001", "SPY": "2002",
        }))

    def test_normal_pair_normalized(self):
        """比例被**归一化成合计 100**：``fPos = 1.5``，两条比例变成 66.67/33.33。

        这是 PHP 第 21-25 行的口径（``$fRatio / $fPos``，``$fPos`` 已除过
        100），不是把原值落库 —— 少了这一步，仓位 150% 的基金明细会合计 150。
        """
        position, ratios = self._decode('"GLD":100,"SPY":50')
        self.assertEqual(position, 1.5)
        self.assertEqual(ratios, {"2001": 100.0 / 1.5, "2002": 50.0 / 1.5})

    def test_hundred_stays_untouched(self):
        """合计正好 100 时归一化是恒等变换。"""
        position, ratios = self._decode('"GLD":50,"SPY":50')
        self.assertEqual(position, 1.0)
        self.assertEqual(ratios, {"2001": 50.0, "2002": 50.0})

    def test_zero_ratio_skipped(self):
        """``$strRatio != '0'``：PHP 8 按数值比，``'0'`` 跳过。"""
        position, ratios = self._decode('"GLD":100,"SPY":0')
        self.assertEqual(position, 1.0)
        self.assertEqual(ratios, {"2001": 100.0})

    def test_zero_dot_zero_skipped(self):
        """``'0.0'`` 也是数值零 —— 数字串比较的坑（pitfalls 记过）。"""
        position, ratios = self._decode('"GLD":100,"SPY":0.0')
        self.assertEqual(position, 1.0)
        self.assertEqual(ratios, {"2001": 100.0})

    def test_empty_string_ratio_kept_as_zero(self):
        """空串 **不**被跳过：PHP 8 里 ``'' != '0'`` 按字符串比为真，
        ``floatval('')`` 又是 0，于是落一条比例为 0 的行、且不计入合计。
        本版照抄这个形状（写不写由调用方决定，解码层不挑）。"""
        position, ratios = self._decode('"GLD":100,"SPY":""')
        self.assertEqual(position, 1.0)
        self.assertEqual(ratios, {"2001": 100.0, "2002": 0.0})

    def test_unknown_symbol_counted_in_position_but_not_written(self):
        """库里没有的符号：**不写明细行**，但比例照算进 ``fPos``。

        PHP 那边 ``$fPos += $fRatio`` 在查 id 之后，与 id 取没取到无关；
        只有 ``$arRatio[false]``（键转 0）那一行是本版不写的垃圾行。
        """
        position, ratios = self._decode('"GLD":100,"NOSUCH":50')
        self.assertEqual(position, 1.5)
        self.assertEqual(ratios, {"2001": 100.0 / 1.5})

    def test_all_unknown_writes_nothing(self):
        """一条有效比例都没有：PHP 返回 0.0（调用方当假值不写库），
        本版 ``(None, {})`` —— 写库行为一致，只是形制不同。

        这一条同时防住"把库里真实持仓清空"：全打错的表单不能变成写操作。
        """
        position, ratios = self._decode('"NOSUCH":50,"ALSOBAD":50')
        self.assertIsNone(position)
        self.assertEqual(ratios, {})

    def test_bad_json_returns_none(self):
        """``json_decode`` 失败：PHP 返回 null，本版 ``(None, {})``。"""
        position, ratios = self._decode("not-json")
        self.assertIsNone(position)
        self.assertEqual(ratios, {})

    def test_empty_input(self):
        """空串解出 ``{}``，PHP 的 ``if ($ar = ...)`` 同样当假值。"""
        position, ratios = self._decode("")
        self.assertIsNone(position)
        self.assertEqual(ratios, {})

    def test_debug_decode_wraps_braces(self):
        """入参是**去掉花括号的 JSON 对象体**。"""
        self.assertEqual(debug_decode('"GLD":100'), {"GLD": 100})
        self.assertIsNone(debug_decode("oops"))
        self.assertIsNone(debug_decode(""))     # {} 在 PHP 里是假值


# ---------------------------------------------------------------------------
# UpdateStockOptionHoldings（写序）
# ---------------------------------------------------------------------------
class UpdateStockOptionHoldingsTest(unittest.TestCase):
    def _sqls(self):
        connection = FakeConnection()
        order = []

        class RecordingPosition:
            def write_pos(self, stock_id, position):
                order.append(("pos", stock_id, position))
                return True

        class RecordingDate:
            def write_date(self, stock_id, date):
                order.append(("date", stock_id, date))
                return True

        class RecordingHoldings:
            def delete_all(self, stock_id):
                order.append(("delete", stock_id))
                return 0

            def insert(self, stock_id, holding_id, ratio):
                order.append(("insert", stock_id, holding_id, ratio))
                return 1

        return (FakeSource(connection), order,
                RecordingPosition(), RecordingDate(), RecordingHoldings())

    def test_write_order_pos_then_date_then_holdings(self):
        """顺序照抄 PHP：**仓位 → 持仓日 → 整份替换明细**。

        中途失败时留下"新仓位 + 旧明细"比反过来更容易被下一次更新纠正
        （仓位影响估值，明细影响测算表）。
        """
        source, order, position_sql, date_sql, holdings_sql = self._sqls()
        updated = update_stock_option_holdings(
            source, "9003", "2026-10-22", '"GLD":100',
            position_sql=position_sql, date_sql=date_sql, holdings_sql=holdings_sql,
            stock_sql=FakeStockSql({"GLD": "2001"}),
        )
        self.assertTrue(updated)
        self.assertEqual([step[0] for step in order],
                         ["pos", "date", "delete", "insert"])
        self.assertEqual(order[0], ("pos", "9003", 1.0))
        self.assertEqual(order[1], ("date", "9003", "2026-10-22"))
        # 比例以**字符串**落库（PHP ``strval($fRatio)``），让 MySQL 自己截断
        self.assertEqual(order[3], ("insert", "9003", "2001", "100"))

    def test_bad_payload_writes_nothing(self):
        source, order, position_sql, date_sql, holdings_sql = self._sqls()
        updated = update_stock_option_holdings(
            source, "9003", "2026-10-22", "not-json",
            position_sql=position_sql, date_sql=date_sql, holdings_sql=holdings_sql,
        )
        self.assertFalse(updated)
        self.assertEqual(order, [])


# ---------------------------------------------------------------------------
# _updateStockHoldings 的四道闸门
# ---------------------------------------------------------------------------
class UpdateStockHoldingsGateTest(unittest.TestCase):
    """对应 PHP ``_QdiiMixAccount::_updateStockHoldings``（``_qdiimix.php:46``）。"""

    def _run(self, ref, fetch=None, dry_run=False, trace=None):
        return update_stock_holdings(ref, fetch=fetch, source=ref.source,
                                     dry_run=dry_run, trace=trace)

    @staticmethod
    def _date_of(connection):
        rows = connection.rows("holdingsdate")
        return rows[0]["date"] if rows else None

    # -- 闸门 1 / 2 ---------------------------------------------------------
    def test_gate1_netvalue_date_equals_holdings_date(self):
        """净值日 == 持仓日：已经最新，什么都不写。"""
        source = _make_source(netvalue_date="2026-10-22", holdings_date="2026-10-22")
        ref = FakeRef(symbol="SZ164701", source=source)
        trace = []
        self.assertFalse(self._run(ref, trace=trace))
        self.assertEqual(self._date_of(source.connection), "2026-10-22")
        self.assertIn("已最新", trace[0])

    def test_gate2_holdings_date_equals_official_date(self):
        """持仓日 == 官方 EST 日期：官方估值已用新净值，再补是同一天重复写。

        注意 PHP 第 56 行**没有假值守卫** —— 持仓日与官方日期都取不到时
        （都是 ``false``）同样会命中这道闸门提前返回。本版照抄。
        """
        source = _make_source(netvalue_date="2026-10-23", holdings_date="2026-10-22")
        ref = FakeRef(symbol="SZ164701", official_date="2026-10-22", source=source)
        trace = []
        self.assertFalse(self._run(ref, trace=trace))
        self.assertEqual(self._date_of(source.connection), "2026-10-22")
        self.assertIn("官方", trace[0])

    def test_no_holdings_date_yet_proceeds(self):
        """持仓日为空、官方日期也不是空：前两道闸门都不拦。

        （官方日期为空时 PHP 会因两侧都是 false 提前返回，见上一条测试；
        真实流里这种基金的 holdings 表也是空的，根本建不起账。）
        """
        source = _make_source(netvalue_date="2026-10-23", holdings_date=None,
                              fundest_date="2026-10-21")
        ref = FakeRef(symbol="SH513050", official_date="2026-10-20", source=source,
                      shanghai_etf=True)
        trace = []
        self.assertFalse(self._run(ref, dry_run=True, trace=trace))
        self.assertTrue(any("沪市" in line for line in trace))

    # -- ETF 支线的 est 日期闸门 --------------------------------------------
    def test_etf_est_date_equals_netvalue_date(self):
        """fundest 还没到新净值日，不取（PHP: ``$strEstDate == $strNetValueDate``）。"""
        source = _make_source(netvalue_date="2026-10-23", holdings_date="2026-10-21",
                              fundest_date="2026-10-23")
        ref = FakeRef(symbol="SH513050", source=source, shanghai_etf=True)
        trace = []
        self.assertFalse(self._run(ref, trace=trace))
        self.assertIn("fundest 还没到", trace[0])

    def test_etf_a_day_too_early(self):
        """est 日 == 行情日：港股/A 股还没收工，清单不是最终版。"""
        source = _make_source(netvalue_date="2026-10-23", holdings_date="2026-10-21",
                              fundest_date="2026-10-22")
        ref = FakeRef(symbol="SH513050", source=source, shanghai_etf=True,
                      quote_date="2026-10-22")
        trace = []
        self.assertFalse(self._run(ref, trace=trace))
        self.assertIn("a day too early", trace[0])

    def test_hk_mix_exempt_from_too_early(self):
        """港股混合族（``in_arrayHkMix``）豁免"早一天"闸门 —— 港股 16:00 收市，
        当晚清单就是最终版，QDII 净值反而要等美股。"""
        source = _make_source(netvalue_date="2026-10-23", holdings_date="2026-10-21",
                              fundest_date="2026-10-22")
        ref = FakeRef(symbol="SH513090", source=source, shanghai_etf=True,
                      quote_date="2026-10-22")
        trace = []
        self.assertFalse(self._run(ref, dry_run=True, trace=trace))
        self.assertTrue(any("沪市" in line for line in trace))

    # -- ETF 支线的时间窗 ----------------------------------------------------
    def test_before_930_not_updated(self):
        source = _make_source(netvalue_date="2026-10-23", holdings_date="2026-10-21",
                              fundest_date="2026-10-22")
        ref = FakeRef(symbol="SH513050", source=source, shanghai_etf=True,
                      quote_date="2026-10-21", hour_minute=929)
        trace = []
        self.assertFalse(self._run(ref, trace=trace))
        self.assertIn("9:30", trace[0])

    def test_after_1455_stop(self):
        source = _make_source(netvalue_date="2026-10-23", holdings_date="2026-10-21",
                              fundest_date="2026-10-22")
        ref = FakeRef(symbol="SH513050", source=source, shanghai_etf=True,
                      quote_date="2026-10-21", hour_minute=1456)
        trace = []
        self.assertFalse(self._run(ref, trace=trace))
        self.assertIn("14:55", trace[0])

    def test_time_window_boundaries_inclusive(self):
        """930 与 1455 是**闭区间**端点（PHP 是 ``<`` / ``>``）。"""
        for hour_minute in (930, 1455):
            source = _make_source(netvalue_date="2026-10-23", holdings_date="2026-10-21",
                                  fundest_date="2026-10-22")
            ref = FakeRef(symbol="SH513050", source=source, shanghai_etf=True,
                          quote_date="2026-10-21", hour_minute=hour_minute)
            trace = []
            self.assertFalse(self._run(ref, dry_run=True, trace=trace))
            self.assertTrue(any("沪市" in line for line in trace),
                            "%d 应该放行" % hour_minute)

    # -- ETF 支线的交易所分派 ------------------------------------------------
    def test_sse_branch_calls_fetch(self):
        """沪市支线：下载 XML 整份替换。"""
        source = _make_source(netvalue_date="2026-10-23", holdings_date="2026-10-21",
                              fundest_date="2026-10-22")
        ref = FakeRef(symbol="SH513050", source=source, shanghai_etf=True,
                      quote_date="2026-10-21")
        fetched = []

        def fetch(url):
            fetched.append(url)
            return b"<xml/>"

        # 下载回来不是真 XML（解析不了），但**调用了下载**这件事本身要断言
        self.assertFalse(self._run(ref, fetch=fetch))
        self.assertEqual(len(fetched), 1)
        self.assertIn("513050", fetched[0])

    def test_szse_branch_calls_fetch_with_quote_date(self):
        """深市支线：按行情日拼文件名。"""
        source = _make_source(netvalue_date="2026-10-23", holdings_date="2026-10-21",
                              fundest_date="2026-10-22")
        ref = FakeRef(symbol="SZ159605", source=source, shenzhen_etf=True,
                      quote_date="2026-10-21")
        fetched = []

        def fetch(url):
            fetched.append(url)
            return b""

        self.assertFalse(self._run(ref, fetch=fetch))
        self.assertEqual(len(fetched), 1)
        self.assertIn("159605", fetched[0])
        self.assertIn("20261021", fetched[0])

    def test_neither_exchange_reports_and_skips(self):
        source = _make_source(netvalue_date="2026-10-23", holdings_date="2026-10-21",
                              fundest_date="2026-10-22")
        ref = FakeRef(symbol="SZ159605", source=source, quote_date="2026-10-21")
        trace = []
        self.assertFalse(self._run(ref, trace=trace))
        self.assertIn("无处下载", trace[-1])

    # -- LOF 支线 -----------------------------------------------------------
    def test_lof_advances_date_when_all_priced(self):
        """LOF 族（交易所不公布申赎清单）：成分股在净值日都有收盘价才推进日期。"""
        source = _make_source(netvalue_date="2026-10-23", holdings_date="2026-10-22")
        ref = FakeRef(symbol="SZ164701", source=source, check_holdings_date=True)
        self.assertTrue(self._run(ref))
        self.assertEqual(self._date_of(source.connection), "2026-10-23")

    def test_lof_keeps_date_when_missing_close(self):
        source = _make_source(netvalue_date="2026-10-23", holdings_date="2026-10-22")
        ref = FakeRef(symbol="SZ164701", source=source, check_holdings_date=False)
        self.assertFalse(self._run(ref))
        self.assertEqual(self._date_of(source.connection), "2026-10-22")

    def test_lof_dry_run_diagnoses_without_writing(self):
        source = _make_source(netvalue_date="2026-10-23", holdings_date="2026-10-22")
        ref = FakeRef(symbol="SZ164701", source=source, check_holdings_date=True)
        trace = []
        self.assertFalse(self._run(ref, dry_run=True, trace=trace))
        self.assertEqual(self._date_of(source.connection), "2026-10-22")
        self.assertTrue(any("推进持仓日期" in line for line in trace))

    # -- 其他 ---------------------------------------------------------------
    def test_missing_symbol_or_id(self):
        source = _make_source(netvalue_date="2026-10-23", holdings_date="2026-10-22")
        ref = FakeRef(symbol="", source=source)
        self.assertFalse(self._run(ref))
        ref = FakeRef(stock_id="", source=source)
        self.assertFalse(self._run(ref))

    def test_source_without_connection_raises(self):
        """写路径不允许"静默不写"——没有连接就抛。"""
        ref = FakeRef(symbol="SZ164701")
        with self.assertRaises(ValueError):
            self._run(ref)


# ---------------------------------------------------------------------------
# 深市 PCF 端到端（合成样本，不走网络）
# ---------------------------------------------------------------------------
#: 合成 PCF：只保留解析路径要命中的行 —— 日期行、两行现金、表头、
#: 一行"申赎现金"（被跳过）、一行港股、一行美股。
_SYNTHETIC_PCF = """\


                                                  合成ETF申购赎回清单
                                               ( 2026-10-22 )

 基本信息
-------------------------------------------------------------------------------------------------------------------------------------------------------
 2026-10-21日 信息内容

                                        现金差额：  820.72元
                      最小申购、赎回单位资产净值：  1100754.99元

-------------------------------------------------------------------------------------------------------------------------------------------------------
 证券代码                             证券简称                                股份数量    现金替代标志    申购现金替代保证金率     赎回现金替代保证金率     申购替代金额     赎回替代金额    挂牌市场  映射代码  是否实物对价申赎
 159900                               申赎现金                                      0         必须                0.00%                                  1,210,947.6600           0.0000     深圳市场
 1810                                 小米集团-W                                2,505         允许               10.00%                                          0.0000           0.0000     香港市场
 PDD                                  拼多多                                      118         允许               10.00%                                          0.0000           0.0000     其他市场
-------------------------------------------------------------------------------------------------------------------------------------------------------
""".encode("gb18030")


class SzseHoldingsParseTest(unittest.TestCase):
    def _run_parse(self):
        connection = FakeConnection(
            stock=[
                {"id": "501", "symbol": "USCNY", "name": "美元中间价"},
                {"id": "502", "symbol": "HKCNY", "name": "港元中间价"},
                {"id": "601", "symbol": "01810", "name": "小米集团-W"},
                {"id": "602", "symbol": "PDD", "name": "拼多多"},
            ],
            netvaluehistory=[
                {"id": 1, "stock_id": "501", "date": "2026-10-21", "close": 7.10},
                {"id": 2, "stock_id": "502", "date": "2026-10-21", "close": 0.91},
            ],
            stockhistory=[
                {"id": 1, "stock_id": "601", "date": "2026-10-21", "close": 55.10,
                 "adjclose": 55.10},
                {"id": 2, "stock_id": "602", "date": "2026-10-21", "close": 118.0,
                 "adjclose": 118.0},
            ],
        )
        source = FakeSource(connection)
        holdings_file = SzseHoldingsFile(source, "9003")
        ok = holdings_file.parse_text(_SYNTHETIC_PCF.decode("gb18030"))
        self.assertTrue(holdings_file.done())
        return source, holdings_file, ok

    def test_cash_lines(self):
        """``最小申购赎回单位资产净值 − 现金差额`` 是分母。"""
        _source, holdings_file, _ok = self._run_parse()
        self.assertAlmostEqual(holdings_file.f_cash, 1100754.99 - 820.72, places=6)

    def test_date_is_previous_trading_day(self):
        """日期取**第一张表**（T-1）的 ``X日 信息内容`` 行。"""
        _source, holdings_file, _ok = self._run_parse()
        self.assertEqual(holdings_file.str_date, "2026-10-21")

    def test_holdings_written_with_ratios(self):
        source, holdings_file, _ok = self._run_parse()
        rows = source.connection.rows("holdings")
        by_holding = dict((r["holding_id"], r) for r in rows)
        self.assertEqual(sorted(by_holding), ["601", "602"])

        # 港股：数量 × 复权价 × 港元汇率
        expected_hk = 100.0 * 2505 * 55.10 * 0.91 / (1100754.99 - 820.72)
        self.assertAlmostEqual(float(by_holding["601"]["ratio"]), expected_hk, places=9)
        # 美股：数量 × 复权价 × 美元汇率（``is_numeric`` 判据，PDD 不是数字串）
        expected_us = 100.0 * 118 * 118.0 * 7.10 / (1100754.99 - 820.72)
        self.assertAlmostEqual(float(by_holding["602"]["ratio"]), expected_us, places=9)

        # 比例以**字符串**落库（PHP ``strval``），让 MySQL 自己按列精度截断
        self.assertIsInstance(by_holding["601"]["ratio"], str)

    def test_substitution_cash_row_skipped(self):
        """``159900`` 申赎现金行的 ``股份数量`` 是 ``'0'``，被 ``array_filter``
        连 ``'0'`` 一起滤掉后列数不足 8，整行跳过。"""
        source, _holdings_file, _ok = self._run_parse()
        self.assertNotIn("159900", [r["holding_id"] for r in source.connection.rows("holdings")])

    def test_holdings_date_written(self):
        source, _holdings_file, _ok = self._run_parse()
        dates = source.connection.rows("holdingsdate")
        self.assertEqual([r["date"] for r in dates], ["2026-10-21"])

    def test_symbols_created_on_the_fly(self):
        """清单里的符号不在库中就地建行（``InsertSymbol``）。"""
        connection = FakeConnection(
            netvaluehistory=[
                {"id": 1, "stock_id": "501", "date": "2026-10-21", "close": 7.10},
                {"id": 2, "stock_id": "502", "date": "2026-10-21", "close": 0.91},
            ],
            stockhistory=[
                {"id": 1, "stock_id": "101", "date": "2026-10-21", "close": 55.10,
                 "adjclose": 55.10},
                {"id": 2, "stock_id": "102", "date": "2026-10-21", "close": 118.0,
                 "adjclose": 118.0},
            ],
        )
        source = FakeSource(connection)
        holdings_file = SzseHoldingsFile(source, "9003")
        holdings_file.parse_text(_SYNTHETIC_PCF.decode("gb18030"))
        self.assertTrue(holdings_file.done())
        symbols = sorted(r["symbol"] for r in connection.rows("stock"))
        self.assertIn("01810", symbols)
        self.assertIn("PDD", symbols)
        # 港股符号**补零到 5 位**（``ConvertHolding``）
        self.assertNotIn("1810", symbols)


# ---------------------------------------------------------------------------
# 管理员写入口的权限闸门（submitholdings.php 的 AdminRun）
# ---------------------------------------------------------------------------
class ProcessAdminHoldingsGateTest(unittest.TestCase):
    """``process_admin_holdings`` 的 ``admin=`` 参数（2026-10-26 补的闸门）。

    PHP 的 ``AdminRun()`` 是::

        if ($this->IsAdmin())  $this->AdminProcess();

    原来只迁了里面那半截，等于留着一个"谁调都能写"的函数。现在外圈那道
    判断折成一个**必填关键字参数**：给默认值就等于没补这个洞。
    """

    class _Bomb:
        """一碰就炸的数据源：证明 ``admin=False`` 时压根没走到取数。"""

        @property
        def connection(self):  # pragma: no cover - 真被访问就是测试失败
            raise AssertionError("admin=False 时不应该碰数据源")

    def test_denied_returns_without_touching_source(self):
        result = process_admin_holdings(
            self._Bomb(), "SZ164701", {"holdings": '"GLD":100'}, admin=False)
        self.assertTrue(result["denied"])
        self.assertFalse(result["updated"])
        self.assertIsNone(result["stock_id"])
        self.assertIsNone(result["position"])

    def test_admin_flag_is_required_keyword(self):
        """漏传 ``admin`` 直接 TypeError —— 不给"忘了判权限"的机会。"""
        with self.assertRaises(TypeError):
            process_admin_holdings(FakeSource(FakeConnection()), "SZ164701", {})

    def test_denied_flag_is_false_when_authorized(self):
        """授权但符号查不到时：``denied`` 必须是 False（否则调用方会误报越权）。"""
        result = process_admin_holdings(
            FakeSource(FakeConnection()), "", {}, admin=True)
        self.assertFalse(result["denied"])
        self.assertFalse(result["updated"])


if __name__ == "__main__":
    unittest.main()
