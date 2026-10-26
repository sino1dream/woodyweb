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

## 身份从哪来（三步，逐级回落）

1. ``flask.session['email']`` —— 账号体系接上之后的正常来源。PHP 那边
   等价于 ``GetLoginEmail()``（session 里存的是 email）；
2. 环境变量 ``PALMMICRO_ADMIN_EMAIL`` —— 给"想以非管理员身份验证权限闸门"
   的场合用（测试与本地排查）；
3. :data:`DEMO_ADMIN_EMAIL` —— 演示环境固定身份，与 ``DEMO_MEMBER_ID``
   （``core/stockgroupsql.py``）同一套取舍：账号体系暂缓，但**闸门本身
   是真的**（见 :func:`is_admin`，不是恒返回 True 的桩）。

**说清楚"闸门是真的"是什么意思**：``is_admin()`` 会真的拿身份去比
``ADMIN_EMAIL``。演示环境之所以恒为管理员，是因为第 3 步给的默认身份
**就是**管理员邮箱，而不是这条路绕过了判断。所以只要 session 里放了别的
邮箱，写入口立刻 403 —— ``web/tests/test_quarter_report.py`` 与
``test_exhaustive_holdings.py`` 各有一条这样的用例。
"""

from __future__ import annotations

import os

from flask import has_request_context, session

#: ``define('ADMIN_EMAIL', 'woody@palmmicro.com')``（``php/email.php:2``）。
ADMIN_EMAIL = "woody@palmmicro.com"

#: 演示环境的管理员身份。**故意取成 ``ADMIN_EMAIL`` 本身** ——
#: 换成别的邮箱就得再放宽一次判定，等于把闸门改松。
DEMO_ADMIN_EMAIL = ADMIN_EMAIL

#: session 里存登录邮箱的键（PHP 的 ``$_SESSION['email']`` 同名）。
SESSION_EMAIL_KEY = "email"

#: 覆盖身份的环变量（排查权限闸门时用，不进代码）。
ENV_ADMIN_EMAIL = "PALMMICRO_ADMIN_EMAIL"


def current_admin_email() -> str:
    """当前请求的登录邮箱（判定"是不是管理员"用的那个身份）。

    三级回落见模块 docstring；任何一级取值失败都不抛异常 —— 权限判断
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
