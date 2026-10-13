"""站内链接：估值表首格那个指向自己详情页的链接。

对应 PHP：``php/stocklink.php`` 的 ``GetStockLink()`` 一族。

## 为什么单独一个模块，而不是并进 ``externallink.py``

``externallink.py`` 管的是**外链**（新浪行情、雪球，``target="_blank"``）；
这里管的是**站内**链接（自己的 ``.php`` 页面）。PHP 里也是两个文件
（``externallink.php`` / ``stocklink.php``），混在一起会让"这个首格该跳外站
还是跳自己"变得看不出来 —— 而这两个首格在页面上是**紧挨着**的两张表。

## 迁这一个的起因是一个真 bug

``MysqlReference.GetStockLink`` 原先被别名成 ``get_external_link``（注释写
"语义与 GetExternalLink() 相同"）。于是估值表首格渲染出**新浪外链**，
而生产端 ``qdiicn.php`` 表2 的首格是
``https://palmmicro.com/woody/res/sh513100cn.php``（站内详情页）。
详情页只有一只基金、没有外链并排，所以这个错一直没被发现；
是分类页把两张表摆到一起、用 ``_prod_qdiipage_compare.py`` 逐格比 href
才抓出来的。

## 范围

只迁估值表首格用到的那一支：

====================================  =========================================
PHP                                    本模块
====================================  =========================================
``GetPhpLink()``                       :func:`get_php_link`
``GetGroupStockLink()``                :func:`get_group_stock_link`
``GetMyStockLink()``                   :func:`get_my_stock_link`
``StockSymbol::GetStockLink()``        :func:`get_stock_link`
====================================  =========================================

``php/stocklink.php`` 里那一大堆基金公司链接（``GetEFundSoftwareLinks`` 等）
**不迁** —— 那是页面页脚的"基金公司官网"区，本项目的页面还没有那一块。

## 两处与 PHP 的差异（迁移期取舍）

1. **不带服务器名**。PHP 的 ``GetInternalLink()`` 用 ``UrlGetServer()`` 拼
   绝对 URL；这里返回站点绝对路径（``/woody/res/xxx.php``），
   由部署层决定域名。对照脚本因此只比对**路径部分**。
2. **没有"当前页"分支**。PHP 的 ``GetPageLink()`` 在目标就是当前页时返回
   纯文本（不加 ``<a>``）；页面层没有"当前页"概念，直接给链接。
"""

from __future__ import annotations

from typing import Any

from core.stocksymbol import in_array_all
from ref.externallink import get_link_element

#: PHP ``define('PATH_STOCK', '/woody/res/')``（``php/httplink.php:2``）。
PATH_STOCK = "/woody/res/"

#: PHP ``define('URL_CNPHP', 'cn.php')`` / ``define('URL_PHP', '.php')``
#: （``php/url.php:2-3``）。中文页加 ``cn``，英文页不加。
URL_CNPHP = "cn.php"
URL_PHP = ".php"

#: PHP ``define('ALL_STOCK_DISPLAY', '全部股票代码')``（``php/stocklink.php:125``）。
ALL_STOCK_DISPLAY = "全部股票代码"

#: ``GetMyStockLink()`` 用的页面名（``php/stocklink.php:128``）。
MYSTOCK_PAGE = "mystock"

#: ``StockGetTransactionLink()`` 用的页面名（``php/stocklink.php:346``）。
MYSTOCKTRANSACTION_PAGE = "mystocktransaction"

#: ``GetOvernightCnhLink()`` 用的页面名（``php/stocklink.php:115``）。
OVERNIGHT_CNH_PAGE = "overnightcnh"

#: PHP ``define('OVERNIGHT_CNH_DISPLAY', '义工群对冲计算器')``
#: （``php/stocklink.php:18``）。
OVERNIGHT_CNH_DISPLAY = "义工群对冲计算器"


def _php_truthy(value: Any) -> bool:
    """PHP 布尔上下文：``''`` / ``'0'`` / ``False`` / ``None`` 都是假值。

    Python 里 ``'0'`` 是**真值**，所以 ``if ($strSymbol)`` 不能直译成
    ``if symbol:`` —— 一个 ``'0'`` 符号名（虽然罕见）会让查询串少一段。
    """
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return value not in ("", "0")
    return bool(value)


def get_php_link(
    path_page: Any,
    query: Any = None,
    display: Any = None,
    chinese: bool = True,
) -> str:
    """对应 ``GetPhpLink()``：拼一个站内 ``.php`` 链接。

    PHP 原文（``php/httplink.php:87``）::

        $str = $strPathPage;
        $str .= UrlGetPhp($bChinese);        // 'cn.php' 或 '.php'
        if ($strQuery) $str .= '?'.$strQuery;
        return GetInternalLink($str, $strDisplay);

    注意 ``GetInternalLink()`` 与 ``GetExternalLink()`` 的**唯一**区别就是
    没有 ``target="_blank"`` —— 站内链接在当前窗口跳。
    """
    page = "" if path_page is None else str(path_page)
    url = page + (URL_CNPHP if chinese else URL_PHP)
    if query:
        url += "?" + str(query)
    # PHP 的 GetInternalLink() 在 $strDisplay 为空时用 basename($strPath)
    text = display if display else page.rsplit("/", 1)[-1]
    return get_link_element(text, url)


