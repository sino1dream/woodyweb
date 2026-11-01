"""账号页：登录 / 注册 / 改密 / 改邮箱 / 重置密码 / 关号 / 资料 / 编辑资料 / 登出。

对应 PHP ``account/`` 目录那一族页面。目录结构是"一页四行壳子"：

============================  ===============================================
PHP 页面                       实现
============================  ===============================================
``login.php`` / ``logincn.php``           ``account/php/_editemail.php`` +
``register*.php``                          ``_editemailform.php``（同一个表单，
``password*.php``                          靠 ``UrlGetPage()`` 分派）
``updateemail*.php``
``reminder*.php``
``closeaccount*.php``
``profile*.php``                          ``account/php/_profile.php``
``editprofile*.php``                       ``_editprofileform.php``
``submitprofile.php``                      ``_submitprofile.php``（资料入库）
============================  ===============================================

所以六个"邮箱表单"页在本地合成**一个**处理函数 + 一张 :data:`PAGE_CONFIG`
表：字段显隐、是否需要登录、标题文案全在表里，函数只按 ``action`` 分派。
这与分类页那边"19 个壳子 → 一个注册表"是同一个判断 —— 壳子里没有逻辑。

## 一、URL 与 PHP 的对照

PHP 的页面名带 ``cn`` 后缀（``logincn.php``），``UrlGetPage()`` 会把它剥掉，
所以 ``$_SESSION`` 跳转、``SwitchTo('login')`` 用的都是**不带后缀**的名字
（``Auth()`` 里写的是 ``SwitchTo('/account/login')``）。本地路由因此是
``/account/login`` 而不是 ``/account/logincn``。

## 二、六个动作的字段显隐（``_editemailform.php:73-111``）

================  ========  ========  ==========  ==============
动作              邮箱       密码       重复密码     需要登录
================  ========  ========  ==========  ==============
``login``         可编辑     可编辑     隐藏         否
``register``      可编辑     可编辑     可编辑       否
``password``      **只读**   可编辑     可编辑       是
``updateemail``   可编辑     隐藏       隐藏         是
``reminder``      可编辑     隐藏       隐藏         否
``closeaccount``  可编辑¹    隐藏       隐藏         是
================  ========  ========  ==========  ==============

¹ 非管理员时只读（``_readonlyEditEmail()`` 的 ``case EDIT_EMAIL_CLOSE``：
``if ($bAdmin == false) return true;``）。管理员能改这一格，因为**关别人的
帐号**要先把邮箱填成对方那个。

"隐藏"在 PHP 里是 ``disabled`` + 标签置空（输入框还在、提交不上来）；
本地直接不渲染那一格，语义相同而 HTML 干净。

## 三、三处本地化的取舍

1. **只做中文页**。``account/`` 每页都有中英两份，本地只迁中文（与
   ``/autotractor`` 等页一致），``_editemailform.php`` 里那套
   ``$bChinese = false`` 的分支不迁。
2. **新增 ``/account/logout``**。PHP **没有**登出入口 —— ``Logout()`` 只在
   ``_remindPassword()`` 里被调用，``EchoLinks()`` 里也没有链接。
   本地加一个，否则演示时"换个身份看看"只能清 cookie。这是新增功能，
   在 README 里单列。
3. **成功后跳 ``/account/profile``**。PHP 是 ``SwitchToSess()``（跳回进入
   表单前的那个页面，靠 session 里记的来源地址），本地没有"来源页"这套
   机制，统一落到资料页 —— 那里能看到登录身份，也算"跳回原处"的合理近似。
"""

from __future__ import annotations

