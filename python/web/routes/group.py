"""分组页（单组的交易明细 + 该组资金汇总）的路由。

对应 PHP 两个调用现场（``woody/res/php/_mystockgroup.php`` 的 mystockgroup 分支）：:

    $group = new MyStockGroup($strGroupId, $arTransactionRef);
    if ($acct->EchoStockTransaction($group))        // 交易明细段落
        $acct->EchoMoneyParagraph($group, ...);      // 资金汇总段落（仅这一组）

与 ``/account`` 不同，这里**只渲染选中的那一个分组**：交易明细是这个组的
最新十条，资金汇总也只有这一组（没有末尾的"全部"行）。

## 为什么是演示数据

与 :mod:`web.routes.account` 完全一致 —— PHP 那边的数据来自 ``stockgroup`` /
``stockgroupitem`` / ``stocktransaction`` 三张表 + 登录态取出的会员与分组，
演示库里都没有、账号体系也暂缓，所以这里复用
:mod:`web.routes.account_demo_data` 的写死样例。分组名链接指回本页
（``?groupid=``），其余链接指向 PHP 页面、演示环境下点不通。
"""

from __future__ import annotations

from flask import Blueprint, Markup, render_template, request, url_for

from web.bootstrap import install_paths

install_paths()

from web.routes.account_demo_data import (  # noqa: E402
    DEMO_GROUP_NAMES,
    _demo_net_value,
    _demo_single_group,
    _demo_transaction_records,
    _ref_of,
)
from web.services.money_service import build_money_table  # noqa: E402
from web.services.transaction_service import (  # noqa: E402
    TABLE_COMMON_DISPLAY,
    build_transaction_table,
)

group_bp = Blueprint("group", __name__)

#: 默认显示哪个分组（没有 ``?groupid=`` 时）。
DEFAULT_GROUP_ID = "1"


def _group_link_of(group_id):
    """``group_link_of``：``$acct->GetGroupLink($strGroupId)`` 的替身。

    演示页没有分组页面，于是**指回本页**（``?groupid=``），点一下切上面的
    交易明细 —— 与 ``/account`` 同一套取舍，让这个注入口子真的在点。
    """
    name = DEMO_GROUP_NAMES.get(group_id, str(group_id))
    return Markup('<a href="%s">%s</a>'
                  % (url_for("group.group_page", groupid=group_id), name))


@group_bp.route("/group")
def group_page():
    """交易明细段落（``php/ui/transactionparagraph.php``）+ 单组资金汇总段落
    （``_stockaccount.php:213`` 的 ``EchoMoneyParagraphs([$group], ...)``）。"""
    group_id = request.args.get("groupid") or DEFAULT_GROUP_ID
    if group_id not in DEMO_GROUP_NAMES:
        # 演示页没有"分组不存在"这一说（PHP 那边会先 $acct->EchoStockGroup()
        # 判一次），给个兜底别摆一张空表
        group_id = DEFAULT_GROUP_ID
    records = _demo_transaction_records(group_id)

    # ``_stockaccount.php:206`` 的调用形状：ref=false（全部标的）、b_all=false
    # —— 标题是"交易记录"链接、只显示最新 TABLE_COMMON_DISPLAY 条。
    transaction_table = build_transaction_table(
        records,
        ref_builder=_ref_of,
        symbol_of=lambda stock_id: stock_id,
        net_value_of=_demo_net_value,
        b_all=False,
        group_id=group_id,
    )

    # 只对选中的这一组调 EchoMoneyParagraph —— 没有末尾的"全部"行。
    money_table = build_money_table(
        [_demo_single_group(group_id)],
        usdcny_ref=_ref_of("fx_susdcnh"),
        hkcny_ref=_ref_of("fx_hkdcnh"),
        group_link_of=_group_link_of,
        # 不注入 member_email_of —— 与"没登录"一致，于是没有测试列。
    )

    return render_template(
        "group.html",
        transaction_table=transaction_table,
        money_table=money_table,
        group_id=group_id,
        group_name=DEMO_GROUP_NAMES.get(group_id, group_id),
        groups=sorted(DEMO_GROUP_NAMES.items()),
        table_common_display=TABLE_COMMON_DISPLAY,
        record_count=len(records),
    )
