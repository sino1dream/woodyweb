"""投资组合（个股持仓盈亏）段落 —— 对应 ``php/ui/portfolioparagraph.php``（160 行）。

这是 ``php/ui`` 里**唯一一个按"用户自己的交易"渲染**的段落：数据不是行情，
而是 ``stocktransaction`` 表里那个人自己的买卖记录。调用方有两个：

* ``woody/res/php/_myportfolio.php:153`` —— 整个组合一页一张表；
* ``woody/res/php/_stockaccount.php:207`` —— 单个分组一张表
  （``EchoStockTransaction()`` 里，与交易明细段落并排）。

因为账号登录体系用户要求**暂缓**，本模块只做"拿到交易记录之后怎么渲染"，
取数那一侧（按 member 查分组）留空，由调用方喂 ``MyStockTransaction`` 进来。

## ⚠️ 本段落最需要注意的三件事

### 1. ``static $fCny`` 是**跨调用持久**的隐藏全局

.. code-block:: php

    function _echoPortfolioTableItem($trans)
    {
        static $fCny = 0.0;
        ...
        if (in_arrayQdiiMix($strSymbol) || in_arrayQdii($strSymbol))
            $fCny += $fVal * $ref->GetPosition();
        ...
        case 'fx_susdcnh':
            $fVal += $fCny;

PHP 的函数级 ``static`` 在**同一次请求里的所有调用之间共享**。于是
``_myportfolio.php`` 先渲染整张组合表（``$fCny`` 累满），随后
``_stockaccount.php`` 若再渲染分组表，``$fCny`` **不会归零**，
会把上一张表的量一起算进美元折算里。

Python 没有等价的"函数级静态"，硬用模块级变量会把状态泄漏到进程里
（多线程下还会互相踩）。所以这里改成**显式传入**的
:class:`PortfolioCnyTotal`：一次页面渲染建一个实例，需要跨表共享就
把它传给每一次 :func:`build_portfolio_table`。不传则内部新建
（等价于 PHP 里只调用一次段落的情况）。

### 2. 列数（7）与格数（2 / 6 / 7）不相等

``if ($iShares != 0)`` 把市值以下四格整块跳过；``switch`` 只对七个符号
补最后一格。所以同一张表里会出现 2 格、6 格、7 格三种行宽 ——
**照抄，不补空**。补空格会让"没持仓"和"持仓为 0"在页面上看起来一样，
而 PHP 是靠少几格来区分的。

### 3. ``_getArbitrageTestStr()`` 的 ``while ($record = ...)`` 是**双向**泄漏

.. code-block:: php

    while ($record = mysqli_fetch_assoc($result)) {
        if ($strStockId != $record['stock_id']) {
            $iArbitrageQuantity = intval($record['quantity']);
            if ($iArbitrageQuantity > 0) break;      // 只有正的才停
        }
    }
    if ($record)                                     // ← 循环变量还在作用域里
        return number_format($iArbitrageQuantity + ... * GetStockHedge(
            SqlGetStockSymbol($record['stock_id']), $record['stock_id']));
    return '';

PHP 的 ``while ($record = ...)`` **每一轮都给 ``$record`` 赋值**，包括
最后那次取不到行的对拍。于是循环结束后 ``$record`` 是什么，取决于怎么
收尾：

* **break 出来** —— ``$record`` 是触发 break 那一条，与
  ``$iArbitrageQuantity`` 指向同一行；
* **自然耗尽**（组内非本标的行数量全都 ≤ 0）—— 最后一次赋值把
  ``null`` 留在了 ``$record`` 里，``if ($record)`` 为假，函数返回空串。

所以"数量"与"hedge"**永远同源**，不存在"最后一行是别的标的"的情形。
初版 Python 移植把耗尽路径误写成"取最后一行"（还顺手写了条测试把它
钉住），``_php_portfolio_compare.py`` 逐例对照当场抓出：没有正数量
条目时 PHP 回空串，误写的版本会拿最后一行的 stock_id 去查 hedge。
见 pitfalls 第 47 条。
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Sequence

from sql.historytable import TableRow, format_int_display, format_number_display
from ui.uicolumn import (
    column_change,
    column_holding,
    column_price,
    column_profit,
    column_quantity,
    column_symbol,
    column_test,
    columns_to_dicts,
)

from web.services.reference_service import percentage_cell  # noqa: E402

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: ``EchoTableParagraphBegin($ar, 'myportfolio', …)`` 的 ``$strId``。
#: PHP 渲染成 ``id="myportfoliotable"``（``table.php:115`` 拼了 ``table`` 后缀）。
PORTFOLIO_TABLE_ID = "myportfolio"

#: 段落标题前缀（``'个股'.$profit_col->GetDisplay()``）。
PORTFOLIO_TITLE_PREFIX = "个股"

#: 价格列的前缀（``new TableColumnPrice('平均')``）—— 列头因此是 ``平均价格``。
#: 注意与 :data:`PORTFOLIO_TITLE_PREFIX` 不是一回事：一个是标题前缀，
#: 一个是价格列前缀，两处都硬编码在 PHP 的 ``EchoPortfolioParagraph()`` 里。
PORTFOLIO_PRICE_PREFIX = "平均"

#: ``_getPortfolioTestVal()`` 里那几个手工数量（``portfolioparagraph.php:9-18``）。
#: 作者把自己想盯的几只标的的"目标持仓"写死在代码里，测试列显示
#: ``实际持仓 - 目标持仓``。其它符号默认 0，即显示实际持仓本身。
PORTFOLIO_TEST_QUANTITY = {
    "KWEB": 200,
    "SH600104": 12000,
    "TLT": 500,
}

#: 走 ``GetArbitrageQuantity()`` 的 LOF 符号（``portfolioparagraph.php:117-122``）。
#: 这些是"能拿一篮子股票换回来"的 LOF，测试列显示折合的股票份数。
ARBITRAGE_SYMBOLS = (
    "SZ161125",
    "SZ161127",
    "SZ161130",
    "SZ162411",
    "SZ162415",
    "SZ164906",
)

#: ``hf_ES`` 的合约乘数 Divisor（``$ar[] = strval($iShares / 5)``）。
HF_ES_DIVISOR = 5.0

#: 美元折人民币那一行的符号（``portfolioparagraph.php:130``）。
FX_SUSDCNH_SYMBOL = "fx_susdcnh"

#: 美元金额显示的千位单位（``/ 1000.0`` 后加 ``K``）。
FX_THOUSAND = 1000.0

#: 美元金额显示的货币前缀（``'$'.number_format(...).'K'``）。
FX_DOLLAR_PREFIX = "$"
FX_DOLLAR_SUFFIX = "K"


# ---------------------------------------------------------------------------
# static $fCny 的显式替代
# ---------------------------------------------------------------------------


class PortfolioCnyTotal:
    """``static $fCny`` 的显式版本：跨行累加的"折人民币总额"。

    只累加 QDII 与 QDII 混合标的的**人民币市值**
    （``$fVal * $ref->GetPosition()``），在 ``fx_susdcnh`` 那一行被读一次，
    折成美元显示。见模块 docstring 第 1 条 —— 这个对象必须由**页面**
    持有并在多张表之间传递，不能做成模块级变量。
    """

    def __init__(self, value: float = 0.0) -> None:
        self.value = float(value)

    def add(self, value: Any) -> float:
        """累加一笔人民币市值，返回累加后的总额。"""
        try:
            self.value += float(value)
        except (TypeError, ValueError):
            return self.value
        return self.value

    def add_qdii(self, value: Any, position: Any) -> float:
        """对应 ``$fCny += $fVal * $ref->GetPosition()``。

        ``position`` 是仓位（0.0 ~ 1.0）。空值按 0 处理 —— PHP 的
        ``GetPosition()`` 返回 ``$this->ratio``，取不到时是 0.0，
        乘出来还是 0.0，与"不加"等价。
        """
        try:
            amount = float(value) * _php_floatval(position)
        except (TypeError, ValueError):
            return self.value
        return self.add(amount)


# ---------------------------------------------------------------------------
# 三个测试列（对应 _getPortfolioTestVal / _getArbitrageTestStr / GetArbitrageQuantity）
# ---------------------------------------------------------------------------


def get_portfolio_test_val(shares: Any, symbol: Any) -> int:
    """对应 ``_getPortfolioTestVal()``（``portfolioparagraph.php:4``）。

    .. code-block:: php

        return $iShares - $iQuantity;   // $iQuantity 是写死的目标持仓

    只对 :data:`PORTFOLIO_TEST_QUANTITY` 里的三个符号给非零目标，
    其余符号目标是 0，于是**原样返回持仓数**。所以这一列对绝大多数标的
    只是"持仓"的复制 —— 这是有意的，作者只为那三只设了提醒线。
    """
    target = PORTFOLIO_TEST_QUANTITY.get(symbol, 0)
    try:
        return int(shares) - target
    except (TypeError, ValueError):
        return 0


def get_arbitrage_quantity(symbol: Any, stock_id: Any, quantity: Any,
                           hedge_of: Callable[[Any, Any], Any] = None) -> str:
    """对应 ``GetArbitrageQuantity()``（``php/ui/stockcomm.php:65``）。

    .. code-block:: php

        return number_format($fQuantity / GetStockHedge($strSymbol, $strStockId));

    ``GetStockHedge()`` 是"一份基金抵多少份对标股票"的比率，除出来就是
    折股的股票份数。**带千分位逗号**（``number_format`` 单参数版），
    见 :func:`sql.historytable.format_int_display`。

    ``hedge_of`` 是注入的对冲比率查询；不传时按 1.0 计（即原样显示份数），
    与 PHP 里 ``GetStockHedge()`` 查不到值时回 1.0 一致。
    """
    try:
        shares = float(quantity)
    except (TypeError, ValueError):
        shares = 0.0
    hedge = 1.0
    if hedge_of is not None:
        try:
            hedge = _php_floatval(hedge_of(symbol, stock_id))
        except (TypeError, ValueError):
            hedge = 1.0
    if hedge == 0.0:
        hedge = 1.0
    return format_int_display(shares / hedge)


def get_arbitrage_test_str(shares: Any, group_id: Any, stock_id: Any, symbol: Any,
                           items: Sequence = (),
                           symbol_of: Callable[[Any], Any] = None,
                           hedge_of: Callable[[Any, Any], Any] = None) -> str:
    """对应 ``_getArbitrageTestStr()``（``portfolioparagraph.php:51``）。

    只有 ``KWEB`` 走这一支（PHP 的 ``switch`` 里只给它开了口）。
    含义是：**同一个分组里第一个正数量标的的持仓 + 本标的偏离目标的
    部分折成对冲量**。``KWEB`` 与中概互联 LOF 常被作者放在同一分组做
    对冲，所以这里要跨标的取值。

    PHP 原文的语义（照抄，见模块 docstring 第 3 条）::

        while ($record = mysqli_fetch_assoc($result)) {
            if ($strStockId != $record['stock_id']) {          // 跳过自己
                $iArbitrageQuantity = intval($record['quantity']);
                if ($iArbitrageQuantity > 0) break;            // 找到正的才停
            }
        }
        if ($record)                                            // ← 只有 break 才为真
            return number_format($iArbitrageQuantity +
                                 _getPortfolioTestVal(...) * GetStockHedge(...));
        return '';

    * ``items`` —— 分组内全部条目（``[{'stock_id':…, 'quantity':…}, …]``），
      由调用方注入（数据库侧暂缓）；
    * ``symbol_of`` —— ``SqlGetStockSymbol($stock_id)`` 的替身；
    * **组内没有正数量的非本标的行时返回空串** —— PHP 的 while 在耗尽时
      把 ``null`` 写回 ``$record``，``if ($record)`` 为假。这不是"取不到
      数据"的容错，是原作的逻辑：没有可对冲的腿就不显示这一列。
    """
    arbitrage_quantity = 0
    break_record = None
    for record in items:
        record_stock_id = record.get("stock_id") if hasattr(record, "get") else None
        if stock_id != record_stock_id:
            arbitrage_quantity = int(_php_floatval(record.get("quantity")))
            if arbitrage_quantity > 0:
                # PHP 在这里 break：$record 停在触发行，数量与 hedge 同源
                break_record = record
                break

    if break_record is None:
        # 自然耗尽时 PHP 的 while 赋值把 null 留在 $record 里，
        # if ($record) 为假 → 空串（见模块 docstring 第 3 条）
        return ""

    quantity = get_portfolio_test_val(shares, symbol)
    other_id = break_record.get("stock_id") if hasattr(break_record, "get") else None
    other_symbol = symbol_of(other_id) if symbol_of is not None else other_id
    hedge = 1.0
    if hedge_of is not None:
        try:
            hedge = _php_floatval(hedge_of(other_symbol, other_id))
        except (TypeError, ValueError):
            hedge = 1.0
    if hedge == 0.0:
        hedge = 1.0
    return format_int_display(arbitrage_quantity + quantity * hedge)


# ---------------------------------------------------------------------------
# 一行（对应 _echoPortfolioTableItem）
# ---------------------------------------------------------------------------


def _php_floatval(value: Any) -> float:
    """PHP ``floatval()``：空串 / ``False`` / ``None`` 都是 0.0。"""
    if value is None or value is False or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _php_round(value: float) -> int:
    """PHP ``round()``（默认 0 位小数）：**四舍五入远离零**。

    Python 的 ``round()`` 是银行家舍入（``round(2.5) == 2``），
    而 PHP 得 3。``fx_susdcnh`` 那行的金额显示会被这个差异咬到，
    所以不能直接用内置 ``round``。
    """
    if value >= 0:
        return int(value + 0.5)
    return int(value - 0.5)


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


def _test_cell(symbol: Any, shares: Any, stock_id: Any, group_id: Any, value: Any,
               cny: "PortfolioCnyTotal", ref: Any,
               hedge_of: Callable[[Any, Any], Any] = None,
               items: Sequence = (),
               symbol_of: Callable[[Any], Any] = None) -> Any:
    """最后一格（``测试``）的取值。返回 ``str`` 或 ``(文本, URL)`` 二元组。

    ``switch`` 的七个分支逐个照抄。**没有 default** —— 不在清单里的符号
    不追加这一格，于是行宽是 6 而不是 7。
    """
    if symbol == FX_SUSDCNH_SYMBOL:
        # PHP: $fVal += $fCny; 然后两个展示都基于加过之后的 $fVal。
        # 注意市值那一格（索引 2）已经用加之前的 $fVal 填好了，这里只影响本格。
        total = _php_floatval(value) + cny.value
        rate = _php_floatval(_invoke(ref, "get_val", "GetVal"))
        if rate == 0.0:
            rate = 1.0
        text = "%s%s%s" % (FX_DOLLAR_PREFIX,
                          format_int_display(total / rate / FX_THOUSAND),
                          FX_DOLLAR_SUFFIX)
        from ref.stocklink import get_overnight_cnh_link

        return (text, get_overnight_cnh_link(text, str(_php_round(0.0 - total))))

    if symbol == "hf_ES":
        try:
            return str(int(_php_floatval(shares) / HF_ES_DIVISOR))
        except (TypeError, ValueError):
            return "0"

    if symbol in ARBITRAGE_SYMBOLS:
        return get_arbitrage_quantity(symbol, stock_id, shares, hedge_of=hedge_of)

    if symbol in PORTFOLIO_TEST_QUANTITY:
        if symbol == "KWEB":
            return get_arbitrage_test_str(shares, group_id, stock_id, symbol,
                                          items=items, symbol_of=symbol_of,
                                          hedge_of=hedge_of)
        return str(get_portfolio_test_val(shares, symbol))

    return None


def build_portfolio_row(trans: Any, cny: "PortfolioCnyTotal" = None,
                        name_of: Callable[[Any], Any] = None,
                        hedge_of: Callable[[Any, Any], Any] = None,
                        items_of: Callable[[Any], Sequence] = None,
                        symbol_of: Callable[[Any], Any] = None,
                        link_builder: Callable[..., Any] = None) -> TableRow:
    """对应 ``_echoPortfolioTableItem()``：把一笔交易渲染成一行。

    ============================  ============================================
    参数                          用途
    ============================  ============================================
    ``trans``                     :class:`core.stocktrans.MyStockTransaction`
    ``cny``                       ``static $fCny`` 的显式版本，见
                                   :class:`PortfolioCnyTotal`。不传则内部
                                   新建一个**只服务本行**的实例 —— 那样
                                   ``fx_susdcnh`` 行只会看到自己累加的量。
    ``name_of``                   ``SqlGetStockName()`` 的替身，填首格 tooltip。
    ``hedge_of``                  ``GetStockHedge()`` 的替身。
    ``items_of``                  按分组号取组内条目（``_getArbitrageTestStr`` 用）。
    ``symbol_of``                 ``SqlGetStockSymbol()`` 的替身。
    ``link_builder``              首格链接构造器；不传则用
                                   :func:`ref.stocklink.get_transaction_link`
                                   （PHP 原样的站内 URL）。
    ============================  ============================================
    """
    if cny is None:
        cny = PortfolioCnyTotal()

    row = TableRow()
    ref = _invoke(trans, "get_ref", "GetRef")
    symbol = _invoke(trans, "get_symbol", "GetSymbol")
    stock_id = _invoke(ref, "get_stock_id", "GetStockId") if ref is not None else None
    group_id = _invoke(trans, "get_group_id", "GetGroupId")

    # 首格：交易明细页链接。PHP 的 StockGetTransactionLink() 返回 <a> 片段，
    # 这里改成 URL + 文本两段，由模板拼 <a>（见 TableRow.href 的说明）。
    if link_builder is not None:
        linked = link_builder(group_id, symbol)
        if isinstance(linked, tuple):
            row.href, first_text = linked
        else:
            row.href, first_text = linked, symbol
    else:
        from ref.stocklink import get_transaction_link

        html = get_transaction_link(group_id, symbol)
        row.href = _href_of(html)
        first_text = _text_of(html)
    row.add(first_text)

    # 盈亏格。颜色走全站统一的涨红跌绿（PHP 的 GetNumberDisplay 只把负数染红）。
    row.add(format_number_display(_invoke(trans, "get_profit", "GetProfit")),
            _profit_color(_invoke(trans, "get_profit", "GetProfit")))

    shares = _invoke(trans, "get_total_shares", "GetTotalShares")
    if shares != 0:
        value = _invoke(trans, "get_value", "GetValue")

        # static $fCny 的累加点：QDII 与 QDII 混合标的折人民币
        if _in_qdii(symbol):
            position = _invoke(ref, "get_position", "GetPosition")
            cny.add_qdii(value, position)

        row.add(format_number_display(value))
        row.add(str(shares))
        row.add(_invoke(trans, "get_avg_cost_display", "GetAvgCostDisplay") or "")

        total_cost = _invoke(trans, "get_total_cost", "GetTotalCost")
        if _php_floatval(total_cost) > 0.0:
            avg_cost = _invoke(trans, "get_avg_cost", "GetAvgCost")
            text = _invoke(ref, "get_percentage_text", "GetPercentageText", avg_cost)
            row.add(percentage_cell(text))
        else:
            row.add("")

        items = items_of(group_id) if items_of is not None else ()
        cell = _test_cell(symbol, shares, stock_id, group_id, value, cny, ref,
                          hedge_of=hedge_of, items=items, symbol_of=symbol_of)
        if cell is not None:
            if isinstance(cell, tuple):
                # fx_susdcnh：文本 + 跳转链接（非首格也能挂链接，见 TableRow.hrefs）
                row.add(cell[0])
                row.hrefs[len(row.cells) - 1] = cell[1]
            else:
                row.add(cell)

    if name_of is not None:
        row.title = name_of(symbol) or ""
    return row


def _in_qdii(symbol: Any) -> bool:
    """``in_arrayQdiiMix($strSymbol) || in_arrayQdii($strSymbol)``。"""
    from core.stocksymbol import in_array_qdii, in_array_qdii_mix

    try:
        return bool(in_array_qdii_mix(symbol) or in_array_qdii(symbol))
    except (TypeError, ValueError):
        return False


def _profit_color(value: Any) -> str:
    """盈亏格颜色。PHP 的 ``GetNumberDisplay`` 只把**负数**染红。

    本项⽬全站统一涨红跌绿，所以正数红、负数绿、零灰。
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if number > 0.0:
        return "red"
    if number < 0.0:
        return "green"
    return "grey"


