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
import os

from typing import Any

from flask import Blueprint, current_app, jsonify, redirect, render_template, request, url_for

from web.bootstrap import install_paths

install_paths()

from cli.fundcli import parse_market_values  # noqa: E402
from calc.fundgroups import get_fund_group, get_fund_group_names, get_symbol_group  # noqa: E402
from web.services.fund_list_service import (  # noqa: E402
    FundGroupNotFoundError,
    FundListService,
)
from web.services.fund_service import FundService  # noqa: E402
from web.services.qdii_page_service import build_qdii_page  # noqa: E402
from web.services.reference_service import reference_symbols_for_fund  # noqa: E402


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
        trading=_build_trading(symbol),
        fundest=_build_fundest(symbol),
        reference=_build_reference(symbol),
        shares=_build_shares(symbol),
        sma=_build_sma(symbol),
        calibration=_build_calibration(symbol),
        fundlist=_build_fund_list(symbol),
        fundpairsma=_build_fund_pair_sma(symbol),
    )


def _build_qdii_page() -> dict[str, Any]:
    """QDII 分类页（``woody/res/qdiicn.php``）的两张表。

    与 ``_render_fund`` 的分工：那边是**一只**基金的详情，这边是**整个分类**
    的汇总；两张表本身复用同一批服务（``reference_service`` /
    ``fundest_service``），分类规则在
    :func:`~web.services.qdii_page_service.build_qdii_page` 里。

    ``sort`` 透传 PHP 的 ``?sort=symbol`` / ``?sort=premium``
    （``_echoFundEstParagraph()`` 里那两个分支，只在超过 2 行时生效）。
    """
    reference_service = _get_reference_service()
    fundest_service = _get_fundest_service()
    page = build_qdii_page(
        source=getattr(reference_service, "source", None),
        reference_service=reference_service,
        fundest_service=fundest_service,
        sort=request.args.get("sort") or None,
    )
    return page, _fund_group_nav()


def _fund_group_nav() -> list:
    """页尾的「其它分组」导航。

    与首页 ``routes/main.py::_list_groups()`` 同一套写法：**只读分组定义、
    不连数据库**。区别是首页要的是数量，这里只要标题；分组数量让模板自己
    从 ``item.count`` 取，所以直接复用同一结构。
    """
    groups = []
    for name in get_fund_group_names():
        definition = get_fund_group(name)
        groups.append(
            {
                "name": name,
                "title": definition["title"],
                "count": len(definition["symbols"]()),
            }
        )
    return groups


def _wants_live_refresh() -> bool:
    """本请求是否**额外联网补一次**五档盘口。

    ## 默认已经能显示盘口了

    ``realtimequote`` 快照表带 ``orderbook`` 列（五档买卖挂单），
    由后台抓取进程每 60 秒写一次，页面正常读库即可 —— 不需要联网。

    那这个开关还剩什么用？两种情形：

    1. daemon 还没跑过（或刚清库），快照里没有该符号的盘口；
    2. 就想看"此刻"的挂单，不想等下一轮抓取。

    这时显式要求一次：

    * 请求带 ``?live=1``；或
    * 环境变量 ``PALMMICRO_TRADING_LIVE_QUOTES=1``。

    这与 ``/api/quotes`` 的 ``?refresh=1`` 是同一套路（见 ``web/routes/quote.py``），
    目的都是让"什么时候会联网"在代码里数得清楚 ——
    **默认请求一次都不联网**，联网只能是被显式要求的。
    """
    if request.args.get("live") in ("1", "true", "yes"):
        return True
    return os.getenv("PALMMICRO_TRADING_LIVE_QUOTES", "0") == "1"


