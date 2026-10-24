"""交易管理页（分组交易明细 + 新增/修改表单 + 写端点）的路由。

对应 PHP 两个页面 + 一个提交端点::

    woody/res/php/_mystocktransaction.php:5   EchoAll()
        → EchoTransactionParagraph($acct, $strGroupId[, $ref])
    woody/res/php/_editstocktransaction.php    EchoAll()
        → StockEditTransactionForm($acct, STOCK_TRANSACTION_EDIT)
    woody/res/php/_submittransaction.php      _SubmitTransactionAccount::Process()

## 与 PHP 的差异（README §六 有完整清单）

1. **一律 POST**：PHP 的 delete / adjust / empty 是 ``submittransaction.php
   ?delete=<id>`` 这种 **GET 链接副作用**。本项目拆成
   ``/transactions/delete`` / ``/adjust`` / ``/empty`` 三个 POST 端点，
   分派与取值语义交给 ``core.submittransaction.SubmitTransactionAccount
   .process()``（那边照抄 PHP，只是把 ``$_POST`` + URL query 并成一个
   ``params`` 字典）。
2. **不接登录态**：PHP 未登录时 ``Process`` 不执行。演示环境固定
   ``DEMO_MEMBER_ID``（账号体系暂缓，页面有常驻说明），权限闸门恒开 ——
   与 ``/admin`` 分组页同一形态。
3. **接真库**：交易记录、成员、价格都从库里读（不再用写死的样例数据）。
   测试用 ``transactions_source_factory`` 注入假数据源。

## 数据来源（真库）

* 分组列表 → ``StockGroupSql.get_all(DEMO_MEMBER_ID)``
* 交易记录 → ``StockGroupItemSql.get_all_stock_transaction(0, 10)``
  （带 ``?symbol=`` 时走 ``get_stock_transaction`` 只取那一支）
* 表单默认值 → ``core.edittransactionform.build_transaction_form``
"""

from __future__ import annotations

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from web.bootstrap import install_paths

install_paths()

from core.edittransactionform import build_transaction_form  # noqa: E402
from core.stockdata import MySqlFundDataSource  # noqa: E402
from core.stockgroupsql import (  # noqa: E402
    DEMO_MEMBER_ID,
    StockGroupItemSql,
    StockGroupSql,
)
from core.submittransaction import (  # noqa: E402
    SUBMIT_EDIT,
    SUBMIT_NEW,
    SubmitTransactionAccount,
)
from web.db import get_db  # noqa: E402
from web.services.transaction_service import (  # noqa: E402
    TABLE_COMMON_DISPLAY,
    build_transaction_table,
)

transactions_bp = Blueprint("transactions", __name__)

#: 新增按钮的文案（``STOCK_TRANSACTION_NEW``）。
SUBMIT_LABEL_NEW = SUBMIT_NEW
#: 修改按钮的文案（``STOCK_TRANSACTION_EDIT``）。
SUBMIT_LABEL_EDIT = SUBMIT_EDIT


def _source() -> MySqlFundDataSource:
    """取数据源：优先测试注入的工厂，缺省用请求级库连接。

    与 ``/admin`` 分组页同一形态（``admin_source_factory``）。
    """
    factory = current_app.extensions.get("transactions_source_factory")
    if factory is not None:
        return factory()
    return MySqlFundDataSource(get_db())


# ---------------------------------------------------------------------------
# 真库取数
# ---------------------------------------------------------------------------

def _group_rows(source: MySqlFundDataSource) -> list:
    """``[(group_id, group_name)]``：当前会员的全部分组。"""
    group_sql = StockGroupSql(source)
    return [(str(record["id"]), record["groupname"])
            for record in group_sql.get_all(DEMO_MEMBER_ID)]


def _default_group_id(source: MySqlFundDataSource) -> str:
    """没给 ``?groupid=`` 时用第一个分组（没有分组则空串）。"""
    rows = _group_rows(source)
    return rows[0][0] if rows else ""


def _records_of(source: MySqlFundDataSource, group_id: Any,
                symbol: Any = None) -> list:
    """本分组最新 ``TABLE_COMMON_DISPLAY`` 条交易（``filled`` DESC）。

    带 ``?symbol=`` 时只取那一支（``GetStockTransaction``），否则整组
    （``GetAllStockTransaction``）。PHP 的 ``$bAll`` 取默认 false，
    所以是"最新 10 条 + 标题链接"，不是全量翻页。
    """
    item_sql = StockGroupItemSql(source, group_id)
    if not symbol:
        return item_sql.get_all_stock_transaction(0, TABLE_COMMON_DISPLAY) or []
    stock_id = source.get_stock_id(symbol)
    if stock_id is None:
        return []
    return item_sql.get_stock_transaction(stock_id, 0,
                                          TABLE_COMMON_DISPLAY) or []


def _ref_builder(source: MySqlFundDataSource):
    """``ref_builder``：按符号建引用对象（读库快照，取不到不崩）。"""

    def build(symbol: Any) -> Any:
        try:
            from web.services.reference_service import build_stock_references

            refs = build_stock_references([symbol], source=source)
        except Exception:      # 取不到行情不该让整页 500
            return None
        return refs[0] if refs else None

    return build


def _net_value_of(source: MySqlFundDataSource):
    """``net_value_of(stock_id, date, prev)``：单位净值（含 QDII 的前一日）。

    PHP 是 ``NetValueHistorySql::GetClose()`` / ``GetClosePrev()``
    （``transactionparagraph.php:24`` 按是否 QDII 选）。
    """
    from sql.historytable import NetValueHistorySql

    connection = getattr(source, "connection", source)
    net_sql = NetValueHistorySql(connection)

    def query(stock_id: Any, date: Any, prev: bool = False) -> Any:
        try:
            if prev:
                return net_sql.get_close_prev(stock_id, date)
            return net_sql.get_close(stock_id, date)
        except Exception:
            return None

    return query


