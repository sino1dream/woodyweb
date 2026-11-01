"""身份判定闸门的测试（``Account::IsAdmin()`` → ``web/services/admin_auth.py``）。

这一层的核心不是"怎么判管理员"（那就是一句字符串比较），而是
**"没有身份时算不算管理员"**：

* 账号体系没启用（默认）→ 算。理由是本地的写入口测试与冒烟脚本都依赖
  "开箱即管理员"，而那时根本没有登录页；
* 账号体系启用（``PALMMICRO_AUTH=1`` / ``AUTH_ENABLED``）→ **不算**。
  不这样的话，浏览器清掉 cookie 就当场提权 —— 登录页存在的意义就没了。

第二条是这一层真正的风险点，所以单独一个文件钉死它。
"""

from __future__ import annotations

import os
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
from web.services import admin_auth  # noqa: E402
from web.services.admin_auth import (  # noqa: E402
    ADMIN_EMAIL,
    ENV_ADMIN_EMAIL,
    ENV_AUTH_ENABLED,
    SESSION_EMAIL_KEY,
    SESSION_MEMBER_KEY,
)


class _Base(unittest.TestCase):
    def setUp(self):
        for name in (ENV_ADMIN_EMAIL, ENV_AUTH_ENABLED):
            patcher = mock.patch.dict(os.environ, {}, clear=False)
            patcher.start()
            self.addCleanup(patcher.stop)
            os.environ.pop(name, None)

    def app(self, **config):
        config.setdefault("TESTING", True)
        config.setdefault("SECRET_KEY", "test")
        return create_app(config)

    def client(self, **config):
        return self.app(**config).test_client()


class DefaultDemoTest(_Base):
    def test_no_session_is_admin_when_auth_disabled(self):
        """默认（账号体系没启用）→ 演示管理员身份。"""
        app = self.app()
        with app.test_request_context("/"):
            self.assertTrue(admin_auth.is_admin())

    def test_other_email_in_session_is_not_admin(self):
        """闸门是真的：session 里放别人的邮箱立刻降级（不是恒 True 的桩）。"""
        app = self.app()
        with app.test_request_context("/") as ctx:
            ctx.session[SESSION_EMAIL_KEY] = "someone@example.com"
            self.assertFalse(admin_auth.is_admin())

    def test_admin_email_in_session_is_admin(self):
        app = self.app()
        with app.test_request_context("/") as ctx:
            ctx.session[SESSION_EMAIL_KEY] = ADMIN_EMAIL
            self.assertTrue(admin_auth.is_admin())

    def test_env_override_wins_over_demo_default(self):
        os.environ[ENV_ADMIN_EMAIL] = "someone@example.com"
        app = self.app()
        with app.test_request_context("/"):
            self.assertFalse(admin_auth.is_admin())


class AuthEnabledTest(_Base):
    def test_no_session_is_not_admin_when_enabled(self):
        """账号体系启用 → 没有 session 就不是管理员（防清 cookie 提权）。"""
        app = self.app(AUTH_ENABLED=True)
        client = app.test_client()
        with app.test_request_context("/"):
            self.assertEqual(admin_auth.current_admin_email(), "")
            self.assertFalse(admin_auth.is_admin())

    def test_session_email_still_wins(self):
        app = self.app(AUTH_ENABLED=True)
        with app.test_request_context("/") as ctx:
            ctx.session[SESSION_EMAIL_KEY] = "someone@example.com"
            self.assertFalse(admin_auth.is_admin())
            ctx.session[SESSION_EMAIL_KEY] = ADMIN_EMAIL
            self.assertTrue(admin_auth.is_admin())

    def test_env_override_still_works_when_enabled(self):
        """启用之后环境变量仍是最后一道口子（本地排查权限用）。"""
        os.environ[ENV_ADMIN_EMAIL] = ADMIN_EMAIL
        app = self.app(AUTH_ENABLED=True)
        with app.test_request_context("/"):
            self.assertTrue(admin_auth.is_admin())

    def test_env_variable_turns_it_on(self):
        os.environ[ENV_AUTH_ENABLED] = "1"
        app = self.app()
        with app.test_request_context("/"):
            self.assertFalse(admin_auth.is_admin())

    def test_env_variable_false_values(self):
        for value in ("0", "off", "no", ""):
            with self.subTest(value=value):
                os.environ[ENV_AUTH_ENABLED] = value
                app = self.app()
                with app.test_request_context("/"):
                    self.assertTrue(admin_auth.is_admin(),
                                    "AUTH 未启用时应回落到演示管理员")


class MemberIdTest(_Base):
    def test_none_without_session(self):
        app = self.app()
        with app.test_request_context("/"):
            self.assertIsNone(admin_auth.current_member_id())

    def test_reads_sess_id(self):
        app = self.app()
        with app.test_request_context("/") as ctx:
            ctx.session[SESSION_MEMBER_KEY] = "7"
            self.assertEqual(admin_auth.current_member_id(), "7")

    def test_blank_counts_as_none(self):
        """PHP ``trim($strMemberId) == ''`` 也算没登录。"""
        app = self.app()
        with app.test_request_context("/") as ctx:
            ctx.session[SESSION_MEMBER_KEY] = "   "
            self.assertIsNone(admin_auth.current_member_id())


class SessionKeyTest(unittest.TestCase):
    def test_keys_match_php(self):
        """键名照抄 PHP：``$_SESSION['SESS_ID']`` 与 ``$_SESSION['email']``。

        换掉 ``SESS_ID`` 会让"从 PHP 带过来的会话"认不出来（同域名部署时
        两个应用共用一个 session 存储的场合），也没有任何好处。
        """
        self.assertEqual(SESSION_MEMBER_KEY, "SESS_ID")
        self.assertEqual(SESSION_EMAIL_KEY, "email")


if __name__ == "__main__":
    unittest.main()