def _build_trading(symbol: str) -> dict[str, Any] | None:
    """基金详情页的「五档交易」表（``php/ui/tradingparagraph.php``）。

    这里要传进去的是**基金引用对象**（``refobj.QdiiReference``）而不是纯行情引用：
    ``EchoFundTradingParagraph($fund, …)`` 内部先 ``GetStockRef($fund)`` 取盘口，
    再用 ``$fund`` 自己的三档估值算溢价列。顺序不能反 —— 反过来就只剩三列空表。

    **盘口没有数据就整段跳过**：盘口来自快照表的 ``orderbook`` 列，
    没抓到（或该品种本来就没有挂单数据）时就是这样，与详情页其它三张表的
    处理一致。``?live=1`` 可以强迫本次请求联网补一次（见
    :func:`_wants_live_refresh`）。

    ## 最右边那一列是 QDII 特有的「换算价」列

    PHP ``_qdiigroup.php:96`` 传了一个回调 ``'_tradingUserDefined'``，
    在五档表右边再补一列：把每一档基金价格换算成中间层标的与各杠杆标的的价格。
    本版由 :mod:`web.services.qdii_trading_column` 提供同构的 callable。

    **拿不到中间层标的就不补这一列**，而不是补一列空数据 —— 此时退回
    ``tradingparagraph.php`` 的通用形态，与其它四个段落的处理一致。
    """
    service = _get_trading_service()
    if service is None:
        return None
    try:
        refs = _get_trading_refs(symbol)
        if not refs:
            return None
        callback, width = _build_trading_column(refs[0])
        table = service.fund_table(refs[0], callback=callback,
                                   callback_width=width)
    except Exception:
        return None
    return table if table else None


def _build_trading_column(fund: Any) -> tuple:
    """建五档表最后一列的回调；拿不到就返回 ``(None, 默认宽度)``。

    与 EST / 参考数据那几段保持同一种装配顺序：先从自己身上取中间层标的，
    再去 ``fundpair`` 查谁跟踪它。查不到（``fundpair`` 里没记录）会得到空列表，
    这时回调仍然可用 —— 那一列只有中间层标的那一格（``^NDX价格``）。
    """
    default_width = _default_callback_width()
    est_symbol = _est_symbol_of(fund)
    if not est_symbol:
        return None, default_width
    try:
        from web.services.qdii_trading_column import (
            PAIR_ESTIMATE_WIDTH,
            build_leverage_references,
            build_trading_column,
        )

        source = getattr(_get_reference_service(), "source", None)
        refs = build_leverage_references(est_symbol, source)
        callback = build_trading_column(fund, refs)
    except Exception:
        return None, default_width
    return callback, PAIR_ESTIMATE_WIDTH


def _default_callback_width() -> int:
    """回调列的默认宽度（``trading_service.TRADING_CALLBACK_WIDTH``）。"""
    from web.services.trading_service import TRADING_CALLBACK_WIDTH

    return TRADING_CALLBACK_WIDTH


def _est_symbol_of(fund: Any) -> Any:
    """基金的中间层标的符号；取不到返回 ``None``（此时不补那一列）。"""
    return _symbol_of(_est_ref_of(fund)) or None


def _build_fund_list(symbol: str) -> dict[str, Any] | None:
    """基金指数对照表（``php/ui/fundlistparagraph.php:38``）。

    PHP 在 ``_qdiigroup.php:100`` 的守卫是 ``if (count($arLev) > 0)`` ——
    **没有跟踪这只基金中间层标的的杠杆标的，整段就不出现**，与本项目其它段落
    "缺数据就整段不摆"的处理一致（比摆一张只有表头的空表好）。

    末列「对冲值」由 ``_qdiigroup.php:41 _callbackFundListHedge`` 提供，
    见 :mod:`web.services.qdii_fund_list`。``$arLev`` 与五档表那一列同源
    （都是"谁跟踪了我的中间层标的"），所以直接复用
    :func:`~web.services.qdii_trading_column.build_leverage_references`，
    不再查第二次。
    """
    try:
        from web.services.qdii_fund_list import fund_list_table

        refs = _get_trading_refs(symbol)
        if not refs:
            return None
        fund = refs[0]
        est_symbol = _est_symbol_of(fund)
        if not est_symbol:
            return None
        source = getattr(_get_reference_service(), "source", None)
        leverage_refs = _leverage_refs_of(est_symbol, source)
        table = fund_list_table(
            fund,
            leverage_refs,
            source=source,
            link_builder=current_app.extensions.get("fundlist_link_builder"),
            # 这些标的之所在这里，正是因为它们跟踪 ``est_symbol`` —— 引用对象
            # 自己说不出来（FundPairReference 未迁），由调用方补上。
            pair_symbol=est_symbol,
        )
    except Exception:
        return None
    if table is None:
        return None
    table["link"] = _fund_list_group_link(symbol)
    return table


