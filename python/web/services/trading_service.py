"""五档交易（盘口）表 —— 对应 ``php/ui/tradingparagraph.php``（197 行）。

这是 ``php/ui`` 里**唯一依赖实时五档行情**的段落：``php/_mystock.php``、
``woody/res/php/_qdiigroup.php``、``_qdiimix.php``、``_chinafuture.php`` 都用它，
展示"涨停 / 卖5…卖1 / 买1…买5 / 跌停"共 12 行，每行后面挂若干档**溢价**列。

## 行的顺序与整行底色

.. code-block:: php

    _echoTradingTableItem('orange', '涨停', $fPrice * 1.1, '', ...);   // 橙
    for ($i = 4; $i >= 0; $i--)  卖{$i+1}                              // i==0 时黄
    for ($i = 0; $i <  5; $i++)  买{$i+1}                              // i==0 时黄
    _echoTradingTableItem('orange', '跌停', $fPrice * 0.9, '', ...);   // 橙

底色逻辑在 ``_getTradingColor()`` 里：**只有买一/卖一染黄**（``$i == 0``），
其余不染；涨停/跌停两行染橙。橙/黄是整行 ``background-color``，
与本列常见的文字色是两回事，所以走 ``TableRow.background``。

## 涨停基准价那个三元表达式，看着反其实是对的

.. code-block:: php

    $fPrice = floatval(($ref->IsStockMarketTrading($now_ymd, false) &&
                        ($now_ymd->GetYMD() == $ref->GetDate()))
                       ? $ref->GetPrevPrice() : $ref->GetPrice());

拆开看：

* **交易时段中**用 ``GetPrevPrice()`` —— 新浪字段里它是**昨收**，
  所以"今日涨停 = 昨收 × 1.1"，正确；
* **收市后**用 ``GetPrice()`` —— 那时它是**今日收盘**，
  所以算出来的是"明日涨停 = 今收 × 1.1"，也正确；
* ``GetYMD() == GetDate()`` 是防串档闸门：行情是昨天的数据时
  （抓取出问题、或节假日），不跟着算涨停。

``IsStockMarketTrading($now_ymd, false)`` 的第二参数 ``false`` 是
"不剔除午休"——午休时段也算交易时段。

## ⚠️ 首行扛住了一个 PHP 隐形依赖：``'0'`` 在 PHP 里是**假值**

``_checkTradingQuantity()`` 内部那句 ``if ($strQuantity != '0') return false;``
是**不可达的死代码**：外层 ``if ($strQuantity = $ref->GetAskQuantity($i))``
已经把 ``'0'`` 判为假值滤掉了（PHP 的字符串 ``'0'`` 在布尔上下文里是 ``false``）。

这在 Python 里是个**真陷阱**：``'0'`` 在 Python 是**真值**，照字面翻译会把
"没有挂单"误判成"有挂单"，从而多渲染一张空表。所以本模块一律走
:func:`_php_truthy`，而不是直接用 ``if value:``。

## 列数与格数并不总是相等（照抄，不补空格）

每一行按"定值格子 + 若干档溢价 + 可选回调列"组装，其中回调列的判定是
``empty($strPrice) == false`` —— **价格缺失时这一格不追加**。而表头里的回调列
只看有没有传 callback。所以当"有量无量价"（可从数据源拿到 _item 不到 5 档）
时，该行会比表头**少一格**。

本版照抄这个错位（补空格等于掩盖），并把 ``cell_count`` / ``column_count``
一起返回让调用方自己发现；``RowLengthTest`` 把它钉住。

## 删除了什么：``strlen()*11`` 的像素宽度估算

PHP 在加回调列时算了这么个宽度::

    $iLen = strlen($strNoTag)*11;
    if (!$_SESSION['mobile'])  $iLen = min($iLen, LayoutGetDisplayWidth() - TableColumnGetTotalWidth($arColumn));
    $arColumn[] = new TableColumn($strText, $iLen);

即"一个字符按 11 像素估、再被屏幕宽度截一刀"。**本版直接删掉这条估算**，
改用定宽 :data:`TRADING_CALLBACK_WIDTH`，原因有三：

1. 本项目的表格宽度由 CSS 决定（``web/static/style.css`` 的 ``.data-table``）；
   写在 column 里的 px 只是给浏览器一个 hint，不会被当成硬约束；
2. ``strlen()`` 按**字节**算，中文名一个字 3 字节，估出来必然偏窄三重；
3. 它依赖 ``LayoutGetDisplayWidth()``（屏幕675擅自未迁移的 ``php/layout.php``）
   与 ``$_SESSION['mobile']`` —— 把"有没有体格/session"采进列定义，
   会让同一个数据结构无法给 JSON API 征用。
"""

