"""首页路由。"""

from __future__ import annotations

from flask import Blueprint, redirect, render_template, request, url_for

from calc.fundgroups import get_fund_group, get_fund_group_names
from web.services.category_page_service import get_category_nav


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
        # 16 个分类页（woody/res/biotechcn.php 一族）的入口清单。
        # 与 qdii_count 同一个取舍：只读注册表、不连库，库挂了首页照样能开。
        categories=get_category_nav(),
    )
