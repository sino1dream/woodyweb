"""对比页路由：``abcompare`` / ``ahcompare`` / ``adrhcompare`` 三个 PHP 页面。

## 对应 PHP

三个页面文件都只有 4 行，逻辑在 ``woody/res/php/_mystockgroup.php:154``。它们和
``category.py`` 那 16 个分类页**共用同一个 ``EchoAll()``**，所以路径也照"生产端
页面名去掉 cn 后缀"各自直挂，而不是塞进 ``/category/<name>``：这三个是工具页
（PHP ``EchoLinks()`` 把它们放在工具页那一段），不是基金分类。

## 为什么不自带数据源

``source`` 从 ``reference_service_factory`` 借：那里已经有个连好库的数据源；
对比页额外要读三张配对表，用同一个连接即可。测试注入 reference_service 时
``source`` 可能是假对象（没有 ``connection``），符号数组就是空，页面显示空态。

## AH 历史页为什么挂在这个蓝图

``ahhistorycn.php`` 在 PHP 里 require 的是 ``_ahhistory.php``（不是
``_mystockgroup.php``），但它的入口只有一处：``EchoAhParagraph()`` 在表格
**只有一行**时挂的链接。所以它跟这三个对比页同生共死，路由也放一起。
"""

from __future__ import annotations

from flask import (
    Blueprint,
    abort,
    current_app,
    render_template,
    request,
    url_for,
)

from web.bootstrap import install_paths

install_paths()

from sql.historytable import TABLE_COMMON_DISPLAY  # noqa: E402
from web.services.ah_history_service import build_ah_history  # noqa: E402
from web.services.compare_page_service import (  # noqa: E402
    COMPARE_PAGES,
    CompareNotFoundError,
    build_compare_page,
)


compare_bp = Blueprint("compare", __name__)


def _services() -> tuple:
    """取出 reference / fundest 服务与其数据源；缺哪个都退化成 ``None``。"""
    reference_factory = current_app.extensions.get("reference_service_factory")
    reference_service = reference_factory() if reference_factory else None
    fundest_factory = current_app.extensions.get("fundest_service_factory")
    fundest_service = fundest_factory() if fundest_factory else None
    source = getattr(reference_service, "source", None)
    return source, reference_service, fundest_service


def _nav(source: Any = None, current: str = "") -> list:
    """页尾列出**其它**两个对比页（PHP ``EchoLinks()`` 会把当前页跳过）。"""
    nav = []
    for name, definition in COMPARE_PAGES.items():
        if name == current:
            continue
        nav.append(
            {
                "name": name,
                "display": definition["display"],
                "title": definition["title"],
            }
        )
    return nav


def render_compare(name: str) -> str:
    """装配并渲染一个对比页。未知页名 404。"""
    source, reference_service, fundest_service = _services()
    try:
        page = build_compare_page(
            name,
            source=source,
            reference_service=reference_service,
            fundest_service=fundest_service,
            history_link_builder=lambda symbol: url_for(
                "compare.ah_history_page", symbol=symbol
            ),
        )
    except CompareNotFoundError:
        abort(404)
    return render_template(
        "compare_page.html",
        page=page,
        nav=_nav(source, current=name),
    )


@compare_bp.route("/abcompare")
def abcompare_page():
    """``abcomparecn.php``：A股和B股对比。"""
    return render_compare("abcompare")


@compare_bp.route("/ahcompare")
def ahcompare_page():
    """``ahcomparecn.php``：A股和H股对比。"""
    return render_compare("ahcompare")


@compare_bp.route("/adrhcompare")
def adrhcompare_page():
    """``adrhcomparecn.php``：ADR和H股对比。"""
    return render_compare("adrhcompare")


def _parse(args: Any) -> tuple:
    """``?start=&num=``，语义与 ``/history/<kind>/<symbol>`` 一致。

    ``num=0`` 表示"全部记录"；非法值退回默认，不让 URL 变成 500。
    """

    def to_int(value: Any, default: int) -> int:
        try:
            value = int(value)
        except (TypeError, ValueError):
            return default
        return value if value >= 0 else default

    return to_int(args.get("start"), 0), to_int(args.get("num"), TABLE_COMMON_DISPLAY)


def _paging_links(output: Any) -> dict:
    """给模板准备翻页链接参数（与 ``routes/history.py`` 同口径）。"""
    if output is None:
        return {"show": False, "total": 0}
    total = output["total"]
    start = output["start"]
    num = output["num"]
    if total <= 0 or (start == 0 and num == 0):
        return {"show": False, "total": total}
    return {
        "show": True,
        "total": total,
        "start": start,
        "num": num,
        "first": start + 1,
        "last": min(start + num, total),
        "prev_start": max(start - num, 0) if start > 0 else None,
        "next_start": start + num if start + num < total else None,
        "all": start != 0,
    }


@compare_bp.route("/ahhistory/<symbol>")
def ah_history_page(symbol: str):
    """``ahhistorycn.php?symbol=X``：这只 A 股的 AH 历史比价。"""
    start, num = _parse(request.args)
    source, _reference_service, _fundest_service = _services()
    output = build_ah_history(symbol, source=source, start=start, num=num)
    return render_template(
        "ah_history.html",
        page=output,
        symbol=symbol,
        paging=_paging_links(output),
    )
