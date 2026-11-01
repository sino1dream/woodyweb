"""账号页路由的测试（``account/login*.php`` 一族 → ``/account/<action>``）。

**用 sqlite 真库**（同 ``test_membersql.py`` 的建表方式），因为它要验的正是
"表单 → 服务 → 数据库 → session → 下一页"这条链。把 membersql 整体 patch 掉
就只能验"调用了几次"，验不出"登录完资料页真的能显示这个人"。

## 六个"照抄 PHP"的行为各有一条用例

1. 登录成功写 ``SESS_ID`` + ``email`` 两个键（PHP 只写 SESS_ID，
   邮箱靠查库；本地写两份省一次查询，改邮箱时手动刷新）；
2. 关闭帐号**不清** session（``_profile.php:404`` 把 ``Logout()`` 注释掉了）；
3. 重置密码**清** session（``_remindPassword`` 结尾 ``Logout()``），
   并把新密码打在页面上（本地不发信）；
4. 未登录访问需登录的四个动作 → 跳登录页；
5. 修改密码页的邮箱框**只读**（PHP ``_readonlyEditEmail``）；
6. 登录页**没有**"重复密码"框（PHP ``_disableEditPassword`` 那个分支）。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[3]
PYTHON_DIR = ROOT_DIR / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from web.bootstrap import install_paths  # noqa: E402

install_paths()

from core import membersql  # noqa: E402
from sqlitedb import FakeSource, SqliteConnection, SqliteCursor  # noqa: E402
from web.app import create_app  # noqa: E402
from web.services.admin_auth import SESSION_EMAIL_KEY, SESSION_MEMBER_KEY  # noqa: E402


class _Cursor(SqliteCursor):
    """``NOW()`` → ``CURRENT_TIMESTAMP``，``DELETE ... LIMIT 1`` 去掉 LIMIT。"""

    def execute(self, sql, params=()):
        sql = sql.replace("NOW()", "CURRENT_TIMESTAMP")
        if sql.lstrip().upper().startswith("DELETE"):
            sql = sql.replace(" LIMIT 1", "")
        super().execute(sql, params)


class _Connection(SqliteConnection):
    def cursor(self):
        return _Cursor(self._conn.cursor(), self.queries)


DDL = (
    """
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
    """,
    """
    CREATE TABLE `profile` (
      `id` INTEGER PRIMARY KEY ,
      `member_id` INTEGER NOT NULL UNIQUE ,
      `name` TEXT NOT NULL DEFAULT '' ,
      `phone` TEXT NOT NULL DEFAULT '' ,
      `address` TEXT NOT NULL DEFAULT '' ,
      `web` TEXT NOT NULL DEFAULT '' ,
      `signature` TEXT NOT NULL DEFAULT ''
    )
    """,
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

MEMBER_EMAIL = "demo@palmmicro.com"
MEMBER_PASSWORD = "demo1234"


class _Base(unittest.TestCase):
    def setUp(self):
        self.connection = _Connection()
        for ddl in DDL:
            self.connection._conn.execute(ddl)
        self.connection._conn.execute(
            "INSERT INTO member (id, email, password, register, login, status, "
            "activity, ip) VALUES (1, ?, ?, '2026-01-01', '2026-01-01', 1, 7, '')",
            (MEMBER_EMAIL, membersql.hash_password(MEMBER_PASSWORD)))
        self.connection._conn.commit()
        self.source = FakeSource(self.connection)
        self.app = create_app(
            {"TESTING": True, "SECRET_KEY": "test"},
            auth_source_factory=lambda: self.source,
        )
        self.client = self.app.test_client()

    # -- 助手 ---------------------------------------------------------------

    def session_data(self):
        with self.client.session_transaction() as session:
            return dict(session)

    def login(self):
        return self.client.post("/account/login",
                                data={"email": MEMBER_EMAIL,
                                      "password": MEMBER_PASSWORD})

    def member_row(self):
        return self.connection.single(
            "SELECT * FROM member WHERE id = 1")


class PageTest(_Base):
    def test_login_page_renders_without_confirm_field(self):
        response = self.client.get("/account/login")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("登录帐号", html)
        self.assertIn('name="password"', html)
        # 登录不要"重复密码"：PHP 的 _disableEditPassword 分支
        self.assertNotIn('name="confirm"', html)

    def test_register_page_has_confirm_field(self):
        html = self.client.get("/account/register").get_data(as_text=True)
        self.assertIn('name="confirm"', html)

    def test_password_page_email_is_readonly(self):
        with self.client.session_transaction() as session:
            session[SESSION_MEMBER_KEY] = "1"
            session[SESSION_EMAIL_KEY] = MEMBER_EMAIL
        html = self.client.get("/account/password").get_data(as_text=True)
        self.assertIn("readonly", html)
        self.assertIn('name="confirm"', html)

    def test_unknown_action_is_404(self):
        self.assertEqual(self.client.get("/account/nosuch").status_code, 404)

    def test_login_page_intro_links_to_local_register(self):
        """引导语里的链接指向本地页面 —— PHP 写的是 ``registercn.php``。"""
        html = self.client.get("/account/login").get_data(as_text=True)
        self.assertIn('href="/account/register"', html)
        self.assertIn('href="/account/reminder"', html)


class LoginRouteTest(_Base):
    def test_success_writes_session_and_redirects_to_profile(self):
        response = self.login()
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/account/profile"))
        data = self.session_data()
        self.assertEqual(data[SESSION_MEMBER_KEY], "1")
        self.assertEqual(data[SESSION_EMAIL_KEY], MEMBER_EMAIL)

    def test_success_records_login_ip(self):
        self.login()
        self.assertEqual(self.member_row()["ip"], "127.0.0.1")

    def test_failure_returns_400_with_message(self):
        response = self.client.post("/account/login",
                                    data={"email": MEMBER_EMAIL,
                                          "password": "wrong"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("请检查登录电子邮件帐号和密码",
                      response.get_data(as_text=True))
        self.assertEqual(self.session_data(), {})

    def test_failure_keeps_the_typed_email(self):
        """PHP 把上次输入存 ``$_SESSION['SESS_EMAIL_INPUT']`` 再回填。"""
        html = self.client.post(
            "/account/login",
            data={"email": MEMBER_EMAIL, "password": "wrong"}).get_data(as_text=True)
        self.assertIn('value="%s"' % MEMBER_EMAIL, html)


class GuardTest(_Base):
    def test_password_requires_login(self):
        response = self.client.get("/account/password")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/account/login", response.headers["Location"])

    def test_profile_requires_login(self):
        response = self.client.get("/account/profile")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/account/login", response.headers["Location"])

    def test_reminder_does_not_require_login(self):
        """忘了密码的人才用重置 —— PHP 那个 switch 里 reminder 不调 Auth()。"""
        self.assertEqual(self.client.get("/account/reminder").status_code, 200)


class ProfileRouteTest(_Base):
    def test_profile_shows_member_and_status(self):
        self.login()
        html = self.client.get("/account/profile").get_data(as_text=True)
        self.assertIn(MEMBER_EMAIL, html)
        self.assertIn("不接收任何邮件", html)   # status=1
        self.assertIn("活动指数: 7", html)

    def test_profile_shows_name_from_profile_table(self):
        """资料里的名字显示在"名字"那行；标题仍然是「我的资料」。

        标题走 ``GetWhoseDisplay()``：看**自己**时恒为「我的」（PHP
        ``GetMemberId() == GetLoginId()`` 那一支），所以这里**不该**出现
        「张三的资料」—— 那种标题只有管理员用 ``?email=`` 查别人才会有。
        """
        self.connection._conn.execute(
            "INSERT INTO profile (id, member_id, name, phone, address, web, "
            "signature) VALUES (1, 1, '张三', '', '', '', '')")
        self.connection._conn.commit()
        self.login()
        html = self.client.get("/account/profile").get_data(as_text=True)
        self.assertIn("名字: 张三", html)
        self.assertIn("我的资料", html)
        self.assertNotIn("张三的资料", html)

    def test_deleted_member_clears_session(self):
        """会员行被删（另一个标签页关了号）→ 清 session 并回登录页。"""
        with self.client.session_transaction() as session:
            session[SESSION_MEMBER_KEY] = "404"
        response = self.client.get("/account/profile")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.session_data().get(SESSION_MEMBER_KEY), None)


class LogoutTest(_Base):
    def test_logout_clears_session(self):
        self.login()
        response = self.client.get("/account/logout")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/account/login", response.headers["Location"])
        session = self.session_data()
        self.assertNotIn(SESSION_MEMBER_KEY, session)
        self.assertNotIn(SESSION_EMAIL_KEY, session)


class ReminderRouteTest(_Base):
    def test_success_logs_out_and_shows_new_password(self):
        """``_remindPassword`` 结尾 ``Logout()`` —— 已登录的人会被踢下线。"""
        self.login()
        response = self.client.post("/account/reminder",
                                    data={"email": MEMBER_EMAIL},
                                    follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("新密码：", html)
        # 新密码是 16 位十六进制，页面上那串要能真的登进去
        self.assertNotEqual(self.member_row()["password"], "")
        with self.client.session_transaction() as session:
            self.assertNotIn(SESSION_MEMBER_KEY, dict(session))

    def test_unregistered_email(self):
        response = self.client.post("/account/reminder",
                                    data={"email": "nobody@palmmicro.com"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("此电子邮件尚未注册", response.get_data(as_text=True))


class RegisterRouteTest(_Base):
    def test_register_logs_in_immediately(self):
        response = self.client.post(
            "/account/register",
            data={"email": "new@palmmicro.com", "password": "pw123456",
                  "confirm": "pw123456"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/account/profile", response.headers["Location"])
        data = self.session_data()
        self.assertEqual(data[SESSION_EMAIL_KEY], "new@palmmicro.com")
        self.assertIsNotNone(data.get(SESSION_MEMBER_KEY))

    def test_duplicate_email_blocked(self):
        response = self.client.post(
            "/account/register",
            data={"email": MEMBER_EMAIL, "password": "pw", "confirm": "pw"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("登录电子邮件已经注册", response.get_data(as_text=True))


class CloseAccountRouteTest(_Base):
    def test_close_self_keeps_session(self):
        """**照抄**：PHP 把 ``Logout()`` 注释掉了，关号后 session 还留着。

        表现是"下一次请求掉登录"（``GetLoginEmail()`` 查不到人），
        而不是立刻跳登录页 —— 这条用例钉的正是这个绕的行为。
        """
        self.login()
        response = self.client.post("/account/closeaccount",
                                    data={"email": MEMBER_EMAIL})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/account/login", response.headers["Location"])
        with self.client.session_transaction() as session:
            self.assertEqual(dict(session).get(SESSION_MEMBER_KEY), "1")
        self.assertIsNone(self.member_row())


class EditProfileRouteTest(_Base):
    def test_get_shows_form(self):
        self.login()
        html = self.client.get("/account/editprofile").get_data(as_text=True)
        self.assertIn('name="signature"', html)
        self.assertIn('value="1"', html)   # status=1 的 radio 选中

    def test_post_writes_profile_and_status(self):
        self.login()
        response = self.client.post(
            "/account/editprofile",
            data={"name": "张三", "phone": "12345678", "address": "上海",
                  "web": "http://example.com", "signature": "签名档",
                  "status": "2"})
        self.assertEqual(response.status_code, 302)
        profile = self.connection.single(
            "SELECT * FROM profile WHERE member_id = 1")
        self.assertEqual(profile["name"], "张三")
        self.assertEqual(profile["signature"], "签名档")
        self.assertEqual(self.member_row()["status"], 2)

    def test_post_requires_login(self):
        response = self.client.post("/account/editprofile", data={"name": "x"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/account/login", response.headers["Location"])


if __name__ == "__main__":
    unittest.main()
