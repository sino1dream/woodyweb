"""详情页的「基金指数对照表」——**带对冲值**那一版。

对应三处 PHP：

* ``php/ui/fundlistparagraph.php:38`` ``EchoFundListParagraph($arRef, $callback)``
  整张表；
* 同文件 ``:14`` ``_echoFundListItem()`` 一行六格；
* ``woody/res/php/_qdiigroup.php:41`` ``_callbackFundListHedge()`` 最后一格。

只有 QDII 详情页传回调（``_qdiigroup.php:100``），其它调用点
（``fundlistcn.php`` / ``_chinaindex.php`` / ``_qdiimix.php``）传的是默认
``$callback = false``，那一列显示「参考值」——那一版在
:mod:`web.services.fund_list_service` 里，本模块不管。

生产端 ``sz161125cn.php`` 实测（``fundlisttable``，宽 540）::

    代码 | 跟踪代码 | 仓位 | 校准值    | 日期        | 对冲值
    SH   | ^GSPC    | -1   | 245.591  | 2026-10-09 | -72
    SPY  | ^GSPC    | 1    | 10.035   | 2026-10-09 | 1732

## 对冲值那一格在算什么

.. code-block:: php

    function _callbackFundListHedge($strSymbol)
    {
        $ref = $acct->GetRef();
        return GetStockHedge($ref->GetSymbol(), $ref->GetStockId(), $strSymbol);
    }

``GetStockHedge()``（``php/stock.php:401``）因为**显式给了** ``$strLev``
而走比率分支：扫描**本符号**的全部校准记录，取"两边都有值的第一个日期"，
返回 ``(本校准值 / 本仓位) / (对校准值 / 对仓位)``；找不到共同的日期就 ``1.0``。

说人话：**对冲一份本基金要配多少份那个杠杆标的**。所以这一列跟这一行的
「校准值」不是一回事 —— 校准值是本行标的自己的，对冲值是两只标的的**比值**。

**这条公式不用重写**：``auto/stockdata.py`` 的 ``get_stock_hedge()`` 已经把它
迁好了（含比率分支、首个共同日期、回退 1.0）。本模块只负责把它接到
``$acct->GetRef()`` 的口径上 —— 也就是"当前这只基金"。

## 两处容易被抄漏的细节

1. **「跟踪」列只有第一个出现的是链接** —— PHP 用 ``static $arSymbol`` 去重
   （``_getFundPairLink()``），生产端实测：SH 行的 ``^GSPC`` 是链接，
   SPY 行的同一个 ``^GSPC`` 变纯文本。本模块用 ``seen`` 集合表达同一件事。
2. **数字按 0 为基准染色**：``GetNumberDisplay()`` 走
   ``StockGetPriceDisplay($fVal, 0.0, ...)``，大于容差是 ``black``（
   ``black`` 不包 ``<font>``，等于纯文本），小于负容差是 ``red``，
   落在容差内是 ``grey``。容差是 ``0.5 / 10**精度`` —— 精度 0 时是 0.5，
   所以 ``0.3`` 会染成灰而不是黑。

## 已知偏差

* **表 id 带符号前缀**（``SZ161125fundlist``）。PHP 写死 ``'fundlist'``；
  本版沿用 :mod:`web.services.calibration_service` 的做法加符号前缀 ——
  同一页将来若摆两张这种表不会撞 id。
* **宽度用常量而不是 PHP 的像素和**：``uicolumn`` 那六个列类已经把生产端的
  80 / 80 / 60 / 140 / 100 / 80 迁好了，本模块直接用。
* 没有 ``GetFundListLink()`` 那一整段：PHP 把「基金指数对照表」这个链接放在
  表**前面**（``EchoTableParagraphBegin(..., $str)`` 的第三个参数），
  本版按本项目其它表的做法挂到表后面的 ``table-note`` 里，位置不同、内容一样。
"""

from __future__ import annotations

from typing import Any, Callable, Sequence

from web.bootstrap import install_paths

install_paths()

from sql.historytable import TableRow, format_number_display  # noqa: E402
from ui.uicolumn import (  # noqa: E402
    column_calibration,
    column_date,
    column_hedge,
    column_position,
    column_symbol,
    columns_to_dicts,
)

#: 表 id 的后缀。PHP 用固定的 ``'fundlist'``，本版前面再拼符号（见模块 docstring）。
FUNDLIST_ID_SUFFIX = "fundlist"

#: 表标题，对应 ``GetFundListLink()`` 的链接文字。
FUNDLIST_TITLE = "基金指数对照表"

#: ``php/debug.php:14`` 的 ``CALIBRATION_PRECISION``：校准值那一列的精度。
CALIBRATION_PRECISION = 3

