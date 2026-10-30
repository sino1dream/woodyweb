"""A/B、A/H、ADR/H 三个对比页的服务层。

对应 PHP ``woody/res/php/_mystockgroup.php`` 里被 ``abcomparecn.php`` /
``ahcomparecn.php`` / ``adrhcomparecn.php`` 三个壳子共用的 ``EchoAll()``：

.. code-block:: php

        _echoStockGroupArray(GetCategoryArray($strPage), LayoutUseWide());

与已经迁完的 16 个"行业分类页"是**同一个函数**，差别只有符号数组的来源：

* 那 16 页的数组来自 ``php/stock/stocksymbol.php`` 里的硬编码 + 派生
  （``QdiiGetSymbolArray()`` 之类）；
* 这三个对比页的数组来自三张**配对表**（``abpair`` / ``ahpair`` / ``adrpair``）
  的 ``GetSymbolArray()`` —— 数据在库里，不是代码里。

所以本模块不能像 ``category_page_service`` 那样把"符号数组"写死成函数字面量，
必须先拿到数据源再查表。

## 段落顺序（照 PHP）

``_echoStockGroupArray()`` 对这批符号做完分类后固定输出几段：

1. 参考数据（``EchoReferenceParagraph``）；
2. 净值估算（``EchoFundArrayEstParagraph``）—— 对比页当前的符号都不是 A 股基金，
   这一段实际为空；仍然保留，避免以后数组里混进基金时悄悄少一段；
3. ``EchoAbParagraph``；
4. ``EchoAhParagraph``；
5. ``EchoAdrhParagraph``。

行细节与列定义全部复用 :mod:`web.services.pair_service`，本模块只负责分类与装配。
"""

from __future__ import annotations

from typing import Any, Optional

from web.bootstrap import install_paths

install_paths()

from ref.refobj import (  # noqa: E402
    MysqlReference,
    StockGetPairReferences,
    build_qdii_reference,
)
from sql.optionstore import AbPairStore, AdrPairStore, AhPairStore  # noqa: E402
from sql.positiontable import FundPositionSql  # noqa: E402
from web.services.pair_service import build_pair_table  # noqa: E402


class CompareNotFoundError(LookupError):
    """对比页名不在注册表里。路由据此 404。"""


#: PHP ``GetCategoryArray()``（``woody/res/php/_stocklink.php:8-21``）的三个
#: compare 分支就是"取哪张配对表的 SymbolArray"，这里按同样方式附上 store 类。
PAIR_STORES = {
    "abcompare": AbPairStore,
    "ahcompare": AhPairStore,
    "adrhcompare": AdrPairStore,
}

#: 标题常量来自 ``php/stocklink.php`` 的三个 ``*_COMPARE_DISPLAY``；description
#: 来自 ``_mystockgroup.php::_getMetaDescriptionStr()``。注意标题本身**不带**
#: "工具"二字 —— 那是 description 才有的后缀。
COMPARE_PAGES = {
    "abcompare": {
        "display": "A股和B股对比",
        "title": "A股和B股对比",
        "description": (
            "A股和B股对比工具，按A/B价格比和B/A价格比排序。"
            "B股的正式名称是人民币特种股票，以外币认购和买卖，"
            "在中国上海和深圳证券交易所上市交易。"
        ),
        "php": "abcomparecn.php",
        "notes": (),
    },
    "adrhcompare": {
        "display": "ADR和H股对比",
        "title": "ADR和H股对比",
        "description": (
            "ADR和H股对比工具，按ADR/H价格比和H/ADR价格比排序。"
            "这里H股泛指香港上市的所有企业，ADR也不限于传统意义，"
            "同时包括了二次回港上市美股。"
        ),
        "php": "adrhcomparecn.php",
        "notes": (
            "类似软件：ADR / 二次上市 / 高盛外链（PHP 的 GetExternalLink() 未迁，只留文字）。",
            "已知问题：2018年9月3日星期一，00386分红除权，导致AH和ADRH对比不准。"
            "SNP的分红除权在9月5日，而SH600028的分红除权在9月12日。",
        ),
    },
    "ahcompare": {
        "display": "A股和H股对比",
        "title": "A股和H股对比",
        "description": (
            "A股和H股对比工具，按A/H价格比和H/A价格比排序。"
            "H股开始是指获中国证监会批核到香港上市的国有企业，也称国企股，"
            "现在已经扩大到两地上市私企。"
        ),
        "php": "ahcomparecn.php",
        "notes": (
            "类似软件：新浪 / 阿思达克外链（PHP 的 GetExternalLink() 未迁，只留文字）。",
            "已知问题：2018年9月3日星期一，00386分红除权，导致AH和ADRH对比不准。"
            "SNP的分红除权在9月5日，而SH600028的分红除权在9月12日。",
        ),
    },
}

#: 三个段落的固定顺序，与 ``_echoStockGroupArray()`` 里那三个 if 同序。
PAIR_ORDER = (("ab", "AB对比"), ("ah", "AH对比"), ("adr", "ADR和H股对比"))


