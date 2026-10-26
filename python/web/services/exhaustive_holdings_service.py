"""穷举持仓页的服务层：四张表的装配。

对应 PHP ``woody/res/exhaustiveholdingscn.php`` 的
``_echoExhaustiveHoldingsParagraph()``（:292-338）—— 那里用四个
``EchoTableParagraphBegin()`` 依次摆出：

========  ==========================  ==========================================
表 id      标题                       内容
========  ==========================  ==========================================
current   「当前使用的持仓数据」        每组持仓一个比例 + 基金仓位
quarter   「季报持仓」                 每个季报日一行（比例 + 仓位）
（页面名）「穷举持仓比例」             逐日累加方程解出来的比例与仓位
verify    「验证最激进数据」           克莱姆法则解"最激进"的那一季
========  ==========================  ==========================================

行内容全部由 :mod:`core.exhaustiveholdings` 计算（纯逻辑 + 单测），本模块只
负责"取哪几笔数据、摆哪几列、按什么条件停"。

## 三个停表条件（照抄，别"优化"）

1. 穷举表最多 ``num`` 行（PHP ``$iTotal == $iNum`` → break，``$iNum`` 来自
   ``GetNum()/4``，默认 25）；
2. 验证表见到第一行"解得出来"就 break（``$bReturn``）；
3. **持仓数 > 4 时后两张表整段不出现**（``if ($iCount <= 4)``）——
   未知数比方程还多，解不出有意义的比例。

## 与 PHP 的两处结构差异

* **没有账号层**：PHP 整页在 ``$acct->EchoStockGroup()`` 后面，还带
  ``GetEditInput()`` 的阈值表单；本版的登录态收敛成路由参数（固定 demo
  身份、权限闸门恒开，与 admin 分组页同一套路），阈值走 ``?input=``；
* **管理员写入链接不生成**：PHP 在"仓位合理但比例不匹配"时给一个
  ``submitholdings.php`` 的确认链接。写回仓位需要管理员校验，而账号体系暂缓，
  所以这里恒不生成（``core.exhaustiveholdings.position_cell`` 留了注入口）。
"""

from __future__ import annotations

from typing import Any, Optional

from web.bootstrap import install_paths

install_paths()

from core.exhaustiveholdings import (  # noqa: E402
    DEFAULT_EXHAUSTIVE_ROWS,
    HOLDINGS_NETVALUE_DIFF,
    ExhaustiveHoldingsCalculator,
    get_quarter_holdings_extra,
    switch_date_pairs,
)
from ref.holdingsref import StockGetHoldingsReference  # noqa: E402
from sql.historytable import (  # noqa: E402
    NetValueHistorySql,
    QuarterReportSql,
    TableRow,
)
from ui.uicolumn import (  # noqa: E402
    STOCK_DISP_POSITION,
    column_base,
    column_change,
    column_date,
    column_error,
    column_net_value,
    column_position,
    columns_to_dicts,
)

#: 穷举表在 HTML 里的 ``id``。PHP 传的是 ``$acct->GetPage()`` —— 对
#: ``SymbolAccount`` 就是页面文件名（``exhaustiveholdings``），不是"exhaustive"。
#: 沿用同一个 id，锚点链接（``#current`` / ``#verify``）里写错会跳不动。
EXHAUSTIVE_TABLE_ID = "exhaustiveholdings"

#: ``QUARTER_REPORT_DISPLAY``（``php/stocklink.php:29``）。
QUARTER_REPORT_DISPLAY = "季报持仓"

#: 穷举表的标题。PHP 是 ``GetFundLinks($strSymbol)`` —— 一串跑到别的页面的
#: 链接（历史价 / 净值 / 份额 / 校准 …）。本地那些页面多数还没有路由，
#: 摆一串死链不如只写表名（与 ``_build_holdings_est`` 第三条取舍同源）。
EXHAUSTIVE_TITLE = "穷举持仓比例"

#: ``GetNetValueTableColumn()`` 的前三列（``netvaluehistoryparagraph.php:39``）：
#: 日期 / 净值 / 涨幅。后两张表都以它开头。
NET_VALUE_COLUMNS = ("date", "net_value", "change")


def _stock_column(name: Any) -> dict:
    """``TableColumnStock($holding_ref)``：宽 80、``maroon`` 色。

    PHP 的列头文本是 **ref 的 ``GetDisplay()``**（不是符号原文）——
    对美股是名字、对 A/H 股是"符号+名字"。名字取不到时 ``GetDisplay()``
    会退化，所以这里不额外查库。
    """
    return column_base(str(name or ""), 80, "maroon").to_dict()


def build_current_columns(holding_refs: list) -> list:
    """表 1 的列：每个成分股一列 + 仓位。"""
    columns = [_stock_column(_display_of(ref)) for ref in holding_refs]
    columns.append(column_position().to_dict())
    return columns


