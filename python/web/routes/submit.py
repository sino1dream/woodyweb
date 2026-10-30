"""三个"抓外部数据写库"的后台入口（``woody/res/submit*.php``）。

## PHP 那边是什么

三个页面都是"点一下就把外部数据抓进库"的管理员动作页，**没有可见内容**：

* ``submitdividend.php?symbol=XOP`` → ``_AdminDividendAccount`` → ``stockdividend``
* ``submitnetvalue.php?symbol=QQQ`` → ``_AdminNetValueAccount`` → ``netvaluehistory``
* ``submithistory.php?symbol=SPY`` → ``_AdminHistoryAccount`` → ``stockhistory``

三个的 ``AdminProcess()`` 跑完都 ``SwitchToSess()`` 跳回，触发方式是一个
带 ``confirm()`` 的链接。生产端它们**几乎进不去**（详见
``web/services/submit_service.py`` 的模块文档），所以本地把这批做成一张
工具页 + 三个 POST 端点：

============================  ========================================
路由                            对应 PHP
============================  ========================================
``GET  /submit``               三个页面合起来的一张工具表（本地新增）
``POST /submit/dividend``      ``submitdividend.php``
``POST /submit/netvalue``      ``submitnetvalue.php``
``POST /submit/history``       ``submithistory.php``
============================  ========================================

## 三条与 PHP 的差异

1. **POST 不是 GET** —— 理由与 ``routes/admin.py``、``routes/holdings.py``
   一致：不在 GET 上做写操作。
2. **权限闸门**：``is_admin()``，非管理员 403 且不碰库。
3. **写后有反馈**：flash 出"写了多少行 / 为什么没写"，PHP 只写进 debug 日志。
"""

from __future__ import annotations

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from web.bootstrap import install_paths

install_paths()

from core.stockdata import MySqlFundDataSource  # noqa: E402
from web.db import get_db  # noqa: E402
from web.services.admin_auth import denied_message, is_admin  # noqa: E402
from web.services import submit_service  # noqa: E402

submit_bp = Blueprint("submit", __name__, url_prefix="/submit")


def _source() -> MySqlFundDataSource:
    """取数据源：优先测试注入的工厂，缺省用请求级库连接（同 ``admin._source``）。"""
    factory = current_app.extensions.get("submit_source_factory")
    if factory is not None:
        return factory()
    return MySqlFundDataSource(get_db())


@submit_bp.route("")
def index():
    """工具页。``?symbol=`` 时顺带把该符号三张表的状态列出来。"""
    symbol = (request.args.get("symbol") or "").strip().upper()
    status = submit_service.describe_symbol(_source(), symbol) if symbol else None
    return render_template(
        "submit_tools.html",
        tools=submit_service.tool_list(),
        symbol=symbol,
        status=status,
        is_admin=is_admin(),
    )


@submit_bp.route("/<name>", methods=["POST"])
def run(name: str):
    """跑一个工具。名字非法 404，未授权 403，缺符号 400。"""
    tool = submit_service.get_tool(name)
    if tool is None:
        return render_template(
            "error.html", message="没有这个工具：%s" % name, status=404), 404

    if not is_admin():
        return render_template(
            "error.html", message=denied_message(), status=403), 403

    symbol = (request.form.get("symbol") or "").strip().upper()
    if not symbol:
        return render_template(
            "error.html", message="缺少 symbol（符号）。", status=400), 400

    outcome = submit_service.run_tool(tool["key"], _source(), symbol)
    flash(outcome["message"])
    return redirect(url_for("submit.index", symbol=symbol))