from __future__ import annotations

from typing import Any, Callable, Sequence

from web.bootstrap import install_paths

install_paths()

from sql.historytable import TableRow  # noqa: E402
from ui.uicolumn import (  # noqa: E402
    STOCK_DISP_FAIR,
    STOCK_DISP_OFFICIAL,
    STOCK_DISP_REALTIME,
    columns_to_dicts,
    column_base,
    column_premium,
    column_price,
    column_quantity,
    column_stock,
    table_column_get_est,
    table_column_get_premium,
    table_column_get_price,
)

from web.services.reference_service import percentage_cell  # noqa: E402

#: PHP ``define('TRADING_QUOTE_NUM', 5)``：五档。
TRADING_QUOTE_NUM = 5

#: ``EchoTableParagraphBegin($arColumn, 'trading', $str)`` 的 ``$strId``。
TRADING_TABLE_ID = "trading"

#: 涨停 / 跌停两行的整行底色（``$strColor = 'orange'``）。
TRADING_COLOR_LIMIT = "orange"
#: 买一 / 卖一一行的整行底色（``_getTradingColor(): ($i == 0) ? 'yellow' : false``）。
TRADING_COLOR_FIRST = "yellow"

#: 第一列表头 ``new TableColumn('交易', 50)``。
TRADING_LABEL_TEXT = "交易"
TRADING_LABEL_WIDTH = 50

#: 回调列的宽度。见模块 docstring：PHP 的 ``strlen()*11`` 估算已删。
TRADING_CALLBACK_WIDTH = 80

#: 涨停 / 跌停的行标签与倍率。
TRADING_LIMIT_UP = "涨停"
TRADING_LIMIT_DOWN = "跌停"
TRADING_LIMIT_UP_RATE = 1.1
TRADING_LIMIT_DOWN_RATE = 0.9

#: 各档行标签的前缀（``'卖'.($i+1)`` / ``'买'.($i+1)``）。
TRADING_SELL_PREFIX = "卖"
TRADING_BUY_PREFIX = "买"


