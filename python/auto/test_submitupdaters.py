"""三个"抓外部数据写库"落地模块的单测。

覆盖 ``updater/etfdividend.py``（``submitdividend.php``）、
``updater/etfnav.py``（``submitnetvalue.php``）、
``updater/historyupdate.py``（``submithistory.php``）的**解析与编排**。

## 三处刻意的"不测"

* **不发网络请求**：抓取函数一律注入（``fetch=`` / ``fetch_text=`` /
  ``fetch_bytes=``），所以下面用的都是**真实抓回来的样本形状**（见各
  ``SAMPLE_`` 常量的出处注释），不是编出来的。
* **不测 iShares**：那条路本版不支持（老式 .xls），测的是"会明确报不支持"。
* **不测 Proshares 的真实响应**：``www.proshares.com`` 现在被 Cloudflare
  拦（403），拿不到新样本，这里用的是 PHP docblock 里的真实响应
  （``woody/res/submitdividend.php:53-95``，TQQQ 2024 年四条）。

真实数据的端到端核对在 ``_scratch/_submit_pages_live.py``（要联网 + 真库），
这里的假库只保证"SQL 写到了该写的地方、闸门拦在了该拦的行"。
"""

from __future__ import annotations

import io
import re
import unittest
import zipfile
from typing import Any, Dict, List

from source import xlsxreader
from updater import etfnav, etfdividend, historyupdate

# ---------------------------------------------------------------------------
# 样本
# ---------------------------------------------------------------------------
#: 实测抓回来的 SSGA 分红响应（XOP，2026-10-30，80 条里取头 3 条 + 一条
#: 有真实资本利得数字的）。``exDate`` 是 **m/d/Y**。
SAMPLE_SPDR_DIVIDEND = """[
 {"exDate":"09/21/2026","recordDate":"09/21/2026","dividend":"0.757469",
  "payableDate":"09/23/2026","shortTeamCapital":"-","longTeamCapital":"-"},
 {"exDate":"06/22/2026","recordDate":"06/22/2026","dividend":"0.748259",
  "payableDate":"06/24/2026","shortTeamCapital":"-","longTeamCapital":"-"},
 {"exDate":"12/22/2025","longTeamCapital":"0.000000","recordDate":"12/22/2025",
  "dividend":"0.910371","payableDate":"12/24/2025","shortTeamCapital":"0.000000"},
 {"exDate":"03/23/2026","dividend":"-","recordDate":"03/23/2026",
  "payableDate":"03/25/2026"}
]"""

#: PHP docblock 里的真实 Proshares 响应（TQQQ 2024，``submitdividend.php:53``）。
#: ``ExDate`` 是 ISO **带时间**，PHP 用 ``substr(..., 0, 10)`` 切片。
SAMPLE_PROSHARES_DIVIDEND = """[
 {"CashDividendPerShare":0.275411,"Dividend":0.275411,
  "EffectiveDate":"2024-12-20T00:00:00","ExDate":"2024-12-23T00:00:00",
  "PayableDate":"2024-12-31T00:00:00","Symbol":"TQQQ"},
 {"CashDividendPerShare":0.230214,"Dividend":0.230214,
  "EffectiveDate":"2024-09-24T00:00:00","ExDate":"2024-09-25T00:00:00",
  "PayableDate":"2024-10-02T00:00:00","Symbol":"TQQQ"}
]"""

#: 实测抓回来的 Invesco QQQ 响应（2026-10-29，只留用得到的字段）。
SAMPLE_INVESCO_NAV = """{"effectiveDate":"2026-10-29","cusip":"46090E103",
 "currency":"USD","nav":715.178947,"marketValue":480886323695.28,
 "sharesOutstanding":672400000}"""

#: SPDR ``navhist`` 表的前 6 行（实测 SPY 文件的开头，含第 3 行整行空白）。
#: 最后两行是构造的：一行周末（2026-10-31 是周六）、一行早于 2014。
SPDR_NAV_ROWS = [
    ["Fund Name:", "State Street SPDR S&P 500 ETF Trust"],
    ["Ticker Symbol:", "SPY"],
    [],
    ["Date", "NAV", "Shares Outstanding", "Total Net Assets"],
    ["29-Oct-2026", 769.506335, 1057282116, 813585286253.05],
    ["28-Oct-2026", 771.855842, 1055532116, 814718629722.0],
    ["31-Oct-2026", 770.0, 1050000000, 800000000000.0],
    ["31-Dec-2013", 180.0, 900000000, 160000000000.0],
]


