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

import os
from typing import Any

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from web.bootstrap import install_paths

install_paths()

from calc.fundgroups import get_symbol_group  # noqa: E402
from ref.externallink import get_xueqiu_url  # noqa: E402
from sql.historytable import TABLE_COMMON_DISPLAY  # noqa: E402
from web.services.admin_auth import denied_message, is_admin  # noqa: E402
from web.services.etf_dividend_service import build_etf_dividend  # noqa: E402
from web.services.rotation_service import (  # noqa: E402
    build_rotation_page,
    rotation_peers,
    rotation_symbol_array,
)
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


def _get_trading_service():
    """五档交易表服务；``/fund`` 与 ``/stock`` 共用同一个工厂。

    拿到之后补一个"轮动交易页"链接构造器：生 URL 要 ``url_for``，服务层不该
    依赖请求上下文（见 ``trading_service.make_rotation_link_builder``）。
    注入了自带构造器的替身时不覆盖。
    """
    factory = current_app.extensions.get("trading_service_factory")
    service = None if factory is None else factory()
    if service is not None and getattr(service, "link_builder", None) is None:
        from web.services.trading_service import make_rotation_link_builder

        service.link_builder = make_rotation_link_builder(url_for)
    return service


def _wants_live_refresh() -> bool:
    """本请求是否**额外联网补一次**五档盘口。

    与 ``routes/fund.py`` 的同名函数同一套路（那边是基金页的五档表）：

    * 默认不联网 —— 盘口来自 ``realtimequote`` 快照的 ``orderbook`` 列，
      由后台抓取进程每 60 秒写一次（见 ``auto/sql/quotestore.py``）；
    * ``?live=1`` 或 ``PALMMICRO_TRADING_LIVE_QUOTES=1`` 才显式联网一次。

    为什么这一页也要有：PHP 的 ``new StockReference()`` 每次请求都同步抓新浪，
    盘口永远是"此刻"的；本版读库，没有这个开关就等于永远比 PHP 慢一轮。
    """
    if request.args.get("live") in ("1", "true", "yes"):
        return True
    return os.getenv("PALMMICRO_TRADING_LIVE_QUOTES", "0") == "1"


def _fill_order_book(ref: Any) -> bool:
    """给引用对象**联网**补一次五档；返回是否补到。

    用 ``apply_network`` 而不是先读库再抓：这里要的就是"此刻的挂单"。
    快照里已经有盘口时也会覆盖 —— 用户显式要求了，就按联网的算
    （与 ``routes/fund.py:_fill_order_book`` 同义，那边注释更全）。
    """
    try:
        from ref.refquote import NetworkQuoteSource, apply_network

        return apply_network(ref, NetworkQuoteSource())
    except Exception:
        return False


def _get_fundest_service():
    """估值表服务；第 2 / 第 3 分支都用得到。"""
    factory = current_app.extensions.get("fundest_service_factory")
    return None if factory is None else factory()


def _get_detail_service() -> Any:
    """详情页服务；注入了就用注入的（测试里整个顶掉）。

    与 ``sma_service_factory`` 等同一套路子：默认实现要连库，而单测只想验
    "分支怎么选、模板怎么摆"，把装配过程顶掉最直接。
    """
    factory = current_app.extensions.get("stock_detail_service_factory")
    if factory is not None:
        return factory()
    return StockDetailService(
        source=getattr(_get_reference_service(), "source", None),
        link_builder=_table_link,
    )


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


