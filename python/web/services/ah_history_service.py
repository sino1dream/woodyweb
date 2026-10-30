"""AH 历史价格比较页 —— 对应 ``woody/res/php/_ahhistory.php``（103 行）。

页面按**A 股自己的交易日**逐日列出三样东西：港币人民币中间价、A 股收盘价、
H 股收盘价，再算出 A/H 与 H/A 两个比价。

## 与 PHP 逐句对应的三个要点

1. **港币中间价取不到就整天跳过**（``if ($strHKCNY = $cny_ref->GetClose($strDate))``）。
   所以这张表的行数通常少于 ``stockhistory`` 的行数 —— 中间价表缺哪天，
   哪天就不出现；
2. **H 股收盘价取不到时只显示前三格**；有 H 价才追加收盘价与两个比价
   （``if ($strCloseH = $h_ref->GetClose($strDate))``），行是**变长**的；
3. 比价是 ``$ref->GetPriceRatio($strDate)``，H/A 直接取倒数；
4. **只有 A 股收盘价过 ``rtrim0``** —— 中间价与 H 价是 ``GetClose()`` 的原样。

## 已知偏差

* **不画走势图、不生成 CSV 下载**。PHP 用 ``DateImageFile`` + ``PageCsvFile``，
  这两样属于渲染/文件层，本版先只迁移表格数据；图与 CSV 后面单独补。
* ``$bAdmin`` 时的"更新历史价格"链接未迁（``GetUpdateStockHistoryLink()``
  在生产端本来也没有调用点）。
"""

from __future__ import annotations

from typing import Any

from web.bootstrap import install_paths

install_paths()

from core.stockdata import rtrim0  # noqa: E402
from ref.refobj import AhPairReference  # noqa: E402
from sql.historytable import (  # noqa: E402
    TABLE_COMMON_DISPLAY,
    TableRow,
    format_ratio_display,
)
from sql.stockhistory import StockHistorySql  # noqa: E402
from ui.uicolumn import (  # noqa: E402
    columns_to_dicts,
    column_date,
    column_ratio,
    column_stock,
)


#: PHP 的表 id：``$strSymbol.'ahhistory'``（``_ahhistory.php:48``）。
AH_HISTORY_TABLE_ID_SUFFIX = "ahhistory"


def _display_of(ref: Any) -> str:
    """列头用的显示文本；``get_display()`` 没有就用符号。"""
    getter = getattr(ref, "get_display", None)
    if getter is not None:
        try:
            display = getter()
        except Exception:
            display = None
        if display:
            return str(display)
    getter = getattr(ref, "get_symbol", None)
    if getter is None:
        return ""
    try:
        symbol = getter()
    except Exception:
        return ""
    return "" if symbol is None else str(symbol)


def build_ah_history(
    symbol: str,
    source: Any = None,
    start: int = 0,
    num: int = TABLE_COMMON_DISPLAY,
) -> Any:
    """装配 AH 历史表；没有配对 H 股或连不上库时返回 ``None``。

    ================  ==================================================
    返回键             含义
    ================  ==================================================
    ``table``         ``{"columns", "rows", "id", "title"}``
    ``total``         A 股历史总行数（翻页用）
    ``start`` / ``num``  当前翻页参数，与 PHP ``?start=&num=`` 同义
    ================  ==================================================
    """
    connection = getattr(source, "connection", None)
    if connection is None:
        return None

    ref = AhPairReference(symbol, source=source)
    h_ref = ref.get_pair_ref()
    if h_ref is None:
        return None
    cny_ref = ref.get_cny_ref()

    history_sql = StockHistorySql(connection)
    stock_id = ref.get_stock_id()
    total = history_sql.count(stock_id)
    records = history_sql.get_all(stock_id, start, num)

    rows = []
    for record in records:
        date = record.get("date")
        if cny_ref is None:
            continue
        hkcny = cny_ref.get_close(date)
        if not hkcny:
            # PHP：中间价取不到，这一天整行都不出现
            continue

        row = TableRow()
        row.add(str(date))
        # PHP 只给**A 股收盘价**做了 ``rtrim0``（`_ahhistory.php:11`）；
        # 中间价与 H 价是 ``GetClose()`` 的原样（那两列没过 rtrim0）。
        # 照抄，别为了"三列看起来一致"把它们一起 trim。
        row.add(str(hkcny))
        row.add(rtrim0(record.get("close")))

        h_close = h_ref.get_close(date)
        if h_close:
            row.add(str(h_close))
            ratio = ref.get_price_ratio(date)
            if ratio:
                row.add(format_ratio_display(ratio))
                row.add(format_ratio_display(1.0 / float(ratio)))
        rows.append(row)

    columns = columns_to_dicts(
        [
            column_date(),
            column_stock(_display_of(cny_ref)),
            column_stock(_display_of(ref)),
            column_stock(_display_of(h_ref)),
            column_ratio("A/H"),
            column_ratio("H/A"),
        ]
    )
    return {
        "symbol": symbol,
        "title": "AH历史价格比较",
        "table": {
            "id": "%s%s" % (symbol, AH_HISTORY_TABLE_ID_SUFFIX),
            "title": "AH历史价格比较",
            "columns": columns,
            "rows": rows,
            "count": len(rows),
        },
        "total": total,
        "start": start,
        "num": num,
        #: 与 ``/history`` 一样默认分页；``num=0`` 表示"全部记录"。
        "paged": True,
    }
