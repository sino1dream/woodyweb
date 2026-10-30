"""删标的页（``/stock/<symbol>/delete``）的路由测试。

对应 PHP ``woody/res/deletesymbol.php``。**这是本项目唯一会成片删数据的入口**，
所以这一层测的东西比别处更偏向"什么都没发生"：

* 非管理员 POST → 403，而且库里**一行都没少**；
* 被闸门拦住 → 302 回预览页，库里一行都没少（PHP 会先删掉校准历史再拒绝，
  本版不这么做 —— 差异见 ``README_deletesymbol_migration.md`` §5）；
* 未知符号 → 200 加一句提示，不是 500，也不是 404（PHP 是静默跳回）。

分派逻辑本身在 ``auto/test_deletesymbol.py``（34 个用例，手写假库）；
这里只验"路由 + 权限 + 模板 + flash"这一圈，用 sqlite 真跑 SQL
（``web/tests/sqlitedb.py`` 会把 ``%s`` 翻成 ``?``），所以页面上那些行数是
真查出来的，不是桩给的。
"""

import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
PYTHON_DIR = ROOT_DIR / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from web.bootstrap import install_paths  # noqa: E402

install_paths()

from web.app import create_app  # noqa: E402
from web.tests.sqlitedb import SqliteConnection  # noqa: E402

#: 被测符号与它的配对符号（都在 sqlite 里，不碰真库）
STOCK_ID = 419
PAIR_ID = 420


def build_connection():
    """铺满 ``deletesymbol`` 会数的 20 张表 —— 只建被查到的列。

    **表必须全建**：``deletesymbol.plan()`` 对每张登记表都发一条
    ``SELECT COUNT(*)``，少一张 sqlite 就抛 ``no such table``。
    """
    connection = SqliteConnection()
    connection.create_stock([(STOCK_ID, "ZZTEST", "测试"), (PAIR_ID, "ZZPAIR", "配对")])

    # 六张 PHP 会删的表
    connection.create_calibration()
    connection.create_daily("stockema50")
    connection.create_daily("stockema200")
    connection.create_history()
    connection.create_daily("netvaluehistory")

    # 六张残留表
    connection.execute(
        "CREATE TABLE `stockhistorydate` (`id` INTEGER PRIMARY KEY, `date` TEXT NOT NULL)")
    connection.create_val("lastcalibration")
    connection.create_val("fundposition")
    connection.create_daily("quarterreport")
    connection.create_daily("stockdividend")
    connection.execute(
        "CREATE TABLE `realtimequote` (`id` INTEGER PRIMARY KEY, `symbol` TEXT NOT NULL)")
    connection.execute(
        "CREATE TABLE `estquote` (`id` INTEGER PRIMARY KEY, `symbol` TEXT NOT NULL)")
    connection.execute(
        "CREATE TABLE `holdings` (`id` INTEGER PRIMARY KEY, `stock_id` INTEGER NOT NULL, "
        "`holding_id` INTEGER NOT NULL)")

    # 其余残留表（``ORPHAN_TABLES`` 里 15 张，一张都不能少 —— 少一张就 no such table）
    connection.create_daily("fundest")
    connection.create_daily("shareshistory")
    connection.create_daily("sharesdiff")
    connection.create_daily("stocksplit")
    connection.create_daily("futurepremium")
    connection.execute(
        "CREATE TABLE `holdingsdate` (`id` INTEGER PRIMARY KEY, `date` TEXT NOT NULL)")

    # 闸门
    for table in ("abpair", "adrpair", "ahpair", "fundpair", "futurespair"):
        connection.create_pair(table)
    connection.execute(
        "CREATE TABLE `stockgroupitem` (`id` INTEGER PRIMARY KEY AUTOINCREMENT, "
        "`stockgroup_id` INTEGER NOT NULL, `stock_id` INTEGER NOT NULL)")

    connection.insert_daily("calibrationhistory", STOCK_ID, "2026-10-23", 1.2,
                            "`time`, `num`", ("09:00:00", 1))
    connection.insert_daily("stockema50", STOCK_ID, "2026-10-23", 1.5)
    connection.insert_daily("stockema200", STOCK_ID, "2026-10-23", 1.6)
    connection.insert_daily("stockhistory", STOCK_ID, "2026-10-23", 10.5,
                            "`volume`, `adjclose`", (1000, 10.5))
    connection.insert_daily("netvaluehistory", STOCK_ID, "2026-10-23", 2.5)
    connection.execute(
        "INSERT INTO `stockhistorydate` (`id`, `date`) VALUES (?, ?)", (STOCK_ID, "2026-10-23"))
    connection.execute(
        "INSERT INTO `lastcalibration` (`id`, `close`) VALUES (?, ?)", (STOCK_ID, 3.5))
    connection.execute(
        "INSERT INTO `fundposition` (`id`, `close`) VALUES (?, ?)", (STOCK_ID, 0.6))
    connection.insert_daily("quarterreport", STOCK_ID, "2026-09-30", 100)
    connection.insert_daily("stockdividend", STOCK_ID, "2026-09-21", 0.5)
    connection.execute(
        "INSERT INTO `realtimequote` (`id`, `symbol`) VALUES (?, ?)", (STOCK_ID, "ZZTEST"))
    connection.execute(
        "INSERT INTO `estquote` (`id`, `symbol`) VALUES (?, ?)", (STOCK_ID, "ZZTEST"))
    connection.execute(
        "INSERT INTO `holdings` (`id`, `stock_id`, `holding_id`) VALUES (?, ?, ?)",
        (1, STOCK_ID, 900))
    return connection


