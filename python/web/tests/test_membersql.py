"""会员 / 资料表的 SQL 访问层测试（``php/sql/sqlmember.php`` → ``core/membersql.py``）。

用 ``tests/sqlitedb.py`` 的内存库跑**真 SQL**，而不是手写分发 —— 这一层的
价值就在"SQL 写对了没有"：``LIMIT 1``、``ORDER BY``、列名、以及
"先查后插/先读后写"那几条。手写分发只能验调用次数，验不出 SQL 本身。

## 与 MySQL 的三处差异（在包装层抹平或忽略）

1. ``NOW()`` → ``CURRENT_TIMESTAMP``（sqlite 没有 ``NOW()``，见
   :class:`_Connection`）；
2. ``INSERT ... id = 0`` 走自增：MySQL 的 AUTO_INCREMENT 遇到 0 会自增，
   sqlite 会**存成 0**。本文件因此只插一行会员，"id 是几"不做断言；
3. 字符串比较的 collation：MySQL 的 ``utf8_unicode_ci`` 不区分大小写，
   sqlite 的 ``=`` 区分。这里**不测**大小写 —— 测了也是测 sqlite，
   与生产端行为相反，反而会误导。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[3]
PYTHON_DIR = ROOT_DIR / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))
WEB_TESTS_DIR = Path(__file__).resolve().parent
if str(WEB_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(WEB_TESTS_DIR))

from web.bootstrap import install_paths  # noqa: E402

install_paths()

from core import membersql  # noqa: E402
from sqlitedb import FakeSource, SqliteConnection, SqliteCursor  # noqa: E402


class _Cursor(SqliteCursor):
    """抹平两处 MySQL → sqlite 的语法差。

    1. ``NOW()`` → ``CURRENT_TIMESTAMP``（sqlite 没有 ``NOW()``）；
    2. ``DELETE ... LIMIT 1`` → 去掉 ``LIMIT``：sqlite 默认编译不出这个语法，
       而本模块那条 DELETE 的 ``WHERE`` 是 ``UNIQUE(member_id)``，
       去掉 LIMIT 语义不变（与 ``sqlitedb`` 对 UPDATE 的处置同理）。

    只在这一层做替换 —— 改共享的 ``SqliteCursor`` 会让别的测试也背上
    一个"看不见的改写"，而这两条差异只有本模块有。
    """

    def execute(self, sql, params=()):
        sql = sql.replace("NOW()", "CURRENT_TIMESTAMP")
        if sql.lstrip().upper().startswith("DELETE"):
            sql = sql.replace(" LIMIT 1", "")
        super().execute(sql, params)


class _Connection(SqliteConnection):
    def cursor(self):
        return _Cursor(self._conn.cursor(), self.queries)


MEMBER_DDL = """
    CREATE TABLE `member` (
      `id` INTEGER PRIMARY KEY ,
      `email` TEXT NOT NULL UNIQUE ,
      `password` TEXT NOT NULL DEFAULT '' ,
      `register` TEXT NOT NULL ,
      `login` TEXT NOT NULL ,
      `status` INTEGER NOT NULL ,
      `activity` INTEGER NOT NULL ,
      `ip` TEXT NOT NULL DEFAULT ''
    )
"""

PROFILE_DDL = """
    CREATE TABLE `profile` (
      `id` INTEGER PRIMARY KEY ,
      `member_id` INTEGER NOT NULL UNIQUE ,
      `name` TEXT NOT NULL DEFAULT '' ,
      `phone` TEXT NOT NULL DEFAULT '' ,
      `address` TEXT NOT NULL DEFAULT '' ,
      `web` TEXT NOT NULL DEFAULT '' ,
      `signature` TEXT NOT NULL DEFAULT ''
    )
