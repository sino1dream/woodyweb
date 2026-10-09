"""基金估值路由。

页面层次对应 PHP 侧的分组页 + 基金页：

* ``/fund/<group>``：分组列表（对应 ``php/ui/fundlistparagraph.php`` 的对照表）；
* ``/fund/<group>/<symbol>``：某个分组下的基金详情页；
* ``/fund/<symbol>``：兼容旧链接，按符号所属号段自动判断分组。

注意注册顺序：``/fund/<group>`` 与 ``/fund/<symbol>`` 是同参数个数的规则，
Werkzeug 会让它们重载，匹配时**先注册的规则优先**。所以必须先注册分组列表，
否则 ``/fund/qdiilof`` 会被当成基金符号交给 ``fund_page``。
"""

from __future__ import annotations

import json

from typing import Any

from flask import Blueprint, current_app, jsonify, redirect, render_template, request, url_for

from web.bootstrap import install_paths

install_paths()

from fundcli import parse_market_values  # noqa: E402
from fundgroups import get_symbol_group  # noqa: E402
from web.services.fund_list_service import (  # noqa: E402
    FundGroupNotFoundError,
    FundListService,
)
from web.services.fund_service import FundService  # noqa: E402


fund_bp = Blueprint("fund", __name__)


def _get_service() -> FundService:
    """从应用扩展中取得基金服务。"""
    return current_app.extensions["fund_service_factory"]()


def _get_list_service() -> FundListService:
    """从应用扩展中取得基金列表服务。"""
    return current_app.extensions["fund_list_service_factory"]()


def _parse_sources() -> dict[str, float]:
    """解析查询参数中的实时数据源。"""
    return parse_market_values(request.args.getlist("source"))


def _render_fund(symbol: str, group: str):
    """渲染基金详情页，并把分组信息带过去用于生成返回链接。"""
    output = _get_service().get_fund(symbol, _parse_sources())
    config_json = json.dumps(output["config"], ensure_ascii=False, indent=2, sort_keys=True)

    return render_template(
        "fund_detail.html",
        fund=output,
        config_json=config_json,
        group=group,
        fundest=_build_fundest(symbol),
        reference=_build_reference(symbol),
    )


def _build_fundest(symbol: str) -> dict[str, Any] | None:
    """基金详情页的「估值」表（``php/ui/fundestparagraph.php``）。

    **默认不渲染**，这是本版与 PHP 最大的一处不同，也是刻意的：

    这张表要显示的是**三档**估值（官方 / 参考 / 实时），分别来自
    ``GetOfficialNetValue()`` / ``GetFairNetValue()`` / ``GetRealtimeNetValue()``，
    在 Python 侧必须由 ``refobj.QdiiReference`` 配 ``fundestcalc`` 的
    estimator 填进去。而基金详情页现在只有一个
    ``PalmmicroFundCalculator.est_net_value()`` 的结果（就是页面上那个
    "估值净值"）—— 把它硬塞进「官方EST」列，等于**又造一条 EST 口径**。

    上一次出现两条口径并存的后果是：SZ161130 的官方 EST 从 4.59 变成 20245，
    页面还不报错。所以这里只留注入口，等 estimator 接线那一步做完再打开。

    注入方式：``create_app(fundest_refs_builder=...)``。
    """
    builder = current_app.extensions.get("fundest_refs_builder")
    if builder is None:
        return None
    try:
        return current_app.extensions["fundest_service_factory"]().get_table(builder([symbol]))
    except Exception:
        # 估值算不出来不该让整个详情页挂掉
        return None


def _build_reference(symbol: str) -> dict[str, Any] | None:
    """基金详情页的「参考数据」表（``php/ui/referenceparagraph.php``）。

    **取不到行情时返回 ``None`` 而不是空表**：模板据此整段跳过。
    宁可少一块，也不要在页面上摆一张全空的表格 —— 那会让人以为是数据丢了。

    只取基金本身这一个符号：PHP 的详情页还会带上估值标的与汇率，
    但那两个目前要等 refobj 的 ``QdiiReference`` 接进页面后再补。
    """
    service = _get_reference_service()
    if service is None:
        return None
    try:
        table = service.get_table(_get_reference_refs([symbol]))
    except Exception:
        return None
    return table if table["rows"] else None


def _get_reference_service():
    factory = current_app.extensions.get("reference_service_factory")
    return None if factory is None else factory()


def _get_reference_refs(symbols: list[str]):
    """按符号建引用对象；未显式注入时走真实抓取（可能联网，已在上层兜底）。"""
    builder = current_app.extensions.get("reference_refs_builder")
    if builder is not None:
        return builder(symbols)
    from web.services.reference_service import build_stock_references

    return build_stock_references(symbols)


@fund_bp.route("/api/fund/<symbol>")
def api_fund(symbol: str):
    """返回单个基金的估值 JSON。"""
    output = _get_service().get_fund(symbol, _parse_sources())
    return jsonify(output)


@fund_bp.route("/fund/<group>/<symbol>")
def fund_page_in_group(group: str, symbol: str):
    """分组下的基金详情页，例如 /fund/qdiilof/SZ161130。

    三种情况：

    1. 符号确实属于该分组 -> 正常渲染；
    2. 符号属于另一个分组 -> 重定向到它真正的分组，避免出现「返回列表里没有这
       一行」的死链接；
    3. 分组名或符号不认识 -> 就地渲染，保证旧链接可用。
    """
    symbol_group = get_symbol_group(symbol)
    if symbol_group is not None and symbol_group != group:
        return redirect(url_for("fund.fund_group_page", group=symbol_group))

    return _render_fund(symbol, group)


@fund_bp.route("/fund/<group>")
def fund_group_page(group: str):
    """分组列表页，例如 /fund/qdiilof、/fund/qdiietf。"""
    service = _get_list_service()
    try:
        output = service.get_group(group)
    except FundGroupNotFoundError:
        # 分组名不存在时退化成「当成基金符号」的旧行为，保证 /fund/SZ161130 仍然可用
        return _render_fund(group, get_symbol_group(group))

    return render_template(
        "fund_list.html",
        group=output,
        groups=service.list_groups(),
    )


@fund_bp.route("/fund/<symbol>")
def fund_page(symbol: str):
    """兼容旧链接的基金详情页，例如 /fund/SZ161130。

    按号段推断所属分组，这样详情页的「返回分组列表」能指对地方。显式传 group
    也只在该符号确实属于该分组时才采用。
    """
    group = request.args.get("group")
    symbol_group = get_symbol_group(symbol)
    if group is None or group != symbol_group:
        group = symbol_group
    return _render_fund(symbol, group)