def build_xlsx(rows: List[List[Any]]) -> bytes:
    """按 Excel 的结构拼一个最小 xlsx（共享字符串 + 数字）。

    整行为空时**不写 ``<row>``**，让行号出现缺口 —— 真实的 SPDR 文件第 3 行
    就是空的，读取器必须按 ``r`` 属性占位才对得上（这是它最容易写错的地方）。
    """
    strings: List[str] = []
    for row in rows:
        for value in row:
            if isinstance(value, str) and value not in strings:
                strings.append(value)

    sheet_rows = []
    for index, row in enumerate(rows):
        if not row:
            continue
        cells = []
        for column, value in enumerate(row):
            if value is None:
                continue
            reference = "%s%d" % (_column_letter(column), index + 1)
            if isinstance(value, str):
                cells.append(
                    '<c r="%s" t="s"><v>%d</v></c>' % (reference, strings.index(value)))
            else:
                cells.append('<c r="%s"><v>%s</v></c>' % (reference, value))
        sheet_rows.append('<row r="%d">%s</row>' % (index + 1, "".join(cells)))

    shared = "".join("<si><t>%s</t></si>" % _escape(text) for text in strings)
    sheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>%s</sheetData></worksheet>" % "".join(sheet_rows)
    )
    shared_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">%s</sst>'
        % shared
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="navhist" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
        '2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", rels)
        archive.writestr("xl/sharedStrings.xml", shared_xml)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return buffer.getvalue()