def _leverage_refs_of(est_symbol: str, source: Any) -> list:
    """``$arLev`` —— 谁跟踪了 ``est_symbol``（``GetLeverageSymbols``）。

    ## PHP 里两处用的是**同一个** ``$arLev``

    .. code-block:: php

        if (count($arLev) > 0)
        {
            EchoFundListParagraph($arLev, '_callbackFundListHedge');
            EchoFundPairSmaParagraphs($ref->GetEstRef(), $arLev);
        }

    所以本版也只查一次、两处共用：查两遍不只是浪费，还可能在两次查询之间
    ``fundpair`` 表变了，给出两张互相矛盾的表。

    ``fundlist_refs_builder`` 是测试与无库环境的注入口（与
    ``trading_refs_builder`` / ``sma_refs_builder`` 同一套约定）。
    """
    builder = current_app.extensions.get("fundlist_refs_builder")
    if builder is not None:
        return list(builder(est_symbol) or ())
    from web.services.qdii_trading_column import build_leverage_references

    return list(build_leverage_references(est_symbol, source) or ())


def _build_fund_pair_sma(symbol: str) -> list:
    """每个杠杆标的**各一张**均线表（``smaparagraph.php:156``）。

    ## 为什么是"好几张表"而不是"一张表多几列"

    .. code-block:: php

        foreach ($arFundPairRef as $fund_pair_ref)
        {   EchoSmaParagraph($ref, '', $fund_pair_ref, '_callbackFundPairSma', $callback2); }

    被算均线的是**同一个** ``$ref``（中间层标的 ``^GSPC``），变的只有
    ``$cb_ref``（``SH`` / ``SPY``）—— 于是每张表的左边五列**完全一样**，
    只有第 6 列"换算到这个标的是多少"不同。生产端 ``sz161125cn.php`` 上
    就是并排三张（QDII 那张 + SH + SPY）。

    所以服务返回的是**列表**，模板里逐个渲染。

    ## 空列表不是错误

    没有杠杆标的、或中间层标的没有历史数据时都返回空列表 —— PHP 那边对应
    ``if (count($arLev) > 0)`` 与 ``EchoTableParagraphBegin()`` 的守卫。
    """
    service = _get_sma_service()
    if service is None:
        return []
    refs = _get_sma_refs(symbol)
    if not refs:
        return []
    est_ref = _est_ref_of(refs[0])
    if est_ref is None:
        return []
    try:
        source = getattr(_get_reference_service(), "source", None)
        return list(service.fund_pair_tables(est_ref,
                                             _leverage_refs_of(_symbol_of(est_ref), source)))
    except Exception:
        # 一张装饰性的对照表不该让整页 500
        return []


def _est_ref_of(fund: Any) -> Any:
    """``$ref->GetEstRef()`` —— 中间层标的的**引用对象**。

    与 :func:`_symbol_of` 分开：配对均线表要的是对象本身（它要被算均线、
    要 ``GetPriceDisplay``），而五档表那一列只要符号去查 ``fundpair``。
    """
    for name in ("get_est_ref", "GetEstRef"):
        getter = getattr(fund, name, None)
        if callable(getter):
            try:
                return getter()
            except Exception:
                return None
    return None


def _symbol_of(ref: Any) -> str:
    """引用对象的符号；取不到给空串（调用方据此跳过）。"""
    for name in ("get_symbol", "GetSymbol"):
        getter = getattr(ref, name, None)
        if callable(getter):
            try:
                return str(getter() or "")
            except Exception:
                return ""
    return ""


def _fund_list_group_link(symbol: str) -> dict[str, str] | None:
    """``GetFundListLink()``：指向这只基金所在分组的列表页。

    PHP 把它摆在表**前面**当标题链接；本版按本项目其它表的做法摆到表后面的
    ``table-note`` 里（位置不同、内容一样）。取不到分组就 ``None`` ——
    不该为了一个装饰链接让整张表消失。
    """
    group = get_symbol_group(symbol)
    if not group:
        return None
    return {
        "url": url_for("fund.fund_group_page", group=group),
        "text": "基金指数对照表",
    }


