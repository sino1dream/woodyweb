"""交易写入口 —— 对应 ``woody/res/php/_submittransaction.php``。

## 这一层是什么

PHP 把"新增/修改/删除/调整/清空备注/合并/基金申购"**七个动作**塞进一个
``_SubmitTransactionAccount::Process()``：靠 ``$_POST['submit']`` 的值与
URL 查询串（``?delete=`` / ``?adjust=`` / ``?empty=`` / ``?groupid=``）
分派，末尾统一 ``UpdateStockGroupItem()`` 回填成员的
``quantity / cost / record`` 三列（那是页面列表的预统计值）。

本模块照搬这个形状，但按本项目的两条硬约定改造：

1. **不碰全局**：``$_POST`` 由调用方以 ``params`` 字典传入（测试能直接
   造，web 路由传 ``request.form``）。URL 查询串里的 ``delete`` /
   ``adjust`` / ``empty`` / ``groupid`` 也并进同一个字典 —— 用户拍板
   **一律 POST**，PHP 那几个 GET 写操作不复活。
2. **权限闸门可注入**：PHP 的 ``IsGroupReadOnly()`` / ``IsAdmin()``
   依赖登录态（账号体系暂缓）。这里把登录态收敛成构造参数
   ``member_id`` / ``is_admin``，判定逻辑一字不改。

## 七个动作与 PHP 的对应

============================  ==========================================
动作                          PHP 入口
============================  ==========================================
新增                          ``STOCK_TRANSACTION_NEW`` → ``_onNew``
修改                          ``STOCK_TRANSACTION_EDIT`` → ``_onEdit``
                              （要带 ``edit=<交易 id>``）
合并成员的交易                ``STOCK_TRANSACTION_MERGE`` →
                              ``_onMergeTransaction``
删除一笔                      ``?delete=<id>``
按净值调整数量                ``?adjust=<id>&netvalue=<净值>``
清空备注                      ``?empty=<id>``
对冲申购（基金）              ``?groupid=&fundid=&amount=&netvalue=``
============================  ==========================================

## 三个照抄、别"修正"的点

* **删除分支管理员绕过只读**：``$strGroupId = $this->IsAdmin() ?
  SqlGetStockGroupId(...) : $this->_canModifyStockTransaction(...)`` ——
  只有 delete 这一支有这个旁路，adjust / empty 没有。
* **合并的方向由 ``type0`` 决定**：``$_POST['type0'] == '0'`` 时
  ``symbol0`` 是源、``symbol1`` 是目标，否则反过来。合并后**两边成员**
  都要重算（PHP 连着两次 ``UpdateStockGroupItemTransaction``）。
* **``UpdateStockGroupItem`` 遍历整组**：它只拿 ``group_id`` 与"item_id
  是否非空"做闸门，重算的是**该组全部成员**（不是只重算那一笔所属成员）。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from core.stockgroupsql import (
    StockGroupItemSql,
    StockGroupSql,
    sql_get_stock_group_id,
)
from core.stocktrans import (
    StockTransaction,
    _php_eq_post_select,
    add_sql_transaction,
    get_group_item_sym,
    get_stock_cost,
    get_stock_quantity,
    sql_clean_string,
    strval_round,
)

# ---------------------------------------------------------------------------
# PHP 常量（分散在三个文件里，这里集中）
# ---------------------------------------------------------------------------

#: ``STOCK_TRANSACTION_NEW``（``woody/res/php/_edittransactionform.php:2``）
SUBMIT_NEW = "新增股票交易"
#: ``STOCK_TRANSACTION_EDIT``（``_edittransactionform.php:3``）
SUBMIT_EDIT = "修改股票交易"
#: ``STOCK_TRANSACTION_MERGE``（``_editmergeform.php:2``）
SUBMIT_MERGE = "Merge Transaction"
#: ``STOCK_TRANSACTION_MERGE_CN``（``_editmergeform.php:3``）
SUBMIT_MERGE_CN = "合并交易"
#: ``STOCK_DISP_ORDER``（``php/ui/stockdisp.php:18``）：申购备注的后缀
DISP_ORDER = "申购"

#: PHP ``UrlGetQueryValue()`` 的"取不到"是 ``false``，而
#: ``($x = UrlGetQueryValue(...)) == false`` 会把 ``'0'`` 也一起判掉
#: （``'0' == false`` 在 PHP 8 为真）。这里复用同一语义。
def _php_query_value(params: Any, key: str) -> Any:
    """对应 ``UrlGetQueryValue($strKey)`` 的**有效部分**。

    键不存在或值是 PHP 假值（``''`` / ``'0'`` / ``None`` / ``False``）
    → ``False``（PHP 的 ``false``）。注意与 :func:`_post_has` 的区别：
    这里是"取值并判真假"，不是"键是否存在"。
    """
    if params is None:
        return False
    if not hasattr(params, "get"):
        return False
    value = params.get(key)
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return False if value in ("", "0") else value
    return value


def _php_truthy(value: Any) -> bool:
    """PHP 真假：``''`` / ``'0'`` / ``0`` / ``False`` / ``None`` 都是假。"""
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return value not in ("", "0")
    return bool(value)


# ---------------------------------------------------------------------------
# 费率与折股（基金申购分支用）
# ---------------------------------------------------------------------------

#: ``StockGetFundFeeRatio()``（``woody/res/php/_stock.php:151``）的整张表。
#: switch 的 fall-through 已经展开成"符号 → 费率"，未列出的符号落默认值。
FUND_FEE_RATIO = {
    "SZ160416": 0.0,
    "SZ161116": 0.0,
    "SZ161226": 0.01,
    "SH501018": 0.012,
    "SZ161125": 0.012,
    "SZ161126": 0.012,
    "SZ161127": 0.012,
    "SZ161128": 0.012,
    "SZ161129": 0.012,
    "SZ161130": 0.012,
    "SZ162719": 0.012,
    "SZ164824": 0.012,
    "SZ164906": 0.012,
    "SZ161815": 0.016,
}
#: switch 走完没匹配上的默认值（``_stock.php:177`` 的 ``return 0.015``）。
FUND_FEE_RATIO_DEFAULT = 0.015


def stock_get_fund_fee_ratio(symbol: Any) -> float:
    """对应 ``StockGetFundFeeRatio($strSymbol)``（``_stock.php:151``）。

    场内基金的**申购费率**（不是佣金）：申购金额先扣掉费率才换份额，
    所以 ``_onAddFundPurchase`` 里份额 = 金额 / (1 + 费率) / 净值。
    表里没有的符号落 :data:`FUND_FEE_RATIO_DEFAULT`（1.5%）。
    """
    if symbol is None:
        return FUND_FEE_RATIO_DEFAULT
    return FUND_FEE_RATIO.get(str(symbol), FUND_FEE_RATIO_DEFAULT)


def get_arbitrage_quantity(symbol: Any, stock_id: Any, quantity: Any,
                           hedge_of: Callable[[Any, Any], Any] = None) -> str:
    """对应 ``GetArbitrageQuantity()``（``php/ui/stockcomm.php:65``）。

    .. code-block:: php

        return number_format($fQuantity / GetStockHedge($strSymbol, $strStockId));

    与 ``web/services/portfolio_service.get_arbitrage_quantity`` 同一个
    公式，但**这一份给 auto 层用**（web → auto 单向依赖，反过来不行）。
    ``hedge_of`` 是注入的对冲比率查询（``GetStockHedge``），不传按 1.0 计
    —— 与 PHP 查不到对冲值时回 1.0 一致。
    """
    from sql.historytable import format_int_display

    try:
        shares = float(quantity)
    except (TypeError, ValueError):
        shares = 0.0
    hedge = 1.0
    if hedge_of is not None:
        try:
            hedge = float(hedge_of(symbol, stock_id))
        except (TypeError, ValueError):
            hedge = 1.0
    if hedge == 0.0:
        hedge = 1.0
    return format_int_display(shares / hedge)


# ---------------------------------------------------------------------------
# 回填成员预统计（php/stocktrans.php:134 / :148）
# ---------------------------------------------------------------------------

def update_stock_group_item_transaction(item_sql: Any, item_id: Any) -> int:
    """对应 ``UpdateStockGroupItemTransaction($sql, $strGroupItemId)``
    （``php/stocktrans.php:134``）：重算一个成员的 quantity/cost/record。

    三列是**页面列表的预统计值**，不是交易表的数据：
    ``quantity`` = 累计份数（卖出为负，可归零）、``cost`` = 累计成本
    （**含手续费**，见 :func:`core.stocktrans.add_sql_transaction`）、
    ``record`` = 成交笔数。每笔交易写完都要跑一次，否则页面列对不上。

    没有交易时 PHP 也会 UPDATE（把三列刷成 0）—— 删掉最后一笔交易后
    正是靠这一下把成员清干净。
    """
    trans = StockTransaction()
    for record in (item_sql.trans_sql.get_record(item_id) or []):
        add_sql_transaction(trans, record)
    return item_sql.update(item_id,
                           str(trans.get_total_shares()),
                           str(trans.get_total_cost()),
                           str(trans.get_total_records()))


def update_stock_group_item(group_id: Any, item_id: Any,
                            source: Any = None) -> int:
    """对应 ``UpdateStockGroupItem($strGroupId, $strGroupItemId)``
    （``php/stocktrans.php:148``）。

    两个闸门都是"假值即返回"（``if ($strGroupId == false) return;``）。
    **重算范围是整组全部成员** —— ``item_id`` 只用来做第二个闸门，
    不参与筛选（PHP 同款，别"优化"成只重算这一个成员）。

    返回重算的成员数（PHP 无返回，这里给个数方便调用方断言）。
    """
    if not _php_truthy(group_id):
        return 0
    if not _php_truthy(item_id):
        return 0
    item_sql = StockGroupItemSql(source, group_id)
    count = 0
    for record in item_sql.get_all():
        update_stock_group_item_transaction(item_sql, record["id"])
        count += 1
    return count


# ---------------------------------------------------------------------------
# _SubmitTransactionAccount
# ---------------------------------------------------------------------------

class SubmitTransactionAccount:
    """对应 ``_SubmitTransactionAccount extends StockAccount``
    （``woody/res/php/_submittransaction.php:100``）。

    登录态收敛成两个构造参数（账号体系暂缓，见模块 docstring）：

    * ``member_id`` —— PHP 的 ``GetLoginId()``。``IsGroupReadOnly`` 拿它
      跟分组的 ``member_id`` 比；
    * ``is_admin`` —— PHP 的 ``IsAdmin()``。只有 **delete** 那一支用它
      绕过只读闸门。

    ``source`` 是数据源（``StockGroupItemSql`` / ``StockGroupSql`` 的构造
    入参）；测试直接传内存假连接即可。
    """

    def __init__(self, source: Any, member_id: Any = None,
                 is_admin: bool = False,
                 item_sql_factory: Callable[[Any, Any], Any] = None) -> None:
        self.source = source
        self.member_id = member_id
        self.is_admin = bool(is_admin)
        #: 可注入的成员 Sql 工厂（测试用它换掉真 ``StockGroupItemSql``）。
        self._item_sql_factory = item_sql_factory

    def is_group_read_only(self, group_id: Any) -> bool:
        """对应 ``StockAccount::IsGroupReadOnly()``（``_stockaccount.php:171``）。

        .. code-block:: php

            if ($strGroupId)    return ($this->GetGroupMemberId($strGroupId) == $this->GetLoginId()) ? false : true;
            return false;

        即**分组不属于当前登录用户就是只读**；分组 id 为假值时**不**只读
        （PHP 的短路）。``GetGroupMemberId`` = ``StockGroupSql::GetKeyId``
        = 分组行的 ``member_id`` 列。
        """
        if not _php_truthy(group_id):
            return False
        group_sql = StockGroupSql(self.source)
        record = group_sql.get_record_by_id(group_id)
        member_id = record.get("member_id") if record else None
        return not self._php_loose_eq(member_id, self.member_id)

    @staticmethod
    def _php_loose_eq(left: Any, right: Any) -> bool:
        """PHP 的 ``==``：数字串与数字按数值比（``'1' == 1`` 为真）。"""
        if left is None or right is None:
            return left is right
        if isinstance(left, str) or isinstance(right, str):
            if str(left) == str(right):
                return True
            try:
                return float(left) == float(right)
            except (TypeError, ValueError):
                return False
        return left == right

    # -- 闸门 ---------------------------------------------------------------
    def can_modify_stock_transaction(self, item_id: Any) -> Any:
        """对应 ``_canModifyStockTransaction()``（``_submittransaction.php:102``）。

        返回**分组 id**（真值）表示可写；``False`` 表示拒绝
        （分组只读或查不到分组）。PHP 返回的就是 ``$strGroupId``，
        调用方拿它当布尔用。
        """
        group_id = sql_get_stock_group_id(self.source, item_id)
        if group_id is None:
            return False
        if self.is_group_read_only(group_id):
            return False
        return group_id

    def _item_sql(self, group_id: Any) -> Any:
        """``new StockGroupItemSql($strGroupId)``。"""
        if self._item_sql_factory is not None:
            return self._item_sql_factory(self.source, group_id)
        return StockGroupItemSql(self.source, group_id)

    def _trans_sql(self) -> Any:
        """``new StockTransactionSql()``（独立于分组，按交易 id 操作）。"""
        from sql.stocktransactiontable import StockTransactionSql

        connection = getattr(self.source, "connection", self.source)
        return StockTransactionSql(connection)

    # -- 七个动作 -----------------------------------------------------------
    def on_new(self, item_id: Any, quantity: Any, price: Any, cost: Any,
               remark: Any) -> Any:
        """对应 ``_onNew()``（``_submittransaction.php:135``）。"""
        group_id = self.can_modify_stock_transaction(item_id)
        if _php_truthy(group_id):
            item_sql = self._item_sql(group_id)
            if item_sql.trans_sql.insert(item_id, quantity, price, cost, remark):
                self._debug_stock_transaction(
                    item_sql.get_stock_id(item_id), group_id, quantity,
                    price, cost, remark)
        return group_id

    def on_edit(self, trans_id: Any, item_id: Any, quantity: Any,
                price: Any, cost: Any, remark: Any) -> Any:
        """对应 ``_onEdit()``（``_submittransaction.php:148``）。

        注意 PHP 的 ``Update`` 不写 ``filled``（成交时间不可改），
        见 ``sql/stocktransactiontable.py`` 的模块 docstring 第 1 条。
        """
        group_id = self.can_modify_stock_transaction(item_id)
        if _php_truthy(group_id):
            item_sql = self._item_sql(group_id)
            if item_sql.trans_sql.update(trans_id, item_id, quantity, price,
                                         cost, remark):
                self._debug_stock_transaction(
                    item_sql.get_stock_id(item_id), group_id, quantity,
                    price, cost, remark)
        return group_id

    def on_merge_transaction(self, params: Any) -> bool:
        """对应 ``_onMergeTransaction()``（``_submittransaction.php:161``）。

        **方向由 ``type0`` 决定**：``$_POST['type0'] == '0'`` 时
        ``symbol0`` 是源、``symbol1`` 是目标；否则反过来。合并是"把源成员
        的全部交易改挂到目标成员"（``trans_sql->Merge``），改完**两边**
        成员都要重算。
        """
        if _php_eq_post_select(_get(params, "type0"), "0"):
            src_item_id = _get(params, "symbol0")
            dst_item_id = _get(params, "symbol1")
        else:
            src_item_id = _get(params, "symbol1")
            dst_item_id = _get(params, "symbol0")

        src_group_id = self.can_modify_stock_transaction(src_item_id)
        if not _php_truthy(src_group_id):
            return False
        dst_group_id = self.can_modify_stock_transaction(dst_item_id)
        if not _php_truthy(dst_group_id):
            return False

        item_sql = self._item_sql(src_group_id)
        merged = item_sql.trans_sql.merge(src_item_id, dst_item_id)
        if merged:
            update_stock_group_item_transaction(item_sql, src_item_id)
            update_stock_group_item_transaction(
                self._item_sql(dst_group_id), dst_item_id)
        return bool(merged)

    def delete_transaction(self, trans_id: Any) -> dict:
        """对应 ``?delete=<id>`` 分支（``_submittransaction.php:222``）。

        **只有这一支管理员绕过只读闸门**：``$strGroupId = $this->IsAdmin() ?
        SqlGetStockGroupId(...) : $this->_canModifyStockTransaction(...)``。
        """
        trans_sql = self._trans_sql()
        record = trans_sql.get_record_by_id(trans_id)
        if not record:
            return _result("delete", False, None, None)
        item_id = record.get("groupitem_id")
        if self.is_admin:
            group_id = sql_get_stock_group_id(self.source, item_id)
        else:
            group_id = self.can_modify_stock_transaction(item_id)
        if not _php_truthy(group_id):
            return _result("delete", False, group_id, item_id)
        deleted = trans_sql.delete_by_id(trans_id)
        return _result("delete", bool(deleted), group_id, item_id)

    def adjust_transaction(self, trans_id: Any, net_value: Any) -> dict:
        """对应 ``?adjust=<id>&netvalue=<净值>`` 分支
        （``_submittransaction.php:235``）。

        **按新净值反推数量**：保持"成交额"不变，换一个价格重算份数
        （分红/份额折算后修记录用）。``fees`` 与 ``remark`` 原样带回。
        """
        trans_sql = self._trans_sql()
        record = trans_sql.get_record_by_id(trans_id)
        if not record:
            return _result("adjust", False, None, None)
        item_id = record.get("groupitem_id")
        group_id = self.can_modify_stock_transaction(item_id)
        if not _php_truthy(group_id):
            return _result("adjust", False, group_id, item_id)
        quantity = (_php_floatval(record.get("quantity"))
                    * _php_floatval(record.get("price"))
                    / _php_floatval(net_value))
        updated = trans_sql.update(trans_id, item_id,
                                   _php_strval_float(quantity), net_value,
                                   record.get("fees"), record.get("remark"))
        return _result("adjust", bool(updated), group_id, item_id)

    def empty_remark(self, trans_id: Any) -> dict:
        """对应 ``?empty=<id>`` 分支（``_submittransaction.php:248``）。

        数量/价格/手续费原样带回，**只把 remark 置空**（PHP 的
        ``Update`` 第 5 个参数缺省是 ``''``）。
        """
        trans_sql = self._trans_sql()
        record = trans_sql.get_record_by_id(trans_id)
        if not record:
            return _result("empty", False, None, None)
        item_id = record.get("groupitem_id")
        group_id = self.can_modify_stock_transaction(item_id)
        if not _php_truthy(group_id):
            return _result("empty", False, group_id, item_id)
        updated = trans_sql.update(trans_id, item_id,
                                   record.get("quantity"),
                                   record.get("price"),
                                   record.get("fees"))
        return _result("empty", bool(updated), group_id, item_id)

    def on_add_fund_purchase(self, group_id: Any, params: Any,
                             hedge_of: Callable[[Any, Any], Any] = None
                             ) -> Any:
        """对应 ``_onAddFundPurchase($strGroupId)``
        （``_submittransaction.php:113``）。

        PHP 的四个输入（``fundid`` / ``amount`` / ``netvalue`` /
        ``groupid``）在 URL 查询串里；本项目一律 POST，所以从 ``params``
        取，但**"取不到即放弃"的判定照旧**（``UrlGetQueryValue`` 的
        ``== false``，连 ``'0'`` 一起判掉）。

        份额 = 金额 / (1 + 费率) / 净值；手续费记
        ``strval_round(金额 × 费率 × 0.1, 3)``（**那个 0.1 是 PHP 原式**，
        看着像笔误但照抄 —— 改了就和生产端落库值不一致，见 pitfalls #114）。

        备注形如 ``'}1,000 申购'``：前导 ``}`` / ``{`` 是**交替的对冲批次
        标记**（``_edittransactionform.php:109-110`` 取反），带上
        ``申购`` 二字后编辑表单会把 fees 当申购费回填
        （``_edittransactionform.php:155``），交易段落也会给这行挂
        adjust 链接（``transactionparagraph.php:21``）。
        """
        if self.is_group_read_only(group_id):
            return False
        fund_id = _php_query_value(params, "fundid")
        amount = _php_query_value(params, "amount")
        net_value = _php_query_value(params, "netvalue")
        if fund_id is False or amount is False or net_value is False:
            return False

        item_sql = self._item_sql(group_id)
        item_id = item_sql.get_id(fund_id)
        if item_id is None:
            return False

        from sql.stocksql import StockSql

        connection = getattr(self.source, "connection", self.source)
        fund_symbol = StockSql(connection).get_symbol(fund_id)
        fee_ratio = stock_get_fund_fee_ratio(fund_symbol)
        f_amount = _php_floatval(amount)
        f_quantity = f_amount / (1.0 + fee_ratio) / _php_floatval(net_value)
        remark = "}" + get_arbitrage_quantity(
            fund_symbol, fund_id, f_quantity, hedge_of=hedge_of)
        remark += " " + DISP_ORDER
        if item_sql.trans_sql.insert(item_id, str(int(f_quantity)),
                                     net_value,
                                     strval_round(f_amount * fee_ratio * 0.1,
                                                  3),
                                     remark):
            self._debug_fund_purchase(group_id, fund_id)
        return item_id

    # -- 调试钩子（PHP 的 trigger_error，本版留空实现给子类覆盖） -----------
    def _debug_stock_transaction(self, stock_id: Any, group_id: Any,
                                 quantity: Any, price: Any, cost: Any,
                                 remark: Any) -> None:
        """对应 ``_debugStockTransaction()``（``_submittransaction.php:78``）。

        PHP 里是 ``trigger_error`` 打日志（**且 remark 为空时直接 return**）。
        本版不做日志，留空实现给调用方覆盖（web 层想记就子类化）。
        """
        return None

    def _debug_fund_purchase(self, group_id: Any, fund_id: Any) -> None:
        """对应 ``_debugFundPurchase()``（``_submittransaction.php:93``）。"""
        return None

    # -- 分派 ---------------------------------------------------------------
    def process(self, params: Any,
                hedge_of: Callable[[Any, Any], Any] = None) -> dict:
        """对应 ``Process($strLoginId)``（``_submittransaction.php:188``）。

        ``params`` 是**合并后的表单 + 查询串**（一律 POST）。返回
        ``{"action", "ok", "group_id", "group_item_id"}`` 四键的字典，
        方便 web 层 flash + 重定向；PHP 的 ``Process`` 无返回值。

        顺序照 PHP：``submit``（merge / new / edit）→ ``delete`` →
        ``adjust`` → ``empty`` → ``groupid``（基金申购）。**merge 分支
        提前返回**（PHP 的 ``return``，不走末尾的回填）。
        """
        submit = _get(params, "submit")
        if submit is not None:
            if submit in (SUBMIT_MERGE, SUBMIT_MERGE_CN):
                ok = self.on_merge_transaction(params)
                return _result("merge", ok, None, None)

            item_id = _get(params, "symbol")
            quantity = get_stock_quantity(params)
            price = sql_clean_string(_get(params, "price"))
            cost = get_stock_cost(get_group_item_sym(item_id, self.source),
                                  quantity, price, params)
            remark = sql_clean_string(_get(params, "remark"))
            if submit == SUBMIT_NEW:
                group_id = self.on_new(item_id, quantity, price, cost, remark)
                result = _result("new", _php_truthy(group_id), group_id,
                                 item_id)
            elif submit == SUBMIT_EDIT:
                trans_id = _php_query_value(params, "edit")
                if trans_id is False:
                    return _result("edit", False, False, item_id)
                group_id = self.on_edit(trans_id, item_id, quantity, price,
                                        cost, remark)
                result = _result("edit", _php_truthy(group_id), group_id,
                                 item_id)
            else:
                #: PHP 的 switch 没有 default：未知 submit 值什么都不做，
                #: 只留 ``group_id = false`` 让末尾回填空转。
                return _result("unknown", False, False, item_id)
            update_stock_group_item(result["group_id"], result["group_item_id"],
                                    self.source)
            return result

        delete_id = _php_query_value(params, "delete")
        if delete_id is not False:
            result = self.delete_transaction(delete_id)
            update_stock_group_item(result["group_id"], result["group_item_id"],
                                    self.source)
            return result

        adjust_id = _php_query_value(params, "adjust")
        if adjust_id is not False:
            result = self.adjust_transaction(
                adjust_id, _php_query_value(params, "netvalue"))
            update_stock_group_item(result["group_id"], result["group_item_id"],
                                    self.source)
            return result

        empty_id = _php_query_value(params, "empty")
        if empty_id is not False:
            result = self.empty_remark(empty_id)
            update_stock_group_item(result["group_id"], result["group_item_id"],
                                    self.source)
            return result

        group_id = _php_query_value(params, "groupid")
        if group_id is not False:
            item_id = self.on_add_fund_purchase(group_id, params,
                                                hedge_of=hedge_of)
            result = _result("fund", _php_truthy(item_id), group_id, item_id)
            update_stock_group_item(group_id, item_id, self.source)
            return result

        return _result("none", False, False, False)

    # -- PHP 原名别名（AGENTS.md：保留 camelCase 别名） ---------------------
    IsGroupReadOnly = is_group_read_only
    CanModifyStockTransaction = can_modify_stock_transaction
    OnNew = on_new
    OnEdit = on_edit
    OnMergeTransaction = on_merge_transaction
    OnAddFundPurchase = on_add_fund_purchase
    Process = process


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _get(params: Any, key: str) -> Any:
    """``$_POST[$key]``（键不存在 → ``None``，PHP 会给 notice）。"""
    if params is None or not hasattr(params, "get"):
        return None
    return params.get(key)


def _result(action: Any, ok: Any, group_id: Any, group_item_id: Any) -> dict:
    """统一的返回形状（PHP 无返回，这四键给 web 层用）。"""
    return {"action": action, "ok": bool(ok), "group_id": group_id,
            "group_item_id": group_item_id}


def _php_floatval(value: Any) -> float:
    """``floatval()``：空串/``None`` → 0.0（PHP 同款，不抛异常）。"""
    from ref.stockref import _php_floatval as _impl

    return _impl(value)


def _php_strval_float(value: Any) -> str:
    """``strval(float)``：PHP 用 ``precision=14`` 出串（``610.0`` → ``'610'``）。

    adjust 分支把重算出的份数写回 ``quantity`` 列时用这个，避免 Python
    的 ``str(610.0) == '610.0'`` 与 PHP 的 ``'610'`` 不一致（虽然落
    DOUBLE 列数值一样，但逐例对照要能对上）。
    """
    from ref.stockref import _php_strval_float as _impl

    return _impl(value)


# PHP 原名别名（AGENTS.md：保留 camelCase 别名）
UpdateStockGroupItemTransaction = update_stock_group_item_transaction
UpdateStockGroupItem = update_stock_group_item
StockGetFundFeeRatio = stock_get_fund_fee_ratio
GetArbitrageQuantity = get_arbitrage_quantity