# ---------------------------------------------------------------------------
# 对外：三种入口
# ---------------------------------------------------------------------------
class TradingService:
    """把引用对象整理成五档盘口表。

    ==============================  ==============================
    构造参数                          用途
    ==============================  ==============================
    ``link_builder``                 由符号生成站内 URL（如 ``/fund/<sym>``）。
                                     服务层不认识 ``url_for``，必须注入。
    ``now_provider``                 返回"市场当前时刻"（tz-aware datetime）。
                                     不传就按 ref 自己的时区取 ``datetime.now``。
                                     注入是为了让测试能固定时间。
    ``source``                       数据源，只用于查中文名，可为 ``None``。
    ==============================  ==============================
    """

    def __init__(self, link_builder: Callable[..., Any] = None,
                 now_provider: Callable[[Any], Any] = None,
                 source: Any = None):
        self.link_builder = link_builder
        self.now_provider = now_provider
        self.source = source

    # -- EchoFundTradingParagraph ------------------------------------------
    def fund_table(self, fund: Any, callback: Callable[..., Any] = None,
                   callback_width: int = TRADING_CALLBACK_WIDTH) -> Any:
        """对应 ``EchoFundTradingParagraph($fund, $callback = false)``。

        ``callback`` 同时承担两件事 —— **不传参**时返回列头文本、
        **传价格**时返回该行那一格的文本。这是 PHP 里
        ``call_user_func($callback)`` 与 ``call_user_func($callback, $strPrice)``
        的同构写法。

        ``callback_width`` 见 :meth:`_table`：回调列宽度按内容挑，
        默认那个值是给"短文本"的，QDII 那列要用
        :data:`qdii_trading_column.PAIR_ESTIMATE_WIDTH`。

        溢价列的追加：有官方估值才加"官方溢价"列；在此之下**参考与实时是
        两个并列的分支**（PHP ``tradingparagraph.php:121`` 与 ``:127`` 是
        两个同级 ``if``）—— 只要实时估值有值就加"实时溢价"列，哪怕参考
        估值为空。顺序拷自 PHP。
        """
        ref = _stock_ref_of(fund)
        if ref is None:
            return None

        official = _invoke(fund, "get_official_net_value", "GetOfficialNetValue")
        est_prices: list = []
        est_text = ""
        columns = _base_columns()

        if _php_truthy(official):
            # PHP：官方里嵌**两个并列** if（参考、实时互不依赖，只依赖官方存在）。
            # 曾经误写成嵌套 —— fair 为空时 realtime 连读都不读，演示库
            # SZ161130/SZ161125 实测把「实时溢价」列整列吞掉（2026-10-19，
            # 用户对照生产端发现）。列与 est_prices 在这里同步 append，
            # 保证「官方+实时」（缺参考）时也是两列对两格，不会错位。
            est_prices.append(official)
            columns.append(column_premium(STOCK_DISP_OFFICIAL))
            prev = _floatval(_invoke(ref, "get_prev_price", "GetPrevPrice"))
            est_text = _text(_invoke(ref, "get_price_display", "GetPriceDisplay", official, prev))

            fair = _invoke(fund, "get_fair_net_value", "GetFairNetValue")
            if _php_truthy(fair):
                est_prices.append(fair)
                columns.append(column_premium(STOCK_DISP_FAIR))
                est_text += "|" + _text(
                    _invoke(ref, "get_price_display", "GetPriceDisplay", fair, prev))

            realtime = _invoke(fund, "get_realtime_net_value", "GetRealtimeNetValue")
            if _php_truthy(realtime):
                est_prices.append(realtime)
                columns.append(column_premium(STOCK_DISP_REALTIME))
                est_text += "|" + _text(
                    _invoke(ref, "get_price_display", "GetPriceDisplay", realtime, prev))

        base_title = self._title_of(ref)
        if est_text:
            title = "%s相对于%s%s的%s" % (base_title, table_column_get_est(),
                                        est_text, table_column_get_premium())
        else:
            title = base_title

        return self._table(columns, est_prices, ref, callback=callback, title=title,
                           fund=fund, callback_width=callback_width)

    # -- EchoFundPairTradingParagraph --------------------------------------
    def pair_table(self, ref: Any) -> Any:
        """对应 ``EchoFundPairTradingParagraph($ref)``：相对配对标的的溢价。"""
        pair_ref = _invoke(ref, "get_pair_ref", "GetPairRef")
        pair_symbol = _my_stock_link_text(pair_ref)

        columns = _base_columns()
        columns.append(column_premium(STOCK_DISP_OFFICIAL))

        title = "%s相对于%s的%s" % (self._title_of(ref), pair_symbol, table_column_get_premium())
        official = _invoke(ref, "get_official_net_value", "GetOfficialNetValue")
        return self._table(columns, [official], ref, title=title)

    # -- EchoTradingParagraph ----------------------------------------------
    def get_table(self, ref: Any, ah_ref: Any = None, adr_ref: Any = None) -> Any:
        """对应 ``EchoTradingParagraph($ref, $ah_ref, $adr_ref)``。

        注意这里**没有**加"溢价" 列：列头用的是 ``TableColumnStock``（对照标的名），
        行里对应的是 ``EstFromPair()`` 算出的溢价百分比。即"列名写标的、格子填溢价"。
        """
        columns = _base_columns()
        title = self._title_of(ref)
        est_prices: list = []

        if ah_ref is not None:
            h_ref = _invoke(ah_ref, "get_pair_ref", "GetPairRef")
            h_display = _display_of(h_ref)
            title += "相对于%s港币%s" % (h_display, _text(_invoke_price(h_ref)))
            columns.append(column_stock(h_display))
            est_prices.append(_invoke(ah_ref, "est_from_pair", "EstFromPair"))

            if adr_ref is not None:
                adr_display = _display_of(adr_ref)
                title += "和%s美元%s" % (adr_display, _text(_invoke_price(adr_ref)))
                columns.append(column_stock(adr_display))
                est_prices.append(_invoke(
                    ah_ref, "est_from_pair", "EstFromPair",
                    _invoke(adr_ref, "est_to_pair", "EstToPair")))

            title += "的" + table_column_get_premium()

        return self._table(columns, est_prices, ref, title=title)

    # -- 内部 --------------------------------------------------------------
    def _table(self, columns: list, est_prices: Sequence, ref: Any,
               callback: Callable[..., Any] = None, title: str = "",
               fund: Any = None,
               callback_width: int = TRADING_CALLBACK_WIDTH) -> Any:
        """ ``if (_checkTradingQuantity($ref)) return;`` 之后那段通用渲染流程。

        ``callback_width`` 让调用方能给回调列挑宽度：不同页面的回调列内容长度
        差别很大（``_qdiigroup.php`` 那列要塞 ``^GSPC|SH|SPY价格``），
        统一用默认值会挤成一团。
        """
        if not has_trading_quantity(ref):
            # PHP 在这里整段 return —— 没有挂单量就不摆表（避免一张全空的盘口）
            return None

        if callback is not None:
            columns.append(column_base(_text(callback()), callback_width))

        rows = build_trading_rows(ref, est_prices, callback=callback, now=self._now_of(ref))
        if not rows:
            return None

        symbol = _text(_invoke(ref, "get_symbol", "GetSymbol"))
        return {
            "id": TRADING_TABLE_ID,
            "title": title,
            "columns": columns_to_dicts(columns),
            "rows": rows,
            "count": len(rows),
            "symbol": symbol,
            #: 最长的一行有多少格 —— 用来发现"缺价格时少一格回调列"这类错位
            "cell_count": max(len(row.cells) for row in rows),
            "column_count": len(columns),
            #: PHP 末尾 ``if (in_arrayXopQdii($sym) || …) $str .= ' '.GetRotationTradingLink();``
            "rotation_link": _rotation_link(symbol, self.link_builder),
        }

    def _title_of(self, ref: Any) -> str:
        """对应 ``_getTradingParagraphStr()``：``{符号}当前5档交易价格``。

        PHP 的第一段是 ``GetXueqiuLink($ref)``（带雪球外链的 `<a>`）。本版只给文本 ——
        这一个召集符号 −− 链接由 :attr:`rotation_link` 那种结构化数据承载，
        不在文案里混 HTML。
        """
        symbol = _text(_invoke(ref, "get_symbol", "GetSymbol"))
        return "%s当前%d档交易%s" % (symbol, TRADING_QUOTE_NUM, table_column_get_price())

    def _now_of(self, ref: Any) -> Any:
        """取"这个符号所在市场的当前时刻"。

        对应 PHP ``GetNowYMD()``：它在引用对象构造时已被 ``SetTimeZone()`` 处理过，
        拿到的就是**市场当地时间**。
        """
        if self.now_provider is not None:
            return self.now_provider(ref)
        return market_now(ref)


