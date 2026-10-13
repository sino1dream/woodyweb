"""QDII 分类页服务。

对应 PHP：``woody/res/qdiicn.php``（只有 4 行）的 ``EchoAll()`` 落到
``woody/res/php/_mystockgroup.php`` 的
``_echoStockGroupArray(GetCategoryArray('qdii'), LayoutUseWide())``。

页面是两张表，顺序固定，与生产端
https://palmmicro.com/woody/res/qdiicn.php 一致：

1. **参考数据** —— ``php/ui/referenceparagraph.php`` 的
   ``EchoReferenceParagraph($arRef)``；列：代码(外链) / 价格 / 涨幅 /
   日期 / 时间 / 名称。首格是**新浪外链**（``GetExternalLink()``）。
2. **估值** —— ``php/ui/fundestparagraph.php`` 的
   ``EchoFundArrayEstParagraph($arFund, false, $bWide)``；列：代码(站内链接) /
   官方EST / EST日期 / 官方溢价 / [参考EST / 参考溢价] /
   [实时EST / 实时溢价]。后四列**动态出现**：
   ``_getFundEstTableColumn()`` 扫遍整个列表，一只基金都没有参考（或实时）
   估值时，对应的两列就不加。

## 分类装配（PHP ``_echoStockGroupArray()`` 的 foreach）

对每个符号：

* 先建普通引用对象（``StockGetReference`` → ``MysqlReference``）；
* 若是 A 股基金（``IsFundA()``）**且**算得出官方净值
  （``GetOfficialNetValue()``），再建一个基金引用对象
  （``StockGetFundReference`` → ``build_qdii_reference``）进 EST 表；
* 否则 PHP 会去建 AB/AH/ADR 三个配对引用 —— ``GetCategoryArray('qdii')``
  只返回 A 股基金代码（``php/stock/stocksymbol.php:199`` 起全是
  SH/SZ 号段），所以这个分支在本页不会触发；即使触发，那三个段落
  （``EchoAbParagraph`` 等）本页也不渲染，行为与"什么都不做"一致。
* ``if ($bWide == false || $bIncluded == false) $arRef[] = $ref;``
  —— 非宽屏时恒为真，所以**全部**符号都进参考数据表。
  生产端表1 与表2 行数相同（都是 41 行），正是这个原因。
* ``$arTransactionRef``（``IsTradable()``）只服务「我的股票」页的交易段落，
  本页不渲染，故不收集。

## 与 PHP 的差异

**某个符号建不出引用对象时跳过它**（PHP 会直接 fatal）。这与基金详情页
``/fund/<symbol>`` 的既有取舍一致：宁可少一行，也不要整页 500。
"""

from __future__ import annotations

from typing import Any

from web.bootstrap import install_paths

install_paths()

from core.stocksymbol import qdii_get_symbol_array  # noqa: E402
from ref.refobj import MysqlReference, build_qdii_reference  # noqa: E402

#: 页面标题。PHP ``_getTitleStr('qdii')`` = ``QDII_DISPLAY . '基金净值计算工具'``，
#: 而 ``QDII_DISPLAY`` 在 ``php/stocklink.php:22`` 定义为 ``'美股QDII'``。
PAGE_TITLE = "美股QDII基金净值计算工具"

#: 页面说明。PHP ``_getMetaDescriptionStr('qdii')``（``_mystockgroup.php``）::
#:
#:     QDII_DISPLAY.'官方'.STOCK_DISP_EST.'用来验证计算方式的准确性。以参考'
#:     .STOCK_DISP_EST.'为准，折价不申购、溢价不赎回。上涨赚溢价、下跌赚净值，
#:     不要怂，就是干！'
#:
#: ``STOCK_DISP_EST`` 在 ``php/ui/stockdisp.php:8`` 是 ``'EST'``。
PAGE_DESCRIPTION = (
    "美股QDII官方EST用来验证计算方式的准确性。以参考EST为准，"
    "折价不申购、溢价不赎回。上涨赚溢价、下跌赚净值，不要怂，就是干！"
)


def classify_symbols(symbols: list, source: Any = None) -> tuple:
    """按 PHP 的 foreach 把符号分成「参考数据」与「估值」两组。

    返回 ``(refs, funds, skipped)``：

    ================  ====================================================
    返回值            含义
    ================  ====================================================
    ``refs``          全部成功建出普通引用对象的符号（非宽屏时全进参考表）
    ``funds``         A 股基金里算得出官方净值的那批（进 EST 表）
    ``skipped``       建引用对象失败的符号，调用方可以选择记账
    ================  ====================================================

    建 ``fund`` 失败**不**影响它已经进 ``refs`` 的事实：PHP 里
    ``$arRef[] = $ref`` 在 ``IsFundA()`` 分支之外，两者互不牵连。
    """
    refs = []
    funds = []
    skipped = []
    for symbol in symbols:
        try:
            ref = MysqlReference(symbol, source=source)
        except Exception:
            skipped.append(symbol)
            continue
        refs.append(ref)

        if not ref.is_fund_a():
            continue
        try:
            fund = build_qdii_reference(symbol, source=source)
        except Exception:
            # 官方净值这一档拿不到，PHP 的守卫 ``if ($fund->GetOfficialNetValue())``
            # 同样不会把它加进 $arFund；只是这里连对象都没建出来。
            continue
        if fund.get_official_net_value():
            funds.append(fund)
    return refs, funds, skipped


def build_qdii_page(
    source: Any = None,
    reference_service: Any = None,
    fundest_service: Any = None,
    symbols: list = None,
    wide: bool = False,
    sort: str = None,
) -> dict[str, Any]:
    """装配 QDII 分类页的两张表。

    ==========================  ==========================================
    参数                         含义
    ==========================  ==========================================
    ``source``                  数据源，建引用对象要用
    ``reference_service``       :class:`~web.services.reference_service.ReferenceService`
    ``fundest_service``         :class:`~web.services.fundest_service.FundEstService`
    ``symbols``                 符号清单；**测试注入用**，默认取
                                 :func:`core.stocksymbol.qdii_get_symbol_array`
    ``wide``                    PHP 的 ``$bWide``（宽屏时名称做成外链）
    ``sort``                    EST 表的排序，见 ``fundest_service``
    ==========================  ==========================================

    任一张表的数据为空时对应键为 ``None``（模板据此整段跳过），
    这与详情页"缺数据就整段不摆"的既定做法一致。
    """
    if symbols is None:
        symbols = qdii_get_symbol_array()

    refs, funds, skipped = classify_symbols(list(symbols), source=source)

    reference = None
    if reference_service is not None and refs:
        try:
            table = reference_service.get_table(refs, wide=wide)
            reference = table if table.get("rows") else None
        except Exception:
            reference = None

    est = None
    if fundest_service is not None and funds:
        try:
            est = fundest_service.get_table(funds, wide=wide, sort=sort)
        except Exception:
            est = None

    return {
        "title": PAGE_TITLE,
        "description": PAGE_DESCRIPTION,
        "reference": reference,
        "est": est,
        "counts": {
            "symbols": len(list(symbols)),
            "reference": len(refs),
            "est": len(funds),
            "skipped": len(skipped),
        },
        "skipped": skipped,
    }
