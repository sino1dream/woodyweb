"""基金估值表 —— 对应 ``php/ui/fundestparagraph.php``（174 行）。

这是 ``php/ui`` 里**被引用最多**的一个段落：``_qdii.php`` / ``_qdiieu.php`` /
``_qdiieu.php`` / ``_qdiimix.php`` / ``_chinafuture.php`` / ``_chinaindex.php`` /
``_mystock.php`` / ``_mystockgroup.php`` / ``holdingscn.php`` 都在用它，
是"官方 / 参考 / 实时"三档估值的主展示位。

## 表头是**扫一遍所有 ref** 决定的

.. code-block:: php

    $bFair = false;
    foreach ($arRef as $ref) if ($ref->GetFairNetValue())     { $bFair = true; $ar[] = ...; break; }
    foreach ($arRef as $ref) if ($ref->GetRealtimeNetValue()) { $ar[] = ...; break; }

即"**只要有任意一个标的有参考估值，就给所有人加两列**"。这是为了让表格
列数统一 —— 但只有"参考估值"那一档做了补齐，见下。

## ⚠️ 实时估值那一档少一次补齐（PHP 的错位 bug）

行里对"参考估值"是这样处理的：

.. code-block:: php

    if ($fFairPrice = $ref->GetFairNetValue())  { $ar[] = ...; $ar[] = ...; }
    else if ($bFair)                            { $ar[] = ''; $ar[] = ''; }   // ← 补齐

而"实时估值"只有 if、**没有 else**：

.. code-block:: php

    if ($fRealtimePrice = $ref->GetRealtimeNetValue())  { $ar[] = ...; $ar[] = ...; }

所以"只要有任何一个标的有实时估值"（列因此加了两列），所有**没有**实时估值的
标的那一行就会**少两格** —— 后面的单元格整体左移。

本版照抄（不静默补空格，那等于编造两格空白掩盖错位），并把 ``cell_count``
与 ``columns`` 一起返回，让调用方能自己发现。测试
``RowLengthTest`` 把这条钉住。

## 排序只在"默认标题 + 超过 2 行"时发生

``_echoFundEstParagraph()`` 里那段 ``if ($str === false)`` 的意思是：
**只有调用方没指定标题时**才接管标题、才考虑排序。排序分支又包在
``if ($iCount > 2)`` 里。两个条件都满足才排。

排序键是**溢价率**（``_callbackSortFundEst``）：

.. code-block:: php

    $fNetValue = $ref->GetOfficialNetValue();
    if (method_exists($ref, 'GetStockRef'))     return $ref->GetStockRef()->GetPercentage($fNetValue);
    return $ref->GetPercentage($fNetValue);

``GetPercentage($fNetValue)`` 以净值为除数、现价为被除数，
即 ``(价格/净值 - 1) * 100``，正是溢价率。

## 溢价这一格的颜色

PHP 用的是 ``GetPercentageDisplay()``，也就是"跌红涨黑"。本版沿用
**已裁决的全站统一口径"涨红跌绿"**（见 ``README_ui_column_migration.md``
第四节）—— 于是溢价为正红、为负绿，与分组列表页的 ``premium_class``
（``negative if premium < 0 else positive``）**终于一致了**。
PHP 那边这两处本来是相反的。
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Sequence

from web.bootstrap import install_paths

install_paths()

from sql.historytable import TableRow  # noqa: E402
from ui.uicolumn import (  # noqa: E402
    STOCK_DISP_EST,
    columns_to_dicts,
    column_change,
    column_date,
    column_name,
    column_official_est,
    column_fair_est,
    column_premium,
    column_price,
    column_realtime_est,
    column_symbol,
    column_time,
    ref_has_data,
    ref_sort_by_numeric,
    ref_sort_by_symbol,
)

from web.services.reference_service import (  # noqa: E402
    get_stock_reference_cells,
    percentage_cell,
    stock_name_of,
)

#: PHP ``EchoTableParagraphBegin($arColumn, 'estimation', $str)`` 的 ``$strId``。
FUND_EST_TABLE_ID = "estimation"

#: ``if ($iCount > 2)`` —— 超过这么多行才考虑排序。
FUND_EST_SORT_THRESHOLD = 2

#: 排序键。PHP 从查询串 ``UrlGetQueryValue('sort')`` 里取这两个值。
SORT_BY_SYMBOL = "symbol"
SORT_BY_PREMIUM = "premium"


def build_fund_est_columns(refs: Iterable[Any] = (), wide: bool = False) -> tuple:
    """列定义 + 两档估值是否存在。

    返回 ``(columns, has_fair, has_realtime)``。后两个是行的组装依据 ——
    PHP 用引用参数 ``&$bFair`` 把它们带出来，这里直接返回，少一个副作用。

    ======================  ==============================================
    列                       条件
    ======================  ==============================================
    代码                     恒有
    价格/涨幅/日期/时间/名称   ``wide`` 为真
    官方EST / EST日期 / 溢价   恒有
    参考EST / 溢价             **任意一个** ref 有参考估值
    实时EST / 溢价             **任意一个** ref 有实时估值
    ======================  ==============================================
    """
    columns = [column_symbol()]
    if wide:
        columns += [
            column_price(),
            column_change(),
            column_date(),
            column_time(),
            column_name(),
        ]

    columns += [column_official_est(), column_date(STOCK_DISP_EST), column_premium()]

    refs = list(refs)
    has_fair = any(_has(ref, "get_fair_net_value", "GetFairNetValue") for ref in refs)
    if has_fair:
        columns += [column_fair_est(), column_premium()]

    has_realtime = any(_has(ref, "get_realtime_net_value", "GetRealtimeNetValue") for ref in refs)
    if has_realtime:
        columns += [column_realtime_est(), column_premium()]

    return columns_to_dicts(columns), has_fair, has_realtime


class FundEstService:
    """把一批基金引用对象整理成估值表。"""

    def __init__(self, source: Any = None):
        #: 只用来查股票中文名（``SqlGetStockName()``），可为 ``None``
        self.source = source

    # -- 对外 -------------------------------------------------------------
    def get_table(
        self,
        refs: Iterable[Any],
        wide: bool = False,
        title: str = None,
        sort: str = None,
    ) -> Any:
        """整理成 ``{"columns", "rows", ...}``。

        ``sort`` 取 :data:`SORT_BY_SYMBOL` / :data:`SORT_BY_PREMIUM` / ``None``。
        排序**只在超过 2 行时**生效（``if ($iCount > 2)``）。

        **没有任何一行可显示时返回 ``None``** —— 这是本版与 PHP 的一处
        刻意差异：PHP 会照常渲染一张只有表头的空表，而基金详情页
        （``/fund/<symbol>``）上的既定做法是"宁可整块不出现，也不要摆一张
        空表让人以为数据丢了"。
        """
        # 先固化成 list：``refs`` 可能是生成器，而下面要用它两次
        # （一次决定列、一次决定行），生成器只能遍历一遍。
        refs = [ref for ref in refs if ref is not None]

        columns, has_fair, has_realtime = build_fund_est_columns(refs, wide=wide)

        if len(refs) > FUND_EST_SORT_THRESHOLD:
            refs = self._sorted(refs, sort)

        rows = []
        for ref in refs:
            row = self._row(ref, wide, has_fair)
            if row is not None:
                rows.append(row)

        if not rows:
            return None

        return {
            "id": FUND_EST_TABLE_ID,
            "title": title or "",
            "columns": columns,
            "rows": rows,
            "count": len(rows),
            "has_fair": has_fair,
            "has_realtime": has_realtime,
            #: 最长的一行有多少格 —— 用来发现"实时估值少两格"那类错位
            "cell_count": max(len(row.cells) for row in rows),
        }

    # -- 内部 -------------------------------------------------------------
    def _sorted(self, refs: list, sort: str) -> list:
        """``if ($iCount > 2)`` 里的两个排序分支。"""
        if sort == SORT_BY_SYMBOL:
            return ref_sort_by_symbol(refs)
        if sort == SORT_BY_PREMIUM:
            return ref_sort_by_numeric(refs, _premium_of)
        return refs

    def _row(self, ref: Any, wide: bool, has_fair: bool) -> Any:
        """对应 ``_echoFundEstTableItem()``。

        **没有行情的 ref 整行跳过** —— PHP 开头那句
        ``if (RefHasData($ref) == false) return;``。返回 ``None`` 表示跳过。
        """
        if not ref_has_data(ref):
            return None

        row = TableRow()
        row.add(_text(_invoke(ref, "get_stock_link", "GetStockLink")))

        if wide:
            # PHP 取的是 $ref->GetStockRef()，不是 $ref 自己 ——
            # 估值列的百分比要以"场内价格 vs 净值"算，那是 stock_ref 的职责。
            stock_ref = _invoke(ref, "get_stock_ref", "GetStockRef")
            for cell in get_stock_reference_cells(stock_ref if stock_ref is not None else ref):
                row.add(cell)
            row.add(stock_name_of(self.source, ref))
        else:
            row.title = stock_name_of(self.source, ref)

        row.add(_text(_invoke(ref, "get_price_display", "GetPriceDisplay", self._official(ref))))
        row.add(_text(_invoke(ref, "get_official_date", "GetOfficialDate")))
        row.add(percentage_cell(_invoke(ref, "get_percentage_text", "GetPercentageText", self._official(ref))))

        fair = _invoke(ref, "get_fair_net_value", "GetFairNetValue")
        if fair:
            row.add(_text(_invoke(ref, "get_price_display", "GetPriceDisplay", fair)))
            row.add(percentage_cell(_invoke(ref, "get_percentage_text", "GetPercentageText", fair)))
        elif has_fair:
            # PHP 对"参考估值"这一档做了补齐 —— 实时那一档没有，见模块 docstring
            row.add("")
            row.add("")

        realtime = _invoke(ref, "get_realtime_net_value", "GetRealtimeNetValue")
        if realtime:
            row.add(_text(_invoke(ref, "get_price_display", "GetPriceDisplay", realtime)))
            row.add(percentage_cell(_invoke(ref, "get_percentage_text", "GetPercentageText", realtime)))

        return row

    @staticmethod
    def _official(ref: Any) -> Any:
        return _invoke(ref, "get_official_net_value", "GetOfficialNetValue")


def build_fund_est_table(
    refs: Sequence[Any],
    wide: bool = False,
    title: str = None,
    sort: str = None,
    source: Any = None,
) -> Any:
    """一步到位的便捷函数（不需要复用 service 实例时用这个）。"""
    return FundEstService(source).get_table(refs, wide=wide, title=title, sort=sort)


def default_fundest_service_factory() -> FundEstService:
    """创建使用当前请求数据库连接的估值表服务（名称从库里查）。"""
    from core.stockdata import MySqlFundDataSource
    from web.db import get_db

    return FundEstService(MySqlFundDataSource(get_db()))


def _premium_of(ref: Any) -> Any:
    """``_callbackSortFundEst()``：以**官方估值**为除数的溢价率。

    优先用 ``GetStockRef()``（PHP 里 ``method_exists`` 那一支），
    拿不到就退回 ref 自己。
    """
    net_value = _invoke(ref, "get_official_net_value", "GetOfficialNetValue")
    stock_ref = _invoke(ref, "get_stock_ref", "GetStockRef")
    target = stock_ref if stock_ref is not None else ref
    return _invoke(target, "get_percentage", "GetPercentage", net_value)


def _has(ref: Any, snake: str, camel: str) -> bool:
    """某个 ref 是否**真的有**这一档估值（要求值非空）。"""
    return bool(_invoke(ref, snake, camel))


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


def _text(value: Any) -> str:
    return "" if value is None else str(value)
