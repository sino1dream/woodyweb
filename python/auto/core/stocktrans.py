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

## 交易写入口（``woody/res/php/_submittransaction.php``）

本模块还承担写入口的两个纯函数（不碰 ``$_POST`` 全局，由调用方传
``params`` 字典进来，方便测试）：

* :func:`get_stock_quantity` —— ``_getStockQuantity()``：卖出记负数；
* :func:`get_stock_cost` —— ``_getStockCost()``：**六种默认佣金费率** +
  用户显式佣金/税费的百分比档。费率表见函数 docstring。

``php/stocktrans.php`` 的 ``UpdateStockGroupItemTransaction`` /
``UpdateStockGroupItem``（交易写完回填成员的 quantity/cost/record）与
``_submittransaction.php`` 的七个写入动作在 ``core/submittransaction.py``
—— 那边才碰数据库（本模块保持纯模型/纯函数）。
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


# ---------------------------------------------------------------------------
# PHP 口径小助手（_submittransaction.php 用到）
# ---------------------------------------------------------------------------

#: 与 historytable / stockref 同一个常量（PHP ``php/stock.php`` 的
#: ``MIN_FLOAT_VAL``）。``strval_round`` 的"按量级选精度"分支要用它，
#: 不 import 是为了让本模块的纯函数部分不拖 sql 层依赖
#: （sql 层在 ``number_display_cell`` 等处是**函数内**延迟 import 的）。
MIN_FLOAT_VAL = 0.0000005


def strval_round(value: Any, precision: Any = None) -> str:
    """对应 PHP ``strval_round()``（``php/debug.php:22``）。

    ::

        if ($iPrecision === false)
        {
            $f = abs($fVal);
            if ($f > (10 - MIN_FLOAT_VAL))      $iPrecision = 2;
            else if ($f > (2 - MIN_FLOAT_VAL))  $iPrecision = 3;
            else                                $iPrecision = 4;
        }
        return strval(round($fVal, $iPrecision));

    不传精度时按**量级**选档（>=10 → 2 位、>=2 → 3 位、其余 4 位）。
    交易写入口用 ``strval_round($fCommission + $fTax, 3)`` 显式 3 位。

    舍入与字符串化都委托 ``sql.historytable`` 的
    :func:`mysql_round`（``_php_strval_float(php_round(...))``）——
    ``php_round`` 是按 **PHP 8 实测口径**（最短十进制 half-away-from-zero）
    写的，本模块不另造一套（core → ref/sql 会成环，只能延迟 import；
    ``number_display_cell`` 也是这个模式）。
    """
    from sql.historytable import mysql_round

    if precision is None:
        magnitude = abs(_php_floatval(value))
        if magnitude > (10.0 - MIN_FLOAT_VAL):
            precision = 2
        elif magnitude > (2.0 - MIN_FLOAT_VAL):
            precision = 3
        else:
            precision = 4
    return mysql_round(value, int(precision))


def sql_clean_string(value: Any) -> str:
    """对应 PHP ``SqlCleanString()``（``php/sql.php:147``）的**有效部分**。

    PHP 是 ``UrlCleanString``（trim）再 ``mysqli_real_escape_string``。
    本项目的 SQL 一律参数化，转义不再需要，**只保留 trim 语义**：
    表单里的 ``' 100 '`` 与 ``'100'`` 必须视作同一个数，否则
    ``_getStockCost`` 的 ``empty()`` 分支会判错。
    """
    if value is None:
        return ""
    return str(value).strip()


def _php_empty(value: Any) -> bool:
    """对应 PHP ``empty()`` 对**字符串与标量**的语义。

    真值集合：``'' / '0' / 0 / 0.0 / None / False``。
    注意 ``'0.0'`` **不是** empty（两个非空字符串不同）—— 这是照抄的坑：
    用户在佣金框里填 ``0`` 想清零，PHP 会当成"没填"走默认费率。
    """
    if value is None or value is False:
        return True
    if isinstance(value, str):
        return value == "" or value == "0"
    if isinstance(value, (int, float)):
        return float(value) == 0.0
    return not value


def _php_eq_post_select(value: Any, expected: str) -> bool:
    """对应 PHP ``$_POST['x'] == '1'`` / ``== '0'`` 的松比语义。

    PHP 8 里数字串与数字串按**数值**比：``'1' == '1'`` 显然为真，
    而 ``1 == '1'``（数字与数字串）也为真。Python 的 ``1 == '1'`` 是
    False，所以要显式归一：两边都尝试转 float 再比，失败回落字符串比。
    """
    if isinstance(value, str):
        if value == expected:
            return True
        try:
            return float(value) == float(expected)
        except ValueError:
            return False
    if isinstance(value, (int, float)):
        return float(value) == float(expected)
    return False


def get_stock_quantity(params: Any) -> str:
    """对应 PHP ``_getStockQuantity()``（``_submittransaction.php:5``）。

    ::

        $strQuantity = SqlCleanString($_POST['quantity']);
        if ($_POST['type'] == '0')    // sell
        {
            $strQuantity = '-'.$strQuantity;
        }

    卖出（``type == '0'``）时在**串前**加负号 —— 数量始终以字符串形态
    往下传（``Insert`` 直接入库，``AddSqlTransaction`` 再 ``intval``）。
    注意加负号发生在 trim **之后**，``'-' + ''`` 得 ``'-'``（PHP 同款，
    不设防）。
    """
    quantity = sql_clean_string(_post_value(params, "quantity"))
    if _php_eq_post_select(_post_value(params, "type"), "0"):
        quantity = "-" + quantity
    return quantity