def _href_of(html: str) -> str:
    """从 ``<a href="...">`` 里取出 URL（服务层不产出 HTML 片段）。"""
    text = str(html)
    start = text.find('href="')
    if start < 0:
        return ""
    start += len('href="')
    end = text.find('"', start)
    return text[start:end] if end > start else ""


def _text_of(html: str) -> str:
    """从 ``<a href="...">TEXT</a>`` 里取出显示文本。"""
    text = str(html)
    start = text.find(">")
    if start < 0:
        return ""
    start += 1
    end = text.find("</a>", start)
    return text[start:end] if end > start else text[start:]


# ---------------------------------------------------------------------------
# 整张表（对应 EchoPortfolioParagraph）
# ---------------------------------------------------------------------------


def portfolio_columns() -> list:
    """七个列定义，顺序与 PHP 的 ``EchoTableParagraphBegin(array(...))`` 一致。

    ==================  ======  =========  ==================================
    PHP                宽 度    颜色        说明
    ==================  ======  =========  ==================================
    ``TableColumnSymbol``      80       maroon    代码
    ``TableColumnProfit``      105      red       盈亏
    ``TableColumnHolding``     105      —         持仓
    ``TableColumnQuantity``    100      indigo    数量
    ``TableColumnPrice('平均')`` 70      blue      平均价格
    ``TableColumnChange``      70       —         涨幅
    ``TableColumnTest``        110      —         测试
    ==================  ======  =========  ==================================

    总宽 640。``TableColumnPrice`` 的第一个参数是**前缀**，所以列头是
    ``平均`` + ``价格``；``TableColumnProfit`` 的显示值还单独用在段落标题上
    （``'个股'.$profit_col->GetDisplay()``）。
    """
    profit = column_profit()
    return [
        column_symbol(),
        profit,
        column_holding(),
        column_quantity(),
        column_price(PORTFOLIO_PRICE_PREFIX),
        column_change(),
        column_test(),
    ]


