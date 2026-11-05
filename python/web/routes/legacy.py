"""``/woody/res/*.php`` 旧地址的兼容跳转。

生产端的地址就是文件名（``sz161226cn.php``），本地换成按功能分组的路径后
旧地址全部 404。这一层把旧地址 302 到对应的本地页面，映射表在
:mod:`web.services.legacy_urls`。

**只跳转，不渲染页面**：``sz161226cn.php`` 在生产端也只有 4 行壳子
（``require('php/_chinafuture.php')``），真正的实现早就在 ``/fund/<symbol>`` 里了。

三种结果：

1. 映射命中 → 302（带上了查询串里除 ``symbol``/``sym`` 之外的参数，
   ``overnightcnhcn.php?overnightcnh=100000`` 这种要靠它）；
2. 这一页需要符号但没给 → 200 + 一个输入框（``SymbolRequired``）；
3. 没有对应页 → 404 + 一句准确的原因（``legacy_urls.KNOWN_MISSING``）。
"""

from __future__ import annotations

from urllib.parse import urlencode

from flask import Blueprint, redirect, render_template, request

from web.services import legacy_urls


legacy_bp = Blueprint("legacy", __name__)


@legacy_bp.route("/woody/res/<path:page>")
def legacy_page(page: str):
    """把 PHP 旧地址转到本地路径。"""
    # PHP 那边符号走 ``?symbol=``；``?sym=`` 是手敲时的常见写法，一并收下
    symbol = request.args.get("symbol") or request.args.get("sym") or ""
    try:
        target = legacy_urls.resolve(page, symbol)
    except legacy_urls.SymbolRequired:
        # 不猜符号：``/stock//edit/xxx`` 会 404，``/fund/`` 会渲染空页，
        # 都不如直接要一个。表单提交回本地址（GET），填完就跳。
        return render_template(
            "legacy_notice.html", page=page, need_symbol=True, symbol="", reason=""
        )

    if target is None:
        return (
            render_template(
                "legacy_notice.html",
                page=page,
                need_symbol=False,
                symbol="",
                reason=legacy_urls.missing_reason(page),
            ),
            404,
        )

    # 转发其它查询参数（``symbol`` 已经进了路径，不要重复带）
    extra = [(key, value) for key, value in request.args.items(multi=True)
             if key not in ("symbol", "sym")]
    if extra:
        target = "%s?%s" % (target, urlencode(extra))
    return redirect(target, code=302)
