"""新增/修改交易的表单数据 —— 对应 ``woody/res/php/_edittransactionform.php``。

PHP 这边是"echo 一段 HTML + 内联 JS"，本项目分成两半：

* **数据**在本模块（成员的现价 / 上次成交数量 / 价格下拉的 11 档 /
  建议备注的 ``{`` / ``}`` 取反）；
* **渲染**交给 Jinja 模板（``web/templates/transaction_form.html``）。

## 表单的四个默认值从哪来

============================  ==========================================
字段                          来源
============================  ==========================================
标的（``symbol``）            本分组**可交易**的成员，按 A 股 → 港股 →
                              美股三组各排序后拼接（``SqlGetStockGroupItem
                              SymbolArray``）
价格（``price``）             该成员的最新收盘价（``GetCloseNow``）
数量（``quantity``）          该成员**最近一笔**交易的份数（去掉负号）
佣金（``commission``）        编辑时：备注含"申购"才回填 ``fees``，
                              否则留空（``_edittransactionform.php:155``）
备注（``remark``）            新增时：上一条备注含 ``{`` 给 ``}``、含 ``}``
                              给 ``{``，都没有给空串
============================  ==========================================

## 三个照抄点

* **价格下拉是 ±5 个最小变动单位**（``for ($i = -5; $i <= 5; $i++)``），
  步长是 ``10 ^ -precision``（precision 取自 ``StockSymbol::GetPrecision``），
  共 11 档；``IsTradable()`` 为假时**没有**这个下拉（PHP 给 ``false``）。
* **只有可交易的成员出现在下拉里**：``SqlGetStockGroupItemSymbolArray``
  逐个判 ``IsTradable()``，不可交易的（如指数、外汇）整条跳过。
* **空价数组直接不渲染表单**：``if (count($arPrice) == 0) return;``
  （``_edittransactionform.php:161``）。

## 已知偏差

* **常用备注（``CommonPhraseSql``）未迁**：那张表不在本次范围，
  :data:`COMMON_PHRASE_TABLE_MISSING` 记着这件事，表单里对应下拉留空。
* **JS 联动改成 JSON 注入**：PHP 用 ``HtmlGetJsArray`` 拼 JS 字面量，
  本模块直接给 dict，模板里 ``| tojson``。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from core.stockdata import rtrim0
from core.stockgroupsql import StockGroupItemSql
from core.stocksymbol import StockSymbol
from core.submittransaction import DISP_ORDER

#: 价格下拉的档数：``for ($i = -5; $i <= 5; $i++)`` 共 11 档。
PRICE_OPTION_RANGE = range(-5, 6)

#: ``CommonPhraseSql`` 那张表未迁（账号体系相关的辅助表），表单里对应
#: 下拉留空。哪天要迁，改这一个常量并补上查询即可。
COMMON_PHRASE_TABLE_MISSING = True


def _connection_of(source: Any) -> Any:
    return getattr(source, "connection", source)


def _symbol_of(source: Any, stock_id: Any) -> Optional[str]:
    """``SqlGetStockSymbol()``：优先用数据源自己的方法（少一次查询）。"""
    getter = getattr(source, "get_stock_symbol", None)
    if getter is not None:
        return getter(stock_id)
    from sql.stocksql import StockSql

    return StockSql(_connection_of(source)).get_symbol(stock_id)


def sql_get_stock_group_item_symbol_array(item_sql: Any,
                                          source: Any = None) -> Dict[str, str]:
    """对应 ``SqlGetStockGroupItemSymbolArray($item_sql)``
    （``php/sql/sqlstockgroup.php:190``）。

    **只收可交易的成员**（``IsTradable()``），并按 A 股 → 港股 → 美股
    三组**各自 asort 再拼接**（``$arA + $arH + $arUS``）——PHP 注释里
    明说不能用 ``array_merge``（数字键会被重排）。

    返回 ``{item_id: symbol}``（Python dict 保序，等价于 PHP 的数组顺序）。
    """
    stock_ids = item_sql.get_stock_id_array()
    if not stock_ids:
        return {}
    group_a: Dict[str, str] = {}
    group_h: Dict[str, str] = {}
    group_us: Dict[str, str] = {}
    for item_id, stock_id in stock_ids.items():
        symbol = _symbol_of(source if source is not None else item_sql,
                            stock_id)
        if not symbol:
            continue
        sym = StockSymbol(symbol)
        if not sym.is_tradable():
            continue
        if sym.is_symbol_a():
            group_a[item_id] = symbol
        elif sym.is_symbol_h():
            group_h[item_id] = symbol
        else:
            group_us[item_id] = symbol
    result: Dict[str, str] = {}
    for group in (group_a, group_h, group_us):
        for item_id in sorted(group, key=lambda key: group[key]):
            result[item_id] = group[item_id]
    return result


def get_group_item_price_array(item_sql: Any,
                               history_sql: Any = None) -> Dict[str, Any]:
    """对应 ``_getGroupItemPriceArray($item_sql)``
    （``_edittransactionform.php:32``）：``{item_id: 最新收盘}``。

    取不到收盘价的成员**留 None**（PHP 的 ``GetCloseNow`` 给 false）；
    调用方据此决定表单显示什么。
    """
    if history_sql is None:
        from sql.historytable import StockHistorySql

        history_sql = StockHistorySql(getattr(item_sql, "_connection", None))
    prices: Dict[str, Any] = {}
    for item_id, stock_id in item_sql.get_stock_id_array().items():
        prices[item_id] = history_sql.get_close_now(stock_id)
    return prices


def get_group_item_quantity_array(item_sql: Any) -> Dict[str, str]:
    """对应 ``_getGroupItemQuantityArray($item_sql)``
    （``_edittransactionform.php:114``）：``{item_id: 上次成交份数}``。

    ``ltrim($record['quantity'], '-')`` —— **卖出的负号去掉**，因为表单
    的买卖方向由 ``type`` 单选框管。没成交过的成员给空串。
    """
    quantities: Dict[str, str] = {}
    trans_sql = item_sql.trans_sql
    for item_id in item_sql.get_stock_id_array():
        count = trans_sql.count(item_id)
        if not count:
            quantities[item_id] = ""
            continue
        records = trans_sql.get_record(item_id, 0, 1) or []
        if not records:
            quantities[item_id] = ""
            continue
        quantities[item_id] = str(records[0].get("quantity", "")).lstrip("-")
    return quantities


def get_price_option(sym: Any, price: Any) -> Optional[List[str]]:
    """对应 ``_getPriceOption($strGroupItemId, $strPrice)``
    （``_edittransactionform.php:13``）：价格下拉的 11 档。

    .. code-block:: php

        $iPrecision = $sym->GetPrecision();
        $fPrice = round($strPrice, $iPrecision);
        $iPow = pow(10, $iPrecision);
        for ($i = -5; $i <= 5; $i ++)   $ar[] = strval($fPrice + floatval($i) / $iPow);

    ``IsTradable()`` 为假时返回 ``None``（PHP 给 ``false``，于是没有这个
    下拉）。注意``round`` 是 PHP 口径（half away from zero），``strval``
    是 ``precision=14`` 的浮点转串 —— 两处都用现成的助手，不自己造。
    """
    from sql.historytable import php_round

    if not _call(sym, "is_tradable"):
        return None
    precision = int(_call_value(sym, "get_precision") or 0)
    try:
        base = php_round(price, precision)
    except (TypeError, ValueError):
        base = 0.0
    step_divisor = 10 ** precision
    options = []
    for index in PRICE_OPTION_RANGE:
        options.append(_php_strval_float(base + float(index) / step_divisor))
    return options


def get_price_option_js_array(prices: Dict[str, Any],
                              sym_of: Callable[[str], Any]) -> Dict[str, str]:
    """对应 ``_getPriceOptionJsArray($arPrice)``
    （``_edittransactionform.php:46``）：``{item_id: "档1,档2,..."}``。

    PHP 拼的是逗号串（JS 里再 ``split(',')``）；本模块同样给逗号串，
    模板里 ``| tojson`` 即可。
    """
    result: Dict[str, str] = {}
    for item_id, price in prices.items():
        options = get_price_option(sym_of(item_id), price)
        if options:
            result[item_id] = ",".join(options)
    return result


def get_suggested_remark(item_sql: Any, item_id: Any) -> str:
    """对应 ``_getSuggestedRemark($item_sql, $strGroupItemId)``
    （``_edittransactionform.php:81``）：新增时给备注一个建议值。

    * 指定了成员 → 取该成员**最近一笔**交易的备注；
    * 没指定（``$strGroupItemId`` 为假）→ 取**本分组**最近一笔的备注。

    然后 ``{`` / ``}`` **取反**：这是对冲申购的批次标记（见
    ``core/submittransaction`` 的 docstring 与 pitfalls #114）。
    """
    trans_sql = item_sql.trans_sql
    remark = ""
    if item_id:
        if trans_sql.count(item_id):
            records = trans_sql.get_record(item_id, 0, 1) or []
            if records:
                remark = records[0].get("remark") or ""
    else:
        if item_sql.count_all_stock_transaction() > 0:
            records = item_sql.get_all_stock_transaction(0, 1) or []
            if records:
                remark = records[0].get("remark") or ""
    if "{" in remark:
        return "}"
    if "}" in remark:
        return "{"
    return ""


def get_first_group_item(item_sql: Any, source: Any = None) -> Optional[str]:
    """对应 ``_getFirstGroupItem($item_sql)``
    （``_edittransactionform.php:74``）：成员下拉的第一项。"""
    symbols = sql_get_stock_group_item_symbol_array(item_sql, source)
    for item_id in symbols:
        return item_id
    return None


def build_transaction_form(source: Any, group_id: Any, submit: str,
                           trans_id: Any = None, item_id: Any = None,
                           history_sql: Any = None,
                           common_phrases: Any = None) -> Optional[dict]:
    """对应 ``StockEditTransactionForm()``
    （``_edittransactionform.php:137``）的**数据部分**。

    ``trans_id`` 非空走编辑分支（从交易记录回填），否则走新增分支。
    返回 ``None`` 表示"这个分组没有可填的成员 / 取不到价，不渲染表单"
    （PHP 的 ``if (count($arPrice) == 0) return;``）。

    返回的字典键名与表单字段名一一对应，模板直接取用：

    ==================  ==================================================
    键                   含义
    ==================  ==================================================
    ``submit``          提交按钮文案（``SUBMIT_NEW`` / ``SUBMIT_EDIT``）
    ``type``            ``'1'`` 买入 / ``'0'`` 卖出
    ``symbols``         ``[(item_id, symbol)]`` 下拉选项
    ``item_id``         当前选中的成员
    ``price``           价格框默认值
    ``quantity``        数量框默认值
    ``commission``      佣金框默认值
    ``remark``          备注框默认值
    ``price_array``     ``{item_id: 现价}``（JS 联动）
    ``quantity_array``  ``{item_id: 上次份数}``（JS 联动）
    ``price_option_array``  ``{item_id: "档1,档2,..."}``（JS 联动）
    ``price_options``   当前成员的 11 档价格（下拉）
    ``common_phrases``  常用备注（``CommonPhraseSql`` 未迁，恒空）
    ==================  ==================================================
    """
    #: PHP 的编辑分支先解出分组再建 ``StockGroupItemSql``（``:143-159``）：
    #: ``if (($strGroupId = SqlGetStockGroupId($record['groupitem_id'])) == false) return;``
    #: —— **分组号是从交易记录反查的**，不是 ``?groupid=`` 给的那个。
    #: 顺序不能倒：用错了分组，成员下拉就是另一组的成员。
    if trans_id:
        from sql.stocktransactiontable import StockTransactionSql

        record = StockTransactionSql(_connection_of(source)).get_record_by_id(
            trans_id)
        if record is None:
            return None
        from core.stockgroupsql import sql_get_stock_group_id

        resolved = sql_get_stock_group_id(source, record.get("groupitem_id"))
        if resolved is None:
            return None
        group_id = resolved

    item_sql = StockGroupItemSql(source, group_id)
    prices = get_group_item_price_array(item_sql, history_sql=history_sql)
    if not prices:
        return None
    quantities = get_group_item_quantity_array(item_sql)
    symbols = sql_get_stock_group_item_symbol_array(item_sql, source)

    trans_type = "1"
    price = ""
    quantity = ""
    commission = ""
    remark = ""

    if trans_id:
        #: ``record`` 在上面那一段已经取过（反查分组要用），这里直接用。
        item_id = record.get("groupitem_id")
        raw_quantity = str(record.get("quantity") or "")
        if _php_intval(raw_quantity) < 0:
            trans_type = "0"
            quantity = raw_quantity.lstrip("-")
        else:
            quantity = raw_quantity
        price = rtrim0(record.get("price"))
        remark = record.get("remark") or ""
        #: **备注含"申购"才回填手续费**（``_edittransactionform.php:155``）：
        #: 对冲申购的 fees 是申购费，其它交易的 fees 是佣金，不该混填。
        commission = rtrim0(record.get("fees")) if DISP_ORDER in remark else ""
    else:
        commission = ""
        remark = get_suggested_remark(item_sql, item_id)
        if not item_id:
            item_id = get_first_group_item(item_sql, source)
        price = prices.get(item_id) or ""
        quantity = quantities.get(item_id) or ""

    def _sym_of(candidate: Any) -> Any:
        symbol = symbols.get(candidate)
        return StockSymbol(symbol) if symbol else None

    return {
        "submit": submit,
        "type": trans_type,
        "symbols": list(symbols.items()),
        "item_id": item_id,
        "price": price,
        "quantity": quantity,
        "commission": commission,
        "remark": remark,
        #: 取不到收盘价的成员在 PHP 的 JS 数组里是 ``false``（空串），
        #: 这里同样给空串 —— 表单只是没有可联动的价，不该整页崩。
        "price_array": {key: _str_or_empty(value)
                        for key, value in prices.items()},
        "quantity_array": quantities,
        "price_option_array": get_price_option_js_array(prices, _sym_of),
        "price_options": get_price_option(_sym_of(item_id), price) or [],
        "common_phrases": common_phrases or {},
        "trans_id": trans_id,
    }


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _call(sym: Any, name: str) -> bool:
    method = getattr(sym, name, None)
    if method is None:
        return False
    return bool(method())


def _call_value(sym: Any, name: str) -> Any:
    method = getattr(sym, name, None)
    if method is None:
        return None
    return method()


def _php_intval(value: Any) -> int:
    """``intval()``：空串 / ``None`` / 非数字 → 0（PHP 同款，不抛异常）。"""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _php_strval_float(value: Any) -> str:
    """``strval(float)``（precision=14）。"""
    from ref.stockref import _php_strval_float as _impl

    return _impl(value)


def _str_or_empty(value: Any) -> str:
    """``HtmlGetJsArray`` 里的 false 就是空串（``GetCloseNow`` 取不到时）。"""
    if value is None or value == "":
        return ""
    return _php_strval_float(value)


# PHP 原名别名（AGENTS.md：保留 camelCase 别名）
SqlGetStockGroupItemSymbolArray = sql_get_stock_group_item_symbol_array
GetPriceOption = get_price_option
GetSuggestedRemark = get_suggested_remark
StockEditTransactionForm = build_transaction_form
