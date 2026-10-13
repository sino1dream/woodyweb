"""交易管理页（分组交易明细，支持按标的过滤）的路由。

对应 PHP ``woody/res/php/_mystocktransaction.php`` 的 ``EchoAll()``::

    if ($strSymbol = $acct->GetSymbol())        // ?symbol= 某支股票
        EchoTransactionParagraph($acct, $strGroupId, new MyStockReference($strSymbol));
    else
        EchoTransactionParagraph($acct, $strGroupId);   // 整组

与 :mod:`web.routes.account` / :mod:`web.routes.group` 一样，这里复用已迁的
:func:`web.services.transaction_service.build_transaction_table`，用写死的样例
数据。区别在参数：

* 带 ``?symbol=`` 时整张表只有那一支标的的交易，且引用对象按该符号建
  （``ref`` 非空，走"单标的"分支）；
* 不带时按分组出全部交易（``ref`` 为空，走"全部"分支，按 ``groupitem_id``
  缓存引用对象）。

注意 PHP 这份代码调用 ``EchoTransactionParagraph`` 时 ``$bAll`` 取默认
（``false``），所以也是"最新 10 条 + 标题链接"，不是全量翻页 —— 这里照抄。
"""

from __future__ import annotations

from flask import Blueprint, render_template, request

from web.bootstrap import install_paths

install_paths()

from web.routes.account_demo_data import (  # noqa: E402
    DEMO_GROUP_NAMES,
    _demo_net_value,
    _demo_transaction_records,
    _ref_of,
)
from web.services.transaction_service import (  # noqa: E402
    TABLE_COMMON_DISPLAY,
    build_transaction_table,
)

transactions_bp = Blueprint("transactions", __name__)

#: 默认显示哪个分组（没有 ``?groupid=`` 时）。
DEFAULT_GROUP_ID = "2"


@transactions_bp.route("/transactions")
def transactions_page():
    """分组交易管理页（``_mystocktransaction.php``）。"""
    group_id = request.args.get("groupid") or DEFAULT_GROUP_ID
    if group_id not in DEMO_GROUP_NAMES:
        group_id = DEFAULT_GROUP_ID
    symbol = request.args.get("symbol")

    records = _demo_transaction_records(group_id, symbol)
    # 带 ?symbol= 时给一个单标的引用对象；否则留空走按 groupitem_id 缓存。
    ref = _ref_of(symbol) if symbol else None

    transaction_table = build_transaction_table(
        records,
        ref=ref,
        ref_builder=_ref_of,
        symbol_of=lambda stock_id: stock_id,
        net_value_of=_demo_net_value,
        b_all=False,
        group_id=group_id,
    )

    return render_template(
        "transactions.html",
        transaction_table=transaction_table,
        group_id=group_id,
        group_name=DEMO_GROUP_NAMES.get(group_id, group_id),
        groups=sorted(DEMO_GROUP_NAMES.items()),
        symbol=symbol,
        table_common_display=TABLE_COMMON_DISPLAY,
        record_count=len(records),
    )
