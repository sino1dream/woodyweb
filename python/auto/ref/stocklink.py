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

#: 下面三个是 **CK4 持仓分支**那一段用的页面与显示文字
#: （``php/stocklink.php:28`` / ``:231`` / ``:243``）。
#: 显示文字由 PHP 的两个常量拼出来：``STOCK_DISP_POSITION`` = '仓位'
#: （``php/ui/stockdisp.php:19``）、``STOCK_DISP_HOLDING`` = '持仓'（``:12``）。
HOLDINGS_PAGE = "holdings"
EXHAUSTIVE_HOLDINGS_PAGE = "exhaustiveholdings"
FUND_POSITION_PAGE = "fundposition"

HOLDINGS_DISPLAY = "基金持仓"
FUND_POSITION_DISPLAY = "仓位估算"
EXHAUSTIVE_HOLDINGS_DISPLAY = "穷举持仓比例"

#: ``GetOvernightCnhLink()`` 用的页面名（``php/stocklink.php:115``）。
OVERNIGHT_CNH_PAGE = "overnightcnh"

#: PHP ``define('OVERNIGHT_CNH_DISPLAY', '义工群对冲计算器')``
#: （``php/stocklink.php:18``）。
OVERNIGHT_CNH_DISPLAY = "义工群对冲计算器"

#: ``GetMyStockGroupLink()`` 用的页面名（``php/stocklink.php:329``）。
MYSTOCKGROUP_PAGE = "mystockgroup"

#: PHP ``define('STOCK_GROUP_DISPLAY', '股票分组')``（``php/stocklink.php:328``）。
STOCK_GROUP_DISPLAY = "股票分组"

#: PHP ``define('STOCK_TRANSACTION_DISPLAY', '交易记录')``
#: （``php/stocklink.php:347``）。
STOCK_TRANSACTION_DISPLAY = "交易记录"

#: ``GetDeleteLink()`` 的两个分支（``php/httplink.php:32``）。
DELETE_DISPLAY_CN = "删除"
DELETE_DISPLAY_US = "Delete"
DELETE_QUESTION_CN = "确认删除"
DELETE_QUESTION_US = "Confirm delete "

#: ``GetEditLink()`` 的显示文本（``php/account.php:11``、``:15``）。
DISP_EDIT_CN = "修改"
DISP_EDIT_US = "Edit"


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


def get_stock_symbol_link(
    page: Any, symbol: Any, display: Any = None, chinese: bool = True
) -> str:
    """对应 ``GetStockSymbolLink()``（``php/stocklink.php:118``）::

        $strQuery = 'symbol='.$strSymbol;
        return GetStockPageLink($strPage, ($strDisplay ? $strDisplay : $strSymbol), $strQuery);

    页面 + ``symbol=`` 查询串的通用形状。**没给显示文字时用符号本身**
    （不是页面名）—— PHP 那句三元表达式就是这个意思。
    """
    text = display if display else symbol
    return get_php_link(PATH_STOCK + str(page), "symbol=" + str(symbol), text, chinese)


def get_holdings_link(
    symbol: Any, display: Any = HOLDINGS_DISPLAY, chinese: bool = True
) -> str:
    """对应 ``GetHoldingsLink()``（``php/stocklink.php:144``）。

    持仓页：显示这只基金持有哪几只股票、各占多少。
    """
    return get_stock_symbol_link(HOLDINGS_PAGE, symbol, display or symbol, chinese)


def get_exhaustive_holdings_link(symbol: Any, chinese: bool = True) -> str:
    """对应 ``GetExhaustiveHoldingsLink()``（``php/stocklink.php:244``）。

    穷举页：反推"各成分股比例取多少才能让估算净值等于真实净值"。
    **只有持仓数 <= 4 时才有意义**（``fundestparagraph.php:168``）——
    未知数太多就解不出来了，所以调用方要自己判数量。
    """
    return get_stock_symbol_link(
        EXHAUSTIVE_HOLDINGS_PAGE, symbol, EXHAUSTIVE_HOLDINGS_DISPLAY, chinese
    )


def get_fund_position_link(
    symbol: Any, display_symbol: bool = False, chinese: bool = True
) -> str:
    """对应 ``GetFundPositionLink()``（``php/stocklink.php:232``）。

    ``display_symbol`` 对应 PHP 的 ``$bDisplaySymbol``：为真时链接文字用符号，
    否则用"仓位估算"。
    """
    return get_stock_symbol_link(
        FUND_POSITION_PAGE, symbol,
        symbol if display_symbol else FUND_POSITION_DISPLAY, chinese
    )


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