def build_quarter_columns(holding_refs: list, extra: dict) -> list:
    """表 2 的列：日期 + 季报独有的标的 + 当前持仓 + 仓位。

    **顺序有意义**：季报独有的标的排在当前持仓**前面**（PHP
    ``array_merge([Date], $arExtraColumn, $ar)``），而行的取值也按同一顺序
    填（``_echoQuarterHoldingsItem`` 遍历的就是 ``extra + 当前持仓``）。
    """
    columns = [column_date().to_dict()]
    for stock_id, name in extra.items():
        columns.append(_stock_column(name))
    for ref in holding_refs:
        columns.append(_stock_column(_display_of(ref)))
    columns.append(column_position().to_dict())
    return columns


def build_exhaustive_columns(holding_refs: list) -> list:
    """表 3 的列：净值三列 + **误差** + 持仓 + 仓位。

    ## 「误差」这一列的列名与内容是错位的（照抄 PHP）

    PHP 的表头是 ``array_merge(GetNetValueTableColumn(), [new TableColumnError()], $ar)``
    —— 第 4 列叫「误差」，但 ``_echoExhaustiveHoldingsItem`` 那一格填的是
    :meth:`~core.exhaustiveholdings.ExhaustiveHoldingsCalculator.exhaustive_row`
    里的**估值涨幅**（``GetPercentageDisplay($fNetValue, _estNetValue($strDate))``），
    不是误差。格数一致（4 + 持仓 + 仓位），所以页面不会错位，只是那个列头
    名不副实。

    生产端 ``exhaustiveholdingscn.php`` 上就是这样。真要改得先确认作者本意是
    "用净值 vs 估值的偏差当误差"还是"这里本来就该放误差但漏算了"——
    两种解释都会改变列名或内容，属于**行为变更**，不在迁移范围内。
    """
    columns = [column_date().to_dict(), column_net_value().to_dict(),
               column_change().to_dict(), column_error().to_dict()]
    for ref in holding_refs:
        columns.append(_stock_column(_display_of(ref)))
    columns.append(column_position().to_dict())
    return columns


def build_verify_columns(holding_refs: list) -> list:
    """表 4 的列：净值三列 + 持仓 + 仓位（**没有**误差列）。"""
    columns = [column_date().to_dict(), column_net_value().to_dict(),
               column_change().to_dict()]
    for ref in holding_refs:
        columns.append(_stock_column(_display_of(ref)))
    columns.append(column_position().to_dict())
    return columns


def _display_of(ref: Any) -> str:
    """``$ref->GetDisplay()``（``TableColumnStock`` 的列头文本）。"""
    for name in ("get_display", "GetDisplay", "get_symbol", "GetSymbol"):
        method = getattr(ref, name, None)
        if callable(method):
            try:
                return str(method())
            except Exception:
                continue
    return ""


def _row(cells: list, match: bool = False, background: str = "") -> TableRow:
    """cells 已是 ``(文本, 颜色)`` 二元组或纯文本 —— ``TableRow.add`` 两种都收。"""
    row = TableRow(match=match, background=background)
    for cell in cells:
        if isinstance(cell, tuple) and len(cell) == 2:
            row.add(cell[0], cell[1])
        else:
            row.add(cell)
    return row


