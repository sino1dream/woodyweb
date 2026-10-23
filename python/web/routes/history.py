"""历史数据路由。

对应 PHP 生产端的 6 个历史数据页面（``woody/res/*cn.php``）：

===============================  =======================================  ==============================
本模块 URL                       生产端 URL                                对应 PHP
===============================  =======================================  ==============================
``/history/stockhistory/<sym>``   stockhistorycn.php?symbol=                stockhistoryparagraph.php
``/history/fundhistory/<sym>``    fundhistorycn.php?symbol=                 fundhistoryparagraph.php
``/history/netvaluehistory/<sym>`` netvaluehistorycn.php?symbol=            netvaluehistoryparagraph.php
``/history/netvalueclose/<sym>``  netvalueclosecn.php?symbol=               netvaluecloseparagraph.php
``/history/calibrationhistory/<sym>`` calibrationhistorycn.php?symbol=      calibrationhistoryparagraph.php
``/history/shareshistory/<sym>``  fundsharecn.php?symbol=                   fundshareparagraph.php
``/history/fundposition/<sym>``   fundpositioncn.php?symbol=                (woody/res/fundpositioncn.php)
``/history/fundposition/<sym>/chart.png`` （图表 URL）                      dateimagefile.php
===============================  =======================================  ==============================

分页参数沿用 PHP 的 ``start`` / ``num``（``num=0`` 表示全部记录）。

``fundposition`` 额外接受 ``input=`` —— 页面上"净值涨跌%阈值"输入框的值，
对应 PHP ``GetEditInput()``（``fundpositioncn.php:70``），留空则沿用默认
``POS_NETVALUE_DIFF``（4%）。这一页在 PHP 侧**没有翻页**，所以也不显示翻页菜单。

``chart.png`` 是走势图：PHP 把 ``DateImageFile`` 画成临时文件再让页面
``<img>`` 指过去（``fundpositioncn.php:54-58``），这里直接吐 PNG bytes。
取数走**同一次** ``get_table``（含 ``input=`` / ``num=``），所以图上的线
永远和表里的行对得上。
"""

from __future__ import annotations

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    jsonify,
    render_template,
    request,
)

from web.bootstrap import install_paths

install_paths()

from ui.dateimage import draw_netvalue_position_chart  # noqa: E402
from ui.uicolumn import POS_NETVALUE_DIFF, STOCK_DISP_POSITION  # noqa: E402
from web.services.history_service import (  # noqa: E402
    HISTORY_KINDS,
    HistoryNotFoundError,
    HistoryService,
    parse_paging,
)


history_bp = Blueprint("history", __name__)


def _get_service() -> HistoryService:
    """从应用扩展中取得历史数据服务。"""
    return current_app.extensions["history_service_factory"]()


def _parse(args) -> tuple:
    """解析分页参数。"""
    return parse_paging(args)


@history_bp.route("/api/history/<kind>/<symbol>")
def api_history(kind: str, symbol: str):
    """返回历史数据表格的 JSON。"""
    start, num = _parse(request.args)
    output = _get_service().get_table(kind, symbol, start, num, f_input=_threshold())
    # TableRow 不是 JSON 原生类型，转成纯 dict
    output["rows"] = [
        {"cells": [list(cell) if isinstance(cell, tuple) else cell for cell in row.cells],
         "match": row.match}
        for row in output["rows"]
    ]
    return jsonify(output)


def _threshold() -> float:
    """``?input=`` 阈值，对应 PHP ``GetEditInput()``（``fundpositioncn.php:70``）。

    PHP 的写法是 ``'' !== ($strInput = GetEditInput()) ? floatval($strInput) : POS_NETVALUE_DIFF``
    —— 注意它是**空串才走默认**，``strval(POS_NETVALUE_DIFF)`` 那个默认值在表单里
    永远非空，所以生产端实际上永远拿得到 4.0 这个初值。这里同样按"给了就解析、
    给的是负数或非数字就退回默认"处理。
    """
    raw = request.args.get("input", None)
    if raw is None or raw == "":
        return POS_NETVALUE_DIFF
    try:
        return float(raw)
    except (TypeError, ValueError):
        return POS_NETVALUE_DIFF


@history_bp.route("/history/<kind>/<symbol>")
def history_page(kind: str, symbol: str):
    """渲染一个历史数据页面。

    未知的 ``kind`` 直接 404 —— 与 PHP 不同（PHP 每个页面是独立文件，
    不存在的页面自然 404），这里用一张表驱动全部页面，所以需要显式校验。
    """
    start, num = _parse(request.args)
    output = _get_service().get_table(kind, symbol, start, num, f_input=_threshold())
    return render_template(
        "history.html",
        table=output,
        kinds=HISTORY_KINDS,
        paging=_paging_links(output),
        threshold=_threshold(),
    )


@history_bp.route("/history/fundposition/<symbol>/chart.png")
def fundposition_chart(symbol: str):
    """净值 + 仓位走势图（``DateImageFile``，``fundpositioncn.php:54-58``）。

    PHP 把图落成临时文件、页面 ``<img>`` 再指过去；这里直接吐 PNG bytes。
    ``input=`` / ``num=`` 与表格页面共用同一份 ``get_table``，改阈值或行数
    时图与表一起变 —— 不会出现"表里 8 行、图上 3 个点"的对不上。

    **base 少于两点时 404**：PHP 的 ``if ($jpg->Draw(...))`` 为假时页面上
    那个 ``<img>`` 压根不出现（``dateimagefile.php:26`` 的
    ``count($arBase) < 2`` 闸门），模板侧也用 ``table.chart`` 为空的守卫
    不渲染 ``<img>``，所以正常浏览不会碰到这个 404。
    """
    start, num = _parse(request.args)
    output = _get_service().get_table("fundposition", symbol, start, num,
                                      f_input=_threshold())
    chart = output.get("chart") or {}
    png = draw_netvalue_position_chart(
        chart.get("base") or [],
        chart.get("focus") or [],
        symbol=symbol,
        #: PHP ``GetAll(TableColumnGetPosition(), $strSymbol)`` 的标题两截
        name=STOCK_DISP_POSITION,
    )
    if png is None:
        abort(404)
    return Response(png, mimetype="image/png")


def _paging_links(output: dict) -> dict:
    """给模板准备翻页链接的参数。

    ``num=0``（全部记录）时不提供翻页 —— 与 PHP ``GetMenuLink()``
    在 ``$iStart == 0 && $iNum == 0`` 时直接返回总数一致。

    ``fundposition`` 这一页在 PHP 侧压根没有翻页：``$acct->GetNum()`` 只用来
    截断行数，不生成任何菜单链接（对比 netvaluehistorycn.php 是有的）。这里
    显式关掉，否则会渲染出一个点了没反应的"下一页"。
    """
    total = output["total"]
    if not output.get("paged", True) or total <= 0:
        return {"show": False, "total": total}
    start = output["start"]
    num = output["num"]
    if total <= 0 or (start == 0 and num == 0):
        return {"show": False, "total": total}
    last = min(start + num, total)
    return {
        "show": True,
        "total": total,
        "start": start,
        "num": num,
        "first": start + 1,
        "last": last,
        "prev_start": max(start - num, 0) if start > 0 else None,
        "next_start": start + num if start + num < total else None,
        "all": start != 0,
    }
