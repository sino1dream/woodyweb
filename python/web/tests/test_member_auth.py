"""账号认证六个动作的测试（``account/php/_profile.php`` → ``auth_service.py``）。

**全部离线**：``core.membersql`` 的每个 SQL 函数都被替身接管。这一层要验的是
"校验顺序 / 错误码 / 落库用哪一个值"，而不是 SQL 本身（SQL 的文本与参数
在 ``test_membersql.py`` 里用记录型假连接验）。

## 为什么这六个动作值得单独一大组测试

它们是"改一行就可能悄悄错、错了还不容易发现"的那类代码：

* ````_loginAccount`` 判空用清洗值、比对用原值** —— 密码带反斜杠时两者不同；
* **注册成功自动登录**，漏了这一步用户注册完还要再登一次（不会报错）；
* **关闭帐号不清 session**（PHP 把 ``Logout()`` 注释掉了）—— 看起来像 bug，
  但页面行为依赖它；
* **重置密码取 md5 的后 16 位**，然后**再 md5 一次**落库 —— 少一层就是
  "新密码发出去却登不进来"。

每一条都有对应用例。
"""

from __future__ import annotations

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

from core import membersql  # noqa: E402
from web.services import auth_service as auth  # noqa: E402


class _Store:
    """内存会员表，够 ``membersql`` 的读函数用（写函数只记调用）。"""

    def __init__(self):
        self.members = {}
        self.profiles = {}
        self.calls = []
        self.next_id = 1

    # -- 造数据 -------------------------------------------------------------

    def add(self, email, password="", **fields):
        member = {
            "id": self.next_id,
            "email": email,
            "password": membersql.hash_password(password) if password else "",
            "register": "2026-01-01",
            "login": "2026-01-01",
            "status": 1,
            "activity": 0,
            "ip": "",
        }
        member.update(fields)
        self.members[email] = member
        self.next_id += 1
        return member

    def by_id(self, member_id):
        for member in self.members.values():
            if str(member["id"]) == str(member_id):
                return member
        return None

    def by_email(self, email):
        return self.members.get(email)


def _patch_store(store):
    """把 membersql 的 SQL 函数换成 ``_Store`` 的读写，返回 patch 列表。

    只替换"碰到数据库"的那些；``hash_password`` / ``sql_clean_string``
    保持真实现 —— 它们正是本组用例要验的口径。
    """
    def get_id_by_email(source, email):
        member = store.by_email(str(email))
        return None if member is None else str(member["id"])

    def get_member_by_id(source, member_id):
        member = store.by_id(member_id)
        return dict(member) if member else None

    def get_email_by_id(source, member_id):
        member = store.by_id(member_id)
        return None if member is None else member["email"]

    def exec_login(source, email, password):
        member = store.by_email(str(email))
        if member is None:
            return None
        if member["password"] != membersql.hash_password(password):
            return None
        return str(member["id"])

    def insert_member(source, email, password, ip=""):
        store.calls.append(("insert_member", email, password, ip))
        store.add(str(email), password, ip=ip)
        return True

    def update_password_field(source, email, password):
        store.calls.append(("update_password_field", email, password,
                            membersql.hash_password(password)))
        member = store.by_email(str(email))
        if member is None:
            return False
        member["password"] = membersql.hash_password(password)
        return True

    def update_login_email(source, member_id, email):
        store.calls.append(("update_login_email", member_id, email))
        member = store.by_id(member_id)
        if member is None:
            return False
        store.members.pop(member["email"], None)
        member["email"] = str(email)
        store.members[str(email)] = member
        return True

    def update_login_field(source, email, ip):
        store.calls.append(("update_login_field", email, ip))
        return True

    def get_profile_by_member_id(source, member_id):
        profile = store.profiles.get(str(member_id))
        return dict(profile) if profile else None

    def get_name_by_member_id(source, member_id):
        profile = store.profiles.get(str(member_id))
        if not profile:
            return None
        return profile.get("name") or None

    def delete_member(source, member_id):
        store.calls.append(("delete_member", member_id))
        return {"groups": 0, "profile": 0, "member": 1}

    targets = {
        "sql_get_id_by_email": get_id_by_email,
        "sql_get_member_by_id": get_member_by_id,
        "sql_get_email_by_id": get_email_by_id,
        "sql_exec_login": exec_login,
        "sql_insert_member": insert_member,
        "sql_update_password_field": update_password_field,
        "sql_update_login_email": update_login_email,
        "sql_update_login_field": update_login_field,
        "sql_get_profile_by_member_id": get_profile_by_member_id,
        "sql_get_name_by_member_id": get_name_by_member_id,
        "sql_delete_member": delete_member,
    }
    patchers = [mock.patch.object(membersql, name, value)
                for name, value in targets.items()]
    for patcher in patchers:
        patcher.start()
    return patchers


