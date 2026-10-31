"""义工群对冲计算器（``woody/res/overnightcnhcn.php``）。

## PHP 那边是什么

一个**独立工具页**，不在任何导航里（只有 ``EchoLinks()`` 那一大块相关链接
带入口），靠 ``?overnightcnh=95000`` 这样的查询串预填金额：

.. code-block:: php

    $acct = new StockAccount();          // 页面名 overnightcnh（文件名的后缀 cn.php 被剥掉）
    if (($strInput = GetEditInput()) === false)              // ① POST
        if (($strInput = $acct->GetQuery()) === false)       // ② ?overnightcnh=
            $strInput = '100000';                            // ③ 默认

一条纵向逻辑：一件"要平衡的离岸人民币"，按符号表算 23 只 QDII 各该买卖多少股，
再换算成对冲标的（QQQ / SPY / XOP …）的股数。

## 三个"点一下换个口径"的链接

页头除了说明文字还有三个 ``CopyPhpLink``，直接把金额换成 ``1MCL`` /
``1MGC`` / ``100000F`` 再算一遍（``MCL``/``MGC`` 是"1 桶/1 盎司折多少 CNH"
的反算，``F`` 是"这笔钱申购成份额"）。本版做成 GET 链接，语义相同。

## 与 PHP 的三处差异

1. **输入为空也认查询串**：PHP 的 ③ 默认值只在①②都取不到时生效，本版照抄；
2. **不 500**：净值缺失的行标成"算不出"，符号进页脚清单
   （PHP 8 在 ``价格 ÷ 官方净值`` 处抛 ``DivisionByZeroError`` 整页白屏）；
3. **只在 GET 上读、POST 只重算不写库** —— 与 PHP 一样，这一页没有任何写操作。
"""

from __future__ import annotations

from typing import Any, List

from flask import Blueprint, current_app, render_template, request, url_for

from web.bootstrap import install_paths

install_paths()

from web.services.overnight_cnh_service import (  # noqa: E402
    INPUT_QUERY_ITEM,
    build_fund_reference,
    build_future_reference,
    build_page,
    input_presets,
    overnight_symbols,
)

overnight_bp = Blueprint("overnight", __name__)


def _get_reference_service() -> Any:
    """取引用服务（只为借它的 ``source``，与 ``routes/compare.py`` 同一出处）。"""
    factory = current_app.extensions.get("reference_service_factory")
    return None if factory is None else factory()


def _input_text() -> Any:
    """``GetEditInput()`` → ``GetQuery()`` → 默认值（见模块文档的 ①②③）。

    ``None`` 表示"三段都没给"，交给服务层回落到 ``100000``；
    ``''``（点"清除"）是**明确给的空值**，服务层据此只出表单。
    """
    if request.method == "POST":
        if "clear" in request.form:
            return ""
        if "submit" in request.form:
            return (request.form.get("input") or "").strip()
        return None
    value = request.args.get(INPUT_QUERY_ITEM)
    return None if value is None else value.strip()


def _build_refs(symbols: List[str], source: Any) -> list:
    """按符号建引用对象；建不出来的位置留 ``None``（跳过并记入 missing）。"""
    builder = current_app.extensions.get("overnight_refs_builder")
    if builder is not None:
        return builder(symbols)
    return [build_fund_reference(symbol, source) for symbol in symbols]


@overnight_bp.route("/overnightcnh", methods=["GET", "POST"])
def overnight_cnh_page():
    """义工群对冲计算器（PHP ``woody/res/overnightcnhcn.php``，200 行）。

    路径用 ``/overnightcnh`` 而不是 ``/overnightcnhcn``：PHP 的文件名是
    「页面名 + ``cn.php``」，``UrlGetPage()`` 剥掉后缀后就是 ``overnightcnh``，
    与 ``GetOvernightCnhLink()`` 拼出来的链接同形。

    测试注入 ``overnight_refs_builder`` 时整段顶掉引用对象的构造（不必连库）。
    """
    service = _get_reference_service()
    source = getattr(service, "source", None)

    symbols = overnight_symbols()
    refs = _build_refs(symbols, source)

    page = build_page(
        input_text=_input_text(),
        symbols=symbols,
        refs=refs,
        source=source,
        fund_link=lambda symbol: url_for("fund.fund_page", symbol=symbol),
        stock_link=lambda symbol: url_for("stock.stock_detail_page", symbol=symbol),
        future_ref_builder=lambda symbol: build_future_reference(symbol, source),
    )
    return render_template(
        "overnight_cnh.html",
        page=page,
        presets=input_presets(),
    )
