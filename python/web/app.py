"""Palmmicro Python Web 服务入口。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Mapping


# 直接执行 python/web/app.py 时，先把 python 和 python/auto 放入导入路径。
WEB_DIR = Path(__file__).resolve().parent
PYTHON_DIR = WEB_DIR.parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from web.bootstrap import install_paths

install_paths()

from flask import Flask, jsonify, render_template, request, url_for
from web.config import Config
from web.db import DatabaseUnavailableError, init_app
from web.routes import register_blueprints
from web.services.fund_list_service import (
    FundGroupNotFoundError,
    default_fund_list_service_factory,
)
from web.services.fund_service import FundNotFoundError, default_fund_service_factory
from web.services.history_service import (
    HistoryNotFoundError,
    default_history_service_factory,
)
from web.services.fundest_service import FundEstService, default_fundest_service_factory
from web.services.calibration_service import (
    CalibrationService,
    default_calibration_service_factory,
)
from web.services.reference_service import ReferenceService, default_reference_service_factory
from web.services.status_service import default_status_service_factory
from web.services.sma_service import SmaService, default_sma_service_factory
from web.services.stock_service import StockService, default_stock_service_factory
from web.services.trading_service import TradingService, default_trading_service_factory


def _history_page_link(symbol: Any, kind: Any) -> Any:
    """给 ``CalibrationService`` 用的链接构造器：指向那个 kind 的历史页。

    ## 为什么在 ``web/app.py`` 而不是服务层

    服务层不认识 ``url_for``（那样测试就得先搭请求上下文）。而这张表的
    ``link_builder`` 契约是"给符号与页面名，返回 URL"，正好够这里的路由表达，
    所以在装配层补上即可 —— 服务 layer 那一侧不用改。

    PHP 那边对应 ``GetCalibrationHistoryLink($strSymbol)``，
    拼的是 ``calibrationhistorycn.php?symbol=…``。
    """
    return url_for("history.history_page", kind=kind, symbol=symbol)


def _fund_list_link(symbol: Any, kind: Any) -> Any:
    """给 ``qdii_fund_list`` 用的链接构造器：按 ``kind`` 决定指向哪一页。

    ``kind == 'fund'`` 是「跟踪」列（``$ref->GetMyStockLink()``，指向标的自己的
    详情页）；其余是历史页（这里是 ``calibrationhistory``）。
    两种都在服务层外拼，理由同 :func:`_history_page_link`。
    """
    if kind == "fund":
        return url_for("fund.fund_page", symbol=symbol)
    return url_for("history.history_page", kind=kind, symbol=symbol)


def _error_response(message: str, status: int):
    """按请求类型返回 JSON 或 HTML 错误。"""
    if request.path.startswith("/api/"):
        return jsonify({"error": message}), status
    return render_template("error.html", message=message, status=status), status


def create_app(
    config: Mapping[str, Any] | None = None,
    fund_service_factory: Any = None,
    status_service_factory: Any = None,
    fund_list_service_factory: Any = None,
    history_service_factory: Any = None,
    updater_factory: Any = None,
    reference_service_factory: Any = None,
    reference_refs_builder: Any = None,
    fundest_service_factory: Any = None,
    fundest_refs_builder: Any = None,
    trading_service_factory: Any = None,
    trading_refs_builder: Any = None,
    stock_detail_service_factory: Any = None,
    stock_service_factory: Any = None,
    sma_service_factory: Any = None,
    sma_history_factory: Any = None,
    sma_refs_builder: Any = None,
    calibration_service_factory: Any = None,
    fundlist_link_builder: Any = None,
    fundlist_refs_builder: Any = None,
    admin_source_factory: Any = None,
    #: 交易页（web/routes/transactions.py）的数据源工厂 —— 与
    #: ``admin_source_factory`` 同一套路：缺省 None → 请求级库连接，
    #: 测试注入假数据源即可不连库。
    transactions_source_factory: Any = None,
    #: 持仓引用构造器的注入口：``/fund/<group>/<symbol>`` 走不走
    #: ``EchoHoldingsEstParagraph`` 那一支，取决于这一步给不给引用对象。
    #: 与 ``fundest_refs_builder`` / ``sma_refs_builder`` 同一套路 ——
    #: 让"持仓型 vs QDII 型"的分流能不连库验证。
    holdings_ref_builder: Any = None,
) -> Flask:
    """创建 Flask 应用，允许测试注入服务工厂。"""
    app = Flask(__name__)
    app.config.from_object(Config)
    if config:
        app.config.update(config)
    app.json.ensure_ascii = False

    app.extensions["fund_service_factory"] = fund_service_factory or default_fund_service_factory
    app.extensions["status_service_factory"] = status_service_factory or default_status_service_factory
    app.extensions["fund_list_service_factory"] = (
        fund_list_service_factory or default_fund_list_service_factory
    )
    app.extensions["history_service_factory"] = (
        history_service_factory or default_history_service_factory
    )
    # 实时行情更新器：缺省用进程级单例（quoteupdater.get_updater），
    # 测试可注入带假行情源的实例。
    if updater_factory is not None:
        app.extensions["updater_factory"] = updater_factory

    # 基金详情页的「参考数据」表（php/ui/referenceparagraph.php）。
    # 两个口子都可以注入：
    #   reference_service_factory  —— 替换整个表格服务；
    #   reference_refs_builder     —— 只替换"按符号建引用对象"这一步
    #                                 （默认实现会真的抓新浪，测试必须顶掉）。
    app.extensions["reference_service_factory"] = (
        reference_service_factory or default_reference_service_factory
    )
    if reference_refs_builder is not None:
        app.extensions["reference_refs_builder"] = reference_refs_builder

    # 基金详情页的「估值」表（php/ui/fundestparagraph.php）。
    # 与参考数据表不同，它的 refs builder **没有默认实现** —— 三档估值必须
    # 由带 estimator 的引用对象填，凭页面现有的单一估值硬塞进「官方EST」列
    # 等于又造一条 EST 口径。详见 web/routes/fund.py 的 _build_fundest()。
    app.extensions["fundest_service_factory"] = (
        fundest_service_factory or default_fundest_service_factory
    )
    if fundest_refs_builder is not None:
        app.extensions["fundest_refs_builder"] = fundest_refs_builder

    # 基金详情页的「五档交易」表（php/ui/tradingparagraph.php）。
    # 盘口数据只有抓实时行情才有 —— 库快照里没有买卖五档，所以：
    #   trading_service_factory  —— 替换整个表格服务；
    #   trading_refs_builder     —— 只替换"按符号建引用对象"这一步，
    #                               默认实现会按需联网（见 web/routes/fund.py）。
    app.extensions["trading_service_factory"] = (
        trading_service_factory or default_trading_service_factory
    )
    if trading_refs_builder is not None:
        app.extensions["trading_refs_builder"] = trading_refs_builder

    # 非基金详情页 ``/stock/<symbol>`` 的段落组装器（CK2b / CK4 两个分支）。
    # **没有默认工厂**：它要 ``link_builder``（生成站内链接需要 ``url_for``，
    # 也就是请求上下文），装配只能在路由层做（``routes/stock.py``）。
    # 所以这里是"注入了才注册"，没注入时路由自己按默认方式装配。
    if stock_detail_service_factory is not None:
        app.extensions["stock_detail_service_factory"] = stock_detail_service_factory

    # 股票列表页（php/ui/stockparagraph.php）：从 stock 表分页取符号。
    app.extensions["stock_service_factory"] = (
        stock_service_factory or default_stock_service_factory
    )

    # 均线表（php/ui/smaparagraph.php）。数值来自 auto/stockhistory.py，
    # 本服务只负责摆表。可注入 sma_service_factory 整块替换（测试用假
    # StockHistory 就走这个口子）。
    app.extensions["sma_service_factory"] = (
        sma_service_factory or default_sma_service_factory
    )
    if sma_history_factory is not None:
        app.extensions["sma_history_factory"] = sma_history_factory
    if sma_refs_builder is not None:
        app.extensions["sma_refs_builder"] = sma_refs_builder

    # 详情页里嵌入的「校准记录」（php/ui/calibrationhistoryparagraph.php）。
    # 取数复用 HistoryService 的 calibrationhistory —— 与 /history 那张表同源，
    # 这里只补一个链接构造器：URL 要 url_for 才拼得出来，服务层不认识它。
    app.extensions["calibration_service_factory"] = (
        calibration_service_factory
        or (lambda: default_calibration_service_factory(_history_page_link))
    )

    # QDII 详情页的「基金指数对照表」：列里的两个链接（标的详情页 / 校准历史页）
    # 要 url_for 才拼得出来，所以像上面那样在装配层注入。
    app.extensions["fundlist_link_builder"] = fundlist_link_builder or _fund_list_link
    # ``$arLev`` 默认查 ``fundpair``；测试可以直接给一批引用对象。
    app.extensions["fundlist_refs_builder"] = fundlist_refs_builder

    # 后台管理（web/routes/admin.py）的数据源工厂。缺省 None → 路由现场用
    # 请求级库连接（web.db.get_db）；测试注入假数据源即可不连库。
    if admin_source_factory is not None:
        app.extensions["admin_source_factory"] = admin_source_factory
    if transactions_source_factory is not None:
        app.extensions["transactions_source_factory"] = (
            transactions_source_factory)
    # 持仓引用构造器；缺省 None → 路由现场连库查 holdings 表
    # （见 routes/fund.py::_build_holdings_ref）。
    if holdings_ref_builder is not None:
        app.extensions["holdings_ref_builder"] = holdings_ref_builder

    init_app(app)
    register_blueprints(app)

    @app.errorhandler(FundNotFoundError)
    def handle_fund_not_found(exc: FundNotFoundError):
        return _error_response("未找到基金代码：%s" % exc, 404)

    @app.errorhandler(FundGroupNotFoundError)
    def handle_fund_group_not_found(exc: FundGroupNotFoundError):
        return _error_response("未找到基金分组：%s" % exc, 404)

    @app.errorhandler(HistoryNotFoundError)
    def handle_history_not_found(exc: HistoryNotFoundError):
        return _error_response("未找到历史数据：%s" % exc, 404)

    @app.errorhandler(DatabaseUnavailableError)
    def handle_database_unavailable(exc: DatabaseUnavailableError):
        return _error_response(str(exc), 503)

    @app.errorhandler(ValueError)
    def handle_value_error(exc: ValueError):
        return _error_response(str(exc), 400)

    return app


app = create_app()


if __name__ == "__main__":
    app.run(
        host=os.getenv("PALMMICRO_WEB_HOST", "127.0.0.1"),
        port=int(os.getenv("PALMMICRO_WEB_PORT", "5000")),
        debug=os.getenv("PALMMICRO_WEB_DEBUG", "0") == "1",
    )