# ---------------------------------------------------------------------------
# 列
# ---------------------------------------------------------------------------
def _base_columns() -> list:
    """``_getTradingTableColumn()``：交易 / 价格 / 数量三列。"""
    return [
        column_base(TRADING_LABEL_TEXT, TRADING_LABEL_WIDTH),
        column_price(),
        column_quantity(),
    ]


# ---------------------------------------------------------------------------
# 行
# ---------------------------------------------------------------------------
def build_trading_rows(ref: Any, est_prices: Sequence = (), callback: Callable[..., Any] = None,
                       now: Any = None) -> list:
    """对应 ``_echoTradingTableData()``：组装涨停 / 五档 / 跌停共 12 行。

    返回 :class:`~historytable.TableRow` 列表。**不保证每行格数相同** ——
    见模块 docstring 里那处 PHP 错位。

    ``est_prices`` 的顺序必须与附录的表头一致（官方 / 参考 / 实时），
    假值（``None`` / ``False`` / ``0``）表示"没有这一档"，与前面对齐地不产格子。
    """
    est_prices = list(est_prices or ())
    precision = _precision_of(ref)
    base = _limit_base_price(ref, now)

    rows = []

    rows.append(_make_row(ref, est_prices, callback, TRADING_COLOR_LIMIT, TRADING_LIMIT_UP,
                          _limit_text(base * TRADING_LIMIT_UP_RATE, precision), "", precision))

    # for ($i = TRADING_QUOTE_NUM - 1; $i >= 0; $i --)  卖5 … 卖1
    for index in range(TRADING_QUOTE_NUM - 1, -1, -1):
        rows += _maybe_row(ref, est_prices, callback, index, True, precision)

    # for ($i = 0; $i < TRADING_QUOTE_NUM; $i ++)       买1 … 买5
    for index in range(TRADING_QUOTE_NUM):
        rows += _maybe_row(ref, est_prices, callback, index, False, precision)

    rows.append(_make_row(ref, est_prices, callback, TRADING_COLOR_LIMIT, TRADING_LIMIT_DOWN,
                          _limit_text(base * TRADING_LIMIT_DOWN_RATE, precision), "", precision))

    return [row for row in rows if row is not None]