#: 仓位列：``GetNumberDisplay($fPos)`` 不传精度 → 默认 2。
POSITION_PRECISION = 2

#: 对冲值列：``GetNumberDisplay($val, 0)`` —— 明确传 0，取整。
HEDGE_PRECISION = 0

#: ``GetStockHedge()`` 三条失败路径共同的返回值（``php/stock.php:428``）。
DEFAULT_HEDGE = 1.0

#: 第一行哪一格是「跟踪」列；``TableRow.hrefs`` 用下标挂链接。
_PAIR_CELL_INDEX = 1

#: 链接构造器要的两种 kind：一种指向历史页，一种指向标的自己的详情页。
KIND_CALIBRATION_HISTORY = "calibrationhistory"
KIND_FUND = "fund"


def build_hedge_callback(fund: Any, source: Any = None) -> Callable[[Any], Any]:
    """``_callbackFundListHedge()``：给一个符号算它对本基金的对冲值。

    **本基金**取自 PHP 的 ``$acct->GetRef()`` —— 也就是正在看的这只基金，
    不是行里的杠杆标的。拿不到符号就返回 ``None``（调用方据此退回"没有
    对冲值"的形态，而不是摆一列 1.0 —— 那看着像算出来了，其实什么都没算）。
    """
    symbol = _symbol_of(fund)
    if not symbol:
        return None
    stock_id = _stock_id_of(fund, source)

    def callbackFundListHedge(row_symbol: Any = None) -> Any:
        return _hedge_of(source, symbol, stock_id, row_symbol)

    callbackFundListHedge.__name__ = "_callbackFundListHedge"
    return callbackFundListHedge


#: camelCase 别名（``AGENTS.md`` 第 3 条）。
callbackFundListHedge = build_hedge_callback


def fund_list_table(
    fund: Any,
    leverage_refs: Sequence[Any] = (),
    source: Any = None,
    link_builder: Any = None,
    pair_symbol: str = "",
) -> dict[str, Any] | None:
    """``EchoFundListParagraph($arLev, '_callbackFundListHedge')``。

    ``fund`` 是被看的基金（``$acct->GetRef()``），``leverage_refs`` 是
    ``$arLev``。两者缺一个都返回 ``None`` —— PHP 那边的守卫是
    ``if (count($arLev) > 0)``，没有杠杆标的就整段不出现，
    与本项目中其它段落"缺数据就整段不摆"的处理一致。

    ``pair_symbol`` 是「跟踪」列的兜底：PHP 的 ``FundPairReference`` 自己知道
    跟踪谁（``GetPairRef()``），而本项目的引用对象在**未迁移**
    ``FundPairReference`` 的情况下拿不到它（见
    :mod:`web.services.qdii_trading_column` 的说明）。调用方把这些标的找出来时
    用的正是"谁跟踪了中间层标的"，所以把那个符号传进来即可 —— 比让这一列空着好。
    """
    refs = [ref for ref in (leverage_refs or ()) if ref is not None]
    if not refs or fund is None:
        return None

    callback = build_hedge_callback(fund, source)
    # 第二列是 ``new TableColumnSymbol('跟踪')`` —— PHP 的 ``$strPrefix`` 会拼在
    # 文本前面（生产端是「跟踪」+ 栗色的「代码」）。``columns_to_dicts`` 负责拼，
    # 直接把 TableColumn 丢给模板会**丢掉前缀**。
    columns = columns_to_dicts([
        column_symbol(),
        column_symbol(prefix="跟踪"),
        column_position(),
        column_calibration(),
        column_date(),
        column_hedge(),
    ])

    # PHP 的 ``static $arSymbol``：同一份「跟踪」符号只在第一次出现时给链接。
    # 集合的作用域是**这一张表**（PHP 那边是本次请求，但一张表只调一次）。
    seen_pairs: set = set()
    rows = [
        _row_of(ref, source, callback, link_builder, seen_pairs, pair_symbol)
        for ref in refs
    ]

    symbol = _symbol_of(fund) or ""
    return {
        "id": "%s%s" % (symbol, FUNDLIST_ID_SUFFIX),
        "title": FUNDLIST_TITLE,
        "symbol": symbol,
        "columns": columns,
        "rows": rows,
        "count": len(rows),
    }


#: PHP 原名（``AGENTS.md`` 第 3 条）。
EchoFundListParagraph = fund_list_table