def compare_symbols(name: str, source: Any = None) -> list:
    """``GetCategoryArray($strPage)`` 的三个 compare 分支。

    ``source`` 没有 ``connection``（测试假源）时返回空列表：这是"连不上库"
    而不是"库里没数据"，调用方据此显示空态，不让页面 500。
    """
    store_class = PAIR_STORES.get(name)
    if store_class is None:
        raise CompareNotFoundError(name)
    connection = getattr(source, "connection", None)
    if connection is None:
        return []
    return store_class(connection).get_symbol_array()


def _symbol_of(ref: Any) -> str:
    """取符号文本；取不到返回空串（只用于去重）。"""
    getter = getattr(ref, "get_symbol", None)
    if getter is None:
        return ""
    try:
        symbol = getter()
    except Exception:
        return ""
    return "" if symbol is None else str(symbol)


def _in_array_ref(ref: Any, refs: list) -> bool:
    """``in_array_ref($ref, $arRef)``（``woody/res/php/_stock.php:196``）。

    按**符号**比而不是对象身份比 —— PHP 的循环就是逐个比 ``GetSymbol()``。
    """
    symbol = _symbol_of(ref)
    return any(_symbol_of(existing) == symbol for existing in refs)


def classify_symbols(symbols: list, source: Any = None, wide: bool = False) -> tuple:
    """照 ``_echoStockGroupArray()`` 的 foreach 分类。

    返回 ``(refs, funds, pairs, skipped)``。建引用对象失败的符号进 ``skipped``，
    不会拖垮整页；配对引用报错时保留已经建好的部分。
    """
    refs = []
    funds = []
    pairs = {"ab": [], "ah": [], "adr": []}
    skipped = []

    for symbol in symbols:
        try:
            ref = MysqlReference(symbol, source=source)
        except Exception:
            skipped.append(symbol)
            continue

        included = False
        try:
            if ref.is_fund_a():
                fund = build_qdii_reference(symbol, source=source)
                if fund.get_official_net_value():
                    funds.append(fund)
                    included = True
            else:
                ab_ref, ah_ref, adr_ref = StockGetPairReferences(symbol, source)
                for key, candidate in (("ab", ab_ref), ("ah", ah_ref), ("adr", adr_ref)):
                    if candidate is None:
                        continue
                    if _in_array_ref(candidate, pairs[key]):
                        continue
                    pairs[key].append(candidate)
                    included = True
        except Exception:
            # 一个符号取数失败不该让整页 500；已经推出的结果保留。
            pass

        # PHP: if ($bWide == false || $bIncluded == false) $arRef[] = $ref;
        if wide is False or included is False:
            refs.append(ref)
    return refs, funds, pairs, skipped


def _position_reader(source: Any) -> Optional[Any]:
    """``GetPositionSql()->ReadVal($strStockId, true)``。

    没有连接就不给 reader —— ``pair_service`` 会把仓位那格留空，
    页面上只是少一格（ADR 表最后一列），不报错。
    """
    connection = getattr(source, "connection", None)
    if connection is None:
        return None

    def read(stock_id: Any) -> Any:
        return FundPositionSql(connection).read_val(stock_id, True)

    return read


def build_compare_page(
    name: str,
    source: Any = None,
    reference_service: Any = None,
    fundest_service: Any = None,
    symbols: list = None,
    wide: bool = False,
    history_link_builder: Any = None,
) -> dict:
    """装配一个对比页。

    ``symbols`` 用于测试注入；不给就从对应配对表查。返回 dict 里的
    ``reference`` / ``est`` / ``sections[].table`` 任一为空就是 ``None``，
    模板据此整段跳过。
    """
    try:
        definition = COMPARE_PAGES[name]
    except KeyError:
        raise CompareNotFoundError(name)

    if symbols is None:
        symbols = compare_symbols(name, source)
    symbols = list(symbols)
    refs, funds, pairs, skipped = classify_symbols(symbols, source=source, wide=wide)

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
            est = fundest_service.get_table(funds, wide=wide)
        except Exception:
            est = None

    sections = []
    for kind, title in PAIR_ORDER:
        table = build_pair_table(
            pairs[kind],
            kind=kind,
            wide=wide,
            title=title,
            source=source,
            position_reader=_position_reader(source),
        )
        if table is None:
            continue
        # PHP ``EchoAhParagraph()`` 只有**一行**时额外挂一个 AH 历史页链接。
        if kind == "ah" and len(pairs["ah"]) == 1 and history_link_builder is not None:
            try:
                table["history_link"] = {
                    "text": "AH历史价格比较",
                    "url": history_link_builder(_symbol_of(pairs["ah"][0])),
                }
            except Exception:
                pass
        sections.append({"key": kind, "title": title, "table": table})

    return {
        "name": name,
        "display": definition["display"],
        "php": definition["php"],
        "title": definition["title"],
        "description": definition["description"],
        "notes": list(definition["notes"]),
        "reference": reference,
        "est": est,
        "sections": sections,
        "counts": {
            "symbols": len(symbols),
            "reference": len(refs),
            "est": len(funds),
            "pairs": {key: len(value) for key, value in pairs.items()},
            "skipped": len(skipped),
        },
        "skipped": skipped,
    }