def _maybe_row(ref: Any, est_prices: list, callback: Any, index: int, is_sell: bool,
               precision: int) -> list:
    """一档可能出现也可能不出现，所以返回 ``[row]`` 或 ``[]``。"""
    if is_sell:
        quantity = _invoke(ref, "get_ask_quantity", "GetAskQuantity", index)
        price = _invoke(ref, "get_ask_price", "GetAskPrice", index)
        label = "%s%d" % (TRADING_SELL_PREFIX, index + 1)
    else:
        quantity = _invoke(ref, "get_bid_quantity", "GetBidQuantity", index)
        price = _invoke(ref, "get_bid_price", "GetBidPrice", index)
        label = "%s%d" % (TRADING_BUY_PREFIX, index + 1)

    # PHP: if ($strQuantity = $ref->GetAskQuantity($i))  —— `'0'` 是假值，必须走 PHP 语义
    if not _php_truthy(quantity):
        return []

    row = _make_row(ref, est_prices, callback,
                    TRADING_COLOR_FIRST if index == 0 else "",
                    label, _text(price), _text(quantity), precision)
    return [] if row is None else [row]


def _make_row(ref: Any, est_prices: list, callback: Any, background: str, label: str,
              price: str, quantity: str, precision: int) -> Any:
    """对应 ``_echoTradingTableItem()``：一行。

    ``price`` 是**原始价格字符串**（不是 ``GetPriceDisplay`` 的结果）——
    回调列吃的是它，PHP 的 ``call_user_func($callback, $strPrice)`` 亦然。
    """
    # PHP: if ($strQuantity == '0') return;
    #      注意这里是 == 比较而不是 falsy 判定，所以 '' 不会被滤掉（涨停/跌停行靠这条活着）
    if quantity == TRADING_ZERO_QUANTITY:
        return None

    row = TableRow()
    row.background = background
    row.add(label)

    prev = _floatval(_invoke(ref, "get_prev_price", "GetPrevPrice"))
    row.add(_text(_invoke(ref, "get_price_display", "GetPriceDisplay",
                          _floatval(price), prev, precision)))
    row.add(quantity)

    for est in est_prices:
        if not _php_truthy(est):
            continue
        # PHP 的 GetPercentageDisplay() 是「跌红涨黑」，本版按全站已裁决的
        # 「涨红跌绿」统一（见 README_ui_column_migration.md 第四节）。
        row.add(percentage_cell(_text(_invoke(
            ref, "get_percentage_text", "GetPercentageText", est, _floatval(price)))))

    # PHP: if ($callback && (empty($strPrice) == false))
    if callback is not None and not _php_empty(price):
        row.add(_text(callback(price)))

    return row


#: ``if ($strQuantity == '0') return;`` —— 只有**严格等于** '0' 才跳过整行。
TRADING_ZERO_QUANTITY = "0"


# ---------------------------------------------------------------------------
# 涨停基准价与显示口径
# ---------------------------------------------------------------------------
def _limit_base_price(ref: Any, now: Any = None) -> float:
    """对应 ``_echoTradingTableData()`` 开头那句三元表达式。

    返回一个 float；取不到任何价格时是 0.0（``floatval()`` 语义），
    涨停/跌停那两行会顺势算出 ``0.000``。
    """
    if now is None:
        now = market_now(ref)
    if now is not None:
        try:
            trading = bool(_invoke(ref, "is_stock_market_trading",
                                   "IsStockMarketTrading", now, False))
        except Exception:
            trading = False
        same_day = now.strftime("%Y-%m-%d") == _text(_invoke(ref, "get_date", "GetDate"))
        if trading and same_day:
            return _floatval(_invoke(ref, "get_prev_price", "GetPrevPrice"))
    return _floatval(_invoke(ref, "get_price", "GetPrice"))


