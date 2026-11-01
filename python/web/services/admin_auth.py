"""管理员身份判定（``Account::IsAdmin()`` 的本地对应物）。

对应 PHP ``php/account.php:265``::

    function IsAdmin()
    {
        if ($this->GetLoginEmail() == ADMIN_EMAIL)
        {
            return true;
        }
        return false;
    }

``ADMIN_EMAIL`` 定义在 ``php/email.php:2``，值是 ``woody@palmmicro.com``。

## 为什么单独一个模块

这个判断被三处需要：穷举持仓页的"确认更新持仓"链接（页面上要不要给）、
``/holdings/submit`` 与 ``/holdings/quarter/<symbol>`` 两个写入口（给不给写）。
散在路由里各写一遍的话，日后接账号体系时必然漏掉一处 —— 那就是一个
未鉴权的写入口。集中在这里，接登录态只改 :func:`current_admin_email`。

## 身份从哪来（四步，逐级回落）

1. ``flask.session['email']`` —— 账号体系接上之后的正常来源。PHP 那边
   等价于 ``GetLoginEmail()``（session 里存的是 ``SESS_ID``，再用
   ``SqlGetEmailById()`` 换成邮箱；本地登录时两个键一起写，省一次查询）；
2. 环境变量 ``PALMMICRO_ADMIN_EMAIL`` —— 给"想以非管理员身份验证权限闸门"
   的场合用（测试与本地排查）；
3. :data:`DEMO_ADMIN_EMAIL` —— **只在** :data:`AUTH_ENABLED` 关着时用，
   即"账号体系没启用"的演示环境固定身份，与 ``DEMO_MEMBER_ID``
   （``core/stockgroupsql.py``）同一套取舍；
4. 空串（不是管理员）—— 账号体系启用之后，没有 session 就是没有身份。

**说清楚"闸门是真的"是什么意思**：``is_admin()`` 会真的拿身份去比
``ADMIN_EMAIL``。演示环境之所以恒为管理员，是因为第 3 步给的默认身份
**就是**管理员邮箱，而不是这条路绕过了判断。所以只要 session 里放了别的
邮箱，写入口立刻 403 —— ``web/tests/test_quarter_report.py`` 与
``test_exhaustive_holdings.py`` 各有一条这样的用例。

## ``AUTH_ENABLED`` 为什么是个开关

第 3 步那个回落等于"任何人不带 session 就是管理员"。在没有登录页的时候
这无所谓（本地只有演示数据），但登录一旦跑起来，它就是个**提权后门**：
浏览器清掉 cookie 就变管理员。

所以这里留一个开关：``PALMMICRO_AUTH=1``（或 ``create_app({"AUTH_ENABLED": True})``）
关掉第 3 步。默认关闭，理由有两条：

* 现有一批测试与本地冒烟脚本依赖"默认是管理员"这个前提（否则每个写入口
  都得先登录）；
* ``web/routes/account_auth.py`` 的登录页与开关是同一批工作，但"演示环境
  默认能点"这条体验不该被顺手改掉。

要验证真实闸门，起服务时加 ``PALMMICRO_AUTH=1``，再配上
``PALMMICRO_ADMIN_EMAIL=woody@palmmicro.com``（演示库里没有这个会员，
登录不进去，只能靠环境变量给身份）。
"""

from __future__ import annotations

import os

from flask import current_app, has_app_context, has_request_context, session

#: ``define('ADMIN_EMAIL', 'woody@palmmicro.com')``（``php/email.php:2``）。
ADMIN_EMAIL = "woody@palmmicro.com"

#: 演示环境的管理员身份。**故意取成 ``ADMIN_EMAIL`` 本身** ——
#: 换成别的邮箱就得再放宽一次判定，等于把闸门改松。
#: 只在 :data:`AUTH_ENABLED` 关着时生效（见模块 docstring 的开关那节）。
DEMO_ADMIN_EMAIL = ADMIN_EMAIL