def _holdings_note_links(symbol: str) -> dict:
    """估值表说明文字的三个链接（``get_holdings_detail`` 的 ``links``）。

    对应 PHP 那句里的三个站内链接：仓位估算页 / 基金持仓页 / 穷举持仓比例页。
    与 :func:`web.routes.holdings._note_links` 同一份内容 —— ``/stock`` 的
    持仓分支与 ``/holdings`` 页共用这句说明，两处必须给出一样的 URL。

    三个键**全都接上了**（2026-10-26 穷举页路由落地后补的第三个）；
    ``links`` 非 ``None`` 时整个字典缺键会退化成纯文字，所以三个键都给全。

    为什么替掉 PHP 的链接：``ref.stocklink`` 给的是 ``/woody/res/xxx.php``
    形 URL，那是迁移替身，Flask 站点上点不动（见
    ``stock_detail_service._holdings_est_note`` 的 ``links`` 说明）。
    """
    return {
        "position": url_for("history.history_page", kind="fundposition", symbol=symbol),
        "holdings": url_for("holdings.holdings_page", symbol=symbol),
        "exhaustive": url_for("holdings.exhaustive_holdings_page", symbol=symbol),
    }


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

    这条路由接 ``_mystock.php`` 四个分支里的**三个**，判断顺序与 PHP 的
    ``if / else if`` 一致：

    1. 第 2 分支 ``StockGetFundPairReference()`` —— ``fundpair`` 表里有这条记录
       （CK2b）。渲染 EST / 跟踪均线，``/stock/<symbol>`` 页面上会**多出**几张表。
    2. 第 3 分支 ``StockGetHoldingsReference()`` —— ``holdings`` 表里有持仓明细
       （CK4）。给持仓型基金（持有一篮子成分股，而不是跟踪单一标的）估值。
    3. 第 4 分支 ``else`` —— 其它非基金符号（CK3）。

    第 1 个分支（基金）由 ``/fund/<symbol>`` 承担。

    **顺序不能换**：PHP 是 ``else if`` 短路的，一个符号可能同时出现在
    ``fundpair`` 与 ``holdings`` 两张表里（演示库里就有），换序会整页换一套表。

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
    service = _get_detail_service()

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

    #: 第 3 分支（CK4）：``holdings`` 表里有持仓明细的走这条。
    #: 与第 2 分支同样**不**替它预判 —— 让它自己按表判断。
    detail = service.get_holdings_detail(
        symbol,
        ref=refs[0],
        sma_service=_get_sma_service(),
        history_service=_get_history_service(),
        fundest_service=_get_fundest_service(),
        #: 说明文字里的三个站内链接（``url_for`` 版，替掉 PHP 形死链）。
        #: 与 ``/holdings/<symbol>`` 页同一份字典，两处页面必须一致。
        links=_holdings_note_links(symbol),
    )
    if detail is not None:
        return render_template("stock_detail.html", detail=detail, symbol=symbol)

    #: ``?live=1`` 时才联网补盘口；默认读快照（见 :func:`_wants_live_refresh`）。
    #: 必须在 ``get_detail`` 之前 —— 服务层读的是引用对象上已有的五档。
    #: 只喂第 4 分支：五档交易表是那一段独有的，前两个分支不摆这张表。
    if _wants_live_refresh():
        _fill_order_book(refs[0])

    detail = service.get_detail(
        symbol,
        ref=refs[0],
        sma_service=_get_sma_service(),
        history_service=_get_history_service(),
        #: ``_mystock.php:150`` 的 A 股五档交易表；服务层自己带
        #: ``IsSymbolA`` 与"没有挂单量"两道守卫，这里不用预判。
        trading_service=_get_trading_service(),
    )
    return render_template("stock_detail.html", detail=detail, symbol=symbol)


@stock_bp.route("/stock/<symbol>/etfdividend")
def stock_etf_dividend_page(symbol: str):
    """分红数据页（PHP ``woody/res/etfdividendcn.php``，73 行）。

    与 ``/stock/<symbol>/edit``、``/stock/<symbol>/delete`` 同一类：都是
    "某个符号的一个子页面"，所以挂在 ``/stock/<symbol>/`` 下面，而不是
    另开 ``/etfdividend/<symbol>``。

    ## 引用对象由路由层建

    照 ``stock_detail_page`` 的做法走 ``_get_reference_refs``：这样测试注入
    ``reference_refs_builder`` 就能整个顶掉，不必连库。**建不出来不 404** ——
    PHP 那边 ``EchoStockGroup()`` 拿不到 ref 时页面照样出标题和链接，只是没有
    表；这里同样返回 200 让模板出空态（理由与 ``stock_detail_page`` 一致：
    旧链接里的符号可能只是暂时没行情，404 会让外链全废）。

    ## 数据源

    表格要读 ``stockdividend`` 与 ``stockhistory`` 两张表，连接从
    ``reference_service.source`` 借 —— 与 ``routes/compare.py`` 同一个出处。
    借不到（测试里的假服务）时页面只剩标题与链接，不炸。
    """
    refs = _get_reference_refs([symbol])
    reference_service = _get_reference_service()
    page = build_etf_dividend(
        symbol,
        source=getattr(reference_service, "source", None),
        ref=refs[0] if refs else None,
    )
    return render_template("stock_etf_dividend.html", page=page, symbol=symbol)


