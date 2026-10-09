"""参考数据表服务。

对应 PHP ``php/ui/referenceparagraph.php`` 的 ``EchoReferenceParagraph()``。

PHP 那一份是"边拼 HTML 边 echo"：

.. code-block:: php

    $ar = array_merge(array($ref->GetExternalLink()), GetStockReferenceArray($ref));
    $strFirstHint = array_pop($ar);        // 把名称拎出来...
    $ar[] = $strDescription;               // ...换成说明文字
    EchoTableColumn($ar, false, $strFirstHint);   // 名称降级成首格的 title 提示

本版只产出 **数据**（``columns`` + ``rows``），渲染交给模板；颜色不再是
``<font color>`` 片段而是随单元格返回的颜色名。这样同一份数据既能给页面用，
也能给 JSON API 用 —— PHP 那边想复用到接口上就得先正则剥标签。

## 一个必须知道的口径冲突（尚未裁决）

``涨幅`` 这一格的染色规则来自 ``php/stock/stockref.php:147``：

.. code-block:: php

    if (str_ends_with($strDisp, '%'))  $strColor = str_starts_with($strDisp, '-') ? 'red' : 'black';
    else                               $strColor = 'gray';

也就是 **跌红、涨黑**。但同一个 PHP 代码库里 ``StockGetPriceDisplay()``
（``php/stock.php:91``）的默认参数是 ``($strGreater='red', $strLess='green')``，
即 **涨红跌绿**；``python/web`` 的分组列表页用的也是后者。

两份实现**对于"跌该是什么颜色"给出了相反答案**。本模块默认**照抄 PHP 原文**
（见 :func:`percentage_cell`），因为这是迁移而不是 redesign ——
但这个冲突需要裁决，详见 ``README_ui_column_migration.md`` 的已知偏差第 1 条。
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from web.bootstrap import install_paths

install_paths()

from historytable import TableRow  # noqa: E402
from uicolumn import (  # noqa: E402
    columns_to_dicts,
    column_base,
    column_change,
    column_date,
    column_name,
    column_price,
    column_symbol,
    column_time,
    ref_has_data,
    table_column_get_last_width,
)


#: PHP 最后一列的名称列宽 ``new TableColumnName(false, 270)``
#: —— 名称是这一屏的唯一参照物，所以给到 270px。
REFERENCE_NAME_WIDTH = 270

#: 行的说明文字（盘前/盘后）在 PHP 里是 ``<i><font color=gray>…</font></i>``，
#: 这里只保留文本，倾斜与灰色交给 CSS。
REFERENCE_SESSION_COLOR = "gray"


def percentage_cell(text: Any) -> tuple:
    """把涨跌幅文本转成 ``(文本, 颜色)`` 单元格。

    规则逐字照抄 ``php/stock/stockref.php:147``：以 ``-`` 开头是 ``red``，
    带 ``%`` 的其它情况 ``black``，**没有 ``%`` 号是 ``gray``**（那时候
    ``GetPercentageText()`` 返回的是空串，表示该标的压根没有昨收可比）。
    """
    displayed = "" if text is None else str(text)
    if not displayed.endswith("%"):
        return displayed, REFERENCE_SESSION_COLOR
    return displayed, ("red" if displayed.startswith("-") else "black")


def _cell(value: Any) -> Any:
    """空值统一成空串，避免模板里出现 ``None``。"""
    return "" if value is None else value


def build_reference_columns(admin: bool = False, layout_width: int = None) -> list:
    """参考数据表的列定义。

    对应 PHP ``EchoReferenceParagraph()`` 里那段有点绕的组装：

    .. code-block:: php

        $ar = array_merge(array(new TableColumnSymbol()), GetStockReferenceColumn());
        array_pop($ar);                              // 丢掉默认的「名称」列
        $ar[] = new TableColumnName(false, 270);     // 换成更宽的名称列
        if ($bAdmin) $ar[] = new TableColumn('调试数据', TableColumnGetLastWidth($ar));

    **为什么要 pop 再 append**：``GetStockReferenceColumn()`` 返回的名称列宽是
    默认的 150，而参考数据表要 270（它是页面的主要参照物）。本版直接构造，
    不重复"先塞进去再扔掉"的动作 —— 结果一致。
    """
    columns = [
        column_symbol(),
        column_price(),
        column_change(),
        column_date(),
        column_time(),
        column_name(width=REFERENCE_NAME_WIDTH),
    ]
    if admin:
        # PHP：TableColumnGetLastWidth() 依赖全局 LayoutGetDisplayWidth()，
        # 这里改成显式入参（见 uicolumn.table_column_get_last_width）。
        width = table_column_get_last_width(columns, layout_width) if layout_width else 0
        columns.append(column_base("调试数据", width))
    return columns_to_dicts(columns)


def get_stock_reference_cells(ref: Any) -> list:
    """对应 ``GetStockReferenceArray()``：中间四格（价格 / 涨幅 / 日期 / 时间）。

    **没有行情时要输出四个空串而不是省略** —— PHP 原样走的 else 分支，
    因为表格必须列数对齐，少一格整行就会串位。
    """
    if not ref_has_data(ref):
        return ["", "", "", ""]
    return [
        _cell(ref.get_price_display()),
        percentage_cell(ref.get_percentage_text()),
        _cell(ref.get_date()),
        _cell(ref.get_time_hm()),
    ]


class ReferenceService:
    """把一批引用对象整理成"参考数据"表格。

    ``source`` 只用来查股票中文名（对应 PHP 的 ``SqlGetStockName()``）；
    可以传 ``None``，那时名称列为空。
    """

    def __init__(self, source: Any = None, layout_width: int = None):
        self.source = source
        #: 布局总宽度，管理员调试列要靠它算剩余宽度
        self.layout_width = layout_width

    # -- 对外 -------------------------------------------------------------
    def get_table(
        self,
        refs: Iterable[Any],
        wide: bool = False,
        admin: bool = False,
        title: str = None,
    ) -> dict[str, Any]:
        """整理成 ``{"columns", "rows", "title", "count"}``。

        ==========================  =======================================
        参数                         含义
        ==========================  =======================================
        ``refs``                     引用对象列表，**允许含 ``None``**（跳过）
        ``wide``                     PHP 的 ``$bWide``：宽屏时名称做成外链
        ``admin``                    追加"调试数据"列（只有超管看得到）
        ``title``                    表格标题，默认"参考数据"
        ==========================  =======================================
        """
        rows = []
        for ref in refs:
            if ref is None:
                continue
            rows.append(self._row(ref, None, admin, wide))
            extended = getattr(ref, "extended_ref", None)
            if extended is not None:
                rows.append(self._row(extended, self._session_of(extended), False, wide))

        return {
            "title": title or "参考数据",
            "columns": build_reference_columns(admin=admin, layout_width=self.layout_width),
            "rows": rows,
            "count": len(rows),
        }

    # -- 内部 -------------------------------------------------------------
    def _row(self, ref: Any, description: Any, admin: bool, wide: bool) -> TableRow:
        """对应 ``_echoReferenceTableItem()``。

        PHP 的三个动作这里一一对应：

        1. 首格永远是外链（``GetExternalLink()``）；
        2. 中间四格来自 :func:`get_stock_reference_cells`；
        3. 有 ``description`` 时，**名称被降级成首格的 tooltip**，
           最后一格换成说明文字 —— 这是 PHP ``array_pop`` 那段的效果。
        """
        row = TableRow()
        row.add(_cell(ref.get_external_link()))
        for cell in get_stock_reference_cells(ref):
            row.add(cell)

        name = self._name_of(ref)
        if description:
            row.title = name
            row.add((_cell(description), REFERENCE_SESSION_COLOR))
        else:
            row.add(name)

        if admin:
            row.add(_cell(ref.debug_link()))
        return row

    def _name_of(self, ref: Any) -> str:
        """对应 ``SqlGetStockName($ref->GetSymbol())``。

        **已知偏差**：宽屏（``$bWide``）时 PHP 会把名称做成雪球外链
        （``GetXueqiuLink()``，``php/externallink.php:131``）。外链函数族属于
        优先级列表第 5 步、未迁移，所以这里**始终返回纯文本名称**，
        由模板决定要不要加链接。
        """
        if self.source is None:
            return ""
        try:
            return _cell(self.source.get_stock_name(ref.get_symbol()))
        except Exception:
            # 查名字失败不该让整张表挂掉
            return ""

    @staticmethod
    def _session_of(ref: Any) -> str:
        """盘前/盘后说明文字。PHP 是 ``GetMarketSession()``。"""
        getter = getattr(ref, "get_market_session", None)
        if getter is None:
            return ""
        try:
            return _cell(getter())
        except Exception:
            return ""


def build_stock_references(symbols: Iterable[str], quote_loader: Any = None) -> list:
    """为每个符号建一个 :class:`stockref.StockReference` 并**尽力**加载行情。

    对应 PHP 页面里的 ``new StockReference($strSymbol)`` —— PHP 那时会同步抓新浪。
    差别有两条，都是有意的：

    1. **构造不联网**（``StockReference.__init__`` 只组装），加载是这里显式调用
       ``load_sina_data()`` 的结果；
    2. **加载失败不影响页面**。``load_sina_data()`` 取不到数据时把 ``has_data``
       置为 ``False``，参考数据表会按 PHP 的 else 分支输出四个空串，
       表格照样列数对齐。所以用 ``try`` 包住是保守做法 —— 页面不该因为
       某个标的没抓到就整体报错。

    ``quote_loader`` 用来顶掉"取行情原始文本"这一步（测试用）。
    """
    from stockref import StockReference

    refs = []
    for symbol in symbols:
        ref = StockReference(symbol, quote_loader=quote_loader)
        refs.append(_load_best_effort(ref))
    return refs


def _load_best_effort(ref: Any) -> Any:
    """加载行情；失败就把 ``has_data`` 置为 ``False`` 并返回同一个对象。"""
    try:
        ref.load_sina_data()
    except Exception:
        try:
            ref.set_has_data(False)
        except Exception:
            pass
    return ref


def build_reference_table(
    refs: Sequence[Any],
    source: Any = None,
    wide: bool = False,
    admin: bool = False,
    layout_width: int = None,
    title: str = None,
) -> dict[str, Any]:
    """一步到位的便捷函数（不需要复用 service 实例时用这个）。"""
    return ReferenceService(source, layout_width=layout_width).get_table(
        refs, wide=wide, admin=admin, title=title
    )


def default_reference_service_factory() -> ReferenceService:
    """创建使用当前请求数据库连接的参考数据服务（名称从库里查）。"""
    from stockdata import MySqlFundDataSource
    from web.db import get_db

    return ReferenceService(MySqlFundDataSource(get_db()))
