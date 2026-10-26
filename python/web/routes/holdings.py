"""持仓型基金的「基金持仓」页路由。

对应 PHP ``woody/res/holdingscn.php``（139 行）—— 页面本身只有 4 行有效
逻辑（``EchoAll()``），重活全在它拉的三个 ``php/ui`` 段落里：

* ``EchoHoldingsEstParagraph``（估值表 + 说明 + 净值记录）——
  与 ``/stock/<symbol>`` 第 3 分支（CK4）共用
  :func:`web.services.stock_detail_service.get_holdings_est_sections`；
* ``EchoReferenceParagraph``（参考数据表）；
* 七列持仓测算表 + ``EchoAdrhParagraph``（ADR/H 对比表）——
  本页独有，见 :mod:`web.services.holdings_service`。

## 与 PHP 的两处结构差异

1. **没有账号层**：PHP 整页藏在 ``$acct->EchoStockGroup()`` 后面
   （``HasData() || IsAdmin()``），没行情时整页只剩链接。本站点没有账号体系，
   守卫收进服务层（``get_page`` 返回 ``None``），路由照站点既有做法渲染
   "取不到数据"的提示而不是空白页；
2. **没有「分组名称」表**：PHP 的 ``EchoStockGroupParagraph()`` 会先摆一张
   分组表（生产端 ``holdingstable`` 之前那张），属于账号体系（暂缓）。

``/holdings/<symbol>`` 与 ``/stock/<symbol>`` 的第 3 分支内容重叠但不相同：
后者还有均线 / 历史价 / 净值价格比较 / 份额四段（``_mystock.php`` 的公共尾部），
前者按 ``holdingscn.php`` 只有估值 + 参考 + 测算 + ADR 四段。
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, current_app, render_template, request, url_for

from web.bootstrap import install_paths

install_paths()

from calc.fundgroups import get_symbol_group  # noqa: E402
from core.exhaustiveholdings import (  # noqa: E402
    DEFAULT_EXHAUSTIVE_ROWS,
    HOLDINGS_NETVALUE_DIFF,
)
from web.services.holdings_service import HoldingsService  # noqa: E402


holdings_bp = Blueprint("holdings", __name__)


def _get_reference_service():
    """参考数据服务；它的 ``source`` 是持仓页查名字/配对表要用到的。"""
    factory = current_app.extensions.get("reference_service_factory")
    return None if factory is None else factory()


def _get_fundest_service():
    """估值表服务；与 ``/stock`` / ``/fund`` 共用同一个工厂。"""
    factory = current_app.extensions.get("fundest_service_factory")
    return None if factory is None else factory()


def _get_history_service():
    """净值记录段落服务（``EchoNetValueHistoryParagraph``）。"""
    factory = current_app.extensions.get("history_service_factory")
    return None if factory is None else factory()


def _get_service() -> HoldingsService:
    """持仓页服务；注入了就用注入的（测试里整个顶掉）。

    默认实现从**现有**工厂里取 ``source`` / 估值表 / 净值记录三个依赖 ——
    不新增 app 级的工厂注册，与 ``routes/stock.py::_get_detail_service``
    的路子一致（那边也是先查扩展、没有就现造）。
    """
    factory = current_app.extensions.get("holdings_service_factory")
    if factory is not None:
        return factory()
    return HoldingsService(
        getattr(_get_reference_service(), "source", None),
        fundest_service=_get_fundest_service(),
        history_service=_get_history_service(),
    )


def _detail_link(symbol: str) -> str:
    """测算表首格的链接：按符号类型分流到 ``/fund`` 或 ``/stock``。

    与 ``routes/stock.py::_link_builders`` 的 ``detail_link`` 同一判据 ——
    成分股里也可能有 QDII 基金（持仓表允许基金持仓基金），直接一律
    ``/stock`` 会把基金导到一个没有这一行的分组页。
    """
    if get_symbol_group(symbol) is None:
        return url_for("stock.stock_detail_page", symbol=symbol)
    return url_for("fund.fund_page", symbol=symbol)


def _note_links(symbol: str) -> dict:
    """估值表说明文字的三个链接（``get_holdings_est_sections`` 的 ``links``）。

    对应 PHP 那句里的三个站内链接：仓位估算页 / 基金持仓页（本页自己，
    PHP 那边同样是自链接）/ 穷举持仓比例页。三个**全都接上了**（2026-10-26：
    穷举页路由落地后补的第三个）—— 说明文字与生产端逐字对齐,不再有
    "退化成纯文字"的那一段。
    """
    return {
        "position": url_for("history.history_page", kind="fundposition", symbol=symbol),
        "holdings": url_for("holdings.holdings_page", symbol=symbol),
        "exhaustive": url_for("holdings.exhaustive_holdings_page", symbol=symbol),
    }


@holdings_bp.route("/holdings/<symbol>")
def holdings_page(symbol: str):
    """渲染「基金持仓」页（``holdingscn.php``）。

    **没有内容时仍返回 200**（服务层返回 ``None``），渲染"取不到数据"提示：
    旧链接里的符号可能只是暂时没有行情或持仓明细，直接 404 会让历史外链
    全部失效（与 ``/stock/<symbol>`` 同一条约定）。
    """
    page = _get_service().get_page(
        symbol,
        link_builder=_detail_link,
        note_links=_note_links(symbol),
    )
    return render_template("holdings.html", page=page, symbol=symbol)


# ---------------------------------------------------------------------------
# 穷举持仓页（woody/res/exhaustiveholdingscn.php）
# ---------------------------------------------------------------------------
def _get_exhaustive_service():
    """穷举页服务；注入了就用注入的（测试里整个顶掉）。

    与 ``_get_service`` 同一套约定：默认实现从**现有**工厂取 ``source``,
    不新增 app 级工厂注册。
    """
    factory = current_app.extensions.get("exhaustive_holdings_service_factory")
    if factory is not None:
        return factory()
    from web.services.exhaustive_holdings_service import ExhaustiveHoldingsService

    return ExhaustiveHoldingsService(getattr(_get_reference_service(), "source", None))


def _resolve_threshold(raw):
    """``GetEditInput()`` 的取值语义（``exhaustiveholdingscn.php:346``）。

    返回 ``(是否渲染, 阈值百分数, 输入框回填串)``。三个分支：

    * 没有 ``?input=``（``None``）—— 用默认阈值 ``HOLDINGS_NETVALUE_DIFF``，
      输入框回填 ``1.0``；
    * ``?input=`` 是空串 —— PHP ``$strInput = ''`` 时**整段不算**，
      页面只剩输入框（``if ($strInput != '')``），所以不渲染；
    * 有值 —— 原样换算，输入框回填原文。

    **不能用 ``if (x)`` 判空**：``'0' != ''`` 在 PHP 里是真，填 0 是合法输入
    （阈值 0 = 每个有涨跌的日子都进方程）。所以 ``None`` 与 ``""`` 必须分开判。
    """
    if raw is None:
        return (True, HOLDINGS_NETVALUE_DIFF, str(HOLDINGS_NETVALUE_DIFF))
    if raw == "":
        return (False, HOLDINGS_NETVALUE_DIFF, "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        #: 填了非数字：PHP 的 floatval('abc') 是 0.0 —— 阈值 0，照样算
        value = 0.0
    return (True, value, raw)


@holdings_bp.route("/holdings/exhaustive/<symbol>")
def exhaustive_holdings_page(symbol: str):
    """渲染「穷举持仓比例」页。

    **没有账号层**：PHP 是 ``SymbolAccount`` + ``$acct->EchoStockGroup()``
    （登录 + 分组可见性）。本项目的账号体系暂缓，按 admin 分组页的既定做法
    收敛成"固定 demo 身份 + 权限闸门恒开"，所以这里不带任何鉴权参数,
    直接从 URL 取符号 —— 用户要的就是"不用登录客户界面，直接跳转"。

    阈值走 ``?input=``（GET）：这个参数只影响读，没有副作用，保留 GET 是
    照抄 PHP 的表单形态（写操作才一律 POST，见 ``/transactions``）。
    """
    render, threshold, threshold_input = _resolve_threshold(request.args.get("input"))
    page = None
    if render:
        page = _get_exhaustive_service().get_page(
            symbol,
            threshold=threshold,
            num=DEFAULT_EXHAUSTIVE_ROWS,
            #: 管理员写入链接恒关（见 core.exhaustiveholdings.position_cell）
            admin=False,
        )
    return render_template(
        "exhaustive_holdings.html",
        page=page,
        symbol=symbol,
        threshold_input=threshold_input,
        default_threshold=HOLDINGS_NETVALUE_DIFF,
        skipped_by_input=not render,
    )
