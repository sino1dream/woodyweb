"""账户页（交易明细 + 资金汇总）的路由。

对应 PHP 两个调用现场::

    woody/res/php/_mystockgroup.php:143
        if ($acct->EchoStockTransaction($group))
            $acct->EchoMoneyParagraph($group, new CnyReference('USCNY'), new CnyReference('HKCNY'));

    woody/res/php/_myportfolio.php:182
        $portfolio->arStockGroup[] = $portfolio;      // "全部"这一行
        $acct->EchoMoneyParagraphs($portfolio->arStockGroup, USCNY, HKCNY);

``EchoStockTransaction()`` 内部（``_stockaccount.php:206``）先出交易明细
段落再出投资组合段落，所以**一页上两段是挨着的**。本页把交易明细与资金
汇总并在一起：前者按某一个分组显示最新十条，后者把每个分组各出一组行，
外加一个"全部"行。

## 为什么是演示数据

PHP 那边的数据来自三张表：``stockgroup``（按 member 的分组）、
``stockgroupitem``（分组内的标的与预统计量）、``stocktransaction``
（每一笔买卖）。**演示库里这三张表都不存在**，而账号登录体系
（``$acct->Auth()``）用户要求暂缓，所以这里用一份**写死的样例数据集**。

与 :mod:`web.routes.portfolio` 的取舍一致：列定义与行装配都在服务层，
路由只负责"把样例数据喂进去"。

## 演示数据覆盖的分支

**交易明细**按备注分五种形态（``_echoTransactionTableItem`` 里那三层
``if`` 的全部分支）：

==================  ====================================================
备注                 表现
==================  ====================================================
``申购`` + QDII      校准链接按**上一交易日**净值（``GetClosePrev``）
``申购`` + 非 QDII   校准链接按**当日**净值（``GetClose``）
``申购`` 且价等于净值  **不出**校准链接（PHP 的 ``$strNetValue != $strPrice``）
不含 ``申购``        只有清空链接
空备注               两个链接都没有
==================  ====================================================

**资金汇总**按 ``empty()`` 判定出 1~3 行（分组 1 只有港币行、分组 2
只有美元行、"全部"三行都有）—— 见 :mod:`web.services.money_service`
里"美元 / 港币行用 ``empty()`` 判，不是判非零"那条。

## 页面上的链接点得通吗

**分组名**是本页自己拼的（``?groupid=`` 切分组），能点。其余链接
（清空 / 校准 / 修改 / 删除、"股票分组"标题、"交易记录"标题）都指向
PHP 的页面，演示环境下点不通 —— 它们的作用是让形态与 PHP 一致，
逐例对照脚本比的就是这些串。
"""

from __future__ import annotations

from flask import Blueprint, render_template, request, url_for
from markupsafe import Markup

from web.bootstrap import install_paths

install_paths()

from web.services.money_service import build_money_table  # noqa: E402
from web.services.transaction_service import (  # noqa: E402
    TABLE_COMMON_DISPLAY,
    build_transaction_table,
)

account_bp = Blueprint("account", __name__)

#: 默认显示哪个分组（没有 ``?groupid=`` 时）。
DEFAULT_GROUP_ID = "2"

#: 演示数据（样例引用对象、分组、交易记录、净值）抽到共用模块，
#: 账户 / 分组 / 交易三个演示页一起复用，避免三份重复。含义与取值说明见
#: :mod:`web.routes.account_demo_data`。
from web.routes.account_demo_data import (  # noqa: E402
    DEMO_GROUP_NAMES,
    DEMO_NET_VALUES,
    DEMO_REF_ARGS,
    DEMO_TRANSACTION_RECORDS,
    _DemoRef,
    _demo_groups,
    _demo_net_value,
    _ref_of,
)


def _group_link_of(group_id):
    """``group_link_of``：``$acct->GetGroupLink($strGroupId)`` 的替身。

    PHP 那边是"分组名 → 分组页面"的链接。演示页没有分组页面，于是
    指回自己（``?groupid=``），点一下换上面的交易明细 —— 这样这个
    注入口子是真的在点，而不是摆设。
    """
    name = DEMO_GROUP_NAMES.get(group_id, str(group_id))
    return Markup('<a href="%s">%s</a>'
                  % (url_for("account.account_page", groupid=group_id), name))


@account_bp.route("/account")
def account_page():
    """交易明细段落（``php/ui/transactionparagraph.php``）+ 资金汇总段落
    （``_stockaccount.php:213``）。"""
    group_id = request.args.get("groupid") or DEFAULT_GROUP_ID
    if group_id not in DEMO_GROUP_NAMES:
        # 演示页没有"分组不存在"这一说（PHP 那边会先 $acct->EchoStockGroup()
        # 判一次），给个兜底别摆一张空表
        group_id = DEFAULT_GROUP_ID
    records = [record for record in DEMO_TRANSACTION_RECORDS
               if record["group_id"] == group_id]

    # ``_stockaccount.php:206`` 的调用形状：ref=false（全部标的）、b_all=false
    # —— 于是标题是"交易记录"链接、只显示最新 TABLE_COMMON_DISPLAY 条。
    transaction_table = build_transaction_table(
        records,
        ref_builder=_ref_of,
        symbol_of=lambda stock_id: stock_id,
        net_value_of=_demo_net_value,
        b_all=False,
        group_id=group_id,
    )

    money_table = build_money_table(
        _demo_groups(),
        usdcny_ref=_ref_of("fx_susdcnh"),
        hkcny_ref=_ref_of("fx_hkdcnh"),
        group_link_of=_group_link_of,
        # 不注入 member_email_of —— 与"没登录"一致，于是没有测试列。
        # 那一列只对"全部"行出现，且按会员邮箱分两种算法（见 money_service）。
    )

    return render_template(
        "account.html",
        transaction_table=transaction_table,
        money_table=money_table,
        group_id=group_id,
        group_name=DEMO_GROUP_NAMES.get(group_id, group_id),
        groups=sorted(DEMO_GROUP_NAMES.items()),
        table_common_display=TABLE_COMMON_DISPLAY,
        record_count=len(records),
    )
