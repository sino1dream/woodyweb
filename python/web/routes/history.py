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
===============================  =======================================  ==============================

分页参数沿用 PHP 的 ``start`` / ``num``（``num=0`` 表示全部记录）。
"""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, render_template, request

from web.bootstrap import install_paths

install_paths()

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
    output = _get_service().get_table(kind, symbol, start, num)
    # TableRow 不是 JSON 原生类型，转成纯 dict
    output["rows"] = [
        {"cells": [list(cell) if isinstance(cell, tuple) else cell for cell in row.cells],
         "match": row.match}
        for row in output["rows"]
    ]
    return jsonify(output)


@history_bp.route("/history/<kind>/<symbol>")
def history_page(kind: str, symbol: str):
    """渲染一个历史数据页面。

    未知的 ``kind`` 直接 404 —— 与 PHP 不同（PHP 每个页面是独立文件，
    不存在的页面自然 404），这里用一张表驱动全部页面，所以需要显式校验。
    """
    start, num = _parse(request.args)
    output = _get_service().get_table(kind, symbol, start, num)
    return render_template(
        "history.html",
        table=output,
        kinds=HISTORY_KINDS,
        paging=_paging_links(output),
    )


def _paging_links(output: dict) -> dict:
    """给模板准备翻页链接的参数。

    ``num=0``（全部记录）时不提供翻页 —— 与 PHP ``GetMenuLink()``
    在 ``$iStart == 0 && $iNum == 0`` 时直接返回总数一致。
    """
    total = output["total"]
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