def _symbol_of(source: MySqlFundDataSource):
    """``symbol_of(stock_id)``：记录里只有 stock_id，显示要符号。"""

    def query(stock_id: Any) -> Any:
        try:
            return source.get_stock_symbol(stock_id)
        except Exception:
            return None

    return query


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------

@transactions_bp.route("/transactions")
def transactions_page():
    """交易明细 + 新增表单（``_mystocktransaction.php`` + 新增表单）。"""
    source = _source()
    groups = _group_rows(source)
    group_id = request.args.get("groupid") or _default_group_id(source)
    symbol = request.args.get("symbol")

    records = _records_of(source, group_id, symbol) if group_id else []
    ref = _ref_builder(source)(symbol) if symbol else None

    transaction_table = build_transaction_table(
        records,
        ref=ref,
        ref_builder=_ref_builder(source),
        symbol_of=_symbol_of(source),
        group_item_id_of=lambda record: record.get("groupitem_id"),
        net_value_of=_net_value_of(source),
        b_all=False,
        group_id=group_id,
    )

    #: 表单数据取不到（分组没成员/取不到价）时页面照出，只是没有表单。
    form = None
    if group_id:
        form = build_transaction_form(source, group_id, SUBMIT_LABEL_NEW)

    group_name = dict(groups).get(group_id, group_id)
    return render_template(
        "transactions.html",
        transaction_table=transaction_table,
        group_id=group_id,
        group_name=group_name,
        groups=groups,
        symbol=symbol,
        form=form,
        table_common_display=TABLE_COMMON_DISPLAY,
        record_count=len(records),
        member_id=DEMO_MEMBER_ID,
    )


@transactions_bp.route("/transactions/edit")
def edit_transaction_page():
    """修改表单页（``_editstocktransaction.php``）：要带 ``?edit=<交易 id>``。"""
    source = _source()
    trans_id = request.args.get("edit")
    group_id = request.args.get("groupid") or _default_group_id(source)
    form = build_transaction_form(source, group_id, SUBMIT_LABEL_EDIT,
                                  trans_id=trans_id)
    if form is None:
        flash("找不到这笔交易，或该分组没有可交易的成员。")
        return redirect(url_for("transactions.transactions_page",
                                groupid=group_id))
    return render_template(
        "transaction_form.html",
        form=form,
        group_id=group_id,
        group_name=dict(_group_rows(source)).get(group_id, group_id),
        member_id=DEMO_MEMBER_ID,
    )


# ---------------------------------------------------------------------------
# 写端点（一律 POST）
# ---------------------------------------------------------------------------

def _account(source: MySqlFundDataSource) -> SubmitTransactionAccount:
    """演示账号：固定 member_id，权限闸门恒开（账号体系暂缓）。"""
    return SubmitTransactionAccount(source, member_id=DEMO_MEMBER_ID)


def _redirect_back(group_id: Any = None):
    return redirect(url_for("transactions.transactions_page",
                            groupid=group_id))


@transactions_bp.route("/transactions/submit", methods=["POST"])
def submit_transaction():
    """新增 / 修改 / 合并（``$_POST['submit']`` 分派）。"""
    source = _source()
    result = _account(source).process(request.form)
    _flash_result(result)
    return _redirect_back(request.form.get("groupid")
                          or result.get("group_id"))


@transactions_bp.route("/transactions/delete", methods=["POST"])
def delete_transaction():
    """删一笔（PHP 是 ``?delete=<id>`` 的 GET 链接）。"""
    source = _source()
    params = dict(request.form)
    params["delete"] = request.form.get("trans_id")
    result = _account(source).process(params)
    _flash_result(result)
    return _redirect_back(request.form.get("groupid")
                          or result.get("group_id"))


@transactions_bp.route("/transactions/adjust", methods=["POST"])
def adjust_transaction():
    """按净值校准数量（PHP 是 ``?adjust=<id>&netvalue=``）。"""
    source = _source()
    params = dict(request.form)
    params["adjust"] = request.form.get("trans_id")
    result = _account(source).process(params)
    _flash_result(result)
    return _redirect_back(request.form.get("groupid")
                          or result.get("group_id"))


@transactions_bp.route("/transactions/empty", methods=["POST"])
def empty_remark():
    """清空备注（PHP 是 ``?empty=<id>``）。"""
    source = _source()
    params = dict(request.form)
    params["empty"] = request.form.get("trans_id")
    result = _account(source).process(params)
    _flash_result(result)
    return _redirect_back(request.form.get("groupid")
                          or result.get("group_id"))


#: 动作 → 中文说明（flash 文案）
_ACTION_TEXT = {
    "new": "新增交易",
    "edit": "修改交易",
    "merge": "合并交易",
    "delete": "删除交易",
    "adjust": "校准数量",
    "empty": "清空备注",
    "fund": "对冲申购",
    "unknown": "未知动作",
    "none": "没有可执行的动作",
}


def _flash_result(result: dict) -> None:
    """写一条 flash：成功/失败 + 动作名 + 分组。"""
    action = result.get("action")
    text = _ACTION_TEXT.get(action, str(action))
    if result.get("ok"):
        flash("%s：成功（分组 %s）" % (text, result.get("group_id")))
    else:
        flash("%s：未执行（分组 %s，可能是权限闸门挡下或记录不存在）"
              % (text, result.get("group_id")))
