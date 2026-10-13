"""首页路由。"""

from __future__ import annotations

from flask import Blueprint, redirect, render_template, request, url_for

from calc.fundgroups import get_fund_group, get_fund_group_names
from core.stocksymbol import qdii_get_symbol_array


main_bp = Blueprint("main", __name__)


def _list_groups():
    """列出全部基金分组，供首页给出入口。

    直接读分组定义而不是走 FundListService：首页只需要分组名、标题和数量，
    不需要连数据库，这样数据库不可用时首页仍然能打开。
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


@main_bp.route("/")
def index():
    """渲染基金代码输入页与分组入口。"""
    symbol = request.args.get("symbol", "").strip()
    if symbol:
        return redirect(url_for("fund.fund_page", symbol=symbol))
    return render_template(
        "index.html",
        groups=_list_groups(),
        # 美股QDII 分类页（woody/res/qdiicn.php）的符号数。同样不连库 ——
        # 数量只取决于分组定义，与数据库里有没有数据无关。
        qdii_count=len(qdii_get_symbol_array()),
    )
