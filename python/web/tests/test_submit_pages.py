"""后台抓取工具页（``/submit`` 一族）的路由测试。

对应 PHP 三张"点一下就把外部数据抓进库"的动作页：
``woody/res/submitdividend.php`` / ``submitnetvalue.php`` / ``submithistory.php``。

## 这一层测什么、不测什么

**测**：路由分派、权限闸门、参数校验、页面上的现状数字、flash 文案。
**不测**：抓取与解析 —— 那些在 ``auto/test_submitupdaters.py``（用真实样本 +
内存假库）。这里的"跑工具"用 ``mock.patch`` 顶掉 updater，只验证
"哪个工具名调到了哪个函数、参数对不对、结果怎么回给用户"。

**不连库**：页面现状走 sqlite（``web/tests/sqlitedb.py``），因为那几条都是
纯 SELECT（``COUNT(*)`` / ``MAX(date)``）；写路径不经过 sqlite ——
``stockhistorydate`` 的 upsert 用的是 MySQL 的 ``ON DUPLICATE KEY UPDATE``，
sqlite 编译不出来。
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT_DIR = Path(__file__).resolve().parents[3]
PYTHON_DIR = ROOT_DIR / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from web.bootstrap import install_paths  # noqa: E402

install_paths()

from web.app import create_app  # noqa: E402
from web.services import submit_service  # noqa: E402
from web.services.admin_auth import ADMIN_EMAIL  # noqa: E402
from web.tests.sqlitedb import FakeSource, SqliteConnection  # noqa: E402


class _SourceWithSymbols(FakeSource):
    """在 sqlite 版 ``FakeSource`` 上加一个 ``get_stock_id``。

    ``describe_symbol`` 第一步就是拿 symbol 换 stock_id（与
    ``MySqlFundDataSource.get_stock_id`` 同签名，返回**字符串** ——
    真实数据源也是 ``str(row["id"])``）。
    """

    def __init__(self, connection, symbols):
        super().__init__(connection)
        self.symbols = dict(symbols)

    def get_stock_id(self, symbol):
        return self.symbols.get(str(symbol).upper())


def build_source():
    """一只 SPY（有分红/净值/历史）+ 一只空符号 QQQ。"""
    connection = SqliteConnection()
    connection.create_stock([(5, "SPY", "标普500ETF"), (4, "QQQ", "纳指100ETF")])
    connection.create_daily("stockdividend")
    connection.create_daily("netvaluehistory")
    connection.create_daily("shareshistory")
    connection.create_history()
    connection.execute(
        "CREATE TABLE `stockhistorydate` (`id` INTEGER PRIMARY KEY, `date` TEXT NOT NULL)")

    connection.insert_daily("stockdividend", 5, "2026-09-21", 0.757469)
    connection.insert_daily("stockdividend", 5, "2026-06-22", 0.748259)
    connection.insert_daily("netvaluehistory", 5, "2026-10-23", 759.97)
    connection.insert_daily(
        "stockhistory", 5, "2026-10-23", 759.0, "`volume`, `adjclose`", (1234, 759.0))
    connection.execute(
        "INSERT INTO `stockhistorydate` (`id`, `date`) VALUES (?, ?)", (5, "2026-10-23"))

    return _SourceWithSymbols(connection, {"SPY": "5", "QQQ": "4"})


class SubmitPageTest(unittest.TestCase):
    def setUp(self):
        self.source = build_source()
        self.app = create_app(
            {"TESTING": True, "SECRET_KEY": "test"},
            submit_source_factory=lambda: self.source,
        )
        self.client = self.app.test_client()

    # -- 页面 -------------------------------------------------------------
    def test_tool_page_lists_three_php_pages(self):
        html = self.client.get("/submit").get_data(as_text=True)
        for php in ("submitdividend.php", "submitnetvalue.php", "submithistory.php"):
            self.assertIn(php, html)
        #: 三行工具，每行一个 POST 表单
        for key in ("dividend", "netvalue", "history"):
            self.assertIn('action="/submit/%s"' % key, html)

    def test_tool_page_warns_write_is_post(self):
        html = self.client.get("/submit").get_data(as_text=True)
        self.assertIn("写操作一律 POST", html)

    def test_without_symbol_no_status_line(self):
        html = self.client.get("/submit").get_data(as_text=True)
        self.assertNotIn("的现状", html)

    def test_with_symbol_shows_table_state(self):
        """现状三个数取自三张目标表，水位线取自 ``stockhistorydate``。"""
        html = self.client.get("/submit?symbol=SPY").get_data(as_text=True)
        self.assertIn("分红 2 条（最新 2026-09-21）", html)
        self.assertIn("净值 1 条（最新 2026-10-23）", html)
        self.assertIn("历史 1 条（最新 2026-10-23）", html)
        self.assertIn("历史水位线 2026-10-23", html)

    def test_symbol_is_upper_cased(self):
        html = self.client.get("/submit?symbol=spy").get_data(as_text=True)
        self.assertIn("分红 2 条", html)

    def test_unknown_symbol_warns_instead_of_500(self):
        html = self.client.get("/submit?symbol=NOPE").get_data(as_text=True)
        self.assertIn("stock</code> 表里没有", html)

    def test_admin_page_links_to_tools(self):
        """``/admin`` 是页头「后台」的落点，工具页从那里够得着。"""
        html = self.client.get("/admin/groups").get_data(as_text=True)
        self.assertIn("/submit", html)


class SubmitRunTest(unittest.TestCase):
    def setUp(self):
        self.source = build_source()
        self.app = create_app(
            {"TESTING": True, "SECRET_KEY": "test"},
            submit_source_factory=lambda: self.source,
        )
        self.client = self.app.test_client()

    # -- 闸门 -------------------------------------------------------------
    def test_unknown_tool_is_404(self):
        response = self.client.post("/submit/nope", data={"symbol": "SPY"})
        self.assertEqual(response.status_code, 404)

    def test_non_admin_is_403_and_writes_nothing(self):
        """身份换成别人：闸门立刻生效，updater 一次都不该被调。"""
        with self.client.session_transaction() as session:
            session["email"] = "someone@example.com"
        with mock.patch.object(
            submit_service.etfdividend, "update_etf_dividend"
        ) as called:
            response = self.client.post("/submit/dividend", data={"symbol": "SPY"})
        self.assertEqual(response.status_code, 403)
        called.assert_not_called()

    def test_missing_symbol_is_400(self):
        response = self.client.post("/submit/dividend", data={"symbol": "  "})
        self.assertEqual(response.status_code, 400)

    # -- 分派 -------------------------------------------------------------
    def test_dividend_post_calls_updater_and_flashes(self):
        fake = {"symbol": "SPY", "rows": 3, "written": 2, "reason": None}
        with mock.patch.object(
            submit_service.etfdividend, "update_etf_dividend", return_value=fake
        ) as called:
            response = self.client.post(
                "/submit/dividend", data={"symbol": "spy"}, follow_redirects=True)
        called.assert_called_once()
        self.assertEqual(called.call_args[0][1], "SPY")
        html = response.get_data(as_text=True)
        self.assertIn("解析出 3 条分红，写入 2 条", html)

    def test_netvalue_post_mentions_shares_and_calibration(self):
        fake = {"symbol": "SPY", "source": "spdr", "rows": 2, "written": 2,
                "shares_written": 2, "calibration_deleted": 1, "reason": None}
        with mock.patch.object(
            submit_service.etfnav, "update_etf_net_value", return_value=fake
        ):
            response = self.client.post(
                "/submit/netvalue", data={"symbol": "SPY"}, follow_redirects=True)
        html = response.get_data(as_text=True)
        self.assertIn("净值写入 2 条", html)
        self.assertIn("流通股数 2 条", html)
        self.assertIn("清掉 1 条同日校准值", html)

    def test_history_post_passes_quote_date(self):
        """``submithistory`` 要先有行情日期才能比水位线 —— 由路由取好传下去。"""
        fake = {"symbol": "SPY", "rows": 0, "written": 0, "zero_volume_deleted": 0,
                "skipped": {}, "reason": "already_updated", "source_date": "2026-10-29"}
        with mock.patch.object(
            submit_service, "quote_date_of", return_value="2026-10-29"
        ) as date_of, mock.patch.object(
            submit_service.historyupdate, "update_history_chart", return_value=fake
        ) as called:
            response = self.client.post(
                "/submit/history", data={"symbol": "SPY"}, follow_redirects=True)
        date_of.assert_called_once()
        self.assertEqual(called.call_args[0][2], "2026-10-29")
        self.assertIn("今天已经更新过", response.get_data(as_text=True))

    def test_failure_reason_reaches_the_user(self):
        fake = {"symbol": "TQQQ", "rows": 0, "written": 0,
                "reason": "没取到数据（Proshares 接口被 Cloudflare 拦（403））"}
        with mock.patch.object(
            submit_service.etfdividend, "update_etf_dividend", return_value=fake
        ):
            response = self.client.post(
                "/submit/dividend", data={"symbol": "TQQQ"}, follow_redirects=True)
        self.assertIn("Cloudflare", response.get_data(as_text=True))

    def test_redirect_carries_symbol_back(self):
        fake = {"symbol": "SPY", "rows": 0, "written": 0, "reason": None}
        with mock.patch.object(
            submit_service.etfdividend, "update_etf_dividend", return_value=fake
        ):
            response = self.client.post("/submit/dividend", data={"symbol": "SPY"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("symbol=SPY", response.headers["Location"])


class ToolRegistryTest(unittest.TestCase):
    def test_every_tool_has_the_fields_the_template_needs(self):
        for tool in submit_service.tool_list():
            for field in ("key", "php", "title", "label", "sources", "writes", "note"):
                self.assertTrue(tool.get(field), "%s 缺 %s" % (tool.get("key"), field))

    def test_unknown_tool_returns_none(self):
        self.assertIsNone(submit_service.get_tool("nope"))

    def test_admin_email_is_the_demo_identity(self):
        """演示环境默认身份就是管理员 —— 闸门本身仍是真判断（见 admin_auth）。"""
        self.assertEqual(ADMIN_EMAIL, "woody@palmmicro.com")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
