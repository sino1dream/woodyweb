"""轮动交易页 —— 对应 ``woody/res/rotationtradingcn.php``（150 行）。

## 这一页回答什么问题

"我持有一批某指数的 QDII 基金，现在想换成**同一指数的另一只**，该挂多少、
能不能成交、换完划不划算。"

同一个指数在国内往往有十来只 QDII 跟踪（纳指 100 有 12 只、标普 500 有 4 只），
它们的折溢价率差得很远 —— 生产端实测 ``SH513100`` 溢价 18.99% 而 ``SH513110``
只有 13.68%。卖掉高溢价那只、买进低溢价那只，敞口不变、差价落袋。本页就是把
这个差价换算成**可以直接下单的股数**。

## 计算公式（``_echoRotationTradingItem``）

.. code-block:: php

    $fEstQuantity = $fInput / GetStockHedge($strSymbol, $ref->GetStockId());
    $fHedge = GetStockHedge($strSymbol, $stock_ref->GetStockId());   // 每行的
    $fHintQuantity = abs(round($fHedge * $fEstQuantity / 100.0) * 100.0);

三步：

1. **轮动数量 → 对冲标的口径**：``fInput / hedge(本基金)``。``hedge`` 是
   ``GetStockHedge()``（``php/stock.php:401``）给的换算比 —— 有 QQQ/SPY 杠杆配对时
   取**最近一个"两边同日都有校准值"的日期**上 ``(cal/pos)`` 之比，没有配对时
   用当日校准 ÷ 仓位，两边都取不到就 **1.0**；
2. **换成每只候选的量**：``× hedge(候选)`` —— 这样每只买/卖出去的对冲敞口相同；
3. **凑整到 100 股**：``round(x / 100) * 100``；``abs()`` 是因为第 1 步的符号
   （卖出为负）已经由"方向"表达，数量本身只关心大小。

建议数量超过该档可成交数量时，PHP 用 ``GetFontElement($strHintQuantity)``
把它染成**红色**（默认色参 ``'red'``）—— 意思是"这一行做不完"。本版用
``TableRow`` 的格子颜色表达同一件事。

## 方向是反的，注意别抄错

.. code-block:: php

    $bSell = ($fInput < 0.0);                 // 本基金：负数 = 卖
    $bRotationSell = $bSell ? false : true;   // 候选：**反过来**

所以"轮动数量"填负数 → 卖本基金、**买**候选（表头前缀"可买"）；填正数则相反。

没填（或点"清除"）时 ``$fInput = -1000000.0`` —— 一个必然超过任何盘口量的
占位值，让首屏先按"卖 100 万"算一版，方便估量级。

## 四族之外整页空白

``_getRotationSymbolArray()`` 按固定顺序判四个数组：XOP → XBI → 纳指匹配 →
标普匹配。不属于任何一族时 ``EchoAll()`` 的那个 ``if`` 整块跳过，**连分组行
都没有**，只剩标题和页脚链接。

## 一处刻意偏离：标普那一族放宽到 ``SpyQdii``

PHP 详情页的入口链接（``php/ui/tradingparagraph.php:151``）判的是
``in_arraySpyQdii()``（**5 只**，含 ``SZ159655``），本页却判
``in_arraySpyMatch()``（**4 只**，不含）。于是 ``SZ159655`` 有链接点进来、
页面全空 —— 生产端实测确实如此。

本版把**页面这一侧**放宽到 ``SpyQdii``（用户 2026-10-31 的要求），入口不动。
候选列表仍是那 4 只：``SZ159655`` 自己不在匹配数组里，所以四只全列。
两边从此对齐，且没有收窄入口、不影响任何原有可用的标的。

## 别的偏离

1. **表 id** 用 ``<symbol>rotationtrading``。PHP 是 ``$acct->GetPage().'table'``
   （页面名，所有符号共用一个 id）；
2. ``EchoTableParagraphBegin()`` 里"表格超过屏幕宽度就整张不输出"
   （``table.php:99``，靠 ``LayoutGetDisplayWidth()`` 与 ``$_SESSION['mobile']``）
   未迁 —— 列宽在本地由 CSS 决定；
3. 页头的"分组名称代码"行（``EchoStockGroup()``）不做：本地详情页的同类信息
   在 ``/stock/<symbol>`` 上，这里重复一遍没有增量；
4. ``GetNumberDisplay()`` / ``GetPercentageDisplay()`` 的 ``<font>`` 标签换成
   ``TableRow`` 的格子颜色（``cell-red``），模板负责上色；
5. 对冲值取到 0 时不做除法（PHP 8 会抛 ``DivisionByZeroError`` 整页 500）——
   这一版按"算不出"处理，页面出说明但不给表。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from web.bootstrap import install_paths

install_paths()

from core.stockdata import get_leverage_hedge_symbol  # noqa: E402
from core.stocksymbol import (  # noqa: E402
    qdii_get_spy_match_array,
    qdii_get_xbi_symbol_array,
    qdii_get_xop_symbol_array,
    qdii_get_qqq_match_array,
)
from sql.historytable import TableRow, format_price_display  # noqa: E402
from ui.uicolumn import (  # noqa: E402
    columns_to_dicts,
    column_hedge,
    column_premium,
    column_price,
    column_quantity,
    column_symbol,
)

#: ``ROTATION_TRADING_DISPLAY``（``php/stocklink.php:249``）。
ROTATION_TRADING_DISPLAY = "轮动交易"

#: 表 id 后缀（PHP 是 ``GetPage()``，所有符号共用一个 id；这里带上符号以便唯一）。
ROTATION_TABLE_ID_SUFFIX = "rotationtrading"

#: 没填数量时的占位值（``rotationtradingcn.php:90``）。
DEFAULT_ROTATION_INPUT = -1000000.0

#: 建议数量凑整的股数粒度（``round(x / 100) * 100``）。
QUANTITY_LOT = 100.0


# ---------------------------------------------------------------------------
# 族分派
# ---------------------------------------------------------------------------
def rotation_symbol_array(symbol: str) -> Optional[List[str]]:
    """``_getRotationSymbolArray()``（``rotationtradingcn.php:61``）。

    四个分支**顺序固定**：XOP → XBI → 纳指匹配 → 标普匹配，命中即返回。

    **标普那一支用 ``in_array_spy_qdii`` 而不是 ``in_array_spy_match``**，
    见模块 docstring 的"刻意偏离"。返回 ``None`` 表示这个符号不属于任何一族，
    调用方据此整段跳过（PHP 里那个 ``if`` 为假）。
    """
    if not symbol:
        return None

    from core.stocksymbol import in_array_spy_qdii, in_array_xbi_qdii, in_array_xop_qdii, in_array_qqq_match

    try:
        if in_array_xop_qdii(symbol):
            return list(qdii_get_xop_symbol_array())
        if in_array_xbi_qdii(symbol):
            return list(qdii_get_xbi_symbol_array())
        if in_array_qqq_match(symbol):
            return list(qdii_get_qqq_match_array())
        if in_array_spy_qdii(symbol):
            return list(qdii_get_spy_match_array())
    except Exception:
        return None
    return None


def rotation_peers(symbol: str, rotation_symbols: Sequence[str]) -> List[str]:
    """候选清单：去掉自己（``rotationtradingcn.php:82`` 那个 ``if``）。"""
    return [item for item in rotation_symbols if item != symbol]


# ---------------------------------------------------------------------------
# PHP 语义小工具
# ---------------------------------------------------------------------------
def _php_truthy(value: Any) -> bool:
    """PHP 的布尔转换：``''`` / ``'0'`` / ``0`` / ``None`` 都是假。"""
    if value is None or value is False:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value)
    return text not in ("", "0")


def _floatval(value: Any) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return 0.0


def _call(obj: Any, snake: str, camel: str, *args: Any) -> Any:
    """先 snake 后 camel 取方法（引用对象两种命名并存）。"""
    method = getattr(obj, snake, None)
    if method is None:
        method = getattr(obj, camel, None)
    if method is None or not callable(method):
        return None
    try:
        return method(*args)
    except Exception:
        return None


def stock_ref_of(ref: Any) -> Any:
    """``GetStockRef($fund_ref)``（``php/stock.php:323``）。

    PHP 那句是 ``method_exists($fund_ref, 'GetStockRef') ? ... : $fund_ref``：
    基金对象上挂着一份**场内行情引用**，盘口与价格都在那一份上；没有这一层的
    （``^NDX`` 这类）就用自己。

    与 ``trading_service._stock_ref_of`` 同义 —— 那边是段落内部用的私有版本，
    这里要跨模块用，所以单独给一个公开名。
    """
    inner = getattr(ref, "stock_ref", None)
    return inner if inner is not None else ref


def number_display(value: Any, precision: int = 0) -> Any:
    """``GetNumberDisplay($fVal, 0)``（``php/stock.php:111``）→ ``(文本, 颜色)``。

    它是 ``StockGetPriceDisplay($fVal, 0.0, $iPrecision, 'black', 'red')`` ——
    注意首尾色与默认值相反：**大于容差是 black（不染色）、小于负容差才是红**。
    ``format_price_display()`` 返回的是"涨红跌绿"，颜色要反过来映射：
    ``red → ''``、``green → 'red'``、``grey`` 原样。
    """
    text, color = format_price_display(value, 0.0, precision)
    if color == "red":
        return (text, "")
    if color == "green":
        return (text, "red")
    return (text, color)


def hint_quantity(hedge: Any, est_quantity: Any) -> float:
    """``abs(round($fHedge * $fEstQuantity / 100.0) * 100.0)``。

    单独拆出来是为了能直接测那两层取整 —— 用**未取整的** hedge 参与计算
    （页面上显示的"对冲值"是取整后的，拿它反推会差几百股）。
    """
    from sql.historytable import php_round

    product = _floatval(hedge) * _floatval(est_quantity)
    return abs(php_round(product / QUANTITY_LOT, 0) * QUANTITY_LOT)


def _quantity_text(value: float) -> str:
    """``strval(float)`` 对整百数量：PHP 给 ``'871000'``（不带 ``.0``）。"""
    return "%.0f" % value


# ---------------------------------------------------------------------------
# 表结构
# ---------------------------------------------------------------------------
def _columns(rotate_prefix: str, hedge_symbol: str) -> List[Dict[str, Any]]:
    """七列表头（``rotationtradingcn.php:44-50``）。

    ``rotate_prefix`` 是候选方向的前缀（"可买" / "可卖"），
    ``hedge_symbol`` 是第六列的前缀（"QQQ" / "SPY" / 中间层符号）。
    """
    return columns_to_dicts(
        [
            column_symbol(),
            column_price(rotate_prefix),
            column_quantity(rotate_prefix),
            column_quantity("建议"),
            column_hedge(),
            column_quantity(hedge_symbol),
            column_premium(),
        ]
    )


# ---------------------------------------------------------------------------
# 单行
# ---------------------------------------------------------------------------
def build_row(
    ref: Any,
    is_rotate_sell: bool,
    est_quantity: float,
    hedge_provider: Any,
    link_builder: Any = None,
) -> Optional[TableRow]:
    """``_echoRotationTradingItem()`` 的等价物；返回 ``None`` 表示这一格都不出。

    PHP 的结构是"先把代码那一格压进去，再看有没有可成交数量"：

    .. code-block:: php

        $ar[] = CopyPhpLink('symbol='.$strSymbol, $strSymbol);
        if ($strQuantity = $stock_ref->GetAvailableQuantity($bRotationSell))
        { ... 后面六格 ... }

    取不到数量（该档没有挂单）时，这一行**只有代码一格** —— 变长行，
    表头七列、这一行一格。照抄，不补空格。
    """
    if ref is None:
        return None

    stock_ref = stock_ref_of(ref)
    symbol = str(_call(stock_ref, "get_symbol", "GetSymbol") or "")
    if not symbol:
        return None

    row = TableRow()
    row.add(symbol)
    if link_builder is not None:
        try:
            row.href = link_builder(symbol) or ""
        except Exception:
            row.href = ""

    quantity = _call(stock_ref, "get_available_quantity", "GetAvailableQuantity", is_rotate_sell)
    if not _php_truthy(quantity):
        return row

    price = _call(stock_ref, "get_available_price", "GetAvailablePrice", is_rotate_sell)
    price_text = _call(stock_ref, "get_price_display", "GetPriceDisplay", _floatval(price))
    row.add("" if price_text is None else str(price_text))
    row.add(str(quantity))

    hedge = hedge_provider(symbol, stock_ref) if callable(hedge_provider) else 1.0
    hint = hint_quantity(hedge, est_quantity)
    hint_text = _quantity_text(hint)
    #: 装不进这一档的量 → 红字（PHP ``GetFontElement($strHintQuantity)``）。
    over = hint > _floatval(quantity)
    row.add((hint_text, "red") if over else hint_text)

    row.add(number_display(hedge, 0))
    row.add(number_display(est_quantity, 0))

    est = _call(ref, "get_est_net_value", "GetEstNetValue")
    if _php_truthy(est):
        premium = _call(stock_ref, "get_percentage_text", "GetPercentageText", est, _floatval(price))
        if premium:
            row.add(str(premium))
    return row


# ---------------------------------------------------------------------------
# 整页
# ---------------------------------------------------------------------------
def build_rotation_page(
    symbol: str,
    rotation_symbols: Optional[Sequence[str]] = None,
    fund_ref: Any = None,
    source: Any = None,
    rotation_refs: Sequence[Any] = (),
    input_text: Any = None,
    link_builder: Any = None,
) -> Dict[str, Any]:
    """装配整页。

    ==================  ==================================================
    返回键               含义
    ==================  ==================================================
    ``available``       ``False`` = 不属于四族，模板只出标题（PHP 整块跳过）
    ``direction``       ``{"is_sell","op","rotate_op","prefix"}``
    ``input``           输入框回显（PHP ``strval($fInput)``）
    ``summary``         说明行零件；本基金没有档位量时 **``None``**
    ``hedge_symbol``    第六列表头前缀
    ``table``           表格；``None`` = 连表头都不出
    ==================  ==================================================

    ``rotation_refs`` 由调用方建好（要连库，服务层不负责建对象）；条数与
    ``rotation_symbols`` 去掉自己之后**必须一致**，顺序即行序。
    """
    page: Dict[str, Any] = {
        "symbol": symbol,
        "title": symbol + ROTATION_TRADING_DISPLAY,
        "available": rotation_symbols is not None,
        "input": "",
        "direction": None,
        "summary": None,
        "hedge_symbol": "",
        "table": None,
    }
    if rotation_symbols is None:
        return page

    #: ``$fInput``：``GetEditInput()`` 为假（含 '' 与 '0'）时回落到 -1000000。
    raw = "" if input_text is None else str(input_text)
    f_input = _floatval(raw) if _php_truthy(raw) else DEFAULT_ROTATION_INPUT
    is_sell = f_input < 0.0
    page["input"] = _quantity_text(f_input)
    page["direction"] = {
        "is_sell": is_sell,
        "op": "卖" if is_sell else "买",
        "rotate_op": "买" if is_sell else "卖",
        "prefix": "可" + ("买" if is_sell else "卖"),
    }

    if fund_ref is None:
        return page

    stock_ref = stock_ref_of(fund_ref)
    stock_id = _call(stock_ref, "get_stock_id", "GetStockId")

    #: 第六列表头：有 QQQ/SPY 杠杆配对就用配对符号，否则用中间层（估值标的）
    #: 的符号（``rotationtradingcn.php:95-100``）。
    hedge_symbol = get_leverage_hedge_symbol(symbol)
    if not hedge_symbol:
        est_ref = _call(fund_ref, "get_est_ref", "GetEstRef")
        hedge_symbol = str(_call(est_ref, "get_symbol", "GetSymbol") or "") if est_ref else ""
    page["hedge_symbol"] = hedge_symbol

    #: 闸门：本基金这一档没有挂单 → 说明行与表都不出（PHP 那个 if 包住了全部）。
    quantity = _call(stock_ref, "get_available_quantity", "GetAvailableQuantity", is_sell)
    if not _php_truthy(quantity):
        return page

    price = _call(stock_ref, "get_available_price", "GetAvailablePrice", is_sell)
    page["summary"] = {
        "link": _external_link(stock_ref),
        "price": "" if price is None else str(price),
        "premium": _fund_premium(fund_ref, stock_ref, price),
        "quantity": str(quantity),
    }

    hedge_self = _hedge_of(source, symbol, stock_id)
    if not hedge_self:
        #: 偏离 5：PHP 8 在这里会 DivisionByZeroError 整页 500。
        page["summary"]["error"] = "对冲值为 0，算不出轮动数量。"
        return page

    est_quantity = f_input / hedge_self

    def _hedge_provider(candidate_symbol: str, candidate_ref: Any) -> float:
        candidate_id = _call(candidate_ref, "get_stock_id", "GetStockId")
        return _hedge_of(source, candidate_symbol, candidate_id)

    rows = []
    for ref in rotation_refs:
        row = build_row(ref, not is_sell, est_quantity, _hedge_provider, link_builder)
        if row is not None:
            rows.append(row)

    page["table"] = {
        "id": "%s%s" % (symbol, ROTATION_TABLE_ID_SUFFIX),
        "title": ROTATION_TRADING_DISPLAY,
        "columns": _columns(page["direction"]["prefix"], hedge_symbol),
        "rows": rows,
        "count": len(rows),
    }
    return page


def _hedge_of(source: Any, symbol: str, stock_id: Any) -> float:
    """``GetStockHedge($strSymbol, $strStockId)``（``php/stock.php:401``）。

    数据源上没有这个方法（单测的假 source）时返回 ``1.0`` —— 与 PHP
    "两边都没有校准值时给 1.0" 同一个兜底。
    """
    if source is None or not symbol:
        return 1.0
    getter = getattr(source, "get_stock_hedge", None)
    if getter is None:
        return 1.0
    try:
        value = getter(symbol, stock_id)
    except Exception:
        return 1.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 1.0


def _fund_premium(fund_ref: Any, stock_ref: Any, price: Any) -> str:
    """说明行里的"溢价"：``$ref->GetPercentageDisplay($fEst, floatval($strPrice))``。

    取不到估值就**整段不出现**（PHP 是 ``if ($fEst = ...)`` 包住的），
    所以返回空串而不是 "0%"。
    """
    est = _call(fund_ref, "get_est_net_value", "GetEstNetValue")
    if not _php_truthy(est):
        return ""
    text = _call(stock_ref, "get_percentage_text", "GetPercentageText", est, _floatval(price))
    return "" if text is None else str(text)


def _external_link(stock_ref: Any) -> Optional[Dict[str, str]]:
    """说明行开头的那个外链。

    PHP 按板块三派（``rotationtradingcn.php:106-109``）：沪市 ETF → 上交所申赎
    清单、深市 ETF → 深交所、其余 → 雪球。本版复用引用对象上已有的
    ``external_link``（``MysqlReference._build_external_link`` 建的那份，
    与参考数据表首格同一出处），取不到就不给链接。
    """
    link = _call(stock_ref, "get_external_link", "GetExternalLink")
    if not link:
        return None
    symbol = str(_call(stock_ref, "get_symbol", "GetSymbol") or "")
    return {"text": symbol or str(link), "url": str(link)}
