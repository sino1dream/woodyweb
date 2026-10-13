"""股票路由 —— 列表页 + 非基金详情页。

## 两条路由，两个 PHP 出处

``/stock``（列表页）
    对应 ``php/ui/stockparagraph.php``（45 行）。生产端只有 ``_mystock.php``
    一个调用点（第 233 行，登录后的"全部股票"页），所以 PHP 那边这张表藏在
    账号后面；本站点还没有账号体系，这里按只读目录页开放。

``/stock/<symbol>``（详情页）
    对应 ``woody/res/php/_mystock.php:144-170`` 的 **else 分支** ——
    ``_echoMyStockData()`` 四个分支（基金 / FundPair / Holdings / 其它）里
    的最后一个。前三个分支各有归宿，剩下"非基金的 A 股、H 股、ADR、外汇、
    指数、期货"这一大类全都落到这里。

    段落组装在 ``web/services/stock_detail_service.py``，这里只负责取服务
    与渲染 —— 与 ``routes/fund.py`` 里 ``_build_*`` 的分工一致。

分页参数沿用 PHP 的 ``start`` / ``num``（``num=0`` 表示全部记录），
与 ``/history/*`` 用同一个约定（见 ``history_service.parse_paging``）。
"""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, render_template, request, url_for

from web.bootstrap import install_paths

install_paths()

from calc.fundgroups import get_symbol_group  # noqa: E402
from ref.externallink import get_xueqiu_url  # noqa: E402
from sql.historytable import TABLE_COMMON_DISPLAY  # noqa: E402
from web.services.stock_detail_service import StockDetailService  # noqa: E402
from web.services.stock_service import (  # noqa: E402
    StockService,
    default_stock_service_factory,
)


stock_bp = Blueprint("stock", __name__)


def _get_reference_service():
    """参考数据服务；它的 ``source`` 是详情页查配对表要用到的。"""
    factory = current_app.extensions.get("reference_service_factory")
    return None if factory is None else factory()


def _get_reference_refs(symbols: list):
    """按符号建引用对象。

    与 ``routes/fund.py`` 的同款做法：优先用测试注入的
    ``reference_refs_builder``，否则走真实路径（先读库快照、再兜底联网）。
    """
    builder = current_app.extensions.get("reference_refs_builder")
    if builder is not None:
        return builder(symbols)
    from web.services.reference_service import build_stock_references

    service = _get_reference_service()
    return build_stock_references(symbols, source=getattr(service, "source", None))


def _get_sma_service():
    factory = current_app.extensions.get("sma_service_factory")
    return None if factory is None else factory()


def _get_history_service():
    factory = current_app.extensions.get("history_service_factory")
    return None if factory is None else factory()


def _get_fundest_service():
    """估值表服务；只有第 2 分支（``fundpair``）用得到。"""
    factory = current_app.extensions.get("fundest_service_factory")
    return None if factory is None else factory()


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
    def detail_link(symbol: str) -> str:
        """详情页链接按符号类型分流：基金走 ``/fund``，其它走 ``/stock``。

        ``/fund/<symbol>`` 是按号段推断分组的，只服务 QDII 基金；把外汇、
        指数、期货这类符号交给它，页面上的"返回分组列表"会指向一个没有这
        一行的分组页。**非基金符号认不出来时（``get_symbol_group`` 返回
        ``None``）就归 ``/stock``** —— 那才是 ``_mystock.php`` 第 4 分支
        的落点。
        """
        if get_symbol_group(symbol) is None:
            return url_for("stock.stock_detail_page", symbol=symbol)
        return url_for("fund.fund_page", symbol=symbol)

    def xueqiu_link(symbol: str) -> str:
        try:
            return get_xueqiu_url(symbol)
        except Exception:
            # 认不出的符号不该让整页挂掉 —— 那一格退化成纯文本
            return ""

    return detail_link, xueqiu_link


def _table_link(symbol: str, kind: str) -> str:
    """跟踪表那两格链接用的构造器：``(符号, 页面名) -> URL``。

    ``kind == 'fund'`` 是「跟踪」列（``_getFundPairLink()`` 给的
    ``$ref->GetMyStockLink()``，指向标的自己的详情页）；其余是历史页
    （这里是 ``calibrationhistory``）。

    **详情页链接必须走 :func:`_link_builders` 的那份分流**：跟踪列可能指向
    中间层标的（``^NDX`` 这类非基金符号），直接 ``url_for("fund.fund_page")``
    会落到一个没有这一行的分组页。
    """
    if kind == "fund":
        return _link_builders()[0](symbol)
    return url_for("history.history_page", kind=kind, symbol=symbol)


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
        detail_link, xueqiu_link = _link_builders()
        service = StockService(
            sql,
            link_builder=detail_link,
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


@stock_bp.route("/stock/<symbol>")
def stock_detail_page(symbol: str):
    """渲染非基金详情页。

    这条路由接 ``_mystock.php`` 四个分支里的**两个**，判断顺序与 PHP 的
    ``if / else if`` 一致：

    1. 第 2 分支 ``StockGetFundPairReference()`` —— ``fundpair`` 表里有这条记录
       （CK2b）。渲染 EST / 跟踪均线，``/stock/<symbol>`` 页面上会**多出**几张表。
    2. 第 4 分支 ``else`` —— 其它非基金符号（CK3）。

    前三个分支里的第 1 个（基金）由 ``/fund/<symbol>`` 承担，第 3 个
    （Holdings）还没有对应实现，会落到第 4 分支。

    **引用对象建不出来时仍然返回 200**，只是没有表 —— 与 PHP 那边
    ``EchoStockGroup()`` 拿不到 ref 时整页只剩链接的行为一致。不按 404 处理：
    旧链接里的符号可能只是暂时没有行情，直接 404 会让历史外链全部失效。

    ## 为什么不把 ``None`` 传进服务层让它兜底

    服务层的 ``get_detail`` 在 ``ref`` 为空时会自己再建一次引用对象（给
    "直接用服务层"的调用者兜底）。但路由层**已经**负责建 ref 了，再兜一次
    就把"这个符号建不出引用对象"的信号吃掉了 —— 页面会带着一个空壳 ref
    继续渲染出均线和历史表，而 PHP 那边是整段不输出。

    所以第 4 分支建不出来就直接渲染空页，不进服务层。
    """
    service = StockDetailService(
        source=getattr(_get_reference_service(), "source", None),
        link_builder=_table_link,
    )

    #: 第 2 分支优先，且**不**给它传 ref —— 让它自己按 ``fundpair`` 表判断。
    #: 传 ref 进去等于替它决定了答案。
    detail = service.get_fund_pair_detail(
        symbol,
        sma_service=_get_sma_service(),
        history_service=_get_history_service(),
        fundest_service=_get_fundest_service(),
    )
    if detail is not None:
        return render_template("stock_detail.html", detail=detail, symbol=symbol)

    refs = _get_reference_refs([symbol])
    if not refs:
        return render_template("stock_detail.html", detail=None, symbol=symbol)

    detail = service.get_detail(
        symbol,
        ref=refs[0],
        sma_service=_get_sma_service(),
        history_service=_get_history_service(),
    )
    return render_template("stock_detail.html", detail=detail, symbol=symbol)