"""

#: 关号会顺着分组往下删，三张分组表要都在（``core.stockgroupsql`` 的实现
#: 是"先查分组、再删 item/交易、最后删分组"）。
GROUP_DDL = (
    """
    CREATE TABLE `stockgroup` (
      `id` INTEGER PRIMARY KEY ,
      `member_id` INTEGER NOT NULL ,
      `groupname` TEXT NOT NULL ,
      UNIQUE ( `groupname`, `member_id` )
    )
    """,
    """
    CREATE TABLE `stockgroupitem` (
      `id` INTEGER PRIMARY KEY ,
      `stockgroup_id` INTEGER NOT NULL ,
      `stock_id` INTEGER NOT NULL ,
      `quantity` INTEGER NOT NULL DEFAULT 0 ,
      `cost` REAL NOT NULL DEFAULT 0 ,
      `record` INTEGER NOT NULL DEFAULT 0 ,
      UNIQUE ( `stock_id`, `stockgroup_id` )
    )
    """,
    """
    CREATE TABLE `stocktransaction` (
      `id` INTEGER PRIMARY KEY ,
      `groupitem_id` INTEGER NOT NULL ,
      `quantity` INTEGER NOT NULL ,
      `price` REAL NOT NULL ,
      `fees` REAL NOT NULL ,
      `filled` TEXT NOT NULL ,
      `remark` TEXT NOT NULL DEFAULT ''
    )
    """,
)


class _Base(unittest.TestCase):
    def setUp(self):
        self.connection = _Connection()
        self.connection._conn.execute(MEMBER_DDL)
        self.connection._conn.execute(PROFILE_DDL)
        for ddl in GROUP_DDL:
            self.connection._conn.execute(ddl)
        self.source = FakeSource(self.connection)
        self.connection._conn.execute(
            "INSERT INTO member (id, email, password, register, login, status, "
            "activity, ip) VALUES (1, 'demo@palmmicro.com', ?, '2026-01-01', "
            "'2026-01-01', 1, 0, '')",
            (membersql.hash_password("demo1234"),))
        self.connection._conn.commit()


class MemberReadTest(_Base):
    def test_get_id_by_email(self):
        self.assertEqual(
            membersql.sql_get_id_by_email(self.source, "demo@palmmicro.com"),
            "1")

    def test_get_id_by_email_missing_returns_none(self):
        """PHP 返回 ``false``，本地返回 ``None``（AGENTS.md 第 3 条）。"""
        self.assertIsNone(
            membersql.sql_get_id_by_email(self.source, "nobody@palmmicro.com"))

    def test_get_member_by_id_shape(self):
        member = membersql.sql_get_member_by_id(self.source, 1)
        self.assertEqual(member["email"], "demo@palmmicro.com")
        self.assertEqual(member["status"], 1)
        # 补丁打完后的列齐不齐，是资料页能不能渲染的前提
        for key in ("id", "email", "password", "register", "login", "status",
                    "activity", "ip"):
            self.assertIn(key, member)

    def test_get_email_by_id(self):
        self.assertEqual(
            membersql.sql_get_email_by_id(self.source, 1), "demo@palmmicro.com")
        self.assertIsNone(membersql.sql_get_email_by_id(self.source, 404))

    def test_get_member_by_ip_orders_by_login_desc(self):
        self.connection._conn.execute(
            "INSERT INTO member (id, email, password, register, login, status, "
            "activity, ip) VALUES (2, 'b@palmmicro.com', '', '2026-01-01', "
            "'2026-09-09', 1, 0, '1.2.3.4')")
        self.connection._conn.execute(
            "INSERT INTO member (id, email, password, register, login, status, "
            "activity, ip) VALUES (3, 'a@palmmicro.com', '', '2026-01-01', "
            "'2026-01-02', 1, 0, '1.2.3.4')")
        rows = membersql.sql_get_member_by_ip(self.source, "1.2.3.4")
        self.assertEqual([row["email"] for row in rows],
                         ["b@palmmicro.com", "a@palmmicro.com"])


class LoginTest(_Base):
    def test_ok(self):
        self.assertEqual(
            membersql.sql_exec_login(self.source, "demo@palmmicro.com",
                                     "demo1234"), "1")

    def test_wrong_password(self):
        self.assertIsNone(
            membersql.sql_exec_login(self.source, "demo@palmmicro.com", "nope"))

    def test_password_is_hashed_not_plaintext(self):
        """库里存的是 md5 —— 用明文去比必须失败（防"哪天忘了哈希"）。"""
        self.assertIsNone(
            membersql.sql_exec_login(self.source, "demo@palmmicro.com",
                                     membersql.hash_password("demo1234")))


class MemberWriteTest(_Base):
    def test_insert_member(self):
        ok = membersql.sql_insert_member(self.source, "new@palmmicro.com",
                                         "pw123456")
        self.assertTrue(ok)
        member = membersql.sql_get_member_by_id(
            self.source,
            membersql.sql_get_id_by_email(self.source, "new@palmmicro.com"))
        self.assertEqual(member["password"], membersql.hash_password("pw123456"))
        # PHP SqlInsertMember 硬编码 status='1' / activity='0'
        self.assertEqual(member["status"], 1)
        self.assertEqual(member["activity"], 0)

    def test_insert_member_records_ip(self):
        membersql.sql_insert_member(self.source, "new@palmmicro.com", "pw",
                                    "10.0.0.1")
        member = membersql.sql_get_member_by_id(
            self.source,
            membersql.sql_get_id_by_email(self.source, "new@palmmicro.com"))
        self.assertEqual(member["ip"], "10.0.0.1")

    def test_update_password_field_by_email(self):
        membersql.sql_update_password_field(self.source, "demo@palmmicro.com",
                                            "brandnew")
        self.assertEqual(
            membersql.sql_exec_login(self.source, "demo@palmmicro.com",
                                     "brandnew"), "1")
        # 旧密码失效
        self.assertIsNone(
            membersql.sql_exec_login(self.source, "demo@palmmicro.com",
                                     "demo1234"))

    def test_update_login_email(self):
        membersql.sql_update_login_email(self.source, 1, "changed@palmmicro.com")
        self.assertEqual(membersql.sql_get_email_by_id(self.source, 1),
                         "changed@palmmicro.com")
        self.assertIsNone(
            membersql.sql_get_id_by_email(self.source, "demo@palmmicro.com"))

    def test_update_login_field_sets_ip_and_time(self):
        """``login = NOW()``：DATE 列在生产端会截断成日期，这里只要能落进去。"""
        membersql.sql_update_login_field(self.source, "demo@palmmicro.com",
                                         "8.8.8.8")
        member = membersql.sql_get_member_by_id(self.source, 1)
        self.assertEqual(member["ip"], "8.8.8.8")
        self.assertNotEqual(str(member["login"]), "2026-01-01")

    def test_change_activity_accumulates(self):
        membersql.sql_change_activity(self.source, 1, 3)
        membersql.sql_change_activity(self.source, 1, 2)
        self.assertEqual(
            membersql.sql_get_member_by_id(self.source, 1)["activity"], 5)

    def test_change_activity_missing_member_returns_false(self):
        self.assertFalse(membersql.sql_change_activity(self.source, 404, 1))

    def test_update_status(self):
        membersql.sql_update_status(self.source, 1, 2)
        self.assertEqual(
            membersql.sql_get_member_by_id(self.source, 1)["status"], 2)


class ProfileTest(_Base):
    def test_missing_profile_returns_none(self):
        self.assertIsNone(
            membersql.sql_get_profile_by_member_id(self.source, 1))
        self.assertIsNone(membersql.sql_get_name_by_member_id(self.source, 1))

    def test_update_profile_inserts_then_updates(self):
        """PHP 是"查到就 UPDATE、查不到就 INSERT"，本地照抄（不是 UPSERT）。"""
        self.assertTrue(membersql.sql_update_profile(
            self.source, 1, "张三", "12345678", "上海", "http://x", "签名"))
        profile = membersql.sql_get_profile_by_member_id(self.source, 1)
        self.assertEqual(profile["name"], "张三")
        self.assertEqual(membersql.sql_get_name_by_member_id(self.source, 1),
                         "张三")

        self.assertTrue(membersql.sql_update_profile(
            self.source, 1, "李四", "", "", "", ""))
        profile = membersql.sql_get_profile_by_member_id(self.source, 1)
        self.assertEqual(profile["name"], "李四")
        self.assertEqual(profile["signature"], "")

    def test_empty_name_is_none_not_empty_string(self):
        """``GetWhoseDisplay`` 靠"名字取不到"回落到邮箱，空串会让它显示残缺文案。"""
        membersql.sql_update_profile(self.source, 1, "", "", "", "", "")
        self.assertIsNone(membersql.sql_get_name_by_member_id(self.source, 1))

    def test_delete_profile(self):
        membersql.sql_update_profile(self.source, 1, "张三", "", "", "", "")
        self.assertEqual(
            membersql.sql_delete_profile_by_member_id(self.source, 1), 1)
        self.assertIsNone(
            membersql.sql_get_profile_by_member_id(self.source, 1))


class DeleteMemberTest(_Base):
    def test_deletes_member_profile_and_groups(self):
        """``AcctDeleteMember()`` 一次清干净：分组 → 资料 → 会员。"""
        self.connection._conn.execute(
            "INSERT INTO stockgroup (id, member_id, groupname) "
            "VALUES (10, 1, '演示分组A')")
        self.connection._conn.execute(
            "INSERT INTO stockgroupitem (id, stockgroup_id, stock_id) "
            "VALUES (20, 10, 5)")
        self.connection._conn.execute(
            "INSERT INTO stocktransaction (id, groupitem_id, quantity, price, "
            "fees, filled, remark) VALUES (30, 20, 100, 1.5, 5, "
            "'2026-10-15 10:30:00', '')")
        membersql.sql_update_profile(self.source, 1, "张三", "", "", "", "")
        self.connection._conn.commit()

        stats = membersql.sql_delete_member(self.source, 1)
        self.assertEqual(stats["groups"], 1)
        self.assertEqual(stats["profile"], 1)
        self.assertEqual(stats["member"], 1)
        self.assertIsNone(membersql.sql_get_member_by_id(self.source, 1))
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) AS n FROM stocktransaction"),
            [{"n": 0}])


class CleanStringTest(unittest.TestCase):
    def test_spec_cases(self):
        self.assertEqual(membersql.sql_clean_string("  abc "), "abc")
        self.assertEqual(membersql.sql_clean_string("a\\'b"), "a'b")
        self.assertEqual(membersql.sql_clean_string("a\\\\b"), "a\\b")
        self.assertEqual(membersql.sql_clean_string("a\\zb"), "a\\zb")


if __name__ == "__main__":
    unittest.main()
