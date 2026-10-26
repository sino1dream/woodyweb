"""季报写入口（``/holdings/quarter/<symbol>``）的 web 层测试。

对应 PHP 两个文件：

* ``woody/res/editquarterreportcn.php`` → ``php/_editstockoption.php``：
  通用"股票选项"编辑表单的一张皮，季报是其中一个选项；
* ``woody/res/submitstockoptions.php`` 的 REPORT 分支 →::

      case STOCK_OPTION_REPORT:
          if ($bAdmin)  _updateOptionDailySql(new QuarterReportSql(), $strStockId, $strDate, $strVal);

  而 ``_updateOptionDailySql`` 的语义是::

      if (empty($strVal))  return $sql->DeleteByDate($strStockId, $strDate);
      return $sql->WriteDaily($strStockId, $strDate, $strVal);

本模块用**内存假库**跑这两条路，验四件事：默认值口径、写 / 删 / 值没变
三种结果、``'0'`` 当删除（PHP ``empty`` 的坑）、非管理员 403。
假库按 SQL 特征分发（与 ``auto/test_historytable.py`` 的 ``FakeConnection``
同一套路），不连真库。
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
from web.routes.holdings import QUARTER_REPORT_SAMPLE  # noqa: E402


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self.rows = []
        self.rowcount = 0

    def execute(self, sql, params=()):
        self.db.queries.append((sql, params))
        self.rows, self.rowcount = self.db.dispatch(sql, params)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class FakeDb:
    """内存假库：``stock`` 一张符号表 + ``quarterreport`` 一张季报表。"""

    def __init__(self, symbols=None, rows=None):
        #: {symbol: stock_id}
        self.symbols = dict(symbols or {})
        #: {(stock_id, date): {"id","stock_id","date","close"}}
        self.rows = dict(rows or {})
        self.queries = []
        self.commits = 0
        self._next_id = 1000

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    # -- 分发 -----------------------------------------------------------------
    def dispatch(self, sql, params):
        text = " ".join(sql.split())
        if text.upper().startswith("SELECT"):
            if "`stock`" in text:
                symbol = params[0]
                stock_id = self.symbols.get(symbol)
                if stock_id is None:
                    return [], 0
                return [{"id": stock_id, "symbol": symbol, "name": symbol}], 1
            if "`quarterreport`" in text:
                stock_id = params[0]
                rows = [row for key, row in sorted(self.rows.items())
                        if key[0] == stock_id]
                if "`date` = %s" in text:
                    date = str(params[1])
                    rows = [row for row in rows if str(row["date"]) == date][:1]
                    return rows, len(rows)
                #: ``GetRecordNow``：ORDER BY date DESC LIMIT 1
                rows.sort(key=lambda row: str(row["date"]), reverse=True)
                return rows[:1], 1
            return [], 0

        if text.upper().startswith("INSERT"):
            stock_id, date, close = params
            key = (stock_id, str(date))
            if key in self.rows:
                return [], 0
            self._next_id += 1
            self.rows[key] = {"id": self._next_id, "stock_id": stock_id,
                              "date": str(date), "close": close}
            return [], 1
        if text.upper().startswith("UPDATE"):
            close, record_id = params
            for row in self.rows.values():
                if row["id"] == record_id:
                    row["close"] = close
                    return [], 1
            return [], 0
        if text.upper().startswith("DELETE"):
            stock_id, date = params
            key = (stock_id, str(date))
            if key in self.rows:
                del self.rows[key]
                return [], 1
            return [], 0
        return [], 0


class FakeSource:
    def __init__(self, db):
        self.connection = db


def _app(db):
    app = create_app({"TESTING": True})
    app.extensions["holdings_write_source_factory"] = lambda: FakeSource(db)
    return app


class DefaultValueTest(unittest.TestCase):
    """``_getStockOptionDate`` / ``_getStockOptionVal`` 的 REPORT 分支。"""

    def test_empty_table_falls_back_to_sample(self):
        """库里没有季报时：日期 = 今天，值 = PHP 写死的样例串。"""
        app = _app(FakeDb(symbols={"SZ164701": 59}))
        html = app.test_client().get(
            "/holdings/quarter/SZ164701").get_data(as_text=True)
        self.assertIn(QUARTER_REPORT_SAMPLE.replace('"', "&#34;"), html)
        self.assertIn("清空输入删除对应日期季报数据。", html)

    def test_existing_row_is_echoed(self):
        """有记录时回填最新那条的日期与值（``GetDateNow`` / ``GetCloseNow``）。"""
        db = FakeDb(symbols={"SZ164701": 59}, rows={
            (59, "2026-09-30"): {"id": 1, "stock_id": 59,
                                 "date": "2026-09-30", "close": '"GLD":100'},
            (59, "2026-06-30"): {"id": 2, "stock_id": 59,
                                 "date": "2026-06-30", "close": '"SPY":100'},
        })
        html = _app(db).test_client().get(
            "/holdings/quarter/SZ164701").get_data(as_text=True)
        self.assertIn('value="2026-09-30"', html)
        self.assertIn("&#34;GLD&#34;:100", html)

    def test_unknown_symbol_hides_the_form(self):
        """库里没有这个符号：不摆表单，只给提示。

        POST 分支本来就会 400（"stock 表里没有符号"），但让人先填一遍再被拒
        没意义 —— 与穷举页 ``NOPE999 → 200 + 提示`` 的取舍一致。
        """
        html = _app(FakeDb(symbols={})).test_client().get(
            "/holdings/quarter/NOPE999").get_data(as_text=True)
        self.assertIn("没有符号", html)
        self.assertNotIn('name="value"', html)


class SubmitTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDb(symbols={"SZ164701": 59})
        self.client = _app(self.db).test_client()

    def test_insert_then_redirect(self):
        response = self.client.post("/holdings/quarter/SZ164701",
                                    data={"date": "2026-09-30",
                                          "value": '"GLD":100'})
        self.assertEqual(302, response.status_code)
        self.assertEqual(self.db.rows[(59, "2026-09-30")]["close"], '"GLD":100')

    def test_unchanged_value_does_not_update(self):
        """值没变 → 不写（``DailyStringSql.write_daily`` 的 ``return false``）。"""
        self.client.post("/holdings/quarter/SZ164701",
                         data={"date": "2026-09-30", "value": '"GLD":100'})
        updates_before = len([q for q in self.db.queries if q[0].strip().upper().startswith("UPDATE")])
        self.client.post("/holdings/quarter/SZ164701",
                         data={"date": "2026-09-30", "value": '"GLD":100'})
        updates_after = len([q for q in self.db.queries if q[0].strip().upper().startswith("UPDATE")])
        self.assertEqual(updates_before, updates_after)

    def test_changed_value_updates(self):
        self.client.post("/holdings/quarter/SZ164701",
                         data={"date": "2026-09-30", "value": '"GLD":100'})
        self.client.post("/holdings/quarter/SZ164701",
                         data={"date": "2026-09-30", "value": '"GLD":60,"SPY":40'})
        self.assertEqual(self.db.rows[(59, "2026-09-30")]["close"], '"GLD":60,"SPY":40')

    def test_empty_value_deletes(self):
        self.client.post("/holdings/quarter/SZ164701",
                         data={"date": "2026-09-30", "value": '"GLD":100'})
        self.client.post("/holdings/quarter/SZ164701",
                         data={"date": "2026-09-30", "value": ""})
        self.assertNotIn((59, "2026-09-30"), self.db.rows)

    def test_zero_value_also_deletes(self):
        """**PHP 的 ``empty('0')`` 为真** —— 填 0 是删除，不是写 0。"""
        self.client.post("/holdings/quarter/SZ164701",
                         data={"date": "2026-09-30", "value": '"GLD":100'})
        self.client.post("/holdings/quarter/SZ164701",
                         data={"date": "2026-09-30", "value": "0"})
        self.assertNotIn((59, "2026-09-30"), self.db.rows)

    def test_unknown_symbol_is_rejected(self):
        """库里没有这个符号 → 400，不写脏行（PHP 会写进 stock_id = 0）。"""
        response = self.client.post("/holdings/quarter/NOPE999",
                                    data={"date": "2026-09-30", "value": '"GLD":100'})
        self.assertEqual(400, response.status_code)
        self.assertEqual(self.db.rows, {})

    def test_empty_date_is_rejected(self):
        response = self.client.post("/holdings/quarter/SZ164701",
                                    data={"date": "", "value": '"GLD":100'})
        self.assertEqual(400, response.status_code)
        self.assertEqual(self.db.rows, {})

    def test_flash_reports_what_happened(self):
        response = self.client.post("/holdings/quarter/SZ164701",
                                    data={"date": "2026-09-30", "value": '"GLD":100'},
                                    follow_redirects=True)
        self.assertIn("季报已写入", response.get_data(as_text=True))


class PermissionTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDb(symbols={"SZ164701": 59})
        self.client = _app(self.db).test_client()

    def test_post_denied_for_non_admin(self):
        from unittest import mock

        with mock.patch.dict(os.environ,
                             {"PALMMICRO_ADMIN_EMAIL": "guest@example.com"}):
            response = self.client.post("/holdings/quarter/SZ164701",
                                        data={"date": "2026-09-30",
                                              "value": '"GLD":100'})
        self.assertEqual(403, response.status_code)
        self.assertEqual(self.db.rows, {})
        self.assertEqual(self.db.commits, 0)

    def test_get_is_read_only_for_non_admin(self):
        from unittest import mock

        with mock.patch.dict(os.environ,
                             {"PALMMICRO_ADMIN_EMAIL": "guest@example.com"}):
            response = self.client.get("/holdings/quarter/SZ164701")
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn("只能查看不能写", html)
        self.assertIn("disabled", html)

    def test_non_admin_hint_names_the_env_override(self):
        """非管理员时的提示里给出"怎么切身份"（省得下次再翻代码找变量名）。"""
        from unittest import mock

        with mock.patch.dict(os.environ,
                            {"PALMMICRO_ADMIN_EMAIL": "guest@example.com"}):
            html = self.client.get("/holdings/quarter/SZ164701").get_data(as_text=True)
        self.assertIn("PALMMICRO_ADMIN_EMAIL", html)


if __name__ == "__main__":
    unittest.main()