class _Base(unittest.TestCase):
    def setUp(self):
        self.store = _Store()
        self.patchers = _patch_store(self.store)
        self.addCleanup(self._stop)
        self.service = auth.AuthService(object(), ip="10.0.0.9")

    def _stop(self):
        for patcher in reversed(self.patchers):
            patcher.stop()

    def calls(self, name):
        return [call for call in self.store.calls if call[0] == name]


class LoginTest(_Base):
    def test_empty_input_returns_both_codes_and_does_not_query(self):
        """邮箱空 + 密码空 → 两个错误码，且**没有**去查库。

        PHP ``_loginAccount`` 的 ``if (EditEmailErrOcurred($arErrMsg)) return false;``
        挡在 ``Login()`` 之前 —— 少了这道，空密码会白跑一次查询。
        """
        result = self.service.login("", "")
        self.assertFalse(result.ok)
        self.assertEqual(result.errors, [auth.ERR_EMAIL_INPUT,
                                         auth.ERR_PASSWORD_INPUT])
        self.assertEqual(self.store.calls, [])

    def test_bad_email_format(self):
        result = self.service.login("not-an-email", "x")
        self.assertEqual(result.errors, [auth.ERR_EMAIL_INPUT])

    def test_wrong_password(self):
        self.store.add("demo@palmmicro.com", "demo1234")
        result = self.service.login("demo@palmmicro.com", "wrong")
        self.assertFalse(result.ok)
        self.assertEqual(result.errors, [auth.ERR_LOGIN_FAILED])

    def test_success_writes_session_member_and_login_fields(self):
        member = self.store.add("demo@palmmicro.com", "demo1234")
        result = self.service.login("demo@palmmicro.com", "demo1234")
        self.assertTrue(result.ok)
        self.assertEqual(result.member_id, str(member["id"]))
        # 登录成功文案被 PHP 吞掉（_echoAccountProfileMsg 把
        # PROFILE_LOGIN_ACCOUNT 置成 false），所以这里是空串
        self.assertEqual(result.message, "")
        self.assertEqual(self.calls("update_login_field"),
                         [("update_login_field", "demo@palmmicro.com",
                           "10.0.0.9")])

    def test_email_is_trimmed_but_password_is_not(self):
        """邮箱走 ``SqlCleanString``（trim + stripslashes），密码原值比对。

        PHP: ``$strEmail = SqlCleanString($_POST['login'])`` 之后才查库，
        而 ``Login($strEmail, $_POST['password'])`` 用原值。所以邮箱带空格
        能登录、密码带空格不能。
        """
        self.store.add("demo@palmmicro.com", "demo1234")
        ok = self.service.login("  demo@palmmicro.com  ", "demo1234")
        self.assertTrue(ok.ok)
        bad = self.service.login(" demo@palmmicro.com ", " demo1234")
        self.assertFalse(bad.ok)
        self.assertEqual(bad.errors, [auth.ERR_LOGIN_FAILED])

    def test_password_with_trailing_backslash_uses_raw_value(self):
        """密码 ``abc\\`` 的反斜杠：判空/比对都按原值，不当成空串。

        这是 ``stripslashes`` 那个坑的边界 —— 清洗后是 ``abc``（非空），
        原值是 ``abc\\``。Login 传原值，所以能对上库里 ``abc\\`` 的 md5。
        """
        self.store.add("demo@palmmicro.com", "abc\\")
        result = self.service.login("demo@palmmicro.com", "abc\\")
        self.assertTrue(result.ok, "原值比对应该成功")