def portfolio_title() -> str:
    """对应 ``'个股'.$profit_col->GetDisplay()``。"""
    return PORTFOLIO_TITLE_PREFIX + column_profit().text


def build_portfolio_table(trans_list: Iterable,
                          cny: "PortfolioCnyTotal" = None,
                          name_of: Callable[[Any], Any] = None,
                          hedge_of: Callable[[Any, Any], Any] = None,
                          items_of: Callable[[Any], Sequence] = None,
                          symbol_of: Callable[[Any], Any] = None,
                          link_builder: Callable[..., Any] = None) -> dict:
    """对应 ``EchoPortfolioParagraph($arTrans)``：整张表。

    PHP 的守卫是 ``foreach ($arTrans as $trans) if ($trans->GetTotalRecords() > 0)``
    —— **没有交易记录的条目整行跳过**（连符号都不显示）。这里同样先过滤。

    ``cny`` 见 :class:`PortfolioCnyTotal`：一次页面渲染建一个实例、
    多张表传同一个，才能复刻 PHP 那个跨调用持久的行为。
    """
    if cny is None:
        cny = PortfolioCnyTotal()

    rows = []
    for trans in trans_list or ():
        records = _invoke(trans, "get_total_records", "GetTotalRecords")
        if not records:
            continue
        rows.append(build_portfolio_row(trans, cny=cny, name_of=name_of,
                                        hedge_of=hedge_of, items_of=items_of,
                                        symbol_of=symbol_of,
                                        link_builder=link_builder))

    return {
        "id": PORTFOLIO_TABLE_ID,
        "title": portfolio_title(),
        "columns": columns_to_dicts(portfolio_columns()),
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# 行序（_myportfolio.php:118 的 _echoPortfolio：分块 + _transSortBySymbol）
# ---------------------------------------------------------------------------


def sort_trans_by_symbol(trans_list: Iterable) -> list:
    """``_echoPortfolio()`` 的组合排序（``woody/res/php/_myportfolio.php:118``）。

    顺序由**两段** PHP 代码合出来，本函数一次实现两者：

    1. 调用方 ``_echoPortfolio()`` 先把交易按 ``IsSymbolA`` /
       ``IsSymbolH`` 分进三个数组（``$arTransA`` / ``$arTransH`` /
       ``$arTransUS``，其余进美股），再 ``array_merge`` 拼回去；
    2. 每段内部走 ``_transSortBySymbol()``（同文件 ``:14``）——
       **按符号分组**（同符号的多笔并成一组、组内保持到达顺序），
       ``ksort()`` 按符号字节序排组，再拼平。

    Python 的 ``list.sort`` 是稳定排序，同符号的交易天然保持到达顺序，
    与 PHP"分组 + 拼平"等价，不必真的建组。注意**不是**把
    ``_transSortBySymbol`` 单独搬过来 —— 初版 docstring 把来源归成了那个
    函数，读者去对照时会发现它并不分块（迁移记录里记了这条漂移）。

    顺序决定了 ``static $fCny`` 读到什么：

    * QDII / QDII 混合标的**全是 A 股**，排在美股块之前；
    * ``fx_susdcnh`` 是美股符号，且小写 ``f`` 的字节序在大写之后，
      于是它稳居美股块末尾。

    两者合起来保证"累加完成之后才读"。若哪天有人把美元折算行挪到
    QDII 前面，显示的总美元会少一大截 —— 而且不报错。
    """
    groups: dict = {"a": [], "h": [], "us": []}
    for trans in trans_list or ():
        ref = _invoke(trans, "get_ref", "GetRef")
        symbol = _invoke(trans, "get_symbol", "GetSymbol")
        key = "a" if _invoke(ref, "is_symbol_a", "IsSymbolA") else (
            "h" if _invoke(ref, "is_symbol_h", "IsSymbolH") else "us")
        groups[key].append((symbol, trans))

    ordered = []
    for key in ("a", "h", "us"):
        # PHP 的 ksort() 是字节序：大写在小写之前。Python 的 str 比较同为
        # 码位序，对 ASCII 一致；这里显式按码位排，避免 locale 相关意外。
        groups[key].sort(key=lambda item: _sort_key(item[0]))
        ordered.extend(trans for _, trans in groups[key])
    return ordered


def _sort_key(symbol: Any) -> tuple:
    """按码位逐字符比较，等价于 PHP ``ksort()`` 的默认字节序。"""
    return tuple(ord(ch) for ch in str(symbol))