def _limit_text(value: float, precision: int) -> str:
    """对应 ``number_format($fPrice * 1.1, $iPrecision, '.', '')``。

    第四参数是空串 —— 即**不加千分位**。``"%.{p}f"`` 就是这个语义。
    """
    return "%.*f" % (precision, value)


def market_now(ref: Any) -> Any:
    """按符号所在时区取"现在"（对应 PHP ``GetNowYMD()``）。

    时区由 ref 自己决定（``GetTimeZone()``），取不到就用 UTC —— 宁可用错时区
    也不要让整张表因为一次异常消失。
    """
    import datetime

    name = _text(_invoke(ref, "get_time_zone", "GetTimeZone"))
    try:
        from core import tzutil

        zone = tzutil.get_time_zone(name)
    except Exception:
        return datetime.datetime.now(datetime.timezone.utc)
    return datetime.datetime.now(zone)


def has_trading_quantity(ref: Any) -> bool:
    """对应 ``_checkTradingQuantity()``：**有**可显示的挂单量时返回 ``True``。

    PHP 的函数名与返回值是**反的**（它在"全是空"时返回 ``true`` 表示"可以跳过"），
    这里按"能不能渲染"来命名，避免调用方的 ``if not check():`` 绕两道弯。

    顺带一提：PHP 内层那句 ``if ($strQuantity != '0') return false;``
    永远不会被命中 —— 见模块 docstring 里关于 PHP 假值的说明。
    """
    for index in range(TRADING_QUOTE_NUM):
        for method in (("get_ask_quantity", "GetAskQuantity"),
                       ("get_bid_quantity", "GetBidQuantity")):
            quantity = _invoke(ref, method[0], method[1], index)
            if _php_truthy(quantity) and quantity != TRADING_ZERO_QUANTITY:
                return True
    return False


def _precision_of(ref: Any) -> int:
    """``GetPrecision()``，取不到时退回 3（PHP 的默认小数位）。"""
    value = _invoke(ref, "get_precision", "GetPrecision")
    try:
        return int(value)
    except (TypeError, ValueError):
        return 3


# ---------------------------------------------------------------------------
# 链接（结构化，服务层不产 HTML）
# ---------------------------------------------------------------------------
def _rotation_link(symbol: str, link_builder: Callable[..., Any]) -> Any:
    """对应 ``if (in_arrayXopQdii($strSymbol) || …) $str .= ' '.GetRotationTradingLink()``。

    PHP 直接拼了一个 ``<a>`` 塞进说明文字里。本版返回
    ``{"text", "url"}``；``link_builder`` 没给、或该符号不属于轮动分组时返回 ``None``。
    """
    if not symbol or link_builder is None:
        return None
    from core.stocksymbol import (
        in_array_qqq_match,
        in_array_spy_qdii,
        in_array_xbi_qdii,
        in_array_xop_qdii,
    )

    matched = False
    for checker in (in_array_xop_qdii, in_array_xbi_qdii, in_array_spy_qdii, in_array_qqq_match):
        try:
            if checker(symbol):
                matched = True
                break
        except Exception:
            continue
    if not matched:
        return None
    url = link_builder(symbol, "rotationtrading")
    if url is None:
        return None
    return {"text": "轮动交易", "url": url}


# ---------------------------------------------------------------------------
# PHP 语义辅助
# ---------------------------------------------------------------------------
def _php_truthy(value: Any) -> bool:
    """PHP 语境下的"真值"。

    与 Python 唯一的区别是**字符串 ``'0'``**：PHP 判假、Python 判真。
    行情里"没有挂单"常写成 ``'0'``，照字面翻译会多渲染一张空表，
    所以整个 php/ui 迁移都要走这里而不是 ``if value:``。
    """
    if isinstance(value, str):
        return value not in ("", "0")
    return bool(value)


def _php_empty(value: Any) -> bool:
    """PHP 的 ``empty()``：``''`` 与 ``'0'`` 都算空，``None`` 也算。"""
    return not _php_truthy(value)


def _floatval(value: Any) -> float:
    """``floatval()`` 语义。``None`` 也是 0.0（``floatval(null) === 0.0``）。"""
    from ref.stockref import _php_floatval

    return _php_floatval(value)


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _display_of(ref: Any) -> str:
    """``GetDisplay()``：冷文本，取不到退回符号文本。"""
    display = _invoke(ref, "get_display", "GetDisplay")
    if display:
        return _text(display)
    return _text(_invoke(ref, "get_symbol", "GetSymbol"))


