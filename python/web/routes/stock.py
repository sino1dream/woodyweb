"""股票列表路由 —— 对应 ``php/ui/stockparagraph.php``（45 行）。

生产端只有 ``_mystock.php`` 一个调用点（第 233 行，登录后的"全部股票"页），
所以 PHP 那边这张表藏在账号后面；本站点还没有账号体系，这里按只读目录页开放。

分页参数沿用 PHP 的 ``start`` / ``num``（``num=0`` 表示全部记录），
与 ``/history/*`` 用同一个约定（见 ``history_service.parse_paging``）。
"""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, render_template, request, url_for

from web.bootstrap import install_paths

install_paths()

from ref.externallink import get_xueqiu_url  # noqa: E402
from sql.historytable import TABLE_COMMON_DISPLAY  # noqa: E402
from web.services.stock_service import (  # noqa: E402
    StockService,
    default_stock_service_factory,
)


stock_bp = Blueprint("stock", __name__)


def _parse_paging() -> tuple:
    """解析 ``start`` / ``num``；与历史数据页完全同口径。"""

    def _int(name: str, default: int) -> int:
        raw = request.args.get(name)
        if raw is None or raw == "":
            return default
        try:
            value = int(raw)
        except ValueError:
            return default
        return value if value >= 0 else default

    return _int("start", 0), _int("num", TABLE_COMMON_DISPLAY)


def _link_builders() -> tuple:
    """给表格行的两个链接构造器。

    **放在路由层而不放进服务**，是因为生成 URL 需要请求上下文（``url_for``），
    而服务层跑在没有请求的地方也能用（脚本导出、测试）。同理，
    服务返回的 dict 里也只有 URL 字符串、**不含 HTML**。
    """
    def fund_link(symbol: str) -> str:
        return url_for("fund.fund_page", symbol=symbol)

    def xueqiu_link(symbol: str) -> str:
        try:
            return get_xueqiu_url(symbol)
        except Exception:
            # 认不出的符号不该让整页挂掉 —— 那一格退化成纯文本
            return ""

    return fund_link, xueqiu_link


def _get_table(start: int, num: int, admin: bool = False):
    """取表格数据。注入了工厂就用注入的，否则走默认实现。

    注意这里**不**新建 service：工厂返回的对象已经带好数据源，
    只把链接构造器替换成"知道 url_for"的这一份。
    """
    factory = current_app.extensions.get("stock_service_factory")
    service = default_stock_service_factory() if factory is None else factory()

    # 用 getattr 而不是直接取属性：注入的可能不是 StockService 本身
    # （比如已经自带链接构造器的替身），这时原样使用即可。
    sql = getattr(service, "stock_sql", None)
    if sql is not None:
        fund_link, xueqiu_link = _link_builders()
        service = StockService(
            sql,
            link_builder=fund_link,
            external_link_builder=xueqiu_link,
            admin_link_builder=getattr(service, "admin_link_builder", None),
        )
    return service.get_table(start=start, num=num, admin=admin)


@stock_bp.route("/api/stock")
def api_stock():
    """返回股票列表的 JSON。``TableRow`` 先转成纯 dict。"""
    start, num = _parse_paging()
    output = _get_table(start, num)
    output["rows"] = [
        {"cells": row.cells, "href": row.href, "hrefs": row.hrefs} for row in output["rows"]
    ]
    return jsonify(output)


@stock_bp.route("/stock")
def stock_page():
    """渲染股票列表页，对应 PHP 的 ``EchoStockParagraph()``。"""
    start, num = _parse_paging()
    table = _get_table(start, num)
    return render_template("stock_list.html", table=table)
