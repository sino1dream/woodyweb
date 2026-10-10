"""外部链接：参考数据表首格那个指向新浪的外链。

## 范围说明

workbuddy 的第 4 步**刻意不迁** ``php/ui/htmlelement.php``（整份都是拼 HTML
片段的函数）。但参考数据表的**首格**恒为 ``GetExternalLink()`` 的产物，
少了它整行就少一格、列会错位（实测踩到：SZ161130 首格空白，
价格跑到代码列的位置）。

所以这里只补**表里真正用到的那一个**，不做整个模块：

===================================  =======================================
PHP                                   本模块
===================================  =======================================
``GetLinkElement()``                    :func:`get_link_element`
``GetExternalLink()``                   :func:`get_external_link`
``GetSinaFundLink()``                   :func:`get_sina_fund_link`
===================================  =======================================

其余外链（雪球、东财、CME……）仍属未迁范围，用到时再加 ——
**不要**为了"顺手"把 ``htmlelement.php`` 整份搬过来。

## 与 PHP 的逐字对齐

``GetExternalLink($strHttp, $strDisplay)``::

    return GetLinkElement(($strDisplay ? $strDisplay : $strHttp), $strHttp,
                          array('target' => '_blank'));

即 ``<a href="URL" target="_blank">显示名</a>``，属性顺序 ``href`` 在前。
"""

from __future__ import annotations

from typing import Any

#: 新浪财经站点的基地址。对应 PHP ``GetSinaFinanceUrl()``。
SINA_FINANCE_URL = "https://finance.sina.com.cn"

#: 新浪证券（美股/港股行情页）的基地址。对应 PHP ``GetSinaStockUrl()``。
SINA_STOCK_URL = "https://stock.finance.sina.com.cn"

#: 新浪全球指数页。对应 PHP ``GetSinaStockLink()`` 里那条 ``quotes.sina.cn``。
SINA_GLOBAL_INDEX_URL = "https://quotes.sina.cn/global/hq/quotes.php?code="


def get_link_element(content: Any, path_name: Any, extra: Any = None) -> str:
    """对应 ``GetLinkElement()``：拼一个 ``<a>``。

    ``extra`` 是属性字典（PHP 是关联数组），也可以传 ``None``。
    属性之间用空格分隔，值用双引号包住 —— 与 ``GetHtmlElement()`` 一致。
    """
    text = "" if content is None else str(content)
    url = "" if path_name is None else str(path_name)
    attributes = ["href=\"%s\"" % url]
    if extra:
        for key, value in extra.items():
            attributes.append("%s=\"%s\"" % (key, value))
    return "<a %s>%s</a>" % (" ".join(attributes), text)


def get_external_link(http: Any, display: Any = None) -> str:
    """对应 ``GetExternalLink()``：显示名缺省时用 URL 本身。"""
    text = display if display else http
    return get_link_element(text, http, {"target": "_blank"})


def get_sina_fund_link(symbol: Any) -> str:
    """对应 ``GetSinaFundLink()``：中国场内基金的行情页外链。

    PHP 取的是 ``IsFundA()``，取不到再退 ``IsSinaFund()``::

        $strDigit = $sym->IsFundA();
        if ($strDigit == false)  $strDigit = $sym->IsSinaFund();
        if ($strDigit)
            return GetExternalLink(SINA."/fund/quotes/$strDigit/bc.shtml", $strSymbol);
        return $strSymbol;      // 没有外链时退回纯符号文本

    **注意"取不到外链时退回符号文本"这个分支**：它保证首格**永远有内容**，
    表格列数不会错位。本函数照抄。
    """
    from stocksymbol import StockSymbol

    sym = symbol if hasattr(symbol, "get_symbol") else StockSymbol(str(symbol))
    digits = None
    getter = getattr(sym, "is_fund_a", None)
    if callable(getter):
        try:
            digits = getter()
        except Exception:
            digits = None
    if not digits:
        getter = getattr(sym, "is_sina_fund", None)
        if callable(getter):
            try:
                digits = getter()
            except Exception:
                digits = None

    text = ""
    symbol_getter = getattr(sym, "get_symbol", None)
    if callable(symbol_getter):
        try:
            text = symbol_getter() or ""
        except Exception:
            text = ""
    if not text:
        text = str(symbol)

    if not digits:
        return text
    url = "%s/fund/quotes/%s/bc.shtml" % (SINA_FINANCE_URL, digits)
    return get_external_link(url, text)


def _symbol_object(symbol: Any) -> Any:
    """统一拿到 ``StockSymbol`` 对象（允许直接传对象）。"""
    from stocksymbol import StockSymbol

    return symbol if hasattr(symbol, "get_symbol") else StockSymbol(str(symbol))


def _display_of(sym: Any) -> str:
    """取展示名（没有就退回符号本身）。"""
    getter = getattr(sym, "get_display", None)
    if callable(getter):
        try:
            value = getter()
        except Exception:
            value = None
        if value:
            return str(value)
    getter = getattr(sym, "get_symbol", None)
    if callable(getter):
        try:
            return str(getter() or "")
        except Exception:
            return ""
    return str(sym)