def get_stock_cost(sym: Any, quantity: Any, price: Any, params: Any) -> str:
    """对应 PHP ``_getStockCost()``（``_submittransaction.php:15``）。

    ``sym`` 是 ``StockSymbol``（PHP 的 ``GetGroupItemSym`` 解出来的），
    ``params`` 对应 ``$_POST``。返回 ``strval_round(..., 3)`` 的字符串
    （直接进 ``fees`` 列）。

    **六种默认佣金费率**（commission 框为空时按标的类型选）：

    ============================  ==========================================
    标的                          费率
    ============================  ==========================================
    新浪期货（``IsSinaFuture``）  固定 0.62
    新浪外汇（``IsSinaForex``）   固定 15.0
    A 股基金（``IsFundA``）       成交额 × 0.0001
    A 股其它                      买 成交额 × 0.0002 / 卖 × 0.0015，
                                  **保底 5 元**
    港股（``IsSymbolH``）         成交额 × 0.002
    其它（美股等）                <200 份固定 1 元，否则 0.005 × 份数；
                                  卖出再加 成交额 × 0.000028（类似证监会费）
    ============================  ==========================================

    用户显式填了佣金（非 empty）时：``commissionselect == '1'`` 把值当
    **千分比**（``× 成交额 / 1000``）；``taxselect`` 存在时税费同理，
    且税费**只有显式分支才有**（默认费率分支恒 0）。

    照抄的坑：``empty($strCommission)`` 把 ``'0'`` 也当"没填"，
    所以佣金框填 ``0`` 清不掉佣金，反而走默认费率。
    """
    f_tax = 0.0
    f_quantity = _php_floatval(quantity)
    f_quantity_abs = abs(f_quantity)
    f_amount = f_quantity_abs * _php_floatval(price)
    str_commission = sql_clean_string(_post_value(params, "commission"))
    if _php_empty(str_commission):
        #: 默认费率分支。判定顺序照 PHP：期货 → 外汇 → A 股 → 港股 → 其它。
        #: 顺序有意义：A 股里也有一批带 hf_/fx_ 前缀的符号，先判期货/外汇
        #: 才不会落进 A 股分支。
        if _call_sym(sym, "is_sina_future"):
            f_commission = 0.62
        elif _call_sym(sym, "is_sina_forex"):
            f_commission = 15.0
        elif _call_sym(sym, "is_symbol_a"):
            if _call_sym(sym, "is_fund_a"):
                f_commission = f_amount * 0.0001
            else:
                if f_quantity > 0.0:
                    f_commission = f_amount * 0.0002
                else:
                    f_commission = f_amount * 0.0015
                if f_commission < 5.0:
                    f_commission = 5.0        # 五元保底
        elif _call_sym(sym, "is_symbol_h"):
            f_commission = f_amount * 0.002
        else:
            if f_quantity_abs < 200.0:
                f_commission = 1.0
            else:
                f_commission = 0.005 * f_quantity_abs
            if f_quantity < 0.0:
                f_commission += f_amount * 0.000028
    else:
        f_commission = _php_floatval(str_commission)
        if _php_eq_post_select(_post_value(params, "commissionselect"), "1"):
            f_commission *= f_amount / 1000.0

        if _post_has(params, "taxselect"):
            f_tax = _php_floatval(sql_clean_string(_post_value(params, "tax")))
            if _php_eq_post_select(_post_value(params, "taxselect"), "1"):
                f_tax *= f_amount / 1000.0

    return strval_round(f_commission + f_tax, 3)


def _post_value(params: Any, key: str) -> Any:
    """取 ``$_POST`` 值；缺失给 ``None``（PHP 未设键时访问会 notice）。"""
    if params is None:
        return None
    if hasattr(params, "get"):
        return params.get(key)
    return None


def _post_has(params: Any, key: str) -> bool:
    """对应 PHP ``isset($_POST[$key])``：存在且非 None。"""
    return _post_value(params, key) is not None


def _call_sym(sym: Any, name: str) -> bool:
    """调 ``StockSymbol`` 的判定方法；鸭子类型替身也认。

    ``is_sina_forex`` 一类方法 PHP 侧返回的是**匹配到的前缀串**（空串
    表示否），Python 侧保留了这个形状 —— 所以这里统一按真值收，
    不要求返回 bool。
    """
    method = getattr(sym, name, None)
    if method is None:
        return False
    return bool(method())


def get_group_item_sym(item_id: Any, source: Any = None) -> Any:
    """对应 PHP ``GetGroupItemSym()``（``_edittransactionform.php:5``）。

    ::

        $item_sql = new StockGroupItemSql();
        $strStockId = $item_sql->GetStockId($strGroupItemId);
        $strSymbol = SqlGetStockSymbol($strStockId);
        return new StockSymbol($strSymbol);

    item id → stock_id → 符号 → ``StockSymbol``。``source`` 是数据源
    （``StockGroupItemSql`` 的构造入参）；查不到符号时返回 ``None``
    （PHP 会给 ``new StockSymbol(false)``，本版不当真值用）。
    """
    from core.stockgroupsql import StockGroupItemSql
    from core.stocksymbol import StockSymbol

    item_sql = StockGroupItemSql(source)
    stock_id = item_sql.get_stock_id(item_id)
    if stock_id is None:
        return None
    from sql.stocksql import StockSql

    stock_sql = StockSql(_connection_of(source))
    symbol = stock_sql.get_symbol(stock_id)
    if not symbol:
        return None
    return StockSymbol(symbol)


def _connection_of(source: Any) -> Any:
    """取数据源的连接（与 stockgroupsql 的 ``getattr(source, 'connection', source)`` 一致）。"""
    return getattr(source, "connection", source)