from flask import (
    Blueprint,
    Markup,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from web.bootstrap import install_paths

install_paths()

from core.stockdata import MySqlFundDataSource  # noqa: E402
from web.db import get_db  # noqa: E402
from web.services import auth_service as auth  # noqa: E402
from web.services.admin_auth import (  # noqa: E402
    SESSION_EMAIL_KEY,
    SESSION_MEMBER_KEY,
    current_admin_email,
    current_member_id,
    is_admin,
)

account_auth_bp = Blueprint("account_auth", __name__, url_prefix="/account")

#: 六个动作的页面配置。键就是 URL 的最后一段，也是提交按钮的 ``value``
#: （PHP 那边提交值是中文文案，本地用动作名 —— 见 ``auth_service`` 里
#: 为什么把中文从协议里拿掉）。
PAGE_CONFIG = {
    "login": {
        "title": "登录帐号",
        "email_readonly": False,
        "show_password": True,
        "show_confirm": False,
        "require_login": False,
        "require_admin": False,
    },
    "register": {
        "title": "注册新帐号",
        "email_readonly": False,
        "show_password": True,
        "show_confirm": True,
        "require_login": False,
        "require_admin": False,
    },
    "password": {
        "title": "修改密码",
        # 修改密码页的邮箱是**只读**的：密码按 email 改（PHP
        # ``SqlUpdatePasswordField($member['email'], ...)``），让人改这一格
        # 只会把密码改到别人帐号上 —— 所以锁住。
        "email_readonly": True,
        "show_password": True,
        "show_confirm": True,
        "require_login": True,
        "require_admin": False,
    },
    "updateemail": {
        "title": "修改登录帐号",
        "email_readonly": False,
        "show_password": False,
        "show_confirm": False,
        "require_login": True,
        "require_admin": False,
    },
    "reminder": {
        "title": "生成新密码",
        "email_readonly": False,
        "show_password": False,
        "show_confirm": False,
        # 重置密码**不需要**登录（忘了密码的人才用），PHP 底部那个
        # switch 的 case login/register/reminder 三个都不调 Auth()。
        "require_login": False,
        "require_admin": False,
    },
    "closeaccount": {
        "title": "关闭帐号",
        # 非管理员只读（要改成对方邮箱才能关别人的号，见模块 docstring 二）。
        "email_readonly": None,
        "show_password": False,
        "show_confirm": False,
        "require_login": True,
        "require_admin": False,
    },
}

#: 每个动作的引导语（``account/php/_editemail.php::EchoAll()`` 的 switch）。
#: 含链接的那条用 :class:`Markup` —— 文案是服务端常量，不是用户输入。
INTRO = {
    "login": Markup(
        '欢迎回来! 你可以<a href="/account/register">注册</a>新帐号. '
        '忘记密码了? 点击<a href="/account/reminder">这里</a>.'),
    "register": "欢迎注册! 除非你主动要求, 否则我们不会给你发任何邮件.",
    "password": "",
    "updateemail": "我们通过登录电子邮件联系你, 务必确认能收到这个地址的电子邮件.",
    "reminder": "新的密码通过电子邮件发送.",
    "closeaccount": "关闭帐号后所有跟此帐号相关的数据都会被删除, 并且不可恢复.",
}


def _source() -> MySqlFundDataSource:
    """取数据源：优先测试注入的工厂，缺省用请求级库连接（同 ``admin._source``）。"""
    factory = current_app.extensions.get("auth_source_factory")
    if factory is not None:
        return factory()
    return MySqlFundDataSource(get_db())


def _client_ip() -> str:
    """``UrlGetIp()`` 的等价物。

    PHP 优先看 ``HTTP_X_FORWARDED_FOR``（取第一段），没有才用
    ``REMOTE_ADDR``。本地照这个顺序 —— 反代后面部署时才拿得到真实来源，
    但直接连进来时 ``X-Forwarded-For`` 是客户端自己填的，**不能**当作
    可信身份（只用来记 ``member.ip``，不参与任何判定）。
    """
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or ""


def _service() -> auth.AuthService:
    return auth.AuthService(
        _source(),
        is_admin_member=is_admin,
        ip=_client_ip(),
    )


def _fields_for_email_readonly(action: str) -> bool:
    """``closeaccount`` 的只读判定要现场问一次 ``is_admin()``。"""
    config = PAGE_CONFIG[action]
    readonly = config["email_readonly"]
    if readonly is None:
        return not is_admin()
    return bool(readonly)


def _errors_by_field(codes) -> dict:
    """按 ``_editemailform.php`` 的四个位置分组错误文案。"""
    grouped = {"email": [], "password": [], "password2": [], "submit": []}
    for code in codes or []:
        field = auth.ERROR_FIELDS.get(code, "submit")
        text = auth.ERROR_MESSAGES.get(code, "")
        if text:
            grouped.setdefault(field, []).append(text)
    return grouped


def _render(action: str, *, email: str = "", result=None, status: int = 200):
    config = PAGE_CONFIG[action]
    return render_template(
        "account_auth.html",
        action=action,
        form_config=config,
        title=config["title"],
        intro=INTRO.get(action, ""),
        email=email,
        email_readonly=_fields_for_email_readonly(action),
        errors=_errors_by_field(result.errors if result else None),
        result=result,
        login_email=current_admin_email() if current_member_id() else "",
    ), status


def _require_login(action: str):
    """PHP ``$acct->Auth()``：没登录就跳登录页。

    返回 ``None`` 表示"已经登录，继续"，否则返回那个 redirect 响应。
    """
    if not PAGE_CONFIG[action]["require_login"]:
        return None
    if current_member_id():
        return None
    flash("请先登录。")
    return redirect(url_for("account_auth.auth_page", action="login"))


@account_auth_bp.route("/logout")
def logout():
    """登出（**本地新增**，PHP 没有这个入口 —— 见模块 docstring 三.2）。"""
    session.clear()
    flash("已经退出登录。")
    return redirect(url_for("account_auth.auth_page", action="login"))


@account_auth_bp.route("/<action>", methods=["GET", "POST"])
def auth_page(action):
    """六个邮箱表单动作的统一入口。"""
    if action not in PAGE_CONFIG:
        abort(404)

    guard = _require_login(action)
    if guard is not None:
        return guard

    member_id = current_member_id()

    if request.method == "GET":
        # PHP ``EditEmailForm()`` 的第三个参数是 ``$acct->GetLoginEmail()``，
        # 所以已登录时邮箱是预填的（改密码/改邮箱两页尤其明显）。
        email = current_admin_email() if member_id else ""
        return _render(action, email=email)

    email = request.form.get("email", "")
    password = request.form.get("password", "")
    confirm = request.form.get("confirm", "")
    service = _service()

    if action == "login":
        result = service.login(email, password)
    elif action == "register":
        result = service.register(email, password, confirm)
    elif action == "password":
        result = service.change_password(member_id, password, confirm)
    elif action == "updateemail":
        result = service.update_login_email(member_id, email)
    elif action == "reminder":
        result = service.remind_password(email)
    else:  # closeaccount
        result = service.close_account(email, member_id)

    if not result.ok:
        # 失败时把用户填的邮箱留在框里（PHP 存 ``$_SESSION['SESS_EMAIL_INPUT']``），
        # 密码一律不回填 —— 那不是"帮用户省事"，是把明文密码写进 HTML。
        return _render(action, email=email, result=result, status=400)

    _apply_session(action, result, email)
    if result.message:
        flash(result.message)
    # 重置密码把新密码摊在页面上：本地不发信（auth_service 模块 docstring 三.1）
    payload_password = (result.payload or {}).get("password")
    if payload_password:
        flash("新密码：%s" % payload_password)
    return redirect(_redirect_target(action))


def _apply_session(action: str, result, email: str) -> None:
    """动作成功后的 session 变更（逐条对照 PHP）。

    * ``login`` / ``register`` —— ``session_regenerate_id()`` + 写
      ``SESS_ID``。Flask 的 session 是签名 cookie，没有"换 id"这回事，
      ``session.clear()`` 后再写等价于作废旧内容（防 session fixation）；
    * ``updateemail`` —— 只刷新缓存的邮箱，``SESS_ID`` 不动
      （分组挂在 member_id 上，换邮箱不该掉登录）；
    * ``reminder`` —— PHP 结尾 ``Logout()``，清空；
    * ``closeaccount`` —— **不清**（PHP 把 ``Logout()`` 注释掉了，
      见 ``auth_service`` 模块 docstring 二.2）。
    """
    if action in ("login", "register"):
        session.clear()
        session[SESSION_MEMBER_KEY] = result.member_id
        session[SESSION_EMAIL_KEY] = (
            (result.payload or {}).get("email") or email)
    elif action == "updateemail":
        session[SESSION_EMAIL_KEY] = (result.payload or {}).get("email", email)
    elif action == "reminder":
        session.clear()


def _redirect_target(action: str) -> str:
    """成功后跳哪：登录/注册/改密/改邮箱去资料页，重置/关号回登录页。"""
    if action in ("reminder", "closeaccount"):
        return url_for("account_auth.auth_page", action="login")
    return url_for("account_auth.profile_page")


# ---------------------------------------------------------------------------
# 资料页（``account/php/_profile.php::EchoAll()``）
# ---------------------------------------------------------------------------

@account_auth_bp.route("/profile")
def profile_page():
    """``/account/profile``：会员与资料，外带三个编辑入口。

    PHP 的 ``EchoAll()`` 有三段：资料本身 → 博客评论 → 基金申购金额。
    后两段本地不迁（博客内容不迁；``fundpurchase`` 表在 PHP 里已被注释掉
    一半、金额改存 ``groupitemamount``），所以只剩第一段 + 编辑链接。
    """
    member_id = current_member_id()
    if not member_id:
        flash("请先登录。")
        return redirect(url_for("account_auth.auth_page", action="login"))

    source = _source()
    member = auth.membersql.sql_get_member_by_id(source, member_id)
    if member is None:
        # 会员行被删（例如在另一个标签页关了帐号）—— PHP 那边
        # ``EchoAll()`` 拿不到 member 就什么都不显示；本地给个明确提示，
        # 顺便把失效的 session 清掉。
        session.clear()
        flash("登录状态已失效，请重新登录。")
        return redirect(url_for("account_auth.auth_page", action="login"))

    profile = auth.membersql.sql_get_profile_by_member_id(source, member_id)
    whose = auth.get_whose_display(source, member_id, member_id)
    return render_template(
        "account_profile.html",
        title="%s资料" % whose,
        member=member,
        profile=profile or {},
        whose=whose,
        status_text=_status_text(member.get("status")),
        is_admin=is_admin(),
    )


def _status_text(status) -> str:
    """``_profile.php`` 的 status 三个分支。

    ``'2'`` → 接收 Palmmicro 邮件；``'1'`` → 不接收任何邮件；
    其它 → 帐号受限制。PHP 用的是字符串比较（mysqli 给的是字符串），
    本地 pymysql 给 int，两种都要认。
    """
    try:
        value = int(status)
    except (TypeError, ValueError):
        value = 0
    if value == auth.membersql.STATUS_SUBSCRIBED:
        return "接收Palmmicro邮件"
    if value == auth.membersql.STATUS_NO_EMAIL:
        return "不接收任何邮件"
    return "帐号受限制"


# ---------------------------------------------------------------------------
# 编辑资料（``account/php/_editprofileform.php`` + ``_submitprofile.php``）
# ---------------------------------------------------------------------------

@account_auth_bp.route("/editprofile", methods=["GET", "POST"])
def edit_profile_page():
    """资料表单。GET 显示，POST 落库（``SqlUpdateProfile`` + ``SqlUpdateStatus``）。

    PHP 是两个文件：``_editprofileform.php`` 出版单、``_submitprofile.php``
    处理提交（表单 action 指向 ``submitprofile.php``）。本地合成一页 ——
    合起来的理由和六个邮箱表单一样：中间没有"跳开再回来"的逻辑。

    提交后跳回 ``/account/profile``（PHP 是 ``SwitchToLink('profilecn.php')``）。
    """
    member_id = current_member_id()
    if not member_id:
        flash("请先登录。")
        return redirect(url_for("account_auth.auth_page", action="login"))

    source = _source()
    member = auth.membersql.sql_get_member_by_id(source, member_id)
    if member is None:
        session.clear()
        flash("登录状态已失效，请重新登录。")
        return redirect(url_for("account_auth.auth_page", action="login"))

    if request.method == "POST":
        # ``_submitprofile.php`` 的五个字段都用 ``SqlCleanString`` 清洗后落库
        # （与密码那条路不同：这里**没有**"原值 vs 清洗值"的分裂）。
        name = auth.membersql.sql_clean_string(request.form.get("name", ""))
        phone = auth.membersql.sql_clean_string(request.form.get("phone", ""))
        address = auth.membersql.sql_clean_string(request.form.get("address", ""))
        web = auth.membersql.sql_clean_string(request.form.get("web", ""))
        signature = auth.membersql.sql_clean_string(request.form.get("signature", ""))
        status = request.form.get("status", str(auth.membersql.DEFAULT_STATUS))

        ok = auth.membersql.sql_update_profile(
            source, member_id, name, phone, address, web, signature)
        # PHP 的 ``_onEdit()`` 先写资料、再写 status；资料写失败就直接返回，
        # 不去动 status。照抄这个顺序（两条语句，没有事务 —— 演示库
        # MyISAM 本来也不支持事务）。
        if ok:
            ok = auth.membersql.sql_update_status(source, member_id, status)
        if ok:
            flash("资料已经更新。")
        else:
            flash("资料没有写入，请再试一次。")
        return redirect(url_for("account_auth.edit_profile_page"))

    profile = auth.membersql.sql_get_profile_by_member_id(source, member_id) or {}
    return render_template(
        "account_edit_profile.html",
        title="修改帐号资料",
        profile=profile,
        member=member,
        status=_status_value(member.get("status")),
    )


def _status_value(status) -> int:
    """单选按钮选中哪个：只认 1 / 2，其它值两个都不选（与 PHP 一致）。"""
    try:
        value = int(status)
    except (TypeError, ValueError):
        return 0
    return value if value in (1, 2) else 0