def _column_letter(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# ---------------------------------------------------------------------------
# 假库：按 SQL 特征分派的迷你内存表
# ---------------------------------------------------------------------------
#: 只认 ````id = %s``（前面不是 ``stock_``）—— 见 :meth:`FakeConnection._filter`。
_ID_WHERE = re.compile(r"(?<![_\w])`?id`?\s*=\s*%s")


class FakeCursor:
    def __init__(self, connection: "FakeConnection"):
        self.connection = connection
        self.rows: List[Dict[str, Any]] = []
        self.rowcount = 0
        #: ``DateSql.write_date()`` 会读它（PHP 那边是 ``mysqli_insert_id``）
        self.lastrowid = 0

    def execute(self, sql, params=()):
        self.connection.queries.append((sql, tuple(params)))
        self.rows, self.rowcount = self.connection.dispatch(sql, tuple(params))

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class FakeConnection:
    """够这三个 updater 用的假库。

    行以 ``dict`` 存在 ``tables[表名]`` 里（``id`` 自增），写语句真的改内存，
    这样"写了没写 / 删了几行"都能断言，而不只是看 SQL 长什么样。
    """

    _SELECT = re.compile(r"FROM\s+`?(\w+)`?", re.I)
    _INSERT = re.compile(r"INSERT INTO\s+`?(\w+)`?\s*\(([^)]*)\)", re.I)
    _UPDATE = re.compile(r"UPDATE\s+`?(\w+)`?\s+SET\s+(.*?)\s+WHERE\s+id\s*=\s*%s", re.I)
    _DELETE = re.compile(r"DELETE FROM\s+`?(\w+)`?", re.I)

    def __init__(self, tables=None, watermark=None):
        self.tables: Dict[str, List[Dict[str, Any]]] = {
            "stockhistory": [],
            "netvaluehistory": [],
            "shareshistory": [],
            "stockdividend": [],
            "calibrationhistory": [],
            "stockhistorydate": [],
        }
        for name, rows in (tables or {}).items():
            self.tables[name] = [dict(row) for row in rows]
        for stock_id, date in (watermark or {}).items():
            self.tables["stockhistorydate"].append({"id": stock_id, "date": date})
        self.queries: List[tuple] = []
        self.commits = 0
        self._next_id = 1000

    # -- 连接接口 ---------------------------------------------------------
    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def by_table(self, name: str) -> List[Dict[str, Any]]:
        return self.tables[name]

    # -- 分发 -------------------------------------------------------------
    def dispatch(self, sql, params):
        text = sql.strip()
        if text.upper().startswith("SELECT"):
            return self._select(text, params), 0
        if text.upper().startswith("INSERT"):
            return [], self._insert(text, params)
        if text.upper().startswith("UPDATE"):
            return [], self._update(text, params)
        if text.upper().startswith("DELETE"):
            return [], self._delete(text, params)
        return [], 0

    def _select(self, sql, params):
        table = self._table(sql)
        rows = self.tables.get(table, [])
        if "COUNT(*)" in sql:
            #: ``_daily_status`` 用的聚合查询（web 层），这里只需近似
            matching = self._filter(rows, sql, params)
            latest = max((str(r["date"]) for r in matching), default=None)
            return [{"n": len(matching), "latest": latest}]
        if "`date` FROM" in sql and table == "stockhistorydate":
            stock_id = params[0]
            found = [r for r in rows if str(r.get("id")) == str(stock_id)]
            return [dict(found[0])] if found else []
        if "`date` FROM" in sql:
            matching = self._filter(rows, sql, params)
            if not matching:
                return []
            newest = max(matching, key=lambda r: str(r["date"]))
            return [{"date": newest["date"]}]

        matching = self._filter(rows, sql, params)
        if "`date` = %s" in sql:
            return matching[:1]
        if "`date` < %s" in sql:
            matching.sort(key=lambda r: str(r["date"]), reverse=True)
            return matching[:1]
        matching.sort(key=lambda r: str(r["date"]), reverse=True)
        if "LIMIT 1" in sql:
            return matching[:1]
        return matching

    def _filter(self, rows, sql, params):
        matching = list(rows)
        if "stock_id = %s" in sql:
            stock_id = params[0]
            matching = [r for r in matching if str(r.get("stock_id")) == str(stock_id)]
        #: 注意不能用 ``"id = %s" in sql`` —— ``stock_id = %s`` 里也含这三个字符，
        #: 那样会拿 stock_id 去比 ``id``，所有按日期取行的查询都查不到东西。
        if _ID_WHERE.search(sql):
            key = params[0]
            matching = [r for r in matching if str(r.get("id")) == str(key)]
        if "`date` = %s" in sql:
            index = 1 if "stock_id = %s" in sql else 0
            date = str(params[index])
            matching = [r for r in matching if str(r.get("date")) == date]
        if "`date` < %s" in sql:
            index = 1 if "stock_id = %s" in sql else 0
            date = str(params[index])
            matching = [r for r in matching if str(r.get("date")) < date]
        return matching

    def _insert(self, sql, params):
        match = self._INSERT.search(sql)
        if not match:
            return 0
        table = match.group(1)
        names = [name.strip().strip("`") for name in match.group(2).split(",")]
        row = dict(zip(names, params))
        row.setdefault("id", self._next_id)
        self._next_id += 1
        self.tables.setdefault(table, []).append(row)
        return 1

    def _update(self, sql, params):
        match = self._UPDATE.search(sql)
        if not match:
            return 0
        table = match.group(1)
        names = [part.split("=")[0].strip().strip("`")
                 for part in match.group(2).split(",")]
        record_id = params[-1]
        values = params[:-1]
        changed = 0
        for row in self.tables.get(table, []):
            if str(row.get("id")) == str(record_id):
                for name, value in zip(names, values):
                    row[name] = value
                changed += 1
        return changed

    def _delete(self, sql, params):
        table = self._table(sql)
        rows = self.tables.get(table, [])
        if "volume = '0'" in sql or "volume = '0'" in sql.replace('"', "'"):
            stock_id = params[0]
            keep = [r for r in rows
                    if not (str(r.get("stock_id")) == str(stock_id)
                            and abs(float(r.get("volume") or 0)) < 0.0000005)]
            deleted = len(rows) - len(keep)
            self.tables[table] = keep
            return deleted
        if "`date` = %s" in sql and "stock_id = %s" in sql:
            stock_id, date = params[0], str(params[1])
            keep = [r for r in rows
                    if not (str(r.get("stock_id")) == str(stock_id)
                            and str(r.get("date")) == date)]
            deleted = len(rows) - len(keep)
            self.tables[table] = keep
            return deleted
        return 0

    @classmethod
    def _table(cls, sql):
        match = cls._SELECT.search(sql)
        return match.group(1) if match else ""


class FakeSource:
    """``MySqlFundDataSource`` 的最小替身：只要能 ``get_stock_id``。"""

    def __init__(self, symbol="SPY", stock_id=5, connection=None):
        self.symbol = symbol
        self.stock_id = stock_id
        self.connection = connection or FakeConnection()

    def get_stock_id(self, symbol):
        return self.stock_id if str(symbol).upper() == self.symbol else None


# ---------------------------------------------------------------------------
# 分红
# ---------------------------------------------------------------------------
class SpdrDividendParseTest(unittest.TestCase):
    def test_parses_us_style_dates(self):
        rows = etfdividend.parse_spdr_dividends(SAMPLE_SPDR_DIVIDEND)
        self.assertEqual([row["date"] for row in rows],
                         ["2026-09-21", "2026-06-22", "2025-12-22"])
        self.assertEqual(rows[0]["dividend"], "0.757469")

    def test_skips_non_numeric_dividend(self):
        """``dividend: "-"`` 是 SSGA 的缺失标记，不是合法金额。

        PHP 会把它当 0 写进 ``stockdividend``（``floatval('-') == 0``），
        那是脏数据 —— 0 分红会出现在页面表格里。这里直接跳过。
        """
        rows = etfdividend.parse_spdr_dividends(SAMPLE_SPDR_DIVIDEND)
        self.assertNotIn("2026-03-23", [row["date"] for row in rows])

    def test_returns_empty_on_bad_json(self):
        self.assertEqual(etfdividend.parse_spdr_dividends("<html>403</html>"), [])

    def test_keeps_interface_order(self):
        """接口是降序，解析**不重排** —— 与 PHP 的 for 循环逐行对照。"""
        rows = etfdividend.parse_spdr_dividends(SAMPLE_SPDR_DIVIDEND)
        dates = [row["date"] for row in rows]
        self.assertEqual(dates, sorted(dates, reverse=True))


class ProsharesDividendParseTest(unittest.TestCase):
    def test_slices_iso_date_like_php_substr(self):
        rows = etfdividend.parse_proshares_dividends(SAMPLE_PROSHARES_DIVIDEND)
        self.assertEqual([row["date"] for row in rows], ["2024-12-23", "2024-09-25"])

    def test_numeric_dividend_stays_numeric(self):
        rows = etfdividend.parse_proshares_dividends(SAMPLE_PROSHARES_DIVIDEND)
        self.assertEqual(rows[0]["dividend"], 0.275411)

    def test_skips_row_without_exdate(self):
        rows = etfdividend.parse_proshares_dividends('[{"Symbol":"TQQQ"}]')
        self.assertEqual(rows, [])


class DividendRoutingTest(unittest.TestCase):
    def test_spdr_symbols_go_to_ssga(self):
        for symbol in ("SPY", "XBI", "XLE", "XLY", "XOP"):
            self.assertTrue(etfdividend.is_spdr_symbol(symbol))
            self.assertIn("ssga.com", etfdividend.dividend_url_of(symbol))

    def test_everything_else_goes_to_proshares(self):
        """PHP 是 ``if (GetSpdrOfficialUrl()) … else …`` —— 其余全部走 Proshares。"""
        for symbol in ("TQQQ", "SQQQ", "SH513100", "^NDX"):
            self.assertFalse(etfdividend.is_spdr_symbol(symbol))
            self.assertIn("proshares.com", etfdividend.dividend_url_of(symbol))

    def test_year_defaults_to_current_not_hardcoded(self):
        """PHP 写死 ``&year=2025``，到 2026 年就静默失效 —— 这里默认取当年。"""
        import datetime

        url = etfdividend.dividend_url_of("TQQQ")
        self.assertIn("year=%d" % datetime.date.today().year, url)
        self.assertIn("year=2019", etfdividend.dividend_url_of("TQQQ", 2019))


class DividendWriteTest(unittest.TestCase):
    def test_writes_each_row_and_reports_counts(self):
        source = FakeSource("XOP")
        result = etfdividend.update_etf_dividend(
            source, "XOP", fetch=lambda url: SAMPLE_SPDR_DIVIDEND)
        self.assertIsNone(result["reason"])
        self.assertEqual(result["rows"], 3)
        self.assertEqual(result["written"], 3)
        rows = source.connection.by_table("stockdividend")
        self.assertEqual(len(rows), 3)
        self.assertEqual(sorted(str(row["date"]) for row in rows),
                         ["2025-12-22", "2026-06-22", "2026-09-21"])
        self.assertTrue(all(str(row["stock_id"]) == "5" for row in rows))

    def test_second_run_writes_nothing(self):
        """同值重提：``WriteDaily`` 比数值，不变就不 UPDATE。"""
        source = FakeSource("XOP")
        etfdividend.update_etf_dividend(
            source, "XOP", fetch=lambda url: SAMPLE_SPDR_DIVIDEND)
        again = etfdividend.update_etf_dividend(
            source, "XOP", fetch=lambda url: SAMPLE_SPDR_DIVIDEND)
        self.assertEqual(again["rows"], 3)
        self.assertEqual(again["written"], 0)

    def test_reports_reason_when_fetch_fails(self):
        source = FakeSource("TQQQ")
        result = etfdividend.update_etf_dividend(source, "TQQQ", fetch=lambda url: None)
        self.assertEqual(result["written"], 0)
        self.assertIn("Cloudflare", result["reason"])

    def test_unknown_symbol_writes_nothing(self):
        source = FakeSource("XOP")
        result = etfdividend.update_etf_dividend(
            source, "NOPE", fetch=lambda url: SAMPLE_SPDR_DIVIDEND)
        self.assertIn("stock 表里没有", result["reason"])
        self.assertEqual(source.connection.queries, [])


# ---------------------------------------------------------------------------
# 净值
# ---------------------------------------------------------------------------
class InvescoParseTest(unittest.TestCase):
    def test_reads_effective_date_and_nav(self):
        row = etfnav.parse_invesco_nav(SAMPLE_INVESCO_NAV)
        self.assertEqual(row["date"], "2026-10-29")
        self.assertAlmostEqual(row["nav"], 715.178947)

    def test_rejects_payload_without_nav(self):
        self.assertIsNone(etfnav.parse_invesco_nav('{"effectiveDate":"2026-10-29"}'))


class SpdrXlsxParseTest(unittest.TestCase):
    def test_reads_header_gap_and_columns(self):
        """第 3 行整行空白，第 4 行才是表头 —— 按行号读才不会错位。"""
        rows = etfnav.parse_spdr_nav_xlsx(build_xlsx(SPDR_NAV_ROWS))
        self.assertEqual([row["date"] for row in rows],
                         ["2026-10-29", "2026-10-28", "2026-10-31", "2013-12-31"])
        self.assertAlmostEqual(rows[0]["nav"], 769.506335)
        #: 流通股数除以 10000（``shareshistory`` 存"万份"）
        self.assertAlmostEqual(rows[0]["shares"], 105728.2116)

    def test_rejects_legacy_xls(self):
        """老式 .xls（OLE 复合文档）不是 zip，要给可读的错误。"""
        with self.assertRaises(xlsxreader.XlsxError):
            xlsxreader.read_rows(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")

    def test_empty_bytes_raises(self):
        with self.assertRaises(xlsxreader.XlsxError):
            xlsxreader.read_rows(b"")

    def test_reader_numbers_rows_like_excel(self):
        rows = xlsxreader.read_rows(build_xlsx(SPDR_NAV_ROWS), 0)
        self.assertEqual(rows[2], [])          # 第 3 行空白，占位
        self.assertEqual(rows[3][0], "Date")   # 第 4 行是表头


class NetValueWriteTest(unittest.TestCase):
    def test_invesco_writes_one_row_without_shares(self):
        source = FakeSource("QQQ")
        result = etfnav.update_etf_net_value(
            source, "QQQ", fetch_text=lambda url: SAMPLE_INVESCO_NAV)
        self.assertIsNone(result["reason"])
        self.assertEqual(result["written"], 1)
        self.assertEqual(result["shares_written"], 0)
        rows = source.connection.by_table("netvaluehistory")
        self.assertEqual(str(rows[0]["date"]), "2026-10-29")
        self.assertAlmostEqual(float(rows[0]["close"]), 715.178947)

    def test_spdr_writes_shares_and_skips_bad_dates(self):
        """周末跳过、早于 2014 直接停（表是降序的，后面的更老）。"""
        source = FakeSource("SPY")
        result = etfnav.update_etf_net_value(
            source, "SPY", fetch_bytes=lambda url: build_xlsx(SPDR_NAV_ROWS))
        self.assertIsNone(result["reason"])
        self.assertEqual(result["written"], 2)          # 只有 10-29 与 10-28
        self.assertEqual(result["shares_written"], 2)
        #: ``rows`` 只数过闸门的 —— 周末那行被跳过、2013 那行触发 break
        self.assertEqual(result["rows"], 2)
        dates = sorted(str(row["date"]) for row in source.connection.by_table("netvaluehistory"))
        self.assertEqual(dates, ["2026-10-28", "2026-10-29"])

    def test_deletes_calibration_only_when_net_value_changed(self):
        """净值变了 → 那天的校准值作废；净值没变 → 校准值是后来重算的，不能删。"""
        connection = FakeConnection(
            tables={"calibrationhistory": [
                {"id": 1, "stock_id": 5, "date": "2026-10-29", "close": 1.007},
                {"id": 2, "stock_id": 5, "date": "2026-10-28", "close": 1.008},
            ]},
        )
        source = FakeSource("SPY", connection=connection)
        result = etfnav.update_etf_net_value(
            source, "SPY", fetch_bytes=lambda url: build_xlsx(SPDR_NAV_ROWS))
        self.assertEqual(result["calibration_deleted"], 2)
        self.assertEqual(connection.by_table("calibrationhistory"), [])

        #: 再跑一次：净值已经一致，两行都不再写 → 校准值不该被删
        connection.tables["calibrationhistory"] = [
            {"id": 3, "stock_id": 5, "date": "2026-10-29", "close": 1.007}]
        again = etfnav.update_etf_net_value(
            source, "SPY", fetch_bytes=lambda url: build_xlsx(SPDR_NAV_ROWS))
        self.assertEqual(again["written"], 0)
        self.assertEqual(again["calibration_deleted"], 0)
        self.assertEqual(len(connection.by_table("calibrationhistory")), 1)

    def test_ishares_symbol_reports_unsupported(self):
        source = FakeSource("IEO")
        result = etfnav.update_etf_net_value(source, "IEO", fetch_bytes=lambda url: b"")
        self.assertIn("不是 SPDR", result["reason"])
        self.assertEqual(source.connection.queries, [])

    def test_reports_reason_when_download_fails(self):
        source = FakeSource("SPY")
        result = etfnav.update_etf_net_value(source, "SPY", fetch_bytes=lambda url: None)
        self.assertIn("没取到", result["reason"])


# ---------------------------------------------------------------------------
# 历史价格
# ---------------------------------------------------------------------------
def _history_rows():
    """覆盖四道闸门的一组日线（升序，与新浪接口一致）。"""
    return [
        {"date": "2013-12-31", "close": 180.0, "volume": 1000},   # too_old
        {"date": "2026-10-26", "close": 766.0, "volume": 3000},
        {"date": "2026-10-26", "close": 767.0, "volume": 3000},   # duplicate
        {"date": "2026-10-27", "close": 0, "volume": 3000},       # empty_close
        {"date": "2026-10-28", "close": 771.0, "volume": 0},      # zero_volume
        {"date": "2026-10-29", "close": 769.36, "volume": 40299546},
    ]


class HistoryUpdateTest(unittest.TestCase):
    def test_writes_through_all_gates(self):
        source = FakeSource("SPY")
        result = historyupdate.update_history_chart(
            source, "SPY", "2026-10-29", fetch=lambda symbol, **kw: _history_rows())
        self.assertIsNone(result["reason"])
        self.assertEqual(result["written"], 2)      # 10-26 与 10-29
        self.assertEqual(result["skipped"]["too_old"], 1)
        self.assertEqual(result["skipped"]["duplicate"], 1)
        self.assertEqual(result["skipped"]["empty_close"], 1)
        self.assertEqual(result["skipped"]["zero_volume"], 1)
        rows = source.connection.by_table("stockhistory")
        self.assertEqual(sorted(str(row["date"]) for row in rows),
                         ["2026-10-26", "2026-10-29"])

    def test_pushes_watermark_and_purges_zero_volume(self):
        connection = FakeConnection(tables={"stockhistory": [
            {"id": 1, "stock_id": 5, "date": "2026-10-20", "close": 750.0,
             "volume": 0, "adjclose": 750.0},
        ]})
        source = FakeSource("SPY", connection=connection)
        result = historyupdate.update_history_chart(
            source, "SPY", "2026-10-29", fetch=lambda symbol, **kw: _history_rows())
        self.assertEqual(result["zero_volume_deleted"], 1)
        watermark = connection.by_table("stockhistorydate")
        self.assertEqual(len(watermark), 1)
        self.assertEqual(str(watermark[0]["date"]), "2026-10-29")

    def test_second_run_is_noop(self):
        """水位线推上去之后，同一天再点就是 already_updated —— 不重复抓。"""
        source = FakeSource("SPY")
        historyupdate.update_history_chart(
            source, "SPY", "2026-10-29", fetch=lambda symbol, **kw: _history_rows())
        result = historyupdate.update_history_chart(
            source, "SPY", "2026-10-29", fetch=lambda symbol, **kw: _history_rows())
        self.assertEqual(result["reason"], "already_updated")
        self.assertIn("今天已经更新过", historyupdate.describe(result))

    def test_watermark_compares_dates_not_strings(self):
        """库里 ``date`` 是 ``datetime.date``，参数是字符串 —— 要比得上。"""
        import datetime

        connection = FakeConnection(watermark={5: datetime.date(2026, 10, 29)})
        source = FakeSource("SPY", connection=connection)
        result = historyupdate.update_history_chart(
            source, "SPY", "2026-10-29", fetch=lambda symbol, **kw: _history_rows())
        self.assertEqual(result["reason"], "already_updated")

    def test_missing_quote_date_refuses_to_write(self):
        """没有行情日期就不写 —— PHP 会 ``WriteDate($id, '')`` 写个 0000-00-00。"""
        source = FakeSource("SPY")
        result = historyupdate.update_history_chart(
            source, "SPY", "", fetch=lambda symbol, **kw: _history_rows())
        self.assertIn("行情日期", result["reason"])
        self.assertEqual(source.connection.queries, [])

    def test_reports_reason_when_no_data(self):
        source = FakeSource("SPY")
        result = historyupdate.update_history_chart(
            source, "SPY", "2026-10-29", fetch=lambda symbol, **kw: [])
        self.assertEqual(result["reason"], "no_data")
        self.assertIn("没抓到日线", historyupdate.describe(result))

    def test_describe_lists_skipped_rows(self):
        source = FakeSource("SPY")
        result = historyupdate.update_history_chart(
            source, "SPY", "2026-10-29", fetch=lambda symbol, **kw: _history_rows())
        text = historyupdate.describe(result)
        self.assertIn("写入 2 行", text)
        self.assertIn("早于 2014", text)
        self.assertIn("成交量 0", text)


class ZeroVolumeDeleteTest(unittest.TestCase):
    def test_empty_stock_id_deletes_nothing(self):
        """``False`` 进来一行都不删（照抄 ``DeleteAll`` 那道闸的想法）。"""
        from sql.historytable import StockHistorySql

        connection = FakeConnection(tables={"stockhistory": [
            {"id": 1, "stock_id": 5, "date": "2026-10-20", "close": 1,
             "volume": 0, "adjclose": 1}]})
        self.assertEqual(StockHistorySql(connection).delete_by_zero_volume(False), 0)
        self.assertEqual(StockHistorySql(connection).delete_by_zero_volume(None), 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