def _build_calibration(symbol: str) -> dict[str, Any] | None:
    """基金自己的「校准记录」表（``php/ui/fundestparagraph.php:157``）。

    PHP 在 ``echoFundEstParagraph`` 之后紧接着调用
    ``EchoCalibrationHistoryParagraph($ref, 0, 1)`` —— 只显示**最近一条**，
    作用是回答"这只基金的估值系数最近一次是什么时候标的定的"。

    完整记录在 ``/history/calibrationhistory/<符号>``，同一套取数逻辑，
    见 :class:`~web.services.calibration_service.CalibrationService` 的说明。
    """
    service = _get_calibration_service()
    if service is None:
        return None
    try:
        return service.table(symbol)
    except Exception:
        return None


def _calibration_of(ref: Any) -> dict[str, Any] | None:
    """某个引用对象的校准记录（``smaparagraph.php:218`` 那个位置用）。

    那边传的是 ``$ref->GetEstRef()`` —— **中间层标的**（``^GSPC``）而不是基金，
    所以表格少了"净值 / 对冲值"两列（``IsFundA()`` 为假）。
    """
    service = _get_calibration_service()
    if service is None or ref is None:
        return None
    symbol = ""
    for name in ("get_symbol", "GetSymbol"):
        getter = getattr(ref, name, None)
        if callable(getter):
            try:
                symbol = str(getter() or "")
            except Exception:
                symbol = ""
            if symbol:
                break
    if not symbol:
        return None
    try:
        return service.table(symbol)
    except Exception:
        return None


def _get_calibration_service():
    factory = current_app.extensions.get("calibration_service_factory")
    return None if factory is None else factory()


def _get_trading_service():
    factory = current_app.extensions.get("trading_service_factory")
    return None if factory is None else factory()


def _get_trading_refs(symbol: str) -> list:
    """建基金引用对象；盘口默认来自快照，``?live=1`` 时才额外联网补一次。

    ``_build_qdii_refs()`` 内部走 ``refquote.load_quote_into`` 的读库路径，
    快照里那列 ``orderbook`` 会被装填回引用对象（见 ``refquote.apply_order_book``），
    所以**不带任何参数也有盘口** —— 前提是后台抓取进程跑过。
    """
    builder = current_app.extensions.get("trading_refs_builder")
    if builder is not None:
        return builder([symbol])
    refs = _build_qdii_refs([symbol])
    if refs and _wants_live_refresh():
        _fill_order_book(refs[0])
    return refs


def _fill_order_book(ref: Any) -> bool:
    """给 ref 的行情对象**联网**补一次五档；返回是否补到。

    用 ``apply_network``（``auto/refquote.py``）而不是 ``load_quote_into``：
    这里要的就是"此刻的挂单"，读库一遍再抓一遍纯属浪费。
    快照里**已经有**盘口时也会覆盖 —— 既然用户显式要求了，就按联网的算。
    """
    quote_ref = None
    for name in ("get_stock_ref", "GetStockRef"):
        getter = getattr(ref, name, None)
        if callable(getter):
            try:
                quote_ref = getter()
            except Exception:
                quote_ref = None
            if quote_ref is not None:
                break
    if quote_ref is None:
        quote_ref = ref
    try:
        from ref.refquote import NetworkQuoteSource, apply_network

        return apply_network(quote_ref, NetworkQuoteSource())
    except Exception:
        return False


def _build_shares(symbol: str) -> dict[str, Any] | None:
    """基金详情页的「基金场内份额」表（``php/ui/fundshareparagraph.php``）。

    直接复用历史数据服务里的 ``shareshistory`` —— 它与 ``/history/shareshistory/<sym>``
    那张表**是同一张**（PHP 那边也是同一个 ``EchoFundShareParagraph()``），
    没有第二套算法。

    **权限差异要注意**：生产端的独立页面 ``fundsharecn.php`` 要登录
    （见 ``README_history_migration.md``），而详情页里这段是不带鉴权的。
    本版跟随详情页（不鉴权），因为演示库里那份数据本来就只有 4 行样本。
    """
    service = _get_history_service()
    if service is None:
        return None
    try:
        table = service.get_table("shareshistory", symbol, 0, 10)
    except Exception:
        return None
    return table if table.get("rows") else None