class RegisterTest(_Base):
    def test_duplicate_email(self):
        self.store.add("demo@palmmicro.com", "demo1234")
        result = self.service.register("demo@palmmicro.com", "x", "x")
        self.assertIn(auth.ERR_EMAIL_REGISTERED, result.errors)

    def test_password_mismatch(self):
        result = self.service.register("new@palmmicro.com", "aaa", "bbb")
        self.assertEqual(result.errors, [auth.ERR_PASSWORD_MISMATCH])

    def test_empty_confirm(self):
        result = self.service.register("new@palmmicro.com", "aaa", "")
        self.assertIn(auth.ERR_PASSWORD2_INPUT, result.errors)

    def test_success_registers_and_auto_logs_in(self):
        """注册成功 → 写库 + 发欢迎信 + **自动登录**（拿到 member_id）。

        自动登录这一步最容易漏：PHP 最后一行是
        ``$this->Login($strEmail, $_POST['password']);``，漏了用户注册完
        还是未登录状态，页面上什么都不会提示。
        """
        result = self.service.register("new@palmmicro.com", "pw123456",
                                       "pw123456")
        self.assertTrue(result.ok)
        self.assertEqual(result.message, auth.MESSAGE_NEW_ACCOUNT)
        self.assertIsNotNone(result.member_id)
        self.assertEqual(self.calls("insert_member"),
                         [("insert_member", "new@palmmicro.com", "pw123456",
                           "10.0.0.9")])
        self.assertEqual(result.payload["mail"]["to"], "new@palmmicro.com")

    def test_same_errors_when_both_password_empty(self):
        result = self.service.register("new@palmmicro.com", "", "")
        self.assertEqual(result.errors, [auth.ERR_PASSWORD_INPUT,
                                         auth.ERR_PASSWORD2_INPUT])


class ChangePasswordTest(_Base):
    def setUp(self):
        super().setUp()
        self.member = self.store.add("demo@palmmicro.com", "old1234")

    def test_same_password_rejected(self):
        result = self.service.change_password(self.member["id"], "old1234",
                                              "old1234")
        self.assertEqual(result.errors, [auth.ERR_PASSWORD_UNCHANGED])
        self.assertEqual(self.calls("update_password_field"), [])

    def test_mismatch(self):
        result = self.service.change_password(self.member["id"], "aaa", "bbb")
        self.assertEqual(result.errors, [auth.ERR_PASSWORD_MISMATCH])

    def test_success_writes_new_password(self):
        result = self.service.change_password(self.member["id"], "new5678",
                                              "new5678")
        self.assertTrue(result.ok)
        self.assertEqual(result.message, auth.MESSAGE_PASSWORD_CHANGED)
        self.assertEqual(self.calls("update_password_field"),
                         [("update_password_field", "demo@palmmicro.com",
                           "new5678", membersql.hash_password("new5678"))])

    def test_validation_uses_cleaned_value_but_write_uses_raw(self):
        """**照抄的那处不一致**：判"新旧相同"用清洗值，写库用原值。

        构造：库里存的是 ``old\\1234``（**一个**反斜杠），用户输入
        ``old\\\\1234``（两个）。``stripslashes`` 把 ``\\\\`` 合成一个，
        于是清洗值与库里的 md5 相同 → 判定"新旧没有区别"，直接拒绝。
        而如果库里不是这个值，写进去的会是**原值**（两个反斜杠）的 md5
        —— 见下一个用例。

        注意末尾单个反斜杠**不会**被 ``stripslashes`` 剥掉
        （PHP 只处理 ``\\\\`` / ``\\'`` / ``\\"`` / ``\\0`` 这几种），
        所以 ``"old1234\\"`` 那种输入清洗前后是一样的。
        """
        self.store.members["demo@palmmicro.com"]["password"] = (
            membersql.hash_password("old\\1234"))
        result = self.service.change_password(self.member["id"], "old\\\\1234",
                                              "old\\\\1234")
        self.assertEqual(result.errors, [auth.ERR_PASSWORD_UNCHANGED])
        self.assertEqual(self.calls("update_password_field"), [])

    def test_write_is_raw_when_cleaned_value_differs(self):
        """库里是别的值 → 走到写库，写进去的必须是**原值**的 md5。"""
        raw = "brand\\\\new"          # 两个反斜杠
        cleaned = "brand\\new"        # stripslashes 之后剩一个
        result = self.service.change_password(self.member["id"], raw, raw)
        self.assertTrue(result.ok)
        _, _, written_raw, hashed = self.calls("update_password_field")[0]
        self.assertEqual(written_raw, raw)
        self.assertEqual(hashed, membersql.hash_password(raw))
        self.assertNotEqual(hashed, membersql.hash_password(cleaned))