# ---------------------------------------------------------------------------
# 轮动交易页（PHP woody/res/rotationtradingcn.php）
# ---------------------------------------------------------------------------
def _build_rotation_refs(symbols: list, source: Any) -> list:
    """按符号建 ``QdiiReference``；建不出来的位置留 ``None``。

    **必须保持长度与顺序**：行序就是候选顺序，中间少一个不能把后面的挪上来 —
    服务层对 ``None`` 直接跳过（PHP 那边建对象不会失败，这里是防御）。

    用 ``build_qdii_reference`` 而不是 ``_get_reference_refs``：本页要的是
    ``GetEstNetValue()``（溢价列）与 ``GetEstRef()``（XOP/XBI 两族的表头符号），
    只有 QDII 那一层引用才有。与 ``routes/fund.py::_build_qdii_refs`` 同源。
    """
    if source is None:
        return [None for _ in symbols]

    from ref.refobj import build_qdii_reference

    refs = []
    for item in symbols:
        try:
            refs.append(build_qdii_reference(item, source=source))
        except Exception:
            refs.append(None)
    return refs


def _rotation_input() -> Any:
    """``GetEditInput()``（``php/ui/editinputform.php:5``）。

    **顺序有意义**：先看 ``clear``，再看 ``submit`` + ``input``。GET 请求返回
    ``None``（PHP 那边同样是 ``false``），由服务层回落到 -1000000 的占位值。
    """
    if request.method != "POST":
        return None
    if "clear" in request.form:
        return ""
    if "submit" in request.form:
        return request.form.get("input")
    return None


@stock_bp.route("/stock/<symbol>/rotationtrading", methods=["GET", "POST"])
def stock_rotation_trading_page(symbol: str):
    """轮动交易页（PHP ``woody/res/rotationtradingcn.php``，150 行）。

    与 ``/stock/<symbol>/etfdividend`` 同一类子页面。GET 打开表单、POST 只是
    **用新数量重算一遍**（PHP 也 POST 回自己），不下单、不写库。

    ## 四族之外不 404

    PHP 那边 ``EchoAll()`` 的 ``if`` 为假时整块跳过、页面只剩标题 —— 那仍然
    是一个 200 的页面。这里同样 200 + 空态说明，不把"这个符号不能轮动"说成
    "页面不存在"。

    ## 引用对象

    本基金与候选都走 ``_build_rotation_refs``。测试注入
    ``reference_refs_builder`` 时整段顶掉（连数据源都不用给），与
    ``stock_etf_dividend_page`` 同一套路。
    """
    symbols = rotation_symbol_array(symbol)
    service = _get_reference_service()
    source = getattr(service, "source", None)

    builder = current_app.extensions.get("reference_refs_builder")
    fund_ref = None
    peers: list = []
    if builder is not None:
        refs = builder([symbol])
        fund_ref = refs[0] if refs else None
        if symbols is not None:
            peers = builder(rotation_peers(symbol, symbols))
    elif symbols is not None:
        built = _build_rotation_refs([symbol], source)
        fund_ref = built[0]
        peers = _build_rotation_refs(rotation_peers(symbol, symbols), source)

    page = build_rotation_page(
        symbol,
        rotation_symbols=symbols,
        fund_ref=fund_ref,
        source=source,
        rotation_refs=peers,
        input_text=_rotation_input(),
        link_builder=lambda peer: url_for(
            "stock.stock_rotation_trading_page", symbol=peer
        ),
    )
    return render_template("stock_rotation_trading.html", page=page, symbol=symbol)


# ---------------------------------------------------------------------------
# 编辑页（PHP edit 族 16 个 edit*.php → 一个 <option> 参数）
# ---------------------------------------------------------------------------
#: ``GetStockEditDeleteLink()``（``php/stocklink.php:285``）把"修改"和"删除"
#: 两个链接并排放在详情页。本地的"修改"那一半落在 ``/stock/<symbol>/edit``
#: 索引页，所以"删除"也放在同一页的末尾（见 ``stock_option_index.html`` 的
#: 危险操作段），入口与 PHP 一样只有一个。