class ExhaustiveHoldingsService:
    """把一只基金的穷举持仓页装配成模板要的 dict。"""

    def __init__(self, source: Any = None):
        #: 数据源（``MySqlFundDataSource``）：查持仓明细、净值历史、季报
        self.source = source

    # -- 对外 -------------------------------------------------------------
    def get_page(
        self,
        symbol: str,
        threshold: Optional[float] = None,
        num: int = DEFAULT_EXHAUSTIVE_ROWS,
        admin: bool = False,
    ) -> Optional[dict]:
        """整页数据。**取不到持仓时返回 ``None``**（路由渲染提示而不是空表）。

        ``threshold`` 是**百分数**（1.0 表示 1%），与 PHP 的 ``?input=`` 同口径；
        传 ``None`` 用 :data:`HOLDINGS_NETVALUE_DIFF`。
        """
        holdings_ref = self._build_holdings_ref(symbol)
        if holdings_ref is None:
            return None
        if not _call(holdings_ref, "get_holdings_date"):
            #: ``if ($ref->GetHoldingsDate())`` —— 没有持仓日就整页空
            return None

        holding_refs = list(_call(holdings_ref, "get_holdings_ref_array") or [])
        count = len(holding_refs)
        connection = getattr(self.source, "connection", None)
        net_sql = NetValueHistorySql(connection) if connection is not None else None
        quarter_sql = QuarterReportSql(connection) if connection is not None else None

        percentage = HOLDINGS_NETVALUE_DIFF if threshold is None else float(threshold)
        calculator = ExhaustiveHoldingsCalculator(
            holdings_ref, net_sql, percentage / 100.0, count, admin=admin,
        )

        page = {
            "symbol": symbol,
            "count": count,
            "threshold": percentage,
            "current": self._current_table(holding_refs, calculator),
            "quarter": None,
            "exhaustive": None,
            "verify": None,
            #: 持仓超过 4 只时 PHP 整段不算（``if ($iCount <= 4)``），
            #: 页面据此显示一句"为什么没有后两张表"
            "solvable": count <= 4,
        }

        extra = get_quarter_holdings_extra(holdings_ref, quarter_sql)
        page["quarter"] = self._quarter_table(
            holding_refs, extra, calculator, quarter_sql, holdings_ref)

        if page["solvable"] and net_sql is not None:
            pairs = switch_date_pairs(net_sql, _call(holdings_ref, "get_stock_id"))
            page["exhaustive"] = self._exhaustive_table(
                holding_refs, calculator, pairs, num)
            page["verify"] = self._verify_table(holding_refs, calculator, pairs)
        return page

    # -- 四张表 -----------------------------------------------------------
    @staticmethod
    def _current_table(holding_refs: list, calculator: ExhaustiveHoldingsCalculator) -> dict:
        cells, background = calculator.current_row()
        return {
            "id": "current",
            "title": "当前使用的持仓数据",
            "columns": build_current_columns(holding_refs),
            "rows": [_row(cells, background=background)],
            "count": 1,
        }

    @staticmethod
    def _quarter_table(
        holding_refs: list,
        extra: dict,
        calculator: ExhaustiveHoldingsCalculator,
        quarter_sql: Any,
        holdings_ref: Any,
    ) -> Optional[dict]:
        """季报表。季报表为空时**整段不出现**（PHP 的 ``if ($result = ...)``）。"""
        if quarter_sql is None:
            return None
        records = quarter_sql.get_all(_call(holdings_ref, "get_stock_id")) or []
        if not records:
            return None
        rows = []
        for record in records:
            cells, match = calculator.quarter_row(
                record.get("date"), record.get("close"), extra)
            rows.append(_row(cells, match=match))
        return {
            "id": "quarter",
            "title": QUARTER_REPORT_DISPLAY,
            "columns": build_quarter_columns(
                holding_refs,
                {stock_id: _stock_symbol(calculator.ref, stock_id) for stock_id in extra},
            ),
            "rows": rows,
            "count": len(rows),
        }

    @staticmethod
    def _exhaustive_table(
        holding_refs: list,
        calculator: ExhaustiveHoldingsCalculator,
        pairs: list,
        num: int,
    ) -> Optional[dict]:
        rows = []
        for date, net_value, prev_date in pairs:
            if num and len(rows) >= num:
                break
            cells, match = calculator.exhaustive_row(date, net_value, prev_date)
            rows.append(_row(cells, match=match))
        if not rows:
            return None
        return {
            "id": EXHAUSTIVE_TABLE_ID,
            "title": EXHAUSTIVE_TITLE,
            "columns": build_exhaustive_columns(holding_refs),
            "rows": rows,
            "count": len(rows),
        }

    @staticmethod
    def _verify_table(
        holding_refs: list,
        calculator: ExhaustiveHoldingsCalculator,
        pairs: list,
    ) -> Optional[dict]:
        rows = []
        for date, net_value, prev_date in pairs:
            cells, match, done = calculator.verify_row(date, net_value, prev_date)
            rows.append(_row(cells, match=match))
            if done:
                break
        if not rows:
            return None
        return {
            "id": "verify",
            "title": "验证最激进数据",
            "columns": build_verify_columns(holding_refs),
            "rows": rows,
            "count": len(rows),
        }

    # -- 取数 -------------------------------------------------------------
    def _build_holdings_ref(self, symbol: str) -> Any:
        """``new HoldingsReference($strSymbol)``；连不上库时返回 ``None``。"""
        try:
            return StockGetHoldingsReference(symbol, source=self.source)
        except Exception:
            return None


def _stock_symbol(holdings_ref: Any, stock_id: Any) -> str:
    """季报独有标的一列的名字：``SqlGetStockSymbol($strHoldingId)``。

    查不到符号就退回 ``#id`` —— 页面上留个可见的占位比空列头好排查。
    """
    source = getattr(holdings_ref, "source", None)
    getter = getattr(source, "get_stock_symbol", None)
    if callable(getter):
        try:
            symbol = getter(stock_id)
            if symbol:
                return str(symbol)
        except Exception:
            pass
    return "#%s" % stock_id


def _call(target: Any, name: str) -> Any:
    method = getattr(target, name, None)
    return method() if callable(method) else None


def build_exhaustive_page(
    symbol: str,
    source: Any = None,
    threshold: Optional[float] = None,
    num: int = DEFAULT_EXHAUSTIVE_ROWS,
    admin: bool = False,
) -> Optional[dict]:
    """一步到位的便捷函数（路由与测试都用它）。"""
    return ExhaustiveHoldingsService(source).get_page(
        symbol, threshold=threshold, num=num, admin=admin)


#: 表 2/3/4 都提到"仓位"这一列的标题，导出一份给模板写说明用。
POSITION_LABEL = STOCK_DISP_POSITION