class UpdateEmailTest(_Base):
    def setUp(self):
        super().setUp()
        self.member = self.store.add("demo@palmmicro.com", "demo1234")
        self.store.add("other@palmmicro.com", "x")

    def test_same_email(self):
        """改成**自己**的邮箱会同时得到两个码：已注册 + 没有区别。

        这是 PHP 的一处瑕疵：``_updateLoginEmail()`` 先无条件
        ``if (SqlGetIdByEmail($strEmail))``，而自己的邮箱当然查得到自己，
        于是先加 ``ACCT_ERR_EMAIL_REGISTERED``，再在下一行加
        ``ACCT_ERR_EMAIL_UNCHANGED``。用户看到的是"已经注册"顶在
        "没有区别"前面 —— **照抄**，不在这里替它排除自己
        （排除之后页面文案就与生产端不一样了）。
        """
        result = self.service.update_login_email(self.member["id"],
                                                 "demo@palmmicro.com")
        self.assertEqual(result.errors, [auth.ERR_EMAIL_REGISTERED,
                                         auth.ERR_EMAIL_UNCHANGED])

    def test_taken_email(self):
        result = self.service.update_login_email(self.member["id"],
                                                 "other@palmmicro.com")
        self.assertEqual(result.errors, [auth.ERR_EMAIL_REGISTERED])

    def test_bad_format_only_one_code(self):
        """格式非法时只出 ``EMAIL_INPUT`` —— PHP 把"已注册"判断套在
        ``if (filter_var_email(...))`` 里面，格式错就根本不查重。"""
        result = self.service.update_login_email(self.member["id"], "bad")
        self.assertEqual(result.errors, [auth.ERR_EMAIL_INPUT])

    def test_success_returns_new_email_in_payload(self):
        result = self.service.update_login_email(self.member["id"],
                                                 "fresh@palmmicro.com")
        self.assertTrue(result.ok)
        self.assertEqual(result.message, auth.MESSAGE_EMAIL_CHANGED)
        self.assertEqual(result.payload["email"], "fresh@palmmicro.com")


class RemindPasswordTest(_Base):
    def test_unregistered(self):
        result = self.service.remind_password("nobody@palmmicro.com")
        self.assertEqual(result.errors, [auth.ERR_EMAIL_UNREGISTERED])

    def test_new_password_is_16_chars_and_stored_md5_of_it(self):
        """新密码 = ``md5(email + HH:MM:SS)`` 的后 16 位；库里存它的 md5。

        两层哈希少一层就是"信里给了密码却登不进来"，这条用例专门钉它。
        """
        self.store.add("demo@palmmicro.com", "old")
        fixed = __import__("datetime").datetime(
            2026, 11, 2, 9, 30, 15, tzinfo=auth.DEBUG_TIME_ZONE_OFFSET)
        service = auth.AuthService(object(), clock=lambda: fixed)
        result = service.remind_password("demo@palmmicro.com")
        self.assertTrue(result.ok)
        expected = membersql.hash_password("demo@palmmicro.com09:30:15")[16:]
        self.assertEqual(len(expected), 16)
        self.assertEqual(result.payload["password"], expected)
        _, email, raw, hashed = self.calls("update_password_field")[0]
        self.assertEqual(raw, expected)
        self.assertEqual(hashed, membersql.hash_password(expected))

    def test_mail_carries_the_new_password(self):
        self.store.add("demo@palmmicro.com", "old")
        result = self.service.remind_password("demo@palmmicro.com")
        self.assertIn(result.payload["password"], result.payload["mail"]["text"])