def row_counts(connection):
    """删完之后各表还剩几行（只看与本符号有关的行）。"""
    tables = (
        "calibrationhistory", "stockema50", "stockema200", "stockhistory",
        "netvaluehistory", "stockhistorydate", "lastcalibration", "fundposition",
        "quarterreport", "stockdividend", "realtimequote", "estquote", "holdings",
    )
    counts = {}
    for table in tables:
        column = "symbol" if table in ("realtimequote", "estquote") else (
            "id" if table in ("stockhistorydate", "lastcalibration", "fundposition") else "stock_id")
        value = "ZZTEST" if column == "symbol" else STOCK_ID
        row = connection.single(
            "SELECT COUNT(*) AS n FROM `%s` WHERE `%s` = ?" % (table, column), (value,))
        counts[table] = int(row["n"])
    counts["stock"] = int(
        connection.single("SELECT COUNT(*) AS n FROM `stock` WHERE `id` = ?",
                          (STOCK_ID,))["n"])
    return counts


class DeletePageTest(unittest.TestCase):
    def setUp(self):
        self.connection = build_connection()
        self.app = create_app({"TESTING": True, "SECRET_KEY": "test"})
        self.app.extensions["delete_connection_factory"] = lambda: self.connection
        self.client = self.app.test_client()

    # -- GET 预览 ---------------------------------------------------------
    def test_preview_lists_gates_and_tables(self):
        body = self.client.get("/stock/ZZTEST/delete").get_data(as_text=True)
        # 五道闸门都在页面上（哪怕全部未命中，也要摆出来）
        for table in ("abpair", "adrpair", "ahpair", "fundpair", "stockgroupitem"):
            self.assertIn(table, body)
        # 六张会删的表
        for table in ("calibrationhistory", "stockema50", "stockema200",
                      "stockhistory", "netvaluehistory"):
            self.assertIn(table, body)
        # 残留表与它的理由（这条是本次迁移的重点，别被模板改掉）
        self.assertIn("stockhistorydate", body)
        self.assertIn("already_updated", body)

    def test_preview_counts_real_rows(self):
        body = self.client.get("/stock/ZZTEST/delete").get_data(as_text=True)
        self.assertIn("共 6 行", body)  # 六张 PHP 表各 1 行

    def test_symbol_lowercase_is_normalized(self):
        response = self.client.get("/stock/zztest/delete")
        self.assertEqual(200, response.status_code)
        self.assertIn("ZZTEST", response.get_data(as_text=True))

    def test_unknown_symbol_is_200_with_hint(self):
        response = self.client.get("/stock/NOSUCH/delete")
        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn("没有", body)
        # 不能变成 500，也不能是 404 —— PHP 是静默跳回，本版至少要说话
        self.assertNotIn("删除 NOSUCH</button>", body)

    def test_non_admin_sees_disabled_button(self):
        with self.client.session_transaction() as session:
            session["email"] = "someone@example.com"
        body = self.client.get("/stock/ZZTEST/delete").get_data(as_text=True)
        self.assertIn("disabled", body)
        self.assertIn("不是管理员", body)

    def test_edit_index_links_to_delete(self):
        body = self.client.get("/stock/ZZTEST/edit").get_data(as_text=True)
        self.assertIn("/stock/ZZTEST/delete", body)
        self.assertIn("危险操作", body)

    # -- POST 执行 --------------------------------------------------------
    def test_non_admin_post_is_403_and_deletes_nothing(self):
        before = row_counts(self.connection)
        with self.client.session_transaction() as session:
            session["email"] = "someone@example.com"
        response = self.client.post("/stock/ZZTEST/delete")
        self.assertEqual(403, response.status_code)
        self.assertEqual(before, row_counts(self.connection))

    def test_blocked_post_deletes_nothing(self):
        """``fundpair`` 里还有它 → 302 回预览页，库一行没少。

        换成 PHP：这里 ``calibrationhistory`` 会被删掉 1 行再拒绝删标的。
        """
        self.connection.execute(
            "INSERT INTO `fundpair` (`id`, `stock_id`) VALUES (?, ?)", (STOCK_ID, PAIR_ID))
        before = row_counts(self.connection)
        response = self.client.post("/stock/ZZTEST/delete")
        self.assertEqual(302, response.status_code)
        self.assertIn("/stock/ZZTEST/delete", response.headers["Location"])
        self.assertEqual(before, row_counts(self.connection))
        # 尤其校准历史不能动
        self.assertEqual(1, row_counts(self.connection)["calibrationhistory"])

    def test_blocked_post_explains_why(self):
        self.connection.execute(
            "INSERT INTO `stockgroupitem` (`stockgroup_id`, `stock_id`) VALUES (?, ?)",
            (7, STOCK_ID))
        response = self.client.post("/stock/ZZTEST/delete", follow_redirects=True)
        body = response.get_data(as_text=True)
        self.assertIn("删不了", body)
        self.assertIn("stockgroupitem", body)

    def test_successful_post_deletes_six_tables(self):
        response = self.client.post("/stock/ZZTEST/delete")
        self.assertEqual(302, response.status_code)
        self.assertEqual("/stock", response.headers["Location"])

        counts = row_counts(self.connection)
        for table in ("calibrationhistory", "stockema50", "stockema200",
                      "stockhistory", "netvaluehistory", "stock"):
            self.assertEqual(0, counts[table], "%s 没删干净" % table)
        # 默认口径：残留表照 PHP 一样留着
        self.assertEqual(1, counts["stockhistorydate"])
        self.assertEqual(1, counts["lastcalibration"])
        self.assertEqual(1, counts["fundposition"])
        # 配对符号那一行不能受牵连
        self.assertEqual(1, int(self.connection.single(
            "SELECT COUNT(*) AS n FROM `stock` WHERE `id` = ?", (PAIR_ID,))["n"]))

    def test_successful_post_flashes_counts(self):
        response = self.client.post("/stock/ZZTEST/delete", follow_redirects=True)
        body = response.get_data(as_text=True)
        self.assertIn("删除 ZZTEST", body)
        self.assertIn("主表 6 行", body)
        # 残留没清就得提醒，否则管理员以为清干净了
        self.assertIn("残留", body)

    def test_include_orphans_clears_leftovers(self):
        response = self.client.post(
            "/stock/ZZTEST/delete", data={"include_orphans": "1"})
        self.assertEqual(302, response.status_code)
        counts = row_counts(self.connection)
        for table in ("stockhistorydate", "lastcalibration", "fundposition",
                      "quarterreport", "stockdividend", "realtimequote", "estquote",
                      "holdings"):
            self.assertEqual(0, counts[table], "%s 没清干净" % table)

    def test_unknown_symbol_post_redirects_without_error(self):
        response = self.client.post("/stock/NOSUCH/delete")
        self.assertEqual(302, response.status_code)
        body = self.client.post(
            "/stock/NOSUCH/delete", follow_redirects=True).get_data(as_text=True)
        self.assertIn("没什么可删的", body)


if __name__ == "__main__":
    unittest.main()
