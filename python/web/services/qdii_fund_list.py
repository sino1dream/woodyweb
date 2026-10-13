"""详情页的「基金指数对照表」——**带对冲值**那一版。

对应三处 PHP：

* ``php/ui/fundlistparagraph.php:38`` ``EchoFundListParagraph($arRef, $callback)``
  整张表；
* 同文件 ``:14`` ``_echoFundListItem()`` 一行六格；
* ``woody/res/php/_qdiigroup.php:41`` ``_callbackFundListHedge()`` 最后一格。

``EchoFundListParagraph()`` 有**两种形态**，由第二个参数 ``$callback`` 决定，
PHP 里是同一个函数：

* 传回调（``_qdiigroup.php:100``）      → 末列是「对冲值」；
* 不传（``_mystock.php:134`` 的个股详情页，以及 ``fundlistcn.php`` /
  ``_chinaindex.php`` / ``_qdiimix.php``）→ 末列是「参考值」，读
  ``lastcalibration`` 表。

本模块两种都装（``fund_list_table`` 的 ``callback`` 参数），因为**前五列与
逐行组装完全共用** —— 分成两个函数只会让"仓位/校准值/日期三格怎么来"
出现两处实现。分组列表页那一版（``fundlistcn.php`` 的整页形态）另在
:mod:`web.services.fund_list_service`，本模块不管。

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

from sql.historytable import (  # noqa: E402
    TableRow,
    format_number_display,
    format_price_display,
)
from ui.uicolumn import (  # noqa: E402
    column_base,
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

#: 没有回调时末列的列名（``new TableColumn('参考值')``，``fundlistparagraph.php:46``）。
#: PHP 那里是**直接 new 基类**，所以宽 80、无色 —— 没有对应的列预置类。
REFERENCE_COLUMN_TEXT = "参考值"

#: 参考值那一格的精度兜底。PHP ``GetPriceDisplay()`` 用的是 ref 自带的
#: ``GetPrecision()``（多数符号是 3）；ref 说不出来时才用这个。
REFERENCE_PRECISION = 3

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
    """``EchoFundListParagraph($arLev, '_callbackFundListHedge')`` —— 带回调那版。

    ``fund`` 是被看的基金（``$acct->GetRef()``），``leverage_refs`` 是
    ``$arLev``。两者缺一个都返回 ``None`` —— PHP 那边的守卫是
    ``if (count($arLev) > 0)``，没有杠杆标的就整段不出现，
    与本项目中其它段落"缺数据就整段不摆"的处理一致。

    ``pair_symbol`` 是「跟踪」列的兜底。CK2b 已经把 ``FundPairReference`` 迁好了，
    它自己知道跟踪谁（``GetPairRef()``）；但这一路的 ref 未必是
    ``FundPairReference``，所以把符号作为参数传进来仍然更稳 ——
    调用方本来就持有那个标的（这是 ``$arLev`` 的来源），不需要绕 ref 再问一次。

    早期版本在这一段写过"引用对象在未迁移 ``FundPairReference`` 的情况下拿不到
    ``GetPairRef()``"，CK2b 之后那句话不再成立，删掉以免误导。
    """
    refs = [ref for ref in (leverage_refs or ()) if ref is not None]
    if not refs or fund is None:
        return None
    callback = build_hedge_callback(fund, source)
    return _build(
        _symbol_of(fund), refs, source, link_builder, pair_symbol,
        callback, column_hedge(),
    )


#: PHP 原名（``AGENTS.md`` 第 3 条）。
EchoFundListParagraph = fund_list_table


def fund_reference_table(
    refs: Sequence[Any] = (),
    source: Any = None,
    link_builder: Any = None,
    pair_symbol: str = "",
) -> dict[str, Any] | None:
    """``EchoFundListParagraph($arRef)`` —— **不带回调**那版，末列是「参考值」。

    与 :func:`fund_list_table` 是 PHP 里**同一个函数**的两种形态
    （``fundlistparagraph.php:38`` 的 ``$callback = false`` 默认值）：

    ==========================  =========================  ====================
    调用点                        末列                       每格的值
    ==========================  =========================  ====================
    ``_qdiigroup.php:100``       ``TableColumnHedge``        ``_callbackFundListHedge``
    ``_mystock.php:134``         ``TableColumn('参考值')``    ``lastcalibration`` 表
    ==========================  =========================  ====================

    个股详情页走的是第二种：那里 ``$arRef`` 只有**一个** ref（本符号自己），
    问的是"我跟踪谁、仓位多少、上一次校准值是多少"，没有"谁跟踪了我的标的"
    这个问题，自然也就没有对冲值。

    **刻意分成两个函数而不是加一个 ``callback`` 参数**：前五列与逐行组装共用，
    但两个调用点给出的东西不一样（一个要给"被看的基金"去算对冲，一个只给
    行列表），合成一个函数的签名会变成"``fund`` 有时是基金、有时是行自己"。
    共用部分收在 :func:`_build` 里，不会因此出现两份实现。

    表格 id 的符号前缀取 ``refs[0]`` —— 这一版没有"被看的基金"那个参数。
    """
    rows_refs = [ref for ref in (refs or ()) if ref is not None]
    if not rows_refs:
        return None
    return _build(
        _symbol_of(rows_refs[0]), rows_refs, source, link_builder, pair_symbol,
        None, column_base(REFERENCE_COLUMN_TEXT),
    )


def _build(
    symbol: str,
    refs: Sequence[Any],
    source: Any,
    link_builder: Any,
    pair_symbol: str,
    callback: Any,
    last_column: Any,
) -> dict[str, Any]:
    """两种形态共用的装配：五列固定 + 末列由调用方给。

    第二列是 ``new TableColumnSymbol('跟踪')`` —— PHP 的 ``$strPrefix`` 会拼在
    文本前面（生产端是「跟踪」+ 栗色的「代码」）。``columns_to_dicts`` 负责拼，
    直接把 TableColumn 丢给模板会**丢掉前缀**。
    """
    columns = columns_to_dicts([
        column_symbol(),
        column_symbol(prefix="跟踪"),
        column_position(),
        column_calibration(),
        column_date(),
        last_column,
    ])

    # PHP 的 ``static $arSymbol``：同一份「跟踪」符号只在第一次出现时给链接。
    # 集合的作用域是**这一张表**（PHP 那边是本次请求，但一张表只调一次）。
    seen_pairs: set = set()
    rows = [
        _row_of(ref, source, callback, link_builder, seen_pairs, pair_symbol)
        for ref in refs
    ]

    return {
        "id": "%s%s" % (symbol, FUNDLIST_ID_SUFFIX),
        "title": FUNDLIST_TITLE,
        "symbol": symbol,
        "columns": columns,
        "rows": rows,
        "count": len(rows),
    }


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
        _last_cell(ref, source, stock_id, callback),
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


def _last_cell(ref: Any, source: Any, stock_id: Any, callback: Any) -> Any:
    """末格：有回调算对冲值，没有就读 ``lastcalibration`` 当参考值。

    对应 ``fundlistparagraph.php:27-34`` 那段 ``if ($callback) ... else ...``。
    """
    if callback is None:
        return _reference_cell(ref, source, stock_id)
    return _number_cell(
        callback(_symbol_of(ref)) if callable(callback) else None, HEDGE_PRECISION
    )


def _reference_cell(ref: Any, source: Any, stock_id: Any) -> Any:
    """``$ref->GetPriceDisplay(floatval($strVal))`` —— 「参考值」那一格。

    ``$strVal`` 来自 ``LastCalibrationSql::ReadVal($strStockId, true)``
    （``php/sql/sqlval.php:98`` / ``:53``，表 ``lastcalibration`` 的 ``close``
    字段）。它是"上一次校准算出来的值"，与同一行的「校准值」列**不是一回事**：
    校准值取自 ``calibration`` 表当日那一条，参考值取自 ``lastcalibration``
    这张每个 id 只留一个值的表。

    三处容易抄错的地方：

    1. **染色基准是 ref 自己的现价，不是 0** —— ``GetPriceDisplay()`` 只传一个
       参数时 PHP 走 ``stockref.php:162`` 那条 ``$fPrev = floatval($this->strPrice)``。
       所以它与「仓位」/「校准值」两列（``GetNumberDisplay``，以 0 为基准）
       不是一套规则。
    2. 精度取 ref 自带的 ``GetPrecision()``（多数符号是 3），ref 说不出来才用
       :data:`REFERENCE_PRECISION`。
    3. PHP 用 ``if ($strVal = ...)`` 守卫：``"0"`` / ``""`` 都算没取到，那一格
       **不 push**，整行会比表头少一格。本版补一个空串 —— HTML 里少一个
       ``<td>`` 与补空串的渲染结果一致（行尾留白），但补空串能让"这行有几格"
       始终与列数对得上。
    """
    value = _last_calibration_val(source, stock_id)
    if value is None or value == "":
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number == 0.0:
        #: PHP 那边库里存的是 ``strval(0.0)`` 即 ``"0"``，``if`` 判定为假
        return ""
    precision = _invoke(ref, "get_precision", "GetPrecision")
    try:
        precision = int(precision)
    except (TypeError, ValueError):
        precision = REFERENCE_PRECISION
    return format_price_display(number, _invoke(ref, "get_val", "GetVal"), precision)


def _last_calibration_val(source: Any, stock_id: Any) -> Any:
    """``LastCalibrationSql::ReadVal($strStockId, true)``（``sqlval.php:53``）。

    数据源没有这个方法（内存数据源 / 测试替身常见）时返回 ``None``，
    那一格留空 —— 不编造一个值。
    """
    if source is None or not stock_id:
        return None
    reader = getattr(source, "get_last_calibration", None)
    if reader is None or not callable(reader):
        return None
    try:
        return reader(stock_id)
    except Exception:
        return None


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