class CloseAccountTest(_Base):
    def setUp(self):
        super().setUp()
        self.me = self.store.add("demo@palmmicro.com", "demo1234")
        self.other = self.store.add("other@palmmicro.com", "x")

    def test_close_self_allowed(self):
        result = self.service.close_account("demo@palmmicro.com",
                                            str(self.me["id"]))
        self.assertTrue(result.ok)
        self.assertEqual(result.message, auth.MESSAGE_CLOSE_ACCOUNT)
        self.assertEqual(self.calls("delete_member"),
                         [("delete_member", str(self.me["id"]))])

    def test_close_other_needs_admin(self):
        result = self.service.close_account("other@palmmicro.com",
                                            str(self.me["id"]))
        self.assertEqual(result.errors, [auth.ERR_UNAUTH_OP])
        self.assertEqual(self.calls("delete_member"), [])

    def test_admin_can_close_other(self):
        service = auth.AuthService(object(), is_admin_member=lambda: True)
        result = service.close_account("other@palmmicro.com", str(self.me["id"]))
        self.assertTrue(result.ok)
        self.assertEqual(self.calls("delete_member"),
                         [("delete_member", str(self.other["id"]))])

    def test_no_session_counts_as_other(self):
        """没登录（``current_member_id()`` 为 None）→ 相当于删别人 → 要管理员。"""
        result = self.service.close_account("other@palmmicro.com", None)
        self.assertEqual(result.errors, [auth.ERR_UNAUTH_OP])

    def test_unregistered(self):
        result = self.service.close_account("nobody@palmmicro.com",
                                            str(self.me["id"]))
        self.assertEqual(result.errors, [auth.ERR_EMAIL_UNREGISTERED])


class ValidateEmailTest(unittest.TestCase):
    def test_accepted(self):
        for text in ("a@b.com", "first.last+tag@sub.example.cn",
                     "x@localhost", "user@[192.168.1.1]"):
            with self.subTest(email=text):
                self.assertTrue(auth.is_valid_email(text))

    def test_rejected(self):
        for text in ("", "abc", "a@", "@b.com", "a@b..com", "a b@c.com",
                     "a@@b.com", " a@b.com", "a@b.com ", "a@-b.com"):
            with self.subTest(email=text):
                self.assertFalse(auth.is_valid_email(text))


class WhoseDisplayTest(_Base):
    def test_self(self):
        member = self.store.add("demo@palmmicro.com", "x")
        self.assertEqual(
            auth.get_whose_display(object(), member["id"], str(member["id"])),
            "我的")

    def test_other_uses_profile_name(self):
        other = self.store.add("other@palmmicro.com", "x")
        self.store.profiles[str(other["id"])] = {"name": "张三"}
        self.assertEqual(
            auth.get_whose_display(object(), other["id"], "999"), "张三的")

    def test_other_falls_back_to_email(self):
        other = self.store.add("other@palmmicro.com", "x")
        self.assertEqual(
            auth.get_whose_display(object(), other["id"], "999"),
            "other@palmmicro.com的")

    def test_shows_bare_de_zhang_when_nothing_found(self):
        """名字与邮箱都取不到时只留一个「的」—— PHP 拼的是 ``false.'的'``。"""
        self.assertEqual(auth.get_whose_display(object(), "404", "999"), "的")


class CleanStringTest(unittest.TestCase):
    def test_trim_and_stripslashes(self):
        self.assertEqual(membersql.sql_clean_string("  abc  "), "abc")
        self.assertEqual(membersql.sql_clean_string("a\\'b"), "a'b")
        self.assertEqual(membersql.sql_clean_string('a\\"b'), 'a"b')
        self.assertEqual(membersql.sql_clean_string("a\\\\b"), "a\\b")

    def test_other_backslash_kept(self):
        """``\\n`` 这种转义序列**不**被 stripslashes 处理（PHP 只处理三四种）。"""
        self.assertEqual(membersql.sql_clean_string("a\\nb"), "a\\nb")

    def test_none_becomes_empty(self):
        self.assertEqual(membersql.sql_clean_string(None), "")


class HashPasswordTest(unittest.TestCase):
    def test_matches_php_md5(self):
        # 用 PHP 那边算好的值（demo1234）钉住编码口径
        self.assertEqual(membersql.hash_password("demo1234"),
                         "6e9bece1914809fb8493146417e722f6")

    def test_none_is_empty_string_hash(self):
        self.assertEqual(membersql.hash_password(None),
                         membersql.hash_password(""))


if __name__ == "__main__":
    unittest.main()