def _call(sym: Any, name: str) -> Any:
    """调符号对象上的方法；没有或抛异常返回 ``None``。"""
    getter = getattr(sym, name, None)
    if not callable(getter):
        return None
    try:
        return getter()
    except Exception:
        return None


def get_sina_us_stock_link(symbol: Any) -> str:
    """对应 ``GetSinaUsStockLink()``：美股/美股指数行情页。

    PHP::

        if ($sym->IsIndex())  $str = '.'.strtoupper($sym->GetSinaIndexUS());
        else                  $str = $strSymbol;

    所以 ``^NDX`` → ``.NDX``、``QQQ`` → ``QQQ``。
    """
    sym = _symbol_object(symbol)
    text = _display_of(sym)
    if _call(sym, "is_index"):
        index_us = _call(sym, "get_sina_index_us")
        if index_us:
            return get_external_link(
                "%s/usstock/quotes/.%s.html" % (SINA_STOCK_URL, str(index_us).upper()),
                text)
    return get_external_link("%s/usstock/quotes/%s.html" % (SINA_STOCK_URL, text), text)


def get_sina_future_link(symbol: Any) -> str:
    """对应 ``GetSinaFutureLink()``：外盘期货页（``hf_NQ`` → ``NQ``）。"""
    sym = _symbol_object(symbol)
    text = _display_of(sym)
    return get_external_link(
        "%s/futures/quotes/%s.shtml" % (SINA_FINANCE_URL, text), text)


def get_sina_forex_link(symbol: Any) -> str:
    """对应 ``GetSinaForexLink()``：外汇页（``USDCNY`` 原样用）。"""
    sym = _symbol_object(symbol)
    text = _display_of(sym)
    return get_external_link(
        "%s/money/forex/hq/%s.shtml" % (SINA_FINANCE_URL, text), text)


def get_forex_middle_link(symbol: Any) -> str:
    """``USCNY`` 这类**中间价**的外链：新浪没有它的行情页，指向中国货币网。

    生产端 ``sz161130cn.php`` 里 ``USCNY`` 那一行的 href 就是
    ``https://www.chinamoney.com.cn``（见页面 HTML），所以照抄。
    """
    sym = _symbol_object(symbol)
    return get_external_link("https://www.chinamoney.com.cn", _display_of(sym))


def get_sina_stock_link(symbol: Any) -> str:
    """对应 ``GetSinaStockLink()``：按符号类型分派到具体外链。

    PHP 的分派顺序（``php/externallink.php:249``）::

        IsSinaGlobalIndex -> quotes.sina.cn/global
        IsSymbolA         -> IsFundA ? GetSinaFundLink : GetSinaCnStockLink
        IsSymbolH         -> GetSinaHkStockLink
        其他              -> GetSinaUsStockLink

    本版**多两条**，因为参考数据表会放它们，而 PHP 那几行不走这个函数：

    * **期货**（``hf_NQ``）→ :func:`get_sina_future_link`；
    * **外汇**（``USDCNY``）→ :func:`get_sina_forex_link`；
    * **中间价**（``USCNY``）→ :func:`get_forex_middle_link`。

    取不到时退回纯符号文本（首格必须有内容，否则整行少一格、列会错位）。
    """
    sym = _symbol_object(symbol)
    text = _display_of(sym)

    global_index = _call(sym, "is_sina_global_index")
    if global_index:
        return get_external_link(SINA_GLOBAL_INDEX_URL + str(global_index), str(global_index))

    if _call(sym, "is_sina_future"):
        return get_sina_future_link(sym)

    # 外汇两类要分开（实测）：
    #   ``USCNY`` / ``HKCNY`` / ``EUCNY`` / ``JPCNY`` —— ``is_forex()`` 为真，
    #     它们是**中间价**，新浪没有行情页，生产端指向中国货币网；
    #   ``USDCNY`` / ``USDCNH`` —— ``is_forex()`` 为**假**，是即期外汇，
    #     新浪有行情页（``/money/forex/hq/USDCNY.shtml``）。
    if _call(sym, "is_forex"):
        return get_forex_middle_link(sym)
    if _call(sym, "is_east_money_forex") or str(text).startswith("USD"):
        return get_sina_forex_link(sym)

    if _call(sym, "is_symbol_a"):
        if _call(sym, "is_fund_a"):
            return get_sina_fund_link(sym)
        return get_external_link(
            "%s/realstock/company/%s/nc.shtml" % (SINA_FINANCE_URL, str(text).lower()), text)

    if _call(sym, "is_symbol_h"):
        return get_external_link(
            "%s/hkstock/quotes/%s.html" % (SINA_STOCK_URL, text), text)

    if _call(sym, "is_symbol_us") or _call(sym, "is_index"):
        return get_sina_us_stock_link(sym)

    return text
