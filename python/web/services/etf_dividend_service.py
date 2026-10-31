"""ETF 分红数据页 —— 对应 ``woody/res/etfdividendcn.php``（73 行）。

## PHP 那边是什么

一个 ``SymbolAccount``（``php/_emptygroup.php``）页面，整页只有一张表：

    日期 / 分红 / 价格 / 比例(%)

* **分红** = ``stockdividend.close``，每 **1 股（份）**的除息金额，过
  ``rtrim0()``（``php/debug.php:60``，实际是 ``strval(floatval(...))``）；
* **价格** = ``stockhistory`` **当天的收盘价**，``GetClose()`` 原样、**不过**
  ``rtrim0`` —— 与 ``_ahhistory.php`` 同一个坑：只有一列做了 trim。
  这里还多一层：``close`` 列是 ``double(10,3)``，mysqli 交出来的是补零到三位
  的字符串（``'741.470'``），pymysql 交出来的是 ``float``（``741.47``）。
  所以取数走 :meth:`StockHistorySql.get_close_text`（``CAST(... AS CHAR)``），
  不用 ``str(float)`` —— 否则价格列会少一位；
* **比例(%)** = ``number_format(分红 / 价格 * 100.0, 2)``。

这一列是给夜盘跨市场套利用的：美股 ETF 除息日价格会跌掉分红，而持有它的
QDII 基金拿到的是现金分红、净值不跌，于是实时估值端冒出一段**假折价**，
幅度就是这一列。页面的 meta description 写的就是这件事。

## 两道闸门，顺序不能换

1. **取不到引用对象** → 整页只剩标题（``$acct->EchoStockGroup()`` 返回假）。
   这一道包含"``stock`` 表里没有这个符号"：PHP 那边 ``StockCheckSymbol()``
   先过符号校验，取不到 ``stock_id`` 就没有后续；本服务用 ``stock_id is None``
   表达同一个意思；
2. ``CountNetValue() == 0`` → **连表头都不输出**。PHP 在
   ``EchoTableParagraphBegin()`` **之前**就 ``return`` 了，所以没有净值记录的
   标的（A 股个股、指数、外汇）页面上一个表都没有 —— 生产端
   ``?symbol=SH600030`` 实测就是这样。

第 2 道闸门容易被写成"有分红记录才显示表"，那是错的：判据是**净值**，
不是分红。

## 演示库现状（不是 bug）

``stockdividend`` 只有 4 行，``stock_id`` 是 182 / 184 / 186 / 214，而
``stock`` 表最大 id 是 181 —— 四条全是孤儿行，任何符号都查不到。加上
``QQQ`` / ``SPY`` 在演示库没有 ``netvaluehistory`` 记录，连第 2 道闸门都过不去，
所以本地这页恒为空态。

## 与 PHP 的两处刻意偏离

1. **不给 Yahoo 分红外链**。PHP ``GetStockDividendLink()``
   （``php/externallink.php:305``）对非 A/H 标的给
   ``finance.yahoo.com/quote/<sym>/history?filter=div``，实测该域名自
   2021-11-01 起对大陆返回"服务停止"公告页。这里对这类标的**不生成外链**，
   只剩 A 股 / H 股两条新浪链接（实测个股有效、ETF 与 LOF 是空表但链接本身可点）。
2. ``$bAdmin`` 的"更新分红数据"入口未迁。抓取链路已经在
   ``auto/updater/etfdividend.py``（SPDR 五只走 ssga 接口、其余走 Proshares），
   本版没有登录态，要更新走 ``/submit`` 那一页。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from web.bootstrap import install_paths

install_paths()

from core.stockdata import rtrim0  # noqa: E402
from core.stocksymbol import StockSymbol  # noqa: E402
from sql.historytable import TableRow, php_number_format  # noqa: E402
from sql.optionstore import StockDividendSql  # noqa: E402
from sql.stockhistory import StockHistorySql  # noqa: E402
from ui.uicolumn import (  # noqa: E402
    columns_to_dicts,
    column_date,
    column_percentage,
    column_price,
    column_stock,
)


#: ``STOCK_OPTION_DIVIDEND``（``php/stocklink.php:36``）与
#: ``ETF_DIVIDEND_DISPLAY``（``:181``，即 ``STOCK_OPTION_DIVIDEND.'数据'``）。
STOCK_OPTION_DIVIDEND = "分红"
ETF_DIVIDEND_DISPLAY = STOCK_OPTION_DIVIDEND + "数据"

#: 表 id 后缀：PHP 是 ``$strSymbol.'etfdividend'``（``etfdividendcn.php:37``）。
ETF_DIVIDEND_TABLE_ID_SUFFIX = "etfdividend"

#: 新浪两个域名，照 ``php/externalurl.php:53`` / ``:58``。
SINA_STOCK_URL = "https://stock.finance.sina.com.cn"
SINA_VIP_STOCK_URL = "https://vip.stock.finance.sina.com.cn"


def sina_dividend_link(symbol: str) -> Optional[Dict[str, str]]:
    """``GetStockDividendLink()``（``php/externallink.php:305``）的**剩余分支**。

    PHP 三派：

    * ``IsSymbolA()`` → 新浪"A 股分红配股"页，路径里放的是**六位数字**；
    * ``IsSymbolH()`` → 新浪港股"分红派息"页，路径里放的是**完整符号**；
    * 其余 → Yahoo（本版**去掉**，见模块 docstring 的偏离 1）。

    注意 ``IsSymbolA()`` 只看"六位数字 + SH/SZ/BJ 前缀"，**不区分股票与基金**。
    所以 ``SH513100`` / ``SZ161125`` 这类 ETF、LOF 也会拿到这条链接 —— 实测
    那两个页面在新浪是"暂时没有数据"，但链接本身可点，挂着不花成本，照抄。
    """
    try:
        sym = StockSymbol(symbol)
    except Exception:
        return None

    digit = sym.is_symbol_a()
    if digit:
        return {
            "text": "Sina" + ETF_DIVIDEND_DISPLAY,
            "url": "%s/corp/go.php/vISSUE_ShareBonus/stockid/%s.phtml"
            % (SINA_VIP_STOCK_URL, digit),
        }
    if sym.is_symbol_h():
        return {
            "text": "Sina" + ETF_DIVIDEND_DISPLAY,
            "url": "%s/hkstock/dividends/%s.html" % (SINA_STOCK_URL, symbol),
        }
    return None


def _columns() -> List[Dict[str, Any]]:
    """四列表头，对应 ``etfdividendcn.php:33-37``。"""
    return columns_to_dicts(
        [
            column_date(),
            column_stock(STOCK_OPTION_DIVIDEND),
            column_price(),
            column_percentage(),
        ]
    )


def _net_value_count(ref: Any) -> int:
    """``$ref->CountNetValue()``（``php/stock/mysqlref.php:146``）。

    取不到就当 0 —— 与 PHP 里"没有净值记录"同一后果（整表不输出）。
    假引用对象（单测）上没有这个方法，也不该因此报错。
    """
    counter = getattr(ref, "count_net_value", None)
    if counter is None:
        return 0
    try:
        return int(counter())
    except Exception:
        return 0


def build_etf_dividend(
    symbol: str,
    source: Any = None,
    ref: Any = None,
) -> Optional[Dict[str, Any]]:
    """装配分红数据页。

    ==================  ================================================
    返回键               含义
    ==================  ================================================
    ``symbol``          符号
    ``title``           ``<symbol>分红数据``（``GetTitle()``）
    ``dividend_link``   新浪外链 ``{"text", "url"}``；没有就 ``None``
    ``table``           表格 dict；**闸门挡掉时为 ``None``**
    ``net_value_count`` 净值记录条数，模板用它区分两种空态
    ==================  ================================================

    ``ref`` 为空返回 ``None``（对应闸门 1）；其余情况下即使表格为空也返回
    dict —— 页面仍然要渲染标题和链接。
    """
    if ref is None:
        return None

    getter = getattr(ref, "get_stock_id", None)
    stock_id = getter() if getter is not None else None
    #: 闸门 1 的另一半：``stock`` 表里没有这个符号 → ``stock_id`` 为空。
    #: PHP 那边 ``StockCheckSymbol()`` 过不了就 ``EchoStockGroup()`` 返回假，
    #: 后续 ``CountNetValue()`` / ``GetAll()`` 全都无从下手。实测
    #: ``?symbol=NOSUCH`` 在本版如果不拦，会掉进闸门 2 显示"没有净值记录"，
    #: 把"符号不存在"说成"没有净值"，误导。
    if getter is not None and stock_id is None:
        return None

    net_value_count = _net_value_count(ref)
    page: Dict[str, Any] = {
        "symbol": symbol,
        "title": symbol + ETF_DIVIDEND_DISPLAY,
        "dividend_link": sina_dividend_link(symbol),
        "table": None,
        "net_value_count": net_value_count,
    }

    #: 闸门 2：没有净值记录就直接返回，连表头都不给。写法上必须放在
    #: 查库之前 —— PHP 的 return 也在 EchoTableParagraphBegin 之前。
    if net_value_count == 0:
        return page

    connection = getattr(source, "connection", None)
    if connection is None:
        return page

    if stock_id is None:
        return page

    history = StockHistorySql(connection)
    try:
        records = StockDividendSql(connection).get_all(stock_id)
    except Exception:
        #: 表不存在（本地 dev 建库不完整）与"没有记录"在页面上都表现为空表，
        #: 不把异常抛到路由层去换 500 —— PHP 那边表不存在也只是没有行。
        records = []

    rows = []
    for record in records:
        date = record.get("date")
        #: PHP：``if ($strClose = $his_sql->GetClose($strStockId, $strDate))``
        #: —— 取不到收盘价这一行**整行不出现**。注意判的是**字符串**的假值：
        #: 空串与 ``None`` 跳过，而 ``'0.000'`` 在 PHP 里是**真值**，不跳过。
        #: 所以这里用 ``get_close_text()`` 而不是 ``get_close()`` 拿 float ——
        #: 后者会把 0.0 也当作假，且 ``str(741.47)`` 会丢掉 ``double(10,3)``
        #: 应有的第三个小数位（mysqli 给的是 ``'741.470'``）。
        try:
            close_text = history.get_close_text(stock_id, date)
        except Exception:
            close_text = None
        if not close_text:
            continue

        #: 刻意偏离：PHP 8 里 ``1/0`` 抛 ``DivisionByZeroError``，这一页会
        #: **整页 500**。价格是 ``double(10,3)``，理论上可以存 ``0.000``，
        #: 一行脏数据不该让整页挂掉 —— 这里跳过该行。正常数据不会走到这条
        #: 分支（``stockhistory`` 里没有 0 收盘价）。
        if float(close_text) == 0.0:
            continue

        row = TableRow()
        row.add(str(date))
        #: 「分红」过 ``rtrim0``，「价格」原样 —— 只有一列 trim，别顺手统一。
        dividend = rtrim0(record.get("close"))
        row.add(dividend)
        row.add(close_text)
        row.add(php_number_format(float(dividend) / float(close_text) * 100.0, 2))
        rows.append(row)

    page["table"] = {
        "id": "%s%s" % (symbol, ETF_DIVIDEND_TABLE_ID_SUFFIX),
        "title": ETF_DIVIDEND_DISPLAY,
        "columns": _columns(),
        "rows": rows,
        "count": len(rows),
    }
    return page