def get_group_stock_link(
    symbol: Any, display: Any = None, chinese: bool = True
) -> Any:
    """对应 ``GetGroupStockLink()``：符号在"全部"清单里时给它一个专属页面。

    PHP 原文（``php/stocklink.php:377``）::

        if (in_arrayAll($strSymbol))
            return GetStockPageLink(strtolower($strSymbol), ...);
        return false;

    页面名是**小写符号**（``SH513100`` → ``sh513100``），于是 URL 形如
    ``/woody/res/sh513100cn.php`` —— 生产端估值表首格正是这个形状。
    不在清单里时返回 ``None``（PHP 返回 ``false``），由调用方走下一支。
    """
    text = display if display else symbol
    if not in_array_all(symbol):
        return None
    return get_php_link(PATH_STOCK + str(symbol).lower(), None, text, chinese)


def get_my_stock_link(
    symbol: Any = None, display: Any = None, chinese: bool = True
) -> Any:
    """对应 ``GetMyStockLink()``：没有专属页面时的兜底（带查询参数）。

    PHP 原文（``php/stocklink.php:126``）::

        $strPage = 'mystock';
        if ($strSymbol) return GetStockSymbolLink($strPage, $strSymbol, $strDisplay);
        return GetStockPageLink($strPage, ALL_STOCK_DISPLAY);

    于是 URL 形如 ``/woody/res/mystockcn.php?symbol=SH513100``。
    """
    if symbol:
        text = display if display else symbol
        return get_php_link(PATH_STOCK + MYSTOCK_PAGE, "symbol=" + str(symbol), text, chinese)
    text = display if display else ALL_STOCK_DISPLAY
    return get_php_link(PATH_STOCK + MYSTOCK_PAGE, None, text, chinese)


def get_stock_link(symbol: Any, display: Any = None, chinese: bool = True) -> str:
    """对应 ``StockSymbol::GetStockLink()``（``php/stock/stocksymbol.php:1173``）::

        if ($str = GetGroupStockLink($this->GetSymbol())) return $str;
        return $this->GetMyStockLink();

    先试专属页面，拿不到再走 ``mystock`` 兜底 —— 两条路都保证有内容，
    因为首格为空会让整行少一格、后面所有列错位（实测踩过）。
    """
    link = get_group_stock_link(symbol, display, chinese)
    if link:
        return link
    return get_my_stock_link(symbol, display, chinese)


def get_transaction_link(group_id: Any, symbol: Any = None, display: Any = None,
                         chinese: bool = True) -> str:
    """对应 ``StockGetTransactionLink()``（``php/stocklink.php:336``）。

    PHP 原文::

        $strQuery = 'groupid='.$strGroupId;
        if ($strSymbol)	$strQuery .= '&symbol='.$strSymbol;
        if ($strDisplay == false)
        {
            $sym = new StockSymbol($strSymbol);
            $strDisplay = $sym->GetDisplay();
        }
        return GetStockPageLink('mystocktransaction', $strDisplay, $strQuery);

    ``投资组合`` 段落（``portfolioparagraph.php``）首格用的就是它：
    点一下跳到该分组下这只股票的交易明细页。

    **显示文本默认取 ``StockSymbol::GetDisplay()``**（去前缀后的展示名），
    不是原符号 —— 比如 ``hf_ES`` 显示成 ``ES``。所以这里延迟导入
    :class:`core.stocksymbol.StockSymbol`，避免与 ``core`` 形成环。
    """
    query = "groupid=" + ("" if group_id is None else str(group_id))
    if _php_truthy(symbol):
        query += "&symbol=" + str(symbol)

    if display is None or display is False or display == '':
        # PHP 是 `$strDisplay == false`，空串也会重建显示名
        from core.stocksymbol import StockSymbol

        display = StockSymbol(symbol).get_display() if _php_truthy(symbol) else symbol
    return get_php_link(PATH_STOCK + MYSTOCKTRANSACTION_PAGE, query, display, chinese)


def get_overnight_cnh_link(display: Any = OVERNIGHT_CNH_DISPLAY,
                           amount: Any = '95000',
                           chinese: bool = True) -> str:
    """对应 ``GetOvernightCnhLink()``（``php/stocklink.php:112``）。

    PHP 原文::

        $strPage = 'overnightcnh';
        return GetStockPageLink($strPage, $strDisplay, $strPage.'='.$strAmount);

    注意查询串的**键就是页面名本身**（``overnightcnh=-123456``），
    不是 ``amount=`` 这种常规键。``portfolioparagraph.php`` 的
    ``fx_susdcnh`` 那一行把"折成美元的总人民币金额"当参数传进去。
    """
    page = OVERNIGHT_CNH_PAGE
    return get_php_link(PATH_STOCK + page, "%s=%s" % (page, amount), display, chinese)


# ---------------------------------------------------------------------------
# PHP 原名别名（见 AGENTS.md：保留 camelCase 别名）
# ---------------------------------------------------------------------------
GetPhpLink = get_php_link
GetGroupStockLink = get_group_stock_link
GetMyStockLink = get_my_stock_link
GetStockLink = get_stock_link
GetOvernightCnhLink = get_overnight_cnh_link
StockGetTransactionLink = get_transaction_link