def get_on_click_link(path: Any, question: Any, display: Any) -> str:
    """对应 ``GetOnClickLink()``（``php/httplink.php:26``）：带确认弹窗的链接。

    PHP 原文::

        $strQuestion = str_replace('"', '', $strQuestion);
        return GetLinkElement($strDisplay, UrlGetServer().$strPath,
            array('onclick' => GetDoubleQuotes("return confirm('$strQuestion')")));

    两处必须照抄：

    * **先去掉问句里的双引号** —— 问句要塞进 ``onclick="..."`` 里，
      带双引号会把 HTML 属性截断。PHP 用 ``str_replace`` 删，不是转义；
    * ``onclick`` 的值由 :func:`get_link_element` **统一加双引号**，
      与 ``href`` 一样走 ``%s="%s"`` 那一段，所以这里传不带引号的内层文本。

    ``UrlGetServer()`` 按模块 docstring 的取舍 1 不拼（返回站点相对路径）。
    """
    text = "" if question is None else str(question).replace('"', '')
    extra = {"onclick": "return confirm('%s')" % text}
    return get_link_element(display, path, extra)


def get_delete_link(path: Any, cn: Any = "", us: Any = "",
                    chinese: bool = True) -> str:
    """对应 ``GetDeleteLink()``（``php/httplink.php:32``）。

    PHP 是 ``GetOnClickLink()`` 的薄封装，中英文两套问句::

        $strDisplay  = '删除' / 'Delete'
        $strQuestion = '确认删除'.$strCn.'？' / 'Confirm delete '.$strUs.'?'

    注意中文问句末尾是**全角问号 ``？``**、英文是半角 ``?``，
    且英文分支的 ``Confirm delete `` 自带尾空格。
    """
    if chinese:
        display = DELETE_DISPLAY_CN
        question = "%s%s？" % (DELETE_QUESTION_CN, "" if cn is None else cn)
    else:
        display = DELETE_DISPLAY_US
        question = "%s%s?" % (DELETE_QUESTION_US, "" if us is None else us)
    return get_on_click_link(path, question, display)


def get_edit_link(path_page: Any, edit: Any, chinese: bool = True) -> str:
    """对应 ``GetEditLink()``（``php/httplink.php:168``）：``edit=`` 修改链接。

    PHP 是 ``GetPhpLink($strPathPage, 'edit='.$strEdit, 修改/Edit)`` 的封装，
    与 :func:`get_php_link` 的差别只在查询串固定为 ``edit=`` 这一段。
    """
    return get_php_link(path_page, "edit=%s" % ("" if edit is None else edit),
                        DISP_EDIT_CN if chinese else DISP_EDIT_US, chinese)


def stock_get_all_transaction_link(group_id: Any, ref: Any = False,
                                   chinese: bool = True) -> str:
    """对应 ``StockGetAllTransactionLink()``（``php/stocklink.php:349``）。

    PHP 原文::

        if ($ref)	$strSymbol = $ref->GetSymbol();
        else		$strSymbol = false;
        return StockGetTransactionLink($strGroupId, $strSymbol, STOCK_TRANSACTION_DISPLAY);

    与 :func:`get_transaction_link` 的差别只有**显示文本**：这里恒为
    ``交易记录``，不是该符号的展示名 —— 因为链接的语境是"这一组全部交易"，
    显示某个具体符号反而会让人以为只看得到它。

    ``$ref`` 为真时取 ``GetSymbol()``；假值（含 ``None``）时按 PHP 的
    ``$strSymbol = false`` 处理，于是 :func:`get_transaction_link` 不加
    ``symbol=`` 段。
    """
    symbol = _symbol_of(ref) if ref else False
    return get_transaction_link(group_id, symbol, STOCK_TRANSACTION_DISPLAY, chinese)


def get_my_stock_group_link(query: Any = False, display: Any = False,
                            chinese: bool = True) -> str:
    """对应 ``GetMyStockGroupLink()``（``php/stocklink.php:329``）。

    PHP 原文::

        if ($strDisplay == false)	$strDisplay = STOCK_GROUP_DISPLAY;
        return GetStockPageLink('mystockgroup', $strDisplay, $strQuery);

    ``GetStockPageLink()`` 会走到 ``GetPageLink()``，后者在"目标就是当前页"
    时返回纯文本。按模块 docstring 的取舍 2，这里不判当前页，直接给链接。
    """
    text = display if display else STOCK_GROUP_DISPLAY
    return get_php_link(PATH_STOCK + MYSTOCKGROUP_PAGE, query, text, chinese)


def _symbol_of(ref: Any) -> Any:
    """从引用对象取符号：先 ``get_symbol`` 再 ``GetSymbol``。"""
    if ref is None:
        return None
    getter = getattr(ref, "get_symbol", None)
    if getter is None:
        getter = getattr(ref, "GetSymbol", None)
    if getter is None:
        return None
    return getter()


# ---------------------------------------------------------------------------
# PHP 原名别名（见 AGENTS.md：保留 camelCase 别名）
# ---------------------------------------------------------------------------
GetPhpLink = get_php_link
GetGroupStockLink = get_group_stock_link
GetMyStockLink = get_my_stock_link
GetStockLink = get_stock_link
GetOvernightCnhLink = get_overnight_cnh_link
StockGetTransactionLink = get_transaction_link
GetOnClickLink = get_on_click_link
GetDeleteLink = get_delete_link
GetEditLink = get_edit_link
StockGetAllTransactionLink = stock_get_all_transaction_link
GetMyStockGroupLink = get_my_stock_group_link