#: session 里存登录邮箱的键（PHP 的 ``$_SESSION['email']`` 同名）。
SESSION_EMAIL_KEY = "email"

#: session 里存登录会员 id 的键。PHP 用的是 ``$_SESSION['SESS_ID']``，
#: 键名照抄 —— 会话里存的是 id 而不是邮箱，改邮箱不会把登录态弄丢。
SESSION_MEMBER_KEY = "SESS_ID"

#: 覆盖身份的环变量（排查权限闸门时用，不进代码）。
ENV_ADMIN_EMAIL = "PALMMICRO_ADMIN_EMAIL"

#: 开启"账号体系"的环变量。设成非空的 ``1/true/yes`` 即生效。
ENV_AUTH_ENABLED = "PALMMICRO_AUTH"


def auth_enabled() -> bool:
    """账号体系是否启用（决定"没有 session 时算不算管理员"）。

    取值优先级：``current_app.config['AUTH_ENABLED']``（测试与
    ``create_app`` 显式传）→ 环境变量 → 关。
    """
    if has_app_context():
        try:
            value = current_app.config.get("AUTH_ENABLED")
        except Exception:  # pragma: no cover - 极端情况（app 未初始化完）
            value = None
        if value is not None:
            return bool(value)
    raw = os.environ.get(ENV_AUTH_ENABLED, "")
    return raw.strip().lower() in ("1", "true", "yes", "on")


def current_member_id() -> str | None:
    """当前登录会员的 id（``$_SESSION['SESS_ID']``），没登录返回 ``None``。

    PHP::

        $strMemberId = isset($_SESSION['SESS_ID']) ? $_SESSION['SESS_ID'] : false;
        if ($strMemberId) {
            if (trim($strMemberId) == '')  $strMemberId = false;
        }

    也就是"空串也算没登录"。``None`` 与 ``''`` 在 Python 里都假，但
    :func:`is_admin` 那一侧要区分"没有身份"和"身份是空串"，
    所以统一归一成 ``None``。
    """
    if not has_request_context():
        return None
    try:
        member_id = session.get(SESSION_MEMBER_KEY)
    except Exception:  # pragma: no cover - 没有 SECRET_KEY 之类
        return None
    if member_id is None:
        return None
    text = str(member_id).strip()
    return text or None


def current_admin_email() -> str:
    """当前请求的登录邮箱（判定"是不是管理员"用的那个身份）。

    四级回落见模块 docstring；任何一级取值失败都不抛异常 —— 权限判断
    出错时的安全默认是"不是管理员"（返回空串，:func:`is_admin` 自然为假）。
    """
    if has_request_context():
        try:
            email = session.get(SESSION_EMAIL_KEY)
        except Exception:  # pragma: no cover - 没有 SECRET_KEY 之类
            email = None
        if email:
            return str(email)
    override = os.environ.get(ENV_ADMIN_EMAIL)
    if override:
        return override
    if auth_enabled():
        # 账号体系启用后**不再**回落到演示管理员：否则清掉 cookie 就提权。
        return ""
    return DEMO_ADMIN_EMAIL


def is_admin() -> bool:
    """``Account::IsAdmin()``：登录邮箱是管理员邮箱。

    **不要把它改成"恒 True"** —— 演示环境已经是管理员了（
    :data:`DEMO_ADMIN_EMAIL`），改成恒 True 只会让"换身份就失效"这条
    回归测试失去意义。
    """
    return current_admin_email() == ADMIN_EMAIL


def denied_message() -> str:
    """权限不足时给用户看的一句话（两个写入口共用，措辞一致）。"""
    return (
        "这个操作只有管理员可以做（页面上的管理员链接也只有在管理员登录时才出现）。"
        "本地演示环境默认就是管理员身份；如果看不到链接，检查 session 里的登录邮箱。"
    )