def _get_stock_option_service() -> Any:
    """编辑页服务；注入了就用注入的（测试里整个顶掉）。

    与 ``_get_detail_service`` 同一套路子：默认实现要连库，单测只想验
    "分派对不对、权限挡没挡住、未迁的怎么提示"，把装配顶掉最直接。
    """
    factory = current_app.extensions.get("stock_option_service_factory")
    if factory is not None:
        return factory()
    from web.services.stock_option_service import StockOptionService

    reference_service = _get_reference_service()
    return StockOptionService(source=getattr(reference_service, "source", None))


@stock_bp.route("/stock/<symbol>/edit")
def stock_option_index(symbol: str):
    """某个符号的"可编辑项"索引 —— PHP 没有这一页，16 个页面只能手敲 URL。

    补这一页的理由：PHP 的 16 个 ``edit*.php`` 都不进任何导航
    （``GetStockMenuLinks()`` 与 ``EchoLinks()`` 都没有入口），本版把它们收成
    一个 ``<option>`` 参数以后，缺一个索引就更没法发现了。列表顺序与
    ``GetStockOptionArray()`` 一致。

    16 项分三组：**能在这页编辑的** / **另有专门页的**（季报、持仓 —— 直接
    给专门页链接，不给 ``<option>`` 链接，免得点进来再被弹走）/ **还没迁的**
    （现在是空的，留一段逻辑在那儿，加新选项时不必改模板）。
    """
    service = _get_stock_option_service()
    options = service.list_options()
    return render_template(
        "stock_option_index.html",
        symbol=symbol,
        options=options,
        migrated=[
            item for item in options if item["migrated"] and not item["route"]
        ],
        dedicated=[item for item in options if item["route"]],
        pending=[
            item for item in options if not item["migrated"] and not item["route"]
        ],
        admin=is_admin(),
    )


@stock_bp.route("/stock/<symbol>/delete", methods=["GET", "POST"])
def stock_delete(symbol: str):
    """删标的（PHP ``woody/res/deletesymbol.php`` → ``php/deletestock.php``）。

    ## PHP 那边是什么

    一个 ``SymbolAccount`` 子类，只实现 ``AdminProcess()``，``AdminRun()``
    跑完无条件 ``SwitchToSess()`` 跳回 —— 页面上什么都不显示，连"删了没有"
    都看不出来。入口是 ``GetStockEditDeleteLink()`` 拼的带 ``confirm()`` 的
    ``<a href>``（GET 副作用）。

    ## 这里为什么拆成 GET 预览 + POST 执行

    1. 写操作不走 GET（与 ``/submit``、``/holdings/submit`` 同一取舍）；
    2. 这道操作**几乎总是被拦**：五道闸门里 ``stockgroupitem`` 在演示库里
       绝大多数符号上都命中（标的都在某个分组里），PHP 拦下时静默跳回，
       管理员只能猜。预览页把"被哪道闸门拦、还差什么"全列出来；
    3. 要删的行数得先看见 —— ``confirm()`` 里一句"确认删除"不足以说明
       "这会带走 814 行历史价格"。

    ## 权限

    PHP 的做法是 ``AdminRun()`` 里判 ``IsAdmin()``：非管理员连
    ``AdminProcess()`` 都不进。这里同样：GET 只读预览（谁都能看，反正
    页面上没有一个字是隐藏数据），POST 非管理员 403 且**一行都不写**。

    ## 执行完去哪

    删成功 → 回 ``/stock`` 列表（``/stock/<symbol>`` 已经不存在了）；
    被拦住或符号不存在 → 留在预览页，flash 说明原因。
    """
    from web.services import delete_symbol_service

    symbol = (symbol or "").strip().upper()
    connection = _delete_connection()
    admin = is_admin()

    if request.method == "POST":
        if not admin:
            return render_template(
                "error.html", message=denied_message(), status=403
            ), 403
        include_orphans = bool(request.form.get("include_orphans"))
        outcome = delete_symbol_service.execute(
            connection, symbol, include_orphans=include_orphans
        )
        flash(outcome["message"])
        if outcome["ok"]:
            return redirect(url_for("stock.stock_page"))
        return redirect(url_for("stock.stock_delete", symbol=symbol))

    return render_template(
        "stock_delete.html",
        preview=delete_symbol_service.preview(connection, symbol),
        admin=admin,
    )