# ---------------------------------------------------------------------------
# 一行：_echoFundListItem()
# ---------------------------------------------------------------------------
def _row_of(
    ref: Any,
    source: Any,
    callback: Any,
    link_builder: Any,
    seen_pairs: set,
    default_pair_symbol: str = "",
) -> TableRow:
    """一行六格，顺序与 ``_echoFundListItem`` 一致。"""
    symbol = _symbol_of(ref)
    stock_id = _stock_id_of(ref, source)

    pair_ref = _invoke(ref, "get_pair_ref", "GetPairRef")
    pair_symbol = _symbol_of(pair_ref) or default_pair_symbol
    pair_text = _display_of(pair_ref) if pair_ref is not None else ""
    if not pair_text:
        pair_text = pair_symbol

    cells: list = [
        _display_of(ref),
        pair_text,
        _number_cell(_invoke(ref, "get_position", "GetPosition"), POSITION_PRECISION),
        _number_cell(_invoke(ref, "get_factor", "GetFactor"), CALIBRATION_PRECISION),
        _calibration_date(source, stock_id),
        _number_cell(
            callback(symbol) if callable(callback) else None, HEDGE_PRECISION
        ),
    ]

    row = TableRow(cells=cells)
    # ``RefEchoTableColumn()`` 把股票名挂在**第一格**上当 tooltip。
    if symbol and source is not None:
        try:
            row.title = str(source.get_stock_name(symbol) or "")
        except Exception:
            row.title = ""
    if link_builder is not None and symbol:
        try:
            row.href = link_builder(symbol, KIND_CALIBRATION_HISTORY)
        except Exception:
            row.href = ""
    if (
        link_builder is not None
        and pair_symbol
        and pair_symbol not in seen_pairs
    ):
        # 只有**第一次**出现的跟踪符号给链接，之后同符号退化成纯文本。
        seen_pairs.add(pair_symbol)
        try:
            row.hrefs[_PAIR_CELL_INDEX] = link_builder(pair_symbol, KIND_FUND)
        except Exception:
            pass
    return row


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _hedge_of(source: Any, symbol: Any, stock_id: Any, row_symbol: Any) -> Any:
    """``GetStockHedge(本符号, 本id, 行符号)``。

    **不自己算** —— ``auto/stockdata.py`` 的 ``get_stock_hedge()`` 已经是
    这条公式的迁移版（比率分支、首个共同日期、回退 1.0 都在里面）。
    本函数只负责"数据源没有这个方法时"兜成 ``1.0``（PHP 的同名兜底值），
    因为替身数据源与内存数据源不一定实现它。
    """
    method = getattr(source, "get_stock_hedge", None)
    if method is None or not callable(method) or not row_symbol:
        return DEFAULT_HEDGE
    try:
        value = method(symbol, stock_id, row_symbol)
    except Exception:
        return DEFAULT_HEDGE
    return DEFAULT_HEDGE if value is None else value


def _calibration_date(source: Any, stock_id: Any) -> str:
    """``$sql->GetDateNow($strStockId)``：校准表里这个标的的最新日期。"""
    if source is None or not stock_id:
        return ""
    try:
        record = source.get_calibration_now(stock_id)
    except Exception:
        return ""
    if record is None:
        return ""
    return str(getattr(record, "date", "") or "")


def _number_cell(value: Any, precision: int) -> Any:
    """``GetNumberDisplay($fVal, $iPrecision)`` —— **含染色**。

    染色规则见模块 docstring「两处容易被抄漏的细节」第 2 条：基准是 0，
    容差 ``0.5 / 10**precision``；``black`` 等于不包 ``<font>``，所以返回纯文本。
    """
    if value is None or value == "":
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)

    text = format_number_display(number, precision)
    tolerance = 0.5 / (10 ** precision)
    if number > tolerance:
        return text
    if number < -tolerance:
        return (text, "red")
    return (text, "grey")


def _invoke(ref: Any, snake: str, camel: str, *args: Any) -> Any:
    """snake_case 优先、camelCase 兜底；取不到返回 ``None``。

    与其它几个模块里的 ``_invoke`` 同款写法（每个模块各留一份）：
    假对象有可能只有 camelCase。
    """
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


def _symbol_of(ref: Any) -> str:
    """``$ref->GetSymbol()``。"""
    value = _invoke(ref, "get_symbol", "GetSymbol")
    return "" if value is None else str(value)


def _display_of(ref: Any) -> str:
    """``$ref->GetDisplay()``；没有就退回符号（``TableColumnGetStock`` 的口径）。"""
    value = _invoke(ref, "get_display", "GetDisplay")
    if value in (None, ""):
        value = _invoke(ref, "get_symbol", "GetSymbol")
    return "" if value is None else str(value)


def _stock_id_of(ref: Any, source: Any) -> Any:
    """优先用 ref 自带的 ``stock_id``；没有再按符号查库。"""
    stock_id = getattr(ref, "stock_id", None)
    if stock_id:
        return stock_id
    symbol = _symbol_of(ref)
    if not symbol or source is None:
        return None
    try:
        return source.get_stock_id(symbol)
    except Exception:
        return None