def _build_sma(symbol: str) -> dict[str, Any] | None:
    """基金详情页的「均线」表（``php/ui/smaparagraph.php``）。

    ## 用哪个入口：``EchoQdiiSmaParagraph`` 还是 ``EchoFutureSmaParagraph``

    ``woody/res/php/_qdiigroup.php`` 里两个都调了（第 97 与第 103 行）：

    .. code-block:: php

        EchoQdiiSmaParagraph($ref);          // 中间层标的的均线 + 基金估值对照列
        ...
        EchoFutureSmaParagraph($ref);        // 有期货溢价时再加一张"理论价"表

    所以本版**两张都要建**，放进一个 dict 交给模板：

    * ``qdii`` —— 中间层（``^NDX``）的均线，回调列是基金自己的估值；
    * ``future`` —— 同一批均线，但回调列换算成**期货理论价**
      （要 ``futurepremium`` 表里有利率才出得来）。

    ## 取不到数据就整段跳过

    ``SmaService`` 在两种情况下返回 ``None``：建不出 ``StockHistory``、
    或**所有均线都是 0**（后者是刻意偏离 PHP 的守卫，见
    :func:`~web.services.sma_service._has_any_level`）。
    演示库里 ``^NDX`` 一行历史都没有，所以这两张表现在都是不显示的 ——
    那正是守卫要的效果，比摆一张全 0 的表强。
    """
    service = _get_sma_service()
    if service is None:
        return None
    refs = _get_sma_refs(symbol)
    if not refs:
        return None
    ref = refs[0]
    source = getattr(_get_reference_service(), "source", None)
    try:
        future = service.future_table(ref, source=source)
    except Exception:
        future = None
    # smaparagraph.php:218 —— EchoFutureSmaParagraph 在算出期货溢价之后，
    # **先**摆一张中间层标的的校准记录，再摆均线表。取不到就 None（不影响均线表）。
    calibration = _calibration_of(ref.get_est_ref()) if future else None
    return {"qdii": service.qdii_table(ref), "future": future,
            "calibration": calibration}


def _get_sma_service():
    factory = current_app.extensions.get("sma_service_factory")
    return None if factory is None else factory()


def _get_sma_refs(symbol: str) -> list:
    """建中间层引用对象；``sma_refs_builder`` 是给测试用的注入口。

    与 :func:`_get_trading_refs` 同款做法：默认走真实数据库，测试里由
    ``create_app(sma_refs_builder=...)`` 顶替，这样均线接线也能**不连库**验证。
    """
    builder = current_app.extensions.get("sma_refs_builder")
    if builder is not None:
        return builder([symbol])
    return _build_qdii_refs([symbol])


def _get_history_service():
    factory = current_app.extensions.get("history_service_factory")
    return None if factory is None else factory()


