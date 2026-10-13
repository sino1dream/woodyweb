"""证券交易（持仓）模型 —— 对应 ``php/stocktrans.php``。

PHP 那边是两个类：

* ``StockTransaction`` —— 只做加减：``iTotalShares`` / ``fTotalCost`` /
  ``iTotalRecords`` 三个累加量，外加 ``Add()`` / ``AddTransaction()``；
* ``MyStockTransaction extends StockTransaction`` —— 挂上一个
  ``MyStockReference``（``$ref``）与分组号，于是能算出市值、盈亏和均价显示。

本模块是**数据模型**，不发 SQL、不渲染。数据库那一侧（按 member 查分组、
按 groupitem 查交易记录）属于账号体系，用户要求暂缓，所以这里只提供
"拿到记录后怎么累加"的部分 —— 也就是 ``AddSqlTransaction()`` 那个公式。

## 成本里含手续费（容易漏）

PHP ``AddSqlTransaction()``（``php/stocktrans.php:129``）::

    $trans_class->AddTransaction($iQuantity,
                                 $iQuantity * floatval($record['price']) + floatval($record['fees']));

**手续费是加进成本、不加进数量**。所以 ``GetAvgCost()`` 永远比成交价高一丁点，
盈亏也会比"价差 × 数量"少一点。迁移时如果把 fees 漏掉，或用
``(price + fees)`` 当均价，数字会对不上。

## ``GetValue()`` 的持仓方向

``GetValue()`` 是 ``iTotalShares * GetPrice()``，**不带绝对值**。做空/融券
归还时 ``iTotalShares`` 会是负数，市值与盈亏随之变负 —— 这是有意的，
``EchoPortfolioParagraph`` 里 ``if ($iShares != 0)`` 就是靠符号区分方向的。
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# StockTransaction
# ---------------------------------------------------------------------------


class StockTransaction:
    """对应 PHP ``StockTransaction``（``php/stocktrans.php:4``）。

    三个累加量用**公有属性**暴露，与 PHP 的 ``var`` 一致：PHP 的
    ``UpdateStockGroupItemTransaction()`` 直接读 ``$trans->fTotalCost``
    与 ``$trans->iTotalRecords`` 写库， Python 侧以后接数据库时也要能这么读。
    """

    def __init__(self,
                 total_records: int = 0,
                 total_shares: int = 0,
                 total_cost: float = 0.0) -> None:
        self.total_cost = float(total_cost)
        self.total_shares = int(total_shares)
        self.total_records = int(total_records)

    # -- PHP 原名方法 ------------------------------------------------------
    def get_total_shares(self) -> int:
        return self.total_shares

    def get_total_cost(self) -> float:
        return self.total_cost

    def get_total_records(self) -> int:
        return self.total_records

    def set_value(self, total_records: int = 0, total_shares: int = 0,
                 total_cost: float = 0.0) -> None:
        """对应 ``SetValue()``：一次性重置三个累加量（构造时也会调）。"""
        self.total_cost = float(total_cost)
        self.total_shares = int(total_shares)
        self.total_records = int(total_records)

    def add_transaction(self, shares: Any, cost: Any = 0.0) -> None:
        """对应 ``AddTransaction($iShares, $fCost = 0.0)``。

        ``shares`` 走 ``intval()`` 语义：PHP 的 ``$record['quantity']`` 是
        字符串，``intval('100')`` 得 ``100``。这里同样强转，避免调用方传
        ``'100'`` 时把后面全拼成字符串。
        """
        self.total_cost += float(cost)
        self.total_shares += int(shares)
        self.total_records += 1

    def Add(self, trans: "StockTransaction") -> None:
        """对应 ``Add($trans)``：把另一笔交易的合计量并进来。

        只并**合计量**，不并明细 —— PHP 也是
        ``AddTransaction($trans->GetTotalShares(), $trans->GetTotalCost())``。
        于是 ``total_records`` 只加 1（"又多了一组"），
        不是加对方的记录数。
        """
        self.add_transaction(trans.get_total_shares(), trans.get_total_cost())

    def get_avg_cost(self) -> float:
        """对应 ``GetAvgCost()``：含手续费的单位成本。

        ``iTotalShares != 0`` 时才除，否则回 0.0 —— 空仓（全部卖出）时
        避免除零，也避免显示一个莫名其妙的均价。
        """
        if self.total_shares != 0:
            return self.total_cost / self.total_shares
        return 0.0


# ---------------------------------------------------------------------------
# MyStockTransaction
# ---------------------------------------------------------------------------


class MyStockTransaction(StockTransaction):
    """对应 PHP ``MyStockTransaction``（``php/stocktrans.php:55``）。

    比基类多三个字段：``ref``（引用对象）、``group_id``、``group_item_id``。
    前两个由构造函数传入；``group_item_id`` 在 PHP 里是当场查
    ``StockGroupItemSql`` 拿的，属于数据库层，这里改成**调用方显式传入**
    （账号体系暂缓，不能让模型去连库）。
    """

    def __init__(self,
                 ref: Any = None,
                 group_id: Any = False,
                 group_item_id: Any = False) -> None:
        super().__init__()
        self.ref = ref
        self.group_id = group_id
        self.group_item_id = group_item_id

    # -- 标识 --------------------------------------------------------------
    def get_ref(self) -> Any:
        return self.ref

    def get_group_id(self) -> Any:
        return self.group_id

    def get_symbol(self) -> Any:
        """对应 ``GetSymbol()``：``ref`` 为空时返回 ``False``（不是 ``None``）。

        调用方 ``_myportfolio.php`` 拿它做 ``isset($ar[$strSymbol])`` 的键，
        PHP 的 ``false`` 与空串都会走 else 分支，语义上等价于"没有符号"。
        """
        if self.ref:
            return self.ref.get_symbol()
        return False

    def is_symbol_a(self) -> bool:
        """``_myportfolio.php:141`` 用的三个分支判定（A / H / 其它）。"""
        return _call(self.ref, "is_symbol_a", "IsSymbolA")

    def is_symbol_h(self) -> bool:
        return _call(self.ref, "is_symbol_h", "IsSymbolH")

    # -- 显示 --------------------------------------------------------------
    def get_avg_cost_display(self) -> str:
        """对应 ``GetAvgCostDisplay()``：``ref->GetPriceDisplay(GetAvgCost())``。

        注意只传一个参数，于是 PHP 的 ``$fPrev`` 取**当前价** ——
        均价低于现价时显示红色（涨），高于现价时绿色（跌）。
        """
        if self.ref:
            return self.ref.get_price_display(self.get_avg_cost())
        return ''

    def get_value(self) -> float:
        """对应 ``GetValue()``：``iTotalShares * floatval(GetPrice())``。

        ``ref`` 为空时回 0.0（PHP 同）。``GetPrice()`` 可能返回空串，
        所以必须走 ``_php_floatval`` 而不是 ``float()``。
        """
        if self.ref:
            return self.total_shares * _php_floatval(_invoke(self.ref, "get_price", "GetPrice"))
        return 0.0

    def get_profit(self) -> float:
        """对应 ``GetProfit()``：市值减总成本（成本含手续费）。"""
        return self.get_value() - self.total_cost

    def get_profit_display(self) -> str:
        """对应 ``GetProfitDisplay()``：``GetNumberDisplay(GetProfit())``。

        颜色口径见 :func:`number_display_cell` —— PHP 的
        ``GetNumberDisplay()`` 默认 ``$strGreater='black'``、``$strLess='red'``，
        即**只有负数染红**。全站统一成涨红跌绿，见
        ``reference_service.percentage_cell`` 的同一处理。
        """
        from sql.historytable import format_number_display

        return format_number_display(self.get_profit())


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _invoke(ref: Any, snake: str, camel: str, *args: Any) -> Any:
    """先试 snake_case 再试 camelCase（见 AGENTS.md 的别名约定）。"""
    if ref is None:
        return None
    method = getattr(ref, snake, None)
    if method is None:
        method = getattr(ref, camel, None)
    if method is None:
        return None
    return method(*args)


def _call(ref: Any, snake: str, camel: str) -> bool:
    value = _invoke(ref, snake, camel)
    return bool(value)


def _php_floatval(value: Any) -> float:
    """PHP ``floatval()`` 语义：空串 / ``False`` / ``None`` 都是 0.0。"""
    if value is None or value is False or value == '':
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def number_display_cell(value: Any) -> tuple:
    """对应 ``GetNumberDisplay()``（``php/stock.php:111``）的单元格形式。

    PHP 走的是 ``StockGetPriceDisplay($fVal, 0.0, 2, 'black', 'red')``
    —— 以 **0** 为基准，所以正数是 ``black``、负数是 ``red``。
    本项目全站统一"涨红跌绿"，因此这里把正数映射成 ``red``、负数映射成
    ``green``（与 ``reference_service.percentage_cell`` 同一套取舍），
    零映射成 ``grey``。

    返回 ``(文本, 颜色类)``，颜色类由模板映射成 CSS。
    """
    from sql.historytable import format_number_display

    text = format_number_display(value)
    if text == '':
        return ('', '')
    try:
        number = float(value)
    except (TypeError, ValueError):
        return (text, '')
    if number > 0.0:
        return (text, 'red')
    if number < 0.0:
        return (text, 'green')
    return (text, 'grey')


# ---------------------------------------------------------------------------
# PHP 原名别名（见 AGENTS.md：保留 camelCase 别名）
#
# 注意必须是**类属性**而不是模块级变量：模块级赋值只是多了一个指向同一个
# 函数对象的全局名，``trans.GetSymbol()`` 仍然找不到。
# ---------------------------------------------------------------------------

for _camel, _snake in (
    ("GetTotalShares", "get_total_shares"),
    ("GetTotalCost", "get_total_cost"),
    ("GetTotalRecords", "get_total_records"),
    ("SetValue", "set_value"),
    ("AddTransaction", "add_transaction"),
    ("GetAvgCost", "get_avg_cost"),
    ("GetRef", "get_ref"),
    ("GetGroupId", "get_group_id"),
    ("GetSymbol", "get_symbol"),
    ("IsSymbolA", "is_symbol_a"),
    ("IsSymbolH", "is_symbol_h"),
    ("GetAvgCostDisplay", "get_avg_cost_display"),
    ("GetValue", "get_value"),
    ("GetProfit", "get_profit"),
    ("GetProfitDisplay", "get_profit_display"),
):
    for _cls in (StockTransaction, MyStockTransaction):
        if hasattr(_cls, _snake):
            setattr(_cls, _camel, getattr(_cls, _snake))

del _camel, _snake, _cls


def add_sql_transaction(trans_class: "StockTransaction", record: Any) -> None:
    """对应 ``AddSqlTransaction()``（``php/stocktrans.php:129``）。

    ::

        $iQuantity = intval($record['quantity']);
        $trans_class->AddTransaction($iQuantity,
            $iQuantity * floatval($record['price']) + floatval($record['fees']));

    数量取整、成本含手续费。``record`` 是字典（``{'quantity':…, 'price':…,
    'fees':…}``），字段缺失按 0 处理 —— PHP 的 ``floatval(null)`` 也是 0.0，
    ``intval(null)`` 同样是 0，所以缺字段不会炸。
    """
    quantity = int(_php_floatval(record.get('quantity')))
    cost = quantity * _php_floatval(record.get('price')) + _php_floatval(record.get('fees'))
    trans_class.add_transaction(quantity, cost)


def get_sql_transaction_date(record: Any) -> Any:
    """对应 ``GetSqlTransactionDate()``（``php/stocktrans.php:124``）。

    PHP 是 ``strstr($record['filled'], ' ', true)`` —— 取**第一个空格之前**
    的部分，即成交日期（``filled`` 形如 ``2026-10-13 09:30:00``）。
    ``strstr(..., true)`` 在找不到空格时返回 ``False``，按 AGENTS.md 的
    字符串类方法映射规则回 ``None``。

    注意与"第一个空格就在开头"（``' 2026-10-13 …'``）区分：那种情形 PHP
    返回的是**真空串**而不是 false，这里同样返回 ``''``。两者语义不同 ——
    前者是"没有日期"，后者是"日期为空"。
    """
    filled = record.get('filled') if hasattr(record, 'get') else None
    if not filled:
        return None
    text = str(filled)
    return text.split(' ', 1)[0] if ' ' in text else None