def _delete_connection() -> Any:
    """删标的那条连接。

    单独一个钩子而不是复用 ``_get_reference_service().source``：删表要的是
    DB-API 连接（``cursor`` / ``rowcount``），参考对象那层只暴露查询方法。
    测试注入 ``delete_connection_factory`` 就整个顶掉，不碰真库。
    """
    factory = current_app.extensions.get("delete_connection_factory")
    if factory is not None:
        return factory()
    from web.db import get_db

    return get_db()


@stock_bp.route("/stock/<symbol>/edit/<option>", methods=["GET", "POST"])
def stock_option_edit(symbol: str, option: str):
    """通用编辑页（PHP ``edit<option>cn.php`` → ``_editstockoption.php``）。

    ## 一个路由顶 16 个页面

    PHP 的 16 个页面文件都是同一段 4 行壳子，差别只有 URL 里的页面名 ——
    它决定 ``_getEditStockOptionSubmit()`` 把哪一项交给表单。本版把页面名变成
    路径参数，读写分派全在
    :class:`web.services.stock_option_service.StockOptionService` 里。

    ## 写入语义

    与 ``_SubmitOptionsAccount::Process()`` 一致：按选项分派，**各自实现**。
    共同的那一半（日频表）走 ``_updateOptionDailySql`` —— 空值（含 ``'0'``）
    按日期删、否则 ``WriteDaily``。

    ## 权限

    与其它写入口一致：``is_admin()`` 为假时 POST 直接 403，GET 只读展示
    （表单控件 disabled）。PHP 那边非管理员进来会看到 ``$strVal`` 只读 +
    按钮变"返回"，连 POST 都不会发；这里保留表单但挡住写，效果等价且更好懂。

    **例外**：``member_level`` 的那一项（基金申购金额，PHP
    ``STOCK_OPTION_AMOUNT`` 分支没有 ``$bAdmin`` 判断）只要求"有登录身份"，
    因为它改的是登录会员**自己**名下那一行 —— 闸门仍在（没身份就写不了），
    只是不要求管理员。

    ## 已有专门页的两项

    ``editquarterreport`` / ``editstockholdings`` 的写入口在
    ``/holdings/quarter/<symbol>`` 与 ``/holdings/exhaustive/<symbol>``，
    这里 **GET 与 POST 都 302 过去**，不重复实现一份 —— 两处各写一份写入口，
    迟早会跑偏（季报那张表的 ``DailyStringSql`` 与日频表口径还不一样）。
    """
    from web.services.stock_option_service import get_stock_option

    spec = get_stock_option(option)
    if spec is None:
        return render_template(
            "error.html",
            message="没有「%s」这个编辑项。可选项见 /stock/%s/edit。" % (option, symbol),
            status=404,
        ), 404

    if spec.route:
        if request.method == "POST":
            flash(
                "「%s」在专门页里提交，本页不接收 —— 已经把你带到那儿了。"
                % spec.submit
            )
        return redirect(url_for(spec.route, symbol=symbol))

    service = _get_stock_option_service()
    admin = is_admin()
    #: 能写 ≠ 是管理员（见 docstring 的"例外"）。模板用它决定要不要 disable。
    editable = admin or spec.member_level
    error = ""
    form = service.get_form(symbol, option)

    if request.method == "POST":
        if not editable:
            return render_template(
                "error.html", message=denied_message(), status=403
            ), 403
        result = service.submit(
            symbol,
            option,
            request.form.get("date"),
            request.form.get("value"),
        )
        if result.get("ok"):
            flash("%s %s：%s" % (symbol, spec.submit, result.get("message")))
        else:
            error = result.get("message") or "没有写入。"
        #: POST-Redirect-GET 只在成功时做：失败要把人留在页面上看错误，
        #: 顺便把刚才提交的值原样填回去（否则辛苦填的一长串就没了）。
        if result.get("ok"):
            return redirect(
                url_for("stock.stock_option_edit", symbol=symbol, option=option)
            )
        form = dict(form or {})
        form["date"] = request.form.get("date") or form.get("date", "")
        form["value"] = request.form.get("value") or form.get("value", "")

    return render_template(
        "stock_option_form.html",
        symbol=symbol,
        admin=admin,
        editable=editable,
        error=error,
        form=form,
    )