def _invoke_price(ref: Any) -> Any:
    """``GetPriceDisplay()``（不带参数）—— PHP 标题里那句 ``$h_ref->GetPriceDisplay()``。

    不带参数就是"用 ref 自己的现价/昨收"，所以它给的是**带相对涨跌语义的显示文本**
    而不是裸价格。
    """
    return _invoke(ref, "get_price_display", "GetPriceDisplay")


def _my_stock_link_text(ref: Any) -> str:
    """对应 ``RefGetMyStockLink($ref->GetPairRef())`` 的**文本部分**。

    PHP 拼进句子的是带 ``<a>`` 的 HTML；本版只要文字 —— 否则这条说明会被
    Jinja 的自动转义原样打在页面上，变成一串 ``&lt;a href=…&gt;``。

    **``strip_tags`` 不是这里发明的**：同一个 ``tradingparagraph.php`` 在算回调列
    宽度时就写了 ``$strNoTag = strip_tags($strText);``，作者自己也不想在文案里混标签。
    宽度估算虽然删了（见模块 docstring），"先从链接里剥出纯文本"这个动作留着。
    """
    if ref is None:
        return ""
    for snake, camel in (("get_my_stock_link", "GetMyStockLink"),
                         ("get_display", "GetDisplay"),
                         ("get_symbol", "GetSymbol")):
        value = _invoke(ref, snake, camel)
        if value:
            return strip_tags(value)
    return ""


def strip_tags(text: Any) -> str:
    """对应 PHP ``strip_tags()``：去掉 ``<…>`` 标记，只留文字。

    只处理"标签"这一种情况，不解析实体（``&amp;`` 之类留给模板去转义）。
    PHP 的 ``strip_tags`` 也不还原实体。
    """
    import re

    return re.sub(r"<[^>]*>", "", "" if text is None else str(text))


def _stock_ref_of(fund: Any) -> Any:
    """``GetStockRef($fund)``：基金的场内行情引用对象；取不到就用 fund 自己。"""
    if fund is None:
        return None
    ref = _invoke(fund, "get_stock_ref", "GetStockRef")
    return fund if ref is None else ref


def _invoke(ref: Any, snake: str, camel: str, *args: Any) -> Any:
    """按 snake_case 优先、camelCase 兜底调用；取不到返回 ``None``。"""
    if ref is None:
        return None
    method = getattr(ref, snake, None)
    if method is None:
        method = getattr(ref, camel, None)
    if method is None or not callable(method):
        return None
    try:
        return method(*args)
    except Exception:
        return None


def make_rotation_link_builder(url_for_func: Callable[..., Any]) -> Callable[..., Any]:
    """造一个"轮动交易页"链接构造器（``(符号, 页面名) -> URL``）。

    **为什么不写在 :func:`default_trading_service_factory` 里**：生 URL 需要
    ``url_for`` 与请求上下文，而服务层要能在没有请求的地方跑（脚本导出、单测）。
    所以由路由层在拿到服务之后补上这一层，与 ``routes/stock.py::_table_link``
    同一个分工。

    ``kind`` 不为 ``rotationtrading`` 时返回 ``None`` —— 本构造器只服务那一处。
    """
    def build(symbol: str, kind: str) -> Any:
        if kind != "rotationtrading" or not symbol:
            return None
        try:
            return url_for_func("stock.stock_rotation_trading_page", symbol=symbol)
        except Exception:
            return None

    return build


def default_trading_service_factory() -> TradingService:
    """创建一个不带链接构造器的服务。

    ``link_builder`` 留空，于是 :func:`_rotation_link` 恒返回 ``None``。这不是
    忘了接 —— URL 要用 ``url_for``，只有请求上下文里才算得出来。路由层拿到
    服务后会用 :func:`make_rotation_link_builder` 补上（见
    ``routes/stock.py::_get_trading_service``）。
    """
    return TradingService()


# ---------------------------------------------------------------------------
# PHP 原名别名（见 AGENTS.md：保留 camelCase 别名）
# ---------------------------------------------------------------------------
EchoFundTradingParagraph = TradingService.fund_table
EchoFundPairTradingParagraph = TradingService.pair_table
EchoTradingParagraph = TradingService.get_table
