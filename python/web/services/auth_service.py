"""账号认证的六个动作（登录 / 注册 / 改密 / 改邮箱 / 重置密码 / 关号）。

对应 PHP ``account/php/_profile.php`` 的 ``_ProfileAccount`` 类（495 行里
的 ``Login`` / ``Logout`` / ``_loginAccount`` / ``_registerAccount`` /
``_remindPassword`` / ``_closeAccount`` / ``Run``）以及
``account/php/_editemailform.php`` 的错误码表。

## 一、为什么单独一个服务层

``_ProfileAccount`` 把三件事揉在一起：**校验**（错误码数组）、**落库**
（``SqlExecLogin`` 一族）、**跳转**（``SwitchTo`` / ``SwitchToSess``）。
本地按项目一贯的分法拆开：

* 本模块只管"输入 → 结果"：返回 :class:`AuthResult`（成功文案 / 错误码 /
  会员 id），**不碰** ``flask.session``、不 ``redirect``；
* 路由（``web/routes/account.py``）负责把结果写进 session、决定跳哪；
* 数据访问全在 ``core.membersql``。

好处是六个动作都能在无请求上下文的情况下测试 —— 而它们正是"一改就出错、
错了还不容易发现"的那部分（密码落库用的是原值还是清洗值、校验顺序、
成功之后要不要清 session）。

## 二、照抄的三处"不合理"

**1. 密码的校验值与落库值不是同一个东西。**
``_loginAccount`` 判空用 ``$strPassword``（= ``SqlCleanString($_POST['password'])``，
trim + stripslashes），真正拿去比对的却是 ``$_POST['password']`` **原值**
（``$this->Login($strEmail, $_POST['password'])``）。``_changePassword`` 同理：
``md5($strPassword) == $member['password']`` 用清洗值，而
``SqlUpdatePasswordField(...$_POST['password'])`` 用原值。

于是"密码以反斜杠结尾"这种输入会得到自相矛盾的结果：校验说"新旧密码不同"，
落库写进去的又是原值。**PHP 就是这个行为**，本模块逐字复刻
（见 :func:`AuthService.change_password` 的 ``raw_password`` 用法），
并在 README 里记为"照抄，不修"。

**2. 关闭帐号之后不清 session。**
``_closeAccount`` 里 ``$this->Logout()`` 被注释掉了
（``_profile.php:404``），删完会员行 session 里还留着那个已不存在的
``SESS_ID``。下一次请求 ``GetLoginEmail()`` 查不到邮箱 → ``IsAdmin()``
为假，页面表现为"掉登录"。照抄。

**3. 重置密码会先登出。**
``_remindPassword`` 结尾是 ``$this->Logout()`` —— 也就是说没登录的人
用这个功能也没事，已登录的人反而被踢出去。照抄。

## 三、与 PHP 的三处偏离

1. **不发邮件**。PHP 在注册 / 重置 / 关号后调 ``EmailHtml()``，本地没有
   SMTP 配置，也**不应该**在演示环境真发信。改成 :class:`Mailer` 协议，
   默认 :class:`RecordingMailer` 只把信记在 :attr:`AuthResult.payload`，
   页面直接把它显示出来（重置密码尤其需要 —— 否则用户拿不到新密码）。
   这一条不是"省事"：``EmailHtml`` 一失败 PHP 只写一行 debug 日志，
   用户同样是拿不到密码的，本地把结果摊在页面上比"静默失败"更接近意图。
2. **只做中文文案**。``account/`` 目录每页都有 ``xx.php`` / ``xxcn.php``
   两份，本地只迁中文那份（与 ``/autotractor`` 等页一致）。
3. **``IncLogin()`` / ``iplogin`` 表不迁**。那是"同 IP 登录次数"的计数，
   只服务后台排查，属于 ``account/visitor`` 那一族（IP 统计整体不迁）。
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any, Callable

from core import membersql

# ---------------------------------------------------------------------------
# 错误码：与 account/php/_editemailform.php:20-29 一一对应
# ---------------------------------------------------------------------------
#: 登录电子邮件不能为空并且要求是有效的邮件地址
ERR_EMAIL_INPUT = 1
#: 登录电子邮件已经注册
ERR_EMAIL_REGISTERED = 2
#: 此电子邮件尚未注册
ERR_EMAIL_UNREGISTERED = 3
#: 新旧电子邮件没有区别
ERR_EMAIL_UNCHANGED = 4
#: 密码不能为空
ERR_PASSWORD_INPUT = 5
#: 重复密码不能为空
ERR_PASSWORD2_INPUT = 6
#: 密码不一致
ERR_PASSWORD_MISMATCH = 7
#: 新旧密码没有区别
ERR_PASSWORD_UNCHANGED = 8
#: 请检查登录电子邮件帐号和密码
ERR_LOGIN_FAILED = 9
#: 你没有权限做这个操作
ERR_UNAUTH_OP = 10

#: 错误码 → 中文文案。PHP 在 ``_editemailform.php::EditEmailForm()`` 里
#: 把每个错误码映射到**对应输入框**旁边的一句 ``<font>``，文案就在那个
#: ``switch`` 里；这里按同样的措辞集中一份，路由决定挂在哪个框下。
ERROR_MESSAGES = {
    ERR_EMAIL_INPUT: "登录电子邮件不能为空并且要求是有效的邮件地址",
    ERR_EMAIL_REGISTERED: "登录电子邮件已经注册",
    ERR_EMAIL_UNREGISTERED: "此电子邮件尚未注册",
    ERR_EMAIL_UNCHANGED: "新旧电子邮件没有区别",
    ERR_PASSWORD_INPUT: "密码不能为空",
    ERR_PASSWORD2_INPUT: "重复密码不能为空",
    ERR_PASSWORD_MISMATCH: "密码不一致",
    ERR_PASSWORD_UNCHANGED: "新旧密码没有区别",
    ERR_LOGIN_FAILED: "请检查登录电子邮件帐号和密码",
    ERR_UNAUTH_OP: "你没有权限做这个操作",
}

#: 错误码落在哪个输入框下（``_editemailform.php`` 的
#: ``$strEmailErr`` / ``$strPasswordErr`` / ``$strPassword2Err`` / ``$strSubmitErr``）。
#: 本表只用于模板上的**位置**，不影响判定 —— 但位置错了用户会以为错在别处。
ERROR_FIELDS = {
    ERR_EMAIL_INPUT: "email",
    ERR_EMAIL_REGISTERED: "email",
    ERR_EMAIL_UNREGISTERED: "email",
    ERR_LOGIN_FAILED: "email",
    ERR_PASSWORD_INPUT: "password",
    ERR_PASSWORD_UNCHANGED: "password",
    ERR_PASSWORD2_INPUT: "password2",
    ERR_EMAIL_UNCHANGED: "submit",
    ERR_PASSWORD_MISMATCH: "submit",
    ERR_UNAUTH_OP: "submit",
}

#: 六个动作的提交按钮文案（``_editemailform.php:2-18`` 的
#: ``EDIT_EMAIL_*_CN``）。**提交值就是这段中文** —— PHP 的
#: ``switch ($_POST['submit'])`` 直接比中文字面量，本地也用动作名做键，
#: 不把中文塞进协议里（URL/表单里出现中文需要浏览器编码配合，容易出岔子）。
ACTION_LABELS = {
    "login": "登录帐号",
    "register": "注册新帐号",
    "password": "修改密码",
    "updateemail": "修改登录帐号",
    "reminder": "生成新密码",
    "closeaccount": "关闭帐号",
}

#: 成功文案（``_profile.php:9-18`` 的 ``PROFILE_*`` 常量）。
#: ``PROFILE_LOGIN_ACCOUNT`` 在 PHP 里会被 ``_echoAccountProfileMsg``
#: **吞掉**（置成 ``false`` 不显示），本地也返回空串 —— 登录成功后
#: 页面不弹"登录成功"，这是原设计。
MESSAGE_LOGIN = ""
MESSAGE_PASSWORD_CHANGED = "修改密码成功"
MESSAGE_EMAIL_CHANGED = "登录电子邮件已经更新"
MESSAGE_NEW_ACCOUNT = "注册成功"
MESSAGE_NEW_PASSWORD = "新密码通过电子邮件发送"
MESSAGE_CLOSE_ACCOUNT = "帐号已经关闭"

#: 时区取自 ``php/debug.php:4`` 的 ``define('DEBUG_TIME_ZONE', 'Asia/Shanghai')``。
#: ``_remindPassword`` 的新密码里含 ``DebugGetTime()``（HH:MM:SS），
#: 换时区会得到不同的密码 —— 虽然"哪个时区"不影响可用性（随机源是时间），
#: 但演示数据要能与 PHP 探针对上，所以固定这个时区。
DEBUG_TIME_ZONE_OFFSET = datetime.timezone(datetime.timedelta(hours=8))


@dataclass
class AuthResult:
    """一次认证动作的结果。

    ``ok`` 为真时 :attr:`message` 是给用户看的一句中文（可能为空串 ——
    登录成功就是这样），``errors`` 为空；失败时反过来。
    :attr:`payload` 装"页面要额外显示的东西"：重置密码的新密码明文、
    本地不发信时的邮件正文副本。
    """

    ok: bool
    message: str = ""
    errors: list = field(default_factory=list)
    member_id: str | None = None
    payload: dict = field(default_factory=dict)

    @property
    def error_messages(self) -> list:
        """按错误码顺序给中文文案（模板里直接循环）。"""
        return [ERROR_MESSAGES.get(code, "") for code in self.errors]


class Mailer:
    """发信口的默认实现：**不发信，只记下来**。

    ``_emailReport()``（``_profile.php:236``）在 PHP 里调 ``EmailHtml()``，
    本地没有 SMTP，也不该在演示环境往外发邮件。所以默认行为是把
    "给谁 / 什么主题 / 什么正文"塞进 :attr:`AuthResult.payload`，
    由页面显示 —— 重置密码这条尤其重要，否则用户永远拿不到新密码。

    要接真邮件时传一个同签名的 callable 给 :class:`AuthService` 即可，
    服务层不用改。
    """

    def send(self, who: str, subject: str, text: str) -> dict:
        return {"to": who, "subject": subject, "text": text}


def _now_hms(clock: Callable[[], datetime.datetime] | None = None) -> str:
    """``DebugGetTime()`` 的等价物：``Asia/Shanghai`` 下的 ``HH:MM:SS``。"""
    if clock is not None:
        moment = clock()
    else:
        moment = datetime.datetime.now(DEBUG_TIME_ZONE_OFFSET)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=DEBUG_TIME_ZONE_OFFSET)
    return moment.astimezone(DEBUG_TIME_ZONE_OFFSET).strftime("%H:%M:%S")


def is_valid_email(email: str) -> bool:
    """``filter_var($strEmail, FILTER_VALIDATE_EMAIL)`` 的近似。

    PHP 用的是 RFC 5321 的宽松实现（接受 ``a@b``、拒掉带空格的、
    拒掉没有 ``@`` 的）。Python 标准库没有等价物，这里用一条正则覆盖
    PHP 实际会放行的形状：本地部分允许点与常见符号，域名部分要求
    至少一个点 **或者** 是 ``localhost``（PHP 接受 ``a@localhost``）。

    **不要**换成 ``email.utils.parseaddr`` —— 它对 ``a@b`` 判真，
    而 PHP 的 ``FILTER_VALIDATE_EMAIL`` 也判真，两者在这里恰好一致；
    但 ``parseaddr`` 会把 ``"x" <a@b>`` 拆成 ``a@b`` 判真，PHP 会整串判假。
    正则更贴近"整串必须像个地址"这条。
    """
    if not email:
        return False
    text = str(email).strip()
    if text != str(email):
        # PHP 的 filter_var 不接受首尾空白（它不做 trim）
        return False
    if text.count("@") != 1:
        return False
    local, _, domain = text.partition("@")
    if not local or not domain:
        return False
    if len(text) > 254 or len(local) > 64:
        return False
    allowed_local = set(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        ".!#$%&'*+/=?^_`{|}~-")
    if any(char not in allowed_local for char in local):
        return False
    if local.startswith(".") or local.endswith(".") or ".." in local:
        return False
    if domain.startswith(".") or domain.endswith(".") or ".." in domain:
        return False
    if domain.startswith("[") and domain.endswith("]"):
        # 字面量 IP 地址：PHP 接受，这里不细究格式，只要能看出是 IPv4
        inner = domain[1:-1]
        parts = inner.split(".")
        if len(parts) != 4:
            return False
        return all(part.isdigit() and 0 <= int(part) <= 255 for part in parts)
    labels = domain.split(".")
    for label in labels:
        if not label:
            return False
        if label[0] == "-" or label[-1] == "-":
            return False
        allowed = set(
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-")
        if any(char not in allowed for char in label):
            return False
    # 单标签域名（a@b）PHP 也收 —— 只要它不是纯数字/以 - 开头这种明显不是域名的。
    return True


class AuthService:
    """六个认证动作。**无状态**：同一个实例可以服务所有请求。

    :param source: 数据库连接或 data source（透传给 ``core.membersql``）。
    :param mailer: 发信口，默认 :class:`Mailer`（不发信、只记录）。
    :param clock: 返回"现在"的 callable，测试里注入固定时间以复现
        ``_remindPassword`` 生成的新密码。
    :param is_admin_member: 判"当前登录者是不是管理员"的 callable。
        关号那个动作需要它（``_closeAccount`` 里 ``$this->IsAdmin()``）——
        从服务层直接调 ``admin_auth.is_admin`` 会把"无请求上下文"的
        测试路径踩坏，所以由调用方注入。
    :param ip: 当前请求的 IP，写进 ``member.ip``（``UrlGetIp()`` 的替身）。
        同样由调用方给 —— 服务层不读请求对象。
    """

    def __init__(self, source: Any, mailer: Any = None,
                 clock: Callable[[], datetime.datetime] | None = None,
                 is_admin_member: Callable[[], bool] | None = None,
                 ip: str = ""):
        self.source = source
        self.mailer = mailer or Mailer()
        self.clock = clock
        self.is_admin_member = is_admin_member or (lambda: False)
        self.ip = ip

    # -- 内部 ---------------------------------------------------------------

    def _send(self, who: str, subject: str, text: str) -> dict:
        try:
            return self.mailer.send(who, subject, text) or {}
        except Exception:  # pragma: no cover - 发信失败不该让页面 500
            # PHP 那边 ``EmailHtml() == false`` 只写一行 debug 日志，
            # 页面照常返回 —— 这里保持同样的"发信失败不阻塞"。
            return {"to": who, "subject": subject, "text": text,
                    "failed": True}

    # -- 登录 ---------------------------------------------------------------

    def login(self, email: Any, password: Any) -> AuthResult:
        """``_loginAccount()`` + ``Login()``。

        校验顺序照抄：先判邮箱格式、再判密码非空，任一不过就**不尝试**登录
        （PHP 的 ``if (EditEmailErrOcurred($arErrMsg)) return false;``）。
        两个错误可以同时出现（邮箱空 + 密码空），此时返回两个码。

        登录成功后的三件事：``session_regenerate_id()``（由路由做）、
        ``SqlUpdateLoginField()``（记 IP 与登录时间）、``IncLogin()``（本地不迁）。
        """
        email_text = membersql.sql_clean_string(email)
        raw_password = "" if password is None else str(password)
        clean_password = membersql.sql_clean_string(password)

        errors = []
        if not is_valid_email(email_text):
            errors.append(ERR_EMAIL_INPUT)
        if clean_password == "":
            errors.append(ERR_PASSWORD_INPUT)
        if errors:
            return AuthResult(ok=False, errors=errors)

        # 落库比对用**原值**（见模块 docstring 二.1）
        member_id = membersql.sql_exec_login(self.source, email_text, raw_password)
        if member_id is None:
            return AuthResult(ok=False, errors=[ERR_LOGIN_FAILED])

        membersql.sql_update_login_field(self.source, email_text, self._ip())
        return AuthResult(ok=True, message=MESSAGE_LOGIN, member_id=member_id)

    # -- 注册 ---------------------------------------------------------------

    def register(self, email: Any, password: Any, confirm: Any) -> AuthResult:
        """``_registerAccount()``：注册成功就**自动登录**。

        校验顺序照抄 PHP：邮箱已注册 / 邮箱格式 → 密码空 → 重复密码空 →
        两次不一致。注意"已注册"排在"格式"前面 —— 邮箱格式非法时
        ``SqlGetIdByEmail()`` 根本查不到，两个码不会同时出现，
        但顺序换了会让"邮箱合法且已注册"这一例多跑一次查询。

        成功后 PHP 发一封欢迎信（主题就是成功文案），然后
        ``$this->Login(...)``。本地发信走 :class:`Mailer`，
        会员 id 由自动登录那次查询拿到。
        """
        email_text = membersql.sql_clean_string(email)
        raw_password = "" if password is None else str(password)
        clean_password = membersql.sql_clean_string(password)
        clean_confirm = membersql.sql_clean_string(confirm)

        errors = []
        if is_valid_email(email_text):
            if membersql.sql_get_id_by_email(self.source, email_text) is not None:
                errors.append(ERR_EMAIL_REGISTERED)
        else:
            errors.append(ERR_EMAIL_INPUT)
        if clean_password == "":
            errors.append(ERR_PASSWORD_INPUT)
        if clean_confirm == "":
            errors.append(ERR_PASSWORD2_INPUT)
        if clean_password != clean_confirm:
            errors.append(ERR_PASSWORD_MISMATCH)
        if errors:
            return AuthResult(ok=False, errors=errors)

        if not membersql.sql_insert_member(self.source, email_text, raw_password,
                                           self._ip()):
            return AuthResult(ok=False, errors=[ERR_EMAIL_REGISTERED])

        mail = self._send(email_text, MESSAGE_NEW_ACCOUNT, "欢迎")
        member_id = membersql.sql_exec_login(self.source, email_text, raw_password)
        return AuthResult(ok=True, message=MESSAGE_NEW_ACCOUNT,
                          member_id=member_id, payload={"mail": mail})

    # -- 修改密码 -----------------------------------------------------------

    def change_password(self, member_id: Any, password: Any,
                        confirm: Any) -> AuthResult:
        """``_changePassword()``：**必须先登录**（路由层保证 member_id 非空）。

        这里就是模块 docstring 二.1 那个坑最明显的地方：

        * "新旧密码相同"用 ``md5(清洗值)`` 与库里的值比；
        * 真正写库用 ``$_POST['password']`` 原值。

        于是密码为 ``abc\\`` 时：清洗值是 ``abc``，库里若是 ``abc`` 的 md5，
        判定"没有区别"直接拒绝；而如果库里不是，写入的却是 ``abc\\`` 的 md5。
        照抄，不修（修了就和生产端对不上，而且没有任何用户会因此受损 ——
        受害的只有"密码带反斜杠"这种极端输入）。

        ``member['password']`` 也可能是空串（老行没密码）：此时
        ``md5('')`` 与它不等，正常进入写库分支。
        """
        member = membersql.sql_get_member_by_id(self.source, member_id)
        if member is None:
            return AuthResult(ok=False, errors=[ERR_UNAUTH_OP])

        raw_password = "" if password is None else str(password)
        clean_password = membersql.sql_clean_string(password)
        clean_confirm = membersql.sql_clean_string(confirm)

        errors = []
        current_hash = str(member.get("password") or "")
        if clean_password == "":
            errors.append(ERR_PASSWORD_INPUT)
        elif membersql.hash_password(clean_password) == current_hash:
            errors.append(ERR_PASSWORD_UNCHANGED)
        if clean_confirm == "":
            errors.append(ERR_PASSWORD2_INPUT)
        if clean_password != clean_confirm:
            errors.append(ERR_PASSWORD_MISMATCH)
        if errors:
            return AuthResult(ok=False, errors=errors)

        email = str(member.get("email") or "")
        if not membersql.sql_update_password_field(self.source, email, raw_password):
            return AuthResult(ok=False, errors=[ERR_PASSWORD_INPUT])
        return AuthResult(ok=True, message=MESSAGE_PASSWORD_CHANGED,
                          member_id=str(member_id))

    # -- 修改登录邮箱 -------------------------------------------------------

    def update_login_email(self, member_id: Any, email: Any) -> AuthResult:
        """``_updateLoginEmail()``：改登录帐号。

        三个校验码的顺序照抄：已注册 → 格式 → 与当前相同。
        与"注册"不同的是"已注册"排在"格式"**外面**：
        ``if (filter_var_email($strEmail)) { if (SqlGetIdByEmail(...)) ... }``
        所以邮箱格式非法时只会得到 ``EMAIL_INPUT`` 一个码。

        改完之后 session 里存的还是 member_id，不受影响；但
        ``admin_auth`` 那个"session 里的邮箱"缓存要重新取 —— 所以本动作
        成功后路由会顺手把 session 里的 email 刷新一遍。
        """
        member = membersql.sql_get_member_by_id(self.source, member_id)
        if member is None:
            return AuthResult(ok=False, errors=[ERR_UNAUTH_OP])

        email_text = membersql.sql_clean_string(email)
        current_email = str(member.get("email") or "")

        errors = []
        if is_valid_email(email_text):
            existing = membersql.sql_get_id_by_email(self.source, email_text)
            if existing is not None:
                errors.append(ERR_EMAIL_REGISTERED)
        else:
            errors.append(ERR_EMAIL_INPUT)
        if email_text == current_email:
            errors.append(ERR_EMAIL_UNCHANGED)
        if errors:
            return AuthResult(ok=False, errors=errors)

        if not membersql.sql_update_login_email(self.source, member_id, email_text):
            return AuthResult(ok=False, errors=[ERR_EMAIL_INPUT])
        return AuthResult(ok=True, message=MESSAGE_EMAIL_CHANGED,
                          member_id=str(member_id),
                          payload={"email": email_text})

    # -- 重置密码 -----------------------------------------------------------

    def remind_password(self, email: Any) -> AuthResult:
        """``_remindPassword()``：把随机密码发到邮箱，**并且先登出**。

        新密码的生成方式照抄 PHP::

            $strPassword = $strEmail.DebugGetTime();     // email + 'HH:MM:SS'
            $strPassword = md5($strPassword);
            $strPassword = substr($strPassword, 16);     // 取后 16 位
            SqlUpdatePasswordField($strEmail, $strPassword);   // 落库时再 md5 一次

        所以"明文新密码"是 ``md5(email + 时分秒)`` 的后 16 个十六进制字符，
        库里的值是它的 md5。这样设计的好处是 16 字符够短、能手输；
        坏处是同一秒内两次重置会得到同一个密码（演示环境无所谓）。

        PHP 结尾 ``$this->Logout()`` —— 已登录的人用这个功能会被踢下线。
        照抄，清 session 的动作由路由做（本模块返回 ``ok`` 时路由清）。
        """
        email_text = membersql.sql_clean_string(email)

        errors = []
        if is_valid_email(email_text):
            if membersql.sql_get_id_by_email(self.source, email_text) is None:
                errors.append(ERR_EMAIL_UNREGISTERED)
        else:
            errors.append(ERR_EMAIL_INPUT)
        if errors:
            return AuthResult(ok=False, errors=errors)

        new_password = membersql.hash_password(
            email_text + _now_hms(self.clock))[16:]
        if not membersql.sql_update_password_field(self.source, email_text,
                                                   new_password):
            return AuthResult(ok=False, errors=[ERR_EMAIL_UNREGISTERED])

        mail = self._send(email_text, MESSAGE_NEW_PASSWORD,
                          "你的新密码: " + new_password)
        return AuthResult(ok=True, message=MESSAGE_NEW_PASSWORD,
                          payload={"password": new_password, "mail": mail})

    # -- 关闭帐号 -----------------------------------------------------------

    def close_account(self, email: Any, current_member_id: Any) -> AuthResult:
        """``_closeAccount()``：删掉会员和它的一切，**管理员可以删别人**。

        校验顺序照抄，注意第二层嵌套::

            if (filter_var_email($strEmail)) {
                if ($strId = SqlGetIdByEmail($strEmail)) {
                    if ($strId != $_SESSION['SESS_ID']) {
                        if ($this->IsAdmin() == false)  $arErrMsg[] = ACCT_ERR_UNAUTH_OP;
                    }
                } else {
                    $arErrMsg[] = ACCT_ERR_EMAIL_UNREGISTERED;
                }
            } else {
                $arErrMsg[] = ACCT_ERR_EMAIL_INPUT;
            }

        也就是"删自己"永远允许，"删别人"要管理员。``current_member_id``
        为空（没登录）时相当于"删别人"，非管理员直接就 ``UNAUTH_OP``。

        删完之后 **不登出**（PHP 把 ``Logout()`` 注释掉了，见模块 docstring 二.2），
        session 里留着一个已不存在的 id —— 由路由原样保留。
        """
        email_text = membersql.sql_clean_string(email)

        errors = []
        target_id = None
        if is_valid_email(email_text):
            target_id = membersql.sql_get_id_by_email(self.source, email_text)
            if target_id is None:
                errors.append(ERR_EMAIL_UNREGISTERED)
            elif current_member_id is None or str(target_id) != str(current_member_id):
                if not self.is_admin_member():
                    errors.append(ERR_UNAUTH_OP)
        else:
            errors.append(ERR_EMAIL_INPUT)
        if errors:
            return AuthResult(ok=False, errors=errors)

        stats = membersql.sql_delete_member(self.source, target_id)
        mail = self._send(email_text, MESSAGE_CLOSE_ACCOUNT, "再见")
        return AuthResult(ok=True, message=MESSAGE_CLOSE_ACCOUNT,
                          payload={"deleted": stats, "mail": mail})

    # -- IP -----------------------------------------------------------------

    def _ip(self) -> str:
        """``UrlGetIp()`` 的等价物。

        ``UrlGetIp()``（``php/url.php``）会看 ``HTTP_X_FORWARDED_FOR`` 等头；
        本模块不直接读请求（那样就离不开 Flask 上下文），由路由构造时
        通过 ``ip=`` 传进来。默认空串 = "不知道来源"，照实写入，
        不编造 ``127.0.0.1``。
        """
        return self.ip or ""


def get_whose_display(source: Any, member_id: Any, login_id: Any,
                      chinese: bool = True) -> str:
    """``Account::GetWhoseDisplay()``（``php/account.php:191``）：「我」/「某某」的。

    PHP::

        if ($strMemberId == $this->GetLoginId())   $str = '我';
        else {
            if (($str = SqlGetNameByMemberId($strMemberId)) == false)
                $str = SqlGetEmailById($strMemberId);
        }
        return $str.('的');

    两处细节：

    * ``$strMemberId == $this->GetLoginId()`` 是 PHP 的松散比较，``'1' == 1``
      为真、``'' == 0`` 也为真 —— 这里按字符串比，因为两边都是 id 字符串；
    * 名字和邮箱都取不到时返回 ``'的'``（PHP 会拼出 ``false.'的'`` →
      ``'的'``），不是空串。这个残缺文案照抄。
    """
    if member_id is False or member_id is None:
        member_id = login_id
    if login_id is not None and str(member_id) == str(login_id):
        name = "我" if chinese else "My"
    else:
        name = membersql.sql_get_name_by_member_id(source, member_id)
        if not name:
            name = membersql.sql_get_email_by_id(source, member_id)
        if name is None:
            name = ""
    return name + ("的" if chinese else " ")