def _build_fundest(symbol: str) -> dict[str, Any] | None:
    """基金详情页的「估值」表（``php/ui/fundestparagraph.php``）。

    ## 与 PHP 的差别：实时那一档常常是空的

    表要显示的是三档估值（官方 / 参考 / 实时），它们由
    ``refobj.QdiiReference`` 现算：

    * **官方 EST** —— ``fundestcalc.compute_official()``，即"官方净值出来之前的
      那一刻估值"，实测 ``SZ161130`` 得 ``4.545196``，与官方净值一致；
    * **参考 EST** —— ``GetFairNetValue()``，用**当前汇率**；
    * **实时 EST** —— 需要中间层的**配对引用**（``GetRealtimeRef()`` →
      期货/杠杆标的），而 ``FundPairReference`` 未迁移，所以现在恒为 ``None``。

    本版**照 PHP 的行为**：实时档取不到时该行少两格（PHP 那边只有 ``if``、
    没有 ``else`` 补空，见 ``fundest_service`` 的 docstring 与它的测试）。
    表头也按 PHP 扫一遍所有 ref 决定 —— 全是 ``None`` 时就不加那两列。

    **为什么不再"怕两条口径并存"**：这里直接调 ``fundestcalc``（唯一实现），
    没有第二套公式。之前那次"官方 EST 从 4.59 变 20245"的成因是
    ``_qdii_value`` 写死取实时快照，已修并加了估值守卫。
    """
    builder = current_app.extensions.get("fundest_refs_builder")
    service = _get_fundest_service()
    if service is None:
        return None
    try:
        refs = builder([symbol]) if builder is not None else _build_qdii_refs([symbol])
        if not refs:
            # 建不出引用对象（没数据库连接）时整段跳过 —— 不摆空表
            return None
        table = service.get_table(refs)
    except Exception:
        return None
    # ``get_table([])`` 会返回 ``None``，这里必须容住；之前直接 ``.get()``
    # 会让详情页整个 500（实测踩到）。
    if not table:
        return None
    return table if table.get("rows") else None


def _build_qdii_refs(symbols: list) -> list:
    """按符号建 ``refobj.QdiiReference``（含三档估值），失败了不放行空对象。

    取不到数据库连接时返回空列表 —— 让上层整段跳过，而不是摆一张空表。
    """
    service = _get_reference_service()
    source = getattr(service, "source", None)
    if source is None:
        return []

    from ref.refobj import build_qdii_reference

    refs = []
    for symbol in symbols:
        try:
            refs.append(build_qdii_reference(symbol, source=source))
        except Exception:
            continue
    return refs


def _get_fundest_service():
    factory = current_app.extensions.get("fundest_service_factory")
    return None if factory is None else factory()


def _build_reference(symbol: str) -> dict[str, Any] | None:
    """基金详情页的「参考数据」表（``php/ui/referenceparagraph.php``）。

    **取不到行情时返回 ``None`` 而不是空表**：模板据此整段跳过。
    宁可少一块，也不要在页面上摆一张全空的表格 —— 那会让人以为是数据丢了。

    标的清单由 :func:`reference_symbols_for_fund` 给出：基金自己 + 估值标的 +
    杠杆标的（查 ``fundpair``）+ 即期外汇 + 中间价，对应 PHP
    ``_qdiigroup.php`` 里 ``$arRef`` 与 ``$arLev`` 的装配。
    """
    service = _get_reference_service()
    if service is None:
        return None
    source = getattr(service, "source", None)
    try:
        symbols = reference_symbols_for_fund(symbol, source)
        table = service.get_table(_get_reference_refs(symbols))
    except Exception:
        return None
    return table if table["rows"] else None


def _get_reference_service():
    factory = current_app.extensions.get("reference_service_factory")
    return None if factory is None else factory()


def _get_reference_refs(symbols: list[str]):
    """按符号建引用对象。

    未显式注入 builder 时走真实路径：``refobj.MysqlReference`` +
    **先读库快照、再兜底联网**的行情装填（见 ``auto/refquote.py``）。
    ``source`` 由 reference service 提供，读快照要用它。
    """
    builder = current_app.extensions.get("reference_refs_builder")
    if builder is not None:
        return builder(symbols)
    from web.services.reference_service import build_stock_references

    service = _get_reference_service()
    return build_stock_references(symbols, source=getattr(service, "source", None))


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


@fund_bp.route("/qdii")
def qdii_page():
    """QDII 分类页，例如 /qdii、/qdii?sort=premium。

    对应 PHP ``woody/res/qdiicn.php`` —— 生产端把整个 QDII 分类的两张表
    （参考数据 + 估值）放在一页里，代码列分别是新浪外链和站内详情页链接。

    **没有重定向到 /fund/<group>**：那两页（qdiilof / qdiietf）是另一张
    "基金指数对照表"，列定义与服务都不同（``fundlistparagraph.php``），
    与 PHP 的页面划分一致，不合并。
    """
    page, groups = _build_qdii_page()
    return render_template("qdii.html", page=page, groups=groups)
