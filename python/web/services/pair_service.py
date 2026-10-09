"""A/B、A/H、ADR/H 配对比较表 —— 对应 ``php/ui/ahparagraph.php``（94 行）。

一个文件里有**三个**段落函数（``EchoAbParagraph`` / ``EchoAhParagraph`` /
``EchoAdrhParagraph``），三者结构完全一样，只有尾部四（或五）列不同：

.. code-block:: php

    function EchoAhParagraph($arRef, $bWide = false)
    {
        $str = GetAhCompareLink();
        if (count($arRef) == 1) $str .= ' '.GetAhHistoryLink($arRef[0]->GetSymbol());
        $ar = array_merge(_getPairColumn($bWide),
                array(new TableColumnSymbol(STOCK_DISP_HSHARES),      // H股代码
                      new TableColumnPrice(STOCK_DISP_HSHARES),       // H股价格
                      new TableColumnRMB(STOCK_DISP_HSHARES),         // H股人民币￥
                      new TableColumnRatio('A/H'))                    // A/H比价
                );
        _echoPairParagraph($ar, 'ah', $str, $arRef, $bWide);
    }

所以本模块不是"三个函数"，而是**一张列定义表 + 一套行组装**，
``kind`` 决定用哪一套尾部列。

## 行的结构是**变长**的

``_echoPairItem()`` 按"有什么就追加什么"的方式拼行，最多四段：

1. 本标的链接（恒有）；
2. 宽屏时才有的五格行情（``GetStockReferenceArray``）；
3. **有配对标的才有**的三格：配对链接 / 配对价格 / 由配对价反推的估值；
4. **比价非 0 才有**的两格：比价 / 仓位。

PHP 就这样直接 echo 出去，行短了就少几个 ``<td>`` —— 这在 HTML 里不会报错，
但会让列错位。**本版保留变长行为**（这是 PHP 的真实行为），同时把
``count`` / ``cell_count`` 一并返回，让模板或调用方能看出来。

## ⚠️ 缺什么：配对引用对象还没迁

这三个段落消费的 ``AbPairReference`` / ``AhPairReference`` / ``AdrPairReference``
都继承自 ``MyPairReference``（``php/stock/fundpairref.php:3``，属于 ``php/stock``
层，**尚未迁移**）。那个类提供本段落要用的四个方法：
``GetPairRef()`` / ``EstFromPair()`` / ``GetPriceRatio()`` / ``GetCnyRef()``。

**未迁移的后果**：拿到尚未实现这些方法的 ref 时，第 3、4 段整体不输出，
行会退化成只有第 1 段（宽屏时加第 2 段）。这与 PHP 里"配对不存在"
（``$pair_ref = false``）的表现一致，所以不算错，只是**数据不全**。

等 ``MyPairReference`` 迁过来，本模块**不需要改动** —— 它通过
:func:`_invoke` 按方法名取数，方法出现了就会自动填上。

## 两个行为细节

* **窄屏时名称变成首格 tooltip** —— ``RefEchoTableColumn()`` 在
  ``$bWide`` 为假时把 ``SqlGetStockName()`` 挂到首格 ``title`` 上。
  本版用 ``TableRow.title`` 承载，与参考数据表一致。
* **超过 2 行才排序** —— ``if ($iCount > 2)`` 按 ``GetPriceRatio()`` 升序。
  只有两行时保持传入顺序（作者认为两行不值得排序）。
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Sequence

from web.bootstrap import install_paths

install_paths()

from historytable import TableRow, format_price_display  # noqa: E402
from stockdata import rtrim0  # noqa: E402
from uicolumn import (  # noqa: E402
    STOCK_DISP_BSHARES,
    STOCK_DISP_HSHARES,
    columns_to_dicts,
    column_change,
    column_date,
    column_name,
    column_position,
    column_price,
    column_rmb,
    column_ratio,
    column_symbol,
    column_time,
    column_usd,
    ref_sort_by_numeric,
)

from web.services.reference_service import (  # noqa: E402
    get_stock_reference_cells,
    stock_name_of,
)

#: 三种配对。值与 PHP 传给 ``EchoTableParagraphBegin()`` 的 ``$strId`` 一致。
PAIR_KINDS = ("ab", "ah", "adr")

#: PHP 的表 id 是 ``{$strId}table``。
PAIR_TABLE_ID_SUFFIX = "table"

#: 超过这么多行才排序（``ahparagraph.php:36`` 的 ``if ($iCount > 2)``）。
PAIR_SORT_THRESHOLD = 2


def build_pair_columns(kind: str = "ah", wide: bool = False) -> list:
    """列定义，对应 ``_getPairColumn()`` + 各段落尾部那几列。

    =========================  ================================================
    ``kind``                    尾部列
    =========================  ================================================
    ``"ab"``                    B股代码 / B股价格 / B股人民币￥ / A/B比价
    ``"ah"``                    H股代码 / H股价格 / H股人民币￥ / A/H比价
    ``"adr"``                   H股代码 / H股价格 / H股美元$ / ADR/H比价 / 仓位
    =========================  ================================================

    ``wide`` 为真时中间插入 ``GetStockReferenceColumn()`` 那五列
    （价格 / 涨幅 / 日期 / 时间 / 名称），对应 ``_getPairColumn()`` 里的
    ``array_merge``。

    ``"adr"`` 比另外两种**多一列仓位**。原因在 PHP 的语义里：ADR 与 H 股之间
    存在可套利的对冲关系，仓位是算对冲比例的输入；A/B 与 A/H 没有这一层。
    """
    columns = [column_symbol()]
    if wide:
        columns += _stock_reference_columns()

    if kind == "ab":
        columns += _h_or_b_block(STOCK_DISP_BSHARES, "A/B")
    elif kind == "ah":
        columns += _h_or_b_block(STOCK_DISP_HSHARES, "A/H")
    elif kind == "adr":
        # ADR 用美元计价，所以第三列是 USD 而不是 RMB，并多一列仓位
        columns += [
            column_symbol(STOCK_DISP_HSHARES),
            column_price(STOCK_DISP_HSHARES),
            column_usd(STOCK_DISP_HSHARES),
            column_ratio("ADR/H"),
            column_position(),
        ]
    else:
        raise ValueError("未知的配对类型 %r，可选 %s" % (kind, PAIR_KINDS))

    return columns_to_dicts(columns)


def _stock_reference_columns() -> list:
    """``GetStockReferenceColumn()``（``referenceparagraph.php:78``）的五列。"""
    return [
        column_price(),
        column_change(),
        column_date(),
        column_time(),
        column_name(),
    ]


def _h_or_b_block(prefix: str, ratio_prefix: str) -> list:
    """A/B 与 A/H 共用的尾部四列（只有前缀和比价名不同）。"""
    return [
        column_symbol(prefix),
        column_price(prefix),
        column_rmb(prefix),
        column_ratio(ratio_prefix),
    ]


class PairService:
    """把一批配对引用对象整理成比较表。"""

    def __init__(self, source: Any = None, position_reader: Callable[[Any], Any] = None):
        #: 只用来查股票中文名（``SqlGetStockName()``），可为 ``None``
        self.source = source
        #: 读仓位的回调，对应 ``$pos_sql->ReadVal($ref->GetStockId(), true)``
        self.position_reader = position_reader

    # -- 对外 -------------------------------------------------------------
    def get_table(
        self,
        refs: Iterable[Any],
        kind: str = "ah",
        wide: bool = False,
        title: str = "",
    ) -> Any:
        """整理成 ``{"columns", "rows", ...}``；**没有 ref 时返回 ``None``**。

        返回 ``None`` 而不是空表，是照抄 ``_echoPairParagraph()`` 的开头：

        .. code-block:: php

            $iCount = count($arRef);
            if ($iCount == 0)   return;      // 整段不输出

        模板侧因此要写 ``{% if pair %}`` —— 宁可整块不出现，
        也不要摆一张只有表头没有数据的表。
        """
        refs = [ref for ref in refs if ref is not None]
        if not refs:
            return None

        if len(refs) > PAIR_SORT_THRESHOLD:
            refs = ref_sort_by_numeric(refs, _price_ratio_of)

        rows = [self._row(ref, wide) for ref in refs]
        return {
            "kind": kind,
            "id": kind + PAIR_TABLE_ID_SUFFIX,
            "title": title,
            "columns": build_pair_columns(kind=kind, wide=wide),
            "rows": rows,
            "count": len(rows),
            #: 最长的一行有多少格 —— 变长行时用来判断是否缺数据
            "cell_count": max(len(row.cells) for row in rows),
        }

    # -- 内部 -------------------------------------------------------------
    def _row(self, ref: Any, wide: bool) -> TableRow:
        """对应 ``_echoPairItem()`` 的四段拼装。"""
        row = TableRow()
        row.add(_text(_invoke(ref, "get_my_stock_link", "GetMyStockLink")))

        if wide:
            for cell in get_stock_reference_cells(ref):
                row.add(cell)
            row.add(stock_name_of(self.source, ref))
        else:
            # RefEchoTableColumn(): 窄屏时名称降级成首格 tooltip
            row.title = stock_name_of(self.source, ref)

        pair_ref = _invoke(ref, "get_pair_ref", "GetPairRef")
        if pair_ref is None:
            return row

        row.add(_text(_invoke(pair_ref, "get_my_stock_link", "GetMyStockLink")))
        row.add(_text(_invoke(pair_ref, "get_price_display", "GetPriceDisplay")))
        row.add(self._est_cell(ref, pair_ref))

        # PHP 是 if ($fRatio = $ref->GetPriceRatio()) —— 1.0 也算真
        ratio = _invoke(ref, "get_price_ratio", "GetPriceRatio")
        if not ratio:
            return row
        row.add(format_price_display(ratio, 1.0, 4))

        position = self._position_of(ref)
        if position:
            row.add(rtrim0(position))
        return row

    def _est_cell(self, ref: Any, pair_ref: Any) -> str:
        """``$ref->GetPriceDisplay($ref->EstFromPair(配对价, 汇率))``。

        三步缺一不可，任一步拿不到就返回空串 —— **不编造估值**。
        PHP 在这里没有容错（``$cny_ref->GetVal()`` 会直接 fatal），
        本版改成"拿不到就留空"，因为页面不该因为一个标的缺汇率就整段报错。
        """
        pair_price = _invoke(pair_ref, "get_price", "GetPrice")
        cny_ref = _invoke(ref, "get_cny_ref", "GetCnyRef")
        # PHP 的 EstFromPair($fPairVal, false) 内部会回落到 GetDefaultCny() = 1.0
        cny = _invoke(cny_ref, "get_val", "GetVal") if cny_ref is not None else None

        est = _invoke(ref, "est_from_pair", "EstFromPair", pair_price, cny)
        if est is None:
            return ""
        return _text(_invoke(ref, "get_price_display", "GetPriceDisplay", est))

    def _position_of(self, ref: Any) -> Any:
        """``$pos_sql->ReadVal($ref->GetStockId(), true)``。"""
        if self.position_reader is None:
            return None
        stock_id = _invoke(ref, "get_stock_id", "GetStockId")
        if stock_id is None:
            return None
        try:
            return self.position_reader(stock_id)
        except Exception:
            # 读仓位失败不该让整张表挂掉
            return None


def build_pair_table(
    refs: Sequence[Any],
    kind: str = "ah",
    wide: bool = False,
    title: str = "",
    source: Any = None,
    position_reader: Callable[[Any], Any] = None,
) -> Any:
    """一步到位的便捷函数（不需要复用 service 实例时用这个）。"""
    return PairService(source, position_reader).get_table(
        refs, kind=kind, wide=wide, title=title
    )


def _price_ratio_of(ref: Any) -> Any:
    """``_callbackSortPair()``（``ahparagraph.php:3``）：按比价升序。"""
    return _invoke(ref, "get_price_ratio", "GetPriceRatio")


def _invoke(ref: Any, snake: str, camel: str, *args: Any) -> Any:
    """按 snake_case 优先、camelCase 兜底调用 ref 上的方法。

    **取不到就返回 ``None``**，不抛异常 —— 这是本模块能容忍
    ``MyPairReference`` 尚未迁移的关键：方法一出现，格子自动填上。
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


def _text(value: Any) -> str:
    return "" if value is None else str(value)
