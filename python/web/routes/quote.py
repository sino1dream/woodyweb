"""实时行情路由。

对应 PHP：PHP 那边**没有行情接口**，实时行情是在页面请求内同步抓取后直接渲染
（详见 ``python/auto/README_quotes_migration.md`` 的分析）。这里补上三个接口，
让页面/前端脚本直读缓存，同时也提供一个手动触发抓取的入口：

* ``GET  /api/quotes``        —— 用缓存里的最新价渲染列表（可选刷新）
* ``POST /api/quotes/refresh`` —— 立刻抓一批（对应 PHP 的 StockPrefetchArrayData）
* ``GET  /api/quotes/source`` —— 数据源信息与接口自述（换源时用）
"""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from web.bootstrap import install_paths

install_paths()

from updater.quoteupdater import get_updater  # noqa: E402
from source.quotesource import list_quote_sources  # noqa: E402


quote_bp = Blueprint("quote", __name__)


def _get_updater():
    """取实时行情更新器。

    优先用应用扩展里的工厂（测试可注入假行情源），否则退回进程级单例。
    """
    factory = current_app.extensions.get("updater_factory")
    return factory() if factory else get_updater()


def _wants_refresh() -> bool:
    """请求是否要求"顺手刷新一批"。

    默认关闭，因为同步抓取会拖慢响应（那是 PHP 的做法）。要开启可：
    * 设置环境变量 ``PALMMICRO_QUOTE_REFRESH_ON_REQUEST=1``；或
    * 请求里显式带 ``?refresh=1``。
    """
    if request.args.get("refresh") in ("1", "true", "yes"):
        return True
    import os

    return os.getenv("PALMMICRO_QUOTE_REFRESH_ON_REQUEST", "0") == "1"


@quote_bp.route("/api/quotes")
def api_quotes():
    """返回实时行情缓存与数据源信息。"""
    updater = _get_updater()
    if _wants_refresh():
        updater.update()
    quotes = updater.get_all()
    return jsonify({
        "count": len(quotes),
        "quotes": [quote.as_dict() for _symbol, quote in sorted(quotes.items())],
        "updater": updater.describe(),
    })


@quote_bp.route("/api/quotes/refresh", methods=["GET", "POST"])
def api_quotes_refresh():
    """立刻抓一批行情（页面可手动点"刷新"）。"""
    updater = _get_updater()
    updated = updater.update()
    return jsonify({
        "updated": updated,
        "error": updater.last_error,
        "updater": updater.describe(),
    })


@quote_bp.route("/api/quotes/source")
def api_quotes_source():
    """数据源信息；换源时先看这个接口确认接的是谁。"""
    updater = _get_updater()
    return jsonify({
        "available_sources": list_quote_sources(),
        "active": updater.describe()["source"],
        "updater": updater.describe(),
    })
